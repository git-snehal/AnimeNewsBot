"""Diagnostic: what do your RSS feeds actually contain?   Run:  python check_feeds.py"""
import asyncio
import os

for k, v in {"API_ID": "1", "API_HASH": "x", "BOT_TOKEN": "x", "CHANNEL_ID": "@x", "OWNER_ID": "1"}.items():
    os.environ.setdefault(k, v)

import httpx

from config import RSS_FEEDS
from helper.fetcher import _bodies, _fetch_feed, extract_image
from helper.parser import MEDIA_KINDS, clean_text, detect_kind, extract_youtube_url


async def main():
    async with httpx.AsyncClient() as client:
        for name, url in RSS_FEEDS.items():
            _, entries = await _fetch_feed(client, name, url)
            print(f"\n=== {name}: {len(entries)} entries")
            if not entries:
                print("   (feed empty or failed - check the URL / your network)")
                continue
            media = media_with_video = with_img = with_yt = 0
            for e in entries[:20]:
                body, raw = _bodies(e)
                title = clean_text(e.get("title", ""))
                yt = extract_youtube_url(raw)
                kind = detect_kind(title, clean_text(body), bool(yt))
                with_img += bool(extract_image(e))
                with_yt += bool(yt)
                if kind in MEDIA_KINDS:
                    media += 1
                    media_with_video += bool(yt)
                print(f"  [{kind or '-':7}] text={len(clean_text(body)):5} img={'Y' if extract_image(e) else '-'} yt={'Y' if yt else '-'}  {title[:70]}")
            n = min(len(entries), 20)
            print(f"  -> {n} checked: {with_img} with image, {with_yt} with YouTube link; "
                  f"trailer/teaser/PV items: {media} ({media_with_video} have a video link in the feed)")


asyncio.run(main())
