"""YouTube fixtures: no account, Internet, or remote media required."""
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch,Mock
import imageio_ffmpeg
import requests
import yt_dlp
from yt_dlp.networking import Response
from instagram_local.core import Failure,import_file,now,youtube_basename
from instagram_local.routing import route,youtube_options,identity
from instagram_local.youtube import YouTubeEngine,select_formats,classify,convert,prepare_audio_stage,AUDIO_POLICY
from instagram_local.store import Store
from instagram_local.worker import Worker
from instagram_local.library import Library,register_youtube
from instagram_local.server import Application,make_server
from instagram_local.security import parse_cookies
from test_app import PlainTestVault

ID='BaW_jenozKc';URL='https://www.youtube.com/watch?v='+ID
PLAYLIST='PLfixture123456789'


def fmt(name,height=None,audio=False,combined=False,codec='avc1.64001f'):
    return dict(format_id=name,url='https://fixture.googlevideo.com/'+name,
                ext='m4a' if audio else 'mp4',protocol='https',height=height,
                vcodec='none' if audio else codec,acodec='mp4a.40.2' if audio or combined else 'none',
                abr=128 if audio else 0,tbr=height or 128)


class RoutingTests(unittest.TestCase):
    def test_original_audio_before_bitrate_for_mp3_and_mp4(self):
        original={**fmt('140-9',audio=True),'abr':129.476,'language':'en-US','language_preference':10,'format_note':'English (US) original (default), medium'}
        french={**fmt('140-0',audio=True),'abr':129.479,'language':'fr-FR','language_preference':-1,'format_note':'French (FR), medium'}
        indonesian={**fmt('251-8',audio=True),'abr':157.832,'language':'id','language_preference':-1,'ext':'webm','acodec':'opus'}
        english={**fmt('251-9',audio=True),'abr':145.5,'language':'en-US','language_preference':10,'ext':'webm','acodec':'opus'}
        drc={**english,'format_id':'251-drc','abr':145.61,'format_note':'English original, DRC'}
        info={'formats':[fmt('video',360),french,indonesian,original,english,drc]}
        self.assertEqual(select_formats(info,'mp3')[0]['format_id'],'251-9')
        self.assertEqual(select_formats(info,'mp4','360')[1]['format_id'],'140-9')
        self.assertEqual(select_formats({'formats':list(reversed(info['formats']))},'mp3')[0]['format_id'],'251-9')

    def test_original_is_not_assumed_to_be_english(self):
        spanish={**fmt('original',audio=True),'abr':100,'language':'es','language_preference':10}
        english={**fmt('dub',audio=True),'abr':200,'language':'en','language_preference':5}
        for mode in ['mp3','mp4']:
            self.assertEqual(select_formats({'formats':[fmt('video',360),spanish,english]},mode)[-1]['language'],'es')

    def test_ambiguous_or_unusable_original_never_becomes_a_dub(self):
        original={**fmt('original',audio=True),'language':'en','language_preference':10,'protocol':'m3u8_native'}
        french={**fmt('dub',audio=True),'language':'fr','language_preference':5}
        for mode in ['mp3','mp4']:
            with self.assertRaises(Failure):select_formats({'formats':[fmt('video',360),original,french]},mode)
            with self.assertRaisesRegex(Failure,'multiple audio languages'):
                select_formats({'formats':[fmt('video',360),{**original,'language_preference':0},french]},mode)
            with self.assertRaises(Failure):select_formats({'formats':[{**french,'format_note':'French dubbed'}]},mode)

    def test_combined_dub_does_not_override_separate_original_audio(self):
        combined={**fmt('dubbed-combined',360,combined=True),'language':'fr','language_preference':5}
        original={**fmt('original',audio=True),'language':'en','language_preference':10}
        selected=select_formats({'formats':[combined,fmt('video-only',360),original]},'mp4','360')
        self.assertEqual([f['format_id'] for f in selected],['video-only','original'])
        with self.assertRaises(Failure):select_formats({'formats':[combined,original]},'mp4','360')

    def test_youtube_filename_title_channel_id_and_local_date(self):
        self.assertEqual(youtube_basename('Most Epic Deaths in History','Serious History','Yctn_C_6cF8','2026-10-28'),
            'most_epic_deaths_in_history-serious_history-Yctn_C_6cF8-2026-10-28')
        self.assertEqual(youtube_basename('  Hello: WORLD / The "Test"?!  ','Channel / Name',ID,'2026-10-28'),
            'hello_world_the_test-channel_name-'+ID+'-2026-10-28')
        self.assertEqual(youtube_basename(None,None,ID,'2026-10-28'),'untitled-unknown-'+ID+'-2026-10-28')
        value=youtube_basename('A'*500,'B'*500,ID,'2026-10-28')
        self.assertLessEqual(len(value),144);self.assertNotIn('..',value)

    def test_stage_resume_requires_same_track_and_discards_legacy_dub(self):
        with tempfile.TemporaryDirectory() as tmp:
            stage=Path(tmp);media=stage/'media.mp4';partial=stage/'media.f140.part'
            media.write_bytes(b'old dubbed merge');partial.write_bytes(b'old dubbed partial')
            (stage/'INCOMPLETE.txt').write_text('keep',encoding='utf-8')
            selected=[{**fmt('140-9',audio=True),'language':'en-US','language_preference':10}]
            prepare_audio_stage(stage,selected,'mp4')
            self.assertFalse(media.exists());self.assertFalse(partial.exists())
            self.assertTrue((stage/'INCOMPLETE.txt').exists())
            partial.write_bytes(b'correct English partial')
            prepare_audio_stage(stage,selected,'mp4');self.assertEqual(partial.read_bytes(),b'correct English partial')
            prepare_audio_stage(stage,[{**selected[0],'language':'fr-FR'}],'mp4');self.assertFalse(partial.exists())
            self.assertNotIn('https://',(stage/'selection.json').read_text('utf-8'))

    def test_urls_mixed_import_and_explicit_playlist_choice(self):
        for value in [URL,'https://youtu.be/'+ID+'?si=secret','https://m.youtube.com/shorts/'+ID,'youtube.com/live/'+ID]:
            self.assertEqual(route(value)['url'],URL)
        self.assertEqual(route('https://instagram.com/reel/ABCDE/?igsh=secret')['platform'],'instagram')
        both=URL+'&list='+PLAYLIST
        with self.assertRaisesRegex(ValueError,'choice|Choose'):route(both)
        self.assertEqual(route(both,'video')['url'],URL)
        self.assertEqual(route(both,'playlist')['kind'],'playlist')
        self.assertEqual(route('https://youtube.com/playlist?list='+PLAYLIST)['id'],PLAYLIST)
        for special in ['WL','LL','LM','RDMM']:
            self.assertEqual(route('https://youtube.com/playlist?list='+special)['kind'],'playlist')
        text='url,note\n'+URL+',video\nhttps://instagram.com/p/ABCDE/,image'
        self.assertEqual(len(import_file(text,'mixed.csv')['values']),2)
        for bad in ['https://youtube.com/@channel','https://youtube.com/channel/abc','https://youtube.com.evil.test/watch?v='+ID,'https://u:password@youtube.com/watch?v='+ID,'file://youtube.com/watch?v='+ID,URL+'&list=invalid!','https://youtube.com/watch?v=short']:
            with self.assertRaises(ValueError):route(bad)

    def test_segment_identity_and_validation(self):
        full=youtube_options({});clip=youtube_options({'yt_start':'01:02.500','yt_end':'90'})
        self.assertEqual(clip['start'],62.5)
        self.assertNotEqual(identity(ID,full),identity(ID,clip))
        self.assertNotEqual(identity(ID,full),identity(ID,{**full,'mode':'mp3'}))
        self.assertEqual(identity(ID,clip),identity(ID,youtube_options({'yt_start':'62.500','yt_end':'00:01:30'})))
        self.assertNotEqual(identity(ID,{**full,'start':3600.001,'end':3700}),identity(ID,{**full,'start':3600.002,'end':3700}))
        for body in [{'yt_start':4},{'yt_start':10,'yt_end':9},{'yt_start':'nan','yt_end':9},{'yt_start':0,'yt_end':999999},{'yt_quality':2160}]:
            with self.assertRaises(ValueError):youtube_options(body)

    def test_quality_ceiling_lower_fallback_and_usable_mp4(self):
        info={'formats':[fmt('360',360),fmt('720',720),fmt('1080',1080),fmt('audio',audio=True),fmt('webm',2160,codec='vp9')]}
        self.assertEqual(select_formats(info,quality='480')[0]['height'],360)
        self.assertEqual(select_formats(info,quality='720')[0]['height'],720)
        self.assertEqual(select_formats(info)[0]['height'],1080)
        self.assertEqual(select_formats(info,'mp3')[0]['format_id'],'audio')
        for formats in [[fmt('720',720),fmt('audio',audio=True)],[fmt('unknown'),fmt('audio',audio=True)],[fmt('vp9',360,codec='vp9'),fmt('audio',audio=True)]]:
            with self.assertRaises(Failure):select_formats({'formats':formats},quality='360')
        self.assertEqual(len(select_formats({'formats':[fmt('combined',360,combined=True)]})),1)

    def test_classification_does_not_expose_credentials(self):
        for text,kind in [('HTTP Error 429 token=secret','rate_limit'),('Sign in to confirm you’re not a bot secret','challenge'),('Sign in private video secret','auth'),('not available in your country secret','unavailable'),('requested format secret','format'),('connection timed out secret','network'),('HTTP Error 403 secret','restricted')]:
            failure=classify(Exception(text));self.assertEqual(failure.kind,kind);self.assertNotIn('secret',failure.message)
        error=Exception('429');error.status=429;error.headers={'Retry-After':'30'}
        self.assertGreater(classify(error).retry_at,time.time()+20)

    def test_session_domains_proxy_and_no_disk_secrets(self):
        text=json.dumps([dict(domain='.youtube.com',name='SAPISID',value='yt-secret'),dict(domain='.instagram.com',name='sessionid',value='ig-secret'),dict(domain='.evil.test',name='SID',value='evil')])
        cookies=parse_cookies(text,'youtube');self.assertEqual(len(cookies),1)
        engine=YouTubeEngine({'youtube_cookies':cookies,'proxy':'http://user:pass@127.0.0.1:9000'})
        with engine.client() as client:
            self.assertEqual(client.params['proxy'],'http://user:pass@127.0.0.1:9000')
            self.assertFalse(client.params['nocheckcertificate']);self.assertFalse(client.params['cachedir'])
            self.assertIsNone(client.params.get('cookiefile'));self.assertFalse(client.params['writeinfojson'])
            self.assertEqual([c.value for c in client.cookiejar],['yt-secret'])
            self.assertEqual(client.params['retries'],0);self.assertIn('node',client.params['js_runtimes'])


class YouTubeBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.assets=tempfile.TemporaryDirectory();cls.asset=Path(cls.assets.name)
        cls.ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
        opts=dict(stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True)
        if os.name=='nt':opts['creationflags']=subprocess.CREATE_NO_WINDOW
        subprocess.run([cls.ffmpeg,'-nostdin','-f','lavfi','-i','color=c=blue:s=64x64:r=10','-t','2','-c:v','libx264','-pix_fmt','yuv420p','-an',str(cls.asset/'video.mp4')],**opts)
        subprocess.run([cls.ffmpeg,'-nostdin','-f','lavfi','-i','sine=frequency=440:sample_rate=44100','-t','2','-c:a','aac','-vn',str(cls.asset/'audio.m4a')],**opts)

    @classmethod
    def tearDownClass(cls):cls.assets.cleanup()

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.store=Store(self.root/'queue.sqlite3');self.vault=PlainTestVault()
        self.worker=Worker(self.store,self.vault,sleeper=lambda _:None);self.worker.active=True
        self.library=Library(self.store);self.app=Application(self.store,self.vault,self.worker,'fixture-token')
        self.options=dict(folder=str(self.root/'out'),again=False,caption=False,video_id=ID,**youtube_options({}))

    def tearDown(self):
        self.app.library.close();self.library.close();self.worker.close();self.store.db.close();self.tmp.cleanup()

    def queue(self,options=None):
        options=options or self.options
        self.store.enqueue([(identity(ID,options),URL)],options)
        return self.store.one('SELECT * FROM queue ORDER BY id DESC LIMIT 1')

    def success(self,options=None):
        item=self.queue(options);output=self.root/(str(item['id'])+'.mp4');shutil.copyfile(self.asset/'video.mp4',output)
        self.store.success(item,output,[],metadata=dict(video_id=ID,title='Fixture title',channel='Fixture channel',duration=2,resolution=64,mode='mp4',start=None,end=None))
        return item,output


class PersistenceTests(YouTubeBase):
    def test_legacy_finalization_cannot_silently_commit_an_old_dub(self):
        item=self.queue();stage=self.root/'out'/'.incomplete'/str(item['id']);stage.mkdir(parents=True)
        existing=self.root/'out'/'old.mp4';existing.write_bytes(b'keep existing file')
        (stage/'final.json').write_text(json.dumps(dict(metadata={'video_id':ID},files=[])),encoding='utf-8')
        self.worker.engine=Mock();self.worker._item(item)
        self.worker.engine.download_post.assert_not_called()
        self.assertFalse(self.store.completed(item['shortcode']));self.assertEqual(existing.read_bytes(),b'keep existing file')
        failed=self.store.one('SELECT * FROM queue');self.assertEqual(failed['status'],'failed');self.assertIn('older audio selection',failed['error'])

    def test_playlist_library_pages_follow_recorded_positions(self):
        with self.store.tx() as db:
            db.execute('INSERT INTO youtube_playlists VALUES(?,?,?)',(PLAYLIST,'Ordered','https://www.youtube.com/playlist?list='+PLAYLIST))
            for i in range(53):
                video_id=f'{i:011d}';path=self.root/f'{i}.mp4'
                register_youtube(db,path,[],f'2026-10-28T10:00:{52-i:02d}+00:00',dict(video_id=video_id,title=f'Video {i}'))
                db.execute('INSERT INTO youtube_members VALUES(?,?,?)',(PLAYLIST,i+1,video_id))
        first=self.library.listing(playlist=PLAYLIST);second=self.library.listing(playlist=PLAYLIST,before=first['next'])
        self.assertEqual([r['playlist_position'] for r in first['items']],list(range(1,49)))
        self.assertEqual([r['playlist_position'] for r in second['items']],list(range(49,54)))
        self.assertIsNone(second['next'])

    def test_youtube_session_import_and_clear_preserve_instagram(self):
        self.worker.active=False;self.vault.data={'cookies':[{'name':'sessionid','value':'instagram-sentinel'}],'proxy':'http://localhost:9000'}
        self.app.dispatch('/api/youtube-cookies',{'text':json.dumps([dict(domain='.youtube.com',name='SAPISID',value='youtube-sentinel')])})
        self.assertTrue(self.app.settings()['youtube_session']);self.assertEqual(self.vault.data['cookies'][0]['value'],'instagram-sentinel')
        self.app.dispatch('/api/youtube-disconnect',{})
        self.assertFalse(self.app.settings()['youtube_session']);self.assertEqual(self.vault.data['cookies'][0]['value'],'instagram-sentinel')
        self.assertEqual(self.vault.data['proxy'],'http://localhost:9000')

    def test_playlist_keeps_lazy_iterator_between_batches(self):
        self.store.add_source('youtube_playlist','https://www.youtube.com/playlist?list='+PLAYLIST,'Playlist',10000,self.options)
        source=self.store.one('SELECT * FROM sources');calls=[]
        def enumerate_source(source):
            calls.append(source['offset'])
            for i in range(80):yield f'{i:011d}',None,i+1,dict(playlist_id=PLAYLIST,title='Playlist',position=i+1)
        self.worker.engine=Mock();self.worker.engine.enumerate=enumerate_source
        self.worker._source(source)
        self.worker._source(self.store.one('SELECT * FROM sources'))
        self.assertEqual(calls,[0]);self.assertEqual(len(self.store.rows('SELECT * FROM youtube_members')),80)
        self.assertEqual(self.store.one('SELECT status FROM sources')['status'],'done')
        self.assertIsNone(self.worker.playlist_session)

    def test_thousands_of_youtube_records_search_and_page_bound(self):
        with self.store.tx() as db:
            db.executemany('''INSERT INTO library_posts(output,path_key,shortcode,downloaded_at,download_date,filename,platform,title,channel,status)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',[(str(self.root/f'{i}.mp4'),str(self.root/f'{i}.mp4'),f'{i:011d}',f'2026-10-28T10:{i//60%60:02}:{i%60:02}+00:00','2026-10-28',f'{i}.mp4','youtube',f'Title {i}','Test channel','missing') for i in range(5000)])
        first=self.library.listing(platform='youtube');second=self.library.listing(platform='youtube',before=first['next'])
        self.assertEqual(len(first['items']),48);self.assertEqual(len(second['items']),48)
        self.assertFalse({r['id'] for r in first['items']} & {r['id'] for r in second['items']})
        self.assertEqual(len(self.library.listing(platform='youtube',query='Title 4999')['items']),1)

    def test_higher_cached_resolution_does_not_satisfy_lower_ceiling(self):
        item,output=self.success()
        with self.store.tx() as db:
            db.execute('UPDATE library_posts SET metadata=?',(json.dumps(dict(resolution=1080)),))
        self.assertFalse(self.store.completed(item['shortcode'],{**self.options,'quality':'720'}))
        self.assertEqual(self.store.enqueue([(item['shortcode'],URL)],{**self.options,'quality':'720'}),(1,0))

    def test_conversion_failure_never_completes_or_indexes_partial_job(self):
        item=self.queue();self.worker.engine=Mock();self.worker.engine.download_post.side_effect=Failure('format','Conversion failed')
        self.worker._item(item)
        self.assertFalse(self.store.completed(item['shortcode']));self.assertEqual(self.library.listing()['items'],[])
        self.assertEqual(self.store.one('SELECT status FROM queue')['status'],'failed')

    def test_mixed_dispatch_per_line_errors_and_playlist_range_rejection(self):
        result=self.app.dispatch('/api/links',dict(values=[URL,'https://instagram.com/p/ABCDE/',URL,'https://youtube.com/@channel',URL+'&list='+PLAYLIST]))
        self.assertEqual(result['invalid_count'],2);self.assertEqual(len(self.store.rows('SELECT * FROM queue')),2)
        self.assertIn('1 repeated',result['message'])
        result=self.app.dispatch('/api/links',dict(values=[URL+'&list='+PLAYLIST],yt_scope='playlist',yt_start=1,yt_end=2))
        self.assertEqual(result['invalid_count'],1);self.assertEqual(self.store.rows('SELECT * FROM sources'),[])

    def test_ordered_bounded_playlist_checkpoint_membership_skips_existing(self):
        item,output=self.success()
        for playlist in [PLAYLIST,'PLanother123456789']:
            self.store.add_source('youtube_playlist','https://www.youtube.com/playlist?list='+playlist,'Playlist',10000,self.options)
            source=self.store.one('SELECT * FROM sources ORDER BY id DESC LIMIT 1')
            def entries(source):
                for i in range(80):yield ID if i==0 else f'{i:011d}',None,i+1,dict(playlist_id=playlist,title='Fixture playlist '+playlist,position=i+1)
            engine=Mock();engine.enumerate=entries;self.worker.engine=engine
            self.worker._source(source)
            self.assertEqual(self.store.one('SELECT * FROM sources WHERE id=?',(source['id'],))['offset'],50)
        self.assertEqual(len(self.store.rows('SELECT * FROM youtube_members')),100)
        row=self.library.listing(platform='youtube')['items'][0]
        self.assertEqual(len(row['playlists']),2)
        self.assertEqual(len(self.store.rows("SELECT * FROM queue WHERE shortcode=? AND status='done'",(item['shortcode'],))),1)
        self.assertEqual(self.library.listing(playlist=PLAYLIST)['items'][0]['id'],row['id'])
        self.assertEqual([r['position'] for r in self.store.rows('SELECT position FROM youtube_members WHERE playlist_id=? ORDER BY position',(PLAYLIST,))],list(range(1,51)))

    def test_lazy_enumerator_bounded_and_resumes_position(self):
        engine=YouTubeEngine({},sleeper=lambda _:None);requested={};consumed=[]
        def client(extra):
            requested.update(extra);mock=Mock()
            def entries():
                for i in range(5,10000):
                    consumed.append(i);yield {'id':f'{i:011d}'}
            mock.extract_info.return_value=dict(_type='playlist',title='Title',entries=entries())
            mock.__enter__=Mock(return_value=mock);mock.__exit__=Mock(return_value=False)
            return mock
        engine.client=client
        import itertools
        source=dict(offset=4,target='https://www.youtube.com/playlist?list='+PLAYLIST,label='Playlist')
        iterator=engine.enumerate(source);items=list(itertools.islice(iterator,50));iterator.close()
        self.assertEqual(len(consumed),50);self.assertEqual(items[0][2],5);self.assertEqual(items[-1][2],54)
        self.assertTrue(requested['lazy_playlist']);self.assertEqual(requested['playliststart'],5);self.assertEqual(requested['playlistend'],54)

    def test_identity_duplicates_missing_files_and_additive_migration(self):
        item,output=self.success();code=item['shortcode']
        self.assertTrue(self.store.completed(code,self.options))
        self.assertEqual(self.store.enqueue([(code,URL)],self.options),(0,1))
        self.assertEqual(self.store.enqueue([(identity(ID,{**self.options,'mode':'mp3'}),URL)],{**self.options,'mode':'mp3'}),(1,0))
        self.assertTrue(self.store.completed(code,{**self.options,'quality':'360'}))
        library_id=self.library.listing()['items'][0]['id'];self.library.flag(library_id,'favorite',True);self.library.flag(library_id,'hidden',True)
        self.store.set_setting('sentinel',{'keep':True});self.store.db.close();self.store=Store(self.root/'queue.sqlite3');self.library=Library(self.store)
        self.assertTrue(self.library.listing(hidden=True)['items'][0]['favorite'])
        self.assertEqual(self.store.setting('sentinel'),{'keep':True});self.assertFalse(Worker(self.store,self.vault).active)
        output.unlink();self.assertFalse(self.store.completed(code));self.assertEqual(self.library.detail(library_id)['status'],'missing')
        self.assertEqual(self.store.enqueue([(code,URL)],self.options),(1,0))

    def test_future_metadata_search_filters_and_offline_library(self):
        item,output=self.success();row=self.library.listing()['items'][0]
        with patch('requests.Session.request',side_effect=AssertionError('Must stay offline')):
            for query in ['Fixture title','Fixture channel',ID,'watch?v=',output.name,row['download_date']]:
                self.assertEqual(self.library.listing(query=query,platform='youtube')['items'][0]['id'],row['id'])
            self.assertEqual(self.library.listing(platform='instagram')['items'],[])
            self.assertEqual(self.library.detail(row['id'])['metadata']['resolution'],64)
            with self.assertRaises(ValueError):self.library.queue_details(row['id'])
        self.assertNotIn('googlevideo',self.store.one('SELECT metadata FROM library_posts')['metadata'])
        while self.library.sync_records():pass
        self.assertEqual(len(self.store.rows('SELECT * FROM library_posts')),1)

    def test_interrupted_finalization_recovers_without_redownload(self):
        item=self.queue();self.worker.engine=Mock()
        def download(url,stage,options,progress):
            target=stage/'media.mp4';shutil.copyfile(self.asset/'video.mp4',target)
            return target,[],dict(video_id=ID,channel='CON',title='Fixture',mode='mp4',resolution=64)
        self.worker.engine.download_post.side_effect=download
        with patch.object(self.store,'success',side_effect=OSError('simulated crash after rename')):self.worker._item(item)
        self.assertFalse(self.store.completed(item['shortcode']))
        recovered=self.store.one('SELECT * FROM queue WHERE id=?',(item['id'],))
        self.worker._item(recovered)
        self.assertEqual(self.worker.engine.download_post.call_count,1)
        self.assertTrue(self.store.completed(item['shortcode']))
        self.assertTrue(Path(self.store.one('SELECT output FROM downloads')['output']).name.startswith('fixture-_con-'))
        self.assertFalse((self.root/'out'/'.incomplete'/str(item['id'])).exists())

    def test_restrictions_pause_and_transient_retry_limit(self):
        for kind in ['auth','challenge','rate_limit']:
            item=self.queue({**self.options,'again':True});engine=Mock();engine.download_post.side_effect=Failure(kind,'Fixed restriction')
            self.worker.engine=engine;self.worker.active=True;self.worker._item(item)
            self.assertFalse(self.worker.active);self.assertEqual(engine.download_post.call_count,1)
            self.store.update_item(item['id'],status='canceled')
        item=self.queue();engine=Mock();engine.download_post.side_effect=Failure('network','Fixed network error')
        self.worker.engine=engine;self.worker.youtube_factory=lambda secret:engine;self.worker.active=True
        self.worker._item(item);self.assertEqual(engine.download_post.call_count,3)
        self.assertEqual(self.store.one('SELECT status FROM queue WHERE id=?',(item['id'],))['status'],'failed')


class RealMediaTests(YouTubeBase):
    def test_new_filenames_sidecars_collisions_and_library_audio_metadata(self):
        folder=self.root/'out';folder.mkdir()
        base=youtube_basename('A Title: Test!','My Channel',ID)
        occupied=folder/(base+'.mp4');occupied.write_bytes(b'unrelated file')
        for mode in ['mp4','mp3']:
            item=self.queue({**self.options,'mode':mode})
            def download(url,stage,options,progress):
                target=stage/('media.'+options['mode'])
                if options['mode']=='mp3':convert(self.asset/'audio.m4a',target,options)
                else:shutil.copyfile(self.asset/'video.mp4',target)
                subtitle=stage/'media.en.vtt';subtitle.write_text('WEBVTT\n',encoding='utf-8')
                return target,[subtitle],dict(video_id=ID,title='A Title: Test!',channel='My Channel',mode=options['mode'],
                    audio_language='en-US',audio_original=True,audio_policy=AUDIO_POLICY)
            self.worker.engine=Mock();self.worker.engine.download_post.side_effect=download;self.worker._item(item)
            row=self.store.one('SELECT * FROM downloads WHERE shortcode=?',(item['shortcode'],));output=Path(row['output'])
            self.assertTrue(output.name.startswith(base+' ('));self.assertEqual(output.suffix,'.'+mode)
            self.assertTrue(Path(json.loads(row['extras'])[0]).name.startswith(output.stem+'.en.vtt'))
            data=self.library.listing(query=output.name)['items'][0]
            self.assertEqual(data['metadata']['audio_language'],'en-US');self.assertTrue(data['metadata']['audio_original'])
        self.assertEqual(occupied.read_bytes(),b'unrelated file')

    def test_audio_artwork_changes_invalidate_thumbnail(self):
        mp3=self.root/'audio.mp3';convert(self.asset/'audio.m4a',mp3,{'mode':'mp3'})
        artwork=self.root/'audio.jpg'
        subprocess.run([self.ffmpeg,'-nostdin','-loglevel','error','-i',str(self.asset/'video.mp4'),'-frames:v','1','-y',str(artwork)],
            check=True,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
        with self.store.tx() as db:post_id=register_youtube(db,mp3,[artwork],now(),dict(video_id=ID,mode='mp3'))
        self.library.index(post_id);self.library.make_thumbnail(post_id);row=self.library.row(post_id)
        self.assertEqual(self.library.thumb_state(row),'ready');old=self.library._thumb_paths(row)[0]
        with artwork.open('ab') as stream:stream.write(b'changed')
        self.assertEqual(self.library.thumb_state(row),'pending');self.library.make_thumbnail(post_id)
        self.assertEqual(self.library.thumb_state(row),'ready');self.assertNotEqual(self.library._thumb_paths(row)[0],old)

    def test_real_engine_mp4_merge_mp3_and_segments(self):
        requested=[]
        def fixture_open(client,request):
            url=request if isinstance(request,str) else request.url
            name=url.rsplit('/',1)[-1];requested.append((name,client.params['proxy']))
            data=(self.asset/name).read_bytes()
            return Response(io.BytesIO(data),url,{'Content-Length':str(len(data))})
        for mode,start,end in [('mp4',None,None),('mp3',None,None),('mp4',.2,1.5),('mp3',.2,1.5)]:
            options={**self.options,'mode':mode,'start':start,'end':end,'quality':'360'}
            stage=self.root/(mode+str(start));info=dict(id=ID,title='A title',channel='Channel',duration=2,
                formats=[fmt('video.mp4',64),{**fmt('audio.m4a',audio=True),'language':'en-US','language_preference':10},
                         {**fmt('french.m4a',audio=True),'language':'fr-FR','language_preference':5,'abr':256}],extractor='youtube',webpage_url=URL)
            engine=YouTubeEngine({'proxy':'http://fixture:9000'},sleeper=lambda _:None)
            with patch.object(yt_dlp.YoutubeDL,'extract_info',return_value=info),patch.object(yt_dlp.YoutubeDL,'urlopen',fixture_open):
                output,extras,meta=engine.download_post(URL,stage,options,lambda *_:None)
            self.assertTrue(output.is_file());self.assertEqual(output.suffix,'.'+mode);self.assertEqual(meta['video_id'],ID)
            self.assertEqual(meta['resolution'],64 if mode=='mp4' else None)
            self.assertEqual(meta['audio_language'],'en-US');self.assertTrue(meta['audio_original'])
            self.assertEqual(extras,[])
            probe=subprocess.run([self.ffmpeg,'-hide_banner','-i',str(output)],capture_output=True,text=True,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
            self.assertIn('Audio:',probe.stderr)
            self.assertIn('Video: h264',probe.stderr) if mode=='mp4' else self.assertIn('Audio: mp3',probe.stderr)
        self.assertTrue(all(proxy=='http://fixture:9000' for _,proxy in requested))
        self.assertNotIn('french.m4a',[name for name,_ in requested])

    def test_audio_library_ranges_thumbnail_and_sidecar_deletion(self):
        mp3=self.root/'audio.mp3';convert(self.asset/'audio.m4a',mp3,{'mode':'mp3'})
        sidecar=self.root/'audio.en.vtt';sidecar.write_text('WEBVTT\n',encoding='utf-8')
        with self.store.tx() as db:
            library_id=register_youtube(db,mp3,[sidecar],now(),dict(video_id=ID,title='Audio',mode='mp3'))
        self.library.index(library_id);self.library.make_thumbnail(library_id)
        self.assertEqual(self.library.thumb_state(self.library.row(library_id)),'ready')
        server=make_server(self.app,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        root=f'http://127.0.0.1:{server.server_port}';session=requests.Session();session.trust_env=False
        try:
            session.post(root+'/api/library/access',json={},headers={'X-App-Token':'fixture-token','Origin':root})
            r=session.get(root+f'/media/{library_id}/0',headers={'Sec-Fetch-Site':'same-origin','Range':'bytes=0-15'})
            self.assertEqual(r.status_code,206);self.assertEqual(r.headers['Content-Type'],'audio/mpeg');self.assertEqual(r.content,mp3.read_bytes()[:16])
            self.assertEqual(requests.get(root+f'/media/{library_id}/0',proxies={'http':None}).status_code,403)
        finally:server.shutdown();server.server_close();thread.join();session.close()
        plan=self.library.deletion_preview(library_id);self.library.delete(library_id,plan['confirmation'])
        self.assertFalse(mp3.exists());self.assertFalse(sidecar.exists())


if __name__=='__main__':unittest.main()
