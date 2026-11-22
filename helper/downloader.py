"""yt-dlp wrapper. Everything blocking runs in a worker thread."""
import asyncio
import importlib.util
import logging
import os
import re
import shutil
import subprocess

import yt_dlp
from yt_dlp import YoutubeDL
from yt_dlp.utils import match_filter_func

from config import (
    COOKIES_PATH,
    DOWNLOAD_DIR,
    MAX_VIDEO_HEIGHT,
    MAX_VIDEO_MB,
    MAX_VIDEO_SECONDS,
    YTDLP_REMOTE_COMPONENTS,
)

log = logging.getLogger(__name__)


def _format_selector() -> str:
    h = MAX_VIDEO_HEIGHT
    return (
        f"bv*[height<={h}][vcodec^=avc1]+ba[acodec^=mp4a]"   # ideal: H.264 + AAC (plays everywhere in Telegram)
        f"/bv*[height<={h}][ext=mp4]+ba[ext=m4a]"
        f"/b[height<={h}][ext=mp4]"
        f"/bv*[height<={h}]+ba"                              # anything that fits
        f"/b[height<={h}]"
        f"/b"                                                # last resort: best single file
    )


def js_runtimes() -> dict:
    """YouTube needs a JavaScript runtime. yt-dlp only enables Deno unless told otherwise, so enable every one we find."""
    found = {}
    for name, binary in (("deno", "deno"), ("node", "node"), ("bun", "bun"), ("quickjs", "qjs")):
        path = shutil.which(binary)
        if path:
            found[name] = {"path": path}
    return found or {"deno": {}}


def _base_opts() -> dict:
    opts = {
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "js_runtimes": js_runtimes(),
    }
    if YTDLP_REMOTE_COMPONENTS:
        opts["remote_components"] = ["ejs:github"]
    if os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH
    return opts


def explain(error: str, formats_found=None) -> str:
    """Turn a yt-dlp error into something actionable."""
    e = (error or "").lower()
    if "sign in to confirm" in e or "not a bot" in e:
        return "YouTube wants a logged-in session from this server. Send a cookies.txt and reply /setcookies."
    if "requested format is not available" in e or formats_found == 0:
        return ("YouTube returned no downloadable formats. Usually: no JavaScript runtime (install Deno, or Node 22+), "
                "the EJS solver missing (pip install -U \"yt-dlp[default]\"), or a blocked server IP (add cookies).")
    if "private video" in e or "unavailable" in e:
        return "The video is private, removed or region-locked."
    if "ffmpeg" in e or "ffprobe" in e:
        return "ffmpeg is missing or not on PATH."
    return ""


def _download_sync(url: str):
    """Returns a dict, or None when the duration/live filter rejected the video. Raises on real errors."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    opts = _base_opts() | {
        "format": _format_selector(),
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "max_filesize": MAX_VIDEO_MB * 1024 * 1024,
        "match_filter": match_filter_func(f"duration <= {MAX_VIDEO_SECONDS} & !is_live"),
    }
    with YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            return None
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
        "size_mb": round(os.path.getsize(path) / 1048576, 1),
    }


def _runtime_version(path: str) -> str:
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=5).stdout.strip()
        return out.splitlines()[0] if out else "?"
    except Exception:
        return "?"


def environment_info() -> dict:
    runtimes = {}
    for name, cfg in js_runtimes().items():
        path = cfg.get("path")
        runtimes[name] = _runtime_version(path) if path else "?"
    return {
        "ytdlp": yt_dlp.version.__version__,
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "runtimes": runtimes,
        "ejs": importlib.util.find_spec("yt_dlp_ejs") is not None,
        "remote_components": YTDLP_REMOTE_COMPONENTS,
        "cookies": os.path.exists(COOKIES_PATH),
    }


def runtime_warnings(env: dict) -> list:
    out = []
    if not env["runtimes"]:
        out.append("No JavaScript runtime found (need Deno 2.3+, Node 22+ or Bun 1.2.11+).")
    for name, ver in env["runtimes"].items():
        m = re.search(r"(\d+)\.(\d+)", ver or "")
        if m and name == "node" and int(m.group(1)) < 22:
            out.append(f"Node {ver} is too old for yt-dlp (needs 22+).")
        if m and name == "deno" and (int(m.group(1)), int(m.group(2))) < (2, 3):
            out.append(f"Deno {ver} is too old for yt-dlp (needs 2.3+).")
    if not env["ejs"] and not env["remote_components"]:
        out.append('EJS solver not installed: pip install -U "yt-dlp[default]"')
    if not env["ffmpeg"]:
        out.append("ffmpeg not found.")
    return out


def _diagnose_sync(url: str) -> dict:
    """Metadata-only check: can yt-dlp see real, downloadable formats for this video?"""
    opts = _base_opts() | {"skip_download": True, "ignore_no_formats_error": True, "noplaylist": True}
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return {"ok": False, "error": str(e), "n_formats": 0}
    fmts = [
        f for f in (info.get("formats") or [])
        if (f.get("vcodec") not in (None, "none") or f.get("acodec") not in (None, "none"))
        and f.get("ext") != "mhtml" and "storyboard" not in (f.get("format_note") or "")
    ]
    heights = [f["height"] for f in fmts if f.get("height")]
    return {
        "ok": bool(fmts),
        "error": "" if fmts else "no downloadable formats (only thumbnails/storyboards)",
        "n_formats": len(fmts),
        "best_height": max(heights) if heights else None,
        "title": info.get("title"),
        "duration": int(info.get("duration") or 0),
    }


def _search_sync(show: str, kind_name: str):
    words = [w for w in show.lower().split() if len(w) > 2]
    opts = _base_opts() | {"extract_flat": True}
    with YoutubeDL(opts) as ydl:
        res = ydl.extract_info(f"ytsearch5:{show} anime {kind_name} official", download=False)
    for e in (res or {}).get("entries", []):
        title = (e.get("title") or "").lower()
        dur = e.get("duration") or 0
        if words and all(w in title for w in words) and dur <= MAX_VIDEO_SECONDS and "reaction" not in title:
            return f"https://www.youtube.com/watch?v={e['id']}"
    return None


async def download_trailer_verbose(url: str):
    """-> (info_dict | None, error_text | None)"""
    try:
        info = await asyncio.wait_for(asyncio.to_thread(_download_sync, url), timeout=600)
        return info, (None if info else "skipped: video too long, too big or live")
    except Exception as e:
        return None, str(e)


async def download_trailer(url: str):
    info, err = await download_trailer_verbose(url)
    if err and not info:
        hint = explain(err)
        log.warning("Trailer download failed for %s: %s%s", url, err, f"\n   -> {hint}" if hint else "")
    return info


async def diagnose_video(url: str) -> dict:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_diagnose_sync, url), timeout=120)
    except Exception as e:
        return {"ok": False, "error": str(e), "n_formats": 0}


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
