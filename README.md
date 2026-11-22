# Anime News Bot

Polls anime RSS feeds, rewrites each item into a compact post, and publishes it to a Telegram channel.
Trailers, teasers and PVs get a highlighted layout and the actual video (downloaded with yt-dlp).

## Post format

Regular news
```
✦ Show — Label
> Two-sentence blurb (3+ sentences -> collapsed, tap-to-expand quote)
Date • Platform • Studio
> @YourChannel
```
Trailer / teaser / PV
```
🎬 NEW TRAILER
✦ Show — Official Trailer Released
> blurb
▶ Date • Platform • Studio
> @YourChannel                                  [ 🔗 Source ]
```
Trailers are sent with the video attached and with notification sound; regular news is sent silently.
Date, platform and studio are pulled from the article text with rules. If one can't be found it is left out.

## Commands ("/" menu)

The menu is hardcoded in `helper/commands.py` and registered with Telegram on every start.
Everyone sees `/start /help /ping`. Admins (`OWNER_ID` + `ADMIN_IDS`) also see:

| Command | What it does |
|---|---|
| `/stats` | Posts today so far. `/stats yesterday` or `/stats week` for other ranges |
| `/status` | Running/paused, last check, feed health, cookies, all-time count |
| `/fetchnow` | Check the feeds immediately |
| `/pause` / `/resume` | Stop / restart posting (nothing is skipped, new articles post after resume) |
| `/setcookies` | Reply to a `cookies.txt` file to update yt-dlp cookies (private chat) |

**Each admin must open the bot once and press Start**, otherwise Telegram can't give them their personal menu yet.
Sending `/start` again refreshes it.

## Daily report

Every day at `STATS_TIME` (in `TIMEZONE`) the bot sends each admin a summary: posts today, trailers/teasers/PVs
(and how many got a video), a per-source breakdown, anything skipped after errors, and the all-time total.
Set `STATS_CHAT_ID` to send it to a group instead, or `DAILY_STATS=false` to turn it off.

## Setup

1. Install ffmpeg (needed to merge video+audio). Recent yt-dlp versions also want a JavaScript runtime such as Deno for YouTube.
2. `pip install -r requirements.txt` (Python 3.10-3.12 recommended)
3. `cp .env.example .env` and fill it in. Add the bot as admin of your channel.
4. `python preview.py` shows sample posts offline. `python bot.py` runs the bot.

On the first start the bot marks everything currently in the feeds as seen (no flood). Set `SEED_ON_FIRST_RUN=false` to disable.

## YouTube cookies

If YouTube blocks the downloads (common on servers), send a `cookies.txt` to the bot in a private chat and reply to it with `/setcookies`. Only admins can do this.

## Notes

- Video lookup order for trailer articles: (1) YouTube link in the feed item, (2) the article page itself (`PAGE_VIDEO_FALLBACK`: og:video tags, JSON-LD, then embeds inside the article body only), (3) optionally a YouTube search (`YT_SEARCH_FALLBACK=true`, off by default, can pick the wrong video). If nothing is found the post goes out without a video.
- `python check_feeds.py` shows what each feed really contains (images, summary length, how many trailer items have a video link).
- If the video can't be downloaded (too big, too long, blocked) the post is sent with the image instead.
- Captions are limited to 1024 characters by Telegram, so very long articles are trimmed at a sentence boundary.
- Failed articles are retried 3 times, then skipped so one bad item can't block the queue.
- Verify the feed URLs in `config.py` still work.
