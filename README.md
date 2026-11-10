<div align="center">

#  Automated Anime News Bot

<img src="https://github.com/git-snehal/git-snehal/blob/main/assets/News.gif" width="100%" alt="Anime News Bot Demo" />

### *A lightning-fast, production-ready autonomous Telegram bot that aggregates, translates, formats, and broadcasts anime news, manga news, anime games news and YouTube video trailers in real-time.*

</div>

---

## 🌟 Live Preview Layout & Aesthetics

Every broadcast is meticulously formatted with dynamic Unicode iconography, clean source attribution, smart context-aware tags, and sleek inline action buttons:

> **❖ A History of Dancing in Anime OPs and EDs ❞**
> 
> > ❯ Exploring how choreography evolved in modern anime openings and endings...
> 
> **✦ Source:** Crunchyroll News ❞
> 
> **#AnimeNews #AnimeSeason #Crunchyroll**

---

## 🚀 Key Features

* **🤖 Fully Autonomous RSS Aggregator:** Scans top industry feeds concurrently (Anime News Network, Crunchyroll News, Anime Corner, Anime Trending).
* **🧠 Intelligent Deduplication:** SQLite-powered tracking ensures zero duplicate articles or redundant spam posts.
* **🌐 Automated Translation:** Built-in auto-translation pipelines to ensure all content is seamlessly localized and readable.
* **🎬 YouTube Trailer Downloader (`yt-dlp`):** Securely extracts and downloads video payloads with cookie authentication support (`/setcookies`).
* **🎨 Dynamic Visual Layouts:** Features rotating Unicode header symbols and smart keyword-based hashtag generators (`#AnimeMovie`, `#AnimeTrailer`, etc.).
* **⚡ Non-Blocking Architecture:** Built using Pyrogram's concurrent event loop pattern alongside `apscheduler` to maintain instant command responsiveness during background tasks.

---

## 🧰 Tech Stack

* **Language:** Python 3.10+
* **Telegram Framework:** Pyrogram (Async MTProto Client)
* **Feed Parser:** `feedparser`
* **Media Extraction:** `yt-dlp`
* **Database:** SQLite3
* **Translation:** `deep-translator`

---

## ⚙️ Configuration (`config.py`)

Create a `config.py` file in your root directory with your credentials:

```python
API_ID = 1234567                     # Your Telegram API ID
API_HASH = "your_api_hash"           # Your Telegram API Hash
BOT_TOKEN = "your_bot_token"         # Bot token from @BotFather
CHANNEL_ID = "@your_channel"         # Target channel username or -100ID
DISCUSSION_LINK = "[https://t.me/](https://t.me/)..." # Group discussion link for posts

RSS_FEEDS = {
    "Anime News Network": "[https://www.animenewsnetwork.com/encyclopedia/reports.xml?id=155&n=all](https://www.animenewsnetwork.com/encyclopedia/reports.xml?id=155&n=all)",
    "Crunchyroll News": "[https://www.crunchyroll.com/news/rss](https://www.crunchyroll.com/news/rss)",
    "Anime Corner": "[https://animecorner.me/feed/](https://animecorner.me/feed/)",
    "Anime Trending": "[https://anitrendz.net/news/feed](https://anitrendz.net/news/feed)"
}
