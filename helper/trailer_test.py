"""Backs the /testtrailer command: can the bot find a trailer link, and can yt-dlp actually get the video?"""
import asyncio
import html

from config import YT_SEARCH_FALLBACK
from helper.downloader import (
    diagnose_video,
    download_trailer_verbose,
    environment_info,
    explain,
    runtime_warnings,
    search_trailer,
)
from helper.fetcher import fetch_new_articles
from helper.pagevideo import fetch_page_info
from helper.parser import KIND_NAME, clean_text, enrich, extract_youtube_url


def _esc(s) -> str:
    return html.escape(str(s), quote=False)


def _ok(flag) -> str:
    return "✅" if flag else "❌"


def _env_lines(env: dict) -> list:
    rt = ", ".join(f"{n} {v}" for n, v in env["runtimes"].items()) if env["runtimes"] else "none found"
    lines = [
        "⚙️ <b>Environment</b>",
        f"   yt-dlp {_esc(env['ytdlp'])} · ffmpeg {_ok(env['ffmpeg'])} · cookies {_ok(env['cookies'])}",
        f"   JS runtime: {_ok(env['runtimes'])} {_esc(rt)}",
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
        if not e["is_media"]:
            continue
        yt, how, image = e["youtube_url"], ("feed" if e["youtube_url"] else None), e.get("image_url")
        if not yt or not image:
            info = await fetch_page_info(http, e["link"])
            if not yt and info["video"]:
                yt, how = info["video"], f"page ({info['where']})"
            image = image or info["image"]
        matched = None
        name = e.get("show") or e.get("search_name")
        if not yt and YT_SEARCH_FALLBACK and name:
            m = await search_trailer(name, KIND_NAME[e["kind"]], e["title_en"], e.get("published"))
            if m:
                yt, how, matched = m["url"], "YouTube search", f"{m['title']} ({m.get('channel') or '?'})"
        out.append({"label": e["title_en"][:80], "yt": yt, "how": how, "image": image, "kind": e["kind"],
                    "matched": matched})
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

    video = None
    first = next((t for t in targets if t["yt"]), None)
    if first:
        lines.append("")
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
            hint = explain(d["error"], d.get("n_formats"))
            if hint:
                lines.append(f"💡 {_esc(hint)}")
    elif targets:
        lines += ["", "No video link found for these, so there's nothing to download. The post would go out with the image."]

    return "\n".join(lines)[:4000], video
