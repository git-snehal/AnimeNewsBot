import os
import re
from yt_dlp import YoutubeDL

def extract_youtube_url(text: str) -> str:
    if not text:
        return None
    match = re.search(r'(https?://(?:www\\.)?(?:youtube\\.com/watch\\?v=|youtu\\.be/)[\\w-]+)', text)
    if match:
        return match.group(1)
    return None

def download_trailer(url: str) -> str:
    if not url:
        return None
        
    url_match = re.search(r'https?://[^\s<>"]+', url)
    if url_match:
        clean_url = url_match.group(0).rstrip(')]}>.,')
    else:
        clean_url = url.strip()

    output_template = "downloads/%(id)s.%(ext)s"
    os.makedirs("downloads", exist_ok=True)
    
    ydl_opts = {
        'format': 'best/bestaudio+bestvideo',
        'outtmpl': output_template,
        'noplaylist': True,
        'quiet': True,
        'no_warnings': True,
    }
    
    if os.path.exists("cookies.txt"):
        ydl_opts['cookiefile'] = "cookies.txt"
        
    try:
        with YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(clean_url, download=True)
            filename = ydl.prepare_filename(info)
            if os.path.exists(filename):
                return filename
    except Exception as e:
        print(f"yt-dlp download error: {e}")
        
    return None