"""Rule-based extraction: clean text, news type, premiere date, platform, studio, YouTube link.

Everything here is best-effort. A field that can't be found is simply left out of the post.
"""
import calendar
import html
import re
from datetime import datetime, timedelta, timezone

from helper import story

# --------------------------------------------------------------------------- text
_DROP_BLOCKS = re.compile(r"(?is)<(script|style|iframe|figure|noscript)\b.*?</\1>")
_BLOCK_END = re.compile(r"(?i)</(?:p|div|li|h[1-6]|blockquote)>|<br\s*/?>")
_TAG = re.compile(r"<[^>]+>")


def clean_text(raw: str) -> str:
    """HTML -> plain text, minus feed boilerplate ("The post ... appeared first on", "Read more")."""
    if not raw:
        return ""
    s = _DROP_BLOCKS.sub(" ", raw)
    s = _BLOCK_END.sub(" ", s)
    s = _TAG.sub("", s)
    s = html.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    s = re.sub(r"\s*The post .{0,200}? appeared first on .{0,120}$", "", s)
    s = re.sub(r"\s*(?:\[?(?:…|\.\.\.)\]?\s*)?(?:Read more|Continue reading)\b.*$", "", s, flags=re.I)
    s = re.sub(r"\s*\[(?:…|\.\.\.)\]\s*$", "", s)
    return s.strip()


_ABBR = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|vs|St|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sept?|Oct|Nov|Dec|Inc|Ltd|Co|No|Vol|Ep)\.$")
_NEXT = r'\s+(?=["“‘(]?[A-Z0-9])'
_SENT_SPLIT = re.compile(rf'(?<=[.!?…]){_NEXT}|(?<=[.!?…]["”’)]){_NEXT}')


def split_sentences(text: str) -> list:
    parts = [p.strip() for p in _SENT_SPLIT.split(text) if p and p.strip()]
    merged = []
    for p in parts:
        if merged and _ABBR.search(merged[-1]):
            merged[-1] += " " + p
        else:
            merged.append(p)
    # a trailing cut-off sentence ("...") looks broken, drop it when there is something else
    if len(merged) > 1 and merged[-1].endswith(("…", "...")):
        merged.pop()
    return merged


# --------------------------------------------------------------------------- YouTube
_YT_RE = re.compile(
    r"(?:https?:)?//(?:www\.|m\.)?"
    r"(?:youtube\.com/(?:watch\?(?:[^\s\"'<>]*?&)?v=|embed/|shorts/)|youtube-nocookie\.com/embed/|youtu\.be/)"
    r"([\w-]{11})"
)


def extract_youtube_url(raw: str):
    if not raw:
        return None
    m = _YT_RE.search(html.unescape(raw))
    return f"https://www.youtube.com/watch?v={m.group(1)}" if m else None


# --------------------------------------------------------------------------- news type
MEDIA_KINDS = {"trailer", "teaser", "pv"}
KIND_NAME = {"trailer": "Trailer", "teaser": "Teaser", "pv": "PV"}
KIND_LABEL = {
    "visual": "Key Visual Revealed",
    "season": "New Season Announced",
    "date": "Premiere Date Revealed",
    "cast": "Cast Revealed",
}
_KIND_DEFS = [
    ("trailer", re.compile(r"\btrailers?\b", re.I)),
    ("teaser", re.compile(r"\bteasers?\b", re.I)),
    ("pv", re.compile(
        r"\bPVs?\b|\bpromo(?:tional)?\s+(?:video|clip)s?\b"
        r"|\b(?:new|first|1st|2nd|3rd|second|third|main|short|latest)\s+videos?\b(?!\s+games?)"
        r"|\bin\s+(?:a\s+|the\s+)?(?:new\s+|main\s+)?videos?\b(?!\s+games?)", re.I)),
    ("visual", re.compile(r"\bkey\s+visuals?\b|\bposter\b", re.I)),
    (
        "season",
        re.compile(
            r"\b(?:sequel|new season|second season|third season|fourth season|final season"
            r"|season\s+\d+|\d+(?:st|nd|rd|th)\s+season)\b",
            re.I,
        ),
    ),
    ("date", re.compile(r"\b(?:release|premiere|air)\s+dates?\b|\bpremieres?\b|\bdebuts?\b", re.I)),
    ("cast", re.compile(r"\bcast\b|\bvoice\s+actors?\b", re.I)),
]


def detect_kind(title: str, body: str, has_video: bool):
    for key, rx in _KIND_DEFS:
        if rx.search(title):
            return key
    head = body[:400]
    for key, rx in _KIND_DEFS[:3]:
        if rx.search(head):
            return key
    if has_video:
        for key, rx in _KIND_DEFS[:3]:
            if rx.search(body):
                return key
    return None


# --------------------------------------------------------------------------- show title
_VERB_LIST = (
    "Reveals Revealed Reveal Unveils Unveiled Unveil Gets Get Announces Announced Streams Streamed Debuts Debuted "
    "Premieres Premiered Releases Released Launches Launched Confirms Confirmed Teases Teased Shares Shared Posts "
    "Drops Dropped Adds Added Casts Cast Lists Sets Delays Delayed Hits Shows Previews Previewed Welcomes Brings "
    "Returns Return Highlights Highlighted Introduces Introduced Features Joins Joined Ends Expands Opens Earns Wins "
    "Licenses Licensed Stars Inspires Reaches Begins Starts Plans Lands Marks Receives Gains Publishes Reports "
    "Updates Updated Offers Promises Counts Celebrates Collaborates Surpasses Tops Rises Falls Goes Becomes "
    "Resumes Concludes Finishes Hints Pairs Teams Reunites Recruits Gives Takes Makes Streams"
).split()
_VERB_RE = re.compile(r"\s(?:" + "|".join(sorted(set(_VERB_LIST), key=len, reverse=True)) + r")\b")
# words that describe the news rather than the show; stripped from the end of what comes before the verb
_MOD_WORDS = {
    "anime", "tv", "film", "movie", "manga", "game", "games", "ova", "ona", "season", "sequel", "final", "second",
    "third", "fourth", "new", "main", "promo", "promotional", "video", "trailer", "teaser", "pv", "short",
    "original", "series", "web", "novel", "novels", "light", "webtoon", "live-action", "part", "cour", "visual",
    "key", "special", "theatrical", "full", "latest", "first",
}
_LEAD_STRIP = {"new", "live-action", "exclusive:", "exclusive", "interview:"}
# companies / platforms that are the *subject of the sentence*, not the show
_NOT_A_SHOW = {
    "crunchyroll", "netflix", "hidive", "hulu", "disney+", "prime video", "amazon", "funimation", "viz", "viz media",
    "yen press", "seven seas", "kodansha", "shueisha", "square enix", "sony", "aniplex", "toho", "kadokawa",
    "oceanveil", "muse asia", "muse india", "bilibili", "tokyo mx", "gkids", "sentai", "discotek", "inklore",
    "j-novel club", "k manga", "nakama press", "denpa", "vertical", "dark horse",
}


def _is_modifier(tok: str, prev: str) -> bool:
    base = re.sub(r"['’]s$", "", tok).strip(",.:;!?").lower()
    if base in _MOD_WORDS:
        return True
    if re.fullmatch(r"\d+(?:st|nd|rd|th)", base):
        return True
    if re.fullmatch(r"\d+", base) and prev.lower().strip(",.:;") in {"season", "part", "cour", "vol", "volume"}:
        return True
    return False


def extract_show(title: str):
    """The show/series the headline is about: everything before the first headline verb, minus descriptive words."""
    m = _VERB_RE.search(title)
    if not m:
        return None
    tokens = title[: m.start()].split()
    while len(tokens) > 1 and tokens[0].lower() in _LEAD_STRIP:
        tokens.pop(0)
    while len(tokens) > 1 and _is_modifier(tokens[-1], tokens[-2]):
        tokens.pop()
    show = " ".join(tokens).strip(" :-–—,;")
    if show[:1] in "'\"‘“" and show[-1:] in "'\"’”":
        show = show[1:-1].strip()
    show = show.strip(" :-–—,;")
    if not (2 <= len(show) <= 100) or show.lower() in _NOT_A_SHOW:
        return None
    return show


# --------------------------------------------------------------------------- date
_MON = (
    r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|June?|July?|Aug(?:ust)?"
    r"|Sept?(?:ember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
)
_YEAR = r"(20\d\d)"
_DATE_PATTERNS = [
    ("md", re.compile(rf"\b({_MON})\.?\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+{_YEAR})?\b")),
    ("dm", re.compile(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+({_MON})\.?(?:,?\s+{_YEAR})?\b")),
    ("my", re.compile(rf"\b({_MON})\.?,?\s+{_YEAR}\b")),
    ("sy", re.compile(rf"\b(Winter|Spring|Summer|Fall|Autumn)\s+{_YEAR}\b")),
]
_DATE_CTX = re.compile(
    r"premier|debut|\bair|release|launch|kick|start|begin|stream|arrive|\bopen|due\b|scheduled|slated|return|coming",
    re.I,
)


def _month_num(mon: str) -> int:
    return list(calendar.month_abbr).index(mon[:3].title())


def _make_date(mon, day, year, ref):
    m = _month_num(mon)
    ref = ref or datetime.now(timezone.utc)
    y = int(year) if year else ref.year
    try:
        d = datetime(y, m, int(day), tzinfo=timezone.utc)
        if not year and d < ref - timedelta(days=45):
            d = d.replace(year=y + 1)
    except ValueError:
        return None
    return f"{calendar.month_name[m]} {d.day}, {d.year}"


def extract_date(text: str, ref=None):
    """First date mentioned next to a premiere/release/airing word. Never falls back to the publish date."""
    found = []
    for kind, rx in _DATE_PATTERNS:
        for m in rx.finditer(text):
            g = m.groups()
            if kind == "md":
                val = _make_date(g[0], g[1], g[2], ref)
            elif kind == "dm":
                val = _make_date(g[1], g[0], g[2], ref)
            elif kind == "my":
                val = f"{calendar.month_name[_month_num(g[0])]} {g[1]}"
            else:
                val = f"{'Fall' if g[0] == 'Autumn' else g[0]} {g[1]}"
            if val:
                found.append((m.start(), m.end(), val))
    for start, end, val in sorted(found):
        if _DATE_CTX.search(text[max(0, start - 80): end + 60]):
            return val
    return None


# --------------------------------------------------------------------------- platform / studio
_PLATFORMS = [
    (r"\bNetflix\b", "Netflix"),
    (r"\bCrunchyroll\b", "Crunchyroll"),
    (r"\bDisney(?:\+| Plus)", "Disney+"),
    (r"\b(?:Amazon )?Prime Video\b", "Prime Video"),
    (r"\bHulu\b", "Hulu"),
    (r"\bHIDIVE\b", "HIDIVE"),
    (r"\bTokyo MX\b", "Tokyo MX"),
    (r"\bAT-X\b", "AT-X"),
    (r"\bABEMA\b", "ABEMA"),
    (r"\bU-NEXT\b", "U-NEXT"),
    (r"\bBilibili\b", "Bilibili"),
    (r"\bFuji TV\b", "Fuji TV"),
    (r"\bNippon TV\b", "Nippon TV"),
    (r"\bTBS\b", "TBS"),
]


def extract_platform(text: str):
    best = None
    for pat, name in _PLATFORMS:
        m = re.search(pat, text)
        if m and (best is None or m.start() < best[0]):
            best = (m.start(), name)
    return best[1] if best else None


_NAME = r"[A-Z][\w!&\-·]*(?:\s+[A-Z][\w!&\-·]*){0,2}"
_STUDIO_PATTERNS = [
    re.compile(rf"\b(?:animated|animation|produced|production)\s+(?:is\s+|was\s+|will\s+be\s+)?(?:being\s+)?(?:handled\s+|produced\s+|created\s+)?(?:by|at)\s+(?:the\s+)?({_NAME})"),
    re.compile(r"\b(?:by|at|from|with)\s+(Studio\s+[A-Z][\w!&\-·]*)"),
    # "Studio Tumble has dropped a new trailer..." / "the Studio Tumble and Flint Sugar co-production"
    re.compile(r"(?:^|[.!?]\s+|\bthe\s+)(Studio\s+[A-Z][\w!&\-·]*)"),
]
_STUDIO_SKIP = {"Netflix", "Crunchyroll", "Amazon", "Disney", "Hulu", "The"}


def extract_studio(text: str):
    for rx in _STUDIO_PATTERNS:
        m = rx.search(text)
        if m and m.group(1).split()[0] not in _STUDIO_SKIP:
            return m.group(1).strip()
    return None


# --------------------------------------------------------------------------- put it together
def enrich(article: dict, title: str, body: str) -> dict:
    title = clean_text(title)
    youtube = extract_youtube_url(article.get("raw_html", ""))
    kind = detect_kind(title, body, bool(youtube))

    facts = f"{title}. {body}"
    date = extract_date(facts, article.get("published"))
    if kind == "date" and not date:
        kind = None  # a "premiere date" label without a date would be a lie

    if kind in MEDIA_KINDS:
        official = re.search(r"\bofficial\b", f"{title} {body[:400]}", re.I)
        label = f"{'Official ' if official else ''}{KIND_NAME[kind]} Released"
    else:
        label = KIND_LABEL.get(kind)

    platform, studio = extract_platform(facts), extract_studio(facts)
    show = extract_show(title)
    title_words = story.tokens(title, drop_generic=True)
    tok = story.tokens(show) if show else title_words
    sentences = split_sentences(body)
    if sentences and sentences[0].strip().lower() == title.strip().lower():
        sentences = sentences[1:]

    return {
        **article,
        "title_en": title,
        "kind": kind,
        "is_media": kind in MEDIA_KINDS,
        "show": show,
        "story_tokens": " ".join(sorted(tok)),
        "title_tokens": " ".join(sorted(title_words)),
        "search_name": show or " ".join(title_words[:6]) or None,
        "label": label,
        "date": date,
        "platform": platform,
        "studio": studio,
        "info": [x for x in (date, platform, studio) if x],
        "sentences": sentences,
        "youtube_url": youtube,
    }
