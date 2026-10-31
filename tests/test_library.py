import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import time
import unittest
from datetime import datetime,timedelta,timezone
import zipfile
from unittest.mock import patch,Mock
import requests
from instagram_local.core import now
from instagram_local.store import Store
from instagram_local.library import Library,register,signature
from instagram_local.engine import Post,Media
from instagram_local.server import Application,make_server
from instagram_local.worker import Worker
from library_fixtures import generate


class LibraryBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets=tempfile.TemporaryDirectory()
        cls.samples=generate(Path(cls.assets.name))

    @classmethod
    def tearDownClass(cls):cls.assets.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.folder=self.root/'outputs';self.folder.mkdir()
        for path in Path(self.assets.name).iterdir():shutil.copyfile(path,self.folder/path.name)
        self.files=[self.folder/path.name for path in self.samples]
        self.store=Store(self.root/'queue.sqlite3')
        self.library=Library(self.store,self.root/'thumbnails')

    def tearDown(self):
        self.library.close();self.store.db.close();self.temp.cleanup()

    def legacy(self):
        for i,path in enumerate(self.files):
            code=['IMAGE01','VIDEO01','CAROUSEL1'][i]
            extras=[str(path.with_suffix('.txt'))] if i==0 else []
            with self.store.tx() as db:
                db.execute('INSERT INTO downloads VALUES(?,?,?,?)',(code,str(path),json.dumps(extras),f'2026-10-{25+i}T10:00:00+00:00'))

    def migrate(self):
        while self.library.sync_records():pass
        for row in self.store.rows('SELECT id FROM library_posts'):self.library.index(row['id'])


class LibraryTests(LibraryBase):
    def test_real_v1_database_migration_preserves_everything(self):
        self.legacy()
        self.store.set_setting('caption',True)
        self.store.enqueue([('PENDING1','https://www.instagram.com/p/PENDING1/')],{'folder':str(self.folder)})
        sentinel=self.root/'credentials.bin';sentinel.write_bytes(b'protected-session-sentinel')
        before=[p.read_bytes() for p in self.files]
        # Remove new tables to reproduce a database created only by v1.
        with self.store.tx() as db:
            for name in ['library_posts','library_media','library_sources','library_imports','library_meta']:db.execute('DROP TABLE '+name)
        self.store.db.close();self.store=Store(self.root/'queue.sqlite3');self.library=Library(self.store,self.root/'thumbnails')
        with patch('instagram_local.engine.Engine.post',side_effect=AssertionError('Must remain offline')):
            self.migrate();self.migrate()
        rows=self.library.listing()['items']
        self.assertEqual(len(rows),3)
        self.assertEqual({r['status'] for r in rows},{'ready'})
        self.assertEqual(self.store.one('SELECT count(*) n FROM downloads')['n'],3)
        self.assertEqual(self.store.one('SELECT * FROM queue')['status'],'pending')
        self.assertTrue(self.store.setting('caption'))
        self.assertEqual(sentinel.read_bytes(),b'protected-session-sentinel')
        self.assertEqual(before,[p.read_bytes() for p in self.files])
        self.assertEqual(list(self.folder.glob('*/')),[])

    def test_repeatable_interrupted_batched_migration_and_unknowns(self):
        self.legacy()
        self.assertEqual(self.library.sync_records(limit=1),1)
        self.store.db.close();self.store=Store(self.root/'queue.sqlite3');self.library=Library(self.store)
        self.migrate();self.assertEqual(len(self.library.listing()['items']),3)
        unknown=self.folder/'unconventional.jpg';shutil.copyfile(self.files[0],unknown)
        with self.store.tx() as db:register(db,'UNKNOWN1',unknown,[],'2026-10-24T00:00:00+00:00')
        row=self.store.one("SELECT id FROM library_posts WHERE shortcode='UNKNOWN1'")
        detail=self.library.detail(row['id'])
        self.assertIsNone(detail['username']);self.assertIsNone(detail['caption'])
        self.assertEqual(detail['sources'],[])
        ambiguous=self.folder/'_CON-AMBIG01-2026-10-27.jpg';shutil.copyfile(self.samples[0],ambiguous)
        with self.store.tx() as db:ambiguous_id=register(db,'AMBIG01',ambiguous,[],'2026-10-27T00:00:00+00:00')
        self.assertIsNone(self.library.detail(ambiguous_id)['username'])

    def test_future_caption_types_and_sources_without_caption_file(self):
        output=self.files[0];output.with_suffix('.txt').unlink()
        options={'folder':str(self.folder),'caption':False,'source':{'kind':'collection','label':'Travel'}}
        self.store.enqueue([('IMAGE01','https://www.instagram.com/p/IMAGE01/')],options)
        item=self.store.one('SELECT * FROM queue')
        post=Post('alice','IMAGE01','Caption retained in SQLite',[Media('i1','https://cdn.example/signed?token=secret','jpg')])
        self.store.success(item,output,[],post=post)
        self.migrate()
        detail=self.library.detail(self.store.one('SELECT id FROM library_posts')['id'])
        self.assertEqual(detail['caption'],'Caption retained in SQLite')
        self.assertEqual(detail['username'],'alice')
        self.assertEqual(detail['media'][0]['kind'],'image')
        self.assertEqual(detail['sources'],[{'kind':'collection','label':'Travel'}])
        self.assertFalse(output.with_suffix('.txt').exists())
        with self.store.lock:
            dump='\n'.join(self.store.db.iterdump())
        self.assertNotIn('cdn.example',dump);self.assertNotIn('token=secret',dump)

    def test_explicit_details_fetch_uses_worker_without_redownloading(self):
        self.legacy();self.migrate()
        post_id=self.store.one("SELECT id FROM library_posts WHERE shortcode='VIDEO01'")['id']
        self.assertEqual(self.library.queue_details(post_id),1)
        job=self.store.one("SELECT * FROM queue WHERE status='pending'")
        before=self.store.one("SELECT * FROM downloads WHERE shortcode='VIDEO01'")
        post=Post('bob','VIDEO01','Explicitly fetched fixture caption',[Media('v','https://cdn.example/secret?token=x','mp4')])
        engine=Mock();engine.post.return_value=post
        worker=Worker(self.store,Mock());worker.engine=engine;worker.active=True
        worker._item(job)
        engine.download.assert_not_called();engine.post.assert_called_once_with('https://www.instagram.com/p/VIDEO01/')
        self.assertEqual(self.store.one('SELECT status FROM queue')['status'],'details_done')
        self.assertEqual(self.store.one("SELECT * FROM downloads WHERE shortcode='VIDEO01'"),before)
        self.assertEqual(self.library.detail(post_id)['caption'],'Explicitly fetched fixture caption')
        self.assertEqual(self.files[1].read_bytes(),self.samples[1].read_bytes())

    def test_details_fetch_restrictions_pause_the_existing_queue(self):
        from instagram_local.core import Failure
        self.legacy();self.migrate()
        post_id=self.store.one("SELECT id FROM library_posts WHERE shortcode='VIDEO01'")['id']
        self.library.queue_details(post_id)
        worker=Worker(self.store,Mock());worker.active=True;worker.engine=Mock()
        worker.engine.post.side_effect=Failure('rate_limit','Fixture restriction',time.time()+60)
        worker._item(self.store.one('SELECT * FROM queue'))
        self.assertFalse(worker.active);self.assertEqual(worker.engine.post.call_count,1)
        self.assertEqual(self.store.one('SELECT status FROM queue')['status'],'pending')
        self.assertIsNone(self.library.detail(post_id)['caption'])

    def test_zip_order_caption_and_direct_members_without_extraction(self):
        self.legacy();self.migrate()
        row=self.store.one("SELECT id FROM library_posts WHERE shortcode='CAROUSEL1'")
        detail=self.library.detail(row['id'])
        self.assertEqual([x['kind'] for x in detail['media']],['image','video','image'])
        self.assertEqual(detail['caption'],'Local carousel caption.')
        with self.library.open_media(row['id'],1) as (stream,offset,media,_):
            stream.seek(offset);self.assertEqual(stream.read(media['size']),self.files[1].read_bytes())
        self.assertEqual(list(self.folder.glob('*/')),[])

    def test_search_date_link_shortcode_filename_and_account(self):
        self.legacy();self.migrate()
        for query,code in [('2026-10-25','IMAGE01'),('https://www.instagram.com/p/VIDEO01/','VIDEO01'),('CAROUSEL1','CAROUSEL1'),('alice-CAROUSEL1-2026-10-27.zip','CAROUSEL1')]:
            with self.subTest(query=query):self.assertEqual([r['shortcode'] for r in self.library.listing(query=query)['items']],[code])
        self.assertEqual(len(self.library.listing(username='ALICE')['items']),2)
        self.assertEqual(self.library.listing(query="%' OR 1=1 --")['items'],[])
        self.assertEqual(self.library.filters()['usernames'],['alice','bob'])

    def test_favorites_hidden_restore_survive_restart(self):
        self.legacy();self.migrate();post_id=self.store.one('SELECT id FROM library_posts')['id']
        self.library.flag(post_id,'favorite',True);self.library.flag(post_id,'hidden',True)
        self.store.db.close();self.store=Store(self.root/'queue.sqlite3');self.library=Library(self.store)
        self.assertEqual(self.library.listing(favorites=True)['items'],[])
        hidden=self.library.listing(favorites=True,hidden=True)['items'];self.assertEqual([r['id'] for r in hidden],[post_id])
        self.library.flag(post_id,'hidden',False)
        self.assertEqual(self.library.listing(favorites=True)['items'][0]['id'],post_id)
        self.assertTrue(self.files[0].exists())

    def test_missing_corrupt_incomplete_and_changed_files(self):
        self.legacy();self.migrate()
        image=self.store.one("SELECT id FROM library_posts WHERE shortcode='IMAGE01'")['id']
        self.files[0].unlink();self.assertEqual(self.library.detail(image)['status'],'missing')
        shutil.copyfile(self.samples[0],self.files[0]);self.assertEqual(self.library.detail(image)['status'],'ready')
        self.files[0].write_bytes(b'not an image');self.assertEqual(self.library.detail(image)['status'],'corrupt')
        archive=self.store.one("SELECT id FROM library_posts WHERE shortcode='CAROUSEL1'")['id']
        self.files[2].write_bytes(b'broken zip');self.assertEqual(self.library.detail(archive)['status'],'corrupt')
        stage=self.folder/'.incomplete'/'123';stage.mkdir(parents=True);partial=stage/'001.jpg';shutil.copyfile(self.samples[0],partial)
        with self.store.tx() as db:post_id=register(db,'STAGING1',partial,[],now())
        self.assertEqual(self.library.detail(post_id)['status'],'incomplete')
        with self.assertRaises(ValueError):
            with self.library.open_media(post_id,0):pass

    def test_zip_traversal_and_missing_number_rejected(self):
        for names in [['../001.jpg'],['001.jpg','003.jpg'],['001.jpg','001.jpg']]:
            path=self.folder/'alice-UNSAFE1-2026-10-27.zip'
            with zipfile.ZipFile(path,'w') as archive:
                for name in names:archive.writestr(name,self.files[0].read_bytes())
            with self.store.tx() as db:post_id=register(db,'UNSAFE1',path,[],now())
            self.assertEqual(self.library.detail(post_id)['status'],'corrupt')

    def test_deflated_zip_member_is_range_seekable(self):
        path=self.folder/'alice-DEFLATE1-2026-10-27.zip'
        with zipfile.ZipFile(path,'w',compression=zipfile.ZIP_DEFLATED) as archive:archive.write(self.files[1],'001.mp4')
        with self.store.tx() as db:post_id=register(db,'DEFLATE1',path,[],now())
        self.assertEqual(self.library.detail(post_id)['status'],'ready')
        with self.library.open_media(post_id,0) as (stream,offset,media,_):
            stream.seek(offset+100);self.assertEqual(stream.read(100),self.files[1].read_bytes()[100:200])

    def test_reparse_output_and_sidecar_are_not_followed(self):
        self.legacy();self.migrate()
        post_id=self.store.one("SELECT id FROM library_posts WHERE shortcode='IMAGE01'")['id']
        original=Path.lstat
        class Reparse:
            st_mode=0;st_file_attributes=0x400
        def simulated(path,*args,**kwargs):
            return Reparse() if path==self.files[0] else original(path,*args,**kwargs)
        with patch.object(Path,'lstat',simulated):
            self.assertEqual(self.library.detail(post_id)['status'],'corrupt')
            with self.assertRaises(ValueError):self.library.deletion_preview(post_id)

    def test_unwritable_thumbnail_cache_does_not_block_original(self):
        self.legacy();self.migrate()
        with patch.object(self.library,'prune_cache',side_effect=OSError('Cache not writable')):
            self.library.start()
        post_id=self.store.one("SELECT id FROM library_posts WHERE shortcode='VIDEO01'")['id']
        self.assertEqual(self.library.detail(post_id)['status'],'ready')
        with self.library.open_media(post_id,0) as (stream,offset,media,_):
            self.assertGreater(media['size'],0)

    def test_thumbnail_generation_invalidation_failure_and_bound(self):
        self.legacy();self.migrate()
        for row in self.store.rows('SELECT * FROM library_posts'):
            self.library.make_thumbnail(row['id'])
            self.assertEqual(self.library.thumb_state(row),'ready')
            jpg,_=self.library._thumb_paths(row);self.assertLess(jpg.stat().st_size,500_000)
        row=self.store.one("SELECT * FROM library_posts WHERE shortcode='IMAGE01'")
        old=self.library._thumb_paths(row)[0]
        self.files[0].write_bytes(self.files[0].read_bytes()+b'changed')
        self.library.index(row['id']);changed=self.library.row(row['id'])
        self.assertNotEqual(self.library._thumb_paths(changed)[0],old)
        with patch('subprocess.run',side_effect=OSError('Fixture preview failure')):self.library.make_thumbnail(row['id'])
        self.assertEqual(self.library.thumb_state(changed),'failed')
        with self.library.open_media(row['id'],0) as (stream,offset,media,_):self.assertGreater(media['size'],0)
        self.library.cache_files=2;self.library.prune_cache()
        self.assertLessEqual(len(list(self.library.cache.iterdir())),2)

    def test_delete_exact_files_confirmation_and_missing_duplicate_handling(self):
        self.legacy();self.migrate();post_id=self.store.one("SELECT id FROM library_posts WHERE shortcode='IMAGE01'")['id']
        untouched=self.folder/'keep-me.txt';untouched.write_text('Keep')
        preview=self.library.deletion_preview(post_id)
        self.assertEqual([r['path'] for r in preview['files']],[str(self.files[0]),str(self.files[0].with_suffix('.txt'))])
        with self.assertRaises(ValueError):self.library.delete(post_id,'no-confirmation')
        self.library.delete(post_id,preview['confirmation'])
        self.assertTrue(untouched.exists());self.assertFalse(self.files[0].exists());self.assertFalse(self.files[0].with_suffix('.txt').exists())
        self.assertFalse(self.store.completed('IMAGE01'))
        self.assertEqual(self.store.one('SELECT count(*) n FROM downloads')['n'],3)
        self.assertEqual(self.library.detail(post_id)['status'],'deleted')
        self.migrate();self.assertEqual(self.library.detail(post_id)['status'],'deleted')
        self.assertEqual(self.store.enqueue([('IMAGE01','https://www.instagram.com/p/IMAGE01/')],{'folder':str(self.folder)}),(1,0))

    def test_delete_rejects_changed_or_unrelated_sidecar(self):
        self.legacy();self.migrate();post_id=self.store.one("SELECT id FROM library_posts WHERE shortcode='IMAGE01'")['id']
        preview=self.library.deletion_preview(post_id);self.files[0].write_bytes(self.files[0].read_bytes()+b'changed')
        with self.assertRaises(ValueError):self.library.delete(post_id,preview['confirmation'])
        with self.store.tx() as db:db.execute('UPDATE library_posts SET extras=? WHERE id=?',(json.dumps([str(self.root/'unrelated.txt')]),post_id))
        with self.assertRaises(ValueError):self.library.deletion_preview(post_id)

    def test_thousands_paginated_bounded_and_no_media_reads(self):
        with self.store.tx() as db:
            for i in range(5000):
                stamp=(datetime(2026,10,27,10,tzinfo=timezone.utc)+timedelta(seconds=i)).isoformat()
                register(db,f'BATCH{i:05}',self.folder/f'alice-BATCH{i:05}-2026-10-27.jpg',[],stamp)
        started=time.perf_counter()
        with patch('instagram_local.library.media_type',side_effect=AssertionError('No full media decode')):
            first=self.library.listing();second=self.library.listing(before=first['next'])
            search=self.library.listing(query='BATCH01234')
        self.assertLess(time.perf_counter()-started,3)
        self.assertEqual(len(first['items']),48);self.assertEqual(len(second['items']),48)
        self.assertTrue(set(r['id'] for r in first['items']).isdisjoint(r['id'] for r in second['items']))
        self.assertEqual(len(search['items']),1);self.assertLess(len(json.dumps(first)),50000)


class MediaHttpTests(LibraryBase):
    def setUp(self):
        super().setUp();self.legacy();self.migrate()
        vault=Mock();vault.load.side_effect=AssertionError('Offline Library must not load Instagram credentials')
        worker=Worker(self.store,vault)
        self.app=Application(self.store,vault,worker,'local-fixture-capability');self.app.library=self.library
        self.server=make_server(self.app,0)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.session=requests.Session();self.session.trust_env=False
        self.url=f'http://127.0.0.1:{self.server.server_port}'
        self.headers={'Origin':self.url,'X-App-Token':'local-fixture-capability'}
        self.media_headers={'Sec-Fetch-Site':'same-origin'}

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.session.close();super().tearDown()

    # Only HTTP tests are inherited; unit cases live in the base's own suite.
    def test_media_authorization_paths_and_offline_api(self):
        post_id=self.store.one("SELECT id FROM library_posts WHERE shortcode='IMAGE01'")['id']
        route=f'{self.url}/media/{post_id}/0'
        self.assertEqual(self.session.get(route).status_code,403)
        authorized=self.session.post(self.url+'/api/library/access',json={},headers=self.headers)
        self.assertEqual(authorized.status_code,200)
        self.assertIn('HttpOnly',authorized.headers['Set-Cookie']);self.assertIn('SameSite=Strict',authorized.headers['Set-Cookie'])
        self.assertEqual(self.session.get(route,headers={'Sec-Fetch-Site':'cross-site'}).status_code,403)
        self.assertEqual(self.session.get(route,headers={**self.media_headers,'Host':'evil.test'}).status_code,403)
        self.assertEqual(self.session.get(route,headers=self.media_headers).content,self.files[0].read_bytes())
        for suffix in ['../0','../../credentials.bin','0?path=C:/Windows/win.ini','9999']:
            self.assertNotEqual(self.session.get(f'{self.url}/media/{post_id}/{suffix}',headers=self.media_headers).status_code,200)
        with patch('instagram_local.engine.Engine.post',side_effect=AssertionError('No network')):
            result=self.session.post(self.url+'/api/library/list',json={},headers=self.headers)
            self.assertEqual(result.status_code,200)
            self.assertEqual(len(result.json()['items']),3)

    def test_single_and_zip_video_ranges_and_head(self):
        self.session.cookies.set('library_media',self.app.media_token,path='/media/')
        expected=self.files[1].read_bytes()
        for code,position in [('VIDEO01',0),('CAROUSEL1',1)]:
            post_id=self.store.one('SELECT id FROM library_posts WHERE shortcode=?',(code,))['id'];route=f'{self.url}/media/{post_id}/{position}'
            for range_value,start,end in [('bytes=0-99',0,99),('bytes=100-',100,len(expected)-1),('bytes=-40',len(expected)-40,len(expected)-1)]:
                result=self.session.get(route,headers={**self.media_headers,'Range':range_value})
                self.assertEqual(result.status_code,206);self.assertEqual(result.content,expected[start:end+1])
                self.assertEqual(result.headers['Content-Range'],f'bytes {start}-{end}/{len(expected)}')
                self.assertEqual(result.headers['Content-Type'],'video/mp4')
            result=self.session.head(route,headers=self.media_headers)
            self.assertEqual(result.status_code,200);self.assertEqual(int(result.headers['Content-Length']),len(expected));self.assertEqual(result.content,b'')
            for invalid in ['bytes=999999999-','bytes=9-2','bytes=-0','bytes=0-4,9-10','garbage']:
                self.assertEqual(self.session.get(route,headers={**self.media_headers,'Range':invalid}).status_code,416)
            full=self.session.get(route,headers={**self.media_headers,'Range':'bytes=0-9','If-Range':'"other"'})
            self.assertEqual(full.status_code,200);self.assertEqual(full.content,expected)
