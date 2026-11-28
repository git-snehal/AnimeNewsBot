"""Episode-rating posts from IMDb's free daily datasets (datasets.imdbws.com, personal / non-commercial use).

1. Follow the most popular anime airing right now (AniList) and find each one's IMDb series id
   (Wikidata's AniList->IMDb links first, then a title match in IMDb's own title list).
2. Once IMDb's daily files change: read the ratings of those shows' episodes.
3. An episode that got its first votes recently and reaches IMDB_MIN_VOTES votes and IMDB_MIN_RATING gets a post:
   our own portrait card (poster + gold rating) and a short caption. At most IMDB_MAX_PER_DAY a day, one per check.

IMDb's terms for these files require the credit line we add to every post.
"""
import asyncio
import csv
import gzip
import io
import logging
import os
import time
from datetime import datetime

import httpx

from config import (CHANNEL_ID, IMDB_MAX_PER_DAY, IMDB_MIN_RATING, IMDB_MIN_VOTES, IMDB_RATINGS, IMDB_TRACK_SHOWS,
                    IMDB_WINDOW_DAYS, SIGNATURE, TMDB_API_KEY, TZ)
from helper import anilist, tmdb
from helper.cards import rating_card
from helper.publish import esc, fetch_bytes, keyboard, send_image, signature_html

log = logging.getLogger(__name__)

BASE = "https://datasets.imdbws.com/"
DATA_DIR = os.path.join("database", "imdb")
WIKIDATA = "https://query.wikidata.org/sparql"
WD_QUERY = "SELECT ?anilist ?imdb WHERE { ?item wdt:P8729 ?anilist; wdt:P345 ?imdb. }"
CREDIT = "Information courtesy of IMDb (https://www.imdb.com). Used with permission."
CHECK_EVERY = 3 * 3600
SHOWS_EVERY = 3 * 86400        # refresh the list of followed shows
BASICS_EVERY = 7 * 86400       # IMDb's big title list, only needed to match new shows


def _path(name):
    return os.path.join(DATA_DIR, name)


async def _download(client, db, name: str, max_age: int = 0) -> bool:
    """Conditional download of one dataset file. max_age: don't even ask when our copy is younger than this."""
    os.makedirs(DATA_DIR, exist_ok=True)
    path = _path(name)
    if max_age and os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        return False
    url = BASE + name
    meta = db.get_meta("http:" + url, {}) if os.path.exists(path) else {}
    headers = {"User-Agent": "AnimeNewsBot/3.0"}
    if meta.get("modified"):
        headers["If-Modified-Since"] = meta["modified"]
    async with client.stream("GET", url, headers=headers, timeout=600, follow_redirects=True) as r:
        if r.status_code == 304:
            os.utime(path)
            return False
        r.raise_for_status()
        with open(path + ".part", "wb") as f:
            async for chunk in r.aiter_bytes(1 << 17):
                f.write(chunk)
        db.set_meta("http:" + url, {"modified": r.headers.get("last-modified")})
    os.replace(path + ".part", path)
    log.info("IMDb: downloaded %s", name)
    return True


# ------------------------------------------------------------------ which shows, which IMDb ids
async def _wikidata_map(client, db) -> dict:
    """{anilist_id: imdb_id} from Wikidata, refreshed weekly."""
    cached = db.cache_get("wikidata_anilist_imdb", max_age=7 * 86400)
    if cached is not None:
        return {int(k): v for k, v in cached.items()}
    try:
        r = await client.get(WIKIDATA, params={"query": WD_QUERY}, timeout=90,
                             headers={"Accept": "text/csv", "User-Agent": "AnimeNewsBot/3.0 (https://github.com/; Telegram anime news bot) httpx"})
        r.raise_for_status()
        out = {}
        for row in csv.DictReader(io.StringIO(r.text)):
            if row.get("anilist", "").isdigit() and row.get("imdb", "").startswith("tt"):
                out[int(row["anilist"])] = row["imdb"]
        db.cache_set("wikidata_anilist_imdb", out)
        return out
    except Exception as e:
        log.warning("IMDb: Wikidata lookup failed (%s), using title matching only", e)
        old = db.cache_get("wikidata_anilist_imdb")
        return {int(k): v for k, v in (old or {}).items()}


def _basics_index(path: str):
    """({normalized title: [(imdb_id, start_year)]}, {imdb_id: {normalized titles}}) for animated series in IMDb's
    title list (runs in a thread)."""
    idx, titles = {}, {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        next(f, None)
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) < 9 or p[1] not in ("tvSeries", "tvMiniSeries") or "Animation" not in p[8]:
                continue
            year = int(p[5]) if p[5].isdigit() else None
            for t in {p[2], p[3]}:
                key = anilist.norm(t)
                if key:
                    idx.setdefault(key, []).append((p[0], year))
                    titles.setdefault(p[0], set()).add(key)
    return idx, titles


def _names_match(titles: set, chain: list) -> bool:
    return bool(titles & {anilist.norm(n) for m in chain for n in anilist.names(m)})


def _match_basics(idx: dict, chain: list):
    """IMDb id for a show: try the airing season first, then its prequels one by one, and only accept an IMDb series
    that started within a year of THAT season. So 'Dragon Ball Super: Beerus' finds Dragon Ball Super (2015),
    never the original Dragon Ball (1986), and 'Frieren Season 2' (2026) finds the 2023 series via season 1."""
    for media in chain:
        year = (media.get("startDate") or {}).get("year")
        for name in anilist.names(media):
            for tt, y in idx.get(anilist.norm(name), []):
                if year is not None and y is not None and abs(y - year) <= 1:
                    return tt
    return None


async def tracked_shows(client, db, force=False) -> list:
    """[{anilist_id, imdb, name, cover, banner}] for the most popular airing anime. Cached for a few days.

    An IMDb id is only trusted when IMDb's own title for it is one of the show's names: Wikidata links are checked
    against IMDb's title list (a wrong link once mapped a spin-off to One Piece), then title matching fills the gaps."""
    cached = db.get_meta("imdb_shows")
    if cached and not force and time.time() - cached["at"] < SHOWS_EVERY:
        return cached["shows"]
    airing = await anilist.airing(client, IMDB_TRACK_SHOWS)
    wd = await _wikidata_map(client, db)
    await _download(client, db, "title.basics.tsv.gz", max_age=BASICS_EVERY)
    idx, titles = (await asyncio.to_thread(_basics_index, _path("title.basics.tsv.gz"))
                   if os.path.exists(_path("title.basics.tsv.gz")) else ({}, {}))
    shows = []
    for media in airing:
        chain = await anilist.prequel_chain(client, media)
        entry = {"anilist_id": media["id"], "name": anilist.display_name(media) or anilist.display_name(chain[-1]),
                 "cover": (media.get("coverImage") or {}).get("extraLarge"), "banner": media.get("bannerImage"),
                 "imdb": None, "how": None}
        for m in chain:                                    # airing season first, then its prequels
            tt = wd.get(m["id"])
            if tt and (not titles or _names_match(titles.get(tt, set()), chain)):
                entry["imdb"], entry["how"] = tt, "wikidata"
                break
        if not entry["imdb"] and idx:
            entry["imdb"] = _match_basics(idx, chain)
            entry["how"] = "title" if entry["imdb"] else None
        shows.append(entry)
    del idx, titles
    missing = [e["name"] for e in shows if not e["imdb"]]
    log.info("IMDb: following %d airing shows, %d matched to IMDb%s", len(shows), len(shows) - len(missing),
             f" (no IMDb match: {', '.join(missing[:12])})" if missing else "")
    db.set_meta("imdb_shows", {"at": time.time(), "shows": shows})
    return shows


# ------------------------------------------------------------------ episode ratings
def _read_episodes(ep_path: str, rt_path: str, parents: set) -> dict:
    """{episode_id: (parent, season, episode, rating, votes)} for rated episodes of the followed shows (thread)."""
    eps = {}
    with gzip.open(ep_path, "rt", encoding="utf-8") as f:
        next(f, None)
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) == 4 and p[1] in parents and p[2].isdigit() and p[3].isdigit():
                eps[p[0]] = (p[1], int(p[2]), int(p[3]))
    out = {}
    with gzip.open(rt_path, "rt", encoding="utf-8") as f:
        next(f, None)
        for line in f:
            p = line.rstrip("\n").split("\t")
            if len(p) == 3 and p[0] in eps:
                out[p[0]] = (*eps[p[0]], float(p[1]), int(p[2]))
    return out


def _store(db, ratings: dict) -> int:
    """Save ratings. A show seen for the first time: its older episodes are history (never posted), only its newest
    two count as new. -> number of newly rated episodes."""
    now = int(time.time())
    c = db.conn
    known_parents = {r[0] for r in c.execute("SELECT DISTINCT parent FROM imdb_episodes")}
    known_eps = {r[0] for r in c.execute("SELECT ep_id FROM imdb_episodes")}
    by_parent = {}
    for ep, row in ratings.items():
        by_parent.setdefault(row[0], []).append((ep, row))
    new = 0
    for parent, rows in by_parent.items():
        fresh = set()
        if parent not in known_parents:
            top = max(r[1] for _, r in rows)
            latest = sorted((x for x in rows if x[1][1] == top), key=lambda x: x[1][2])
            fresh = {ep for ep, _ in latest[-2:]}
        for ep, (par, season, episode, rating, votes) in rows:
            if ep in known_eps:
                c.execute("UPDATE imdb_episodes SET rating = ?, votes = ? WHERE ep_id = ?", (rating, votes, ep))
                continue
            first = now if (parent in known_parents or ep in fresh) else 0
            new += bool(first)
            c.execute("INSERT INTO imdb_episodes (ep_id, parent, season, episode, first_rated, rating, votes) "
                      "VALUES (?, ?, ?, ?, ?, ?, ?)", (ep, par, season, episode, first, rating, votes))
    c.commit()
    return new


def candidates(db, ignore_rules=False) -> list:
    cols = ("ep_id", "parent", "season", "episode", "first_rated", "rating", "votes", "posted_at")
    if ignore_rules:
        sql, args = ("SELECT * FROM imdb_episodes e WHERE first_rated > 0 "
                     "AND season = (SELECT MAX(season) FROM imdb_episodes x WHERE x.parent = e.parent) "
                     "ORDER BY rating DESC, votes DESC LIMIT 10"), ()
    else:
        sql = ("SELECT * FROM imdb_episodes e WHERE posted_at IS NULL AND first_rated >= ? AND votes >= ? AND rating >= ? "
               "AND season = (SELECT MAX(season) FROM imdb_episodes x WHERE x.parent = e.parent) "
               "ORDER BY rating DESC, votes DESC")
        args = (int(time.time()) - IMDB_WINDOW_DAYS * 86400, IMDB_MIN_VOTES, IMDB_MIN_RATING)
    return [dict(zip(cols, r)) for r in db.conn.execute(sql, args)]


def _season_note(db, ep: dict) -> str:
    rows = db.conn.execute("SELECT ep_id, rating FROM imdb_episodes WHERE parent = ? AND season = ? AND votes >= ?",
                           (ep["parent"], ep["season"], max(50, IMDB_MIN_VOTES // 3))).fetchall()
    if len(rows) >= 3 and ep["rating"] >= max(r[1] for r in rows):
        return ", the highest-rated episode of the season so far"
    return ""


async def refresh(client, db, force_shows=False) -> dict:
    shows = await tracked_shows(client, db, force=force_shows)
    parents = {s["imdb"] for s in shows if s["imdb"]}
    changed = await _download(client, db, "title.ratings.tsv.gz", max_age=6 * 3600)
    changed = await _download(client, db, "title.episode.tsv.gz", max_age=20 * 3600) or changed
    new = 0
    if changed or force_shows or not db.conn.execute("SELECT 1 FROM imdb_episodes LIMIT 1").fetchone():
        ratings = await asyncio.to_thread(_read_episodes, _path("title.episode.tsv.gz"),
                                          _path("title.ratings.tsv.gz"), parents)
        new = _store(db, ratings)
        log.info("IMDb: %d rated episodes of %d shows, %d newly rated", len(ratings), len(parents), new)
    return {"shows": shows, "new": new}


async def build_post(client, db, ep: dict, shows: list):
    show = next((s for s in shows if s["imdb"] == ep["parent"]), None) or {"name": ep["parent"]}
    poster_url = await tmdb.poster_for_imdb(client, ep["parent"]) if TMDB_API_KEY else None
    poster = await fetch_bytes(client, poster_url) if poster_url else None
    poster = poster or await fetch_bytes(client, show.get("cover")) or await fetch_bytes(client, show.get("banner"))
    if not poster:
        raise RuntimeError(f"no poster for {show['name']}")
    # long runners (One Piece, Conan) are all "season 1" on IMDb: just say the episode number
    season = None if ep["season"] == 1 and ep["episode"] > 50 else ep["season"]
    image = await asyncio.to_thread(rating_card, poster, show["name"], season, ep["episode"], ep["rating"],
                                    ep["votes"], SIGNATURE)
    where = f"Season {season}, Episode {ep['episode']}" if season else f"Episode {ep['episode']}"
    caption = "\n\n".join([
        f"✦ <b>{esc(show['name'])}</b> — <i>Episode Rating</i>",
        f"<blockquote>{where} is rated <b>{ep['rating']:.1f}/10</b> on IMDb "
        f"from {ep['votes']:,} votes{_season_note(db, ep)}.</blockquote>",
        f"<b>IMDb</b> • <b>{ep['votes']:,} votes</b>",
        signature_html(),
        f"<i>{esc(CREDIT)}</i>",
    ])
    url = f"https://www.imdb.com/title/{ep['ep_id']}/"
    return image, caption, url, show


def _today_start() -> int:
    now = datetime.now(TZ)
    return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


async def post_next(app, db, client) -> bool:
    if db.posted_today("rating", _today_start()) >= IMDB_MAX_PER_DAY:
        return False
    shows = (db.get_meta("imdb_shows") or {}).get("shows", [])
    for ep in candidates(db):
        try:
            image, caption, url, show = await build_post(client, db, ep, shows)
        except Exception as e:
            log.warning("IMDb: could not build the post for %s: %s", ep["ep_id"], e)
            db.conn.execute("UPDATE imdb_episodes SET posted_at = -1 WHERE ep_id = ?", (ep["ep_id"],))
            db.conn.commit()
            continue
        msg = await send_image(app, CHANNEL_ID, image, caption, keyboard([("⭐ IMDb", url)]), silent=True,
                               name="rating.jpg")
        db.conn.execute("UPDATE imdb_episodes SET posted_at = ? WHERE ep_id = ?", (int(time.time()), ep["ep_id"]))
        db.conn.commit()
        db.add_posted(url, f"{show['name']} S{ep['season']}E{ep['episode']} IMDb rating", source="IMDb ratings",
                      kind="rating", message_id=getattr(msg, "id", None), sent_as="photo_upload", caption=caption)
        log.info("IMDb rating posted: %s S%sE%s %.1f (%d votes)", show["name"], ep["season"], ep["episode"],
                 ep["rating"], ep["votes"])
        return True
    return False


async def imdb_loop(app, db):
    if not IMDB_RATINGS:
        return
    await asyncio.sleep(150)
    async with httpx.AsyncClient() as client:
        while True:
            try:
                from helper.state import state
                await refresh(client, db)
                if not state.paused:
                    await post_next(app, db, client)
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("IMDb ratings job failed")
            await asyncio.sleep(CHECK_EVERY)


async def test_report(app, db, client, chat_id) -> str:
    """/imdbtest: refresh, then show what is followed and send the best recent episode's post to this chat."""
    res = await refresh(client, db, force_shows=True)
    shows = res["shows"]
    matched = [s for s in shows if s["imdb"]]
    lines = [f"⭐ <b>IMDb ratings</b>", f"Following {len(shows)} airing shows, {len(matched)} matched to IMDb."]
    missing = [s["name"] for s in shows if not s["imdb"]]
    if missing:
        lines.append("No IMDb match: " + esc(", ".join(missing[:15])) + (" …" if len(missing) > 15 else ""))
    ready = candidates(db)
    lines.append(f"Ready to post now (≥{IMDB_MIN_RATING}, ≥{IMDB_MIN_VOTES} votes, first rated in the last "
                 f"{IMDB_WINDOW_DAYS} days): {len(ready)}")
    best = ready[:1] or candidates(db, ignore_rules=True)[:1]
    if best:
        image, caption, url, show = await build_post(client, db, best[0], shows)
        await send_image(app, chat_id, image, caption, keyboard([("⭐ IMDb", url)]), name="rating.jpg")
        lines.append(f"Preview sent: {esc(show['name'])} S{best[0]['season']}E{best[0]['episode']} "
                     f"({best[0]['rating']:.1f}, {best[0]['votes']:,} votes)"
                     + ("" if ready else " — below the posting rules, shown only as a preview"))
    else:
        lines.append("No recently rated episodes yet (the first check marks older episodes as history).")
    return "\n".join(lines)
