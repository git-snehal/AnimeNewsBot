"""Daily / on-demand reports."""
import asyncio
import html
import logging
from datetime import date, datetime, timedelta

from pyrogram.enums import ParseMode

from config import ADMIN_IDS, DAILY_STATS, STATS_CHAT_ID, STATS_TIME, TZ

log = logging.getLogger(__name__)


def day_bounds(d: date):
    """Unix timestamps for [00:00, next 00:00) of a calendar day in the configured timezone."""
    start = datetime(d.year, d.month, d.day, tzinfo=TZ)
    nxt = d + timedelta(days=1)
    end = datetime(nxt.year, nxt.month, nxt.day, tzinfo=TZ)
    return int(start.timestamp()), int(end.timestamp())


def _esc(s: str) -> str:
    return html.escape(str(s), quote=False)


def day_report(db, d: date, title: str = None, all_time: bool = True) -> str:
    s = db.stats(*day_bounds(d))
    lines = [f"📊 <b>{_esc(title or d.strftime('%A, %d %b %Y'))}</b>", "", f"📰 Posted: <b>{s['posted']}</b>"]
    if s["posted"]:
        lines.append(f"   🎬 Trailers / teasers / PVs: {s['media']} ({s['with_video']} with video)")
        if s.get("ratings"):
            lines.append(f"   ⭐ IMDb episode ratings: {s['ratings']}")
        if s.get("netflix"):
            lines.append(f"   📺 Netflix Top 10 chart: {s['netflix']}")
        lines.append(f"   🗞 Other news: {s['posted'] - s['media'] - s.get('ratings', 0) - s.get('netflix', 0)}")
        lines += ["", "<b>By source</b>"]
        lines += [f"   • {_esc(src)} — {n}" for src, n in s["by_source"]]
    if s["duplicates"]:
        lines += ["", f"♻️ Duplicates skipped (same story from another outlet): {s['duplicates']}"]
    if s["skipped"]:
        lines += ["", f"⚠️ Skipped after repeated errors: {s['skipped']}"]
    if all_time:
        lines += ["", f"All-time posts: {db.total_posted()}"]
    return "\n".join(lines)


def week_report(db, today: date) -> str:
    lines = ["📊 <b>Last 7 days</b>", ""]
    total = media = 0
    for i in range(6, -1, -1):
        d = today - timedelta(days=i)
        s = db.stats(*day_bounds(d))
        total += s["posted"]
        media += s["media"]
        lines.append(f"   {d.strftime('%a %d %b')} — {s['posted']}" + (f"  (🎬 {s['media']})" if s["media"] else ""))
    lines += ["", f"Total: <b>{total}</b> posts, {media} trailers/teasers/PVs", f"All-time posts: {db.total_posted()}"]
    return "\n".join(lines)


def seconds_until(hhmm: str, now: datetime) -> float:
    h, m = (int(x) for x in hhmm.split(":"))
    target = now.replace(hour=h, minute=m, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


async def daily_stats_loop(app, db):
    if not DAILY_STATS:
        return
    recipients = [STATS_CHAT_ID] if STATS_CHAT_ID else ADMIN_IDS
    while True:
        await asyncio.sleep(seconds_until(STATS_TIME, datetime.now(TZ)))
        try:
            today = datetime.now(TZ).date()
            text = day_report(db, today, title=f"Daily report — {today.strftime('%d %b %Y')}")
            for chat in recipients:
                try:
                    await app.send_message(chat, text, parse_mode=ParseMode.HTML)
                except Exception as e:  # e.g. an admin who never pressed Start
                    log.warning("Could not send daily report to %s: %s", chat, e)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Daily report failed")
        await asyncio.sleep(61)  # make sure we don't fire twice in the same minute
