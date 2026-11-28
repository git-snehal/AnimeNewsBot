import os
import re
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, "config.env"))


def _env(name, default=None, required=False):
    value = os.getenv(name, default)
    if required and not value:
        raise RuntimeError(f"Missing required environment variable: {name} (set it in config.env, see config.env.example)")
    return value


def _bool(name, default):
    return str(os.getenv(name, default)).strip().lower() in ("1", "true", "yes", "on")


def _chat_id(name):
    v = _env(name, "").strip()
    return int(v) if v.lstrip("-").isdigit() else None


# ---- Telegram ----
API_ID = int(_env("API_ID", required=True))
API_HASH = _env("API_HASH", required=True)
BOT_TOKEN = _env("BOT_TOKEN", required=True)
OWNER_ID = int(_env("OWNER_ID", required=True))

# Admins: the owner plus anyone listed in ADMIN_IDS (comma separated). Admins get the full "/" menu.
ADMIN_IDS = sorted({OWNER_ID, *[int(x) for x in re.split(r"[,\s]+", os.getenv("ADMIN_IDS", "")) if x.strip()]})
# Optional admins-only group where admin commands (including /logs) also work. Everyone in it can see the replies.
CONTROL_GROUP_ID = _chat_id("CONTROL_GROUP_ID")

_channel = _env("CHANNEL_ID", required=True)
CHANNEL_ID = int(_channel) if _channel.lstrip("-").isdigit() else _channel

# ---- Post look ----
SIGNATURE = _env("SIGNATURE", "@OD3NS_ANIMES")
SIGNATURE_PREFIX = _env("SIGNATURE_PREFIX", "╰ ")  # shown before the handle, inside the bold quote
DISCUSSION_LINK = _env("DISCUSSION_LINK", "")
SHOW_SOURCE_BUTTON = _bool("SHOW_SOURCE_BUTTON", True)
SILENT_REGULAR_NEWS = _bool("SILENT_REGULAR_NEWS", True)
PIN_TRAILERS = _bool("PIN_TRAILERS", False)
# Prefer a portrait picture (key visual / poster) from inside the article over the wide preview image.
PORTRAIT_IMAGES = _bool("PORTRAIT_IMAGES", True)

# Titles containing any of these whole words/phrases (comma separated, case-insensitive) are never posted.
BLOCKED_WORDS = [w.strip().lower() for w in _env("BLOCKED_WORDS", "puzzles vol,recap").split(",") if w.strip()]

# ---- Polling ----
POLL_INTERVAL = int(_env("POLL_INTERVAL", "600"))
ENTRIES_PER_FEED = 10
POST_DELAY = 5
MAX_RETRIES = 3
SEED_ON_FIRST_RUN = _bool("SEED_ON_FIRST_RUN", True)

# ---- Fetching ----
# Proxy for sites that block your server (HTTP 403), e.g. Cloudflare WARP in proxy mode: socks5://127.0.0.1:40000
# Only used after the normal attempts were refused. Empty = off.
PAGE_PROXY = _env("PAGE_PROXY", "")

# ---- Trailers (yt-dlp) ----
DOWNLOAD_TRAILERS = _bool("DOWNLOAD_TRAILERS", True)
PAGE_VIDEO_FALLBACK = _bool("PAGE_VIDEO_FALLBACK", True)
YT_SEARCH_FALLBACK = _bool("YT_SEARCH_FALLBACK", True)
MAX_VIDEO_MB = int(_env("MAX_VIDEO_MB", "150"))
MAX_VIDEO_SECONDS = int(_env("MAX_VIDEO_SECONDS", "600"))
MAX_VIDEO_HEIGHT = int(_env("MAX_VIDEO_HEIGHT", "720"))
COOKIES_PATH = _env("COOKIES_PATH", "cookies.txt")
YTDLP_PROXY = _env("YTDLP_PROXY", "")
YTDLP_AUTO_UPDATE = _bool("YTDLP_AUTO_UPDATE", True)   # check once a day, restart the bot only when something changed
YTDLP_UPDATE_PRE = _bool("YTDLP_UPDATE_PRE", False)    # nightly builds: only yt-dlp itself, never its dependencies
YOUTUBE_POT_ENABLED = _bool("YOUTUBE_POT_ENABLED", True)
YOUTUBE_POT_URL = _env("YOUTUBE_POT_URL", "http://127.0.0.1:4416")
YTDLP_REMOTE_COMPONENTS = _env("YTDLP_REMOTE_COMPONENTS", "auto")
YTDLP_JS_RUNTIME_PATH = _env("YTDLP_JS_RUNTIME_PATH", "")
DOWNLOAD_DIR = "downloads"

# ---- Optional: TMDB (free key from themoviedb.org) ----
# Sharper posters for rating cards / charts, and tells live-action titles apart from anime. Empty = AniList images only.
TMDB_API_KEY = _env("TMDB_API_KEY", "")


def _yt(channel_id: str) -> dict:
    """An official YouTube channel as a feed: only its trailers / teasers / PVs are used (see parser.is_trailer_video)."""
    return {"urls": [f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"], "youtube": True}


# ---- Feeds (verify these URLs still work with /feeds) ----
RSS_FEEDS = {
    # A feed is one URL, a list of alternative URLs (tried in order), or a dict with "urls" plus optional
    # "allow_paths" / "allow_categories" filters, "plain_headers", or "youtube": True for a YouTube channel.
    "Anime News Network": {
        "urls": [
            "https://www.animenewsnetwork.com/all/rss.xml?ann-edition=in",
            "https://www.animenewsnetwork.com/news/rss.xml?ann-edition=in",
        ],
        "allow_paths": ["/news/", "/interest/", "/daily-briefs/"],
    },
    "ComicBook.com": {
        "urls": ["https://comicbook.com/category/anime/feed/"],
        "allow_paths": ["/anime/news/"],
        "plain_headers": True,
    },
    "Crunchyroll News": [
        "https://cr-news-api-service.prd.crunchyrollsvc.com/v1/en-US/rss",
        "https://www.crunchyroll.com/news/rss",
    ],
    "Anime Corner": "https://animecorner.me/feed/",
    "Anime Trending": "https://anitrendz.net/news/feed/",
    # Official channels: trailers arrive here first, with the video already attached.
    "YouTube: Crunchyroll": _yt("UC6pGDc4bFGD1_36IKv3FnYg"),
    "YouTube: Netflix Anime": _yt("UCBSs9x2KzSLhyyA9IKyt4YA"),
    "YouTube: Aniplex USA": _yt("UCDb0peSmF5rLX7BvuTcJfCw"),
    "YouTube: TOHO animation": _yt("UC14Yc2Qv92DMuyNRlHvpo2Q"),
    "YouTube: KADOKAWA anime": _yt("UCY5fcqgSrQItPAX_Z5Frmwg"),
    "YouTube: Muse Asia": _yt("UCGbshtvS9t-8CW11W7TooQg"),
}
_disabled = {x.strip().lower() for x in os.getenv("DISABLED_FEEDS", "").split(",") if x.strip()}
RSS_FEEDS = {k: v for k, v in RSS_FEEDS.items() if k.lower() not in _disabled}

# ---- Netflix Top 10 (official data from top10.netflix.com) ----
NETFLIX_TOP10 = _bool("NETFLIX_TOP10", True)
NETFLIX_MAX_ROWS = int(_env("NETFLIX_MAX_ROWS", "6"))

# ---- IMDb episode ratings (IMDb's free non-commercial datasets) ----
IMDB_RATINGS = _bool("IMDB_RATINGS", True)
IMDB_MIN_RATING = float(_env("IMDB_MIN_RATING", "8.5"))
IMDB_MIN_VOTES = int(_env("IMDB_MIN_VOTES", "300"))
IMDB_MAX_PER_DAY = int(_env("IMDB_MAX_PER_DAY", "3"))
IMDB_TRACK_SHOWS = int(_env("IMDB_TRACK_SHOWS", "60"))   # most popular airing anime to follow
IMDB_WINDOW_DAYS = 14                                    # only episodes that got their first votes recently

# ---- Daily report ----
TIMEZONE = _env("TIMEZONE", "UTC")
TZ = ZoneInfo(TIMEZONE)  # fails fast on a typo
DAILY_STATS = _bool("DAILY_STATS", True)
STATS_TIME = _env("STATS_TIME", "23:59")  # HH:MM in TIMEZONE
STATS_CHAT_ID = _chat_id("STATS_CHAT_ID")  # default: DM every admin

# ---- Logs (/logs) ----
LOG_FILE = os.path.join("logs", "bot.log")
