"""Builds the HTML caption.

  regular news                      trailers / teasers / PVs (video posts)
  ------------                      --------------------------------------
  ✦ Show — Label                    ✦ Show — Trailer Released  |  🎬 NEW TRAILER
  > two-sentence blurb              > blurb
  Date • Platform • Studio X        Date • Platform • Studio X
  > ╰ @signature                    > ╰ @signature

Blurb of up to 2 sentences -> normal quote. Longer -> full text (as much as fits) in a collapsed,
tap-to-expand quote.
"""
import html
import re

from config import SIGNATURE, SIGNATURE_PREFIX
from helper.parser import KIND_NAME

CAPTION_LIMIT = 1024  # Telegram caption limit; photo/video captions can't be longer
SAFETY = 30           # emoji count as 2 in Telegram's UTF-16 length, plus a little slack

_HIGHLIGHTS = [
    re.compile(r"\b\d+-(?:hour|minute|episode)[- ](?:special|premiere|long)\b", re.I),
    re.compile(r"\b(?:world|global) premiere\b", re.I),
    re.compile(r"\b(?:new|second|third|fourth|final|\d+(?:st|nd|rd|th)) season\b", re.I),
    re.compile(r"\bseason \d+\b", re.I),
]


def _esc(s: str) -> str:
    return html.escape(s, quote=False)


def _plain_len(s: str) -> int:
    return len(html.unescape(re.sub(r"<[^>]+>", "", s)))


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    cut = text[: max(limit - 1, 1)].rsplit(" ", 1)[0].rstrip(",;:-– ")
    return cut + "…"


def _fit(sentences, budget):
    out, used = [], 0
    for s in sentences:
        add = len(s) + (1 if out else 0)
        if used + add > budget:
            break
        out.append(s)
        used += add
    if not out and sentences:
        out = [_cut(sentences[0], budget)]
    return out


def _bold_first(text: str) -> str:
    for rx in _HIGHLIGHTS:
        m = rx.search(text)
        if m:
            return _esc(text[: m.start()]) + "<b>" + _esc(m.group(0)) + "</b>" + _esc(text[m.end():])
    return _esc(text)


def _studio_html(name: str) -> str:
    """'Studio' in plain text, the studio's name in bold (unless the name already says Studio)."""
    n = re.sub(r"^Studio\s+", "", name, flags=re.I).strip()
    if re.search(r"\bstudios?\b", n, re.I):
        return f"<b>{_esc(n)}</b>"
    return f"Studio <b>{_esc(n)}</b>"


def _info_html(a: dict) -> str:
    """January 6, 2027 • Netflix • Studio TROYCA   (date and platform bold, the word Studio plain, name bold)"""
    parts = []
    if a.get("date"):
        parts.append(f"<b>{_esc(a['date'])}</b>")
    if a.get("platform"):
        parts.append(f"<b>{_esc(a['platform'])}</b>")
    if a.get("studio"):
        parts.append(_studio_html(a["studio"]))
    return " • ".join(parts)


def _header_html(a: dict) -> str:
    """✦ Show — Label            (regular news)
       ✦ Show — Label  |  🎬 NEW TRAILER   (trailers, teasers, PVs: one line)"""
    if a.get("show") and a.get("label") and not a.get("plain_header"):
        line = f"✦ <b>{_esc(a['show'])}</b> — <i>{_esc(a['label'])}</i>"
    else:
        line = f"✦ <b>{_esc(_cut(a['title_en'], 200))}</b>"
    if a["is_media"]:
        line += f"  |  🎬 <b>NEW {KIND_NAME[a['kind']].upper()}</b>"
    return line


def build_post(a: dict) -> str:
    header = _header_html(a)
    info = _info_html(a)
    signature = f"<blockquote><b>{_esc(SIGNATURE_PREFIX + SIGNATURE)}</b></blockquote>"

    skeleton = "\n\n".join(p for p in (header, info, signature) if p)
    budget = CAPTION_LIMIT - _plain_len(skeleton) - 4 - SAFETY

    blurb = ""
    chosen = _fit(a["sentences"], budget) if budget > 40 else []
    if chosen:
        tag = "blockquote expandable" if len(chosen) > 2 else "blockquote"
        blurb = f"<{tag}>{_bold_first(' '.join(chosen))}</blockquote>"

    return "\n\n".join(p for p in (header, blurb, info, signature) if p)
