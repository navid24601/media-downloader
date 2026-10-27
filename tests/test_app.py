import base64
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
import zipfile
from unittest.mock import patch, Mock
import requests
from instagram_local.core import post_url, normalize_lines, basename, safe_name, import_file, redact, Failure
from instagram_local.security import Vault, parse_cookies, validate_proxy
from instagram_local.store import Store
from instagram_local.engine import Engine, Post, Media, response_failure
from instagram_local.worker import Worker
from instagram_local.server import Application, make_server

CODE = 'Dewwmu2CDzv'
URL = f'https://www.instagram.com/p/{CODE}/'


class PlainTestVault:
    """Only a memory fixture, never used by the application."""
    def __init__(self): self.data = {'cookies': [], 'proxy': ''}
    def load(self): return self.data.copy()
    def save(self, data): self.data = data


class FixtureEngine:
    def __init__(self, secret=None):
        self.calls = []
        self.problem = None
        self.post_value = Post('alexdemirel', CODE, 'A fixture caption', [Media('image1', 'https://s.cdninstagram.com/1.jpg', 'jpg'), Media('video2', 'https://s.cdninstagram.com/2.mp4', 'mp4'), Media('image3', 'https://s.cdninstagram.com/3.jpg', 'jpg')], True)
    def close(self): pass
    def post(self, url): return self.post_value
    def download(self, media, path, progress):
        self.calls.append(media.identity)
        if self.problem == media.identity: raise Failure('network', 'Fixture connection interruption')
        path.write_bytes((b'image-' if media.extension == 'jpg' else b'video-') + media.identity.encode())
        progress(path.stat().st_size, path.stat().st_size)


class Base(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'queue.sqlite3')
        self.vault = PlainTestVault()
        self.options = {'folder': str(self.root / 'output'), 'caption': False, 'again': False}
        self.worker = Worker(self.store, self.vault, sleeper=lambda _: None)
        self.worker.active = True
        self.engine = FixtureEngine()
        self.worker.engine = self.engine
    def tearDown(self):
        self.worker.close()
        self.store.db.close()
        self.temp.cleanup()
    def item(self, code=CODE):
        self.store.enqueue([(code, f'https://www.instagram.com/p/{code}/')], self.options)
        return self.store.one('SELECT * FROM queue ORDER BY id DESC LIMIT 1')


class InputTests(unittest.TestCase):
    def test_normalization_and_dedup(self):
        values = ['', f' http://m.instagram.com/reel/{CODE}/?igsh=secret ', URL, 'https://evil.test/p/abcde', 'https://www.instagram.com/stories/user/1/', f'instagram.com/alex/p/{CODE}/']
        valid, errors, dup = normalize_lines(values)
        self.assertEqual(valid, [(CODE, URL)])
        self.assertEqual(dup, 2)
        self.assertEqual(len(errors), 2)
        for value in ['https://instagram.com.evil.test/p/abcde/', 'https://u:p@instagram.com/p/abcde/', 'file://instagram.com/p/abcde/', 'https://instagram.com:55/p/abcde/', 'https://instagram.com/share/abcde/']:
            with self.assertRaises(ValueError): post_url(value)
    def test_names(self):
        self.assertEqual(basename('alexdemirel', CODE, '2026-10-25'), 'alexdemirel-Dewwmu2CDzv-2026-10-25')
        self.assertEqual(safe_name('CON'), '_CON')
        self.assertEqual(safe_name(' ..a:b/c? .'), 'a_b_c_')
    def test_csv(self):
        self.assertEqual(import_file('link,note\n'+URL+',hello\ninvalid,bad', 'a.csv')['values'], [URL, 'invalid'])
        data = 'url,other_link\n'+URL+',https://instagram.com/reel/ABCDE/\n'
        self.assertEqual(len(import_file(data,'a.csv')['columns']), 2)
        self.assertEqual(import_file(data,'a.csv',1)['values'], ['https://instagram.com/reel/ABCDE/'])
        self.assertEqual(import_file(URL+'\r\n\r\n', 'a.txt')['values'][0], URL)
        self.assertEqual(import_file(URL+'\n', 'a.csv')['values'], [URL])
    def test_secret_redaction(self):
        result = redact('sessionid=secret csrftoken:abc token=private https://user:pass@host/x?secret=yes')
        for secret in ('secret', 'abc', 'private', 'user:pass'):
            self.assertNotIn(secret, result)
    def test_cookie_validation(self):
        text = '# Netscape HTTP Cookie File\n#HttpOnly_.instagram.com\tTRUE\t/\tTRUE\t0\tsessionid\tfixture-secret\n.evil.test\tTRUE\t/\tTRUE\t0\ttoken\tevil'
        cookies = parse_cookies(text)
        self.assertEqual(len(cookies), 1)
        self.assertEqual(cookies[0]['name'], 'sessionid')
        with self.assertRaises(ValueError): parse_cookies('[{"domain":"evil.test","name":"sessionid","value":"x"}]')
        with self.assertRaises(ValueError): parse_cookies('[{"domain":".instagram.com","name":"sessionid","value":"x","expirationDate":1}]')
    def test_proxy_validation(self):
        self.assertEqual(validate_proxy('socks5h://u:p@127.0.0.1:1080'), 'socks5h://u:p@127.0.0.1:1080')
        with self.assertRaises(ValueError): validate_proxy('http://host:bad')
        with self.assertRaises(ValueError): validate_proxy('http://host:8080/path?secret=1')


class PersistenceTests(Base):
    def test_success_missing_file_and_again(self):
        item = self.item()
        self.worker._item(item)
        self.assertTrue(self.store.completed(CODE))
        added, skipped = self.store.enqueue([(CODE, URL)], self.options)
        self.assertEqual((added, skipped), (0,1))
        row = self.store.one('SELECT * FROM downloads')
        Path(row['output']).unlink()
        self.assertFalse(self.store.completed(CODE))
        self.assertEqual(self.store.enqueue([(CODE, URL)], self.options), (1,0))
    def test_queue_recovery_and_no_auto_resume(self):
        item = self.item()
        self.store.update_item(item['id'], status='running', attempts=1)
        self.store.db.close()
        self.store = Store(self.root / 'queue.sqlite3')
        worker = Worker(self.store, self.vault)
        self.assertFalse(worker.active)
        self.assertEqual(self.store.one('SELECT * FROM queue')['status'], 'pending')
    def test_complete_mixed_carousel_and_cleanup(self):
        self.options['caption'] = True
        item = self.item()
        self.worker._item(item)
        result = self.store.one('SELECT * FROM queue WHERE id=?',(item['id'],))
        self.assertEqual(result['status'], 'done')
        output = Path(result['output'])
        self.assertTrue(output.name.startswith('alexdemirel-'+CODE+'-'))
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.namelist(), ['001.jpg','002.mp4','003.jpg','caption.txt'])
            self.assertEqual(archive.read('002.mp4'), b'video-video2')
            self.assertIn(URL, archive.read('caption.txt').decode())
        self.assertFalse((output.parent / '.incomplete' / str(item['id'])).exists())
        self.assertEqual(list(output.parent.glob('*.jpg')), [])
    def test_incomplete_not_successful_retry_reuses_items(self):
        item = self.item()
        self.engine.problem = 'video2'
        self.worker.active = False # one attempt for the interrupted fixture
        self.worker._item(item)
        self.assertFalse(self.store.completed(CODE))
        self.assertEqual(list(Path(self.options['folder']).glob('*.zip')), [])
        stage = Path(self.options['folder']) / '.incomplete' / str(item['id'])
        self.assertTrue((stage / 'INCOMPLETE.txt').exists())
        self.engine.problem = None
        self.worker.retry_failed()
        self.worker._item(self.store.one('SELECT * FROM queue WHERE id=?',(item['id'],)))
        self.assertTrue(self.store.completed(CODE))
        self.assertEqual(self.engine.calls.count('image1'), 1)
    def test_collision_and_single_media(self):
        self.engine.post_value = Post('alexdemirel',CODE,'Caption',[Media('img','https://s.cdninstagram.com/a.jpg','jpg')])
        self.options['caption'] = True
        first = self.item()
        self.worker._item(first)
        old = self.store.one('SELECT * FROM downloads')['output']
        self.options['again'] = True
        second = self.item()
        self.worker._item(second)
        new = self.store.one('SELECT * FROM downloads')['output']
        self.assertNotEqual(old,new)
        self.assertIn(' (2).jpg',new)
        self.assertTrue(Path(old).is_file())
        self.assertTrue(Path(new).with_suffix('.txt').is_file())
    def test_retry_limit_and_continue(self):
        self.engine.post = Mock(side_effect=Failure('network','Temporary network failure'))
        factory = lambda _: self.engine
        self.worker.engine_factory = factory
        item = self.item()
        self.worker._item(item)
        self.assertEqual(self.engine.post.call_count, 3)
        self.assertEqual(self.store.one('SELECT * FROM queue')['status'], 'failed')
        self.assertTrue(self.worker.active)
    def test_restrictions_never_retry(self):
        for kind in ('auth','rate_limit','challenge','restricted'):
            with self.subTest(kind=kind):
                self.worker.active = True
                self.engine.post = Mock(side_effect=Failure(kind,'Resolve restriction',time.time()+100))
                item = self.item('ABCDE'+kind.replace('_',''))
                self.worker._item(item)
                self.assertFalse(self.worker.active)
                self.assertEqual(self.engine.post.call_count, 1)
                self.assertEqual(self.store.one('SELECT * FROM queue WHERE id=?',(item['id'],))['status'], 'pending')
        with self.assertRaises(ValueError): self.worker.resume()
    def test_cancel_keeps_completed(self):
        self.worker._item(self.item())
        self.item('ABCDE123')
        self.worker.cancel()
        self.assertTrue(self.store.completed(CODE))
        self.assertEqual(self.store.one("SELECT count(*) n FROM queue WHERE status='canceled'")['n'],1)
    def test_source_checkpoint_atomic_and_latest_profile_pins(self):
        self.store.add_source('profile','https://instagram.com/a/posts/','a',2,self.options)
        source = self.store.one('SELECT * FROM sources')
        self.store.checkpoint(source,'OLDPIN1',None,1,{'taken_at':1,'timeline_pinned_user_ids':[1]})
        self.store.checkpoint(source,'NEWEST1',None,2,{'taken_at':300})
        self.store.checkpoint(source,'NEWER12','cursor2',1,{'taken_at':200})
        self.store.finish_source(source)
        source = self.store.one('SELECT * FROM sources')
        self.assertEqual(source['phase'], 'reels')
        self.store.finish_source(source)
        self.assertEqual([r['shortcode'] for r in self.store.rows('SELECT * FROM queue ORDER BY id')],['NEWEST1','NEWER12'])
        result = self.store.one('SELECT * FROM sources')
        self.assertEqual((result['cursor'],result['offset'],result['emitted']),(None,0,3))
    def test_50_item_incremental_source(self):
        self.store.add_source('saved','https://instagram.com/me/saved/','Saved',None,self.options)
        source = self.store.one('SELECT * FROM sources')
        yielded = []
        def enumerate_fixture(src):
            for i in range(5000):
                yielded.append(i)
                yield f'ABCDE{i}',None,i+1,{}
        self.engine.enumerate = enumerate_fixture
        self.worker._source(source)
        self.assertEqual(len(yielded),50)
        self.assertEqual(self.store.one('SELECT count(*) n FROM queue')['n'],50)
        self.assertEqual(self.store.one('SELECT * FROM sources')['status'],'pending')

    def test_latest_profile_combines_timeline_and_reels(self):
        self.store.add_source('profile','https://instagram.com/a/posts/','a',2,self.options)
        source=self.store.one('SELECT * FROM sources')
        self.store.checkpoint(source,'TIMELN1',None,1,{'taken_at':300})
        self.store.checkpoint(source,'TIMELN2',None,2,{'taken_at':200})
        self.store.finish_source(source)
        source=self.store.one('SELECT * FROM sources')
        self.assertEqual(source['phase'],'reels')
        self.store.checkpoint(source,'REEL001',None,1,{'taken_at':400})
        self.store.checkpoint(source,'TIMELN1',None,2,{'taken_at':300})
        self.store.finish_source(source)
        self.assertEqual([r['shortcode'] for r in self.store.rows('SELECT * FROM queue ORDER BY id')],['REEL001','TIMELN1'])

    def test_pause_finishes_current_post_and_preserves_remaining(self):
        item=self.item()
        self.item('FUTURE1')
        original=self.engine.download
        def download_and_pause(media,path,progress):
            original(media,path,progress)
            self.worker.pause()
        self.engine.download=download_and_pause
        self.worker._item(item)
        self.assertTrue(self.store.completed(CODE))
        self.assertFalse(self.worker.active)
        self.assertEqual(self.store.one("SELECT count(*) n FROM queue WHERE status='pending'")['n'],1)

    def test_crash_after_final_move_recovers_without_duplicate(self):
        item=self.item()
        output,extras=self.worker._save(item,self.engine.post_value,self.options)
        self.assertFalse(self.store.completed(CODE))
        self.worker._item(self.store.one('SELECT * FROM queue WHERE id=?',(item['id'],)))
        self.assertTrue(self.store.completed(CODE))
        self.assertEqual(len(list(output.parent.glob('*.zip'))),1)
        self.assertEqual(self.engine.calls.count('image1'),1)


def response(status=200, data=None, headers=None, url='https://www.instagram.com/api/example'):
    r = requests.Response()
    r.status_code, r.url = status, url
    r.headers.update(headers or {})
    if data is not None:
        r._content = json.dumps(data).encode()
        r.headers['Content-Type'] = 'application/json'
    else:
        r._content = b''
    return r


class AdapterTests(unittest.TestCase):
    def test_status_and_json_classification(self):
        for r, kind in [(response(429,headers={'Retry-After':'120'}),'rate_limit'),(response(401),'auth'),(response(200,{'message':'challenge_required'}),'challenge'),(response(200,{'message':'login_required'}),'auth'),(response(404),'unavailable'),(response(503),'network'),(response(403),'restricted')]:
            self.assertEqual(response_failure(r).kind,kind)
        self.assertGreater(response_failure(response(429,headers={'Retry-After':'120'})).retry_at,time.time()+115)
    def test_pinned_engine_parser_with_media_fixture(self):
        engine = Engine({},sleeper=lambda _:None)
        ext = engine._extractor(__import__('gallery_dl.extractor.instagram',fromlist=['InstagramPostExtractor']).InstagramPostExtractor,URL)
        def image(pk,video=False):
            r={'pk':pk,'media_type':2 if video else 1,'taken_at':1700000000,'original_width':1080,'original_height':1080,'image_versions2':{'candidates':[{'url':'https://s.cdninstagram.com/a.jpg','width':1080,'height':1080}]}}
            if video:r['video_versions']=[{'url':'https://s.cdninstagram.com/b.mp4','width':1080,'height':1080,'type':101}]
            return r
        raw={'pk':'123456789','code':CODE,'caption':{'text':'Caption'},'taken_at':1700000000,'user':{'pk':'1','username':'alexdemirel','full_name':'Alex'},'carousel_media_count':2,'carousel_media':[image('1'),image('2',True)]}
        ext.posts=lambda:iter([raw])
        with patch.object(engine,'_extractor',return_value=ext):
            post=engine.post(URL)
        self.assertEqual([m.extension for m in post.media],['jpg','mp4'])
        self.assertTrue(post.carousel)
        engine.close()
    def test_missing_carousel_media_rejected(self):
        engine = Engine({},sleeper=lambda _:None)
        ext=Mock()
        ext.posts.return_value=iter([{'carousel_media_count':3,'carousel_media':[{'image_versions2':{'candidates':[]}}]}])
        with patch.object(engine,'_extractor',return_value=ext), self.assertRaises(Failure) as ctx:
            engine.post(URL)
        self.assertEqual(ctx.exception.kind,'incomplete')
        engine.close()
    def test_separate_post_audio_does_not_count_as_missing_media(self):
        engine = Engine({}, sleeper=lambda _:None)
        ext = engine._extractor(__import__('gallery_dl.extractor.instagram',fromlist=['InstagramPostExtractor']).InstagramPostExtractor, URL)
        raw = {
            'pk':'123456789', 'code':CODE, 'caption':{'text':'Caption'},
            'taken_at':1700000000, 'user':{'pk':'1','username':'alex','full_name':'Alex'},
            'image_versions2':{'candidates':[{'url':'https://s.cdninstagram.com/a.jpg','width':1080,'height':1080}]},
            'music_metadata':{'music_asset_info':{'id':'99','progressive_download_url':'https://s.cdninstagram.com/music.mp3'}},
        }
        ext.posts = lambda:iter([raw])
        with patch.object(engine,'_extractor',return_value=ext):
            post = engine.post(URL)
        self.assertEqual(len(post.media), 1)
        self.assertEqual(post.media[0].extension, 'jpg')
        engine.close()
    def test_cursor_offset_resume_across_pages(self):
        engine = Engine({},sleeper=lambda _:None)
        ext=Mock(); ext._cursor=None
        def posts():
            yield {'code':'FIRST12'}
            yield {'code':'SECOND1'}
            ext._cursor='next-page'
            yield {'code':'THIRD12'}
        ext.posts=posts
        source={'kind':'profile','target':'https://instagram.com/a/posts/','cursor':'page-start','offset':1}
        with patch.object(engine,'_extractor',return_value=ext):
            result=list(engine.enumerate(source))
        self.assertEqual([(r[0],r[1],r[2]) for r in result],[('SECOND1','page-start',2),('THIRD12','next-page',1)])
        engine.close()
    def test_no_password_login_disk_cache_or_retries(self):
        engine=Engine({'proxy':'socks5h://127.0.0.1:1080'},sleeper=lambda _:None)
        ext=engine._extractor(__import__('gallery_dl.extractor.instagram',fromlist=['InstagramPostExtractor']).InstagramPostExtractor,URL)
        self.assertEqual(ext._retries,0)
        self.assertFalse(ext.session.trust_env)
        self.assertTrue(ext._verify)
        self.assertEqual(ext._proxies.get('https'),'socks5h://127.0.0.1:1080')
        ext.session.close(); engine.close()


class SecurityTests(Base):
    @unittest.skipUnless(os.name=='nt','Windows DPAPI')
    def test_dpapi_round_trip_and_no_plaintext(self):
        vault=Vault(self.root/'secret.bin')
        secret={'cookies':[{'name':'sessionid','value':'fixture-secret'}],'proxy':'http://user:password@host:123'}
        vault.save(secret)
        self.assertEqual(vault.load(),secret)
        self.assertNotIn(b'fixture-secret',vault.path.read_bytes())
        self.assertNotIn(b'password',vault.path.read_bytes())
        secret['cookies']=[];vault.save(secret)
        self.assertFalse(vault.load()['cookies'])
    def test_local_http_protection_and_available_order_gate(self):
        self.worker.active=False
        app=Application(self.store,self.vault,self.worker,'fixture-capability')
        server=make_server(app,0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        root=f'http://127.0.0.1:{server.server_port}'
        session=requests.Session(); session.trust_env=False
        headers={'Origin':root,'X-App-Token':'fixture-capability','Content-Type':'application/json'}
        try:
            self.assertEqual(session.get(root+'/').status_code,200)
            self.assertEqual(session.post(root+'/api/state',json={}).status_code,403)
            self.assertEqual(session.post(root+'/api/state',json={},headers={**headers,'Origin':'https://evil.test'}).status_code,403)
            self.assertEqual(session.post(root+'/api/state',json={},headers={**headers,'Host':'evil.test'}).status_code,403)
            self.assertEqual(session.options(root+'/api/state').status_code,403)
            good=session.post(root+'/api/state',json={},headers=headers)
            self.assertEqual(good.status_code,200)
            self.assertNotIn('fixture-capability',good.text)
            self.assertNotIn('Access-Control-Allow-Origin',good.headers)
            self.vault.data['cookies']=[{'name':'sessionid','value':'fixture-secret'}]
            rejected=session.post(root+'/api/source',json={'kind':'saved','maximum':2},headers=headers)
            self.assertEqual(rejected.status_code,400)
            self.assertIn('available order',rejected.json()['error'])
            accepted=session.post(root+'/api/source',json={'kind':'saved','maximum':2,'accept_available_order':True},headers=headers)
            self.assertEqual(accepted.status_code,200)
        finally:
            server.shutdown();server.server_close();thread.join();session.close()


if __name__=='__main__': unittest.main()
