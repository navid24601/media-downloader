"""Windows current-user DPAPI protection. No plaintext fallback."""
import ctypes
from ctypes import wintypes
import json
import os
import time
from urllib.parse import urlsplit


class Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def protect(data, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("Session storage requires Windows DPAPI")
    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    target = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        fn = crypt.CryptUnprotectData
        fn.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target))
    else:
        fn = crypt.CryptProtectData
        fn.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = fn(ctypes.byref(source), "Instagram Local", None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise RuntimeError("Windows could not protect or open local credentials")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        kernel.LocalFree(target.pbData)


class Vault:
    def __init__(self, path):
        self.path = path

    def load(self):
        if not self.path.exists():
            return {"cookies": [], "proxy": ""}
        return json.loads(protect(self.path.read_bytes(), decrypt=True))

    def save(self, value):
        data = protect(json.dumps(value).encode("utf-8"))
        temporary = self.path.with_suffix(".tmp")
        temporary.write_bytes(data)
        os.replace(temporary, self.path)


def parse_cookies(text):
    """Netscape export or common browser-export JSON; Instagram domains only."""
    if len(text) > 2_000_000:
        raise ValueError("Cookie import is too large")
    rows = []
    try:
        if text.lstrip().startswith(("[", "{")):
            data = json.loads(text)
            rows = data if isinstance(data, list) else data["cookies"]
        else:
            for line in text.splitlines():
                if line.startswith("#HttpOnly_"):
                    line = line[10:]
                if not line.strip() or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) != 7:
                    raise ValueError()
                domain, _, path, secure, expires, name, value = parts
                rows.append(dict(domain=domain, path=path, secure=secure.upper() == "TRUE", expirationDate=float(expires), name=name, value=value))
        result = []
        # Restrict names as well as domains: ignore unrelated browser state.
        for row in rows:
            domain = str(row.get("domain", "")).lower()
            if domain.lstrip(".") not in {"instagram.com", "www.instagram.com"}:
                continue
            name, value = row["name"], row["value"]
            if name not in {"sessionid", "csrftoken", "ds_user_id", "rur", "mid", "ig_did"}:
                continue
            expiry = row.get("expirationDate", row.get("expires", 0)) or 0
            if float(expiry) > 0 and float(expiry) < time.time():
                continue
            if not isinstance(value, str) or any(c in value for c in "\r\n\x00"):
                raise ValueError()
            result.append(dict(domain=domain, path="/", secure=True, name=name, value=value))
        if not any(r["name"] == "sessionid" and r["value"] for r in result):
            raise ValueError()
        return result
    except (KeyError, TypeError, ValueError):
        raise ValueError("Import a Netscape cookies.txt or JSON cookie export containing a non-expired Instagram sessionid") from None


def validate_proxy(value):
    value = value.strip()
    if not value:
        return ""
    try:
        u = urlsplit(value)
        if u.scheme not in {"http", "https", "socks5", "socks5h"} or not u.hostname or not u.port or u.path not in {"", "/"} or u.query or u.fragment:
            raise ValueError()
        if any(c.isspace() for c in value):
            raise ValueError()
    except ValueError:
        raise ValueError("Use http://host:port, https://host:port, or socks5h://host:port; URL-encode credentials") from None
    return value
