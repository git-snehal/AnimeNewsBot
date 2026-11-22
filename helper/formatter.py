"""Builds the HTML caption.

  regular news                      trailers / teasers / PVs
  ------------                      ------------------------
  ✦ Show — Label                    🎬 NEW TRAILER
  > two-sentence blurb              ✦ Show — Official Trailer Released
  Date • Platform • Studio          > blurb
  > @signature                      ▶ Date • Platform • Studio
                                    > @signature

Blurb of up to 2 sentences -> normal quote. Longer -> full text (as much as fits) in a collapsed,
tap-to-expand quote.
"""
import html
import re

from config import SIGNATURE
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


def build_post(a: dict) -> str:
    media = a["is_media"]

    head = []
    if media:
        head.append(f"🎬 <b>NEW {KIND_NAME[a['kind']].upper()}</b>")
    if a.get("show") and a.get("label"):
        head.append(f"✦ <b>{_esc(a['show'])}</b> — <i>{_esc(a['label'])}</i>")
    else:
        head.append(f"✦ <b>{_esc(_cut(a['title_en'], 200))}</b>")
    header = "\n".join(head)

    info = ""
    if a["info"]:
        info = f"{'▶ ' if media else ''}<b>{_esc(' • '.join(a['info']))}</b>"
    signature = f"<blockquote>{_esc(SIGNATURE)}</blockquote>"

    skeleton = "\n\n".join(p for p in (header, info, signature) if p)
    budget = CAPTION_LIMIT - _plain_len(skeleton) - 4 - SAFETY

    blurb = ""
    chosen = _fit(a["sentences"], budget) if budget > 40 else []
    if chosen:
        tag = "blockquote expandable" if len(chosen) > 2 else "blockquote"
        blurb = f"<{tag}>{_bold_first(' '.join(chosen))}</blockquote>"

    return "\n\n".join(p for p in (header, blurb, info, signature) if p)
