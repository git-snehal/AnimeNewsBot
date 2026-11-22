import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timezone

import httpx
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from config import (
    CHANNEL_ID,
    DISCUSSION_LINK,
    DOWNLOAD_TRAILERS,
    MAX_RETRIES,
    PAGE_VIDEO_FALLBACK,
    PIN_TRAILERS,
    POLL_INTERVAL,
    POST_DELAY,
    SEED_ON_FIRST_RUN,
    SHOW_SOURCE_BUTTON,
    SILENT_REGULAR_NEWS,
    YT_SEARCH_FALLBACK,
)
from helper.downloader import cleanup, download_trailer, search_trailer
from helper.fetcher import fetch_new_articles
from helper.formatter import build_post
from helper.pagevideo import find_video_on_page
from helper.parser import KIND_NAME, clean_text, enrich
from helper.state import state
from helper.translator import translate_if_needed

log = logging.getLogger(__name__)
_failures = defaultdict(int)


async def _prepare(article: dict) -> dict:
    title = await asyncio.to_thread(translate_if_needed, clean_text(article["title"]))
    body = await asyncio.to_thread(translate_if_needed, clean_text(article["body_html"]))
    return enrich(article, title, body)


def _keyboard(a: dict):
    row = []
    if SHOW_SOURCE_BUTTON:
        row.append(InlineKeyboardButton("🔗 Source", url=a["link"]))
    if DISCUSSION_LINK:
        row.append(InlineKeyboardButton("💬 Discussion", url=DISCUSSION_LINK))
    return InlineKeyboardMarkup([row]) if row else None


async def _flood_safe(factory):
    for _ in range(3):
        try:
            return await factory()
        except FloodWait as e:
            log.warning("FloodWait: sleeping %ss", e.value)
            await asyncio.sleep(e.value + 1)
    return await factory()


async def _send(app, a: dict, text: str, kb, video):
    """Returns (message, how_it_was_sent). Falls back video -> photo -> text."""
    silent = SILENT_REGULAR_NEWS and not a["is_media"]
    common = dict(chat_id=CHANNEL_ID, parse_mode=ParseMode.HTML, reply_markup=kb, disable_notification=silent)

    attempts = []
    if video:
        attempts.append("video")
    if a.get("image_url"):
        attempts.append("photo")
    attempts.append("text")

    last_error = None
    for mode in attempts:
        try:
            if mode == "video":
                msg = await _flood_safe(lambda: app.send_video(
                    video=video["path"], caption=text, supports_streaming=True,
                    duration=video["duration"], width=video["width"], height=video["height"], **common))
            elif mode == "photo":
                msg = await _flood_safe(lambda: app.send_photo(photo=a["image_url"], caption=text, **common))
            else:
                msg = await _flood_safe(lambda: app.send_message(text=text, disable_web_page_preview=True, **common))
            return msg, mode
        except Exception as e:
            last_error = e
            log.warning("Sending as %s failed (%s), trying next option", mode, e)
    raise last_error


async def _process(app, db, article: dict, client):
    video = None
    try:
        a = await _prepare(article)

        if a["is_media"] and DOWNLOAD_TRAILERS:
            url = a["youtube_url"]
            if not url and PAGE_VIDEO_FALLBACK:
                url = await find_video_on_page(client, article["link"])
                a["youtube_url"] = url
            if not url and YT_SEARCH_FALLBACK and a.get("show"):
                url = await search_trailer(a["show"], KIND_NAME[a["kind"]])
                a["youtube_url"] = url
            if url:
                video = await download_trailer(url)

        msg, mode = await _send(app, a, build_post(a), _keyboard(a), video)
        db.add_posted(
            article["link"], article["title"],
            source=article["source"], kind=a["kind"] or "news", has_video=(mode == "video"),
        )
        log.info("Posted [%s, %s]: %s", a["kind"] or "news", mode, a["title_en"])

        if PIN_TRAILERS and a["is_media"]:
            try:
                await msg.pin(disable_notification=True)
            except Exception as e:
                log.warning("Pin failed: %s", e)

        await asyncio.sleep(POST_DELAY)
    except Exception:
        _failures[article["link"]] += 1
        log.exception("Failed to post %s (attempt %d/%d)", article["link"], _failures[article["link"]], MAX_RETRIES)
        if _failures[article["link"]] >= MAX_RETRIES:
            log.error("Giving up on %s", article["link"])
            db.add_posted(article["link"], article["title"], source=article["source"], status="skipped")
    finally:
        if video:
            cleanup(video["path"])


async def _sleep_or_wake():
    """Wait for the next poll, or until /fetchnow or /resume wakes the loop."""
    try:
        await asyncio.wait_for(state.wake.wait(), timeout=POLL_INTERVAL)
    except asyncio.TimeoutError:
        pass


async def broadcast_news_loop(app, db):
    first_run = db.is_empty()
    async with httpx.AsyncClient() as client:
        while True:
            state.wake.clear()
            if state.paused:
                await _sleep_or_wake()
                continue
            try:
                articles = await fetch_new_articles(client, db)
                state.last_cycle_at = datetime.now(timezone.utc)
                state.last_cycle_found = len(articles)

                if first_run and articles:
                    first_run = False
                    if SEED_ON_FIRST_RUN:
                        for a in articles:
                            db.add_posted(a["link"], a["title"], source=a["source"], status="seeded")
                        log.info("First run: marked %d existing articles as seen, nothing posted.", len(articles))
                        articles = []

                for article in articles:
                    if state.paused:  # /pause pressed mid-cycle: leave the rest for after /resume
                        break
                    await _process(app, db, article, client)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Error in broadcast loop")
            await _sleep_or_wake()
