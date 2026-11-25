"""Shared runtime state so the commands (/status, /pause, /fetchnow) can talk to the news loop."""
import asyncio
from datetime import datetime, timezone


class BotState:
    def __init__(self):
        self.paused = False
        self.wake = asyncio.Event()  # set -> the news loop checks the feeds right now
        self.started_at = datetime.now(timezone.utc)
        self.last_cycle_at = None
        self.last_cycle_found = 0
        self.feed_pref = {}  # name -> (url, header profile index) that worked last time
        self.feed_blocked_until = {}  # name -> unix time before which a fully blocked feed isn't retried
        self.yt_config = None          # the yt-dlp setup that worked last (see downloader.probe_configs)
        self.last_probe_at = 0.0
        self.last_youtube_url = None
        self.feeds = {}  # name -> {"ok": bool, "entries": int, "error": str, "at": datetime}


state = BotState()
