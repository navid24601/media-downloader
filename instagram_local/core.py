"""Input validation, safe errors, filenames, and import handling (no network)."""
import csv
import io
import re
import time
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit, unquote


class Failure(Exception):
    def __init__(self, kind, message, retry_at=0):
        super().__init__(message)
        self.kind, self.message, self.retry_at = kind, message, retry_at

    @property
    def restricted(self):
        return self.kind in {"auth", "challenge", "rate_limit", "restricted"}


def post_url(value):
    value = value.strip()
    if not value:
        raise ValueError("Empty link")
    if "://" not in value:
        value = "https://" + value
    try:
        u = urlsplit(value)
        if u.hostname not in {"instagram.com", "www.instagram.com", "m.instagram.com"} or u.username or u.password or u.port:
            raise ValueError()
        if u.scheme not in {"http", "https"}:
            raise ValueError()
        match = re.fullmatch(r"/(?:[A-Za-z0-9_.]+/)?(?:p|reel|reels|tv)/([A-Za-z0-9_-]{5,32})/?", u.path)
        if not match:
            raise ValueError()
    except ValueError:
        raise ValueError("Use a full Instagram post or Reel link; Stories and share redirects are unsupported") from None
    code = match[1]
    return code, f"https://www.instagram.com/p/{code}/"


def normalize_lines(values):
    valid, errors, seen, duplicates = [], [], set(), 0
    for number, value in enumerate(values, 1):
        if not value.strip():
            continue
        try:
            code, url = post_url(value)
            if code in seen:
                duplicates += 1
            else:
                valid.append((code, url))
                seen.add(code)
        except ValueError as exc:
            # Never echo raw input: query strings may contain credentials.
            errors.append({"line": number, "error": str(exc)})
    return valid, errors, duplicates


def profile_name(value):
    value = value.strip().lstrip("@")
    if "/" in value:
        u = urlsplit(value if "://" in value else "https://" + value)
        if u.hostname not in {"instagram.com", "www.instagram.com"} or u.username or u.port:
            raise ValueError("Use an Instagram username or profile URL")
        value = u.path.strip("/")
    if not re.fullmatch(r"[A-Za-z0-9_.]{1,30}", value) or value.lower() in {"p", "reel", "stories", "explore", "accounts"}:
        raise ValueError("Use an Instagram username or profile URL")
    return value


def collection_url(value):
    u = urlsplit(value.strip())
    if u.scheme != "https" or u.hostname not in {"instagram.com", "www.instagram.com"} or u.username or u.port:
        raise ValueError("Use the HTTPS URL of an Instagram Saved collection")
    match = re.fullmatch(r"/([A-Za-z0-9_.]{1,30})/saved/([^/]+)/([0-9]+)/?", u.path)
    if not match:
        raise ValueError("Collection URL must look like https://www.instagram.com/you/saved/name/123456/")
    return urlunsplit(("https", "www.instagram.com", u.path.rstrip("/") + "/", "", "")), unquote(match[2])[:100]


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", str(value)).strip(" .")[:80]
    if not value:
        value = "unknown"
    if value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}:
        value = "_" + value
    return value


def basename(username, shortcode, day=None):
    return f"{safe_name(username)}-{safe_name(shortcode)}-{day or date.today().isoformat()}"


def import_file(text, filename, column=None):
    if len(text) > 4_000_000:
        raise ValueError("Import limit is 4 MB; split larger files")
    if filename.lower().endswith(".txt"):
        return {"values": text.lstrip("\ufeff").splitlines()}
    if not filename.lower().endswith(".csv"):
        raise ValueError("Choose a TXT or CSV file")
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.reader(io.StringIO(text.lstrip("\ufeff")), dialect))
    if not rows:
        return {"values": []}
    width = max(map(len, rows))
    if width > 100:
        raise ValueError("CSV limit is 100 columns")
    headers = rows[0]
    has_header = any(re.search(r"url|link", x, re.I) for x in headers) or not any(_valid(x) for x in headers)
    body = rows[1:] if has_header else rows
    candidates = [i for i in range(width) if any(i < len(row) and _valid(row[i]) for row in body)]
    named = [i for i, h in enumerate(headers) if re.fullmatch(r"\s*(?:url|link|post[_ ]?url|instagram[_ ]?(?:url|link))\s*", h, re.I)] if has_header else []
    # Multiple columns containing links always require an explicit choice.
    if column is None and len(candidates) > 1:
        return {"columns": [{"index": i, "name": (headers[i][:80] if has_header and i < len(headers) else f"Column {i+1}")} for i in candidates]}
    if column is None:
        column = candidates[0] if len(candidates) == 1 else named[0] if len(named) == 1 else 0 if width == 1 else None
    if column is None or not isinstance(column, int) or not 0 <= column < width:
        return {"columns": [{"index": i, "name": f"Column {i+1}"} for i in range(width)]}
    return {"values": [row[column] if len(row) > column else "" for row in body]}


def _valid(value):
    try:
        post_url(value)
        return True
    except ValueError:
        return False


def retry_after(value):
    try:
        return time.time() + max(0, float(value))
    except (ValueError, TypeError):
        try:
            return parsedate_to_datetime(value).timestamp()
        except (ValueError, TypeError, OverflowError):
            return 0


def redact(text):
    text = re.sub(r"(?i)(https?://|socks5h?://)[^\s/@]+:[^\s/@]+@", r"\1[redacted]@", str(text))
    text = re.sub(r"(?i)\b(sessionid|csrftoken|cookie|authorization|token|password|fb_dtsg|lsd)\b\s*[:=]\s*[^\s,;]+", r"\1=[redacted]", text)
    return re.sub(r"(https?://[^\s?]+)\?[^\s]+", r"\1?[redacted]", text)


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
