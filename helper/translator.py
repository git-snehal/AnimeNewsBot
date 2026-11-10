from deep_translator import GoogleTranslator

def translate_text(text: str, target_lang: str = 'en') -> str:
    if not text:
        return ""
    try:
        translated = GoogleTranslator(source='auto', target=target_lang).translate(text)
        return translated if translated else text
    except Exception:
        return text