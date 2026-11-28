"""Optional TMDB lookups (free API key from themoviedb.org, set TMDB_API_KEY). Without a key every function returns
None and the bot uses AniList images instead."""
import logging

import httpx

from config import TMDB_API_KEY
from helper.anilist import norm

log = logging.getLogger(__name__)
API = "https://api.themoviedb.org/3"
IMG = "https://image.tmdb.org/t/p/"
ANIMATION = 16


async def _get(client: httpx.AsyncClient, path: str, **params):
    if not TMDB_API_KEY:
        return None
    try:
        r = await client.get(f"{API}{path}", params={"api_key": TMDB_API_KEY, **params}, timeout=20)
        if r.status_code != 200:
            log.info("TMDB %s answered %s", path, r.status_code)
            return None
        return r.json()
    except Exception as e:
        log.info("TMDB %s failed: %s", path, type(e).__name__)
        return None


async def poster_for_imdb(client, imdb_id: str, size: str = "original"):
    """Poster URL of the TV show / film with this IMDb id."""
    data = await _get(client, f"/find/{imdb_id}", external_source="imdb_id")
    for key in ("tv_results", "movie_results"):
        for res in (data or {}).get(key) or []:
            if res.get("poster_path"):
                return IMG + size + res["poster_path"]
    return None


async def classify(client, title: str, is_film: bool):
    """{"animated": bool, "poster": url|None} for the title with this exact name, or None (no key / not found)."""
    data = await _get(client, "/search/movie" if is_film else "/search/tv", query=title)
    want = norm(title)
    for res in (data or {}).get("results") or []:
        name = res.get("title") or res.get("name") or ""
        orig = res.get("original_title") or res.get("original_name") or ""
        if want in (norm(name), norm(orig)):
            return {"animated": ANIMATION in (res.get("genre_ids") or []),
                    "poster": IMG + "w500" + res["poster_path"] if res.get("poster_path") else None}
    return None
