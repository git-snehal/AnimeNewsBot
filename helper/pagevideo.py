"""Look at an article page when the RSS item is missing something.

Video (only needed for trailers) is checked in order of trustworthiness:
  1. og:video / twitter:player meta tags
  2. JSON-LD VideoObject
  3. embeds/links inside the article body only (sidebar, related posts, header, footer, comments are ignored)
If nothing clear is found we return None and the post goes out without a video - never a guess.

Image: og:image / twitter:image.
"""
import json
import logging
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from helper.parser import extract_youtube_url

log = logging.getLogger(__name__)

MAX_HTML = 3_000_000

_VIDEO_META = {"og:video", "og:video:url", "og:video:secure_url", "twitter:player", "twitter:player:stream"}
_IMAGE_META = ("og:image", "og:image:secure_url", "og:image:url", "twitter:image", "twitter:image:src")
_NOISE_SELECTORS = (
    "aside, nav, footer, header, form, script, style, "
    "[class*=sidebar], [id*=sidebar], [class*=related], [id*=related], "
    "[class*=comment], [id*=comment], [class*=widget], [class*=trending], [class*=popular], [class*=newsletter]"
)
_BODY_SELECTORS = (
    "article", "main", "[role=main]", ".entry-content", ".post-content", ".article-content", ".article-body",
)


def _find_video(soup):
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or "").lower()
        if key in _VIDEO_META:
            url = extract_youtube_url(tag.get("content", ""))
            if url:
                return url, f"meta:{key}"

    for script in soup.find_all("script", type="application/ld+json"):
        text = script.string or script.get_text() or ""
        if "VideoObject" not in text:
            continue
        try:
            json.loads(text)
        except ValueError:
            continue
        url = extract_youtube_url(text)
        if url:
            return url, "json-ld"

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


def _find_image(soup, base_url):
    metas = {}
    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or "").lower()
        content = (tag.get("content") or "").strip()
        if key in _IMAGE_META and content and key not in metas:
            metas[key] = content
    for key in _IMAGE_META:
        if key in metas:
            return urljoin(base_url, metas[key])
    return None


def parse_page(page: str, base_url: str = ""):
    soup = BeautifulSoup(page, "html.parser")
    video, where = _find_video(soup)
    return {"video": video, "where": where, "image": _find_image(soup, base_url)}


def parse_video_from_html(page: str):
    """Kept for tests: (youtube_url, where) or (None, None)."""
    r = parse_page(page)
    return r["video"], r["where"]


async def fetch_page_info(client: httpx.AsyncClient, page_url: str) -> dict:
    from helper.fetcher import PAGE_HEADERS  # late import: fetcher imports state, keep this module light

    empty = {"video": None, "where": None, "image": None}
    try:
        r = await client.get(page_url, headers=PAGE_HEADERS, timeout=15, follow_redirects=True)
        r.raise_for_status()
        info = parse_page(r.text[:MAX_HTML], str(r.url))
    except Exception as e:
        log.warning("Page lookup failed for %s: %s", page_url, e)
        return empty
    log.info("Page lookup %s -> video: %s (%s) | image: %s", page_url[:90], info["video"] or "none",
             info["where"] or "-", "yes" if info["image"] else "no")
    return info
