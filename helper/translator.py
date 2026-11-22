from deep_translator import GoogleTranslator


def _needs_translation(text: str) -> bool:
    """Only translate when a real share of the letters are CJK/Hangul/etc. English passes through untouched."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return False
    foreign = sum(1 for c in letters if ord(c) >= 0x2E80)
    return foreign / len(letters) > 0.15


def translate_if_needed(text: str, target_lang: str = "en") -> str:
    """Blocking - call it through asyncio.to_thread()."""
    if not text or not _needs_translation(text):
        return text or ""
    try:
        out = GoogleTranslator(source="auto", target=target_lang).translate(text[:4500])
        return out or text
    except Exception:
        return text
