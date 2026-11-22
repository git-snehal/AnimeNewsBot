import os
import re
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv()


def _env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name} (see .env.example)")
    return value


def _bool(name, default):
    return str(os.getenv(name, default)).strip().lower() in ("1", "true", "yes", "on")


# ---- Telegram ----
API_ID = int(_env("API_ID", required=True))
API_HASH = _env("API_HASH", required=True)
BOT_TOKEN = _env("BOT_TOKEN", required=True)
OWNER_ID = int(_env("OWNER_ID", required=True))

# Admins: the owner plus anyone listed in ADMIN_IDS (comma separated). Admins get the full "/" menu.
ADMIN_IDS = sorted({OWNER_ID, *[int(x) for x in re.split(r"[,\s]+", os.getenv("ADMIN_IDS", "")) if x.strip()]})

_channel = _env("CHANNEL_ID", required=True)
CHANNEL_ID = int(_channel) if _channel.lstrip("-").isdigit() else _channel

# ---- Post look ----
SIGNATURE = _env("SIGNATURE", "@OD3NS_ANIMES")
DISCUSSION_LINK = _env("DISCUSSION_LINK", "")
SHOW_SOURCE_BUTTON = _bool("SHOW_SOURCE_BUTTON", True)
SILENT_REGULAR_NEWS = _bool("SILENT_REGULAR_NEWS", True)
PIN_TRAILERS = _bool("PIN_TRAILERS", False)

# ---- Polling ----
POLL_INTERVAL = int(_env("POLL_INTERVAL", "600"))
ENTRIES_PER_FEED = 5
POST_DELAY = 5
MAX_RETRIES = 3
SEED_ON_FIRST_RUN = _bool("SEED_ON_FIRST_RUN", True)

# ---- Trailers (yt-dlp) ----
DOWNLOAD_TRAILERS = _bool("DOWNLOAD_TRAILERS", True)
PAGE_VIDEO_FALLBACK = _bool("PAGE_VIDEO_FALLBACK", True)
YT_SEARCH_FALLBACK = _bool("YT_SEARCH_FALLBACK", False)
MAX_VIDEO_MB = int(_env("MAX_VIDEO_MB", "150"))
MAX_VIDEO_SECONDS = int(_env("MAX_VIDEO_SECONDS", "600"))
MAX_VIDEO_HEIGHT = int(_env("MAX_VIDEO_HEIGHT", "720"))
COOKIES_PATH = _env("COOKIES_PATH", "cookies.txt")
# Lets yt-dlp download its official YouTube challenge-solver script from GitHub when the yt-dlp-ejs package is
# missing. Off by default (it runs downloaded JS); the safer fix is `pip install -U "yt-dlp[default]"`.
YTDLP_REMOTE_COMPONENTS = _bool("YTDLP_REMOTE_COMPONENTS", False)
DOWNLOAD_DIR = "downloads"

# ---- Feeds (verify these URLs still work) ----
RSS_FEEDS = {
    # A feed can be one URL or a list of alternatives: they are tried in order until one answers.
    "Anime News Network": [
        "https://www.animenewsnetwork.com/news/rss.xml?ann-edition=us",
        "https://www.animenewsnetwork.com/all/rss.xml?ann-edition=us",
    ],
    "Crunchyroll News": "https://www.crunchyroll.com/news/rss",
    "Anime Corner": "https://animecorner.me/feed/",
    "Anime Trending": "https://anitrendz.net/news/feed/",
}

# ---- Daily report ----
TIMEZONE = _env("TIMEZONE", "UTC")
TZ = ZoneInfo(TIMEZONE)  # fails fast on a typo
DAILY_STATS = _bool("DAILY_STATS", True)
STATS_TIME = _env("STATS_TIME", "23:59")  # HH:MM in TIMEZONE
_stats_chat = _env("STATS_CHAT_ID", "")
STATS_CHAT_ID = int(_stats_chat) if _stats_chat.strip().lstrip("-").isdigit() else None  # default: DM every admin
