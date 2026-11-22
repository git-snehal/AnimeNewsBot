import asyncio
import logging
import re
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx

from config import ENTRIES_PER_FEED, RSS_FEEDS
from database.db import norm_title
from helper.state import state

log = logging.getLogger(__name__)

UA_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)
# Tried in this order. Some sites (ANN, for one) answer 403 to anything that doesn't look like a browser or a reader.
HEADER_PROFILES = [
    {"User-Agent": UA_BROWSER, "Accept": "application/rss+xml, application/xml;q=0.9, text/xml;q=0.8, */*;q=0.5",
     "Accept-Language": "en-US,en;q=0.9"},
    {"User-Agent": "Feedly/1.0 (+http://www.feedly.com/fetcher.html; like FeedFetcher-Google)", "Accept": "*/*"},
    {"User-Agent": "Mozilla/5.0 (compatible; AnimeNewsBot/2.0)"},
]
PAGE_HEADERS = HEADER_PROFILES[0]
_BAD_IMG = ("icon", "pixel", "avatar", "emoji", "spacer", "logo", "1x1")
_TRACKING = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "igshid")


def clean_link(url: str) -> str:
    """Drop tracking parameters (?utm_source=rss...) so links are stable and tidy."""
    try:
        parts = urlsplit(url)
        q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith(_TRACKING)]
        return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(q), ""))
    except Exception:
        return url


def feed_urls(value) -> list:
    return [value] if isinstance(value, str) else list(value)


def extract_image(entry):
    for media in entry.get("media_content", []) or []:
        url = media.get("url")
        if url and (media.get("medium") == "image" or media.get("type", "").startswith("image") or not media.get("type")):
            if not url.lower().endswith((".mp4", ".webm")):
                return url
    for thumb in entry.get("media_thumbnail", []) or []:
        if thumb.get("url"):
            return thumb["url"]
    for enc in entry.get("enclosures", []) or []:
        if enc.get("type", "").startswith("image/"):
            return enc.get("href")

    blob = " ".join([entry.get("summary", "")] + [c.get("value", "") for c in entry.get("content", []) or []])
    for url in re.findall(r"""src=["']([^"']+)["']""", blob):
        if url.lower().startswith("http") and not any(x in url.lower() for x in _BAD_IMG):
            return url
    return None


def _bodies(entry):
    """(best text for the blurb, everything that might contain a video link)"""
    parts = [entry.get("summary", "")] + [c.get("value", "") for c in entry.get("content", []) or []]
    parts = [p for p in parts if p]
    extra = [entry.get("link", "")]
    extra += [m.get("url", "") for m in entry.get("media_content", []) or []]
    extra += [e.get("href", "") for e in entry.get("enclosures", []) or []]
    extra += [l.get("href", "") for l in entry.get("links", []) or []]
    body = max(parts, key=len) if parts else ""
    return body, "\n".join(parts + extra)


def _published(entry):
    t = entry.get("published_parsed") or entry.get("updated_parsed")
    return datetime(*t[:6], tzinfo=timezone.utc) if t else None


async def _fetch_feed(client: httpx.AsyncClient, name: str, value):
    """Try every URL x header profile (the one that worked last time first) until a feed answers."""
    combos = [(u, i) for u in feed_urls(value) for i in range(len(HEADER_PROFILES))]
    pref = state.feed_pref.get(name)
    if pref in combos:
        combos.remove(pref)
        combos.insert(0, pref)

    attempts = []
    for url, idx in combos:
        try:
            r = await client.get(url, headers=HEADER_PROFILES[idx], timeout=20, follow_redirects=True)
            attempts.append(f"{r.status_code}")
            if r.status_code != 200:
                continue
            parsed = await asyncio.to_thread(feedparser.parse, r.content)
            if not parsed.entries:
                attempts[-1] = "200 but no entries"
                continue
            if state.feed_pref.get(name) != (url, idx):
                log.info("Feed %s: using %s (header profile %d)", name, url, idx)
            state.feed_pref[name] = (url, idx)
            state.feeds[name] = {"ok": True, "entries": len(parsed.entries), "error": "", "url": url,
                                 "profile": idx, "attempts": attempts, "at": datetime.now(timezone.utc)}
            return name, parsed.entries
        except Exception as e:
            attempts.append(type(e).__name__)
    error = "blocked (HTTP 403)" if attempts and set(attempts) == {"403"} else "all attempts failed: " + ", ".join(attempts)
    log.warning("Feed %s failed: %s", name, error)
    state.feeds[name] = {"ok": False, "entries": 0, "error": error, "url": "", "profile": None,
                         "attempts": attempts, "at": datetime.now(timezone.utc)}
    return name, []


fetch_feed = _fetch_feed


async def fetch_new_articles(client: httpx.AsyncClient, db, limit: int = ENTRIES_PER_FEED):
    """db=None skips the 'already posted' filter (used by the test commands)."""
    results = await asyncio.gather(*(_fetch_feed(client, n, u) for n, u in RSS_FEEDS.items()))

    articles, seen_links, seen_titles = [], set(), set()
    for source, entries in results:
        for entry in entries[:limit]:
            link, title = entry.get("link"), entry.get("title")
            if not link or not title:
                continue
            link = clean_link(link)
            tn = norm_title(title)
            if link in seen_links or tn in seen_titles or (db is not None and db.is_posted(link, title)):
                continue
            seen_links.add(link)
            seen_titles.add(tn)
            body, raw = _bodies(entry)
            articles.append(
                {
                    "title": title,
                    "link": link,
                    "source": source,
                    "body_html": body,
                    "raw_html": raw,
                    "image_url": extract_image(entry),
                    "published": _published(entry),
                }
            )
    # oldest first so the channel reads chronologically
    articles.sort(key=lambda a: a["published"] or datetime(1970, 1, 1, tzinfo=timezone.utc))
    return articles
