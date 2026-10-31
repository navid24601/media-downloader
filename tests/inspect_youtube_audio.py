"""Explicit metadata-only diagnostic; never run by unittest discovery."""
import json
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from instagram_local.core import Failure,youtube_basename
from instagram_local.routing import route
from instagram_local.security import Vault
from instagram_local.youtube import YouTubeEngine,classify,select_formats

if __name__=='__main__':
    try:
        parsed=route(sys.argv[1],'video')
        if parsed['platform']!='youtube' or parsed['kind']!='video':raise ValueError()
        root=Path(__file__).resolve().parents[1]
        proxy=Vault(root/'.local'/'credentials.bin').load().get('proxy','')
        engine=YouTubeEngine({'proxy':proxy})
        with engine.client({'skip_download':True}) as client:
            info=client.extract_info(parsed['url'],download=False)
            def summary(f):return {k:f.get(k) for k in ('format_id','language','language_preference','format_note','ext','abr','height')}
            audio=[f for f in info.get('formats',[]) if f.get('acodec') not in (None,'none') and f.get('vcodec')=='none']
            aac=[f for f in audio if f.get('ext')=='m4a']
            fixed={mode:[summary(f) for f in select_formats(info,mode,'360')] for mode in ('mp3','mp4')}
            print(json.dumps(dict(title=info.get('title'),channel=info.get('channel'),language=info.get('language'),
                duration=info.get('duration'),languages=sorted({f['language'] for f in audio if f.get('language')}),
                old_bitrate_only_mp3=summary(max(audio,key=lambda f:f.get('abr') or f.get('tbr') or 0)),
                old_bitrate_only_mp4_audio=summary(max(aac,key=lambda f:f.get('abr') or f.get('tbr') or 0)),
                fixed=fixed,filename_base=youtube_basename(info.get('title'),info.get('channel'),info['id'])),ensure_ascii=True,indent=2))
    except Exception as exc:
        failure=exc if isinstance(exc,Failure) else classify(exc)
        print(failure.kind+': '+failure.message);sys.exit(1)
