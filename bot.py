import asyncio
import os
from pyrogram import Client, filters, idle
from database.db import Database
from helper.news_job import broadcast_news_loop
from config import API_ID, API_HASH, BOT_TOKEN

app = Client("anime_news_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)
db = Database()

@app.on_message(filters.command("ping"))
async def ping_cmd(client, message):
    await message.reply_text("❖ **Anime News Bot is active and running!**")

@app.on_message(filters.private & filters.command("setcookies"))
async def set_cookies_cmd(client, message):
    if not message.reply_to_message or not message.reply_to_message.document:
        await message.reply_text("❖ Please reply to an uploaded `cookies.txt` document file with `/setcookies`!")
        return

    await message.reply_text("❖ Downloading and configuring cookies file...")
    try:
        await client.download_media(
            message.reply_to_message,
            file_name="cookies.txt"
        )
        await message.reply_text("✅ `cookies.txt` successfully saved and loaded! yt-dlp is now authenticated.")
    except Exception as e:
        await message.reply_text(f"❌ Failed to save cookies file: {e}")

async def main():
    await app.start()
    print("Bot logged in successfully! Starting news scheduler...")
    asyncio.create_task(broadcast_news_loop(app, db))
    await idle()
    await app.stop()

if __name__ == "__main__":
    app.run(main())
