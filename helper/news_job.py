import asyncio
import io
import logging
import re
from collections import defaultdict
from datetime import datetime, timezone

import httpx
from PIL import Image
from pyrogram.enums import ParseMode
from pyrogram.types import InputMediaVideo

from config import (
    CHANNEL_ID,
    DOWNLOAD_TRAILERS,
    MAX_RETRIES,
    PAGE_VIDEO_FALLBACK,
    PIN_TRAILERS,
    PORTRAIT_IMAGES,
    POLL_INTERVAL,
    POST_DELAY,
    RSS_FEEDS,
    SEED_ON_FIRST_RUN,
    SHOW_SOURCE_BUTTON,
    SILENT_REGULAR_NEWS,
    YT_SEARCH_FALLBACK,
)
from helper import anilist
from helper.downloader import cleanup, download_trailer_verbose, explain, search_trailer
from helper.fetcher import fetch_new_articles, get_page
from helper.formatter import build_post
from helper.pagevideo import fetch_page_info
from helper.parser import KIND_NAME, clean_text, enrich
from helper.publish import fetch_bytes, flood_safe, keyboard
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
    if a.get("youtube_feed"):
        # Japanese uploads: 『Show』 ... translate the show name on its own, so it isn't mixed into the sentence
        m = re.search(r"[『「]([^』」]{2,80})[』」]", article["title"])
        if m:
            name = await asyncio.to_thread(translate_if_needed, re.sub(r"^(?:劇場版|TVアニメ)\s*", "", m.group(1)))
            if name and name != m.group(1):
                a["show"] = name.strip(" '\"“”")
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


async def _page(candidate: dict, client) -> dict:
    """Article page info (video link, images), fetched at most once per article. YouTube items have no page."""
    if "page" not in candidate:
        if candidate.get("youtube_feed"):
            candidate["page"] = {"video": None, "image": None, "images": [], "status": "youtube item"}
        else:
            candidate["page"] = await fetch_page_info(client, candidate["link"])
    return candidate["page"]


async def _resolve(candidate: dict, client, want_video: bool, notes: list):
    """Fill in a missing video link and/or image from the article page."""
    need_video = want_video and PAGE_VIDEO_FALLBACK and not candidate["youtube_url"]
    need_image = not candidate.get("image_url")
    if not (need_video or need_image or (PORTRAIT_IMAGES and not candidate.get("youtube_feed"))):
        return
    info = await _page(candidate, client)
    if need_video:
        notes.append(f"page {info.get('status') or '?'}")
        if info["video"]:
            candidate["youtube_url"] = info["video"]
            candidate["video_from"] = f"article page ({info.get('where')})"
    if need_image and info["image"]:
        candidate["image_url"] = info["image"]


async def _portrait(candidate: dict, client):
    """A portrait picture (key visual / poster) from inside the article, if there is one. Wide preview images are
    what feeds and link previews give us; the real key visual is usually in the article body."""
    page = candidate.get("page") or {}
    for url in (page.get("images") or [])[:5]:
        data = await fetch_bytes(client, url, max_mb=10)
        if not data:
            continue
        try:
            w, h = Image.open(io.BytesIO(data)).size
        except Exception:
            continue
        if w >= 450 and 1.15 * w <= h <= 2.2 * w:
            log.info("Portrait image picked (%dx%d): %s", w, h, url[:100])
            return url, data
    return None


async def _alt_names(client, a: dict) -> list:
    """All names AniList knows for the show (English, romaji, synonyms) to make the YouTube search match."""
    name = a.get("show") or a.get("search_name")
    media = await anilist.find_show(client, name) if name else None
    return anilist.names(media) if media else []


# ------------------------------------------------------------------ sending
def _keyboard(a: dict):
    return keyboard([("🔗 Source", a["link"])] if SHOW_SOURCE_BUTTON else [])


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
                msg = await flood_safe(lambda: app.send_video(
                    video=video["path"], caption=text, supports_streaming=True,
                    duration=video["duration"], width=video["width"], height=video["height"], **common))
            elif mode == "photo":
                msg = await flood_safe(lambda: app.send_photo(photo=a["image_url"], caption=text, **common))
            elif mode == "photo_upload":
                if a.get("image_bytes"):
                    f = io.BytesIO(a["image_bytes"])
                    f.name = "image.jpg"
                else:
                    f = await _image_file(client, a["image_url"])

                async def _up():
                    f.seek(0)
                    return await app.send_photo(photo=f, caption=text, **common)

                msg = await flood_safe(_up)
            else:
                msg = await flood_safe(lambda: app.send_message(text=text, disable_web_page_preview=True, **common))
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


async def _find_video(client, group: list, notes: list):
    """(candidate with a YouTube link or None). Order: feed item, article pages, then YouTube search."""
    for cand in group:
        if cand["youtube_url"]:
            cand.setdefault("video_from", "YouTube channel feed" if cand.get("youtube_feed") else "feed item")
            return cand
    for cand in group:
        await _resolve(cand, client, want_video=True, notes=notes)
        if cand["youtube_url"]:
            return cand
    first = group[0]
    if not first["is_media"]:
        return None  # the headline isn't about a video: never guess one by searching
    if not YT_SEARCH_FALLBACK:
        notes.append("search off (YT_SEARCH_FALLBACK=false)")
        return None
    name = first.get("show") or first.get("search_name")
    if not name:
        notes.append("search skipped: no show name in the headline")
        return None
    alts = await _alt_names(client, first)
    kind = first["kind"] if first["kind"] in KIND_NAME else first.get("video_hint") or "trailer"
    found, why = await search_trailer(name, KIND_NAME.get(kind, "Trailer"), first["title_en"], first.get("published"), alts)
    if found:
        first["youtube_url"] = found["url"]
        first["video_from"] = f"YouTube search: {found['title']} ({found.get('channel') or '?'})"
        return first
    notes.append(f"search for {name!r}: {why or 'nothing'}")
    return None


async def _add_video_to_earlier_post(app, db, client, group: list, match: dict) -> bool:
    """An earlier post about this story went out as a photo; another outlet has the video: edit it into the post."""
    video, notes = None, []
    try:
        source = await _find_video(client, group, notes)
        if not source:
            log.warning("TRAILER MISS | %s | (earlier photo post) %s", group[0]["title_en"], " | ".join(notes))
            return False
        video, err = await download_trailer_verbose(source["youtube_url"])
        if not video:
            log.warning("TRAILER MISS | %s | download failed: %s", group[0]["title_en"], (err or "")[:200])
            return False
        media = InputMediaVideo(
            video["path"], caption=match["caption"], parse_mode=ParseMode.HTML, supports_streaming=True,
            duration=video["duration"], width=video["width"] or 0, height=video["height"] or 0)
        await flood_safe(lambda: app.edit_message_media(
            CHANNEL_ID, match["message_id"], media, reply_markup=_keyboard({"link": match["link"]})))
        db.mark_video_added(match["id"])
        log.info("TRAILER OK | %s | added to the earlier post (message %s) from %s", group[0]["title_en"],
                 match["message_id"], source.get("video_from"))
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

    primary, video, notes = first, None, []
    try:
        want_video = DOWNLOAD_TRAILERS and (first["is_media"] or bool(first.get("video_hint")))
        if want_video:
            found = await _find_video(client, group, notes)
            if found and found.get("youtube_feed"):
                # an outlet's article about the same trailer reads better than the video description: use its text
                article = next((c for c in group if not c.get("youtube_feed")), None)
                if article:
                    article["youtube_url"], article["video_from"] = found["youtube_url"], found.get("video_from")
                    found = article
            if found:
                primary = found
                state.last_youtube_url = primary["youtube_url"]
                video, err = await download_trailer_verbose(primary["youtube_url"])
                if not video:
                    hint = explain(err or "")
                    notes.append(f"download failed: {(err or '?')[:160]}" + (f" -> {hint}" if hint else ""))
            else:
                primary = next((c for c in group if c.get("image_url")), first)
        if not video:
            await _resolve(primary, client, want_video=False, notes=notes)
            if PORTRAIT_IMAGES and not primary.get("youtube_feed"):
                pick = await _portrait(primary, client)
                if pick:
                    primary["image_url"], primary["image_bytes"] = pick
        if not primary.get("image_url"):
            primary["image_url"] = next((c["image_url"] for c in group if c.get("image_url")), None)

        text = build_post(primary)
        msg, mode = await _send(app, client, primary, text, _keyboard(primary), video)
        db.add_posted(
            primary["link"], primary["title"], source=primary["source"], kind=primary["kind"] or "news",
            has_video=(mode == "video"), story_tokens=primary["story_tokens"], title_tokens=primary["title_tokens"],
            message_id=getattr(msg, "id", None), sent_as=mode, caption=text,
        )
        _mark_duplicates(db, group, skip=primary)
        log.info("Posted [%s, %s]: %s", primary["kind"] or "news", mode, primary["title_en"])
        if want_video:
            if mode == "video":
                log.info("TRAILER OK | %s | from %s", primary["title_en"], primary.get("video_from", "?"))
            elif not primary["is_media"]:
                log.info("Optional video (article mentions a %s) not found for: %s", primary.get("video_hint"),
                         primary["title_en"])
            else:
                log.warning("TRAILER MISS | %s | sent as %s | %s", primary["title_en"], mode,
                            " | ".join(notes) or "video found but sending it failed")

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
            _failures.pop(primary["link"], None)
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


async def _seed(db, articles: list) -> list:
    """A feed seen for the first time (new install, or a feed added later, like the YouTube channels): mark what it
    has right now as seen instead of posting its whole backlog. -> the articles that may be posted."""
    if not SEED_ON_FIRST_RUN:
        return articles
    known = db.sources_seen()
    keep, seeded = [], defaultdict(int)
    for article in articles:
        if article["source"] in known:
            keep.append(article)
            continue
        try:
            a = await _prepare(article)
            kind, tokens, ttokens = a["kind"], a["story_tokens"], a["title_tokens"]
        except Exception:
            kind, tokens, ttokens = None, None, None
        db.add_posted(article["link"], article["title"], source=article["source"], kind=kind,
                      status="seeded", story_tokens=tokens, title_tokens=ttokens)
        seeded[article["source"]] += 1
    for source, n in seeded.items():
        log.info("New feed %s: marked its %d current items as seen, nothing posted from it this time.", source, n)
    return keep


async def broadcast_news_loop(app, db):
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
                articles = await _seed(db, articles)

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
