"""Deciding whether two articles are the same story, whichever outlet wrote them.

We compare the *important words* of the show name (or of the title when no show name could be found),
ignoring accents, punctuation, articles and generic news words. Same news type is required too.
"""
import re
import unicodedata

MEDIA = {"trailer", "teaser", "pv"}

_STOP = {
    "a", "an", "the", "of", "to", "and", "in", "on", "for", "with", "at", "by", "from", "as", "is", "are", "its",
    "it", "be", "or", "my", "me", "your", "his", "her", "their", "this", "that", "into", "than", "who", "what",
}
# words that describe the *news*, not the show (only dropped when we have to use the whole title)
_GENERIC = {
    "anime", "manga", "tv", "film", "movie", "movies", "game", "games", "season", "seasons", "sequel", "new", "main",
    "promo", "video", "videos", "trailer", "teaser", "pv", "visual", "visuals", "key", "cast", "staff", "debut",
    "premiere", "premieres", "date", "release", "releases", "reveals", "revealed", "reveal", "unveils", "unveiled",
    "announces", "announced", "gets", "get", "streams", "live", "action", "short", "episode", "episodes", "part",
    "theme", "song", "songs", "artist", "artists", "opening", "ending", "member", "members", "additional", "latest",
    "first", "second", "third", "final", "full", "title", "starring", "joins", "join", "casts", "more", "also",
    "highlights", "introduces", "previews", "features", "drops", "shares", "confirms", "adds", "arc", "series",
    "original", "novel", "novels", "light", "web", "webtoon", "official", "character", "characters",
}


def tokens(text: str, drop_generic: bool = False) -> list:
    t = unicodedata.normalize("NFKD", text or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    out, seen = [], set()
    for w in re.findall(r"[a-z0-9]+", t):
        if len(w) < 2 or w in _STOP or w.isdigit() or re.fullmatch(r"\d+(?:st|nd|rd|th)", w):
            continue
        if drop_generic and w in _GENERIC:
            continue
        if w not in seen:
            seen.add(w)
            out.append(w)
    return out


def kinds_compatible(a, b) -> bool:
    """Same news type, or both video-type (a 'trailer' and a 'new video' of the same show are the same news)."""
    if not a or not b or a == "news" or b == "news":
        return False
    return a == b or (a in MEDIA and b in MEDIA)


def same_story(tok_a, tok_b) -> bool:
    A, B = set(tok_a), set(tok_b)
    if not A or not B:
        return False
    inter = A & B
    small = min(len(A), len(B))
    if small == 1:  # one-word show names ("Shozen") are only trusted when the word is distinctive
        return len(inter) == 1 and len(next(iter(inter))) >= 6
    return len(inter) >= 2 and len(inter) / small >= 0.8


def same_news(tok_a, tok_b) -> bool:
    """Plain news (no video / season / cast type): the headlines must share at least 3 distinctive words and
    the shorter headline must be almost entirely contained in the other."""
    A, B = set(tok_a), set(tok_b)
    if len(A) < 3 or len(B) < 3:
        return False
    inter = A & B
    return len(inter) >= 3 and len(inter) / min(len(A), len(B)) >= 0.8
