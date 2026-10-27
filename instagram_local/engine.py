"""Replaceable gallery-dl adapter. This is the only Instagram-aware module.

Pinned engine internals are deliberate: posts() yields incrementally and _cursor
identifies the current page. No Instagram endpoint/query IDs are copied here.
"""
import logging
import re
import time
import types
from dataclasses import dataclass
from urllib.parse import urlsplit
from urllib.request import getproxies
from xml.etree import ElementTree
import requests
from gallery_dl.extractor import instagram as ig
from .core import Failure, retry_after


@dataclass
class Media:
    identity: str
    url: str
    extension: str
    manifest: str = ""


@dataclass
class Post:
    username: str
    shortcode: str
    caption: str
    media: list[Media]
    carousel: bool = False


def allowed_host(url):
    try:
        u = urlsplit(url)
        host = u.hostname or ""
        return u.scheme == "https" and not u.username and not u.password and u.port in (None, 443) and any(host == d or host.endswith("." + d) for d in ("instagram.com", "cdninstagram.com", "fbcdn.net"))
    except ValueError:
        return False


def response_failure(response):
    """Inspect status and machine error markers; never expose response text."""
    status = response.status_code
    location = response.headers.get("Location", "").lower()
    final = response.url.lower()
    marker = ""
    if "json" in response.headers.get("Content-Type", "") and len(response.content) < 2_000_000:
        try:
            data = response.json()
            if isinstance(data, dict):
                marker = str({k: data[k] for k in ("message", "error_type", "status", "errors", "challenge") if k in data}).lower()
        except ValueError:
            pass
    if any(x in location + final + marker for x in ("challenge", "checkpoint", "consent_required")):
        return Failure("challenge", "Instagram requires a challenge/checkpoint. Open Instagram in Chrome, resolve it, then import fresh cookies and Resume.")
    if status == 429 or any(x in marker for x in ("please wait", "rate_limit", "feedback_required", "sentry_block")):
        return Failure("rate_limit", "Instagram limited requests. Stop and wait before manually resuming; follow any guidance shown in Instagram.", retry_after(response.headers.get("Retry-After")))
    if status == 401 or any(x in location + final + marker for x in ("accounts/login", "login_required", "login required", "not logged in")):
        return Failure("auth", "Instagram needs a valid session. Log in through Chrome and import fresh cookies, then Resume.")
    if status == 403:
        if "instagram.com" == urlsplit(response.url).hostname or (urlsplit(response.url).hostname or "").endswith(".instagram.com"):
            return Failure("restricted", "Instagram denied access. Check the account in Chrome and refresh the session before resuming.")
        return Failure("unavailable", "Media access was denied. Retry later to refresh the media link.")
    if status in (404, 410):
        return Failure("unavailable", "The post or media is unavailable, deleted, or inaccessible to this session.")
    if status >= 500 or status in (408, 425):
        return Failure("network", "Temporary server/connection failure. Check your VPN or proxy.")
    if status >= 400 or "'status': 'fail'" in marker or "'errors':" in marker:
        return Failure("unavailable", "Instagram did not return usable content. Check the link and account access in Chrome.")
    return None


class Engine:
    _last_api_request = 0
    def __init__(self, secrets, sleeper=time.sleep):
        self.secrets = secrets
        self.sleep = sleeper
        self.last_request = 0
        self.sticky = None
        proxy = secrets.get("proxy", "")
        # Python urllib reads environment proxies, then static Windows Internet
        # Settings when no env proxy exists. PAC/WPAD is not executed.
        self.proxies = {"http": proxy, "https": proxy} if proxy else {k: v for k, v in getproxies().items() if k in {"http", "https", "all", "no"}}
        # Library diagnostics can include signed URLs and FB tokens. Drop them
        # completely; the application emits only fixed, classified messages.
        for name in ("instagram", "gallery_dl", "urllib3", "requests", "yt_dlp"):
            logger = logging.getLogger(name)
            logger.disabled = True
        self.media_session = requests.Session()
        self.media_session.trust_env = False
        self.media_session.proxies.update(self.proxies)

    def close(self):
        self.media_session.close()

    def _extractor(self, cls, url, cursor=None):
        ext = cls.from_url(url)
        if ext is None:
            raise Failure("unavailable", "Unsupported source link")
        options = {"retries": 0, "sleep-request": 0, "sleep-429": 0, "timeout": 30,
                   "verify": True, "proxy-env": False, "proxy": self.proxies,
                   "cookies": None, "user-cache": "memory", "write-pages": False,
                   "cursor": cursor or True, "videos": True, "audio": False,
                   "netrc": False}
        ext.config = lambda key, default=None: options.get(key, default)
        # Keep repeated page/token lookups in memory for this operation only.
        # Never use gallery-dl's disk cache, even when it requests _mem=False.
        memory_cache = {}
        def cache_key(func, args):
            return (func.__qualname__, repr(args))
        def cached(func, *args, **kwargs):
            key = cache_key(func, args)
            if key not in memory_cache:
                memory_cache[key] = func(*args)
            return memory_cache[key]
        def cache_update(func, *args, **kwargs):
            if args:
                memory_cache[cache_key(func, args[:-1])] = args[-1]
        ext.cache = cached
        ext.cache_update = cache_update
        ext.initialize()
        for cookie in self.secrets.get("cookies", []):
            ext.cookies.set(cookie["name"], cookie["value"], domain=cookie["domain"], path="/", secure=True)
        # _init() generates a CSRF value; prefer imported browser value.
        for cookie in self.secrets.get("cookies", []):
            if cookie["name"] == "csrftoken":
                ext.csrf_token = cookie["value"]
        original = ext.request

        def guarded(url, **kwargs):
            if self.sticky:
                raise self.sticky
            if not allowed_host(url):
                raise Failure("unavailable", "The source returned an unsupported media host")
            self.sleep(max(0, 6 - (time.monotonic() - Engine._last_api_request)))
            Engine._last_api_request = time.monotonic()
            kwargs["retries"] = 0
            kwargs["verify"] = True
            kwargs["allow_redirects"] = False
            kwargs["fatal"] = ...  # inspect HTTP failures before the engine handles them
            try:
                response = original(url, **kwargs)
                if not response.status_code:
                    raise Failure("network", "Connection failed. Check VPN/TUN, proxy address, and TLS trust.")
                problem = response_failure(response)
                if problem:
                    self.sticky = problem
                    raise problem
                if 300 <= response.status_code < 400:
                    raise Failure("unavailable", "Instagram redirected this request. Check the source in Chrome.")
                return response
            except Failure as failure:
                self.sticky = failure
                raise
            except requests.exceptions.RequestException:
                self.sticky = Failure("network", "Connection failed. Check VPN/TUN, proxy address, and TLS trust; TLS verification stays enabled.")
                raise self.sticky from None
        # engine may catch request exceptions internally; sticky preserves account
        # restrictions even through those fallback catches.
        ext.request = guarded
        ext.session.hooks["response"].append(self._hook)
        ext.login()
        ext.videos_dash = True
        return ext

    def _hook(self, response, *args, **kwargs):
        problem = response_failure(response)
        if problem:
            self.sticky = problem
            raise problem
        return response

    def _convert_error(self, exc):
        if self.sticky:
            return self.sticky
        if isinstance(exc, Failure):
            return exc
        name = type(exc).__name__.lower()
        if any(x in name for x in ("auth", "login")):
            return Failure("auth", "This content needs a valid Instagram session. Import fresh cookies.")
        if "challenge" in name:
            return Failure("challenge", "Resolve Instagram's challenge in Chrome, refresh cookies, then Resume.")
        if any(x in name for x in ("connection", "timeout", "httperror", "proxy", "ssl")):
            return Failure("network", "Connection failed. Check VPN/TUN and proxy settings.")
        if any(x in name for x in ("keyerror", "valueerror", "indexerror", "typeerror", "attributeerror")):
            return Failure("engine", "Instagram returned an unexpected format. The pinned engine may need an update; see README.")
        return Failure("unavailable", "Content unavailable to this session. Check the link in Chrome.")

    def enumerate(self, source):
        reels = source["kind"] == "profile" and source.get("phase") == "reels"
        cls = ig.InstagramReelsExtractor if reels else {"profile": ig.InstagramPostsExtractor, "saved": ig.InstagramSavedExtractor, "collection": ig.InstagramCollectionExtractor}[source["kind"]]
        if source["kind"] != "profile" and not self.secrets.get("cookies"):
            raise Failure("auth", "Saved library access requires imported Instagram cookies.")
        target = source["target"].replace("/posts/", "/reels/") if reels else source["target"]
        ext = self._extractor(cls, target, source["cursor"])
        ext._cursor = source["cursor"]
        cursor, offset = source["cursor"], 0
        try:
            # Read enumeration nodes directly for Reels. The engine's posts()
            # resolves each Reel's full media, which is deferred until download.
            iterator = iter(ext.api.user_reels(ext.item) if reels else ext.posts())
            for _ in range(source["offset"]):
                next(iterator)
                offset += 1
            for raw in iterator:
                if self.sticky:
                    raise self.sticky
                if ext._cursor != cursor:
                    cursor, offset = ext._cursor, 0
                offset += 1
                if reels:
                    raw = raw.get("media", raw)
                code = raw.get("code")
                if not re.fullmatch(r"[A-Za-z0-9_-]{5,32}", code or ""):
                    raise Failure("engine", "Source returned an item without a valid post identity; enumeration stopped without advancing it.")
                yield code, cursor, offset, raw
        except StopIteration:
            # Saved changes between runs may shorten a page. Do not pretend end.
            raise Failure("engine", "The source page changed while paused. Cancel this source and start it again; completed posts will be skipped.") from None
        except Exception as exc:
            raise self._convert_error(exc) from None
        finally:
            ext.session.close()

    def post(self, url):
        ext = self._extractor(ig.InstagramPostExtractor, url)
        try:
            raw = next(iter(ext.posts()))
            if self.sticky:
                raise self.sticky
            raw_items = raw.get("carousel_media") or [raw]
            expected = raw.get("carousel_media_count", len(raw_items))
            if expected != len(raw_items):
                raise Failure("incomplete", "Instagram omitted carousel items. No complete ZIP was created.")
            # gallery-dl picks candidate[0]; ensure it is the largest exposed image.
            for item in raw_items:
                candidates = item.get("image_versions2", {}).get("candidates", [])
                candidates.sort(key=lambda x: x.get("width", 0) * x.get("height", 0), reverse=True)
            parsed = ext._parse_post(raw)
            # gallery-dl may append a separate music file to _files. It is not
            # one of the post's image/video items and must not affect the count.
            files = [f for f in parsed["_files"] if "audio_url" not in f]
            if len(files) != len(raw_items) or not files:
                raise Failure("incomplete", "One or more media items are missing. No complete output was created.")
            result = []
            for original, f in zip(raw_items, files):
                video = bool(original.get("media_type") == 2 or original.get("video_versions"))
                if video and not f.get("video_url"):
                    raise Failure("incomplete", "A video URL is missing; its thumbnail will not be substituted.")
                url = f["video_url"] if video else f["display_url"]
                if not allowed_host(url):
                    raise Failure("engine", "Media URL uses an unsupported host")
                extension = "mp4" if video else urlsplit(url).path.rsplit(".", 1)[-1].lower()
                if extension not in {"mp4", "jpg", "jpeg", "png", "webp", "avif"}:
                    raise Failure("engine", "Media type could not be identified safely")
                result.append(Media(str(f["media_id"]), url, extension, f.get("_ytdl_manifest_data", "") if video else ""))
            return Post(parsed["username"] or "unknown", parsed["post_shortcode"], parsed.get("description", ""), result, bool(raw.get("carousel_media")))
        except Exception as exc:
            raise self._convert_error(exc) from None
        finally:
            ext.session.close()

    def connection_test(self):
        try:
            with self.media_session.get("https://www.instagram.com/", timeout=(10, 20), allow_redirects=False, verify=True) as response:
                problem = response_failure(response)
                if problem:
                    raise problem
                return "Instagram is reachable through the configured connection. This does not validate your session."
        except requests.exceptions.RequestException:
            raise Failure("network", "Cannot reach Instagram. Check your VPN/TUN or explicit proxy and TLS trust.") from None

    def check_session(self):
        # engine's user(id) validates that the supplied account is accessible;
        # Saved probe actually exercises authentication (one page, no downloads).
        source = dict(kind="saved", target="https://www.instagram.com/me/saved/", cursor=None, offset=0)
        iterator = self.enumerate(source)
        try:
            next(iterator, None)
            return "Authenticated Saved access verified."
        finally:
            iterator.close()

    def download(self, media, path, progress):
        if media.manifest:
            return self._dash(media, path, progress)
        try:
            # No cookies are forwarded to media CDNs. Redirects are rejected.
            with self.media_session.get(media.url, stream=True, timeout=(15, 30), verify=True, allow_redirects=False) as response:
                problem = response_failure(response)
                if problem:
                    raise problem
                if response.status_code != 200:
                    raise Failure("unavailable", "Media request did not return a complete file")
                content_type = response.headers.get("Content-Type", "").lower()
                if not content_type.startswith(("image/", "video/", "application/octet-stream")):
                    raise Failure("incomplete", "Server returned non-media content")
                total = int(response.headers.get("Content-Length", 0)) or None
                done = 0
                with path.open("wb") as output:
                    for chunk in response.iter_content(256 * 1024):
                        output.write(chunk)
                        done += len(chunk)
                        progress(done, total)
                if not done or total and done != total:
                    raise Failure("network", "Media transfer ended early; retry will preserve completed carousel items.")
        except requests.exceptions.RequestException:
            raise Failure("network", "Media connection failed. Check VPN/TUN or proxy.") from None

    def _dash(self, media, path, progress):
        import imageio_ffmpeg
        import yt_dlp
        import yt_dlp.networking

        class Quiet:
            def debug(self, *args): pass
            def info(self, *args): pass
            def warning(self, *args): pass
            def error(self, *args): pass

        def hook(info):
            progress(info.get("downloaded_bytes", 0), info.get("total_bytes"))

        proxy = self.proxies.get("https", self.proxies.get("all", ""))
        opts = dict(quiet=True, no_warnings=True, logger=Quiet(), retries=0, fragment_retries=0,
                    extractor_retries=0, file_access_retries=0, concurrent_fragment_downloads=1,
                    socket_timeout=30, nocheckcertificate=False, proxy=proxy,
                    ffmpeg_location=imageio_ffmpeg.get_ffmpeg_exe(), outtmpl=str(path),
                    format="bestvideo+bestaudio/best", merge_output_format="mp4",
                    progress_hooks=[hook], noprogress=True, overwrites=False)
        # Parse the manifest already supplied by gallery-dl; no second Instagram
        # extraction, no cookies, and no lower-quality silent fallback.
        opts["overwrites"] = False
        try:
            root = ElementTree.fromstring(media.manifest)
            with yt_dlp.YoutubeDL(opts) as ydl:
                extr = ydl.get_info_extractor("Generic")
                formats, _ = extr._parse_mpd_formats_and_subtitles(root, mpd_id="dash")
                if not formats:
                    raise Failure("incomplete", "The DASH manifest contains no usable media formats")
                for f in formats:
                    for url in [f.get("url"), *(frag.get("url") for frag in f.get("fragments", []))]:
                        if url and not allowed_host(url):
                            raise Failure("engine", "The DASH manifest references an unsupported media host")
                # Reject unexpected redirects/statuses before yt-dlp retries.
                original_open = ydl.urlopen
                def guarded_open(request):
                    url = request if isinstance(request, str) else request.url
                    if not allowed_host(url):
                        raise Failure("engine", "Unsupported video host")
                    try:
                        return original_open(request)
                    except yt_dlp.networking.exceptions.HTTPError as exc:
                        if exc.status == 429:
                            self.sticky = Failure("rate_limit", "Media server limited requests. Wait before manually resuming.", retry_after(exc.response.headers.get("Retry-After")))
                            raise self.sticky from None
                        if exc.status in (401, 403):
                            raise Failure("unavailable", "Video access denied; retry later to refresh media links.") from None
                        raise
                ydl.urlopen = guarded_open
                ydl.process_ie_result(dict(id=media.identity, title=media.identity, formats=formats, extractor="instagram-local"), download=True)
            if not path.is_file() or path.stat().st_size == 0:
                raise Failure("incomplete", "Video merging did not produce a complete file")
        except Failure:
            raise
        except Exception:
            if self.sticky and self.sticky.restricted:
                raise self.sticky from None
            raise Failure("network", "Best-quality video transfer or audio merge failed. Completed carousel items are preserved for retry.") from None
