"""Keeping yt-dlp fresh. YouTube changes its player almost every week; an old yt-dlp / solver script then stops
getting video formats ("n challenge solving failed", "Only images are available")."""
import asyncio
import importlib.metadata as md
import logging
import os
import sys
import time

from config import YTDLP_AUTO_UPDATE, YTDLP_UPDATE_PRE

log = logging.getLogger(__name__)

STAMP_FILE = os.path.join("database", "ytdlp_update_stamp")
CHECK_EVERY = 20 * 3600
PACKAGES = ["yt-dlp[default,curl-cffi]", "yt-dlp-ejs", "bgutil-ytdlp-pot-provider"]   # deno is left alone on purpose


def versions() -> dict:
    out = {}
    for name in ("yt-dlp", "yt-dlp-ejs", "bgutil-ytdlp-pot-provider"):
        try:
            out[name] = md.version(name)
        except md.PackageNotFoundError:
            out[name] = None
    return out


async def run_update(pre: bool = None, timeout: int = 600, python: str = None) -> dict:
    """pip install -U yt-dlp & friends into the bot's own virtualenv. -> {ok, before, after, changed, log}"""
    pre = YTDLP_UPDATE_PRE if pre is None else pre
    before = versions()
    cmd = [python or sys.executable, "-m", "pip", "install", "-U", "--disable-pip-version-check"]
    cmd += (["--pre"] if pre else []) + PACKAGES
    try:
        proc = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return {"ok": False, "before": before, "after": versions(), "changed": False, "log": "pip timed out"}
    except Exception as e:
        return {"ok": False, "before": before, "after": before, "changed": False, "log": f"could not run pip: {e}"}
    after = versions()
    text = out.decode(errors="replace").strip()
    return {"ok": proc.returncode == 0, "before": before, "after": after, "changed": before != after,
            "log": "\n".join(text.splitlines()[-6:])}


def report(res: dict) -> str:
    lines = ["✅ <b>Update finished</b>" if res["ok"] else "❌ <b>Update failed</b>"]
    for name, old in res["before"].items():
        new = res["after"].get(name)
        if old != new:
            lines.append(f"• {name}: {old or 'not installed'} → <b>{new or '?'}</b>")
        elif old:
            lines.append(f"• {name}: {old} (already the newest)")
    if not res["ok"]:
        lines.append(f"<pre>{res['log'][-500:]}</pre>")
    return "\n".join(lines)


def _stamp_age() -> float:
    try:
        return time.time() - os.path.getmtime(STAMP_FILE)
    except OSError:
        return float("inf")


def _touch_stamp():
    try:
        os.makedirs(os.path.dirname(STAMP_FILE), exist_ok=True)
        with open(STAMP_FILE, "w") as f:
            f.write(str(int(time.time())))
    except OSError:
        pass


async def maybe_auto_update(**kw):
    """Update if enabled and the last check is older than ~a day. -> result dict, or None when skipped."""
    if not YTDLP_AUTO_UPDATE or _stamp_age() < CHECK_EVERY:
        return None
    _touch_stamp()
    res = await run_update(**kw)
    log.info("yt-dlp auto-update: ok=%s changed=%s %s -> %s", res["ok"], res["changed"], res["before"], res["after"])
    return res


def restart_process():
    """Replace this process with a fresh copy of itself (same Python, same arguments)."""
    os.execv(sys.executable, [sys.executable] + sys.argv)
