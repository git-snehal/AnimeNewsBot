"""Backs the /testtrailer command: can the bot find a trailer link, and can yt-dlp actually get the video?"""
import asyncio
import html

from config import YT_SEARCH_FALLBACK
from helper.downloader import (
    diagnose_video,
    download_trailer_verbose,
    environment_info,
    set_active_cfg,
    probe_video,
    explain,
    runtime_warnings,
    search_trailer,
)
from helper import anilist
from helper.fetcher import fetch_new_articles
from helper.pagevideo import fetch_page_info
from helper.parser import KIND_NAME, clean_text, enrich, extract_youtube_url


def clip_html(text: str, limit: int = 3900) -> str:
    """Cut a long HTML report at a line break, so no tag is cut in half (Telegram would reject the message)."""
    if len(text) <= limit:
        return text
    out = text[:limit].rsplit("\n", 1)[0]
    return out + "\n…"


def _esc(s) -> str:
    return html.escape(str(s), quote=False)


def _ok(flag) -> str:
    return "✅" if flag else "❌"


def _cookie_line(c: dict) -> str:
    if not c.get("exists"):
        return "   cookies: ❌ no cookies.txt (send one and reply /setcookies)"
    if c.get("logged_in"):
        days = f", login valid ~{c['days']} more days" if c.get("days") is not None else ""
        return f"   cookies: ✅ {c['cookies']} cookies ({c['google']} Google/YouTube), logged in{days}"
    return (f"   cookies: ⚠️ {c.get('cookies', 0)} cookies but NO valid Google login in them "
            "(exported while logged out, or expired). Export again while logged in.")


def _pot_line(p: dict) -> str:
    if not p.get("enabled"):
        return "   PO token: off (YOUTUBE_POT_ENABLED=false)"
    plugin = f"✅ {_esc(p['plugin'])}" if p.get("plugin") else "❌ not installed"
    server = "✅ reachable" if p.get("reachable") else "❌ not reachable"
    return f"   PO token: plugin {plugin} · provider {server} ({_esc(p.get('url'))})"


def _env_lines(env: dict) -> list:
    paths = env.get("runtime_paths") or {}
    rt = ", ".join(f"{n} {v} ({paths[n][-40:]})" if n in paths else f"{n} {v}" for n, v in env["runtimes"].items()) \
        if env["runtimes"] else "none found"
    lines = [
        "⚙️ <b>Environment</b>",
        f"   yt-dlp {_esc(env['ytdlp'])} · solver (ejs) {_esc(env.get('ejs_version') or 'n/a')} · ffmpeg {_ok(env['ffmpeg'])} · Python {_esc(env.get('python', '?'))}",
        _cookie_line(env.get("cookie_info") or {}),
        _pot_line(env.get("pot") or {}),
        f"   JS runtime: {_ok(env.get('runtime_ok'))} {_esc(rt)}",
        f"   EJS solver: {_ok(env['ejs'] or env['remote_components'])}",
    ]
    lines += [f"   ⚠️ {_esc(w)}" for w in runtime_warnings(env)]
    return lines


async def _targets(http, arg):
    """What to test: the link the admin gave, or the newest trailer/teaser/PV articles in the feeds."""
    if arg:
        direct = extract_youtube_url(arg)
        if direct:
            return [{"label": "Link you sent", "yt": direct, "how": "direct", "image": None, "kind": None}], None
        info = await fetch_page_info(http, arg)
        how = f"page ({info['where']})" if info["video"] else None
        return [{"label": arg[:80], "yt": info["video"], "how": how, "image": info["image"], "kind": None}], None

    out = []
    for a in reversed(await fetch_new_articles(http, None, limit=10)):  # newest first
        e = enrich(a, clean_text(a["title"]), clean_text(a["body_html"]))
        if not (e["is_media"] or e.get("video_hint")):
            continue
        yt, how, image = e["youtube_url"], ("feed" if e["youtube_url"] else None), e.get("image_url")
        if not yt or not image:
            info = await fetch_page_info(http, e["link"])
            if not yt and info["video"]:
                yt, how = info["video"], f"page ({info['where']})"
            image = image or info["image"]
        matched = None
        name = e.get("show") or e.get("search_name")
        why = None
        if not yt and YT_SEARCH_FALLBACK and name:
            media = await anilist.find_show(http, name)
            kind = e["kind"] if e["kind"] in KIND_NAME else e.get("video_hint") or "trailer"
            m, why = await search_trailer(name, KIND_NAME.get(kind, "Trailer"), e["title_en"], e.get("published"),
                                          anilist.names(media) if media else [])
            if m:
                yt, how, matched = m["url"], "YouTube search", f"{m['title']} ({m.get('channel') or '?'})"
        out.append({"label": e["title_en"][:80], "yt": yt, "how": how, "image": image,
                    "kind": e["kind"] or e.get("video_hint"), "matched": matched, "why": why})
        if len(out) >= 3:
            break
    note = None if out else "No trailer/teaser/PV articles in the feeds right now. Try /testtrailer &lt;article link or YouTube link&gt;."
    return out, note


async def run_trailer_test(http, arg=None, send=False):
    """-> (html_text, downloaded_video_info_or_None)"""
    env = await asyncio.to_thread(environment_info)
    lines = ["🔎 <b>Trailer test</b>", ""] + _env_lines(env) + [""]

    targets, note = await _targets(http, arg)
    if note:
        lines.append(note)
    for t in targets:
        kind = f"{t['kind']} · " if t["kind"] else ""
        lines.append(f"• <b>{_esc(t['label'])}</b>")
        lines.append(
            f"   {kind}video link: " + (f"✅ {_esc(t['how'])}" if t["yt"] else "❌ not found")
            + f" · image: {_ok(t['image'])}"
        )
        if t["yt"]:
            lines.append(f"   {_esc(t['yt'])}")
        if t.get("matched"):
            lines.append(f"   🔍 matched by search: “{_esc(t['matched'])}”. Check it's the right video.")
        elif t.get("why"):
            lines.append(f"   🔍 search: {_esc(t['why'])}")

    video = None
    first = next((t for t in targets if t["yt"]), None)
    if first:
        lines.append("")
        d = await diagnose_video(first["yt"])
        if not d["ok"]:
            # the active setup failed: look for one that works, apply it, and check again
            results = await probe_video(first["yt"], stop_on_success=True)
            if results and results[-1]["ok"]:
                cfg = results[-1]["cfg"]
                set_active_cfg(None if cfg["name"] == "default" else cfg)
                lines.append(f"🔧 The current setup got no formats, so I tried the others: <b>{_esc(cfg['name'])}</b> works. "
                             "Applied and saved.")
                d = await diagnose_video(first["yt"])
        if d["ok"]:
            mins, secs = divmod(d["duration"], 60)
            best = f" (up to {d['best_height']}p)" if d["best_height"] else ""
            lines.append(f"✅ <b>YouTube check:</b> “{_esc(d['title'])}” · {mins}:{secs:02d} · {d['n_formats']} formats{best}")
            if send:
                info, err = await download_trailer_verbose(first["yt"])
                if info:
                    video = info
                    lines.append(f"✅ <b>Download:</b> {info['size_mb']} MB, {info['height']}p. Uploading it to you now…")
                else:
                    lines.append(f"❌ <b>Download failed:</b> {_esc((err or '')[:300])}")
                    hint = explain(err or "")
                    if hint:
                        lines.append(f"💡 {_esc(hint)}")
            else:
                lines.append("ℹ️ Add <code>send</code> to also download it and get the video here.")
        else:
            lines.append(f"❌ <b>YouTube check failed:</b> {_esc(d['error'][:300])}")
            for note in d.get("notes", [])[:4]:
                lines.append(f"   yt-dlp said: {_esc(note[:240])}")
            hint = explain(" ".join(d.get("notes", [])) + " " + d["error"], d.get("n_formats"))
            if hint:
                lines.append(f"💡 {_esc(hint)}")
    elif targets:
        lines += ["", "No video link found for these, so there's nothing to download. The post would go out with the image."]

    return clip_html("\n".join(lines)), video


async def run_probe_report(url: str) -> str:
    """/ytprobe: try every yt-dlp setup against one video, keep the first that works."""
    env = await asyncio.to_thread(environment_info)
    results = await probe_video(url)
    lines = ["🧪 <b>YouTube probe</b>", _esc(url), ""] + _env_lines(env) + [""]
    if not results:
        return "\n".join(lines + ["❌ The probe could not run (see the bot log)."])
    first_ok = None
    js_problem = any(("javascript runtime" in x.lower() or "signature solving failed" in x.lower() or "supported java" in x.lower())
                     for r in results for x in r["notes"] + [r["error"]])
    for r in results:
        name = _esc(r["cfg"]["name"])
        if r["ok"]:
            best = f" (up to {r['best_height']}p)" if r["best_height"] else ""
            lines.append(f"✅ {name}: {r['n_formats']} formats{best}")
            first_ok = first_ok or r
        else:
            why = (r["notes"][0] if r["notes"] else r["error"]) or "failed"
            lines.append(f"❌ {name}: {_esc(why[:110])}")
    lines.append("")
    if first_ok:
        cfg = first_ok["cfg"]
        set_active_cfg(None if cfg["name"] == "default" else cfg)
        lines.append(f"✅ Downloads will use: <b>{_esc(cfg['name'])}</b> (saved, survives restarts). Try <code>/testtrailer send</code>.")
        if js_problem and cfg["name"] != "default":
            lines.append("ℹ️ The default setup (with your cookies) fails because yt-dlp has no WORKING JavaScript runtime. "
                         "Downloads run without cookies for now, which works. Run `pip install -U deno yt-dlp-ejs` in the bot's virtualenv and restart "
                         "so cookies work too.")
    else:
        tips = []
        pot = env.get("pot") or {}
        if not (pot.get("plugin") and pot.get("reachable")):
            tips.append("set up the PO-token provider like your other bot does (README: 'YouTube PO token provider')")
        if not env.get("runtime_ok") or js_problem:
            tips.append("run `pip install -U deno yt-dlp-ejs` in the bot's virtualenv and restart (no working JavaScript runtime)")
        c = env.get("cookie_info") or {}
        if not c.get("logged_in"):
            tips.append("send fresh cookies exported while logged in (/setcookies)")
        tips.append("YouTube probably blocks this server's address: set YTDLP_PROXY in config.env, or run the bot from another network")
        lines.append("❌ Nothing worked. Try: " + "; ".join(tips) + ".")
    return clip_html("\n".join(lines))
