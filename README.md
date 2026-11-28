<div align="center">
Anime News Bot
<img src="https://github.com/git-snehal/git-snehal/blob/main/assets/News.gif" width="100%" alt="Anime News Bot Demo" />

### *A Telegram bot that collects anime news, posts each story once, attaches the official trailer, and adds weekly Netflix charts and IMDb episode ratings with images it draws itself.*

</div>

---

## What's new in v3

| | |
|---|---|
| 🎬 **Trailers found much more often** | Official YouTube channels are now feeds (Crunchyroll, Netflix Anime, Aniplex USA, TOHO animation, KADOKAWA, Muse Asia), so trailers arrive with the video attached. The YouTube search matches every name a show has (English, romaji, synonyms from AniList) and only needs most of a long name. |
| 🏷 **Correct news type** | The headline decides the label. "Movie Sets Theatrical Release for May 2027" is a **Release Date** post, even when the article also mentions a trailer (that trailer is still attached as a bonus). |
| 🖼 **Portrait key visuals** | The bot looks at the pictures inside the article and prefers a portrait key visual / poster over the wide preview image. |
| 🌐 **Sites that block your server** | `PAGE_PROXY` (e.g. Cloudflare WARP) is used only after a site answers 403, for article pages and feeds (ANN). |
| 📺 **Netflix Top 10, weekly** | From Netflix's official data: every anime in any country's Top 10, ranked by number of countries, as a chart image. |
| ⭐ **IMDb episode ratings** | Follows the most popular airing anime and posts an episode when it reaches 8.5+ with 300+ votes (configurable), as a portrait rating card. Max 3 a day. |
| 📄 **/logs** | Get the log as a .txt file: `/logs trailers` shows one line per trailer post with the reason when the video was missing. |
| 🧹 **Fixes** | Runs from its own folder whatever directory starts it; every new feed is seeded (no backlog floods); `/updateytdlp` error messages no longer break; leftover downloads are cleaned at start; whole-word `BLOCKED_WORDS`. |

---

## Post layout

**Trailers, teasers and PVs** (video, notification on):

> **✦ Shadow Diver Donghua** — *Trailer Released*  |  🎬 **NEW TRAILER**
> > Studio Tumble has dropped a new trailer for the Shadow Diver donghua film…
>
> **January 6, 2027** • **Netflix** • Studio **TROYCA**
> > **╰ @YourChannel**

**Regular news** uses the same layout without the tag and is sent silently. **Netflix** and **IMDb** posts are an image the bot draws (1080×1350) with a short caption.

---

## Setup

1. Python **3.11+** (3.12 recommended) and **ffmpeg**.
2. `pip install -r requirements.txt`
3. Copy `config.env.example` to `config.env` and fill it in.
4. Add the bot as an **admin of your channel** (post messages).
5. `python preview.py` shows sample posts offline · `python check_feeds.py` shows what the feeds contain · `python bot.py` runs the bot.

**First start:** every feed the bot hasn't seen before is "seeded": its current items are marked as seen instead of being posted, so the channel isn't flooded. This also happens for any feed you add later. The Netflix job posts the latest week once; the IMDb job treats older episodes as history and only considers each show's two newest episodes plus everything rated from then on.

### Upgrading from v2
Stop the bot, copy the new files over the old folder (keep `config.env`, `cookies.txt`, `*.session` and `database/`), then:
```
venv/bin/pip install -U -r requirements.txt
```
Add the new settings you want from `config.env.example` to your `config.env` (all have sensible defaults). Start the bot. The YouTube channel feeds are seeded on their first check.

---

## 🤖 Commands

Everyone: `/start`, `/help`, `/ping`. Admins (`OWNER_ID` + `ADMIN_IDS`, also inside `CONTROL_GROUP_ID`):

| Command | What it does |
|---|---|
| `/stats` | Posts today. `/stats yesterday`, `/stats week` |
| `/status` | Running/paused, last check, feed health, which features are on |
| `/fetchnow` | Check the feeds now |
| `/feeds` | Test every feed: which work, which are blocked |
| `/logs` | The log as a .txt file. `/logs trailers` (trailer results and miss reasons), `/logs errors`, `/logs 6h`, combined: `/logs trailers 24h`. Only in a private chat or the control group. |
| `/netflixtest` | Builds this week's Netflix post and sends it **to you** (not the channel) |
| `/imdbtest` | Refreshes the IMDb data, shows which shows are followed / matched, sends a rating post preview **to you** |
| `/testtrailer` | Test the trailer pipeline (`/testtrailer <link>`, add `send` to download) |
| `/ytprobe` | Find a working yt-dlp setup for YouTube |
| `/updateytdlp` | Update yt-dlp + solver, restart if anything changed |
| `/pause` / `/resume` | Stop / restart posting (nothing is skipped) |
| `/setcookies` | Reply to a `cookies.txt` with this (private chat) |

> Each admin must open the bot once and press **Start** so Telegram can show them the admin menu.

### Reading `/logs trailers`
Every trailer / teaser / PV post writes one line:
```
TRAILER OK   | Overgeared Climax Trailer | from YouTube channel feed
TRAILER MISS | Some Show Trailer | sent as photo | page www.animenewsnetwork.com blocked (403) | search for 'Some Show': 20 results: 12x name mismatch, 8x too old
```
That tells you exactly which step failed: the article page (blocked / no video), the search (and why each result was rejected), or the download.

---

## 🌐 Cloudflare WARP for sites that block your server (ANN)

WARP is Cloudflare's free official client. In **proxy mode** it only opens a local port (40000); nothing else on the server changes. **Do the steps in this order.** Connecting before switching to proxy mode sends *all* the server's traffic through WARP and can cut your SSH session.

```
# Debian / Ubuntu
curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | sudo gpg --yes --dearmor -o /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflare-client.list
sudo apt update && sudo apt install -y cloudflare-warp

warp-cli --accept-tos registration new   # 1. register
warp-cli --accept-tos mode proxy         # 2. PROXY MODE FIRST (important)
warp-cli --accept-tos connect            # 3. only now connect
warp-cli status                          # should say Connected (mode: proxy)

# test: should print HTTP 200 instead of 403
curl -s -o /dev/null -w "%{http_code}\n" -x socks5h://127.0.0.1:40000 -A "Mozilla/5.0" "https://www.animenewsnetwork.com/news/rss.xml?ann-edition=in"
```
If the test shows 200, set `PAGE_PROXY=socks5://127.0.0.1:40000` in `config.env` and restart the bot. If it still shows 403, ANN blocks WARP too; leave `PAGE_PROXY` empty (the YouTube channel feeds still cover most trailers). Remove WARP any time with `sudo apt remove cloudflare-warp`.

---

## 📺 Netflix Top 10

Netflix publishes the Top 10 of every country each **Tuesday** (week Mon–Sun) at top10.netflix.com. The bot checks every 6 hours, keeps the titles that are anime (exact title match on AniList), ranks them by how many countries they charted in, and posts once per week. Most weeks have **2–6 anime**; a week with none is skipped. With `TMDB_API_KEY` set, live-action titles get a "Live-action" label and their own poster. Data files are kept in `database/netflix/` (~35 MB).

## ⭐ IMDb episode ratings

Uses IMDb's free datasets (datasets.imdbws.com, **personal and non-commercial use**; every post carries IMDb's required credit line). Every 3 hours the bot refreshes IMDb's daily files, reads the ratings of the episodes of the `IMDB_TRACK_SHOWS` most popular airing anime, and posts an episode that got its first votes in the last 14 days and has `IMDB_MIN_VOTES`+ votes and `IMDB_MIN_RATING`+ (one per check, `IMDB_MAX_PER_DAY` per day). Shows are matched to IMDb through Wikidata, then by title in IMDb's own title list; `/imdbtest` lists any show it could not match. Files are kept in `database/imdb/` (~300 MB; the big title list is refreshed weekly, the others daily).

Posters come from AniList (460 px, a little soft) or, with a free `TMDB_API_KEY`, from TMDB in full resolution.

---

## 🩺 Troubleshooting

* **A trailer went out without its video:** `/logs trailers` shows the reason. `blocked (403)` → set up `PAGE_PROXY`. `name mismatch` → the headline's show name differs from YouTube's; tell us the show. `download failed` → `/testtrailer send` and `/ytprobe`.
* **Telegram login `[500 RPC_CALL_FAIL]`:** Telegram-side; the bot retries with growing pauses. Keep the clock right (`timedatectl`), run one copy per token.
* **YouTube downloads fail (`Requested format is not available`, `n challenge solving failed`):** `/updateytdlp`, then `/ytprobe`. A JavaScript runtime (`deno` from requirements.txt) is required. Cloud IPs may need `cookies.txt` (`/setcookies`) or `YTDLP_PROXY`.
* **Same story posted twice:** same show + same news type within 48 h is one story; plain news needs 3 shared key words.
* **A YouTube channel posts too much / the wrong things:** switch it off with `DISABLED_FEEDS=YouTube: Muse Asia`. Only uploads whose title says trailer / teaser / PV (and not clip, episode, preview, short…) are used.

## 🧰 Stack
Python 3.11+, Pyrofork, feedparser, httpx, BeautifulSoup, yt-dlp (+ffmpeg, deno), Pillow, SQLite, deep-translator. Data: AniList, IMDb datasets, Netflix Top 10, Wikidata, optional TMDB. Fonts: Inter and Bebas Neue (SIL Open Font License, in `assets/fonts`).
