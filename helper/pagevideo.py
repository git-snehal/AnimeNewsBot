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


_BAD_IMG = ("icon", "pixel", "avatar", "emoji", "spacer", "logo", "1x1", "gravatar", "doubleclick", "adsystem",
            "/ads/", "banner-ad", "sprite", "placeholder", "loading", "badge", "button")


def _best_src(img) -> str:
    """The biggest version of an <img>: srcset / data-srcset largest width, then lazy-load attributes, then src."""
    for attr in ("srcset", "data-srcset", "data-lazy-srcset"):
        val = img.get(attr)
        if val:
            best, best_w = None, -1
            for part in val.split(","):
                bits = part.strip().split()
                if not bits:
                    continue
                w = int(bits[1][:-1]) if len(bits) > 1 and bits[1].endswith("w") and bits[1][:-1].isdigit() else 0
                if w > best_w:
                    best, best_w = bits[0], w
            if best:
                return best
    for attr in ("data-lazy-src", "data-src", "data-original", "src"):
        if img.get(attr) and not img[attr].startswith("data:"):
            return img[attr]
    return ""


def _body_images(soup, base_url, limit=6) -> list:
    """Pictures inside the article body (key visuals, posters), largest version, in page order."""
    scope = None
    for sel in _BODY_SELECTORS:
        scope = soup.select_one(sel)
        if scope:
            break
    if scope is None:
        return []
    out = []
    for img in scope.find_all("img"):
        src = _best_src(img)
        if not src:
            continue
        url = urljoin(base_url, src)
        low = url.lower()
        if not low.startswith("http") or low.split("?")[0].endswith((".svg", ".gif")) or any(b in low for b in _BAD_IMG):
            continue
        try:
            w, h = int(img.get("width") or 0), int(img.get("height") or 0)
        except ValueError:
            w = h = 0
        if (w and w < 300) or (h and h < 300):
            continue
        if url not in out:
            out.append(url)
        if len(out) >= limit:
            break
    return out


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
    image = _find_image(soup, base_url)          # read the meta tags before _find_video trims the article body
    images = _body_images(soup, base_url)
    video, where = _find_video(soup)
    return {"video": video, "where": where, "image": image, "images": images}


def parse_video_from_html(page: str):
    """Kept for tests: (youtube_url, where) or (None, None)."""
    r = parse_page(page)
    return r["video"], r["where"]


async def fetch_page_info(client: httpx.AsyncClient, page_url: str) -> dict:
    from helper.fetcher import HostBlocked, get_page  # late import: keep this module light

    host = page_url.split("/")[2] if "//" in page_url else page_url
    empty = {"video": None, "where": None, "image": None, "images": [], "status": ""}
    try:
        r = await get_page(client, page_url)
        info = parse_page(r.text[:MAX_HTML], str(r.url))
    except HostBlocked:
        log.debug("Page lookup skipped (site blocks this server): %s", page_url)
        return {**empty, "status": f"{host} blocked (403)"}
    except Exception as e:
        log.warning("Page lookup failed for %s: %s: %s", page_url, type(e).__name__, e)
        return {**empty, "status": f"{host} error {type(e).__name__}"}
    info["status"] = f"{host} ok, video {'found (' + info['where'] + ')' if info['video'] else 'none'}"
    log.info("Page lookup %s -> video: %s (%s) | image: %s | body images: %d", page_url[:90], info["video"] or "none",
             info["where"] or "-", "yes" if info["image"] else "no", len(info["images"]))
    return info
