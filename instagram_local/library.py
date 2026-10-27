"""Offline index of recorded outputs. This module never imports the network engine."""
from contextlib import contextmanager
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import queue
import re
import secrets
import stat
import struct
import subprocess
import threading
import time
import zipfile

MIME = {'.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
        '.webp': 'image/webp', '.avif': 'image/avif', '.mp4': 'video/mp4'}
NAME = re.compile(r'^(?P<user>[A-Za-z0-9_.]{1,30})-(?P<code>[A-Za-z0-9_-]{5,32})-(?P<date>\d{4}-\d{2}-\d{2})(?: \(\d+\))?\.[^.]+$')
PLACEHOLDER = b'<svg xmlns="http://www.w3.org/2000/svg" width="320" height="320" viewBox="0 0 320 320"><rect width="320" height="320" fill="#20242c"/><rect x="116" y="123" width="88" height="64" rx="4" fill="none" stroke="#70798b" stroke-width="3"/><path d="m122 180 27-27 18 16 17-20 15 31" fill="none" stroke="#70798b" stroke-width="3"/><text x="160" y="223" text-anchor="middle" fill="#a1a9b8" font-family="Arial" font-size="13">No preview</text></svg>'


def schema(db):
    db.executescript('''
    CREATE TABLE IF NOT EXISTS library_posts (
      id INTEGER PRIMARY KEY, output TEXT NOT NULL, path_key TEXT NOT NULL UNIQUE,
      shortcode TEXT, username TEXT, caption TEXT, url TEXT, downloaded_at TEXT,
      download_date TEXT, filename TEXT NOT NULL, extras TEXT NOT NULL DEFAULT '[]',
      favorite INTEGER NOT NULL DEFAULT 0, hidden INTEGER NOT NULL DEFAULT 0,
      status TEXT NOT NULL DEFAULT 'pending', error TEXT NOT NULL DEFAULT '',
      signature TEXT NOT NULL DEFAULT '', media_count INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS library_order ON library_posts(hidden,downloaded_at DESC,id DESC);
    CREATE INDEX IF NOT EXISTS library_user ON library_posts(username COLLATE NOCASE);
    CREATE INDEX IF NOT EXISTS library_favorite ON library_posts(hidden,favorite,downloaded_at DESC,id DESC);
    CREATE TABLE IF NOT EXISTS library_media (
      post_id INTEGER NOT NULL, position INTEGER NOT NULL, member TEXT,
      mime TEXT NOT NULL, kind TEXT NOT NULL, size INTEGER NOT NULL,
      PRIMARY KEY(post_id,position));
    CREATE TABLE IF NOT EXISTS library_sources (
      post_id INTEGER NOT NULL, kind TEXT NOT NULL, label TEXT NOT NULL,
      PRIMARY KEY(post_id,kind,label));
    CREATE TABLE IF NOT EXISTS library_imports (
      origin TEXT NOT NULL, record_key TEXT NOT NULL, stamp TEXT NOT NULL,
      PRIMARY KEY(origin,record_key));
    CREATE TABLE IF NOT EXISTS library_meta (key TEXT PRIMARY KEY,value TEXT NOT NULL);
    ''')


def date_from_name(filename):
    match = NAME.fullmatch(filename)
    if not match:
        return None, None, None
    try:
        datetime.strptime(match['date'], '%Y-%m-%d')
    except ValueError:
        return None, None, None
    user=match['user']
    # v1 prepended '_' to Windows device names. A filename cannot distinguish
    # that transformation from an account whose real name starts with '_'.
    reserved={'_CON','_PRN','_AUX','_NUL',*(f'_COM{i}' for i in range(1,10)),*(f'_LPT{i}' for i in range(1,10))}
    if user=='unknown' or user.split('.')[0].upper() in reserved:user=None
    return user, match['code'], match['date']


def register(db, code, output, extras, downloaded_at, *, post=None, source=None):
    """Database-only upsert; called in the same transaction as download success."""
    path = Path(output)
    user, name_code, day = date_from_name(path.name)
    if name_code != code:
        user, day = None, None
    if day is None:
        try:
            recorded=datetime.fromisoformat(downloaded_at)
            if recorded.tzinfo is not None:day=recorded.astimezone().date().isoformat()
        except (ValueError,TypeError):
            pass
    url = f'https://www.instagram.com/p/{code}/' if re.fullmatch(r'[A-Za-z0-9_-]{5,32}', code or '') else None
    if post is not None:
        user = post.username if post.username != 'unknown' else None
        caption = post.caption
        # Completion time is recorded separately from the filename's first-attempt date.
        day = datetime.now().astimezone().date().isoformat()
    else:
        caption = None
    key = os.path.normcase(os.path.abspath(str(path)))
    db.execute('''INSERT INTO library_posts(output,path_key,shortcode,username,caption,url,downloaded_at,download_date,filename,extras)
      VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(path_key) DO UPDATE SET
      extras=excluded.extras, shortcode=excluded.shortcode, url=excluded.url,
      username=coalesce(library_posts.username,excluded.username),
      caption=coalesce(library_posts.caption,excluded.caption),
      downloaded_at=max(excluded.downloaded_at,library_posts.downloaded_at),download_date=coalesce(library_posts.download_date,excluded.download_date),
      status=CASE WHEN excluded.downloaded_at!=library_posts.downloaded_at THEN 'pending' ELSE library_posts.status END''',
      (str(path),key,code,user,caption,url,downloaded_at,day,path.name,json.dumps(extras)))
    row = db.execute('SELECT id FROM library_posts WHERE path_key=?', (key,)).fetchone()
    post_id = row[0]
    if source and source.get('kind') in {'links','file','profile','saved','collection'}:
        db.execute('INSERT OR IGNORE INTO library_sources VALUES(?,?,?)', (post_id,source['kind'],str(source.get('label',''))[:150]))
    if post is not None:
        # Only non-sensitive media metadata; never retain Media.url/manifest.
        db.execute("UPDATE library_posts SET username=?,caption=?,download_date=?,media_count=?,status='pending' WHERE id=?",(user,caption,day,len(post.media),post_id))
        db.execute('DELETE FROM library_media WHERE post_id=?',(post_id,))
        for position,media in enumerate(post.media):
            mime=MIME.get('.'+media.extension,'application/octet-stream')
            member=f'{position+1:03d}.{media.extension}' if post.carousel else None
            db.execute('INSERT INTO library_media VALUES(?,?,?,?,?,?)',(post_id,position,member,mime,mime.split('/')[0],0))
    return post_id


def safe_path(value):
    """Recorded local media only; deny staging, UNC, symlinks, and junctions."""
    path = Path(value)
    if not path.is_absolute() or str(path).startswith(('\\\\','//')) or any(p.lower() == '.incomplete' or p == '..' for p in path.parts):
        raise ValueError('Incomplete or non-local output is not eligible for the Library')
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0) & 0x400:
            raise ValueError('Links and junctions are not served or deleted by the Library')
    return path


def signature(path):
    info = path.stat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('The recorded output is not a regular file')
    return f'{info.st_size}:{info.st_mtime_ns}:{info.st_ino}'


def media_type(name, header):
    extension = Path(name).suffix.lower()
    valid = {'.jpg': header.startswith(b'\xff\xd8\xff'), '.jpeg': header.startswith(b'\xff\xd8\xff'),
             '.png': header.startswith(b'\x89PNG\r\n\x1a\n'),
             '.webp': header.startswith(b'RIFF') and header[8:12] == b'WEBP',
             '.avif': header[4:8] == b'ftyp' and (b'avif' in header or b'avis' in header),
             '.mp4': header[4:8] == b'ftyp'}
    if not valid.get(extension):
        raise ValueError('Media has an unsupported format or damaged header')
    return MIME[extension]


def caption_text(stream):
    text = stream.read(1_000_001)
    if len(text) > 1_000_000:
        return None
    text = text.decode('utf-8-sig',errors='replace')
    # v1 sidecars begin with the original link, then a blank line.
    first, separator, rest = text.partition('\n')
    if re.fullmatch(r'https://(?:www\.)?instagram\.com/(?:p|reel|tv)/[A-Za-z0-9_-]+/',first.strip()):
        return rest.lstrip('\r\n').rstrip('\r\n')
    return text.rstrip('\r\n')


class Library:
    def __init__(self, store, cache_dir=None, cache_bytes=128*1024*1024, cache_files=1000):
        self.store = store
        self.cache = Path(cache_dir or store.path.parent / 'thumbnails')
        self.cache_bytes, self.cache_files = cache_bytes, cache_files
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.started = False
        self.discovered = threading.Event()
        self.requests = queue.Queue(maxsize=96)
        self.requested = set()
        self.confirmations = {}
        self.index_thread = threading.Thread(target=self._index_loop,daemon=True,name='local-library-index')
        self.thumb_thread = threading.Thread(target=self._thumb_loop,daemon=True,name='local-thumbnail-cache')

    def start(self):
        with self.lock:
            if self.started:
                return
            try:self.prune_cache()
            except (OSError,ValueError):pass
            self.started = True
            self.index_thread.start()
            self.thumb_thread.start()

    def close(self):
        self.stop.set()
        for thread in (self.index_thread,self.thumb_thread):
            if thread.is_alive():
                thread.join(timeout=2)

    def sync_records(self, limit=100):
        """Small restartable database-only migration batches; no file decoding."""
        count = 0
        rows = self.store.rows('''SELECT d.* FROM downloads d LEFT JOIN library_imports i
          ON i.origin='downloads' AND i.record_key=d.shortcode
          WHERE i.stamp IS NULL OR i.stamp!=d.time||char(9)||d.output||char(9)||d.extras LIMIT ?''',(limit,))
        with self.store.tx() as db:
            for row in rows:
                register(db,row['shortcode'],row['output'],json.loads(row['extras']),row['time'])
                db.execute("INSERT OR REPLACE INTO library_imports VALUES('downloads',?,?)",(row['shortcode'],row['time']+'\t'+row['output']+'\t'+row['extras']))
                count += 1
            cursor_row = db.execute("SELECT value FROM library_meta WHERE key='queue_cursor'").fetchone()
            cursor = int(cursor_row[0]) if cursor_row else 0
            history = db.execute("SELECT id,shortcode,output,updated,options FROM queue WHERE id>? ORDER BY id LIMIT ?",(cursor,limit)).fetchall()
            for row in history:
                # Fetch status explicitly: canceled/partial entries must never migrate.
                status = db.execute('SELECT status FROM queue WHERE id=?',(row['id'],)).fetchone()[0]
                if status == 'done' and row['output']:
                    existing = db.execute('SELECT id FROM library_posts WHERE path_key=?',(os.path.normcase(os.path.abspath(row['output'])),)).fetchone()
                    if not existing:
                        options = json.loads(row['options'])
                        extra = [str(Path(row['output']).with_suffix('.txt'))] if options.get('caption') and Path(row['output']).suffix.lower() != '.zip' else []
                        register(db,row['shortcode'],row['output'],extra,row['updated'])
                cursor = row['id']
            if history:
                db.execute("INSERT OR REPLACE INTO library_meta VALUES('queue_cursor',?)",(str(cursor),))
            count += len(history)
        return count

    def _index_loop(self):
        while not self.stop.is_set():
            try:
                migrated = self.sync_records()
                if not migrated:self.discovered.set()
                rows = self.store.rows("SELECT id FROM library_posts WHERE status='pending' ORDER BY id LIMIT 12")
                for row in rows:
                    if self.stop.is_set():
                        return
                    self.index(row['id'])
                if not rows and not migrated:
                    self.stop.wait(1)
            except Exception:
                # Leave pending work retryable. Do not expose paths/decoder output.
                self.stop.wait(2)

    def row(self, post_id):
        if type(post_id) is not int or not 1 <= post_id <= 9223372036854775807:
            raise ValueError('Invalid Library item')
        row = self.store.one('SELECT * FROM library_posts WHERE id=?',(post_id,))
        if row is None:
            raise ValueError('Library item not found')
        return row

    def _failure(self, post_id, status, message, stamp=''):
        with self.store.tx() as db:
            db.execute('UPDATE library_posts SET status=?,error=?,signature=? WHERE id=?',(status,message,stamp,post_id))
            db.execute('DELETE FROM library_media WHERE post_id=?',(post_id,))

    def index(self, post_id):
        with self.lock:
            row = self.row(post_id)
            if row['status'] == 'deleted':
                return
            stamp = ''
            try:
                path = safe_path(row['output'])
                stamp = signature(path)
                if stamp == row['signature'] and row['status'] != 'pending':
                    return
                if path.suffix.lower() not in {*MIME,'.zip'}:
                    self._failure(post_id,'unsupported','Output format is not supported by the Library',stamp)
                    return
                caption = None
                media = []
                if path.suffix.lower() == '.zip':
                    with zipfile.ZipFile(path) as archive:
                        infos = archive.infolist()
                        if len(infos) > 1001:
                            raise ValueError('ZIP contains too many entries')
                        numbered = []
                        names = set()
                        for info in infos:
                            if info.filename in names or info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16):
                                raise ValueError('ZIP has duplicate, encrypted, or linked entries')
                            names.add(info.filename)
                            if info.filename == 'caption.txt':
                                if info.file_size <= 1_000_000:
                                    with archive.open(info) as stream:
                                        caption = caption_text(stream)
                                continue
                            match = re.fullmatch(r'(\d{3,})\.(jpg|jpeg|png|webp|avif|mp4)',info.filename,re.I)
                            if not match or info.file_size <= 0 or info.file_size > 8*1024**3 or info.file_size > max(info.compress_size,1)*1000:
                                raise ValueError('ZIP contains incomplete, unsafe, or unsupported entries')
                            numbered.append((int(match[1]),info))
                        numbered.sort(key=lambda x:x[0])
                        if [n for n,_ in numbered] != list(range(1,len(numbered)+1)) or not numbered:
                            raise ValueError('ZIP media numbering is incomplete or duplicated')
                        for position, info in numbered:
                            with archive.open(info) as stream:
                                mime = media_type(info.filename,stream.read(32))
                            media.append((post_id,position-1,info.filename,mime,mime.split('/')[0],info.file_size))
                else:
                    with path.open('rb') as stream:
                        mime = media_type(path.name,stream.read(32))
                    media.append((post_id,0,None,mime,mime.split('/')[0],path.stat().st_size))
                    sidecar = path.with_suffix('.txt')
                    try:
                        safe_path(str(sidecar))
                        if sidecar.is_file():
                            with sidecar.open('rb') as stream:
                                caption = caption_text(stream)
                    except (OSError,ValueError):
                        pass
                with self.store.tx() as db:
                    db.execute('DELETE FROM library_media WHERE post_id=?',(post_id,))
                    db.executemany('INSERT INTO library_media VALUES(?,?,?,?,?,?)',media)
                    db.execute("UPDATE library_posts SET status='ready',error='',signature=?,media_count=?,caption=coalesce(caption,?) WHERE id=?",(stamp,len(media),caption,post_id))
            except FileNotFoundError:
                self._failure(post_id,'missing','Recorded file is missing or moved. Restore it at the recorded path, then Refresh.')
            except ValueError as exc:
                self._failure(post_id,'incomplete' if '.incomplete' in Path(row['output']).parts else 'corrupt',str(exc),stamp)
            except (OSError,zipfile.BadZipFile,RuntimeError,EOFError,NotImplementedError):
                self._failure(post_id,'corrupt','File cannot be read or the ZIP is damaged',stamp)

    def listing(self, query='', username='', favorites=False, hidden=False, before=None, limit=48, source=''):
        limit = max(1,min(int(limit),48))
        clauses, args = ['hidden=?'],[int(bool(hidden))]
        if favorites:
            clauses.append('favorite=1')
        if username:
            clauses.append('username=? COLLATE NOCASE'); args.append(username)
        if source:
            clauses.append('EXISTS(SELECT 1 FROM library_sources s WHERE s.post_id=library_posts.id AND s.label=?)');args.append(source)
        if query:
            # Parameters and LIKE escaping keep literal user searches literal.
            text = '%' + str(query)[:300].replace('\\','\\\\').replace('%','\\%').replace('_','\\_') + '%'
            clauses.append("(download_date LIKE ? ESCAPE '\\' OR url LIKE ? ESCAPE '\\' OR shortcode LIKE ? ESCAPE '\\' OR filename LIKE ? ESCAPE '\\')")
            args.extend([text]*4)
        if before:
            if not isinstance(before,list) or len(before)!=2 or not isinstance(before[0],str) or type(before[1]) is not int:
                raise ValueError('Invalid Library page cursor')
            clauses.append('(downloaded_at,id)<(?,?)');args.extend(before)
        rows = self.store.rows('SELECT * FROM library_posts WHERE '+' AND '.join(clauses)+' ORDER BY downloaded_at DESC,id DESC LIMIT ?',(*args,limit+1))
        more = len(rows)>limit
        rows = rows[:limit]
        for row in rows:
            self.index(row['id']) # stat/header only, no media decoding or network
        result = [self.summary(self.row(row['id'])) for row in rows]
        cursor = [rows[-1]['downloaded_at'],rows[-1]['id']] if more and rows else None
        pending = self.store.one("SELECT count(*) n FROM library_posts WHERE status='pending'")['n']
        return dict(items=result,next=cursor,indexing=pending,discovering=self.started and not self.discovered.is_set())

    def filters(self):
        return dict(usernames=[r['username'] for r in self.store.rows('SELECT DISTINCT username FROM library_posts WHERE username IS NOT NULL ORDER BY username COLLATE NOCASE')],
                    sources=[r['label'] for r in self.store.rows("SELECT DISTINCT label FROM library_sources WHERE label!='' ORDER BY label")])

    def summary(self, row):
        return {k:row[k] for k in ('id','shortcode','username','url','downloaded_at','download_date','filename','favorite','hidden','status','error','media_count')} | {'thumb_state':self.thumb_state(row)}

    def detail(self, post_id):
        self.index(post_id)
        row = self.row(post_id)
        return self.summary(row) | dict(caption=row['caption'],media=self.store.rows('SELECT position,kind,mime,size FROM library_media WHERE post_id=? ORDER BY position',(post_id,)),
                    sources=self.store.rows('SELECT kind,label FROM library_sources WHERE post_id=? ORDER BY kind,label',(post_id,)),output=row['output'])

    def flag(self, post_id, field, value):
        self.row(post_id)
        if field not in {'favorite','hidden'} or type(value) is not bool:
            raise ValueError('Invalid Library change')
        with self.store.tx() as db:
            db.execute(f'UPDATE library_posts SET {field}=? WHERE id=?',(int(value),post_id))

    def queue_details(self, post_id):
        row=self.row(post_id)
        if not row['url'] or not row['shortcode']:
            raise ValueError('No reliable post identity is available for a details fetch')
        if row['caption'] is not None and row['username'] is not None:
            raise ValueError('Caption and account details are already known')
        options=dict(task='details',library_id=post_id,folder=str(Path(row['output']).parent),caption=False,again=False)
        added,_=self.store.enqueue([(row['shortcode'],row['url'])],options)
        return added

    @contextmanager
    def open_media(self, post_id, position):
        """Open only indexed ID/member pairs. Stored ZIP entries are seekable slices."""
        self.index(post_id)
        row = self.row(post_id)
        media = self.store.one('SELECT * FROM library_media WHERE post_id=? AND position=?',(post_id,position))
        if row['status']!='ready' or media is None:
            raise ValueError(row['error'] or 'Media item unavailable')
        path = safe_path(row['output'])
        file = path.open('rb')
        archive = member = None
        offset = 0
        try:
            if media['member'] is not None:
                archive = zipfile.ZipFile(file)
                info = archive.getinfo(media['member'])
                if info.compress_type == zipfile.ZIP_STORED:
                    file.seek(info.header_offset)
                    header = file.read(30)
                    if len(header)!=30 or header[:4]!=b'PK\x03\x04':
                        raise ValueError('ZIP local header is damaged')
                    name_len, extra_len = struct.unpack_from('<HH',header,26)
                    offset = info.header_offset+30+name_len+extra_len
                    if offset+info.file_size > file.seek(0,os.SEEK_END):
                        raise ValueError('ZIP member is truncated')
                    stream = file
                else:
                    member = archive.open(info)
                    stream = member
                media['crc'] = info.CRC
            else:
                stream = file
            # No application lock is held while a client streams a large video.
            yield stream,offset,media,row
        except (zipfile.BadZipFile,EOFError):
            self._failure(post_id,'corrupt','ZIP media could not be read',row['signature'])
            raise ValueError('ZIP media could not be read') from None
        finally:
            if member: member.close()
            if archive: archive.close()
            file.close()

    def _thumb_paths(self, row):
        key = hashlib.sha256(row['signature'].encode()).hexdigest()[:24]
        return self.cache/f"{row['id']}-{key}.jpg", self.cache/f"{row['id']}-{key}.failed"

    def thumb_state(self, row):
        if row['status']!='ready':
            return 'unavailable'
        image, failed = self._thumb_paths(row)
        try:
            return 'ready' if image.is_file() else 'failed' if failed.is_file() else 'pending'
        except OSError:
            return 'failed'

    def thumbnail(self, post_id):
        self.index(post_id)
        row = self.row(post_id)
        image, failed = self._thumb_paths(row)
        if self.thumb_state(row)=='ready':
            try:
                safe_path(str(image))
                os.utime(image,None)
                return image.read_bytes(),'image/jpeg'
            except (OSError,ValueError):
                pass
        if self.thumb_state(row)=='pending':
            self.start()
            with self.lock:
                if post_id not in self.requested:
                    try:
                        self.requests.put_nowait(post_id);self.requested.add(post_id)
                    except queue.Full:
                        pass
        return PLACEHOLDER,'image/svg+xml'

    def _thumb_loop(self):
        while not self.stop.is_set():
            try:
                post_id = self.requests.get(timeout=.5)
            except queue.Empty:
                continue
            try:
                self.make_thumbnail(post_id)
            except Exception:
                pass
            finally:
                with self.lock:
                    self.requested.discard(post_id)
                self.requests.task_done()

    def make_thumbnail(self, post_id):
        import imageio_ffmpeg
        row = self.row(post_id)
        if row['status']!='ready':return
        safe_path(str(self.cache))
        self.cache.mkdir(parents=True,exist_ok=True)
        image, failed = self._thumb_paths(row)
        temporary = image.with_suffix('.part.jpg')
        process = None
        try:
            # No extraction directories and no remote protocols. ZIP bytes are
            # piped from the first indexed member; a failed decode is a placeholder.
            with self.open_media(post_id,0) as (stream,offset,media,current):
                options = dict(stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
                command = [imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-loglevel','error','-threads','1','-protocol_whitelist','file,pipe',
                           '-i',str(safe_path(row['output'])) if media['member'] is None else 'pipe:0',
                           '-frames:v','1','-vf','scale=320:320:force_original_aspect_ratio=decrease','-an','-y',str(temporary)]
                if media['member'] is None:
                    subprocess.run(command,timeout=20,check=True,**options)
                else:
                    process = subprocess.Popen(command,stdin=subprocess.PIPE,**options)
                    timer = threading.Timer(20,process.kill);timer.start()
                    try:
                        stream.seek(offset)
                        left = media['size']
                        try:
                            while left>0 and not self.stop.is_set():
                                block=stream.read(min(left,256*1024))
                                if not block:break
                                process.stdin.write(block);left-=len(block)
                        except (BrokenPipeError,OSError):
                            pass
                        try:process.stdin.close()
                        except OSError:pass
                        if process.wait(timeout=22)!=0:raise ValueError('Preview decoder failed')
                    finally:
                        timer.cancel()
                        if process.poll() is None:process.kill();process.wait()
                if not temporary.is_file() or temporary.stat().st_size>500_000 or temporary.stat().st_size==0:
                    raise ValueError('No usable preview')
                # Changed files cannot receive a thumbnail from the old signature.
                if signature(safe_path(row['output']))!=row['signature']:
                    raise ValueError('Source changed during preview generation')
                os.replace(temporary,image)
        except Exception:
            safe_path(str(failed))
            failed.write_text('Preview unavailable',encoding='ascii')
        finally:
            temporary.unlink(missing_ok=True)
            self.prune_cache()

    def prune_cache(self):
        if not self.cache.exists():return
        safe_path(str(self.cache))
        entries=[]
        for path in self.cache.iterdir():
            if re.fullmatch(r'\d+-[a-f0-9]{24}\.part.jpg',path.name) and not path.is_symlink():
                if time.time()-path.stat().st_mtime>120:path.unlink(missing_ok=True)
                continue
            if re.fullmatch(r'\d+-[a-f0-9]{24}\.(jpg|failed)',path.name) and path.is_file() and not path.is_symlink():
                info=path.stat();entries.append((info.st_mtime,path,info.st_size))
        entries.sort()
        size=sum(x[2] for x in entries)
        while entries and (len(entries)>self.cache_files or size>self.cache_bytes):
            _,path,length=entries.pop(0)
            path.unlink(missing_ok=True);size-=length

    def _recorded_files(self, row):
        primary=safe_path(row['output'])
        if primary.suffix.lower() not in {*MIME,'.zip'}:
            raise ValueError('Only recorded media outputs can be deleted')
        files=[primary]
        for value in json.loads(row['extras']):
            path=safe_path(value)
            if path != primary.with_suffix('.txt'):
                raise ValueError('Recorded sidecar is outside this output; deletion refused')
            files.append(path)
        return files

    def deletion_preview(self, post_id):
        with self.lock:
            row=self.row(post_id)
            files=self._recorded_files(row)
            plan=[dict(path=str(p),signature=signature(p) if p.exists() else None) for p in files]
            self.confirmations={k:v for k,v in self.confirmations.items() if v['expires']>time.monotonic()}
            if len(self.confirmations)>32:self.confirmations.clear()
            token=secrets.token_urlsafe(24)
            self.confirmations[token]=dict(id=post_id,files=plan,expires=time.monotonic()+300)
            return dict(confirmation=token,files=[dict(path=x['path'],exists=x['signature'] is not None) for x in plan])

    def delete(self, post_id, confirmation):
        with self.lock:
            plan=self.confirmations.pop(confirmation,None)
            if not plan or plan['id']!=post_id or plan['expires']<time.monotonic():
                raise ValueError('Review the exact files and confirm deletion again')
            row=self.row(post_id)
            files=self._recorded_files(row)
            if [str(p) for p in files]!=[f['path'] for f in plan['files']]:
                raise ValueError('Recorded outputs changed; review deletion again')
            for path,expected in zip(files,plan['files']):
                actual=signature(path) if path.exists() else None
                if actual!=expected['signature']:
                    raise ValueError('A file changed since confirmation; review deletion again')
            try:
                for path in files:path.unlink(missing_ok=True)
            except OSError:
                self.index(post_id)
                raise ValueError('Some files could not be removed. Check access and review the remaining files again') from None
            self._failure(post_id,'deleted','Local file deleted. History is retained; a future download is permitted.')
            safe_path(str(self.cache))
            for path in self.cache.glob(f'{post_id}-*'):
                if re.fullmatch(r'\d+-[a-f0-9]{24}\.(jpg|failed|part.jpg)',path.name) and not path.is_symlink():path.unlink(missing_ok=True)
