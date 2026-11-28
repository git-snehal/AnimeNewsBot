"""Small helpers for sending to the channel, shared by the news, Netflix and IMDb jobs."""
import asyncio
import html
import io
import logging

import httpx
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import DISCUSSION_LINK, SIGNATURE, SIGNATURE_PREFIX

log = logging.getLogger(__name__)
UA = {"User-Agent": "Mozilla/5.0 (compatible; AnimeNewsBot/3.0)"}


def esc(s) -> str:
    return html.escape(str(s), quote=False)


def signature_html() -> str:
    return f"<blockquote><b>{esc(SIGNATURE_PREFIX + SIGNATURE)}</b></blockquote>"


def keyboard(buttons: list):
    """buttons: [(text, url)]; the discussion button is added when configured."""
    row = [InlineKeyboardButton(t, url=u) for t, u in buttons if u and u.startswith(("http://", "https://"))]
    if DISCUSSION_LINK:
        row.append(InlineKeyboardButton("💬 Discussion", url=DISCUSSION_LINK))
    return InlineKeyboardMarkup([row]) if row else None


async def flood_safe(factory):
    for _ in range(3):
        try:
            return await factory()
        except FloodWait as e:
            log.warning("FloodWait: sleeping %ss", e.value)
            await asyncio.sleep(e.value + 1)
    return await factory()


async def send_image(app, chat_id, image: bytes, caption: str, kb=None, silent=True, name="card.jpg"):
    """Upload an image we made (or downloaded) with an HTML caption."""
    async def go():
        f = io.BytesIO(image)
        f.name = name
        return await app.send_photo(chat_id, f, caption=caption, parse_mode=ParseMode.HTML,
                                    reply_markup=kb, disable_notification=silent)
    return await flood_safe(go)


async def fetch_bytes(client: httpx.AsyncClient, url: str, max_mb: int = 15):
    """Download a picture; None when it fails or isn't an image."""
    if not url:
        return None
    try:
        r = await client.get(url, headers=UA, timeout=30, follow_redirects=True)
        if r.status_code != 200 or not r.headers.get("content-type", "").startswith("image/") \
                or len(r.content) > max_mb * 1024 * 1024:
            return None
        return r.content
    except Exception as e:
        log.info("Image download failed for %s: %s", url[:90], type(e).__name__)
        return None
