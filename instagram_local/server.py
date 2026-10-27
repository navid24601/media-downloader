"""Local HTTP interface: loopback binding, strict Host/Origin, capability token."""
import hmac
import hashlib
import json
import os
import re
import secrets
import zlib
import threading
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from .core import normalize_lines, profile_name, collection_url, import_file, Failure
from .security import parse_cookies, validate_proxy
from .engine import Engine
from .library import Library

STATIC = Path(__file__).with_name("static")


class Application:
    def __init__(self, store, vault, worker, token):
        self.store, self.vault, self.worker, self.token = store, vault, worker, token
        self.http = None
        self.library = Library(store)
        self.media_token = secrets.token_urlsafe(32)

    def settings(self):
        secret = self.vault.load()
        return dict(folder=self.store.setting("folder", str(Path.cwd() / "Downloads")),
                    caption=self.store.setting("caption", False),
                    session=bool(secret.get("cookies")), session_verified=self.store.setting("session_verified", False),
                    proxy_set=bool(secret.get("proxy")), quality="Best available images and video, including DASH + audio; no re-encoding.")

    def options(self, body):
        return dict(folder=self.settings()["folder"], caption=self.store.setting("caption", False), again=body.get("again") is True,
                    source={'kind':'file' if body.get('source')=='file' else 'links','label':'File import' if body.get('source')=='file' else 'Links'})

    def dispatch(self, path, body):
        if path.startswith('/api/library/'):
            # Library operations never load the session vault or use Engine.
            action = path.removeprefix('/api/library/')
            if action == 'access':
                self.library.start()
                return {'message':'Offline Library ready.'}
            if action == 'list':
                return self.library.listing(query=body.get('query',''),username=body.get('username',''),
                    favorites=body.get('favorites') is True,hidden=body.get('hidden') is True,
                    before=body.get('before'),source=body.get('source',''))
            if action == 'filters':return self.library.filters()
            if action == 'detail':return self.library.detail(body.get('id'))
            if action == 'flag':
                self.library.flag(body.get('id'),body.get('field'),body.get('value'))
                return {'message':'Library updated.'}
            if action == 'fetch-details':
                added=self.library.queue_details(body.get('id'))
                return {'message':('Details fetch queued in Downloader. It needs Instagram connectivity and may use your imported session. Resume the queue when ready; no media will be downloaded.' if added else 'This post is already pending in Downloader. Finish that work before queuing a separate details fetch.')}
            if action == 'thumbnails':
                ids=body.get('ids',[])
                if not isinstance(ids,list) or len(ids)>48:raise ValueError('At most 48 thumbnails per request')
                return {'items':[{'id':i,'state':self.library.thumb_state(self.library.row(i))} for i in ids]}
            if action == 'delete-preview':return self.library.deletion_preview(body.get('id'))
            if action == 'delete':
                self.library.delete(body.get('id'),body.get('confirmation',''))
                return {'message':'Recorded local files deleted. Download history is retained.'}
            raise ValueError('Unknown Library action')
        if path == "/api/state":
            return {**self.store.snapshot(body.get("before")), "worker": self.worker.snapshot(), "settings": self.settings()}
        if path == "/api/settings":
            folder = str(body.get("folder", "")).strip()
            if not folder or not Path(folder).is_absolute() or folder.startswith(("\\\\", "//")):
                raise ValueError("Choose an absolute local folder path; network shares are unsupported")
            if any(c in folder for c in "\r\n\x00"):
                raise ValueError("Folder path contains invalid characters")
            # Validate write access now, so an unusable setting cannot silently
            # become a failed queue of thousands of posts.
            try:
                target = Path(folder)
                target.mkdir(parents=True, exist_ok=True)
                import tempfile
                with tempfile.TemporaryFile(dir=target):
                    pass
            except OSError:
                raise ValueError("Cannot write to that folder. Choose a writable local folder") from None
            self.store.set_setting("folder", str(target))
            self.store.set_setting("caption", body.get("caption") is True)
            return {"message": "Settings saved. New queue items use this folder and caption setting."}
        if path == "/api/import":
            return import_file(body.get("text", ""), body.get("filename", ""), body.get("column"))
        if path == "/api/links":
            values = body.get("values", [])
            if not isinstance(values, list) or len(values) > 20000 or not all(isinstance(x, str) for x in values):
                raise ValueError("Use at most 20,000 links per import")
            valid, errors, duplicates = normalize_lines(values)
            added, skipped = self.store.enqueue(valid, self.options(body))
            return dict(message=f"Queued {added}; skipped {skipped} active/completed and {duplicates} repeated links.", errors=errors[:100], invalid_count=len(errors))
        if path == "/api/source":
            kind = body.get("kind")
            maximum = body.get("maximum")
            if maximum is not None and (not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= 100000):
                raise ValueError("Latest N must be between 1 and 100,000")
            if kind == "profile":
                name = profile_name(body.get("target", ""))
                target, label = f"https://www.instagram.com/{name}/posts/", "@" + name
            elif kind in {"saved", "collection"}:
                if not self.settings()["session"]:
                    raise ValueError("Import Instagram cookies before adding a Saved source")
                if maximum and body.get("accept_available_order") is not True:
                    raise ValueError("Accept 'Latest N in available order' before adding this Saved source")
                if kind == "collection":
                    target, label = collection_url(body.get("target", ""))
                else:
                    target, label = "https://www.instagram.com/me/saved/", "All Saved posts"
            else:
                raise ValueError("Unsupported source mode")
            self.store.add_source(kind, target, label, maximum, self.options(body))
            return {"message": "Source queued. Resume to enumerate and download in batches of 50."}
        if path == "/api/resume":
            self.worker.resume()
            return {"message": "Resumed."}
        if path == "/api/pause":
            self.worker.pause()
            return {"message": "Pause requested after the current post/request."}
        if path == "/api/cancel":
            self.worker.cancel()
            return {"message": "Remaining pending work canceled. Completed downloads are kept."}
        if path == "/api/retry":
            self.worker.retry_failed()
            return {"message": "Failed items queued again. Choose Resume when ready."}
        if path in {"/api/cookies", "/api/disconnect", "/api/proxy", "/api/test", "/api/session-test"}:
            # Do not perform parallel account requests or change credentials
            # midway through a post. Pause, finish current request, then retry.
            if self.worker.active or not self.worker.network_lock.acquire(blocking=False):
                raise ValueError("Pause the queue and wait for the current post/request to finish first")
            try:
                secret = self.vault.load()
                if path == "/api/cookies":
                    secret["cookies"] = parse_cookies(body.get("text", ""))
                    self.vault.save(secret)
                    self.store.set_setting("session_verified", False)
                    return {"message": "Cookies stored with Windows DPAPI. Use Verify Saved access for an authenticated check."}
                if path == "/api/disconnect":
                    secret["cookies"] = []
                    self.vault.save(secret)
                    self.store.set_setting("session_verified", False)
                    return {"message": "Imported session cleared. Chrome login is unchanged."}
                if path == "/api/proxy":
                    secret["proxy"] = validate_proxy(body.get("proxy", ""))
                    self.vault.save(secret)
                    return {"message": "Proxy saved securely. Empty uses detected environment/static Windows proxy, or direct VPN/TUN."}
                engine = Engine(secret)
                try:
                    message = engine.check_session() if path == "/api/session-test" else engine.connection_test()
                    if path == "/api/session-test":
                        self.store.set_setting("session_verified", True)
                    return {"message": message}
                except Failure as exc:
                    if exc.restricted:
                        self.worker._restriction(exc)
                        self.store.set_setting("session_verified", False)
                    raise
                finally:
                    engine.close()
            finally:
                self.worker.network_lock.release()
        if path == "/api/shutdown":
            self.worker.pause()
            threading.Thread(target=self.http.shutdown, daemon=True).start()
            return {"message": "Server is shutting down. Unfinished work remains saved; close this tab."}
        raise ValueError("Unknown action")


def make_server(app, port):
    class Handler(BaseHTTPRequestHandler):
        server_version = "InstagramLocal"
        sys_version = ""

        def log_message(self, *args):
            pass  # no HTTP logs: request content and capabilities stay private

        def setup(self):
            super().setup()
            self.connection.settimeout(15)

        def send(self, status, data, content_type="application/json; charset=utf-8"):
            encoded = json.dumps(data).encode("utf-8") if isinstance(data, dict) else data
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            if self.path=='/api/library/access' and status==200:
                self.send_header('Set-Cookie',f'library_media={app.media_token}; HttpOnly; SameSite=Strict; Path=/media/')
            self.security_headers()
            self.end_headers()
            try:
                if self.command!='HEAD':self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def security_headers(self):
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cross-Origin-Resource-Policy", "same-origin")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; media-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'")

        def safe_host(self):
            return self.headers.get("Host") == f"127.0.0.1:{self.server.server_port}"

        def do_GET(self):
            if not self.safe_host():
                return self.send(403, {"error": "Invalid host"})
            if urlsplit(self.path).path == "/health":
                nonce = parse_qs(urlsplit(self.path).query).get("nonce", [""])[0][:100]
                proof = hmac.new(app.token.encode(), nonce.encode(), hashlib.sha256).hexdigest() if nonce else ""
                return self.send(200, {"app": "instagram-local-v1", "proof": proof})
            if self.path.startswith('/media/'):
                return self.serve_media()
            files = {"/": ("index.html", "text/html; charset=utf-8"), "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                     '/library.js':('library.js','text/javascript; charset=utf-8'), '/library.css':('library.css','text/css; charset=utf-8'),
                     "/style.css": ("style.css", "text/css; charset=utf-8")}
            if self.path not in files:
                return self.send(404, {"error": "Not found"})
            name, kind = files[self.path]
            self.send(200, (STATIC / name).read_bytes(), kind)

        def do_HEAD(self):
            if not self.safe_host() or not self.path.startswith('/media/'):
                return self.send(403,{'error':'Invalid media request'})
            self.serve_media(head=True)

        def media_authorized(self):
            try:
                cookie=SimpleCookie(self.headers.get('Cookie',''))
                value=cookie['library_media'].value if 'library_media' in cookie else ''
                origin=f'http://127.0.0.1:{self.server.server_port}'
                return (self.safe_host() and self.headers.get('Sec-Fetch-Site')=='same-origin'
                        and self.headers.get('Origin',origin)==origin and hmac.compare_digest(value,app.media_token))
            except Exception:
                return False

        def serve_media(self,head=False):
            if not self.media_authorized():return self.send(403,{'error':'Unauthorized media request'})
            match=re.fullmatch(r'/media/([1-9]\d{0,17})/(thumb|\d{1,4})(?:\?v=[A-Za-z0-9_-]{1,40})?',self.path)
            if not match:return self.send(404,{'error':'Media route not found'})
            post_id=int(match[1])
            headers_sent=False
            try:
                if match[2]=='thumb':
                    data,kind=app.library.thumbnail(post_id)
                    return self.send(200,data,kind)
                with app.library.open_media(post_id,int(match[2])) as (stream,offset,media,row):
                    size=media['size']
                    start,end=0,size-1
                    etag='"'+hashlib.sha256((row['signature']+str(media['position'])).encode()).hexdigest()[:32]+'"'
                    range_value=self.headers.get('Range')
                    if self.headers.get('If-Range',etag)!=etag:range_value=None
                    if range_value:
                        requested=re.fullmatch(r'bytes=(\d*)-(\d*)',range_value)
                        valid=bool(requested and (requested[1] or requested[2]))
                        if valid:
                            if requested[1]:start=int(requested[1]);end=min(int(requested[2]),size-1) if requested[2] else size-1
                            else:start=max(0,size-int(requested[2]))
                            valid=0<=start<=end<size
                        if not valid:
                            self.send_response(416);self.send_header('Content-Range',f'bytes */{size}');self.send_header('Content-Length','0');self.security_headers();self.end_headers();return
                    stream.seek(offset+start)
                    self.send_response(206 if range_value else 200)
                    self.send_header('Content-Type',media['mime'])
                    self.send_header('Content-Length',str(end-start+1))
                    self.send_header('Accept-Ranges','bytes');self.send_header('ETag',etag)
                    if range_value:self.send_header('Content-Range',f'bytes {start}-{end}/{size}')
                    self.security_headers();self.end_headers();headers_sent=True
                    if head:return
                    remaining=end-start+1
                    crc=0
                    while remaining:
                        block=stream.read(min(256*1024,remaining))
                        if not block:raise ValueError('Media transfer ended early')
                        self.wfile.write(block);remaining-=len(block);crc=zlib.crc32(block,crc)
                    if start==0 and end==size-1 and 'crc' in media and crc!=media['crc']:
                        app.library._failure(post_id,'corrupt','ZIP media failed its integrity check',row['signature'])
            except (BrokenPipeError,ConnectionResetError,TimeoutError):
                pass
            except (ValueError,OSError,KeyError):
                if not headers_sent:self.send(404,{'error':'Local media unavailable. Refresh the Library for details.'})
                else:app.library._failure(post_id,'corrupt','Media transfer could not complete; check the local file')

        def do_OPTIONS(self):
            self.send(403, {"error": "Cross-origin access is disabled"})

        def do_POST(self):
            origin = f"http://127.0.0.1:{self.server.server_port}"
            token = self.headers.get("X-App-Token", "")
            if not self.safe_host() or self.headers.get("Origin") != origin or self.headers.get("Sec-Fetch-Site", "same-origin") != "same-origin" or not hmac.compare_digest(token, app.token):
                return self.send(403, {"error": "Unauthorized local action. Open the app with Launch.cmd."})
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.send(415, {"error": "JSON required"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 6_000_000 or self.headers.get("Transfer-Encoding"):
                    return self.send(413, {"error": "Request limit is 6 MB"})
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict):
                    raise ValueError("JSON object required")
                self.send(200, app.dispatch(self.path, body))
            except Failure as exc:
                self.send(400, {"error": exc.message, "kind": exc.kind})
            except (ValueError, TypeError, KeyError):
                # ValueError from our own validators has safe fixed wording;
                # JSONDecodeError can contain snippets, so never reflect it.
                import sys
                exc = sys.exc_info()[1]
                message = str(exc) if type(exc) is ValueError else "Invalid request"
                self.send(400, {"error": message})
            except Exception:
                self.send(500, {"error": "Local operation failed. Check folder access and Windows session storage."})
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    app.http = server
    return server
