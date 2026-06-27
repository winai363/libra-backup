"""
cover_generator.py — TYPE/PHOTO/ILLUSTRATION-led KDP cover generator.

Rewritten 2026-06 from bestseller research (2025-2026 Amazon top covers):
the TITLE is the hero for most non-fiction, not a stock photo. Three families:

  • type-led  → finance/business (serif authority), AI/tech (bold sans),
                wellness/self-help (calm), seniors (high-contrast large print)
  • photo-led → food/cookbooks (one appetising hero dish + bottom title band)
  • illustration-led → creative/watercolor & children (hero artwork + top title)

Every element is measured with real glyph bounds (bbox) and stacked
sequentially, so decorations never collide with text and nothing touches the
frame. Covers are keyed by niche palette so a batch never looks duplicated.

Public API preserved for callers (kdp_upload, app, gpt_fallback_writer,
quality_gate): generate_cover(...), detect_genre(...), unrenderable_chars(...).

Cover size: 1600 x 2560 px (KDP 6x9 paperback @ ~300dpi).
NEVER asks an image model to render text — all text is overlaid with Pillow.
"""
from __future__ import annotations
from pathlib import Path


# ── Load OpenAI key from .env ───────────────────────────────────────────────
def _load_openai_key() -> str:
    env_path = Path(__file__).parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("OPENAI_API_KEY="):
                return line.split("=", 1)[1].strip()
    import os
    return os.environ.get("OPENAI_API_KEY", "")


# ── Fonts ───────────────────────────────────────────────────────────────────
_FONT_BASE = "/usr/share/fonts/truetype/dejavu/"
FONT_SANS_BOLD   = _FONT_BASE + "DejaVuSans-Bold.ttf"
FONT_SANS        = _FONT_BASE + "DejaVuSans.ttf"
FONT_SERIF_BOLD  = _FONT_BASE + "DejaVuSerif-Bold.ttf"

# Display faces installed for the redesign (bestseller-grade type).
_DISP = "/usr/share/fonts/truetype/libra/"
FONT_ANTON     = _DISP + "Anton-Regular.ttf"        # heavy condensed — AI/tech
FONT_MONTSERRAT = _DISP + "Montserrat-Black.ttf"    # geometric black — wellness
FONT_PLAYFAIR  = _DISP + "PlayfairDisplay.ttf"      # serif authority — finance
FONT_OSWALD    = _DISP + "Oswald.ttf"               # condensed — food
FONT_ARCHIVO   = _DISP + "ArchivoBlack-Regular.ttf" # blocky black — seniors
FONT_BEBAS     = "/usr/share/fonts/opentype/bebas-neue/BebasNeue-Bold.otf"
FONT_LATO_BOLD = "/usr/share/fonts/truetype/lato/Lato-Bold.ttf"
FONT_LATO_BLACK = "/usr/share/fonts/truetype/lato/Lato-Black.ttf"

# CJK (Japanese / Chinese / Korean) — DejaVu/display faces have no CJK glyphs.
_NOTO_BASE = "/usr/share/fonts/opentype/noto/"
FONT_CJK_BOLD = _NOTO_BASE + "NotoSansCJK-Bold.ttc"
FONT_CJK      = _NOTO_BASE + "NotoSansCJK-Regular.ttc"
_CJK_FONT_INDEX = 0   # Noto Sans CJK JP

# Thai needs its own font + raqm shaping for tone marks / floating vowels.
_NOTO_TTF = "/usr/share/fonts/truetype/noto/"
FONT_THAI_BOLD = _NOTO_TTF + "NotoSansThai-Bold.ttf"
FONT_THAI      = _NOTO_TTF + "NotoSansThai-Regular.ttf"

# Thai non-spacing combining marks — a line must never break before one.
_THAI_COMBINING = set(range(0x0E34, 0x0E3B)) | {0x0E31} | set(range(0x0E47, 0x0E4F))


def _is_thai(text: str) -> bool:
    return any(0x0E00 <= ord(ch) <= 0x0E7F for ch in text)


def _is_cjk(text: str) -> bool:
    for ch in text:
        o = ord(ch)
        if (0x3040 <= o <= 0x30FF or 0x4E00 <= o <= 0x9FFF
                or 0x3400 <= o <= 0x4DBF or 0xAC00 <= o <= 0xD7A3
                or 0xFF00 <= o <= 0xFFEF):
            return True
    return False


def _cjk_font(bold: bool):
    return (FONT_CJK_BOLD if bold else FONT_CJK), _CJK_FONT_INDEX


# ── Glyph-coverage check (anti-tofu) ───────────────────────────────────────
_CMAP_CACHE: dict = {}


def _font_glyphs(path: str, index: int = 0):
    key = (path, index)
    if key not in _CMAP_CACHE:
        try:
            from fontTools.ttLib import TTFont
            if path.lower().endswith((".ttc", ".otc")):
                ft = TTFont(path, fontNumber=index)
            else:
                ft = TTFont(path)
            _CMAP_CACHE[key] = set(ft.getBestCmap().keys())
        except Exception:
            _CMAP_CACHE[key] = None
    return _CMAP_CACHE[key]


def _missing_chars(path: str, index: int, text: str) -> list[str]:
    glyphs = _font_glyphs(path, index)
    if glyphs is None:
        return []
    out: list[str] = []
    for ch in text or "":
        if ch.isspace() or ord(ch) < 0x20:
            continue
        if ord(ch) not in glyphs and ch not in out:
            out.append(ch)
    return out


# ── Canvas ──────────────────────────────────────────────────────────────────
W, H = 1600, 2560
MARGIN = 150
CW = W - MARGIN * 2          # content width
CX = W // 2                  # horizontal centre


# ── Genre detection (preserved) ─────────────────────────────────────────────
_GENRE_KEYWORDS: dict[str, list[str]] = {
    "wellness": [
        "anxiety", "angst", "stress", "wellness", "mindfulness", "meditation",
        "mental health", "gelassenheit", "calm", "peace", "therapy", "healing",
        "emotional", "self-care", "depression", "burnout", "resilience",
        "trauma", "fear", "worry", "panic", "breath", "relaxation",
        "adhd", "adhs", "tdah", "neurodiv", "aufmerksamkeit", "overwhelm",
        "achtsamkeit", "entspannung", "wohlbefinden", "sleep", "sommeil",
        "不安", "ストレス", "瞑想", "マインドフルネス", "メンタル", "心の健康",
        "癒し", "焦虑", "压力", "冥想",
    ],
    "tech": [
        "ai", "artificial intelligence", "digital", "technology", "computer",
        "software", "data", "programming", "machine learning", "algorithm",
        "automation", "coding", "developer", "database", "cloud", "cyber",
        "intelligenza artificiale", "tecnologia", "digitale", "chatgpt",
        "prompt", "künstliche intelligenz", "technologie", "ia", "ki",
        "デジタル", "プログラミング", "テクノロジー", "人工知能", "技術",
        "数字", "编程", "技术", "人工智能",
    ],
    "business": [
        "business", "entrepreneur", "startup", "management", "leadership",
        "strategy", "marketing", "productivity", "success", "corporate",
        "negotiation", "sales", "brand", "customer", "revenue", "profit",
        "azienda", "imprenditore", "strategie", "unternehmen", "freelance",
        "ビジネス", "起業", "経営", "マーケティング", "営業", "副業",
        "リーダーシップ", "生産性", "商业", "创业", "管理",
    ],
    "finance": [
        "finance", "money", "invest", "wealth", "budget", "saving", "income",
        "financial", "economia", "finanza", "geld", "finanzen", "banque",
        "stock", "trading", "crypto", "asset", "retire", "debt", "tax",
        "impuesto", "impuestos", "renta", "autónomo", "autonomos", "fiscal",
        "iva", "hacienda", "deduccion", "deducciones", "ahorro", "เงิน",
        "การเงิน", "ลงทุน", "家計", "貯金", "貯蓄", "投資", "お金",
        "副収入", "収入", "節約", "予算", "資産", "借金", "年金", "税",
        "理财", "投资", "储蓄",
    ],
    "food": [
        "recipe", "rezept", "rezepte", "cook", "cooking", "food", "drink",
        "drinks", "cocktail", "mocktail", "beverage", "baking", "cuisine",
        "chef", "meal", "dish", "ingredient", "nutrition", "diet", "eating",
        "kitchen", "restaurant", "wine", "coffee", "tea", "smoothie", "juice",
        "getränk", "kochen", "küche", "backen", "ernährung", "protein",
        "ricetta", "cucina", "bevanda", "receta", "bebida", "cocina",
        "alkoholfrei", "sober", "repas", "protéiné", "proteine",
        "レシピ", "料理", "食事", "ごはん", "お菓子", "飲み物", "食",
        "食谱", "烹饪", "美食",
    ],
    "creative": [
        "creative", "creativity", "workbook", "journal", "art", "design",
        "writing", "paint", "watercolor", "watercolour", "acuarela",
        "aquarelle", "craft", "draw", "drawing", "sketch", "poetry",
        "fiction", "story", "template", "quaderno", "creativo", "kreativ",
        "coloring", "colouring", "malen", "zeichnen",
        "創作", "アート", "デザイン", "イラスト", "絵", "手帳", "創造",
        "创作", "艺术", "设计",
    ],
    "children": [
        "children", "kids", "juvenile", "child", "young adult", "teen kids",
        "toddler", "bambini", "kinder", "enfant", "niños", "vocabulary kids",
        "bilingual kids", "preschool", "picture book",
    ],
    "selfhelp": [
        "self-help", "self help", "personal growth", "habit", "motivation",
        "confidence", "mindset", "goal", "overcome", "transform", "improve",
        "happiness", "positive", "wachstum", "selbsthilfe", "stoicism",
        "stoic", "stoicisme", "stoicismo", "discipline",
        "sviluppo personale", "crescita personale",
        "自己啓発", "習慣", "モチベーション", "目標", "成長", "自信", "幸せ",
        "自我提升", "习惯", "目标",
    ],
    "seniors": [
        "senior", "senioren", "seniorin", "älter", "rentner", "ruhestand",
        "ältere menschen", "retiree", "elderly", "60 plus", "50 plus",
        "smartphone für senioren", "internet für senioren", "large print",
        "computer für senioren", "nonno", "nonna", "anziani", "idosos",
        "シニア", "高齢者", "年配", "老後", "老年", "长辈",
    ],
}


def detect_genre(title: str, categories: list[str], keywords: list[str]) -> str:
    """Return the best-matching genre key for the given book metadata.

    Matching rule (avoids false hits like "ia" inside "principiantes"):
      • multi-word or non-ASCII keyword → substring match
      • short ASCII keyword (≤3 chars: ai, ia, ki, tax, iva) → whole-word only
      • longer ASCII keyword → substring (catches plurals: impuesto→impuestos)
    """
    import re
    text = " ".join([title] + (categories or []) + (keywords or [])).lower()
    tokens = set(re.findall(r"[a-zà-ÿ0-9]+", text))

    def hit(w: str) -> bool:
        if " " in w or not w.isascii():
            return w in text
        if len(w) <= 3:
            return w in tokens
        return w in text

    scores = {g: sum(1 for w in words if hit(w)) for g, words in _GENRE_KEYWORDS.items()}
    best = max(scores, key=lambda g: scores[g])
    return best if scores[best] > 0 else "default"


# ── Per-niche cover recipe ──────────────────────────────────────────────────
# family: which layout renderer to use. Colours are bestseller-keyed.
def _hex(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


COVER: dict[str, dict] = {
    "finance": dict(family="authority", title_font=FONT_PLAYFAIR, title_var=800,
                    bg_top="#faf5e9", bg_bot="#f0e7d4", ink="#10243f",
                    accent="#c19a46", sub="#46505c",
                    strap="GUÍA PRÁCTICA", motif="euro"),
    "business": dict(family="authority", title_font=FONT_PLAYFAIR, title_var=800,
                     bg_top="#0e2a47", bg_bot="#091d33", ink="#ffffff",
                     accent="#d4af37", sub="#cfd6e2",
                     strap="STRATEGY GUIDE", motif="none", dark=True),
    "tech": dict(family="modern", title_font=FONT_ANTON, title_var=None,
                 bg_top="#5b2ee5", bg_bot="#e04696", ink="#ffffff",
                 accent="#c8ff3d", sub="#ffffff"),
    "default": dict(family="modern", title_font=FONT_ANTON, title_var=None,
                    bg_top="#2746b0", bg_bot="#101d57", ink="#ffffff",
                    accent="#ffc832", sub="#dbe6ff"),
    "wellness": dict(family="calm", title_font=FONT_MONTSERRAT, title_var=900,
                     bg_top="#9db4c0", bg_bot="#6c8a9a", ink="#26343c",
                     accent="#c97b5a", band="#f6f3ec", sub="#26343c"),
    "selfhelp": dict(family="calm", title_font=FONT_MONTSERRAT, title_var=900,
                     bg_top="#7fb0a3", bg_bot="#3f7d70", ink="#15302a",
                     accent="#e8a13c", band="#f4f1e6", sub="#15302a"),
    "seniors": dict(family="senior", title_font=FONT_ARCHIVO, title_var=None,
                    bg_top="#1565c0", bg_bot="#0d4a93", ink="#ffffff",
                    accent="#ffb300", band="#ffffff", sub="#0d3a73"),
    "food": dict(family="photo", title_font=FONT_OSWALD, title_var=600,
                 panel="#14402e", ink="#ffffff", accent="#ffd166",
                 sub="#def5e8"),
    "creative": dict(family="illustration", title_font=FONT_PLAYFAIR, title_var=700,
                     panel="#ffffff", ink="#2a2a33", accent="#c2566b",
                     sub="#55555f"),
    "children": dict(family="illustration", title_font=FONT_MONTSERRAT, title_var=900,
                     panel="#ffffff", ink="#1e2433", accent="#ff6b5c",
                     sub="#1e2433", kids=True),
}

# Per-book palette variants so books in the SAME niche never look cloned.
# Chosen deterministically from the title hash → stable across regenerations,
# but a batch of AI/wellness/finance books gets a varied, non-farmed wall.
VARIANTS: dict[str, list[dict]] = {
    "modern": [
        dict(bg_top="#5b2ee5", bg_bot="#e04696", accent="#c8ff3d"),  # indigo→magenta / lime
        dict(bg_top="#1e3a8a", bg_bot="#06b6d4", accent="#ffd400"),  # blue→cyan / yellow
        dict(bg_top="#0f766e", bg_bot="#10b981", accent="#fef08a"),  # teal→green / butter
        dict(bg_top="#7c3aed", bg_bot="#312e81", accent="#22d3ee"),  # violet→indigo / cyan
        dict(bg_top="#0b1220", bg_bot="#1d4ed8", accent="#f472b6"),  # night→blue / pink
        dict(bg_top="#be185d", bg_bot="#f97316", accent="#fde047"),  # magenta→orange / gold
        dict(bg_top="#2563eb", bg_bot="#6d28d9", accent="#a3e635"),  # royal→purple / lime
        dict(bg_top="#0f172a", bg_bot="#059669", accent="#fbbf24"),  # slate→emerald / amber
    ],
    "calm": [
        dict(bg_top="#9db4c0", bg_bot="#6c8a9a", ink="#26343c", accent="#c97b5a", band="#f6f3ec"),  # dusty blue
        dict(bg_top="#a7c4a0", bg_bot="#6f9676", ink="#26342a", accent="#c97b5a", band="#f6f3ec"),  # sage
        dict(bg_top="#b8a9d0", bg_bot="#8b78b0", ink="#2e2a3a", accent="#e0a13c", band="#f6f1ec"),  # lavender
        dict(bg_top="#7fb0a3", bg_bot="#3f7d70", ink="#15302a", accent="#e8a13c", band="#f4f1e6"),  # teal
        dict(bg_top="#d3a9a4", bg_bot="#a87d77", ink="#3a2a28", accent="#6a8a7a", band="#f6f0ec"),  # dusty rose
        dict(bg_top="#8ea3bf", bg_bot="#5d6f93", ink="#222a3a", accent="#d98c5f", band="#f3f1ea"),  # periwinkle
    ],
    "authority": [
        dict(bg_top="#faf5e9", bg_bot="#f0e7d4", ink="#10243f", accent="#c19a46", sub="#46505c"),  # cream
        dict(bg_top="#0e2a47", bg_bot="#091d33", ink="#ffffff", accent="#d4af37", sub="#cfd6e2"),  # navy
        dict(bg_top="#14532d", bg_bot="#0a3d20", ink="#f5f0e1", accent="#d4af37", sub="#cfe2d2"),  # forest
        dict(bg_top="#f3ece0", bg_bot="#e6dac4", ink="#3a1d24", accent="#9c5b3b", sub="#5a4a44"),  # warm sand
    ],
}


# Keywords that get the neon highlight on the "modern" (AI/tech) template.
_HIGHLIGHT = {
    "ai", "ia", "ki", "chatgpt", "gpt", "prompt", "prompts", "engineering",
    "automation", "automatisierung", "automatización", "productivity",
    "productividad", "productivité", "produktivität",
}


# ── Font loading + measurement ──────────────────────────────────────────────
def _font(path, size, var=None, index=0, raqm=False):
    from PIL import ImageFont
    layout = ImageFont.Layout.RAQM if raqm else ImageFont.Layout.BASIC
    f = ImageFont.truetype(path, size, index=index, layout_engine=layout)
    if var is not None:
        try:
            f.set_variation_by_axes([var])
        except Exception:
            pass
    return f


def _choose_title_face(genre: str, text: str):
    """Pick (path, var, index, raqm) for the title, script-aware with glyph
    fallback so a missing glyph never renders as a tofu box."""
    if _is_thai(text):
        return FONT_THAI_BOLD, None, 0, True
    if _is_cjk(text):
        p, i = _cjk_font(bold=True)
        return p, None, i, False
    want = COVER.get(genre, COVER["default"])["title_font"]
    var = COVER.get(genre, COVER["default"]).get("title_var")
    # Glyph fallback chain for Latin/Turkish accents the display face may lack.
    for path, v in [(want, var), (FONT_MONTSERRAT, 900), (FONT_SANS_BOLD, None)]:
        if not _missing_chars(path, 0, text):
            return path, v, 0, False
    return FONT_SANS_BOLD, None, 0, False


def _body_face(text: str, bold=True):
    """Subtitle/strap face, script-aware."""
    if _is_thai(text):
        return (FONT_THAI_BOLD if bold else FONT_THAI), None, 0, True
    if _is_cjk(text):
        p, i = _cjk_font(bold=bold)
        return p, None, i, False
    return (FONT_LATO_BOLD), None, 0, False


def unrenderable_chars(title: str, subtitle: str = "", author: str = "") -> list[str]:
    """Cover-text characters that would render as tofu with the fonts actually
    selected. Empty = safe. Used by quality_gate."""
    tp, tv, ti, _ = _choose_title_face("default", title)
    sp, sv, si, _ = _body_face(subtitle or "")
    missing: list[str] = []
    for ch in (_missing_chars(tp, ti, title)
               + _missing_chars(sp, si, subtitle)
               + _missing_chars(FONT_BEBAS, 0, author)):
        if ch not in missing:
            missing.append(ch)
    return missing


def _line_w(draw, text, fnt):
    bb = draw.textbbox((0, 0), text, font=fnt)
    return bb[2] - bb[0]


# ── Script-aware word wrap ──────────────────────────────────────────────────
_THAI_BREAKER = None


def _thai_words(text: str) -> list[str]:
    global _THAI_BREAKER
    try:
        import icu
        if _THAI_BREAKER is None:
            _THAI_BREAKER = icu.BreakIterator.createWordInstance(icu.Locale("th"))
        bi = _THAI_BREAKER
        bi.setText(text)
        words, prev = [], 0
        for pos in bi:
            words.append(text[prev:pos])
            prev = pos
        return [w for w in words if w]
    except Exception:
        return [text]


def _wrap(draw, text, fnt, maxw):
    """Word-wrap text to fit maxw px/line. CJK = char-wrap, Thai = ICU words,
    Latin = space split."""
    if _is_cjk(text):
        no_break = "。、）」』】〕》〉”’!?！？.,)"
        lines, line = [], ""
        for ch in text:
            cand = line + ch
            if _line_w(draw, cand, fnt) > maxw and line and ch not in no_break:
                lines.append(line)
                line = ch
            else:
                line = cand
        if line:
            lines.append(line)
        return lines
    if _is_thai(text):
        lines, line = [], ""
        for tok in _thai_words(text):
            cand = line + tok
            if _line_w(draw, cand, fnt) > maxw and line:
                lines.append(line.rstrip())
                line = "" if tok == " " else tok
            elif tok == " " and not line:
                continue
            else:
                line = cand
        if line.strip():
            lines.append(line.rstrip())
        return lines
    words, lines, line = text.split(), [], ""
    for w in words:
        cand = (line + " " + w).strip()
        if _line_w(draw, cand, fnt) > maxw and line:
            lines.append(line)
            line = w
        else:
            line = cand
    if line:
        lines.append(line)
    return lines


def _fit(draw, text, path, var, index, raqm, maxw, max_h,
         start, min_s, max_lines, leading_f=0.16):
    """Shrink until every line fits maxw AND the block fits max_h."""
    size = start
    while size >= min_s:
        f = _font(path, size, var, index, raqm)
        lines = _wrap(draw, text, f, maxw)
        widest = max((_line_w(draw, ln, f) for ln in lines), default=0)
        asc, desc = f.getmetrics()
        lh = asc + desc
        leading = int(size * leading_f)
        block_h = len(lines) * lh + (len(lines) - 1) * leading
        if len(lines) <= max_lines and widest <= maxw and block_h <= max_h:
            return f, lines, size, lh, leading
        size -= 4
    f = _font(path, min_s, var, index, raqm)
    lines = _wrap(draw, text, f, maxw)
    asc, desc = f.getmetrics()
    return f, lines, min_s, asc + desc, int(min_s * leading_f)


def _draw_block(draw, lines, top, fnt, lh, leading, fill,
                stroke=None, colors=None):
    sw, sc = (stroke if stroke else (0, None))
    y = top
    for i, ln in enumerate(lines):
        col = colors[i] if colors else fill
        draw.text((CX, y), ln, font=fnt, fill=col, anchor="ma",
                  stroke_width=sw, stroke_fill=sc)
        y += lh + (leading if i < len(lines) - 1 else 0)
    return y


def _strap(draw, text, y, fnt, fill, rule=True):
    draw.text((CX, y), text, font=fnt, fill=fill, anchor="ma")
    asc, desc = fnt.getmetrics()
    yb = y + asc + desc
    if rule:
        w = min(_line_w(draw, text, fnt) + 20, 540)
        draw.rectangle([CX - w // 2, yb + 14, CX + w // 2, yb + 22], fill=fill)
        yb += 30
    return yb


def _author(draw, name, fill, y=H - 210):
    f = _font(FONT_BEBAS, 60)
    draw.text((CX, y), (name or "").upper(), font=f, fill=fill, anchor="ma")


def _gradient(img, top, bot):
    from PIL import ImageDraw
    d = ImageDraw.Draw(img)
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)],
               fill=tuple(int(top[i] + (bot[i] - top[i]) * t) for i in range(3)))


def _subtitle(draw, subtitle, top, fg, band=None, max_lines=3, pad=40):
    """Draw subtitle, optionally on a rounded band. Returns bottom y."""
    if not subtitle:
        return top
    sp, sv, si, sr = _body_face(subtitle)
    f = _font(sp, 50, sv, si, sr)
    lines = _wrap(draw, subtitle, f, CW - 120)[:max_lines]
    asc, desc = f.getmetrics()
    lh = asc + desc
    lead = 14
    block_h = len(lines) * lh + (len(lines) - 1) * lead
    if band is not None:
        draw.rounded_rectangle([MARGIN, top, W - MARGIN, top + block_h + pad * 2],
                               radius=28, fill=band)
        ytext = top + pad
    else:
        ytext = top
    y = ytext
    for i, ln in enumerate(lines):
        draw.text((CX, y), ln, font=f, fill=fg, anchor="ma")
        y += lh + (lead if i < len(lines) - 1 else 0)
    return y + (pad if band is not None else 0)


# ── AI hero image (photo / illustration families only) ──────────────────────
def _ai_image(prompt: str):
    try:
        import openai, base64, io
        from PIL import Image
        key = _load_openai_key()
        if not key:
            return None
        client = openai.OpenAI(api_key=key)
        r = client.images.generate(model="gpt-image-1", prompt=prompt,
                                   size="1024x1536", n=1)
        raw = base64.b64decode(r.data[0].b64_json)
        return Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as e:
        print(f"  hero image failed ({e}) — using fallback panel")
        return None


def _food_prompt(title, keywords):
    kw = ", ".join((keywords or [])[:6])
    return (
        "Professional overhead food photography, ONE single appetising hero dish "
        f"that fits this cookbook: {title}. {kw}. Bright natural daylight, fresh "
        "vibrant ingredients, styled on a warm rustic surface, sharp focus, "
        "magazine quality, warm appetite colours (red, orange, green, yellow). "
        "No text, no words, no letters, no hands, no utensils labels anywhere."
    )


def _illustration_prompt(title, keywords, kids):
    kw = ", ".join((keywords or [])[:6])
    if kids:
        return (
            "Bright friendly flat children's-book illustration, a cheerful grid of "
            f"simple colourful objects relevant to: {title}. {kw}. Bold primary "
            "colours, clean white background, playful, high contrast, no shading "
            "clutter. No text, no words, no letters anywhere."
        )
    return (
        "A single elegant hand-painted watercolour illustration on clean white "
        f"paper that captures this creative book: {title}. {kw}. Loose artistic "
        "brushwork, soft pastel palette, plenty of white space, gallery quality. "
        "No text, no words, no letters anywhere."
    )


# ── Thumbnail legibility check (the #1 bestseller rule) ─────────────────────
def _relative_luminance(c):
    def f(v):
        v /= 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    r, g, b = c
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def _contrast(c1, c2):
    l1, l2 = _relative_luminance(c1), _relative_luminance(c2)
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _thumbnail_ok(out_path) -> bool:
    """Shrink to ~150px wide and verify the title band still has strong
    contrast (proxy for 'readable at Amazon search thumbnail size')."""
    try:
        from PIL import Image
        im = Image.open(out_path).convert("RGB")
        thumb = im.resize((150, 240))
        # sample the title zone (top 18-48%) std-dev of luminance as a
        # rough legibility signal — flat (no text contrast) → low spread.
        crop = thumb.crop((0, int(240 * 0.18), 150, int(240 * 0.48)))
        px = list(crop.getdata())
        lums = [_relative_luminance(p) for p in px]
        mean = sum(lums) / len(lums)
        var = sum((x - mean) ** 2 for x in lums) / len(lums)
        spread = var ** 0.5
        if spread < 0.06:
            print(f"  ⚠️ THUMBNAIL WARNING: low title contrast at 150px "
                  f"(spread={spread:.3f}) — title may be hard to read in search")
            return False
        return True
    except Exception:
        return True


# ── Template renderers ──────────────────────────────────────────────────────
def _render_authority(draw, img, cfg, title, subtitle, author, genre):
    ink, acc = _hex(cfg["ink"]), _hex(cfg["accent"])
    sub = _hex(cfg["sub"])
    _gradient(img, _hex(cfg["bg_top"]), _hex(cfg["bg_bot"]))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    sp, sv, si, sr = _body_face(cfg["strap"])
    y = _strap(draw, cfg["strap"], 230, _font(FONT_BEBAS, 58), acc)
    tp, tv, ti, tr = _choose_title_face(genre, title)
    f, lines, sz, lh, lead = _fit(draw, title, tp, tv, ti, tr, CW, H * 0.40,
                                  start=156, min_s=78, max_lines=4)
    y = _draw_block(draw, lines, y + 70, f, lh, lead, ink)
    draw.rectangle([CX - 170, y + 34, CX + 170, y + 44], fill=acc)
    _subtitle(draw, subtitle, y + 100, sub)
    if cfg.get("motif") == "euro":
        draw.text((CX, H - 470), "€", font=_font(FONT_BEBAS, 120), fill=acc, anchor="ma")
        draw.ellipse([CX - 78, H - 480, CX + 78, H - 320], outline=acc, width=9)
    _author(draw, author, ink)


def _render_modern(draw, img, cfg, title, subtitle, author, genre):
    white, neon = _hex(cfg["ink"]), _hex(cfg["accent"])
    _gradient(img, _hex(cfg["bg_top"]), _hex(cfg["bg_bot"]))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    tp, tv, ti, tr = _choose_title_face(genre, title)
    latin = not (_is_thai(title) or _is_cjk(title))
    text = title.upper() if latin else title
    f, lines, sz, lh, lead = _fit(draw, text, tp, tv, ti, tr, CW, H * 0.46,
                                  start=200, min_s=88, max_lines=5,
                                  leading_f=0.06 if latin else 0.16)
    colors = [neon if any(w.strip(":,.;").lower() in _HIGHLIGHT for w in ln.split())
              else white for ln in lines]
    y = _draw_block(draw, lines, 470, f, lh, lead, white, colors=colors)
    _subtitle(draw, subtitle, y + 80, white, band=(0, 0, 0), max_lines=3)
    _author(draw, author, white)


def _render_calm(draw, img, cfg, title, subtitle, author, genre):
    ink, acc = _hex(cfg["ink"]), _hex(cfg["accent"])
    cream = _hex(cfg["band"])
    _gradient(img, _hex(cfg["bg_top"]), _hex(cfg["bg_bot"]))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    for r in (520, 400, 280):                       # soft breathing-wave motif
        draw.arc([CX - r, 150 - r, CX + r, 150 + r], 0, 180, fill=cream, width=8)
    tp, tv, ti, tr = _choose_title_face(genre, title)
    f, lines, sz, lh, lead = _fit(draw, title, tp, tv, ti, tr, CW, H * 0.32,
                                  start=150, min_s=80, max_lines=4, leading_f=0.18)
    y = _draw_block(draw, lines, 560, f, lh, lead, ink)
    draw.rectangle([CX - 150, y + 30, CX + 150, y + 40], fill=acc)
    _subtitle(draw, subtitle, y + 96, ink, band=cream, max_lines=3)
    _author(draw, author, cream)


def _render_senior(draw, img, cfg, title, subtitle, author, genre):
    white, acc = _hex(cfg["ink"]), _hex(cfg["accent"])
    band = _hex(cfg["band"])
    _gradient(img, _hex(cfg["bg_top"]), _hex(cfg["bg_bot"]))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    # accent header bar — high-contrast, large-print signal
    draw.rectangle([0, 0, W, 70], fill=acc)
    tp, tv, ti, tr = _choose_title_face(genre, title)
    f, lines, sz, lh, lead = _fit(draw, title.upper() if not (_is_thai(title) or _is_cjk(title)) else title,
                                  tp, tv, ti, tr, CW, H * 0.40,
                                  start=180, min_s=92, max_lines=4, leading_f=0.10)
    y = _draw_block(draw, lines, 360, f, lh, lead, white)
    draw.rectangle([CX - 180, y + 30, CX + 180, y + 42], fill=acc)
    _subtitle(draw, subtitle, y + 100, _hex(cfg["sub"]), band=band, max_lines=3)
    _author(draw, author, white)


def _render_photo(draw, img, cfg, title, subtitle, author, genre, keywords):
    from PIL import Image, ImageDraw
    white, acc = _hex(cfg["ink"]), _hex(cfg["accent"])
    panel = _hex(cfg["panel"])
    photo_h = int(H * 0.62)
    hero = _ai_image(_food_prompt(title, keywords))
    if hero is not None:
        img.paste(hero.resize((W, photo_h)), (0, 0))
    else:
        _gradient(img, (210, 104, 60), (150, 60, 30))
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, photo_h, W, H], fill=panel)
    # scrim blending photo into panel
    scrim = Image.new("RGBA", (W, 220), (0, 0, 0, 0))
    ds = ImageDraw.Draw(scrim)
    for i in range(220):
        ds.line([(0, i), (W, i)], fill=(*panel, int(255 * i / 220)))
    img.paste(Image.alpha_composite(
        img.crop((0, photo_h - 220, W, photo_h)).convert("RGBA"), scrim
    ).convert("RGB"), (0, photo_h - 220))
    draw = ImageDraw.Draw(img)
    tp, tv, ti, tr = _choose_title_face(genre, title)
    latin = not (_is_thai(title) or _is_cjk(title))
    f, lines, sz, lh, lead = _fit(draw, title.upper() if latin else title,
                                  tp, tv, ti, tr, CW, H * 0.22,
                                  start=150, min_s=78, max_lines=3, leading_f=0.10)
    y = _draw_block(draw, lines, photo_h + 70, f, lh, lead, white)
    draw.rectangle([CX - 160, y + 26, CX + 160, y + 34], fill=acc)
    _subtitle(draw, subtitle, y + 70, _hex(cfg["sub"]), max_lines=2)
    _author(draw, author, acc)


def _render_illustration(draw, img, cfg, title, subtitle, author, genre, keywords):
    from PIL import Image, ImageDraw
    ink, acc = _hex(cfg["ink"]), _hex(cfg["accent"])
    panel = _hex(cfg["panel"])
    img.paste(Image.new("RGB", (W, H), panel), (0, 0))
    art_top = int(H * 0.40)
    hero = _ai_image(_illustration_prompt(title, keywords, cfg.get("kids", False)))
    if hero is not None:
        art_h = int(H * 0.50)
        img.paste(hero.resize((W, art_h)), (0, art_top))
    draw = ImageDraw.Draw(img)
    # title sits in the clean TOP zone (safest for thumbnail)
    tp, tv, ti, tr = _choose_title_face(genre, title)
    f, lines, sz, lh, lead = _fit(draw, title, tp, tv, ti, tr, CW, H * 0.26,
                                  start=150, min_s=78, max_lines=3, leading_f=0.12)
    y = _draw_block(draw, lines, 150, f, lh, lead, ink)
    draw.rectangle([CX - 150, y + 24, CX + 150, y + 32], fill=acc)
    _subtitle(draw, subtitle, y + 60, _hex(cfg["sub"]), max_lines=2)
    _author(draw, author, ink, y=H - 150)


# ── Main public function (signature preserved) ──────────────────────────────
def generate_cover(
    book_dir,
    title: str,
    subtitle: str,
    author: str,
    categories: list[str] | None = None,
    keywords: list[str] | None = None,
) -> Path:
    """Generate a bestseller-style KDP cover and save it as cover.jpg in
    `book_dir`. Routes to one of three layout families by detected niche.
    Returns the Path to the saved file."""
    from PIL import Image, ImageDraw

    categories = categories or []
    keywords = keywords or []

    genre = detect_genre(title, categories, keywords)
    cfg = COVER.get(genre, COVER["default"])
    family = cfg["family"]

    # Per-book palette variant (deterministic from title) → no cloned batches.
    variants = VARIANTS.get(family)
    if variants:
        import hashlib
        idx = int(hashlib.md5(title.encode("utf-8")).hexdigest(), 16) % len(variants)
        cfg = {**cfg, **variants[idx]}

    img = Image.new("RGB", (W, H), (255, 255, 255))
    draw = ImageDraw.Draw(img)

    # anti-tofu warning (CJK/Thai safety)
    tofu = unrenderable_chars(title, subtitle or "", author or "")
    if tofu:
        print(f"  ⚠️ COVER WARNING: {len(tofu)} char(s) lack a glyph and may "
              f"render as boxes: {''.join(tofu[:20])}")

    if family == "authority":
        _render_authority(draw, img, cfg, title, subtitle, author, genre)
    elif family == "modern":
        _render_modern(draw, img, cfg, title, subtitle, author, genre)
    elif family == "calm":
        _render_calm(draw, img, cfg, title, subtitle, author, genre)
    elif family == "senior":
        _render_senior(draw, img, cfg, title, subtitle, author, genre)
    elif family == "photo":
        _render_photo(draw, img, cfg, title, subtitle, author, genre, keywords)
    elif family == "illustration":
        _render_illustration(draw, img, cfg, title, subtitle, author, genre, keywords)
    else:
        _render_modern(draw, img, cfg, title, subtitle, author, genre)

    out = Path(book_dir) / "cover.jpg"
    img.save(str(out), "JPEG", quality=92)
    _thumbnail_ok(out)
    return out
