"""Images the bot draws itself: the IMDb episode-rating card and the Netflix Top 10 chart (both 1080x1350, 4:5,
which Telegram shows in full). Fonts are bundled in assets/fonts (SIL Open Font License)."""
import io
import math
import os

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
W, H = 1080, 1350
GOLD = (245, 197, 24)
RED = (229, 9, 20)
WHITE = (255, 255, 255)


def _font(size: int, weight: str = "Regular"):
    f = ImageFont.truetype(os.path.join(FONT_DIR, "Inter.ttf"), size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def _bebas(size: int):
    return ImageFont.truetype(os.path.join(FONT_DIR, "BebasNeue.ttf"), size)


def open_image(data: bytes):
    return Image.open(io.BytesIO(data)).convert("RGB")


def _to_jpeg(img) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=92, optimize=True)
    return out.getvalue()


def _star(d, cx, cy, r, fill):
    pts = []
    for i in range(10):
        a = -math.pi / 2 + i * math.pi / 5
        rr = r if i % 2 == 0 else r * 0.45
        pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
    d.polygon(pts, fill=fill)


def _pill(img, text, cy, font, fill=(0, 0, 0), color=WHITE):
    d = ImageDraw.Draw(img)
    tw = d.textlength(text, font=font)
    d.rounded_rectangle([(W - tw) / 2 - 22, cy - 24, (W + tw) / 2 + 22, cy + 24], 24, fill=fill)
    d.text((W / 2, cy), text, font=font, fill=color, anchor="mm")


def _fit_text(d, text, make_font, sizes, max_w):
    """Largest font size from `sizes` at which `text` fits in max_w; wraps into 2 lines if even the smallest doesn't."""
    for size in sizes:
        f = make_font(size)
        if d.textlength(text, font=f) <= max_w:
            return f, [text]
    f = make_font(sizes[-1])
    words, lines, cur = text.split(), [], ""
    for w in words:
        test = (cur + " " + w).strip()
        if d.textlength(test, font=f) <= max_w or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    if len(lines) > 2:
        lines = [lines[0], " ".join(lines[1:])]
        while d.textlength(lines[1] + "…", font=f) > max_w and " " in lines[1]:
            lines[1] = lines[1].rsplit(" ", 1)[0]
        lines[1] += "…"
    return f, lines


def _fmt_votes(n: int) -> str:
    return f"{n:,}"


def rating_card(poster: bytes, show: str, season, episode, rating: float, votes: int, handle: str,
                credit: str = "Rating data: IMDb") -> bytes:
    """Portrait card: blurred poster background, the poster, show name, Season • Episode, gold ★ rating, votes."""
    cover = open_image(poster)
    bg = ImageOps.fit(cover, (W, H)).filter(ImageFilter.GaussianBlur(40))
    bg = Image.blend(bg, Image.new("RGB", (W, H), (8, 8, 14)), 0.55).convert("RGBA")
    fade = Image.linear_gradient("L").resize((W, H))
    fade = fade.point(lambda v: int(255 * max(0, (v / 255 * H - 560) / (H - 560)) ** 1.2) if v / 255 * H > 560 else 0)
    bg.paste(Image.new("RGBA", (W, H), (6, 6, 10, 255)), (0, 0), fade)

    pw = 560
    ph = min(int(cover.height * pw / cover.width), 790)
    poster_img = ImageOps.fit(cover, (pw, ph), Image.LANCZOS).convert("RGBA")
    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, pw, ph], 22, fill=255)
    px, py = (W - pw) // 2, 110
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle([px + 8, py + 18, px + pw + 8, py + ph + 18], 26, fill=(0, 0, 0, 170))
    bg = Image.alpha_composite(bg, shadow.filter(ImageFilter.GaussianBlur(18)))
    bg.paste(poster_img, (px, py), mask)
    d = ImageDraw.Draw(bg)

    _pill(bg, handle, 60, _font(30, "SemiBold"))

    y = py + ph + 46
    tag, f = "EPISODE RATING", _font(26, "Bold")
    tw = d.textlength(tag, font=f)
    d.rounded_rectangle([(W - tw) / 2 - 20, y - 22, (W + tw) / 2 + 20, y + 22], 8, fill=GOLD)
    d.text((W / 2, y), tag, font=f, fill=(15, 15, 15), anchor="mm")

    f, lines = _fit_text(d, show.upper(), _bebas, [72, 64, 56, 50], W - 120)
    y += 74 if len(lines) == 1 else 60
    for line in lines:
        d.text((W / 2, y), line, font=f, fill=WHITE, anchor="mm")
        y += f.size * 0.92
    y -= f.size * 0.92
    sub = "  •  ".join(x for x in (f"Season {season}" if season else "", f"Episode {episode}" if episode else "") if x)
    y += 58
    d.text((W / 2, y), sub, font=_font(34, "Medium"), fill=(200, 200, 210), anchor="mm")

    y += 92
    fr, fs = _font(120, "Black"), _font(48, "Bold")
    r_txt, s_txt = f"{rating:.1f}", "/10"
    wr, ws = d.textlength(r_txt, font=fr), d.textlength(s_txt, font=fs)
    x0 = (W - (84 + 22 + wr + 10 + ws)) / 2
    _star(d, x0 + 42, y + 4, 44, GOLD)
    d.text((x0 + 106, y), r_txt, font=fr, fill=GOLD, anchor="lm")
    d.text((x0 + 106 + wr + 10, y + 22), s_txt, font=fs, fill=(220, 220, 220), anchor="lm")

    y = min(y + 92, H - 80)
    d.text((W / 2, y), f"{_fmt_votes(votes)} votes on IMDb", font=_font(32, "SemiBold"), fill=(235, 235, 235), anchor="mm")
    d.text((W / 2, H - 34), credit, font=_font(22), fill=(140, 140, 150), anchor="mm")
    return _to_jpeg(bg)


def netflix_chart(items: list, heading: str, subheading: str, handle: str, footer: str) -> bytes:
    """items: [{"title", "sub", "line1", "line2", "line3", "poster": bytes|None}] in rank order (1-8 rows)."""
    bg = Image.new("RGB", (W, H), (12, 12, 16))
    glow = Image.new("RGB", (W, H), (0, 0, 0))
    ImageDraw.Draw(glow).ellipse([-300, -500, W + 300, 420], fill=(90, 8, 14))
    bg = Image.blend(bg, glow.filter(ImageFilter.GaussianBlur(120)), 0.6).convert("RGBA")
    d = ImageDraw.Draw(bg)

    _pill(bg, handle, 57, _font(28, "SemiBold"))
    hf, _ = _fit_text(d, heading, _bebas, [92, 84, 76, 68], W - 100)
    d.text((W / 2, 150), heading, font=hf, fill=WHITE, anchor="mm")
    d.text((W / 2, 222), subheading, font=_font(32, "Medium"), fill=(210, 210, 215), anchor="mm")
    d.rectangle([W / 2 - 60, 262, W / 2 + 60, 268], fill=RED)

    n = max(1, len(items))
    gap, top, bottom = 18, 300, H - 76
    row_h = min(300, (bottom - top - gap * (n - 1)) / n)
    k = row_h / 300
    y = top
    for i, it in enumerate(items, 1):
        overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ImageDraw.Draw(overlay).rounded_rectangle([60, y, W - 60, y + row_h], int(22 * k) + 6,
                                                  fill=(255, 255, 255, 16), outline=(255, 255, 255, 40), width=2)
        bg = Image.alpha_composite(bg, overlay)
        d = ImageDraw.Draw(bg)
        d.text((60 + 58 * max(k, 0.6), y + row_h / 2), str(i), font=_bebas(int(150 * max(k, 0.5))),
               fill=RED if i == 1 else (235, 235, 235), anchor="mm")
        ph = int(row_h - 30 * k)
        pw = int(ph * 0.70)
        px = int(60 + 120 * max(k, 0.6))
        if it.get("poster"):
            try:
                p = ImageOps.fit(open_image(it["poster"]), (pw, ph), Image.LANCZOS)
                m = Image.new("L", (pw, ph), 0)
                ImageDraw.Draw(m).rounded_rectangle([0, 0, pw, ph], int(14 * k) + 4, fill=255)
                bg.paste(p, (px, int(y + 15 * k)), m)
            except Exception:
                pass
        tx = px + pw + int(36 * k) + 10
        maxw = W - 80 - tx
        if k > 0.7:
            tf, tl = _fit_text(d, it["title"].upper(), _bebas, [int(58 * k) + 4, int(50 * k) + 4, int(44 * k) + 4], maxw)
            lines = [(tl[0] if len(tl) == 1 else tl[0] + "…", tf, WHITE),
                     (it.get("sub") or "", _font(int(28 * k) + 2, "Medium"), (185, 185, 195)),
                     (it.get("line1") or "", _font(int(32 * k) + 2, "Bold"), GOLD),
                     (it.get("line2") or "", _font(int(28 * k) + 2), (220, 220, 225)),
                     (it.get("line3") or "", _font(int(22 * k) + 2), (140, 140, 150))]
        else:  # compact rows (5+ titles): three readable lines instead of five tiny ones
            tf, tl = _fit_text(d, it["title"].upper(), _bebas, [46, 42, 38], maxw)
            lines = [(tl[0] if len(tl) == 1 else tl[0] + "…", tf, WHITE),
                     (" • ".join(x for x in (it.get("sub"), it.get("line1")) if x), _font(24, "Bold"), GOLD),
                     (it.get("line2") or "", _font(22), (215, 215, 220))]
        heights = [f.size * (1.12 if j == 0 else 1.32) for j, (_, f, _) in enumerate(lines)]
        ty = y + (row_h - sum(heights)) / 2
        for (text, f, color), hh in zip(lines, heights):
            while text and d.textlength(text, font=f) > maxw:
                text = text[:-2].rstrip(" ,") + "…" if not text.endswith("…") else text[:-2] + "…"
            d.text((tx, ty + hh / 2), text, font=f, fill=color, anchor="lm")
            ty += hh
        y += row_h + gap
    d.text((W / 2, H - 34), footer, font=_font(22), fill=(130, 130, 140), anchor="mm")
    return _to_jpeg(bg)
