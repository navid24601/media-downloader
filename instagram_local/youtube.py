"""YouTube-only yt-dlp adapter. No shell, browser extraction, or persisted info JSON."""
import http.cookiejar
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import time
from urllib.request import getproxies
import imageio_ffmpeg
import yt_dlp
from .core import Failure, retry_after
from .routing import VIDEO

MAX_BYTES = 10*1024**3  # Per job, including staging; never an unbounded playlist download.
AUDIO_POLICY = 'original-v1'


def original_audio(fmt):
    # Contract checked against the pinned YouTube extractor: original=10,
    # account/region default=5, other language=-1, descriptive=-10.
    preference=fmt.get('language_preference')
    return (isinstance(preference,(int,float)) and preference>=10 or
            bool(re.search(r'\boriginal\b',str(fmt.get('format_note','')),re.I)))


def preferred_audio(formats):
    """Pick the track before bitrate/codec. Never infer original from 'English'."""
    audio=[f for f in formats if f.get('acodec') not in (None,'none')]
    neutral=[f for f in audio if not re.search(r'\b(?:descriptive|description|dubbed)\b',str(f.get('format_note','')),re.I)
             and not str(f.get('language','')).endswith('-desc') and (f.get('language_preference') or 0)>-10]
    originals=[f for f in neutral if original_audio(f)]
    if originals:return originals
    languages={f['language'] for f in neutral if f.get('language')}
    if len(languages)>1:
        raise Failure('format','YouTube exposed multiple audio languages without identifying the original. No dubbed track was chosen. Try again later with fresh metadata.')
    if not neutral:
        raise Failure('format','The original audio track is unavailable; no dubbed or descriptive track was substituted.')
    # Older/single-track videos may expose no original marker. With one known
    # language prefer its explicitly labeled formats over unmarked combined ones.
    if languages:return [f for f in neutral if f.get('language') in languages]
    return neutral


def audio_quality(fmt):
    # DRC changes dynamics; prefer the ordinary version of the same original
    # track before comparing bitrate. Tiny bitrate differences are not language.
    drc=bool(re.search(r'\bDRC\b',str(fmt.get('format_note','')),re.I) or str(fmt.get('format_id','')).endswith('-drc'))
    return (not drc,fmt.get('abr') or fmt.get('tbr') or 0,fmt.get('asr') or 0)


def classify(error):
    """Only inspect engine diagnostics in memory; return fixed, credential-free text."""
    text=str(error).lower()
    cause=error
    for _ in range(6):
        underlying=getattr(cause,'exc_info',None)
        nested=(underlying[1] if underlying else None) or getattr(cause,'cause',None) or getattr(cause,'__cause__',None)
        if not nested or nested is cause:break
        cause=nested
    status=getattr(cause,'status',None) or getattr(cause,'code',None)
    headers=getattr(cause,'headers',{}) or {}
    if status==429 or any(x in text for x in ('too many requests','rate limit','http error 429')):
        return Failure('rate_limit','YouTube limited requests. Wait and check YouTube in Chrome before manually resuming.',retry_after(headers.get('Retry-After')))
    if any(x in text for x in ('not a bot','captcha','challenge','confirm you’re','confirm you\'re')):
        return Failure('challenge','YouTube requires verification. Open YouTube in Chrome, resolve it, and optionally import fresh YouTube cookies before resuming.')
    if any(x in text for x in ('sign in','login required','cookies are no longer valid','members-only','age-restricted','private video')):
        return Failure('auth','YouTube needs account access. Check that your account can view this video in Chrome and import fresh YouTube cookies before resuming.')
    if any(x in text for x in ('in your country','not available in your region','geo restricted','geo-restricted')):
        return Failure('unavailable','This video is unavailable in the configured connection’s region.')
    if any(x in text for x in ('requested format','no video formats','only images','ffmpeg','postprocessing')):
        return Failure('format','No usable format or completed conversion was produced. Check the quality ceiling and FFmpeg setup; retry explicitly after correcting it.')
    if any(x in text for x in ('timed out','timeout','connection','name resolution','temporary failure','http error 5','network is unreachable')):
        return Failure('network','Temporary YouTube connection failure. Check your VPN/TUN or proxy, then retry.')
    if status==403 or 'http error 403' in text:
        return Failure('restricted','YouTube denied access. Check access in Chrome and the configured connection before manually resuming. Some formats require account or engine support that is unavailable here.')
    return Failure('unavailable','YouTube did not return usable media. Check the video’s availability, account access, and engine setup in README.')


class QuietLogger:
    def debug(self,*args):pass
    def info(self,*args):pass
    def warning(self,*args):pass
    def error(self,*args):pass


def select_formats(info, mode='mp4', quality='best'):
    """Use only known heights and browser-playable MP4 codecs. Never exceed a ceiling."""
    formats=info.get('formats',[])
    # Resolve track identity using all exposed formats, including formats that
    # cannot be downloaded here. An unusable original must not become a dub.
    preferred=preferred_audio(formats)
    preferred_ids={id(f) for f in preferred}
    # Keep all network I/O in yt-dlp's TLS-verifying native HTTP stack.
    # HLS can fall back to an external FFmpeg downloader (including different
    # SOCKS/TLS behavior); those formats are deliberately not selected here.
    formats=[f for f in formats if not f.get('has_drm') and f.get('protocol') in {'https','http_dash_segments'}]
    tracks=[f for f in formats if id(f) in preferred_ids]
    if mode=='mp3':
        audio=[f for f in tracks if f.get('vcodec')=='none']
        audio=audio or tracks
        if not audio:raise Failure('format','No usable audio stream is available for MP3 conversion.')
        return [max(audio,key=audio_quality)]
    ceiling=None if quality=='best' else int(quality)
    audio=[f for f in tracks if f.get('vcodec')=='none' and f.get('ext')=='m4a' and str(f.get('acodec','')).startswith(('mp4a','aac'))]
    candidates=[]
    for f in formats:
        if f.get('ext')!='mp4' or not str(f.get('vcodec','')).startswith(('avc1','h264','av01')):continue
        height=f.get('height')
        if not isinstance(height,(int,float)) or height<=0 or ceiling is not None and height>ceiling:continue
        if f.get('acodec')=='none':
            if not audio:continue
        elif id(f) not in preferred_ids or not str(f.get('acodec','')).startswith(('mp4a','aac')):continue
        candidates.append(f)
    if not candidates:
        raise Failure('format','No playable MP4 at or below the selected ceiling is available without video re-encoding. Try Best or another output mode.')
    video=max(candidates,key=lambda f:(f['height'],f.get('fps') or 0,f.get('tbr') or 0))
    if video.get('acodec')!='none':return [video]
    return [video,max(audio,key=audio_quality)]


def format_selector(options):
    def select(context):
        selected=select_formats(context,options.get('mode','mp4'),options.get('quality','best'))
        if len(selected)==1:yield selected[0];return
        video,audio=selected
        yield dict(format_id=video['format_id']+'+'+audio['format_id'],ext='mp4',
                   requested_formats=selected,protocol=video['protocol']+'+'+audio['protocol'],
                   height=video['height'],width=video.get('width'),vcodec=video['vcodec'],acodec=audio['acodec'],
                   language=audio.get('language'),language_preference=audio.get('language_preference'))
    return select


def prepare_audio_stage(stage, selected, mode):
    """Resume only media from the same track selection, never an old dubbed file."""
    stamp=dict(policy=AUDIO_POLICY,mode=mode,formats=[f.get('format_id') for f in selected],
               languages=[f.get('language') for f in selected])
    receipt=stage/'selection.json'
    try:previous=json.loads(receipt.read_text('utf-8'))
    except (OSError,ValueError):previous=None
    if previous!=stamp:
        for path in stage.iterdir():
            if (path.name.startswith('media.') or path.name in {'converted.mp4','converted.mp3'}) and path.is_file() and not path.is_symlink():
                path.unlink()
        temporary=receipt.with_suffix('.tmp');temporary.write_text(json.dumps(stamp),encoding='utf-8');os.replace(temporary,receipt)


def convert(source, target, options):
    """Local-only post-processing. MP4 cuts use stream copy (keyframe boundaries)."""
    command=[imageio_ffmpeg.get_ffmpeg_exe(),'-nostdin','-hide_banner','-loglevel','error','-protocol_whitelist','file,pipe']
    if options.get('start') is not None:command+=['-ss',str(options['start'])]
    command+=['-i',str(source)]
    if options.get('end') is not None:command+=['-t',str(options['end']-options['start'])]
    if options['mode']=='mp3':command+=['-vn','-map','0:a:0','-c:a','libmp3lame','-q:a','2']
    else:command+=['-map','0:v:0','-map','0:a:0?','-c','copy','-avoid_negative_ts','make_zero','-movflags','+faststart']
    command+=['-y',str(target)]
    try:
        subprocess.run(command,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=True,timeout=1800,
                       **({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
    except (subprocess.SubprocessError,OSError):
        raise Failure('format','Local media conversion failed. Partial staging is retained; the original full download is unchanged.') from None
    if not target.is_file() or target.stat().st_size<32:
        raise Failure('incomplete','Local conversion did not produce complete media.')


class YouTubeEngine:
    _last_request=0
    def __init__(self,secrets,sleeper=time.sleep):
        self.secrets=secrets;self.sleep=sleeper

    def close(self):pass

    def pace(self):
        delay=6-(time.monotonic()-type(self)._last_request)
        if delay>0:self.sleep(delay)
        type(self)._last_request=time.monotonic()

    def params(self,extra=None):
        detected=getproxies()
        proxy=self.secrets.get('proxy') or detected.get('https') or detected.get('all') or detected.get('http') or ''
        # One explicit yt-dlp proxy is used by its extractor, media, fragments,
        # subtitles and thumbnails. No external network downloader is invoked.
        return dict(quiet=True,no_warnings=True,logger=QuietLogger(),cachedir=False,
                    proxy=proxy,nocheckcertificate=False,socket_timeout=30,
                    retries=0,fragment_retries=0,extractor_retries=0,file_access_retries=0,sleep_interval_requests=1,
                    skip_unavailable_fragments=False,concurrent_fragment_downloads=1,
                    noplaylist=True,windowsfilenames=True,continuedl=True,nopart=False,
                    writeinfojson=False,clean_infojson=True,allow_unplayable_formats=False,
                    js_runtimes={'node':{'path':shutil.which('node') or 'node'}},remote_components=set(),
                    ffmpeg_location=imageio_ffmpeg.get_ffmpeg_exe(),**(extra or {}))

    def client(self,extra=None):
        client=yt_dlp.YoutubeDL(self.params(extra))
        for row in self.secrets.get('youtube_cookies',[]):
            client.cookiejar.set_cookie(http.cookiejar.Cookie(0,row['name'],row['value'],None,False,
                row['domain'],True,row['domain'].startswith('.'), '/',True,True,None,True,None,None,{}))
        return client

    def enumerate(self,source):
        self.pace()
        start=source['offset']+1
        try:
            with self.client(dict(extract_flat='in_playlist',lazy_playlist=True,
                                 playliststart=start,playlistend=start+49,ignoreerrors=False)) as client:
                data=client.extract_info(source['target'],download=False)
                if not data or data.get('_type')!='playlist':raise Failure('engine','YouTube did not return a playlist.')
                title=str(data.get('title') or source['label'])[:300]
                for index,entry in enumerate(data.get('entries') or (),start):
                    video_id=entry.get('id') if isinstance(entry,dict) else None
                    # Unavailable flat entries can still have a stable ID; queue
                    # them to record a clear per-video error and continue.
                    if not VIDEO.fullmatch(video_id or ''):
                        raise Failure('unavailable','A playlist entry has no reliable video ID. This playlist paused at that position; review it in YouTube.')
                    yield video_id,None,index,dict(playlist_id=source['target'].split('list=')[1],title=title,position=index)
        except Failure:raise
        except Exception as exc:raise classify(exc) from None

    def download_post(self,url,stage,options,progress):
        self.pace()
        stage=Path(stage)
        stage.mkdir(parents=True,exist_ok=True)
        def report(event):
            if event.get('status')=='downloading':
                if sum(p.stat().st_size for p in stage.iterdir() if p.is_file())>MAX_BYTES:
                    raise Failure('disk','This job reached the 10 GB staging limit. Choose a lower quality or clean its incomplete files.')
                progress(event.get('downloaded_bytes',0),event.get('total_bytes'))
        extra=dict(format=format_selector(options),merge_output_format='mp4',
                   outtmpl=str(stage/'media.%(ext)s').replace('%','%%').replace('%%(ext)s','%(ext)s'),
                   progress_hooks=[report],postprocessor_hooks=[lambda _:progress(0,None)],max_filesize=MAX_BYTES//3,
                   writesubtitles=options.get('subtitles',False),writeautomaticsub=False,
                   subtitleslangs=['en','en.*'],subtitlesformat='vtt',writethumbnail=options.get('thumbnail',False))
        try:
            with self.client(extra) as client:
                info=client.extract_info(url,download=False)
                if not info or not VIDEO.fullmatch(info.get('id','')):raise Failure('engine','No reliable YouTube video identity was returned.')
                if info.get('is_live') or info.get('live_status') in {'is_live','is_upcoming','post_live'}:
                    raise Failure('unavailable','Active, upcoming, or still-processing livestreams are unsupported. Try the completed ordinary video later.')
                if options.get('end') is not None and (not info.get('duration') or options['end']>info['duration']):
                    raise Failure('format','The segment end exceeds the known video duration, or its duration is unavailable.')
                selected=select_formats(info,options['mode'],options['quality'])
                if any((f.get('filesize') or 0)>MAX_BYTES//3 for f in selected):
                    raise Failure('disk','A selected stream exceeds the 3.3 GiB safety limit. Choose a lower quality or MP3. Segments may still need the full source.')
                prepare_audio_stage(stage,selected,options['mode'])
                # Do not serialize this dictionary: it contains signed media URLs.
                client.process_ie_result(info,download=True)
                source=stage/('media.mp4' if options['mode']=='mp4' else 'media.'+selected[0]['ext'])
                if not source.is_file() or source.stat().st_size<32:
                    raise Failure('incomplete','YouTube media or merge is incomplete. Partial files are retained for an explicit retry.')
                target=source
                if options['mode']=='mp3' or options.get('start') is not None:
                    progress(0,None)
                    target=stage/('converted.'+options['mode']);convert(source,target,options)
                # Verify the container matches the promised extension.
                from .library import media_type
                with target.open('rb') as stream:media_type('output.'+options['mode'],stream.read(32))
                metadata=dict(title=info.get('title'),channel=info.get('channel') or info.get('uploader'),
                    video_id=info['id'],duration=info.get('duration'),resolution=selected[0].get('height') if options['mode']=='mp4' else None,
                    mode=options['mode'],start=options.get('start'),end=options.get('end'),
                    segment_precision='keyframe boundaries' if options['mode']=='mp4' and options.get('start') is not None else None,
                    audio_language=selected[-1].get('language'),audio_original=True if original_audio(selected[-1]) else None,
                    audio_policy=AUDIO_POLICY)
                extras=[p for p in stage.iterdir() if p.name.startswith('media.') and p.suffix.lower() in {'.vtt','.jpg','.jpeg','.png','.webp'}]
                return target,extras,metadata
        except Failure:raise
        except Exception as exc:raise classify(exc) from None
