"""Windows launcher and single-instance localhost server."""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

ROOT = Path(__file__).resolve().parent


def restrict_directory(path):
    path.mkdir(exist_ok=True)
    if os.name != "nt":
        raise RuntimeError("This application requires Windows 11")
    # Resolve the current SID without interpolating a username into a shell.
    result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    import csv
    sid = next(csv.reader([result.stdout.strip()]))[1]
    if not sid.startswith("S-1-"):
        raise RuntimeError("Could not identify the Windows account")
    subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F"], capture_output=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)


def verified_instance(info):
    nonce = secrets.token_hex(24)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{int(info['port'])}/health?nonce={nonce}", timeout=2) as response:
        data = json.load(response)
    expected = hmac.new(info["token"].encode(), nonce.encode(), hashlib.sha256).hexdigest()
    return data.get("app") == "instagram-local-v1" and hmac.compare_digest(data.get("proof", ""), expected)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true", help="Start without opening a browser")
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".local", help=argparse.SUPPRESS)
    args = parser.parse_args()
    os.chdir(ROOT)
    from instagram_local.security import Vault
    from instagram_local.store import Store
    from instagram_local.worker import Worker
    from instagram_local.server import Application, make_server
    import msvcrt

    local = args.state_dir.resolve()
    restrict_directory(local)
    runtime = Vault(local / "runtime.bin")
    lock = (local / "server.lock").open("a+b")
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        try:
            info = runtime.load()
            if not verified_instance(info):
                raise RuntimeError()
            if not args.no_browser:
                webbrowser.open(f"http://127.0.0.1:{info['port']}/#{info['token']}")
            print("Instagram Local is already running. Use its browser tab to Resume or Shut down.")
            return
        except Exception:
            print("Another instance is starting or could not be verified. Wait a moment and try Launch.cmd again.")
            return

    token = secrets.token_urlsafe(32)
    store = Store(local / "queue.sqlite3")
    vault = Vault(local / "credentials.bin")
    worker = Worker(store, vault)
    app = Application(store, vault, worker, token)
    server = None
    for port in range(8765, 8796):
        try:
            server = make_server(app, port)
            break
        except OSError:
            continue
    if server is None:
        raise RuntimeError("No available localhost port in 8765-8795. Close unused local servers and retry.")
    runtime.save({"port": server.server_port, "token": token})
    worker.start()
    app.library.start()
    print(f"Instagram Local is running at http://127.0.0.1:{server.server_port}/")
    print("Use Launch.cmd to reopen its authorized browser tab. Use Shut down in the app, or Ctrl+C here.")
    print("The queue stays paused until you choose Resume. Closing the browser does not stop the server.")
    def open_ready():
        for _ in range(30):
            try:
                if verified_instance({"port": server.server_port, "token": token}):
                    webbrowser.open(f"http://127.0.0.1:{server.server_port}/#{token}")
                    return
            except Exception:
                time.sleep(.2)
    if not args.no_browser:
        threading.Thread(target=open_ready, daemon=True).start()
    try:
        server.serve_forever(poll_interval=.25)
    except KeyboardInterrupt:
        pass
    finally:
        worker.close()
        app.library.close()
        server.server_close()
        # A still-running daemon request may finish while we exit; unfinished
        # queue state and staged media recover on the next manual Resume.
        runtime.path.unlink(missing_ok=True)
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        lock.close()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # Never print raw exceptions: network and vault errors may be sensitive.
        print("Startup failed. Run Setup.cmd, confirm this folder is writable, and check the README troubleshooting section.")
        sys.exit(1)
