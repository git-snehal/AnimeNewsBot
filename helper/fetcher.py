import asyncio
import logging
import re
from datetime import datetime, timezone

import feedparser
import httpx

from config import ENTRIES_PER_FEED, RSS_FEEDS
from database.db import norm_title
from helper.state import state

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AnimeNewsBot/2.0; +rss)"}
_BAD_IMG = ("icon", "pixel", "avatar", "emoji", "spacer", "logo", "1x1")


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


async def _fetch_feed(client: httpx.AsyncClient, name: str, url: str):
    try:
        r = await client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        r.raise_for_status()
        parsed = await asyncio.to_thread(feedparser.parse, r.content)
        state.feeds[name] = {"ok": True, "entries": len(parsed.entries), "error": "", "at": datetime.now(timezone.utc)}
        return name, parsed.entries
    except Exception as e:
        log.warning("Feed %s failed: %s", name, e)
        state.feeds[name] = {"ok": False, "entries": 0, "error": str(e)[:80], "at": datetime.now(timezone.utc)}
        return name, []


async def fetch_new_articles(client: httpx.AsyncClient, db, limit: int = ENTRIES_PER_FEED):
    results = await asyncio.gather(*(_fetch_feed(client, n, u) for n, u in RSS_FEEDS.items()))

    articles, seen_links, seen_titles = [], set(), set()
    for source, entries in results:
        for entry in entries[:limit]:
            link, title = entry.get("link"), entry.get("title")
            if not link or not title:
                continue
            tn = norm_title(title)
            if link in seen_links or tn in seen_titles or db.is_posted(link, title):
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
