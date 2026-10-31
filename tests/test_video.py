"""Real FFmpeg/yt-dlp pipeline with tiny generated media and in-memory HTTP."""
import io
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import imageio_ffmpeg
import yt_dlp
from yt_dlp.networking import Response
from instagram_local.engine import Engine, Media


class VideoTests(unittest.TestCase):
    def test_best_dash_video_and_audio_are_merged_without_reencoding(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
            options = dict(capture_output=True, check=True)
            import os
            if os.name == 'nt': options['creationflags'] = subprocess.CREATE_NO_WINDOW
            for size, name in [(64,'low.mp4'),(128,'high.mp4')]:
                subprocess.run([ffmpeg,'-nostdin','-loglevel','error','-f','lavfi','-i',f'color=blue:size={size}x{size}:rate=10','-t','0.3','-an','-c:v','libx264','-pix_fmt','yuv420p',str(root/name)],**options)
            subprocess.run([ffmpeg,'-nostdin','-loglevel','error','-f','lavfi','-i','sine=frequency=440:sample_rate=44100','-t','0.3','-vn','-c:a','aac',str(root/'audio.m4a')],**options)
            manifest='''<MPD xmlns="urn:mpeg:dash:schema:mpd:2011" type="static" mediaPresentationDuration="PT0.3S"><Period duration="PT0.3S"><AdaptationSet mimeType="video/mp4" contentType="video"><Representation id="low" bandwidth="1000" width="64" height="64" codecs="avc1.64000b"><BaseURL>https://fixture.cdninstagram.com/low.mp4</BaseURL></Representation><Representation id="high" bandwidth="5000" width="128" height="128" codecs="avc1.64000b"><BaseURL>https://fixture.cdninstagram.com/high.mp4</BaseURL></Representation></AdaptationSet><AdaptationSet mimeType="audio/mp4" contentType="audio"><Representation id="audio" bandwidth="64000" codecs="mp4a.40.2" audioSamplingRate="44100"><BaseURL>https://fixture.cdninstagram.com/audio.m4a</BaseURL></Representation></AdaptationSet></Period></MPD>'''
            requested=[]
            def fixture_open(ydl, request):
                url=request if isinstance(request,str) else request.url
                name=url.rsplit('/',1)[-1]; requested.append(name)
                data=(root/name).read_bytes()
                return Response(io.BytesIO(data),url,{'Content-Length':str(len(data)),'Content-Type':'video/mp4'})
            engine=Engine({},sleeper=lambda _:None)
            try:
                output=root/'result.part.mp4'
                with patch.object(yt_dlp.YoutubeDL,'urlopen',fixture_open):
                    engine.download(Media('fixture','https://fixture.cdninstagram.com/high.mp4','mp4',manifest),output,lambda done,total:None)
                self.assertTrue(output.is_file())
                self.assertIn('high.mp4',requested)
                self.assertIn('audio.m4a',requested)
                self.assertNotIn('low.mp4',requested)
                probe=subprocess.run([ffmpeg,'-hide_banner','-i',str(output)],capture_output=True,text=True,**({'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {}))
                self.assertIn('128x128',probe.stderr)
                self.assertIn('Audio: aac',probe.stderr)
            finally: engine.close()
