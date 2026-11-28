"""Weekly 'Anime in the Netflix Top 10' post, from Netflix's official data (top10.netflix.com).

Netflix publishes every Tuesday: the Top 10 TV shows and films of the past week (Mon-Sun) in ~90 countries. Anime
rarely reaches the worldwide list, so we look at every country's list, keep the anime (checked against AniList),
and rank them by how many countries they charted in.
"""
import asyncio
import csv
import logging
import os
from datetime import date, timedelta

import httpx

from config import NETFLIX_MAX_ROWS, NETFLIX_TOP10, SIGNATURE, TMDB_API_KEY
from helper import anilist, tmdb
from helper.cards import netflix_chart
from helper.publish import esc, fetch_bytes, keyboard, send_image, signature_html

log = logging.getLogger(__name__)

COUNTRIES_URL = "https://www.netflix.com/tudum/top10/data/all-weeks-countries.tsv"
GLOBAL_URL = "https://www.netflix.com/tudum/top10/data/all-weeks-global.tsv"
DATA_DIR = os.path.join("database", "netflix")
TOP10_PAGE = "https://www.netflix.com/tudum/top10"
CHECK_EVERY = 6 * 3600


async def _download(client: httpx.AsyncClient, db, url: str, path: str) -> bool:
    """Conditional download (only when Netflix changed the file). -> True when a new file was saved."""
    os.makedirs(DATA_DIR, exist_ok=True)
    meta = db.get_meta("http:" + url, {}) if os.path.exists(path) else {}
    headers = {"User-Agent": "Mozilla/5.0 (compatible; AnimeNewsBot/3.0)"}
    if meta.get("etag"):
        headers["If-None-Match"] = meta["etag"]
    if meta.get("modified"):
        headers["If-Modified-Since"] = meta["modified"]
    tmp = path + ".part"
    async with client.stream("GET", url, headers=headers, timeout=120, follow_redirects=True) as r:
        if r.status_code == 304:
            return False
        r.raise_for_status()
        with open(tmp, "wb") as f:
            async for chunk in r.aiter_bytes(1 << 16):
                f.write(chunk)
        db.set_meta("http:" + url, {"etag": r.headers.get("etag"), "modified": r.headers.get("last-modified")})
    os.replace(tmp, path)
    return True


def _latest_week(path: str):
    """(week, rows of that week) from the countries file."""
    week, rows = None, []
    with open(path, encoding="utf-8", newline="") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            w = r.get("week")
            if not w:
                continue
            if week is None or w > week:
                week, rows = w, [r]
            elif w == week:
                rows.append(r)
    return week, rows


def _english_titles(path: str, week: str) -> set:
    """Titles that Netflix files as English-language this week: no need to ask AniList about them."""
    out = set()
    try:
        with open(path, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f, delimiter="\t"):
                if r.get("week") == week and "(English)" in (r.get("category") or ""):
                    out.add(r["show_title"])
    except OSError:
        pass
    return out


def week_label(week: str) -> str:
    end = date.fromisoformat(week)
    start = end - timedelta(days=6)
    if start.month == end.month:
        return f"{start.strftime('%b')} {start.day} – {end.day}, {end.year}"
    if start.year == end.year:
        return f"{start.strftime('%b')} {start.day} – {end.strftime('%b')} {end.day}, {end.year}"
    return f"{start.strftime('%b')} {start.day}, {start.year} – {end.strftime('%b')} {end.day}, {end.year}"


async def _is_anime(client, db, title: str, is_film: bool):
    """Cached AniList check. -> AniList media dict, or None."""
    key = "nf2:" + anilist.norm(title) + (":film" if is_film else ":tv")
    hit = db.cache_get(key)
    if hit is not None and (hit.get("media") or db.cache_get(key, max_age=30 * 86400) is not None):
        return hit.get("media")
    media = await anilist.exact_title(client, title, is_film)
    db.cache_set(key, {"media": media})
    return media


async def collect(client, db, rows: list, english: set) -> list:
    """Anime in this week's lists, most countries first."""
    titles = {}
    for r in rows:
        titles.setdefault(r["show_title"], []).append(r)
    found = []
    for title, rs in titles.items():
        if title in english:
            continue
        is_film = rs[0]["category"] == "Films"
        try:
            media = await _is_anime(client, db, title, is_film)
        except Exception as e:
            log.info("Netflix: AniList check failed for %r (%s), skipped this week", title, e)
            continue
        if not media:
            continue
        rs.sort(key=lambda r: int(r["weekly_rank"]))
        season = (rs[0].get("season_title") or "N/A")
        sub = "Film" if is_film or season == "N/A" else season.split(": ")[-1]
        extra = None
        if TMDB_API_KEY:
            extra = db.cache_get("tmdb:" + anilist.norm(title) + (":film" if is_film else ":tv"), max_age=90 * 86400)
            if extra is None:
                extra = await tmdb.classify(client, title, is_film) or {}
                db.cache_set("tmdb:" + anilist.norm(title) + (":film" if is_film else ":tv"), extra)
        if extra and extra.get("animated") is False:
            sub += " • Live-action"
        countries = sorted({r["country_name"] for r in rs})
        found.append({
            "title": title, "sub": sub, "countries": len(countries), "best": int(rs[0]["weekly_rank"]),
            "best_country": rs[0]["country_name"], "country_list": countries,
            "weeks": max(int(r.get("cumulative_weeks_in_top_10") or 1) for r in rs),
            "poster_url": (extra or {}).get("poster") or (media.get("coverImage") or {}).get("extraLarge"),
            "anilist": media,
        })
    found.sort(key=lambda x: (-x["countries"], x["best"], x["title"]))
    return found


def _caption(items: list, week: str) -> str:
    lines = []
    for i, it in enumerate(items, 1):
        c = it["countries"]
        lines.append(f"{i}. <b>{esc(it['title'])}</b> ({esc(it['sub'])}) — Top 10 in {c} "
                     f"{'country' if c == 1 else 'countries'}, best #{it['best']} in {esc(it['best_country'])}")
    return "\n\n".join([
        "✦ <b>Netflix Top 10</b> — <i>Anime This Week</i>",
        "<blockquote expandable>" + "\n".join(lines) + "</blockquote>" if len(lines) > 3 else
        "<blockquote>" + "\n".join(lines) + "</blockquote>",
        f"<b>{esc(week_label(week))}</b> • <b>Netflix</b>",
        signature_html(),
    ])


async def build(client, db):
    """-> {"week", "items", "image", "caption"} for the newest week, or {"week", "items": []} when no anime charted."""
    await _download(client, db, COUNTRIES_URL, os.path.join(DATA_DIR, "countries.tsv"))
    await _download(client, db, GLOBAL_URL, os.path.join(DATA_DIR, "global.tsv"))
    week, rows = await asyncio.to_thread(_latest_week, os.path.join(DATA_DIR, "countries.tsv"))
    english = await asyncio.to_thread(_english_titles, os.path.join(DATA_DIR, "global.tsv"), week)
    items = (await collect(client, db, rows, english))[:NETFLIX_MAX_ROWS]
    if not items:
        return {"week": week, "items": []}
    cards = []
    for it in items:
        c = it["countries"]
        cards.append({
            "title": it["title"], "sub": it["sub"], "poster": await fetch_bytes(client, it["poster_url"]),
            "line1": f"Top 10 in {c} {'country' if c == 1 else 'countries'}",
            "line2": f"Best: #{it['best']} in {it['best_country']}",
            "line3": ", ".join(it["country_list"]),
        })
    image = await asyncio.to_thread(
        netflix_chart, cards, "ANIME IN THE NETFLIX TOP 10", f"Week of {week_label(week)}", SIGNATURE,
        "Data: Netflix Top 10 (top10.netflix.com)  •  Images: " + ("TMDB / AniList" if TMDB_API_KEY else "AniList"))
    return {"week": week, "items": items, "image": image, "caption": _caption(items, week)}


async def post_week(app, db, client, chat_id, force=False):
    """Post the newest week if it wasn't posted yet (or always, with force=True for /netflixtest)."""
    res = await build(client, db)
    if not force and res["week"] == db.get_meta("netflix_last_week"):
        return None
    if not res["items"]:
        log.info("Netflix week %s: no anime in any country's Top 10, nothing to post", res["week"])
        if not force:
            db.set_meta("netflix_last_week", res["week"])
        return res
    msg = await send_image(app, chat_id, res["image"], res["caption"], keyboard([("🔗 Netflix Top 10", TOP10_PAGE)]),
                           silent=True, name="netflix_top10.jpg")
    if not force:
        db.set_meta("netflix_last_week", res["week"])
        db.add_posted(f"netflix-top10:{res['week']}", f"Netflix Top 10 anime {res['week']}", source="Netflix Top 10",
                      kind="netflix", message_id=getattr(msg, "id", None), sent_as="photo_upload", caption=res["caption"])
        log.info("Netflix Top 10 posted for week %s (%d anime)", res["week"], len(res["items"]))
    return res


async def netflix_loop(app, db):
    from config import CHANNEL_ID
    if not NETFLIX_TOP10:
        return
    await asyncio.sleep(90)
    async with httpx.AsyncClient() as client:
        while True:
            try:
                from helper.state import state
                if not state.paused:
                    await post_week(app, db, client, CHANNEL_ID)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Netflix Top 10 job failed")
            await asyncio.sleep(CHECK_EVERY)
