"""Bounded, offline input routing. Canonical URLs never retain tracking tokens."""
import math
import re
from urllib.parse import urlsplit, parse_qs
from .core import post_url

VIDEO = re.compile(r'[A-Za-z0-9_-]{11}')
PLAYLIST = re.compile(r'(?:[A-Za-z0-9_-]{10,100}|WL|LL|LM|RDMM)')


def route(value, choice=None):
    value = value.strip()
    if len(value) > 2048:
        raise ValueError('Link is too long')
    try:
        code, url = post_url(value)
        return dict(platform='instagram', kind='video', id=code, url=url)
    except ValueError:
        pass
    try:
        u = urlsplit(value if '://' in value else 'https://' + value)
        if u.scheme not in {'http', 'https'} or u.username or u.password or u.port:
            raise ValueError()
        if u.hostname not in {'youtube.com','www.youtube.com','m.youtube.com','music.youtube.com','youtu.be','www.youtu.be'}:
            raise ValueError()
        q = parse_qs(u.query)
        video = None
        if u.hostname.endswith('youtu.be'):
            video = u.path.strip('/')
        elif u.path == '/watch':
            video = q.get('v',[''])[0]
        elif re.fullmatch(r'/(?:shorts|live)/[^/]+/?',u.path):
            video = u.path.strip('/').split('/')[1]
        elif u.path != '/playlist':
            raise ValueError()
        playlist = q.get('list',[''])[0]
        if video is not None and not VIDEO.fullmatch(video):raise ValueError()
        if playlist and not PLAYLIST.fullmatch(playlist):raise ValueError()
        if video and playlist and choice not in {'video','playlist'}:
            raise ValueError('This link contains a video and playlist. Choose “This video” or “This playlist” in YouTube options.')
        if playlist and (not video or choice=='playlist'):
            return dict(platform='youtube',kind='playlist',id=playlist,url='https://www.youtube.com/playlist?list='+playlist)
        if video:
            return dict(platform='youtube',kind='video',id=video,url='https://www.youtube.com/watch?v='+video)
        raise ValueError()
    except ValueError as exc:
        if str(exc).startswith('This link'):raise
        raise ValueError('Use an Instagram post/Reel or a YouTube video, Short, or playlist link. Channels and redirects are unsupported.') from None


def timestamp(value):
    if value in (None,''):return None
    try:
        if len(str(value))>20:raise ValueError()
        parts = str(value).strip().split(':')
        if len(parts)>3 or any(not re.fullmatch(r'\d+(?:\.\d{1,3})?',p) for p in parts):raise ValueError()
        result = 0
        for part in parts:result=result*60+float(part)
        if not math.isfinite(result) or not 0<=result<=86400:raise ValueError()
        return round(result,3)
    except (ValueError,TypeError):
        raise ValueError('Use seconds or HH:MM:SS (up to 24 hours) for the segment range') from None


def youtube_options(body):
    mode=body.get('yt_mode','mp4');quality=str(body.get('yt_quality','best'))
    if mode not in {'mp4','mp3'} or quality not in {'best','1080','720','480','360'}:
        raise ValueError('Choose MP4 or MP3 and a supported quality')
    start,end=timestamp(body.get('yt_start')),timestamp(body.get('yt_end'))
    if (start is None)!=(end is None) or start is not None and start>=end:
        raise ValueError('A segment needs both start and end, with start before end')
    return dict(platform='youtube',mode=mode,quality=quality,start=start,end=end,
                subtitles=body.get('yt_subtitles') is True,thumbnail=body.get('yt_thumbnail') is True)


def identity(video_id, options):
    segment='full' if options.get('start') is None else f"{round(options['start']*1000)}-{round(options['end']*1000)}ms"
    return f"yt:{video_id}:{options.get('mode','mp4')}:{segment}"
