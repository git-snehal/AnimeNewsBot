"""yt-dlp wrapper. Everything blocking runs in a worker thread."""
import asyncio
import logging
import os

from yt_dlp import YoutubeDL
from yt_dlp.utils import match_filter_func

from config import (
    COOKIES_PATH,
    DOWNLOAD_DIR,
    MAX_VIDEO_HEIGHT,
    MAX_VIDEO_MB,
    MAX_VIDEO_SECONDS,
)

log = logging.getLogger(__name__)


def _download_sync(url: str):
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    h = MAX_VIDEO_HEIGHT
    opts = {
        # H.264 + AAC in an mp4 plays everywhere in Telegram; fall back step by step
        "format": f"bv*[vcodec^=avc1][height<={h}]+ba[acodec^=mp4a]/b[ext=mp4][height<={h}]/b[height<={h}]/b",
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "retries": 3,
        "socket_timeout": 30,
        "max_filesize": MAX_VIDEO_MB * 1024 * 1024,
        "match_filter": match_filter_func(f"duration <= {MAX_VIDEO_SECONDS} & !is_live"),
    }
    if os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH

    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            return None  # rejected by the duration / live filter
        done = (info.get("requested_downloads") or [{}])[0]
        path = done.get("filepath") or ydl.prepare_filename(info)

    if not path or not os.path.exists(path):
        return None
    if os.path.getsize(path) > MAX_VIDEO_MB * 1024 * 1024:
        os.remove(path)
        return None
    return {
        "path": path,
        "duration": int(info.get("duration") or 0),
        "width": info.get("width"),
        "height": info.get("height"),
    }


def _search_sync(show: str, kind_name: str):
    words = [w for w in show.lower().split() if len(w) > 2]
    opts = {"quiet": True, "no_warnings": True, "extract_flat": True, "socket_timeout": 30}
    if os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH
    with YoutubeDL(opts) as ydl:
        res = ydl.extract_info(f"ytsearch5:{show} anime {kind_name} official", download=False)
    for e in (res or {}).get("entries", []):
        title = (e.get("title") or "").lower()
        dur = e.get("duration") or 0
        if words and all(w in title for w in words) and dur <= MAX_VIDEO_SECONDS and "reaction" not in title:
            return f"https://www.youtube.com/watch?v={e['id']}"
    return None


async def download_trailer(url: str):
    try:
        return await asyncio.wait_for(asyncio.to_thread(_download_sync, url), timeout=600)
    except Exception as e:
        log.warning("Trailer download failed for %s: %s", url, e)
        return None


async def search_trailer(show: str, kind_name: str):
    try:
        return await asyncio.wait_for(asyncio.to_thread(_search_sync, show, kind_name), timeout=60)
    except Exception as e:
        log.warning("Trailer search failed for %r: %s", show, e)
        return None


def cleanup(path: str):
    try:
        os.remove(path)
    except OSError:
        pass
