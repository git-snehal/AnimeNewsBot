import asyncio
import random
from helper.fetcher import fetch_new_articles
from helper.translator import translate_text
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import CHANNEL_ID, DISCUSSION_LINK

SYMBOLS_TITLE = ["❖", "◆", "✧", "✦", "⚡"]
SYMBOLS_DESC = ["»", "›", "•", "▪"]
SYMBOLS_SOURCE = ["✦", "✧", "➔", "❯"]

def generate_dynamic_tags(title: str, summary: str) -> str:
    text = (title + " " + summary).lower()
    tags = ["#AnimeNews"]
    
    if any(word in text for word in ["movie", "film", "劇場版"]):
        tags.append("#AnimeMovie")
    if any(word in text for word in ["trailer", "pv", "teaser", "promo"]):
        tags.append("#AnimeTrailer")
    if any(word in text for word in ["season", "sequel", "cour"]):
        tags.append("#AnimeSeason")
    if any(word in text for word in ["crunchyroll"]):
        tags.append("#Crunchyroll")
    if any(word in text for word in ["manga", "light novel", "chapter"]):
        tags.append("#MangaNews")
        
    return " ".join(tags[:3])

async def broadcast_news_loop(app, db, interval: int = 600):
    while True:
        try:
            articles = fetch_new_articles(db, limit=5, ignore_db=False)
            for article in reversed(articles):
                title = translate_text(article["title"])
                summary = translate_text(article["summary"])
                link = article["link"]
                source = article["source"]
                image_url = article.get("image_url")

                if db.is_posted(link, title):
                    continue

                s_title = random.choice(SYMBOLS_TITLE)
                s_desc = random.choice(SYMBOLS_DESC)
                s_src = random.choice(SYMBOLS_SOURCE)
                dynamic_tags = generate_dynamic_tags(title, summary)

                post_text = (
                    f"{s_title} {title} ❞\n\n"
                    f"> {s_desc} {summary[:220]}...\n\n"
                    f"{s_src} Source: {source} ❞\n\n"
                    f"{dynamic_tags}"
                )
                
                keyboard = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🔗 Read Article", url=link),
                        InlineKeyboardButton("💬 Discussion", url=DISCUSSION_LINK)
                    ]
                ])

                if image_url:
                    try:
                        await app.send_photo(
                            chat_id=CHANNEL_ID,
                            photo=image_url,
                            caption=post_text,
                            reply_markup=keyboard
                        )
                        db.add_posted(link, title)
                        await asyncio.sleep(5)
                        continue
                    except Exception:
                        pass

                await app.send_message(
                    chat_id=CHANNEL_ID,
                    text=post_text,
                    disable_web_page_preview=True,
                    reply_markup=keyboard
                )
                db.add_posted(link, title)
                await asyncio.sleep(5)

        except Exception as e:
            print(f"Error in broadcast loop: {e}")
        
        await asyncio.sleep(interval)