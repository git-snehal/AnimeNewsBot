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
SIGNATURE_PREFIX = _env("SIGNATURE_PREFIX", "╰ ")  # shown before the handle, inside the bold quote
DISCUSSION_LINK = _env("DISCUSSION_LINK", "")
SHOW_SOURCE_BUTTON = _bool("SHOW_SOURCE_BUTTON", True)
SILENT_REGULAR_NEWS = _bool("SILENT_REGULAR_NEWS", True)
PIN_TRAILERS = _bool("PIN_TRAILERS", False)

# ---- Polling ----
POLL_INTERVAL = int(_env("POLL_INTERVAL", "600"))
ENTRIES_PER_FEED = 10
POST_DELAY = 5
MAX_RETRIES = 3
SEED_ON_FIRST_RUN = _bool("SEED_ON_FIRST_RUN", True)

# ---- Trailers (yt-dlp) ----
DOWNLOAD_TRAILERS = _bool("DOWNLOAD_TRAILERS", True)
PAGE_VIDEO_FALLBACK = _bool("PAGE_VIDEO_FALLBACK", True)
YT_SEARCH_FALLBACK = _bool("YT_SEARCH_FALLBACK", True)
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
    # A feed is one URL, a list of alternative URLs (tried in order), or a dict with "urls" plus optional
    # "allow_paths" / "allow_categories" filters.
    "Anime News Network": {
        "urls": [
            "https://www.animenewsnetwork.com/all/rss.xml?ann-edition=in",
            "https://www.animenewsnetwork.com/news/rss.xml?ann-edition=in",
        ],
        # The "all" feed also carries episode reviews, advertorials, columns... Only these sections are news:
        "allow_paths": ["/news/", "/interest/", "/daily-briefs/"],
        # ...and skip items that are only about games, live-action shows, Korean comics or industry matters
        "allow_categories": ["Anime", "Manga", "Animation", "Music", "People", "Events", "Merch"],
    },
    "Crunchyroll News": [
        "https://cr-news-api-service.prd.crunchyrollsvc.com/v1/en-US/rss",  # their real feed lives on this API host
        "https://www.crunchyroll.com/news/rss",                              # this one only returns a web page
    ],
    "Anime Corner": "https://animecorner.me/feed/",
    "Anime Trending": "https://anitrendz.net/news/feed/",
}
# Feeds you don't want (e.g. one your server's IP is blocked from): DISABLED_FEEDS="Anime News Network"
_disabled = {x.strip().lower() for x in os.getenv("DISABLED_FEEDS", "").split(",") if x.strip()}
RSS_FEEDS = {k: v for k, v in RSS_FEEDS.items() if k.lower() not in _disabled}

# ---- Daily report ----
TIMEZONE = _env("TIMEZONE", "UTC")
TZ = ZoneInfo(TIMEZONE)  # fails fast on a typo
DAILY_STATS = _bool("DAILY_STATS", True)
STATS_TIME = _env("STATS_TIME", "23:59")  # HH:MM in TIMEZONE
_stats_chat = _env("STATS_CHAT_ID", "")
STATS_CHAT_ID = int(_stats_chat) if _stats_chat.strip().lstrip("-").isdigit() else None  # default: DM every admin
