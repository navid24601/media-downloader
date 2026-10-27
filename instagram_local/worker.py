"""One sequential worker, durable checkpoints, transactional media finalization."""
import hashlib
import itertools
import json
import os
import shutil
import threading
import time
import zipfile
from pathlib import Path
from .core import Failure, basename, now
from .engine import Engine


class Worker:
    def __init__(self, store, vault, engine_factory=Engine, sleeper=None):
        self.store, self.vault, self.engine_factory = store, vault, engine_factory
        self.lock = threading.RLock()
        self.network_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.active = False
        self.busy = False
        self.cancel_requested = False
        self.batch_done = 0
        self.progress = None
        self.message = "Ready. Unfinished work is saved. Choose Resume to make network requests."
        self.engine = None
        self.sleeper = sleeper or self.stop_event.wait
        self.thread = threading.Thread(target=self._loop, daemon=True, name="download-worker")

    def start(self):
        self.thread.start()

    def resume(self):
        with self.lock:
            retry_at = self.store.setting("retry_at", 0)
            if time.time() < retry_at:
                raise ValueError("The server requested a wait. Resume after " + time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(retry_at)))
            self.active, self.cancel_requested, self.batch_done = True, False, 0
            self.message = "Working sequentially; the batch pauses after 50 posts."

    def pause(self):
        with self.lock:
            self.active = False
            self.message = "Pausing after the current post/request. Completed files are kept." if self.busy else "Paused. Unfinished work is saved; choose Resume when ready."

    def cancel(self):
        with self.lock:
            self.active = False
            self.cancel_requested = True
            self.message = "Canceling remaining work after the current post/request." if self.busy else "Remaining work canceled. Completed files are kept."
        self._cancel_pending()

    def _cancel_pending(self):
        with self.store.tx() as db:
            db.execute("UPDATE queue SET status='canceled' WHERE status='pending'")
            db.execute("UPDATE sources SET status='canceled' WHERE status IN ('pending','running')")

    def close(self):
        self.pause()
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=3)

    def snapshot(self):
        with self.lock:
            return dict(active=self.active, busy=self.busy, message=self.message, progress=self.progress, batch_done=self.batch_done, retry_at=self.store.setting("retry_at", 0))

    def retry_failed(self):
        with self.store.tx() as db:
            db.execute("UPDATE queue SET status='pending',attempts=0,error='',kind='' WHERE status='failed'")
            db.execute("UPDATE sources SET status='pending',retries=0,error='' WHERE status='failed'")

    def _restriction(self, failure):
        with self.lock:
            self.active = False
            self.message = failure.message
            self.store.set_setting("retry_at", max(failure.retry_at, self.store.setting("retry_at", 0)))

    def _loop(self):
        while not self.stop_event.wait(.25):
            with self.lock:
                if not self.active:
                    continue
                if self.batch_done >= 50:
                    self.active = False
                    self.message = "Batch of 50 finished. Review results and Resume for the next batch."
                    continue
                self.busy = True
            try:
                with self.network_lock:
                    if not self.active:
                        continue
                    self.engine = self.engine_factory(self.vault.load())
                    item = self.store.one("SELECT * FROM queue WHERE status='pending' ORDER BY id LIMIT 1")
                    if item:
                        self._item(item)
                        self.batch_done += 1
                    else:
                        source = self.store.one("SELECT * FROM sources WHERE status='pending' ORDER BY id LIMIT 1")
                        if source:
                            self._source(source)
                        else:
                            self.active = False
                            self.message = "Queue finished. Failed items can be queued again with Retry failed."
            except Exception:
                # Raw third-party exceptions can contain secrets. Never stringify.
                self.active = False
                self.message = "Local processing failed. Check the output folder is writable and session storage is accessible; see README."
            finally:
                if self.engine:
                    self.engine.close()
                    self.engine = None
                with self.lock:
                    self.busy = False
                    self.progress = None
                    if self.cancel_requested:
                        self._cancel_pending()
                        self.message = "Remaining work canceled. Completed files are kept."
                    elif self.message.startswith("Pausing"):
                        self.message = "Paused. Unfinished work is saved; choose Resume when ready."

    def _source(self, source):
        options = json.loads(source["options"])
        maximum = source["maximum"]
        latest_profile = source["kind"] == "profile" and maximum
        previous = source["unpinned"] if latest_profile else source["emitted"]
        remaining = maximum - previous if maximum else 50
        if remaining <= 0:
            self.store.finish_source(source)
            return
        self.progress = dict(label=f"Enumerating {source['label']}", done=0, total=None)
        seen = 0
        iterator = None
        try:
            iterator = self.engine.enumerate(source)
            for code, cursor, offset, raw in itertools.islice(iterator, min(50, remaining)):
                # A request already underway may finish after Pause/Cancel, but
                # its checkpoint is committed only with its discovered item.
                self.store.checkpoint(source, code, cursor, offset, raw)
                seen += 1
                if not self.active or self.stop_event.is_set():
                    break
            if self.active:
                current = self.store.one("SELECT * FROM sources WHERE id=?", (source["id"],))
                counted = current["unpinned"] if latest_profile else current["emitted"]
                complete = seen < min(50, remaining) or maximum and counted >= maximum
                if complete and current["status"] != "canceled":
                    self.store.finish_source(source)
                pending = self.store.one("SELECT count(*) n FROM queue WHERE status='pending'")["n"]
                if seen >= 50 and pending == 0:
                    self.active = False
                    self.message = "Scanned 50 source entries; completed posts were skipped. Resume to scan the next batch."
        except Failure as exc:
            with self.store.tx() as db:
                db.execute("UPDATE sources SET status=?,error=?,retries=retries+1 WHERE id=?", ("pending" if exc.restricted else "failed", exc.message, source["id"]))
            if exc.restricted:
                self._restriction(exc)
            elif exc.kind == "network" and source["retries"] < 2 and self.active:
                self.sleeper((5, 15)[source["retries"]])
                with self.store.tx() as db:
                    db.execute("UPDATE sources SET status='pending' WHERE id=? AND status='failed'", (source["id"],))
        finally:
            if iterator:
                iterator.close()

    def _item(self, item):
        options = json.loads(item["options"])
        details = options.get('task')=='details'
        if not details and not options.get("again") and self.store.completed(item["shortcode"]):
            self.store.update_item(item["id"], status="skipped")
            return
        for attempt in range(item["attempts"], 3):
            self.store.update_item(item["id"], status="running", attempts=attempt + 1, error="", kind="")
            try:
                post = self.engine.post(item["url"])
                if post.shortcode != item["shortcode"]:
                    raise Failure("engine", "The returned post identity differs from the queued link")
                if details:
                    with self.store.tx() as db:
                        target=db.execute('SELECT shortcode FROM library_posts WHERE id=?',(options['library_id'],)).fetchone()
                        if not target or target[0]!=post.shortcode:
                            raise Failure('local','The selected Library record changed; metadata was not applied.')
                        db.execute('UPDATE library_posts SET username=coalesce(username,?),caption=coalesce(caption,?) WHERE id=?',
                                   (post.username if post.username!='unknown' else None,post.caption,options['library_id']))
                        db.execute("UPDATE queue SET status='details_done',kind='details',error='',updated=? WHERE id=?",(now(),item['id']))
                    return
                output, extras = self._save(item, post, options)
                self.store.success(item, output, extras, post=post)
                try:
                    shutil.rmtree(Path(options["folder"]) / ".incomplete" / str(item["id"]))
                except OSError:
                    pass
                return
            except Failure as exc:
                status = "pending" if exc.restricted else "failed"
                self.store.update_item(item["id"], status=status, error=exc.message, kind=exc.kind)
                if exc.restricted:
                    # Restrictions have no automatic retry, including on Resume.
                    self.store.update_item(item["id"], attempts=0)
                    self._restriction(exc)
                    return
                if exc.kind != "network" or attempt >= 2 or not self.active or self.stop_event.is_set():
                    return
                self.progress = dict(label="Temporary connection failure; backing off before retry", done=0, total=None)
                self.sleeper((5, 15)[attempt])
                if not self.active or self.stop_event.is_set():
                    self.store.update_item(item["id"], status="pending", attempts=0)
                    return
                self.engine.close()
                self.engine = self.engine_factory(self.vault.load())
            except OSError:
                self.store.update_item(item["id"], status="failed", error="Cannot write complete output. Check folder permissions and free disk space; partial items are retained.", kind="disk")
                return
            except Exception:
                self.store.update_item(item["id"], status="failed", error="Unexpected processing failure; no successful download was recorded.", kind="local")
                return
        # A restart between attempts must not leave an exhausted item pending.
        self.store.update_item(item["id"], status="failed", error="Automatic retry limit reached. Use Retry failed for another explicit attempt.", kind="network")

    def _save(self, item, post, options):
        folder = Path(options["folder"])
        folder.mkdir(parents=True, exist_ok=True)
        stage = folder / ".incomplete" / str(item["id"])
        stage.mkdir(parents=True, exist_ok=True)
        (stage / "INCOMPLETE.txt").write_text("Incomplete download. This is not a successful output. Completed items are reused on Retry failed.\n", encoding="utf-8")
        base = item["base"] or basename(post.username, post.shortcode)
        if not item["base"]:
            ext = "zip" if post.carousel else post.media[0].extension
            candidate, n = base, 2
            while (folder / f"{candidate}.{ext}").exists() or (folder / f"{candidate}.txt").exists():
                candidate = f"{base} ({n})"
                n += 1
            base = candidate
            self.store.update_item(item["id"], base=base)
            item["base"] = base
        manifest_path = stage / "completed.json"
        try:
            receipts = json.loads(manifest_path.read_text("utf-8"))
        except (OSError, ValueError):
            receipts = {}
        paths = []
        for index, media in enumerate(post.media, 1):
            name = f"{index:03d}.{media.extension}"
            target = stage / name
            receipt = receipts.get(name, {})
            reusable = target.is_file() and receipt.get("id") == media.identity and receipt.get("size") == target.stat().st_size
            if reusable:
                reusable = file_hash(target) == receipt.get("sha256")
            if not reusable:
                temporary = stage / f"{index:03d}.part.{media.extension}"
                def report(done, total):
                    self.progress = dict(label=f"{post.shortcode} · media {index}/{len(post.media)}", done=done, total=total)
                self.engine.download(media, temporary, report)
                if not temporary.is_file() or not temporary.stat().st_size:
                    raise Failure("incomplete", "A media item is empty; no complete output was created")
                os.replace(temporary, target)
                receipts[name] = dict(id=media.identity, size=target.stat().st_size, sha256=file_hash(target))
                receipt_temp = stage / "completed.tmp"
                receipt_temp.write_text(json.dumps(receipts), encoding="utf-8")
                os.replace(receipt_temp, manifest_path)
            paths.append(target)
        # Persist finalization intent before moving files. Recovery after a crash
        # recognizes only a completely finalized file with matching digest.
        caption = f"{item['url']}\n\n{post.caption}\n"
        extras = []
        if post.carousel:
            temporary = stage / "complete.zip.part"
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
                for path in paths:
                    archive.write(path, path.name)
                if options.get("caption"):
                    archive.writestr("caption.txt", caption)
            output = folder / f"{base}.zip"
        else:
            temporary = stage / "complete.media.part"
            shutil.copyfile(paths[0], temporary)
            output = folder / f"{base}.{post.media[0].extension}"
            if options.get("caption"):
                extra = folder / f"{base}.txt"
                caption_temp = stage / "caption.part"
                caption_temp.write_text(caption, encoding="utf-8")
                if extra.exists():
                    if extra.read_bytes() != caption_temp.read_bytes():
                        raise Failure("disk", "Caption filename was occupied while downloading. Rename that file, then retry.")
                    caption_temp.unlink()
                else:
                    os.rename(caption_temp, extra)
                extras.append(extra)
        # base is reserved in SQLite. If a file appeared since reservation,
        # preserve it; never overwrite unrelated user output.
        if output.exists():
            previous = receipts.get("final", {})
            if previous.get("sha256") != file_hash(output):
                raise Failure("disk", "Output filename was occupied while downloading. Rename the existing file, then retry.")
        digest = file_hash(temporary)
        receipts["final"] = dict(sha256=digest)
        manifest_path.write_text(json.dumps(receipts), encoding="utf-8")
        if output.exists():
            temporary.unlink()
        else:
            # On Windows rename refuses an existing destination; this also
            # protects against a file appearing after the collision check.
            os.rename(temporary, output)
        # Keep stage until success is recorded by caller, so restart can recover
        # the finalization intent. Cleanup is performed after that transaction.
        return output, extras


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()
