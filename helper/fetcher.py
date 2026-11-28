import asyncio
import logging
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import feedparser
import httpx

from config import BLOCKED_WORDS, ENTRIES_PER_FEED, PAGE_PROXY, RSS_FEEDS
from database.db import norm_title
from helper.parser import clean_youtube_description, is_trailer_video
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

# Article pages and images use their own header profiles (an HTML Accept header, not an RSS one).
# Some sites drop connections from "Chrome" without a real browser fingerprint, others reject unknown bots: try both.
PAGE_PROFILES = [
    {"User-Agent": "Mozilla/5.0 (compatible; AnimeNewsBot/2.0)", "Accept": "text/html,application/xhtml+xml,*/*;q=0.8"},
    {"User-Agent": UA_BROWSER, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
     "Accept-Language": "en-US,en;q=0.9"},
    {"User-Agent": "Feedly/1.0 (+http://www.feedly.com/fetcher.html; like FeedFetcher-Google)", "Accept": "*/*"},
]
_page_pref = {}  # host -> index of the header profile that worked last time, or "curl"
_BLOCKED = (403, 429, 503)
BLOCKED_RETRY_SECONDS = 3600
HOST_BLOCK_SECONDS = 6 * 3600
_blocked_hosts = {}  # host -> time until which we don't even try (every attempt was refused)


class HostBlocked(Exception):
    """The site refuses this server on every attempt; page lookups for it are skipped for a few hours."""


_proxy_client = None


def proxy_client():
    """Shared client that goes through PAGE_PROXY (e.g. Cloudflare WARP), or None when no proxy is set."""
    global _proxy_client
    if PAGE_PROXY and _proxy_client is None:
        _proxy_client = httpx.AsyncClient(proxy=PAGE_PROXY, follow_redirects=True)
    return _proxy_client


async def _via_proxy(url: str, headers: dict, timeout: float):
    """One attempt through PAGE_PROXY. Returns the response, or None (no proxy / failed / still refused)."""
    pc = proxy_client()
    if pc is None:
        return None
    try:
        r = await pc.get(url, headers=headers, timeout=timeout)
    except Exception as e:
        log.info("Proxy attempt for %s failed: %s: %s", urlsplit(url).netloc, type(e).__name__, e)
        return None
    if r.status_code >= 400:
        log.info("Proxy attempt for %s answered HTTP %s", urlsplit(url).netloc, r.status_code)
        return None
    return r


async def _curl_get(url: str, timeout: float):
    """A request that looks like real Chrome at the TLS level (curl_cffi ships with yt-dlp[curl-cffi])."""
    from curl_cffi.requests import AsyncSession  # ImportError = not installed -> caller skips this step
    async with AsyncSession(impersonate="chrome") as session:
        return await session.get(url, timeout=timeout, allow_redirects=True)


async def get_page(client: httpx.AsyncClient, url: str, timeout: float = 15):
    """GET an article page / image. Tries the header profiles, then a Chrome-impersonating client; a site that
    refuses everything is remembered for a few hours so we stop wasting requests (and log lines) on it."""
    host = urlsplit(url).netloc
    if time.time() < _blocked_hosts.get(host, 0):
        raise HostBlocked(host)

    state_ = {"last": None, "blocked": False}

    async def via_curl():
        try:
            r = await _curl_get(url, timeout)
        except ImportError:
            return None
        except Exception as e:
            state_["last"] = e
            return None
        if r.status_code in _BLOCKED:
            state_["blocked"] = True
            return None
        if r.status_code >= 400:
            state_["last"] = RuntimeError(f"HTTP {r.status_code}")
            return None
        _page_pref[host] = "curl"
        return r

    if _page_pref.get(host) == "curl":
        r = await via_curl()
        if r is not None:
            return r
    if _page_pref.get(host) == "proxy":
        r = await _via_proxy(url, PAGE_PROFILES[1], timeout)
        if r is not None:
            return r

    order = list(range(len(PAGE_PROFILES)))
    if _page_pref.get(host) in order:
        order.remove(_page_pref[host])
        order.insert(0, _page_pref[host])
    for idx in order:
        try:
            r = await client.get(url, headers=PAGE_PROFILES[idx], timeout=timeout, follow_redirects=True)
            if r.status_code in _BLOCKED:
                state_["blocked"] = True
                state_["last"] = httpx.HTTPStatusError(f"HTTP {r.status_code}", request=r.request, response=r)
                continue
            r.raise_for_status()
            _page_pref[host] = idx
            return r
        except httpx.TransportError as e:  # "Server disconnected", timeouts, resets...
            state_["last"] = e

    if _page_pref.get(host) != "curl":
        r = await via_curl()
        if r is not None:
            log.info("%s only answers a Chrome-impersonating client: using that from now on", host)
            return r

    if state_["blocked"] and _page_pref.get(host) != "proxy":
        r = await _via_proxy(url, PAGE_PROFILES[1], timeout)
        if r is not None:
            _page_pref[host] = "proxy"
            log.info("%s only answers through PAGE_PROXY: using the proxy for it from now on", host)
            return r

    if state_["blocked"]:
        _blocked_hosts[host] = time.time() + HOST_BLOCK_SECONDS
        log.warning("%s refuses this server on every attempt (HTTP 403%s): skipping page lookups for it for %d hours. "
                    "Posts still use the image from the RSS feed.", host,
                    ", proxy too" if PAGE_PROXY else ", no PAGE_PROXY set", HOST_BLOCK_SECONDS // 3600)
        raise HostBlocked(host)
    raise state_["last"]


_BAD_IMG = ("icon", "pixel", "avatar", "emoji", "spacer", "logo", "1x1")
_TRACKING = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "igshid")


def clean_link(url: str) -> str:
    """Drop tracking parameters (?utm_source=rss...) so links are stable and tidy."""
    try:
        parts = urlsplit(url)
        q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not k.lower().startswith(_TRACKING)]
        host = "www.crunchyroll.com" if parts.netloc == "crunchyroll.com" else parts.netloc  # the bare host answers 403
        return urlunsplit((parts.scheme, host, parts.path, urlencode(q), ""))
    except Exception:
        return url


def feed_urls(value) -> list:
    if isinstance(value, dict):
        value = value["urls"]
    return [value] if isinstance(value, str) else list(value)


def _words(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (text or "").lower()))


def passes_rules(entry, value) -> bool:
    """Per-feed filters (see config.py). No rules = everything passes."""
    if not isinstance(value, dict):
        return True
    if value.get("youtube") and not is_trailer_video(entry.get("title", "")):
        return False
    paths = value.get("allow_paths")
    if paths and not any(urlsplit(entry.get("link", "")).path.startswith(p) for p in paths):
        return False
    cats = value.get("allow_categories")
    if cats:
        allowed = set().union(*(_words(c) for c in cats))
        tags = set().union(*(_words(t.get("term", "")) for t in entry.get("tags", []) or []), set())
        if tags and not (tags & allowed):
            return False
    return True


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


def _bodies(entry, youtube=False):
    """(best text for the blurb, everything that might contain a video link)"""
    if youtube:
        desc = clean_youtube_description(entry.get("summary", "") or "")
        return desc, entry.get("link", "")
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


async def _fetch_feed(client: httpx.AsyncClient, name: str, value, force: bool = False):
    """Try every URL x header profile (the one that worked last time first) until a feed answers.

    A feed that is blocked outright (403 everywhere) is only retried once an hour, so we don't hammer the site.
    `force=True` (used by /feeds) ignores that pause.
    """
    if not force and time.time() < state.feed_blocked_until.get(name, 0):
        return name, []

    combos = [(u, i) for u in feed_urls(value) for i in range(len(HEADER_PROFILES))]
    pref = state.feed_pref.get(name)
    if pref in combos:
        combos.remove(pref)
        combos.insert(0, pref)

    attempts, detail, started = [], None, time.monotonic()
    for url, idx in combos:
        if time.monotonic() - started > 120:   # a very slow site must not hold up the whole cycle
            attempts.append("gave up after 2 min")
            break
        try:
            r = await client.get(url, headers=HEADER_PROFILES[idx], timeout=45, follow_redirects=True)
            attempts.append(f"{r.status_code}")
            if r.status_code != 200:
                continue
            parsed = await asyncio.to_thread(feedparser.parse, r.content)
            if not parsed.entries:
                head = r.text[:300].lower()
                detail = ("this URL returns a web page, not an RSS feed" if "<html" in head or "<!doctype html" in head
                          else f"unreadable feed ({r.headers.get('content-type', '?')})")
                attempts[-1] = "200 but no entries"
                continue
            if state.feed_pref.get(name) != (url, idx):
                log.info("Feed %s: using %s (header profile %d)", name, url, idx)
            state.feed_pref[name] = (url, idx)
            state.feed_blocked_until.pop(name, None)
            state.feeds[name] = {"ok": True, "entries": len(parsed.entries), "error": "", "url": url,
                                 "profile": idx, "attempts": attempts, "at": datetime.now(timezone.utc)}
            return name, parsed.entries
        except Exception as e:
            attempts.append(type(e).__name__)

    if attempts and set(attempts) <= {"403", "429", "503"} and PAGE_PROXY:
        for url in feed_urls(value):
            r = await _via_proxy(url, HEADER_PROFILES[0], 25)
            if r is None:
                continue
            parsed = await asyncio.to_thread(feedparser.parse, r.content)
            if parsed.entries:
                attempts.append("proxy 200")
                state.feed_blocked_until.pop(name, None)
                state.feeds[name] = {"ok": True, "entries": len(parsed.entries), "error": "", "url": url + " (via proxy)",
                                     "profile": "proxy", "attempts": attempts, "at": datetime.now(timezone.utc)}
                return name, parsed.entries
        attempts.append("proxy refused")

    if attempts and set(attempts) <= {"403", "proxy refused"}:
        error = "blocked by the site (HTTP 403" + (", proxy too" if PAGE_PROXY else "") + "), retrying hourly"
        state.feed_blocked_until[name] = time.time() + BLOCKED_RETRY_SECONDS
    elif detail:
        error = detail
    else:
        error = "all attempts failed: " + ", ".join(attempts)
    log.warning("Feed %s failed: %s", name, error)
    state.feeds[name] = {"ok": False, "entries": 0, "error": error, "url": "", "profile": None,
                         "attempts": attempts, "at": datetime.now(timezone.utc)}
    return name, []


fetch_feed = _fetch_feed


def _blocked(title: str) -> bool:
    """BLOCKED_WORDS match whole words only: 'recap' blocks 'Episode 3 Recap', not 'Recaptured'."""
    t = title.lower()
    return any(re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", t) for w in BLOCKED_WORDS)


async def fetch_new_articles(client: httpx.AsyncClient, db, limit: int = ENTRIES_PER_FEED):
    """db=None skips the 'already posted' filter (used by the test commands)."""
    results = await asyncio.gather(*(_fetch_feed(client, n, u) for n, u in RSS_FEEDS.items()))

    articles, seen_links, seen_titles = [], set(), set()
    for source, entries in results:
        wanted = [e for e in entries if passes_rules(e, RSS_FEEDS.get(source))]
        for entry in wanted[:limit]:
            link, title = entry.get("link"), entry.get("title")
            if not link or not title:
                continue
            if _blocked(title):
                continue
            link = clean_link(link)
            tn = norm_title(title)
            if link in seen_links or tn in seen_titles or (db is not None and db.is_posted(link, title)):
                continue
            seen_links.add(link)
            seen_titles.add(tn)
            rules = RSS_FEEDS.get(source)
            youtube = bool(isinstance(rules, dict) and rules.get("youtube"))
            body, raw = _bodies(entry, youtube)
            articles.append(
                {
                    "title": title,
                    "link": link,
                    "source": source,
                    "body_html": body,
                    "raw_html": raw,
                    "image_url": extract_image(entry),
                    "published": _published(entry),
                    "youtube_feed": youtube,
                }
            )
    # oldest first so the channel reads chronologically
    articles.sort(key=lambda a: a["published"] or datetime(1970, 1, 1, tzinfo=timezone.utc))
    return articles
