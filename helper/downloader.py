"""yt-dlp wrapper. Everything blocking runs in a worker thread."""
import asyncio
import contextlib
import importlib.metadata
import platform
import importlib.util
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yt_dlp
from yt_dlp import YoutubeDL
from yt_dlp.utils import match_filter_func

from config import (
    COOKIES_PATH,
    DOWNLOAD_DIR,
    MAX_VIDEO_HEIGHT,
    MAX_VIDEO_MB,
    MAX_VIDEO_SECONDS,
    YOUTUBE_POT_ENABLED,
    YOUTUBE_POT_URL,
    YTDLP_JS_RUNTIME_PATH,
    YTDLP_PROXY,
    YTDLP_REMOTE_COMPONENTS,
)

from helper.state import state

log = logging.getLogger(__name__)
PROBE_COOLDOWN = 1800  # seconds between automatic probes
CFG_FILE = os.path.join("database", "yt_config.json")  # the working setup survives restarts


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


_MIN_VERSION = {"deno": (2, 3), "node": (22, 0), "bun": (1, 2)}   # what yt-dlp accepts
_RT_CACHE = {}


def _check_runtime(name: str, path: str) -> dict:
    """Does this runtime actually run, and is it new enough for yt-dlp?"""
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=15)
    except Exception as e:
        return {"ok": False, "version": f"? ({type(e).__name__})", "reason": f"cannot be run ({type(e).__name__}: {str(e)[:60]})"}
    first = ((r.stdout or r.stderr or "").strip().splitlines() or [""])[0]
    m = re.search(r"(\d+)\.(\d+)", first)
    if r.returncode != 0 or not m:
        return {"ok": False, "version": "?", "reason": f"`{name} --version` gave exit code {r.returncode}: {first[:80] or 'no output'}"}
    need = _MIN_VERSION.get(name)
    if need and (int(m.group(1)), int(m.group(2))) < need:
        return {"ok": False, "version": first[:40], "reason": f"version {first[:30]} is too old (needs {need[0]}.{need[1]}+)"}
    return {"ok": True, "version": first[:40], "reason": ""}


def runtime_status(name: str, path: str) -> dict:
    key = (name, path)
    if key not in _RT_CACHE:
        _RT_CACHE[key] = _check_runtime(name, path)
    return _RT_CACHE[key]


_RT_BINARIES = {"deno": ("deno",), "node": ("node", "nodejs"), "bun": ("bun",), "quickjs": ("qjs", "quickjs")}


def _runtime_dirs() -> list:
    """Folders worth looking in. A bot started by systemd/pm2/docker often has a shorter PATH than your shell, so look
    next to the Python executable first: `pip install deno` puts the binary there (inside the virtualenv)."""
    home = Path.home()
    return [Path(sys.executable).parent, home / ".deno" / "bin", home / ".bun" / "bin", Path("/usr/local/bin"),
            Path("/usr/bin"), Path("/opt/homebrew/bin"), Path("/root/.deno/bin")]


def _runtime_candidates(name: str) -> list:
    names = _RT_BINARIES[name]
    out = []
    if YTDLP_JS_RUNTIME_PATH:
        p = Path(YTDLP_JS_RUNTIME_PATH).expanduser()
        if p.exists() and p.name.lower().replace(".exe", "") in names:
            out.append(str(p))
    dirs = _runtime_dirs()
    for d in dirs[:1]:                                  # the virtualenv's own folder wins
        out += [str(d / b) for b in names if (d / b).is_file() and os.access(d / b, os.X_OK)]
    out += [w for w in (shutil.which(b) for b in names) if w]
    for d in dirs[1:]:
        out += [str(d / b) for b in names if (d / b).is_file() and os.access(d / b, os.X_OK)]
    seen, unique = set(), []
    for c in out:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def _found_runtimes() -> dict:
    """{name: {"path": ...}}: for each runtime, the first copy that really works (else the first one found)."""
    found = {}
    for name in _RT_BINARIES:
        candidates = _runtime_candidates(name)
        if not candidates:
            continue
        pick = next((c for c in candidates if name not in _MIN_VERSION or runtime_status(name, c)["ok"]), candidates[0])
        found[name] = {"path": pick}
    return found


def _ejs_installed() -> bool:
    return importlib.util.find_spec("yt_dlp_ejs") is not None


def _remote_components() -> list:
    mode = (YTDLP_REMOTE_COMPONENTS or "").strip().lower()
    if mode in ("off", "none", "false", "0", ""):
        return []
    if mode in ("auto", "true", "1"):
        # the solver script normally ships in the yt-dlp-ejs package; fetch it from yt-dlp's GitHub only if missing
        return [] if _ejs_installed() else ["ejs:github"]
    return [part.strip() for part in mode.split(",") if part.strip()]


def js_runtimes() -> dict:
    """YouTube needs a JavaScript runtime. Hand yt-dlp every one that really works (it only enables Deno by default).
    A broken or too-old one is left out, so it can't be picked; if none works we pass what we found."""
    found = _found_runtimes()
    usable = {n: c for n, c in found.items() if n not in _MIN_VERSION or runtime_status(n, c["path"])["ok"]}
    return usable or found or {"deno": {}}


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


_POT_CACHE = {"at": 0.0, "ok": False}


def _pkg_version(name: str):
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def pot_plugin_version():
    """Version of the yt-dlp plugin that talks to the PO-token provider, or None when it isn't installed."""
    try:
        return importlib.metadata.version("bgutil-ytdlp-pot-provider")
    except importlib.metadata.PackageNotFoundError:
        return None


def pot_reachable() -> bool:
    """Is the PO-token provider answering? (any HTTP answer counts, even a 404; checked at most every 30 s)"""
    if not YOUTUBE_POT_ENABLED:
        return False
    now = time.time()
    if now - _POT_CACHE["at"] < 30:
        return _POT_CACHE["ok"]
    try:
        urllib.request.urlopen(YOUTUBE_POT_URL, timeout=3).close()
        ok = True
    except urllib.error.HTTPError:
        ok = True
    except Exception:
        ok = False
    _POT_CACHE.update(at=now, ok=ok)
    return ok


def _load_cfg():
    try:
        with open(CFG_FILE, encoding="utf-8") as f:
            cfg = json.load(f)
        return cfg if isinstance(cfg, dict) and "name" in cfg else None
    except (OSError, ValueError):
        return None


def set_active_cfg(cfg):
    """Use this yt-dlp setup from now on (None = the default one) and remember it across restarts."""
    state.yt_config = cfg
    try:
        if cfg:
            os.makedirs(os.path.dirname(CFG_FILE), exist_ok=True)
            with open(CFG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f)
        elif os.path.exists(CFG_FILE):
            os.remove(CFG_FILE)
    except OSError as e:
        log.warning("Could not save the yt-dlp setup: %s", e)


state.yt_config = _load_cfg()


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
        opts.setdefault("extractor_args", {})["youtube"] = {"player_client": [cfg["client"]]}
    if cfg.get("cookies") is False:
        opts.pop("cookiefile", None)
    if cfg.get("remote"):
        opts["remote_components"] = [cfg["remote"]]
    if cfg.get("pot") is False:
        opts.get("extractor_args", {}).pop("youtubepot-bgutilhttp", None)
    return opts


def _base_opts(collector=None, cfg=None, cookiefile=None) -> dict:
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
    remote = _remote_components()
    if remote:
        opts["remote_components"] = remote
    cookie = cookiefile or (COOKIES_PATH if os.path.exists(COOKIES_PATH) else None)
    if cookie:
        opts["cookiefile"] = cookie
    if pot_reachable():
        # the plugin's documented form (same as `--extractor-args youtubepot-bgutilhttp:base_url=...`)
        opts.setdefault("extractor_args", {})["youtubepot-bgutilhttp"] = {"base_url": [YOUTUBE_POT_URL]}
    return _apply_cfg(opts, cfg)


@contextlib.contextmanager
def _cookie_copy():
    """yt-dlp REWRITES the cookie file it is given (that is why your cookie count kept changing, 24 -> 22).
    Work on a throw-away copy so the original, and any parallel job, stays untouched."""
    if not os.path.exists(COOKIES_PATH):
        yield None
        return
    fd, tmp = tempfile.mkstemp(prefix="ytcookies_", suffix=".txt")
    os.close(fd)
    try:
        shutil.copyfile(COOKIES_PATH, tmp)
        yield tmp
    finally:
        try:
            os.remove(tmp)
        except OSError:
            pass


def explain(error: str, formats_found=None) -> str:
    """Turn a yt-dlp error into something actionable."""
    e = (error or "").lower()
    if "no supported javascript runtime" in e or "signature solving failed" in e or "supported java" in e:
        return ("yt-dlp has no WORKING JavaScript runtime, so it can't solve YouTube's challenge. Run "
                "`pip install -U deno yt-dlp-ejs yt-dlp` inside the bot's virtualenv (they're in requirements.txt) and restart. "
                "This usually also explains the 'sign in to confirm you're not a bot' message.")
    if "challenge" in e and ("fail" in e or "solv" in e):
        return ("yt-dlp could not run its YouTube challenge solver. Check `deno --version` (needs 2.3+), "
                "or Node 22+, and that yt-dlp-ejs is installed.")
    if "sign in to confirm" in e or "not a bot" in e:
        return "YouTube wants a logged-in session from this server. Send a cookies.txt and reply /setcookies."
    if "po token" in e or "po_token" in e:
        return ("YouTube wants a 'PO token' for this client. Run the PO-token provider (README: 'YouTube PO token provider') "
                "and install the plugin: pip install -U bgutil-ytdlp-pot-provider.")
    if "requested format is not available" in e or formats_found == 0:
        return ("YouTube returned no downloadable formats. Usually: no working JavaScript runtime (pip install -U deno), "
                "the EJS solver missing (pip install -U \"yt-dlp[default]\"), or a blocked server IP (add cookies / a proxy).")
    if "private video" in e or "unavailable" in e:
        return "The video is private, removed or region-locked."
    if "ffmpeg" in e or "ffprobe" in e:
        return "ffmpeg is missing or not on PATH."
    return ""


def _download_sync_impl(url: str, cfg=None, cookiefile=None):
    """Returns a dict, or None when the duration/live filter rejected the video. Raises on real errors."""
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    notes = _Collector()
    opts = _base_opts(notes, cfg, cookiefile) | {
        "format": _format_selector(),
        "merge_output_format": "mp4",
        "outtmpl": os.path.join(DOWNLOAD_DIR, f"%(id)s_{int(time.time() * 1000) % 10**8}.%(ext)s"),
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


def ytdlp_age_days():
    """How old is this yt-dlp build? (version looks like 2026.10.21 or 2026.10.21.202512)"""
    m = re.match(r"(\d{4})\.(\d{1,2})\.(\d{1,2})", yt_dlp.version.__version__)
    if not m:
        return None
    try:
        built = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
    except ValueError:
        return None
    return (datetime.now(timezone.utc) - built).days


def environment_info() -> dict:
    runtimes, issues, usable = {}, [], False
    for name, cfg in _found_runtimes().items():
        st = runtime_status(name, cfg["path"]) if name in _MIN_VERSION else {"ok": True, "version": "found", "reason": ""}
        runtimes[name] = st["version"]
        usable = usable or st["ok"]
        if not st["ok"]:
            issues.append(f"{name}: {st['reason']}")
    return {
        "ytdlp": yt_dlp.version.__version__,
        "ytdlp_age_days": ytdlp_age_days(),
        "python": platform.python_version(),
        "python_ok": sys.version_info >= (3, 11),
        "ejs_version": _pkg_version("yt-dlp-ejs"),
        "ffmpeg": bool(shutil.which("ffmpeg")),
        "runtimes": runtimes,
        "runtime_issues": issues,
        "runtime_ok": usable,
        "ejs": importlib.util.find_spec("yt_dlp_ejs") is not None,
        "remote_components": bool(_remote_components()),
        "runtime_paths": {n: c["path"] for n, c in _found_runtimes().items()},
        "cookies": os.path.exists(COOKIES_PATH),
        "cookie_info": cookie_summary(),
        "pot": {"enabled": YOUTUBE_POT_ENABLED, "url": YOUTUBE_POT_URL, "plugin": pot_plugin_version(),
                "reachable": pot_reachable()},
    }


def runtime_warnings(env: dict) -> list:
    out = []
    if not env.get("python_ok", True):
        out.append(f"Python {env.get('python')} is deprecated by yt-dlp and the newest yt-dlp / solver need 3.11+. "
                   "Use Python 3.11 or newer (3.12 recommended) for the bot's virtualenv.")
    age = env.get("ytdlp_age_days")
    if age is not None and age > 14:
        out.append(f"yt-dlp is {age} days old. YouTube changes its player weekly, so old versions stop getting formats. "
                   "Run /updateytdlp (or set YTDLP_AUTO_UPDATE=true).")
    pot = env.get("pot") or {}
    pot_ready = bool(pot.get("enabled") and pot.get("plugin") and pot.get("reachable"))
    if pot.get("enabled") and not pot.get("plugin"):
        out.append("PO-token plugin not installed: pip install -U bgutil-ytdlp-pot-provider (in the bot's virtualenv).")
    elif pot.get("enabled") and not pot.get("reachable"):
        out.append(f"PO-token provider not reachable at {pot.get('url')}. Start it: docker run -d --name bgutil-provider "
                   "-p 4416:4416 --init --restart unless-stopped brainicism/bgutil-ytdlp-pot-provider")
    if not env["runtimes"] and not pot_ready:
        out.append("No JavaScript runtime found. Fix: pip install -U deno yt-dlp-ejs (inside the bot's virtualenv; both are in "
                   "requirements.txt), then restart the bot.")
    for issue in env.get("runtime_issues", []):
        out.append(f"JavaScript runtime problem, {issue}. yt-dlp ignores it, so YouTube returns no formats.")
    if not env["ejs"] and not env["remote_components"]:
        out.append('EJS solver not installed: pip install -U "yt-dlp[default]"')
    if not env["ffmpeg"]:
        out.append("ffmpeg not found.")
    return out


def _diagnose_sync_impl(url: str, cfg=None, cookiefile=None) -> dict:
    """Metadata-only check: can yt-dlp see real, downloadable formats for this video?"""
    notes = _Collector()
    opts = _base_opts(notes, cfg, cookiefile) | {"skip_download": True, "ignore_no_formats_error": True, "noplaylist": True}
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
_BAD_TITLE = re.compile(
    r"\b(reaction|react|reacts|review|explained|breakdown|analysis|amv|gameplay|compilation|recap"
    r"|episodes?\s*\d+|ep\s*\d+|upcoming episodes|next episode|full episode)\b")
_FAN_MADE = re.compile(r"\b(fan\s*made|fanmade|unofficial|concept|fan\s*edit|parody|remake)\b")
# a trailer search must return a trailer / teaser / PV, not an episode preview or a clip
_VIDEO_KIND = re.compile(r"\b(trailer|teaser|pvs?|promo|promotional|announcement|commercial|cm|key visual|official video)\b")
SEARCH_MAX_AGE_DAYS = 21


def season_numbers(text: str) -> set:
    out = set()
    for a, b, c, word in _SEASON_NUM.findall(text or ""):
        if word:
            out.add(_ORDINALS[word.lower()])
        else:
            out.add(int(a or b or c))
    return out


_NAME_STOP = {"the", "and", "season", "anime", "movie", "film", "part", "of", "no", "wa", "ga", "to", "ni", "tv"}


def _name_words(name: str) -> list:
    return [w for w in re.findall(r"[a-z0-9]+", (name or "").lower().replace("'", "")) if len(w) > 1 and w not in _NAME_STOP]


def title_has_name(title: str, all_names: list) -> bool:
    """Does a video title name the show? Any of its names counts (English, romaji, synonyms). Short names (1-2 words)
    must appear completely; longer ones need 3 of every 4 words ('Mobile Suit Gundam SEED FREEDOM ZERO' matches
    'Gundam SEED FREEDOM ZERO | Official Trailer')."""
    norm = " " + re.sub(r"[^a-z0-9]+", " ", (title or "").lower().replace("'", "")) + " "
    words = set(norm.split())
    for name in all_names:
        ws = _name_words(name)
        if not ws:
            continue
        hit = sum(1 for w in ws if w in words)
        if (len(ws) <= 2 and hit == len(ws)) or (len(ws) > 2 and hit / len(ws) >= 0.75):
            return True
    return False


def _search_sync_impl(show: str, kind_name: str, article_title: str = "", published=None, cookiefile=None,
                      alt_names=None):
    """Last-resort YouTube search. Strict on WHAT the video is, flexible on how the show is NAMED.

    Accepted only when the title names the show (any of its names), it is a trailer / teaser / PV (not a reaction,
    review, episode preview or fan edit), its length fits, it doesn't name a different season than the article, and
    it was uploaded recently. Returns ({"url", "title", "channel"} or None, "why nothing matched").
    """
    all_names = [n for n in [show, *(alt_names or [])] if n]
    if not any(_name_words(n) for n in all_names):
        return None, "no usable show name"
    want = season_numbers(article_title)
    max_len = min(MAX_VIDEO_SECONDS, 600)
    why, cands, seen, total = {}, [], set(), 0

    def reject(reason):
        why[reason] = why.get(reason, 0) + 1

    queries = [f"{show} {kind_name}"]
    romaji = next((n for n in alt_names or [] if n and n.lower() != show.lower()), None)
    if romaji:
        queries.append(f"{romaji} {kind_name}")
    for q in queries:
        with YoutubeDL(_base_opts(cookiefile=cookiefile) | {"extract_flat": True}) as ydl:
            res = ydl.extract_info(f"ytsearch10:{q}", download=False)
        entries = (res or {}).get("entries") or []
        total += len(entries)
        for e in entries:
            vid = e.get("id")
            if not vid or vid in seen:
                continue
            seen.add(vid)
            title = e.get("title") or ""
            norm = " " + re.sub(r"[^a-z0-9]+", " ", title.lower()) + " "
            dur = e.get("duration") or 0
            if not title_has_name(title, all_names):
                reject("name mismatch")
                continue
            if _BAD_TITLE.search(norm):
                reject("reaction/review/episode")
                continue
            if _FAN_MADE.search(norm):
                reject("fan-made")
                continue
            if not _VIDEO_KIND.search(norm):
                reject("not a trailer/PV")
                continue
            if dur and (dur > max_len or dur < 5):
                reject("wrong length")
                continue
            got = season_numbers(title)
            if (want and got and not (want & got)) or (not want and any(n >= 2 for n in got)):
                reject("different season")
                continue
            score = 2 * bool(re.search(r"\bofficial\b", norm)) + bool(re.search(
                r"crunchyroll|aniplex|toho|netflix|kadokawa|muse|official|anime", (e.get("channel") or e.get("uploader") or "").lower()))
            cands.append({"url": f"https://www.youtube.com/watch?v={vid}", "title": title, "score": score})
        if cands:
            break

    cands.sort(key=lambda c: -c["score"])
    ref = (published or datetime.now(timezone.utc)).date()
    for cand in cands[:3]:
        try:
            opts = _base_opts(cookiefile=cookiefile) | {"skip_download": True, "ignore_no_formats_error": True, "noplaylist": True}
            with YoutubeDL(opts) as ydl:
                info = ydl.extract_info(cand["url"], download=False)
        except Exception:
            reject("could not read the video")
            continue
        up = info.get("upload_date")
        if up:
            try:
                if datetime.strptime(up, "%Y%m%d").date() < ref - timedelta(days=SEARCH_MAX_AGE_DAYS):
                    reject("too old")
                    continue
            except ValueError:
                pass
        cand["channel"] = info.get("channel") or info.get("uploader")
        return cand, ""

    summary = f"{total} results" + (": " + ", ".join(f"{n}x {r}" for r, n in why.items()) if why else "")
    log.info("YouTube search found nothing for %r (names: %s): %s", show, ", ".join(all_names[:4]), summary)
    return None, summary


def probe_configs() -> list:
    """The yt-dlp setups worth trying when YouTube returns no formats."""
    runtimes = list(js_runtimes().keys())
    cfgs = [{"name": "default", "runtimes": None, "client": None, "cookies": True}]
    if len(runtimes) > 1:
        cfgs += [{"name": f"only {r}", "runtimes": [r], "client": None, "cookies": True} for r in runtimes]
    if _ejs_installed() and not _remote_components():
        # the newest solver script straight from yt-dlp's GitHub: fixes "n challenge solving failed" after a player change
        cfgs.append({"name": "default + newest solver script (GitHub)", "runtimes": None, "client": None, "cookies": True,
                     "remote": "ejs:github"})
    if pot_reachable():
        cfgs.append({"name": "default, without PO token", "runtimes": None, "client": None, "cookies": True, "pot": False})
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


def _download_sync(url: str, cfg=None):
    with _cookie_copy() as ck:
        return _download_sync_impl(url, cfg, ck)


def _diagnose_sync(url: str, cfg=None) -> dict:
    with _cookie_copy() as ck:
        return _diagnose_sync_impl(url, cfg, ck)


def _search_sync(show: str, kind_name: str, article_title: str = "", published=None, alt_names=None):
    with _cookie_copy() as ck:
        return _search_sync_impl(show, kind_name, article_title, published, ck, alt_names)


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
        set_active_cfg(None if cfg["name"] == "default" else cfg)
        log.info("Found a working setup: %s. Saved it and retrying the download.", cfg["name"])
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


async def search_trailer(show: str, kind_name: str, article_title: str = "", published=None, alt_names=None):
    """-> (match dict or None, why nothing matched)"""
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_search_sync, show, kind_name, article_title, published, alt_names), timeout=120)
    except Exception as e:
        log.warning("Trailer search failed for %r: %s", show, e)
        return None, f"search error: {type(e).__name__}"


def purge_downloads(max_age: int = 3600):
    """Leftovers from a crash or a failed download (.part files, fragments). Called at start."""
    if not os.path.isdir(DOWNLOAD_DIR):
        return 0
    n = 0
    for f in os.listdir(DOWNLOAD_DIR):
        path = os.path.join(DOWNLOAD_DIR, f)
        try:
            if os.path.isfile(path) and time.time() - os.path.getmtime(path) > max_age:
                os.remove(path)
                n += 1
        except OSError:
            pass
    return n


def cleanup(path: str):
    try:
        os.remove(path)
    except OSError:
        pass
