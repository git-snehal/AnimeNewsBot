"""Offline preview of post formatting (no Telegram, no network).  Run:  python preview.py"""
import os
from datetime import datetime, timezone

for k, v in {"API_ID": "1", "API_HASH": "x", "BOT_TOKEN": "x", "CHANNEL_ID": "@x", "OWNER_ID": "1"}.items():
    os.environ.setdefault(k, v)

from helper.formatter import build_post
from helper.parser import clean_text, enrich

SAMPLES = [
    {
        "title": "Bride of the Barrier Master Anime Reveals Official Trailer, Jan. 6 Premiere on Netflix",
        "body_html": "<p>Netflix just dropped a stunning new trailer ahead of its global debut. Mark your calendars—this magical romance kicks off with a <b>1-hour special</b> on January 6! The anime is produced by Studio TROYCA.</p><p>The post Bride ... appeared first on Anime Corner.</p>",
        "raw_html": '<iframe src="https://www.youtube.com/embed/dQw4w9WgXcQ?feature=oembed"></iframe>',
    },
    {
        "title": "Spy x Family Season 4 Announced for Spring 2027",
        "body_html": "<p>The anime will return with a new season. Wit Studio and CloverWorks will again handle the animation. The staff and cast are returning. More details will be shared soon. Crunchyroll will stream it worldwide.</p>",
        "raw_html": "",
    },
    {"title": "Manga Sales Chart Updated This Week", "body_html": "<p>Weekly figures are out.</p>", "raw_html": ""},
]

for s in SAMPLES:
    s.update(link="https://example.com", source="demo", image_url=None, published=datetime(2026, 11, 20, tzinfo=timezone.utc))
    a = enrich(s, clean_text(s["title"]), clean_text(s["body_html"]))
    print(f"--- kind={a['kind']} video={a['youtube_url']}")
    print(build_post(a), "\n")
