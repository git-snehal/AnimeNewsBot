<div align="center">

#  Automated Anime News Bot

<img src="https://github.com/git-snehal/git-snehal/blob/main/assets/News.gif" width="100%" alt="Anime News Bot Demo" />

### *A fast, autonomous Telegram bot that aggregates, cleans, formats and broadcasts anime news, and attaches the official YouTube trailer when there is one.*

</div>

---

## 🌟 Live Preview Layout

**Trailers, teasers and PVs** (video posts) put everything on one header line: bold title, italic news type, and a bold `🎬 NEW TRAILER` tag, with the downloaded video attached and notification sound on.

> **✦ Shadow Diver Donghua** — *Trailer Released*  |  🎬 **NEW TRAILER**
>
> > Studio Tumble has dropped a new trailer for the Shadow Diver (Long Xin Shaonu) donghua film. The Studio Tumble and Flint Sugar co-production, which is also a Made by Bilibili title, …
>
> **January 6, 2027** • **Netflix** • Studio **TROYCA**
>
> > **╰ @Od3nsAnimeNews**

**Regular news** uses the same layout with a plain `✦ **Show** — *Label*` header (no tag) and is sent silently.

* The info line is `Date • Platform • Studio X`. The date, platform and studio name are bold, the word "Studio" is plain, and anything the bot can't find is simply left out.
* **Long articles stay tidy:** a blurb of two sentences or fewer is a normal quote. Anything longer is written in full inside a collapsed, tap-to-expand quote.
* The signature (`SIGNATURE` in `config.env`, with the `╰ ` prefix) always sits in its own bold quote at the bottom.

---

## 🚀 Key Features

* **🤖 Fully Autonomous RSS Aggregator:** scans Anime News Network (India edition), ComicBook.com (anime news), Crunchyroll News, Anime Corner and Anime Trending concurrently. ANN's "all" feed is filtered to its news sections (reviews and advertorials are dropped); ComicBook.com is limited to its `/anime/news/` articles and shown with its original headline. Each feed's rules live in `config.py`.
* **🧠 Smart Deduplication:** SQLite tracking by link and headline. The same story from several outlets within 48 hours is posted once: trailers/teasers/PVs and typed news (season, cast, date...) match on the show name and news type, plain news matches on the headline's key words. If the first outlet only had a photo and a later one has the trailer video, the bot edits the video into the original post.
* **🧩 Rule-Based Formatting (no paid API):** news type, show title, premiere date, platform and studio are extracted from the article text. A field that can't be found is simply left out.
* **🎬 Trailer Videos (`yt-dlp`):** finds the YouTube video in the feed item, falls back to the article page (`og:video`, JSON-LD, embeds inside the article body only), downloads it and posts it with the news.
* **🖼 Images Always:** uses the feed image, then the article's `og:image`, and uploads the picture itself if Telegram can't fetch the URL.
* **🌐 Auto-Translation:** only runs when the text isn't English, so English titles are never mangled.
* **📊 Daily Report:** every day the admins get a summary of how many posts went out, how many were trailers/teasers/PVs, and a per-source breakdown.
* **🛠 Admin Commands:** `/stats`, `/status`, `/fetchnow`, `/pause`, `/resume`, `/setcookies`, all in the "/" menu.
* **⚡ Non-Blocking Architecture:** feed fetching, translation and downloads run off the event loop, so commands respond instantly even during background work.
* **🛡 Resilient:** FloodWait handling, video → photo → text fallback, and a bad article is retried 3 times and then skipped so it can never block the queue.

---

## 🧰 Tech Stack

* **Language:** Python 3.10+ (3.10 to 3.12 recommended)
* **Telegram Framework:** Pyrofork (async MTProto client, Pyrogram-compatible)
* **Feeds:** `feedparser` + `httpx`
* **Page parsing:** `beautifulsoup4`
* **Media Extraction:** `yt-dlp` (+ ffmpeg)
* **Database:** SQLite3
* **Translation:** `deep-translator`

---

## ⚙️ Setup

1. Install **ffmpeg** (needed to merge video and audio). Recent `yt-dlp` versions also want a JavaScript runtime such as Deno for YouTube.
2. `pip install -r requirements.txt` (this also installs `deno`, the JavaScript runtime that YouTube downloads need, and `yt-dlp-ejs`)
3. Copy `config.env.example` to `config.env` and fill it in (see below).
4. Add the bot as an **admin of your channel** with permission to post.
5. `python preview.py` shows sample posts offline. `python check_feeds.py` shows what your feeds really contain. `python bot.py` runs the bot.

On the first start the bot marks everything currently in the feeds as seen, so it doesn't flood your channel. Set `SEED_ON_FIRST_RUN=false` to disable that.

### Configuration (`config.env`)

```env
# --- required ---
API_ID=1234567
API_HASH=your_api_hash
BOT_TOKEN=your_bot_token
CHANNEL_ID=@your_channel          # or -100xxxxxxxxxx
OWNER_ID=123456789                # your Telegram user id
ADMIN_IDS=                        # more admins, comma separated (optional)

# --- look ---
SIGNATURE=@OD3NS_ANIMES
SHOW_SOURCE_BUTTON=true           # small "🔗 Source" button under each post
DISCUSSION_LINK=                  # optional "💬 Discussion" button
SILENT_REGULAR_NEWS=true          # regular news silent, trailers with sound
PIN_TRAILERS=false

# --- trailers ---
DOWNLOAD_TRAILERS=true
PAGE_VIDEO_FALLBACK=true          # look for the video on the article page if the feed has none
YT_SEARCH_FALLBACK=true           # last resort when feed + article page have no video: strict YouTube search
YOUTUBE_POT_ENABLED=true           # use the PO-token provider when it is running
YOUTUBE_POT_URL=http://127.0.0.1:4416
YTDLP_PROXY=                      # optional proxy for YouTube only, e.g. socks5://127.0.0.1:40000
YTDLP_REMOTE_COMPONENTS=auto           # auto = use the yt-dlp-ejs package | off | ejs:github | ejs:npm
YTDLP_JS_RUNTIME_PATH=            # only if the bot can't find deno/node, e.g. /root/.deno/bin/deno
MAX_VIDEO_MB=150
MAX_VIDEO_SECONDS=600
MAX_VIDEO_HEIGHT=720
COOKIES_PATH=cookies.txt

# --- polling & report ---
POLL_INTERVAL=600
BLOCKED_WORDS=puzzles vol,recap       # titles containing any of these are never posted
DISABLED_FEEDS=                   # feed names to switch off, comma separated
SEED_ON_FIRST_RUN=true
TIMEZONE=UTC                      # e.g. Asia/Kolkata, Europe/London
DAILY_STATS=true
STATS_TIME=23:59                  # HH:MM in TIMEZONE
STATS_CHAT_ID=                    # empty = DM every admin
```

The RSS feed list lives in `config.py`. Check that the URLs still work with `python check_feeds.py`.

---

## 🤖 Commands

The "/" menu is hardcoded in `helper/commands.py` and registered with Telegram on every start. Everyone sees `/start`, `/help` and `/ping`. Admins (`OWNER_ID` + `ADMIN_IDS`) also see:

| Command | What it does |
|---|---|
| `/stats` | Posts today so far. `/stats yesterday` or `/stats week` for other ranges |
| `/status` | Running or paused, last check, feed health, cookies, all-time count |
| `/fetchnow` | Check the feeds immediately |
| `/feeds` | Test every RSS feed right now: which ones work, which are blocked |
| `/ytprobe` | Tries every YouTube setup (JS runtime, player client, with/without cookies) on one video, shows which work, and makes downloads use the first one that does. `/ytprobe [YouTube link]` |
| `/testtrailer` | Test the trailer pipeline: `/testtrailer` (newest trailer articles), `/testtrailer <article or YouTube link>`, add `send` to also download the video and get it here |
| `/pause` / `/resume` | Stop / restart posting (nothing is skipped, new articles post after resume) |
| `/setcookies` | Reply to a `cookies.txt` file to update yt-dlp cookies (private chat) |

> **Each admin must open the bot once and press Start**, otherwise Telegram can't give them their personal menu yet. Sending `/start` again refreshes it.

---

## 🍪 YouTube Cookies

If YouTube blocks downloads (common on servers), send a `cookies.txt` to the bot in a private chat and reply to it with `/setcookies`. Only admins can do this.

---

## 🩺 Troubleshooting

**Trailer videos don't download** (`Requested format is not available`): YouTube only returns real video formats if yt-dlp can run its JavaScript challenge solver. Run `/testtrailer send` in the bot. It tells you exactly what's missing. The usual fixes:

1. A JavaScript runtime: **Deno 2.3+**, **Node 22+** or **Bun** (the bot detects and enables whichever is installed; yt-dlp alone only enables Deno).
2. The solver package: `pip install -U "yt-dlp[default]"`
3. On a cloud server or Colab, YouTube often blocks the IP: send a `cookies.txt` and reply `/setcookies`.

**The bot won't start: `[500 RPC_CALL_FAIL] (caused by "auth.ImportBotAuthorization")`:** this is an error on Telegram's side during login (problems with your token or API keys give 400 errors such as `ACCESS_TOKEN_INVALID` instead). The bot now retries the login with growing pauses (15s, 30s, 1 min, up to 2 min, 20 attempts) and clears a half-finished session file between tries, so usually you just wait. If it keeps failing:
1. Make sure the server clock is right (`timedatectl`; `NTP service: active`).
2. Run only one copy of the bot with this token.
3. Copy `anime_news_bot.session` from a machine where the bot already logged in (for example your Colab) into the bot folder on the VPS, and stop the bot on the other machine first. The VPS then reuses that login and skips the failing step.

**YouTube PO token provider (optional extra):** with a working JS runtime (above) it is usually not needed. It is a small local service that supplies YouTube's "proof of trust" token, and helps when YouTube still refuses a server's address. The bot uses it automatically when it finds it, and skips it silently when it doesn't. Three steps on the server:
```
# 1. the provider (needs Docker: sudo apt install -y docker.io)
docker run -d --name bgutil-provider -p 4416:4416 --init --restart unless-stopped brainicism/bgutil-ytdlp-pot-provider

# 2. the yt-dlp plugin (already in requirements.txt; install it in the bot's virtualenv)
/root/AnimeNewsBot/venv/bin/pip install -U bgutil-ytdlp-pot-provider

# 3. a current yt-dlp (nightly builds carry the newest YouTube fixes)
/root/AnimeNewsBot/venv/bin/pip install -U --pre "yt-dlp[default,curl-cffi]"
```
Then restart the bot and run `/testtrailer`: the "PO token" line must show the plugin ✅ and the provider ✅. If you already run another bot that has a provider, check `docker ps` and `curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:4416`: any answer (200 or 404) means it is up and the bot can reuse it. Use `YOUTUBE_POT_URL` in `config.env` if it listens elsewhere.

**No video, and `/testtrailer` says "JS runtime: ❌ none found" / "No supported JavaScript runtime":** YouTube needs a JavaScript runtime to unlock video formats. The easy, reliable way (the same one the other working bots use) is the `deno` pip package, which ships the Deno binary inside your virtualenv:
```
/root/AnimeNewsBot/venv/bin/pip install -U -r requirements.txt     # installs deno, yt-dlp-ejs, yt-dlp, the PO-token plugin...
```
Restart the bot. It looks for runtimes next to the Python executable first (so it works even when systemd/pm2 gives the bot a short `PATH`), checks that each one runs and is new enough, and hands yt-dlp the full path. `/testtrailer` shows which runtime was found and where. If yours lives somewhere unusual, set `YTDLP_JS_RUNTIME_PATH=/full/path/to/deno` in `config.env`.

The bot also works on a *copy* of `cookies.txt` for every download, because yt-dlp rewrites the cookie file it is given.

**Videos work on one machine but not on another (for example Colab works, your VPS doesn't):** YouTube treats cloud / datacenter addresses as bots far more often than home or Google-owned ones. The bot copes in three ways:
1. When a download is blocked, it automatically tries other yt-dlp setups (each JS runtime on its own, other player clients, with and without cookies), remembers the first one that works, and retries. It does this at most once every 30 minutes.
2. `/ytprobe` runs the same search on demand and shows the result for every setup.
3. If nothing works, the server's address itself is blocked. Set `YTDLP_PROXY` in `config.env` to a proxy (it is used for YouTube only), or run the bot from another network.

**Videos still don't download:** run `/ytprobe`, then `/testtrailer send`. It now shows:
- whether `cookies.txt` really contains a valid Google login (a file exported while logged out, or an expired one, is the most common reason),
- which JavaScript runtime was found, with its version (yt-dlp needs Deno 2.3+ or Node 22+) and whether the EJS solver is installed,
- the actual warnings yt-dlp printed ("yt-dlp said: ...") and a hint for the likely fix.

Python 3.11 or newer is recommended: yt-dlp has deprecated 3.10.

**Anime News Network keeps answering 403 from your server:** tested with a Chrome-impersonating client (`curl_cffi`) as well as plain `curl` and the bot: all get 403, so ANN is blocking your server's address, not the bot's connection style. Options: set up a proxy for it, run the bot from a home or other network, or switch the feed off with `DISABLED_FEEDS=Anime News Network`.

**Anime News Network:** its feed blocks many cloud / Colab IPs (HTTP 403). It works from a normal VPS or home connection. Use the edition you want by changing `ann-edition=in` to `us` in `config.py`. Run `/feeds` to check.

**A feed shows a 403 / is blocked:** run `/feeds`. A feed that is blocked everywhere is only retried once an hour. If your server's IP is simply refused, switch the feed off with `DISABLED_FEEDS=Anime News Network` in `config.env`.

**`/feeds` says "returns a web page, not an RSS feed":** that URL isn't a feed. Replace it in `config.py`. Each feed can be a list of alternative URLs in `config.py`, and the bot tries several browser-like request profiles and remembers the one that works. If every attempt is blocked, the site is rejecting your server's IP, and you can remove that feed or run the bot from a different network.

**Same story posted twice:** the bot treats the same show + news type within 48 hours as one story (a trailer, teaser and PV of one show count as the same). Two stories of different types about one show (say a trailer and a cast reveal) are still posted separately, and plain news is only merged when the headlines share at least 3 key words.

---

## 📝 Notes

* **Video lookup order for trailer articles:** the feed item, then the article page, then a strict YouTube search (`YT_SEARCH_FALLBACK`). The search only accepts a video whose title has every word of the show name, matches the season, isn't a reaction/review, fits the length limit and was uploaded in the last 3 weeks. `/testtrailer` shows what it matched, so you can check it. If nothing passes, the post goes out without a video.
* If a video can't be downloaded (too big, too long, blocked), the post is sent with the image instead.
* Telegram limits captions to 1024 characters, so very long articles are trimmed at a sentence boundary.
* Date, platform and studio detection is rule-based and best effort. If a detail isn't in the article text, it is left out.
