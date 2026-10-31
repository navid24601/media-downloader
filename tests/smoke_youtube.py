"""Explicit, single public-video smoke test. Never run by unittest discovery."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import subprocess
import tempfile
import imageio_ffmpeg
from instagram_local.youtube import YouTubeEngine
from instagram_local.security import Vault
from instagram_local.core import Failure


class SmallSmokeEngine(YouTubeEngine):
    def client(self,extra=None):
        extra={**(extra or {}),'max_filesize':30*1024*1024,
               'match_filter':lambda info,**_: 'Smoke test accepts at most 30 seconds.' if (info.get('duration') or 999)>30 else None}
        return super().client(extra)


if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    parent=root/'test-output';parent.mkdir(exist_ok=True)
    # Reuse only the protected connection setting. No account cookies are loaded
    # into yt-dlp, and no secret value or raw exception is printed.
    try:
        proxy=Vault(root/'.local'/'credentials.bin').load().get('proxy','')
        with tempfile.TemporaryDirectory(prefix='youtube-public-smoke-',dir=parent) as folder:
            engine=SmallSmokeEngine({'proxy':proxy})
            media,extras,metadata=engine.download_post('https://www.youtube.com/watch?v=jNQXAC9IVRw',Path(folder),
                dict(mode='mp4',quality='360',start=None,end=None,thumbnail=False,subtitles=False),lambda *_:None)
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-v','error','-i',str(media),'-f','null','-'],
                check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=60,
                **({'creationflags':subprocess.CREATE_NO_WINDOW} if sys.platform=='win32' else {}))
            print('Public smoke passed: playable MP4,',metadata['resolution'],'p,',media.stat().st_size,'bytes. Test files cleaned.')
    except Failure as exc:
        print('Public smoke blocked:',exc.kind,'—',exc.message,'Test files cleaned.');sys.exit(2)
    except Exception:
        print('Public smoke could not complete local verification. Test files cleaned.');sys.exit(3)
