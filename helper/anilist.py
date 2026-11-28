"""AniList (free GraphQL API, no key): every name a show goes by, posters, and what is airing right now.

Used by the trailer search (match English / romaji names), the Netflix chart (is this title an anime?) and the
IMDb rating posts (which shows to follow, and their posters). Rate limit is 90 requests/minute; we stay well below.
"""
import asyncio
import logging
import re
import time
import unicodedata

import httpx

log = logging.getLogger(__name__)

API = "https://graphql.anilist.co"
HEADERS = {"User-Agent": "AnimeNewsBot/3.0", "Content-Type": "application/json", "Accept": "application/json"}
FIELDS = ("id format status countryOfOrigin seasonYear startDate{year} title{romaji english native} synonyms "
          "coverImage{extraLarge large color} bannerImage siteUrl popularity")
_lock = asyncio.Lock()
_last = 0.0
_cache = {}  # (query, vars) -> result, for this process


def norm(s: str) -> str:
    t = unicodedata.normalize("NFKD", s or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower().replace("’", "'")
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


_SEASON_SUFFIX = re.compile(
    r"[\s:\-–]*(?:\(?(?:season|part|cour)\s*\d+\)?|\d+(?:st|nd|rd|th)\s+(?:season|part|cour)"
    r"|(?:second|third|fourth|fifth|final)\s+season|season\s+(?:two|three|four)|\bII+\b)\s*$", re.I)


def strip_season(name: str) -> str:
    prev = None
    while name and prev != name:
        prev, name = name, _SEASON_SUFFIX.sub("", name).strip(" :-–")
    return name


def names(media: dict, strip: bool = True) -> list:
    """English, romaji and Latin-script synonyms, each also without a 'Season 2' style suffix (strip=True)."""
    out = []
    t = media.get("title") or {}
    for n in [t.get("english"), t.get("romaji"), *(media.get("synonyms") or [])]:
        if not n or not re.search(r"[A-Za-z]", n):
            continue
        for v in ((n, strip_season(n)) if strip else (n,)):
            if v and v not in out:
                out.append(v)
    return out


def display_name(media: dict) -> str:
    t = media.get("title") or {}
    return strip_season(t.get("english") or t.get("romaji") or "") or (t.get("english") or t.get("romaji") or "")


async def query(client: httpx.AsyncClient, q: str, variables: dict, cache: bool = True):
    global _last
    key = (q, repr(sorted(variables.items())))
    if cache and key in _cache:
        return _cache[key]
    async with _lock:
        for attempt in range(4):
            wait = 0.8 - (time.monotonic() - _last)
            if wait > 0:
                await asyncio.sleep(wait)
            _last = time.monotonic()
            try:
                r = await client.post(API, json={"query": q, "variables": variables}, headers=HEADERS, timeout=30)
            except httpx.TransportError as e:
                log.info("AniList request failed (%s), retrying", type(e).__name__)
                await asyncio.sleep(3 * (attempt + 1))
                continue
            if r.status_code == 429:
                await asyncio.sleep(int(r.headers.get("Retry-After", "30")) + 1)
                continue
            if r.status_code >= 500:
                await asyncio.sleep(3 * (attempt + 1))
                continue
            r.raise_for_status()
            data = r.json().get("data")
            if cache:
                _cache[key] = data
                if len(_cache) > 2000:
                    _cache.clear()
            return data
    raise RuntimeError("AniList did not answer")


async def search(client, name: str, per_page: int = 5) -> list:
    q = f"query($s:String,$n:Int){{Page(perPage:$n){{media(search:$s,type:ANIME,sort:SEARCH_MATCH){{{FIELDS}}}}}}}"
    data = await query(client, q, {"s": name[:100], "n": per_page})
    return (data or {}).get("Page", {}).get("media") or []


async def find_show(client, name: str):
    """The anime a headline's show name refers to: a search hit that has this exact name (any language/synonym),
    else the top hit if its main title shares most words with the name. None when unsure."""
    if not name:
        return None
    try:
        hits = await search(client, name)
    except Exception as e:
        log.info("AniList lookup failed for %r: %s", name, e)
        return None
    want = norm(strip_season(name))
    for h in hits:
        if want in {norm(n) for n in names(h)}:
            return h
    if hits:
        a, b = set(want.split()), set(norm(display_name(hits[0])).split())
        if a and b and len(a & b) / min(len(a), len(b)) >= 0.8:
            return hits[0]
    return None


_FILM_FORMATS = {"MOVIE", "SPECIAL", "ONA", "OVA"}
_TV_FORMATS = {"TV", "TV_SHORT", "ONA"}


async def exact_title(client, title: str, is_film: bool = None, min_popularity: int = 2000):
    """Netflix titles: an anime (JP / CN / KR) whose English, romaji or synonym title is exactly this one, of the
    right kind (film / series; never a music video) and known to enough AniList users. Full names only: 'Lupin'
    must not match 'Lupin III'."""
    try:
        hits = await search(client, title)
    except Exception as e:
        log.info("AniList lookup failed for %r: %s", title, e)
        raise
    want = norm(title)
    for h in hits:
        if h.get("countryOfOrigin") not in ("JP", "CN", "KR") or (h.get("popularity") or 0) < min_popularity:
            continue
        fmt = h.get("format")
        if is_film is True and fmt not in _FILM_FORMATS or is_film is False and fmt not in _TV_FORMATS \
                or fmt not in _FILM_FORMATS | _TV_FORMATS:
            continue
        if want in {norm(n) for n in names(h, strip=False)}:
            return h
    return None


async def airing(client, count: int = 60) -> list:
    """The most popular anime airing right now (TV / ONA), with their prequels so seasons can be traced back."""
    q = (f"query($p:Int){{Page(page:$p,perPage:50){{media(type:ANIME,status:RELEASING,format_in:[TV,ONA,TV_SHORT],"
         f"sort:POPULARITY_DESC){{{FIELDS} relations{{edges{{relationType node{{id type format title{{romaji english}} "
         f"synonyms startDate{{year}}}}}}}}}}}}}}")
    out, page = [], 1
    while len(out) < count and page <= 3:
        data = await query(client, q, {"p": page}, cache=False)
        media = (data or {}).get("Page", {}).get("media") or []
        if not media:
            break
        out += [m for m in media if m.get("countryOfOrigin") in ("JP", "CN", "KR")]
        page += 1
    return out[:count]


async def prequel_chain(client, media: dict, max_hops: int = 6) -> list:
    """[media, its prequel, that one's prequel, ...] for TV seasons: IMDb lists all seasons under the first one."""
    chain, cur = [media], media
    q = (f"query($id:Int){{Media(id:$id){{{FIELDS} relations{{edges{{relationType node{{id type format title{{romaji english}} "
         f"synonyms startDate{{year}}}}}}}}}}}}")
    for _ in range(max_hops):
        edges = ((cur.get("relations") or {}).get("edges")) or []
        prev = next((e["node"] for e in edges if e.get("relationType") == "PREQUEL"
                     and e["node"].get("type") == "ANIME" and e["node"].get("format") in ("TV", "ONA", "TV_SHORT")), None)
        if not prev or any(c["id"] == prev["id"] for c in chain):
            break
        try:
            data = await query(client, q, {"id": prev["id"]})
        except Exception:
            break
        cur = (data or {}).get("Media")
        if not cur:
            break
        chain.append(cur)
    return chain
