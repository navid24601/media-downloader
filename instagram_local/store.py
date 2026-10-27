"""Small SQLite queue; bounded UI queries and atomic enumeration checkpoints."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from .core import now


class Store:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS sources (
          id INTEGER PRIMARY KEY, kind TEXT, target TEXT, label TEXT, maximum INTEGER,
          cursor TEXT, offset INTEGER DEFAULT 0, emitted INTEGER DEFAULT 0,
          unpinned INTEGER DEFAULT 0,
          phase TEXT DEFAULT 'timeline',
          status TEXT DEFAULT 'pending', error TEXT DEFAULT '', retries INTEGER DEFAULT 0,
          options TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS queue (
          id INTEGER PRIMARY KEY, shortcode TEXT NOT NULL, url TEXT NOT NULL,
          status TEXT DEFAULT 'pending', attempts INTEGER DEFAULT 0,
          error TEXT DEFAULT '', kind TEXT DEFAULT '', output TEXT DEFAULT '',
          options TEXT NOT NULL, updated TEXT NOT NULL, base TEXT DEFAULT '');
        CREATE INDEX IF NOT EXISTS queue_status ON queue(status, id);
        CREATE INDEX IF NOT EXISTS queue_code ON queue(shortcode);
        CREATE TABLE IF NOT EXISTS downloads (
          shortcode TEXT PRIMARY KEY, output TEXT NOT NULL, extras TEXT NOT NULL DEFAULT '[]', time TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS candidates (
          source_id INTEGER, shortcode TEXT, published INTEGER, PRIMARY KEY(source_id,shortcode));
        ''')
        from .library import schema
        schema(self.db)
        # Closure/restart never initiates network activity; running work becomes pending.
        with self.tx() as db:
            db.execute("UPDATE queue SET status='pending' WHERE status='running'")
            db.execute("UPDATE sources SET status='pending' WHERE status='running'")

    @contextmanager
    def tx(self):
        with self.lock:
            with self.db:
                yield self.db

    def rows(self, sql, args=()):
        with self.lock:
            return [dict(x) for x in self.db.execute(sql, args)]

    def one(self, sql, args=()):
        rows = self.rows(sql, args)
        return rows[0] if rows else None

    def setting(self, key, default=None):
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return json.loads(row["value"]) if row else default

    def set_setting(self, key, value):
        with self.tx() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", (key, json.dumps(value)))

    def completed(self, code):
        row = self.one("SELECT * FROM downloads WHERE shortcode=?", (code,))
        return bool(row and Path(row["output"]).is_file() and all(Path(p).is_file() for p in json.loads(row["extras"])))

    def enqueue(self, items, options, db=None):
        if db is None:
            with self.tx() as db:
                return self.enqueue(items, options, db)
        added = skipped = 0
        for code, url in items:
            active = db.execute("SELECT 1 FROM queue WHERE shortcode=? AND status IN ('pending','running')", (code,)).fetchone()
            if active:
                skipped += 1
                continue
            done = options.get('task')!='details' and not options.get("again") and self.completed(code)
            db.execute("INSERT INTO queue(shortcode,url,status,options,updated) VALUES(?,?,?,?,?)", (code, url, "skipped" if done else "pending", json.dumps(options), now()))
            skipped += int(done)
            added += int(not done)
        return added, skipped

    def update_item(self, item_id, **values):
        allowed = {"status", "attempts", "error", "kind", "output", "base"}
        assert set(values) <= allowed
        values["updated"] = now()
        with self.tx() as db:
            db.execute("UPDATE queue SET " + ",".join(k + "=?" for k in values) + " WHERE id=?", (*values.values(), item_id))

    def add_source(self, kind, target, label, maximum, options):
        options = {**options, 'source': {'kind':kind, 'label':label}}
        with self.tx() as db:
            db.execute("INSERT INTO sources(kind,target,label,maximum,options) VALUES(?,?,?,?,?)", (kind, target, label, maximum, json.dumps(options)))

    def checkpoint(self, source, code, cursor, offset, raw=None):
        with self.tx() as db:
            latest_profile = source["kind"] == "profile" and source["maximum"]
            unpinned = 0
            if latest_profile:
                published = (raw or {}).get("taken_at")
                if not isinstance(published, (int, float)):
                    from .core import Failure
                    raise Failure("engine", "Profile item has no publication time; Latest N cannot be selected reliably")
                db.execute("INSERT OR IGNORE INTO candidates VALUES(?,?,?)", (source["id"], code, published))
                unpinned = int(not ((raw or {}).get("timeline_pinned_user_ids") or (raw or {}).get("clips_tab_pinned_user_ids")))
            else:
                self.enqueue([(code, f"https://www.instagram.com/p/{code}/")], json.loads(source["options"]), db)
            db.execute("UPDATE sources SET cursor=?,offset=?,emitted=emitted+1,unpinned=unpinned+?,retries=0 WHERE id=?", (cursor, offset, unpinned, source["id"]))

    def finish_source(self, source):
        with self.tx() as db:
            if source["kind"] == "profile" and source["phase"] == "timeline":
                # Profile Reels can be absent from the timeline. Enumerate both
                # streams; latest candidates are combined before choosing N.
                db.execute("UPDATE sources SET phase='reels',cursor=NULL,offset=0,unpinned=0,error='' WHERE id=?", (source["id"],))
                return
            if source["kind"] == "profile" and source["maximum"]:
                codes = db.execute("SELECT shortcode FROM candidates WHERE source_id=? ORDER BY published DESC,shortcode LIMIT ?", (source["id"], source["maximum"]))
                for row in codes:
                    self.enqueue([(row[0], f"https://www.instagram.com/p/{row[0]}/")], json.loads(source["options"]), db)
                db.execute("DELETE FROM candidates WHERE source_id=?", (source["id"],))
            db.execute("UPDATE sources SET status='done',error='' WHERE id=?", (source["id"],))

    def success(self, item, output, extras, post=None):
        with self.tx() as db:
            completed_at = now()
            db.execute("INSERT OR REPLACE INTO downloads VALUES(?,?,?,?)", (item["shortcode"], str(output), json.dumps([str(p) for p in extras]), completed_at))
            db.execute("UPDATE queue SET status='done',output=?,error='',kind='',updated=? WHERE id=?", (str(output), completed_at, item["id"]))
            from .library import register
            register(db,item['shortcode'],output,[str(p) for p in extras],completed_at,post=post,source=json.loads(item['options']).get('source'))

    def snapshot(self, before=None):
        counts = {r["status"]: r["n"] for r in self.rows("SELECT status,count(*) n FROM queue GROUP BY status")}
        return dict(counts=counts, queue=self.rows("SELECT id,shortcode,url,status,attempts,error,kind,output,updated FROM queue WHERE status IN ('pending','running','failed') ORDER BY id LIMIT 100"),
                    sources=self.rows("SELECT id,kind,label,maximum,emitted,status,error FROM sources ORDER BY id DESC LIMIT 50"),
                    history=self.rows("SELECT id,url,output,status,error,updated FROM queue WHERE status IN ('done','details_done','skipped','failed','canceled') AND id<? ORDER BY id DESC LIMIT 50", (before or 9223372036854775807,)))
