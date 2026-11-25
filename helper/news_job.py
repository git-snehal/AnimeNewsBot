import asyncio
import io
import logging
from collections import defaultdict
from datetime import datetime, timezone

import httpx
from pyrogram.enums import ParseMode
from pyrogram.errors import FloodWait
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, InputMediaVideo

from config import (
    CHANNEL_ID,
    DISCUSSION_LINK,
    DOWNLOAD_TRAILERS,
    MAX_RETRIES,
    PAGE_VIDEO_FALLBACK,
    PIN_TRAILERS,
    RSS_FEEDS,
    POLL_INTERVAL,
    POST_DELAY,
    SEED_ON_FIRST_RUN,
    SHOW_SOURCE_BUTTON,
    SILENT_REGULAR_NEWS,
    YT_SEARCH_FALLBACK,
)
from helper.downloader import cleanup, download_trailer, search_trailer
from helper.fetcher import fetch_new_articles, get_page
from helper.formatter import build_post
from helper.pagevideo import fetch_page_info
from helper.parser import KIND_NAME, clean_text, enrich
from helper.state import state
from helper.story import kinds_compatible, same_news, same_story
from helper.translator import translate_if_needed

log = logging.getLogger(__name__)
_failures = defaultdict(int)


# ------------------------------------------------------------------ preparing
async def _prepare(article: dict) -> dict:
    title = await asyncio.to_thread(translate_if_needed, clean_text(article["title"]))
    body = await asyncio.to_thread(translate_if_needed, clean_text(article["body_html"]))
    a = enrich(article, title, body)
    rules = RSS_FEEDS.get(article["source"])
    a["plain_header"] = bool(isinstance(rules, dict) and rules.get("plain_headers"))
    return a


def _same(a: dict, b: dict) -> bool:
    if a["kind"] and b["kind"]:
        return kinds_compatible(a["kind"], b["kind"]) and same_story(a["story_tokens"].split(), b["story_tokens"].split())
    if not a["kind"] and not b["kind"]:
        return same_news(a["title_tokens"].split(), b["title_tokens"].split())
    return False


def group_stories(prepared: list) -> list:
    """Articles in this batch about the same story (any outlet) end up in one group, oldest first."""
    groups = []
    for a in prepared:
        for g in groups:
            if any(_same(m, a) for m in g):
                g.append(a)
                break
        else:
            groups.append([a])
    return groups


async def _resolve(candidate: dict, client, want_video: bool):
    """Fill in a missing video link and/or image from the article page (one request at most)."""
    need_video = want_video and PAGE_VIDEO_FALLBACK and not candidate["youtube_url"]
    need_image = not candidate.get("image_url")
    if not (need_video or need_image):
        return
    info = await fetch_page_info(client, candidate["link"])
    if need_video and info["video"]:
        candidate["youtube_url"] = info["video"]
    if need_image and info["image"]:
        candidate["image_url"] = info["image"]


# ------------------------------------------------------------------ sending
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


async def _image_file(client: httpx.AsyncClient, url: str):
    """Download the image ourselves, for when Telegram can't fetch the URL (hotlink protection, redirects...)."""
    r = await get_page(client, url, timeout=20)
    ctype = r.headers.get("content-type", "").split(";")[0].strip().lower()
    if not ctype.startswith("image/") or len(r.content) > 10 * 1024 * 1024:
        raise ValueError(f"not a usable image ({ctype}, {len(r.content)} bytes)")
    f = io.BytesIO(r.content)
    f.name = "image." + {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}.get(ctype, "jpg")
    return f


async def _send(app, client, a: dict, text: str, kb, video):
    """Returns (message, how_it_was_sent). Falls back video -> photo -> photo we upload ourselves -> text."""
    silent = SILENT_REGULAR_NEWS and not a["is_media"]
    common = dict(chat_id=CHANNEL_ID, parse_mode=ParseMode.HTML, reply_markup=kb, disable_notification=silent)

    attempts = []
    if video:
        attempts.append("video")
    if a.get("image_url"):
        attempts += ["photo", "photo_upload"]
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
            elif mode == "photo_upload":
                f = await _image_file(client, a["image_url"])

                async def _up():
                    f.seek(0)
                    return await app.send_photo(photo=f, caption=text, **common)

                msg = await _flood_safe(_up)
            else:
                msg = await _flood_safe(lambda: app.send_message(text=text, disable_web_page_preview=True, **common))
            return msg, mode
        except Exception as e:
            last_error = e
            log.warning("Sending as %s failed (%s), trying next option", mode, e)
    raise last_error


# ------------------------------------------------------------------ one story
def _mark_duplicates(db, group, skip=None):
    for a in group:
        if a is not skip:
            db.add_posted(a["link"], a["title"], source=a["source"], kind=a["kind"], status="duplicate",
                          story_tokens=a["story_tokens"], title_tokens=a["title_tokens"])


async def _add_video_to_earlier_post(app, db, client, group: list, match: dict) -> bool:
    """An earlier post about this story went out as a photo; another outlet has the video: edit it into the post."""
    video = None
    try:
        source = None
        for cand in group:
            await _resolve(cand, client, want_video=True)
            if cand["youtube_url"]:
                source = cand
                break
        if not source:
            return False
        video = await download_trailer(source["youtube_url"])
        if not video:
            return False
        media = InputMediaVideo(
            video["path"], caption=match["caption"], parse_mode=ParseMode.HTML, supports_streaming=True,
            duration=video["duration"], width=video["width"] or 0, height=video["height"] or 0)
        await _flood_safe(lambda: app.edit_message_media(
            CHANNEL_ID, match["message_id"], media, reply_markup=_keyboard({"link": match["link"]})))
        db.mark_video_added(match["id"])
        log.info("Added the video to the earlier post (message %s): %s", match["message_id"], group[0]["title_en"])
        return True
    except Exception:
        log.exception("Could not add the video to the earlier post")
        return False
    finally:
        if video:
            cleanup(video["path"])


async def _process_group(app, db, group: list, client):
    first = group[0]
    match = db.find_story(first["story_tokens"], first["kind"], first["title_tokens"])
    if match:
        if (first["is_media"] and DOWNLOAD_TRAILERS and match["status"] == "posted" and not match["has_video"]
                and match["sent_as"] in ("photo", "photo_upload") and match["message_id"] and match["caption"]):
            await _add_video_to_earlier_post(app, db, client, group, match)
        _mark_duplicates(db, group)
        log.info("Same story already posted, skipped: %s", first["title_en"])
        return

    primary, video = first, None
    try:
        want_video = first["is_media"] and DOWNLOAD_TRAILERS
        if want_video:
            # several outlets cover the same trailer: use the first one whose page actually has the video
            for cand in group:
                await _resolve(cand, client, want_video=True)
                if cand["youtube_url"]:
                    primary = cand
                    break
            else:
                primary = next((c for c in group if c.get("image_url")), first)
        else:
            await _resolve(primary, client, want_video=False)
        if not primary.get("image_url"):
            primary["image_url"] = next((c["image_url"] for c in group if c.get("image_url")), None)

        if want_video:
            url = primary["youtube_url"]
            name = primary.get("show") or primary.get("search_name")
            if not url and YT_SEARCH_FALLBACK and name:
                found = await search_trailer(name, KIND_NAME[primary["kind"]], primary["title_en"], primary.get("published"))
                if found:
                    url = primary["youtube_url"] = found["url"]
                    log.info("YouTube search matched %r (%s) for: %s", found["title"], found.get("channel"), primary["title_en"])
            if url:
                state.last_youtube_url = url
                video = await download_trailer(url)

        text = build_post(primary)
        msg, mode = await _send(app, client, primary, text, _keyboard(primary), video)
        db.add_posted(
            primary["link"], primary["title"], source=primary["source"], kind=primary["kind"] or "news",
            has_video=(mode == "video"), story_tokens=primary["story_tokens"], title_tokens=primary["title_tokens"],
            message_id=getattr(msg, "id", None), sent_as=mode, caption=text,
        )
        _mark_duplicates(db, group, skip=primary)
        log.info("Posted [%s, %s]: %s", primary["kind"] or "news", mode, primary["title_en"])

        if PIN_TRAILERS and primary["is_media"]:
            try:
                await msg.pin(disable_notification=True)
            except Exception as e:
                log.warning("Pin failed: %s", e)

        await asyncio.sleep(POST_DELAY)
    except Exception:
        _failures[primary["link"]] += 1
        log.exception("Failed to post %s (attempt %d/%d)", primary["link"], _failures[primary["link"]], MAX_RETRIES)
        if _failures[primary["link"]] >= MAX_RETRIES:
            log.error("Giving up on %s", primary["link"])
            db.add_posted(primary["link"], primary["title"], source=primary["source"], status="skipped")
    finally:
        if video:
            cleanup(video["path"])


# ------------------------------------------------------------------ the loop
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
                        for article in articles:
                            try:
                                a = await _prepare(article)
                                kind, tokens, ttokens = a["kind"], a["story_tokens"], a["title_tokens"]
                            except Exception:
                                kind, tokens, ttokens = None, None, None
                            db.add_posted(article["link"], article["title"], source=article["source"], kind=kind,
                                          status="seeded", story_tokens=tokens, title_tokens=ttokens)
                        log.info("First run: marked %d existing articles as seen, nothing posted.", len(articles))
                        articles = []

                prepared = []
                for article in articles:
                    try:
                        prepared.append(await _prepare(article))
                    except Exception:
                        log.exception("Could not prepare %s", article["link"])

                for group in group_stories(prepared):
                    if state.paused:  # /pause pressed mid-cycle: leave the rest for after /resume
                        break
                    await _process_group(app, db, group, client)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Error in broadcast loop")
            await _sleep_or_wake()
