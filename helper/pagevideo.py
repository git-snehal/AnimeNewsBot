"""Fallback: find the article's own YouTube video on the article page when the RSS item has none.

Checked in order of trustworthiness:
  1. og:video / twitter:player meta tags
  2. JSON-LD VideoObject
  3. embeds/links inside the article body only (sidebar, related posts, header, footer, comments are ignored)
If nothing clear is found we return None and the post goes out without a video - never a guess.
"""
import json
import logging

import httpx
from bs4 import BeautifulSoup

from helper.parser import extract_youtube_url

log = logging.getLogger(__name__)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AnimeNewsBot/2.0)"}
MAX_HTML = 3_000_000

_META_KEYS = {"og:video", "og:video:url", "og:video:secure_url", "twitter:player", "twitter:player:stream"}
_NOISE_SELECTORS = (
    "aside, nav, footer, header, form, script, style, "
    "[class*=sidebar], [id*=sidebar], [class*=related], [id*=related], "
    "[class*=comment], [id*=comment], [class*=widget], [class*=trending], [class*=popular], [class*=newsletter]"
)
_BODY_SELECTORS = (
    "article", "main", "[role=main]", ".entry-content", ".post-content", ".article-content", ".article-body",
)


def parse_video_from_html(page: str):
    """Returns (youtube_url, where_it_was_found) or (None, None)."""
    soup = BeautifulSoup(page, "html.parser")

    # 1. meta tags describing the page's main video
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or "").lower()
        if key in _META_KEYS:
            url = extract_youtube_url(tag.get("content", ""))
            if url:
                return url, f"meta:{key}"

    # 2. JSON-LD VideoObject
    for script in soup.find_all("script", type="application/ld+json"):
        text = script.string or script.get_text() or ""
        if "VideoObject" not in text:
            continue
        try:
            json.loads(text)  # skip malformed blocks
        except ValueError:
            continue
        url = extract_youtube_url(text)
        if url:
            return url, "json-ld"

    # 3. article body, minus the noisy parts of the page
    scope = None
    for sel in _BODY_SELECTORS:
        scope = soup.select_one(sel)
        if scope:
            break
    if scope is None:
        return None, None  # no identifiable article body: don't scan the whole page
    for noisy in scope.select(_NOISE_SELECTORS):
        noisy.decompose()
    url = extract_youtube_url(str(scope))
    return (url, "article-body") if url else (None, None)


async def find_video_on_page(client: httpx.AsyncClient, page_url: str):
    try:
        r = await client.get(page_url, headers=HEADERS, timeout=15, follow_redirects=True)
        r.raise_for_status()
        url, where = parse_video_from_html(r.text[:MAX_HTML])
    except Exception as e:
        log.warning("Page video lookup failed for %s: %s", page_url, e)
        return None
    if url:
        log.info("Found video on page (%s): %s -> %s", where, page_url, url)
    else:
        log.info("No video found on page: %s", page_url)
    return url
