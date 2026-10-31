"""Combined offline Library UI fixture, no network worker or real session."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import json
import shutil
import requests
import yt_dlp
from library_fixtures import generate
from test_app import PlainTestVault
from instagram_local.core import now
from instagram_local.youtube import convert
from instagram_local.library import register_youtube
from instagram_local.store import Store
from instagram_local.worker import Worker
from instagram_local.server import Application,make_server

root=Path(__file__).resolve().parents[1]/'test-output'/'combined-library'
files=generate(root/'media');store=Store(root/'fixture.sqlite3')
for index,path in enumerate(files):
    with store.tx() as db:db.execute('INSERT OR REPLACE INTO downloads VALUES(?,?,?,?)',
        (['IMAGE01','VIDEO01','CAROUSEL1'][index],str(path),'[]',f'2026-10-{25+index}T10:00:00+00:00'))
video=root/'media'/'Fixture channel-BaW_jenozKc-2026-10-28.mp4';shutil.copyfile(files[1],video)
# The v2 fixture video has no audio; create a tiny sine-wave audio locally.
import subprocess,os,imageio_ffmpeg
audio=video.with_suffix('.mp3')
subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-v','error','-f','lavfi','-i','sine=frequency=440:duration=2','-c:a','libmp3lame','-y',str(audio)],
    check=True,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
with store.tx() as db:
    for path,mode in [(video,'mp4'),(audio,'mp3')]:
        register_youtube(db,path,[],now(),dict(video_id='BaW_jenozKc',title='Offline YouTube fixture '+mode.upper(),channel='Fixture channel',duration=2,resolution=128 if mode=='mp4' else None,mode=mode,start=None,end=None))
    db.execute("INSERT OR REPLACE INTO youtube_playlists VALUES('PLfixture123456789','Offline playlist','https://www.youtube.com/playlist?list=PLfixture123456789')")
    db.execute("INSERT OR REPLACE INTO youtube_members VALUES('PLfixture123456789',1,'BaW_jenozKc')")
store.set_setting('folder',str(root/'media'));vault=PlainTestVault();worker=Worker(store,vault)
app=Application(store,vault,worker,'combined-ui-fixture')
def blocked(*args,**kwargs):raise AssertionError('Outbound requests disabled in offline fixture')
requests.Session.request=blocked;yt_dlp.YoutubeDL.urlopen=blocked
app.library.start();server=make_server(app,8793)
print('Combined offline fixture ready on 127.0.0.1:8793; worker stopped.')
try:server.serve_forever()
finally:app.library.close();server.server_close();store.db.close()
