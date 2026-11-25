"""yt-dlp wrapper. Everything blocking runs in a worker thread."""
import asyncio
import importlib.util
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timedelta, timezone

import yt_dlp
from yt_dlp import YoutubeDL
from yt_dlp.utils import match_filter_func

from config import (
    COOKIES_PATH,
    DOWNLOAD_DIR,
    MAX_VIDEO_HEIGHT,
    MAX_VIDEO_MB,
    MAX_VIDEO_SECONDS,
    YTDLP_PROXY,
    YTDLP_REMOTE_COMPONENTS,
)

from helper.state import state

log = logging.getLogger(__name__)
PROBE_COOLDOWN = 1800  # seconds between automatic probes


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


class _Collector:
    """Receives yt-dlp's own warnings/errors so we can show *why* it found no formats."""

    def __init__(self):
        self.lines = []

    def debug(self, msg): pass

    def info(self, msg): pass

    def warning(self, msg): self._add(msg)

    def error(self, msg): self._add(msg)

    def _add(self, msg):
        m = re.sub(r"\s+", " ", str(msg)).strip()
        if m and "Deprecated Feature" not in m and m not in self.lines:
            self.lines.append(m[:300])


def _apply_cfg(opts: dict, cfg) -> dict:
    """Apply a yt-dlp setup (JS runtime subset / player client / cookies on-off). None = the one that worked last."""
    cfg = cfg if cfg is not None else state.yt_config
    if not cfg:
        return opts
    if cfg.get("runtimes"):
        found = js_runtimes()
        chosen = {n: found[n] for n in cfg["runtimes"] if n in found}
        if chosen:
            opts["js_runtimes"] = chosen
    if cfg.get("client"):
        opts["extractor_args"] = {"youtube": {"player_client": [cfg["client"]]}}
    if cfg.get("cookies") is False:
        opts.pop("cookiefile", None)
    return opts


def _base_opts(collector=None, cfg=None) -> dict:
    opts = {
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "retries": 3,
        "js_runtimes": js_runtimes(),
    }
    if collector is not None:
        opts["logger"] = collector
    if YTDLP_PROXY:
        opts["proxy"] = YTDLP_PROXY
    if YTDLP_REMOTE_COMPONENTS:
        opts["remote_components"] = ["ejs:github"]
    if os.path.exists(COOKIES_PATH):
        opts["cookiefile"] = COOKIES_PATH
    return _apply_cfg(opts, cfg)


def explain(error: str, formats_found=None) -> str:
    """Turn a yt-dlp error into something actionable."""
    e = (error or "").lower()
    if "sign in to confirm" in e or "not a bot" in e:
        return "YouTube wants a logged-in session from this server. Send a cookies.txt and reply /setcookies."
    if "challenge" in e and ("fail" in e or "solv" in e):
        return ("yt-dlp could not run its YouTube challenge solver. Check `deno --version` (needs 2.3+), "
                "or Node 22+, and that yt-dlp-ejs is installed.")
    if "po token" in e or "po_token" in e:
        return ("YouTube wants a 'PO token' for this client. See yt-dlp's PO Token guide "
                "(the bgutil-ytdlp-pot-provider plugin automates it).")
    if "requested format is not available" in e or formats_found == 0:
        return ("YouTube returned no downloadable formats. Usually: no JavaScript runtime (install Deno, or Node 22+), "
                "the EJS solver missing (pip install -U \"yt-dlp[default]\"), or a blocked server IP (add cookies).")
    if "private video" in e or "unavailable" in e:
        return "The video is private, removed or region-locked."
    if "ffmpeg" in e or "ffprobe" in e:
        return "ffmpeg is missing or not on PATH."
    return ""


def _download_sync(url: str, cfg=None):
    """Returns a dict, or None when the duration/live filter rejected the video. Raises on real errors."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    notes = _Collector()
    opts = _base_opts(notes, cfg) | {
        "format": _format_selector(),
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(id)s.%(ext)s"),
        "noplaylist": True,
        "max_filesize": MAX_VIDEO_MB * 1024 * 1024,
        "match_filter": match_filter_func(f"duration <= {MAX_VIDEO_SECONDS} & !is_live"),
    }
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            if not info:
                return None
            done = (info.get("requested_downloads") or [{}])[0]
            path = done.get("filepath") or ydl.prepare_filename(info)
    except Exception:
        if notes.lines:
            log.warning("yt-dlp said: %s", " | ".join(notes.lines[:4]))
        raise

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
    except Exception as e:
        return f"? ({type(e).__name__})"


def cookie_summary(path: str = None) -> dict:
    """Is cookies.txt usable? Reports counts and whether a (non-expired) Google login is inside. Never shows values."""
    path = path or COOKIES_PATH
    if not os.path.exists(path):
        return {"exists": False}
    login_names = {"SID", "__Secure-1PSID", "__Secure-3PSID", "LOGIN_INFO", "SAPISID", "__Secure-1PAPISID",
                   "__Secure-3PAPISID", "HSID", "SSID"}
    total = google = 0
    logged_in, soonest, now = False, None, time.time()
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if line.startswith("#HttpOnly_"):
                    line = line[len("#HttpOnly_"):]
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 7:
                    continue
                total += 1
                if "youtube.com" in parts[0] or "google.com" in parts[0]:
                    google += 1
                    if parts[5] in login_names:
                        try:
                            exp = int(float(parts[4]))
                        except ValueError:
                            exp = 0
                        if exp == 0 or exp > now:
                            logged_in = True
                            if exp:
                                soonest = exp if soonest is None else min(soonest, exp)
    except OSError:
        return {"exists": True, "cookies": 0, "google": 0, "logged_in": False, "days": None}
    return {"exists": True, "cookies": total, "google": google, "logged_in": logged_in,
            "days": int((soonest - now) / 86400) if soonest else None}


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
        "cookie_info": cookie_summary(),
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
    for name, ver in env["runtimes"].items():
        if (ver or "").startswith("?"):
            out.append(f"Could not read {name}'s version {ver}. Run `{name} --version`: yt-dlp ignores Deno older than 2.3 "
                       "and Node older than 22, and then YouTube returns no formats.")
    if not env["ejs"] and not env["remote_components"]:
        out.append('EJS solver not installed: pip install -U "yt-dlp[default]"')
    if not env["ffmpeg"]:
        out.append("ffmpeg not found.")
    return out


def _diagnose_sync(url: str, cfg=None) -> dict:
    """Metadata-only check: can yt-dlp see real, downloadable formats for this video?"""
    notes = _Collector()
    opts = _base_opts(notes, cfg) | {"skip_download": True, "ignore_no_formats_error": True, "noplaylist": True}
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:
        return {"ok": False, "error": str(e), "n_formats": 0, "notes": notes.lines[:5]}
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
        "notes": notes.lines[:5],
    }


_ORDINALS = {"second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6}
_SEASON_NUM = re.compile(r"season\s*(\d+)|(\d+)(?:st|nd|rd|th)\s+season|\bs(\d{1,2})\b|\b(second|third|fourth|fifth|sixth)\s+season", re.I)
_BAD_TITLE = re.compile(r"\b(reaction|react|reacts|review|explained|breakdown|analysis|amv|gameplay|compilation|recap)\b")
SEARCH_MAX_AGE_DAYS = 21


def season_numbers(text: str) -> set:
    out = set()
    for a, b, c, word in _SEASON_NUM.findall(text or ""):
        if word:
            out.add(_ORDINALS[word.lower()])
        else:
            out.add(int(a or b or c))
    return out


def _search_sync(show: str, kind_name: str, article_title: str = "", published=None):
    """Last-resort YouTube search. Deliberately strict: no video beats the wrong video.

    A result is only accepted when its title contains every word of the show name, it is not a reaction/review/etc.,
    its length fits, it doesn't name a different season than the article, and it was uploaded recently.
    Returns {"url", "title", "channel"} or None.
    """
    words = [w for w in re.findall(r"[a-z0-9]+", show.lower()) if len(w) > 2]
    if not words:
        return None
    want = season_numbers(article_title)
    max_len = min(MAX_VIDEO_SECONDS, 600)

    with YoutubeDL(_base_opts() | {"extract_flat": True}) as ydl:
        res = ydl.extract_info(f"ytsearch8:{show} {kind_name} anime", download=False)

    cands, why, entries = [], {}, (res or {}).get("entries") or []

    def reject(reason):
        why[reason] = why.get(reason, 0) + 1

    for e in entries:
        title = e.get("title") or ""
        norm = " " + re.sub(r"[^a-z0-9]+", " ", title.lower()) + " "
        dur = e.get("duration") or 0
        if not e.get("id") or not all(w in norm for w in words):
            reject("title lacks the show name")
            continue
        if _BAD_TITLE.search(norm):
            reject("reaction/review/etc.")
            continue
        if dur and (dur > max_len or dur < 5):
            reject("wrong length")
            continue
        got = season_numbers(title)
        if (want and got and not (want & got)) or (not want and any(n >= 2 for n in got)):
            reject("different season")  # e.g. the article is about season 2, this is a season 1 video
            continue
        cands.append({"url": f"https://www.youtube.com/watch?v={e['id']}", "title": title})

    ref = (published or datetime.now(timezone.utc)).date()
    for cand in cands[:3]:
        try:
            opts = _base_opts() | {"skip_download": True, "ignore_no_formats_error": True, "noplaylist": True}
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(cand["url"], download=False)
        except Exception:
            reject("could not read the video")
            continue
        up = info.get("upload_date")
        if up:
            try:
                if datetime.strptime(up, "%Y%m%d").date() < ref - timedelta(days=SEARCH_MAX_AGE_DAYS):
                    reject("uploaded too long ago")  # probably a previous trailer of a show that just got news
                    continue
            except ValueError:
                pass
        cand["channel"] = info.get("channel") or info.get("uploader")
        return cand

    log.info("YouTube search found nothing for %r: %d results, rejected: %s", show, len(entries),
             ", ".join(f"{n}x {r}" for r, n in why.items()) or "none")
    return None


def probe_configs() -> list:
    """The yt-dlp setups worth trying when YouTube returns no formats."""
    runtimes = list(js_runtimes().keys())
    cfgs = [{"name": "default", "runtimes": None, "client": None, "cookies": True}]
    if len(runtimes) > 1:
        cfgs += [{"name": f"only {r}", "runtimes": [r], "client": None, "cookies": True} for r in runtimes]
    if os.path.exists(COOKIES_PATH):
        cfgs.append({"name": "default, without cookies", "runtimes": None, "client": None, "cookies": False})
    cfgs += [{"name": f"{c} client", "runtimes": None, "client": c, "cookies": True} for c in ("tv", "mweb", "web_safari")]
    cfgs.append({"name": "android_vr client (no cookies)", "runtimes": None, "client": "android_vr", "cookies": False})
    return cfgs


def _probe_sync(url: str, stop_on_success: bool = False) -> list:
    out = []
    for cfg in probe_configs():
        d = _diagnose_sync(url, cfg)
        out.append({"cfg": cfg, "ok": d["ok"], "n_formats": d["n_formats"], "best_height": d.get("best_height"),
                    "error": d["error"], "notes": d.get("notes", [])})
        if d["ok"] and stop_on_success:
            break
    return out


async def probe_video(url: str, stop_on_success: bool = False) -> list:
    try:
        return await asyncio.wait_for(asyncio.to_thread(_probe_sync, url, stop_on_success), timeout=300)
    except Exception as e:
        log.warning("YouTube probe failed: %s", e)
        return []


def _is_blocking_error(err) -> bool:
    e = (err or "").lower()
    return "requested format is not available" in e or "sign in to confirm" in e or "not a bot" in e


async def _try_download(url: str):
    try:
        info = await asyncio.wait_for(asyncio.to_thread(_download_sync, url), timeout=600)
        return info, (None if info else "skipped: video too long, too big or live")
    except Exception as e:
        return None, str(e)


async def download_trailer_verbose(url: str):
    """-> (info_dict | None, error_text | None). When YouTube blocks us, find a setup that works and retry once."""
    info, err = await _try_download(url)
    if info or not _is_blocking_error(err):
        return info, err
    if time.time() - state.last_probe_at < PROBE_COOLDOWN:
        return info, err
    state.last_probe_at = time.time()
    log.info("YouTube is blocking this download. Trying other yt-dlp setups (JS runtime / client / cookies)...")
    results = await probe_video(url, stop_on_success=True)
    if results and results[-1]["ok"]:
        cfg = results[-1]["cfg"]
        state.yt_config = None if cfg["name"] == "default" else cfg
        log.info("Found a working setup: %s. Retrying the download.", cfg["name"])
        info, err = await _try_download(url)
    else:
        log.warning("No yt-dlp setup got formats from YouTube. Your server's address is probably blocked: "
                    "set YTDLP_PROXY in config.env, or run /ytprobe for details.")
    return info, err


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


async def search_trailer(show: str, kind_name: str, article_title: str = "", published=None):
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_search_sync, show, kind_name, article_title, published), timeout=90)
    except Exception as e:
        log.warning("Trailer search failed for %r: %s", show, e)
        return None


def cleanup(path: str):
    try:
        os.remove(path)
    except OSError:
        pass
