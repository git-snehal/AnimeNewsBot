"""The bot's log file and the /logs command: `/logs`, `/logs trailers`, `/logs errors`, `/logs 6h`, combinations
like `/logs trailers 24h`."""
import logging
import os
import re
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

from config import LOG_FILE

_TS = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_TRAILER = ("TRAILER", "YouTube search", "Trailer search", "Trailer download", "Page lookup", "yt-dlp",
            "YouTube is blocking", "working setup", "Portrait image")
MAX_ALL = 3 * 1024 * 1024  # `/logs` without filters: the newest 3 MB


def setup_logging():
    os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for h in list(root.handlers):
        root.removeHandler(h)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    fileh = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fileh.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(fileh)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("pyrogram").setLevel(logging.WARNING)


def parse_args(args: list):
    """-> (mode, hours). mode: all | trailers | errors. hours: None or a number ('6h', '30m', '2d')."""
    mode, hours = "all", None
    for a in args:
        a = a.lower()
        if a in ("trailer", "trailers", "t"):
            mode = "trailers"
        elif a in ("error", "errors", "e", "warnings"):
            mode = "errors"
        else:
            m = re.fullmatch(r"(\d+(?:\.\d+)?)([mhd]?)", a)
            if m:
                n = float(m.group(1))
                hours = {"m": n / 60, "d": n * 24}.get(m.group(2), n)
    return mode, hours


def _entries(path: str):
    """Log records with their continuation lines (tracebacks), oldest first, over the rotated files too."""
    files = [f"{path}.{i}" for i in (3, 2, 1)] + [path]
    cur = None
    for f in files:
        if not os.path.exists(f):
            continue
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                m = _TS.match(line)
                if m:
                    if cur:
                        yield cur
                    cur = [m.group(1), line]
                elif cur:
                    cur.append(line)
    if cur:
        yield cur


def collect(args: list, path: str = LOG_FILE):
    """-> (text, description, number of records)"""
    mode, hours = parse_args(args)
    cutoff = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S") if hours else None
    out, n = [], 0
    for ts, *lines in _entries(path):
        if cutoff and ts < cutoff:
            continue
        head = lines[0]
        if mode == "trailers" and not any(k in head for k in _TRAILER):
            continue
        if mode == "errors" and not (" WARNING " in head or " ERROR " in head or " CRITICAL " in head):
            continue
        out.append("".join(lines))
        n += 1
    text = "".join(out)
    if mode == "all" and not hours and len(text.encode()) > MAX_ALL:
        text = text.encode()[-MAX_ALL:].decode(errors="ignore")
        text = text[text.find("\n") + 1:]
    what = {"all": "full log", "trailers": "trailer lines", "errors": "warnings and errors"}[mode]
    if mode == "trailers":
        misses = sum(1 for line in out if "TRAILER MISS" in line.split("\n")[0])
        oks = sum(1 for line in out if "TRAILER OK" in line.split("\n")[0])
        what += f" ({oks} posted with video, {misses} without)"
    desc = what + (f", last {args_hours(hours)}" if hours else "")
    return text, desc, n


def args_hours(h: float) -> str:
    if h < 1:
        return f"{round(h * 60)} min"
    if h >= 48 and h % 24 == 0:
        return f"{int(h // 24)} days"
    return f"{h:g} h"
