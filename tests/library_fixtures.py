"""Small synthetic offline assets, generated with the already bundled FFmpeg."""
import os
from pathlib import Path
import subprocess
import zipfile
import imageio_ffmpeg


def generate(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    options=dict(capture_output=True,check=True)
    if os.name=='nt':options['creationflags']=subprocess.CREATE_NO_WINDOW
    ffmpeg=imageio_ffmpeg.get_ffmpeg_exe()
    image=root/'alice-IMAGE01-2026-10-25.jpg'
    blue=root/'blue.png'
    video=root/'bob-VIDEO01-2026-10-26.mp4'
    for color,path in [('0x835b46',image),('0x315f86',blue)]:
        subprocess.run([ffmpeg,'-nostdin','-v','error','-f','lavfi','-i',f'color=c={color}:s=240x180','-frames:v','1','-y',str(path)],**options)
    subprocess.run([ffmpeg,'-nostdin','-v','error','-f','lavfi','-i','testsrc2=size=240x180:rate=15','-f','lavfi','-i','sine=frequency=330:sample_rate=44100',
                    '-t','2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart','-y',str(video)],**options)
    carousel=root/'alice-CAROUSEL1-2026-10-27.zip'
    with zipfile.ZipFile(carousel,'w',compression=zipfile.ZIP_STORED) as z:
        # Deliberately unordered central-directory entries.
        z.write(blue,'003.png');z.write(image,'001.jpg');z.write(video,'002.mp4')
        z.writestr('caption.txt','https://www.instagram.com/p/CAROUSEL1/\n\nLocal carousel caption.\n')
    sidecar=image.with_suffix('.txt')
    sidecar.write_text('https://www.instagram.com/p/IMAGE01/\n\nA local image caption.\n',encoding='utf-8')
    return image,video,carousel
