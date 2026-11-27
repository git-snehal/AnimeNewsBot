import asyncio
import html
import logging
import os
import shutil
from contextlib import suppress
from datetime import datetime, timedelta, timezone

import httpx
from pyrogram import Client, filters, idle
from pyrogram.enums import ParseMode

from config import (
    ADMIN_IDS, API_HASH, API_ID, BOT_TOKEN, COOKIES_PATH, DAILY_STATS, DOWNLOAD_TRAILERS,
    PAGE_VIDEO_FALLBACK, POLL_INTERVAL, RSS_FEEDS, TIMEZONE, TZ,
)
from database.db import Database
from helper.downloader import cleanup, cookie_summary, environment_info, runtime_warnings, set_active_cfg
from helper.fetcher import fetch_feed
from helper.commands import help_text, register_commands, set_admin_menu
from helper.news_job import broadcast_news_loop
from helper.startup import start_with_retry
from helper.state import state
from helper.updater import maybe_auto_update, report as update_report, restart_process, run_update
from helper.stats import daily_stats_loop, day_report, week_report
from helper.parser import extract_youtube_url
from helper.trailer_test import run_probe_report, run_trailer_test

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("bot")

app = Client("anime_news_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
db = Database()
admin_only = filters.user(ADMIN_IDS)


def _is_admin(message) -> bool:
    return bool(message.from_user and message.from_user.id in ADMIN_IDS)


def _ago(dt) -> str:
    if not dt:
        return "never"
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    if secs < 60:
        return f"{secs}s ago"
    if secs < 3600:
        return f"{secs // 60} min ago"
    return f"{secs // 3600}h {(secs % 3600) // 60}m ago"


# ------------------------------------------------------------------ everyone
@app.on_message(filters.private & filters.command("start"))
async def start_cmd(client, message):
    if _is_admin(message):
        await set_admin_menu(client, message.chat.id)  # makes sure this admin's "/" menu is up to date
        await message.reply_text("❖ Anime News Bot — admin menu is ready. Type / to see the commands.\n\n" + help_text(True))
    else:
        await message.reply_text("❖ Anime News Bot posts the latest anime news to the channel.")


@app.on_message(filters.command("help"))
async def help_cmd(client, message):
    await message.reply_text(help_text(_is_admin(message)))


@app.on_message(filters.command("ping"))
async def ping_cmd(client, message):
    await message.reply_text("❖ Anime News Bot is active and running!")


# ------------------------------------------------------------------ admins
@app.on_message(admin_only & filters.command("stats"))
async def stats_cmd(client, message):
    arg = (message.command[1].lower() if len(message.command) > 1 else "today")
    today = datetime.now(TZ).date()
    if arg in ("week", "7", "7d"):
        text = week_report(db, today)
    elif arg in ("yesterday", "y"):
        d = today - timedelta(days=1)
        text = day_report(db, d, title=f"Yesterday — {d.strftime('%d %b %Y')}")
    else:
        text = day_report(db, today, title=f"Today so far — {today.strftime('%d %b %Y')}")
    await message.reply_text(text, parse_mode=ParseMode.HTML)


@app.on_message(admin_only & filters.command("status"))
async def status_cmd(client, message):
    up = datetime.now(timezone.utc) - state.started_at
    lines = [
        "⏸ <b>Paused</b>" if state.paused else "🟢 <b>Running</b>",
        f"Uptime: {up.days}d {up.seconds // 3600}h {(up.seconds % 3600) // 60}m",
        f"Last check: {_ago(state.last_cycle_at)} ({state.last_cycle_found} new found)",
        f"Checks every {POLL_INTERVAL // 60} min · timezone {TIMEZONE}",
        "",
        "<b>Feeds</b>",
    ]
    for name, f in state.feeds.items():
        lines.append(f"   {'✅' if f['ok'] else '❌'} {name} — " + (f"{f['entries']} entries" if f["ok"] else f["error"] or "failed"))
    if not state.feeds:
        lines.append("   (not checked yet)")
    lines += [
        "",
        f"Cookies: {'✅ present' if os.path.exists(COOKIES_PATH) else '❌ none (set with /setcookies)'}",
        f"Trailer downloads: {'on' if DOWNLOAD_TRAILERS else 'off'} · page lookup: {'on' if PAGE_VIDEO_FALLBACK else 'off'}",
        f"Daily report: {'on' if DAILY_STATS else 'off'}",
        f"Posted all-time: {db.total_posted()}",
    ]
    await message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)


@app.on_message(admin_only & filters.command("feeds"))
async def feeds_cmd(client, message):
    status = await message.reply_text("⏳ Checking all feeds…")
    async with httpx.AsyncClient() as http:
        await asyncio.gather(*(fetch_feed(http, n, u, force=True) for n, u in RSS_FEEDS.items()))
    lines = ["📡 <b>Feeds</b>", ""]
    for name, f in state.feeds.items():
        if f["ok"]:
            host = f["url"].split("/")[2] if "//" in f["url"] else f["url"]
            lines.append(f"✅ <b>{html.escape(name)}</b> — {f['entries']} entries ({html.escape(host)})")
        else:
            lines.append(f"❌ <b>{html.escape(name)}</b> — {html.escape(f['error'])}")
            lines.append(f"      tried: {html.escape(', '.join(f['attempts']) or '-')}")
    await status.edit_text("\n".join(lines), parse_mode=ParseMode.HTML)


async def _restart_soon(client, delay: float = 3):
    await asyncio.sleep(delay)
    try:
        await client.stop(block=False)
    except Exception:
        pass
    restart_process()


@app.on_message(admin_only & filters.command("updateytdlp"))
async def updateytdlp_cmd(client, message):
    status = await message.reply_text("⏳ Updating yt-dlp and the YouTube solver… this can take a couple of minutes.")
    res = await run_update(pre=False if "stable" in message.command[1:] else None)
    text = update_report(res)
    if res["ok"] and res["changed"]:
        text += "\n\n🔄 Restarting the bot to load the new version…"
    elif res["ok"]:
        text += "\n\nNothing new to install. If downloads still fail, run /ytprobe."
    await status.edit_text(text, parse_mode=ParseMode.HTML)
    if res["ok"] and res["changed"]:
        await _restart_soon(client)


async def ytdlp_update_loop(client):
    """Once a day (and shortly after start): update yt-dlp; restart only when something actually changed."""
    await asyncio.sleep(45)
    while True:
        try:
            res = await maybe_auto_update()
            if res and res["ok"] and res["changed"]:
                for admin in ADMIN_IDS:
                    with suppress(Exception):
                        await client.send_message(admin, "🔄 yt-dlp was updated automatically:\n" + update_report(res)
                                                  + "\nRestarting now.", parse_mode=ParseMode.HTML)
                await _restart_soon(client, 2)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("yt-dlp auto-update failed")
        await asyncio.sleep(6 * 3600)


@app.on_message(admin_only & filters.command("ytprobe"))
async def ytprobe_cmd(client, message):
    arg = message.command[1] if len(message.command) > 1 else ""
    url = extract_youtube_url(arg) or state.last_youtube_url or "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    status = await message.reply_text("⏳ Trying different YouTube setups (JS runtime, client, cookies)… up to a minute.")
    try:
        await status.edit_text(await run_probe_report(url), parse_mode=ParseMode.HTML, disable_web_page_preview=True)
    except Exception as e:
        await status.edit_text(f"❌ Probe crashed: {html.escape(str(e))}")


@app.on_message(admin_only & filters.command("testtrailer"))
async def testtrailer_cmd(client, message):
    args = message.command[1:]
    send = any(a.lower() == "send" for a in args)
    arg = next((a for a in args if a.lower() != "send"), None)
    status = await message.reply_text("⏳ Testing the trailer lookup… this can take up to a minute.")
    video = None
    try:
        async with httpx.AsyncClient() as http:
            text, video = await run_trailer_test(http, arg, send)
        await status.edit_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        if video:
            try:
                await message.reply_video(
                    video["path"], caption="✅ Test download", supports_streaming=True,
                    duration=video["duration"], width=video["width"], height=video["height"])
            except Exception as e:
                await message.reply_text(f"❌ Upload failed: {html.escape(str(e))}")
    except Exception as e:
        await status.edit_text(f"❌ Test crashed: {html.escape(str(e))}")
    finally:
        if video:
            cleanup(video["path"])


@app.on_message(admin_only & filters.command("fetchnow"))
async def fetchnow_cmd(client, message):
    if state.paused:
        await message.reply_text("⏸ Posting is paused. Use /resume first.")
        return
    state.wake.set()
    await message.reply_text("🔄 Checking the feeds now. New posts will appear in the channel shortly.")


@app.on_message(admin_only & filters.command("pause"))
async def pause_cmd(client, message):
    state.paused = True
    await message.reply_text("⏸ Posting paused. Nothing is skipped: new articles are posted after /resume.")


@app.on_message(admin_only & filters.command("resume"))
async def resume_cmd(client, message):
    state.paused = False
    state.wake.set()
    await message.reply_text("▶️ Posting resumed. Checking the feeds now.")


@app.on_message(admin_only & filters.private & filters.command("setcookies"))
async def set_cookies_cmd(client, message):
    doc = message.reply_to_message.document if message.reply_to_message else None
    if not doc:
        await message.reply_text("❖ Reply to an uploaded `cookies.txt` file with /setcookies.")
        return
    if doc.file_size and doc.file_size > 2 * 1024 * 1024:
        await message.reply_text("❌ That file is too large for a cookies file.")
        return
    try:
        tmp = await client.download_media(message.reply_to_message, file_name="downloads/cookies_upload.txt")
        shutil.move(tmp, COOKIES_PATH)
        set_active_cfg(None)          # new cookies: forget the old setup and let the bot search again
        state.last_probe_at = 0
        c = cookie_summary()
        if c.get("logged_in"):
            check = f"✅ It contains a valid Google login ({c['cookies']} cookies)."
        else:
            check = "⚠️ But I can't find a valid Google login in it (exported while logged out, or expired). Export again while logged in."
        await message.reply_text(f"Cookies saved to `{os.path.abspath(COOKIES_PATH)}`.\n{check}")
    except Exception as e:
        await message.reply_text(f"❌ Failed to save cookies: {e}")


async def main():
    await start_with_retry(app, "anime_news_bot")
    await register_commands(app)
    log.info("Bot started. Admins: %s", ADMIN_IDS)
    try:
        for w in runtime_warnings(await asyncio.to_thread(environment_info)):
            log.warning("YouTube downloads: %s", w)
    except Exception:
        log.exception("Could not check the YouTube setup")
    tasks = [
        asyncio.create_task(broadcast_news_loop(app, db)),
        asyncio.create_task(daily_stats_loop(app, db)),
        asyncio.create_task(ytdlp_update_loop(app)),
    ]
    try:
        await idle()
    finally:
        for t in tasks:
            t.cancel()
        for t in tasks:
            with suppress(asyncio.CancelledError):
                await t
        await app.stop()


if __name__ == "__main__":
    app.run(main())
