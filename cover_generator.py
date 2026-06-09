"""
cover_generator.py — Smart KDP book cover generator

Pipeline:
  1. Detect genre from title / categories / keywords
  2. Try DALL-E 3 → generate a background image (no text)
  3. Overlay title + subtitle + author with Pillow
  4. If DALL-E fails → fall back to pure Pillow gradient + pattern

Cover size: 1600 x 2560 px (KDP paperback trim 6x9 in @ ~300dpi)
Safe zone: 80px from each edge
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


# ── gpt-image-1 prompt templates per genre ─────────────────────────────────
# Design principles from KDP bestseller research:
#   - Single strong focal point — no busy compositions
#   - Composition leaves upper 35% in dark tones for title text
#   - Photorealistic / editorial photography style (not abstract AI art)
#   - Genre-specific visual vocabulary that signals the niche instantly
#   - Works at thumbnail size (160×250 px) — high contrast, clear subject
# NEVER ask the model to render text — text is overlaid by Pillow
DALLE_PROMPTS: dict[str, str] = {
    "wellness": (
        "Serene lifestyle flat-lay shot from directly above on a smooth white marble surface: "
        "a single white ceramic bowl filled with dried lavender, three smooth grey river stones "
        "arranged mindfully, delicate eucalyptus sprigs, and a folded linen cloth in soft sage green. "
        "Soft diffused natural window light from the left, clean and airy commercial photography. "
        "The upper third of the composition fades into clean bright marble — ideal for dark title text. "
        "Minimalist, spa-like, calming. No text, no words, no letters anywhere."
    ),
    "tech": (
        "Dramatic close-up of an open premium laptop on a dark slate desk, the screen glowing "
        "with a cool blue data visualization — luminous network nodes and connecting lines. "
        "Deep black background, subtle reflections on the desk surface, cool blue and cyan accent lighting "
        "from the sides. Cinematic editorial technology photography. "
        "The top 40% of the frame is deep dark background — perfect for white title text. "
        "No text, no UI labels, no letters visible on screen or anywhere."
    ),
    "business": (
        "Low-angle cinematic view looking up at a modern glass skyscraper tower reflecting golden-hour "
        "clouds, dramatic deep navy sky occupying the upper half of the frame. "
        "Warm architectural lighting from below, polished steel and glass facade detail. "
        "Premium corporate photography, wide-angle perspective creating powerful upward thrust. "
        "The sky in the upper 40% is rich deep navy — clear space for white title text. "
        "No text, no logos, no people, no letters."
    ),
    "finance": (
        "Elegant close-up editorial photograph: a premium dark green leather desk pad with a polished "
        "gold fountain pen resting diagonally, a single crisp white envelope, and a vintage brass "
        "compass partially visible at the corner. Warm directional studio lighting casting clean shadows, "
        "dark vignette framing, prosperous and trustworthy mood. "
        "Upper third fades to deep rich dark tones for title overlay. "
        "No text, no numbers, no financial symbols, no letters."
    ),
    "creative": (
        "Top-down flat-lay of a creative workspace on a warm light wood surface: an open blank "
        "sketchbook with a single brushstroke of watercolor, scattered colored pencils arranged "
        "in a fan, a small ceramic palette with 4 vibrant paint dots, and a white ceramic mug. "
        "Warm natural morning light, editorial lifestyle photography. "
        "The upper portion transitions to clean light wood tones — space for bold title text. "
        "No text, no writing visible, no letters."
    ),
    "children": (
        "Bright overhead view of a colorful children's craft table: scattered crayons in rainbow "
        "order, star-shaped paper cutouts in primary colors, a small painted wooden toy, "
        "and a child's drawing of a sun (no words, just shapes). "
        "Vibrant saturated colors, warm cheerful afternoon light, joyful and playful mood. "
        "Upper area is filled with clear bright sky blue background. "
        "No text, no letters, no numbers, no writing."
    ),
    "selfhelp": (
        "Cinematic wide-angle shot of a winding mountain trail ascending toward a brilliant "
        "golden sunrise, warm light flooding the horizon and silhouetting the path ahead. "
        "Dramatic clouds parting to reveal bright amber and gold sky, deep valley visible below "
        "in soft blue morning haze. Inspirational landscape photography, shallow depth of field. "
        "The upper 40% is filled with rich golden-amber sky tones — ideal for title text. "
        "No text, no people, no animals, no letters."
    ),
    "food": (
        "Professional overhead food photography flat-lay on a clean white marble surface: "
        "sliced bright citrus fruits (lemon, lime, grapefruit), scattered fresh mint leaves, "
        "a few plump raspberries and blackberries, small edible flowers in yellow and purple, "
        "and condensation droplets on the marble. Bright natural daylight from above, "
        "clean commercial food photography, vibrant and refreshing mood. "
        "Upper third transitions to pure white marble — space for title text. "
        "No text, no bottles, no glasses, no labels, no letters."
    ),
    "seniors": (
        "Warm lifestyle photograph of a pair of hands — an older adult's hands — gently holding "
        "a modern smartphone, soft warm indoor window light from the side, shallow depth of field "
        "blurring a cozy home interior in the background. Authentic, reassuring, approachable mood. "
        "Warm golden tones, slightly soft focus, comfortable domestic setting. "
        "The upper 40% blurs into soft warm bokeh — space for title text. "
        "No text, no screen content visible, no letters."
    ),
    "default": (
        "Cinematic aerial photograph of early morning mist rolling through a forested mountain valley, "
        "warm golden sunrise light breaking through the peaks and illuminating the fog below. "
        "Rich atmospheric depth, premium landscape photography. "
        "The upper 35% of the frame is filled with warm golden-blue sky — clear space for title. "
        "No text, no people, no structures, no letters."
    ),
}

# ── Fonts (all pre-installed on server) ────────────────────────────────────
_FONT_BASE = "/usr/share/fonts/truetype/dejavu/"
FONT_SANS_BOLD      = _FONT_BASE + "DejaVuSans-Bold.ttf"
FONT_SANS           = _FONT_BASE + "DejaVuSans.ttf"
FONT_SERIF_BOLD     = _FONT_BASE + "DejaVuSerif-Bold.ttf"
FONT_SERIF          = _FONT_BASE + "DejaVuSerif.ttf"
FONT_COND_BOLD      = _FONT_BASE + "DejaVuSansCondensed-Bold.ttf"

# ── CJK fonts (Japanese / Chinese / Korean) ────────────────────────────────
# DejaVu has no CJK glyphs → renders as tofu boxes (□). Noto Sans CJK is a
# pan-CJK superset (kana + hanzi + hangul); face index 0 = "Noto Sans CJK JP".
_NOTO_BASE = "/usr/share/fonts/opentype/noto/"
FONT_CJK_BOLD       = _NOTO_BASE + "NotoSansCJK-Bold.ttc"
FONT_CJK            = _NOTO_BASE + "NotoSansCJK-Regular.ttc"
_CJK_FONT_INDEX     = 0   # Noto Sans CJK JP

# Thai needs its own font (DejaVu/Noto-CJK have no Thai glyphs) AND complex-text
# shaping so tone marks / floating vowels land in the right place (raqm layout).
_NOTO_TTF = "/usr/share/fonts/truetype/noto/"
FONT_THAI_BOLD      = _NOTO_TTF + "NotoSansThai-Bold.ttf"
FONT_THAI           = _NOTO_TTF + "NotoSansThai-Regular.ttf"

# Thai non-spacing combining marks (above/below vowels + tone marks). A line
# must never break *before* one of these — they attach to the preceding letter.
_THAI_COMBINING = set(range(0x0E34, 0x0E3B)) | {0x0E31} | set(range(0x0E47, 0x0E4F))


def _is_thai(text: str) -> bool:
    """True if `text` contains any Thai character (U+0E00–U+0E7F)."""
    return any(0x0E00 <= ord(ch) <= 0x0E7F for ch in text)


def _is_cjk(text: str) -> bool:
    """True if `text` contains any CJK ideograph / kana / hangul character."""
    for ch in text:
        o = ord(ch)
        if (0x3040 <= o <= 0x30FF      # Hiragana + Katakana
                or 0x4E00 <= o <= 0x9FFF   # CJK Unified Ideographs
                or 0x3400 <= o <= 0x4DBF   # CJK Extension A
                or 0xAC00 <= o <= 0xD7A3   # Hangul syllables
                or 0xFF00 <= o <= 0xFFEF): # Fullwidth forms
            return True
    return False


def _cjk_font(bold: bool):
    """Return (font_path, index) for the CJK face, weight per `bold`."""
    return (FONT_CJK_BOLD if bold else FONT_CJK), _CJK_FONT_INDEX


# ── Glyph-coverage check (anti-tofu) ───────────────────────────────────────
# A "valid" JPEG can still show garbled □ boxes if the chosen font lacks glyphs
# for the script (e.g. DejaVu has no CJK/Thai). This catches that before upload.
_CMAP_CACHE: dict = {}


def _font_glyphs(path: str, index: int = 0):
    """Return the set of codepoints a font can render, or None if unreadable."""
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
            _CMAP_CACHE[key] = None   # glyph data unavailable → skip the check
    return _CMAP_CACHE[key]


def _missing_chars(path: str, index: int, text: str) -> list[str]:
    """Characters in `text` the font at (path,index) cannot render (no tofu)."""
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


def unrenderable_chars(title: str, subtitle: str = "", author: str = "") -> list[str]:
    """Return cover-text characters that would render as tofu boxes with the
    fonts this generator would actually select. Empty list = safe to publish.
    Returns [] if fontTools/glyph data is unavailable (degrades gracefully)."""
    if _is_thai(title) or _is_thai(subtitle or ""):
        tp, ti = FONT_THAI_BOLD, 0
        sp, si = FONT_THAI, 0
    elif _is_cjk(title) or _is_cjk(subtitle or ""):
        tp, ti = _cjk_font(bold=True)
        sp, si = _cjk_font(bold=False)
    else:
        tp, ti = FONT_SANS_BOLD, 0
        sp, si = FONT_SANS, 0
    missing: list[str] = []
    for ch in (_missing_chars(tp, ti, title)
               + _missing_chars(sp, si, subtitle)
               + _missing_chars(FONT_SANS_BOLD, 0, author)):
        if ch not in missing:
            missing.append(ch)
    return missing

W, H    = 1600, 2560
SAFE    = 80               # minimum margin from edge
MAX_TW  = W - SAFE * 2 - 40   # max text width


# ── Genre themes ────────────────────────────────────────────────────────────
# Each theme defines gradient colours, accent colour, font choice, and
# the background pattern to overlay.
THEMES: dict[str, dict] = {
    # Calm / mental-health / anxiety / mindfulness
    "wellness": {
        "bg_top":      (110, 60, 160),   # medium purple
        "bg_bottom":   (55,  20, 100),   # deep purple
        "accent":      (255, 190, 230),  # soft rose
        "title_color": (255, 255, 255),
        "sub_color":   (230, 195, 255),
        "author_bg":   (140, 85, 195),
        "author_fg":   (255, 255, 255),
        "font_title":  FONT_SERIF_BOLD,
        "font_sub":    FONT_SERIF,
        "pattern":     "circles",
    },
    # Technology / AI / digital / software
    "tech": {
        "bg_top":      (13,  27,  75),   # dark navy
        "bg_bottom":   (5,   12,  40),   # deeper navy
        "accent":      (245, 200, 66),   # gold
        "title_color": (255, 255, 255),
        "sub_color":   (180, 215, 255),
        "author_bg":   (245, 200, 66),
        "author_fg":   (13,  27,  75),
        "font_title":  FONT_SANS_BOLD,
        "font_sub":    FONT_SANS,
        "pattern":     "grid",
    },
    # Business / leadership / management / productivity
    "business": {
        "bg_top":      (27,  42,  74),   # navy
        "bg_bottom":   (12,  20,  45),
        "accent":      (212, 175, 55),   # gold
        "title_color": (255, 255, 255),
        "sub_color":   (200, 205, 220),
        "author_bg":   (212, 175, 55),
        "author_fg":   (27,  42,  74),
        "font_title":  FONT_SANS_BOLD,
        "font_sub":    FONT_SANS,
        "pattern":     "diagonal",
    },
    # Personal finance / money / investing / budgeting
    "finance": {
        "bg_top":      (20,  75,  45),   # dark green
        "bg_bottom":   (8,   40,  20),
        "accent":      (212, 175, 55),   # gold
        "title_color": (255, 255, 255),
        "sub_color":   (185, 230, 195),
        "author_bg":   (212, 175, 55),
        "author_fg":   (20,  60,  30),
        "font_title":  FONT_SANS_BOLD,
        "font_sub":    FONT_SANS,
        "pattern":     "diagonal",
    },
    # Creative / art / writing / journaling / workbook
    "creative": {
        "bg_top":      (210, 80,  80),   # coral-red
        "bg_bottom":   (155, 40,  40),
        "accent":      (255, 225, 100),  # warm yellow
        "title_color": (255, 255, 255),
        "sub_color":   (255, 240, 200),
        "author_bg":   (255, 225, 100),
        "author_fg":   (80,  25,  25),
        "font_title":  FONT_SANS_BOLD,
        "font_sub":    FONT_SANS,
        "pattern":     "dots",
    },
    # Children / juvenile / kids / young adults
    "children": {
        "bg_top":      (60,  165, 235),  # sky blue
        "bg_bottom":   (30,  110, 185),
        "accent":      (255, 220, 50),   # bright yellow
        "title_color": (255, 255, 255),
        "sub_color":   (255, 245, 170),
        "author_bg":   (255, 200, 50),
        "author_fg":   (30,  50,  80),
        "font_title":  FONT_COND_BOLD,
        "font_sub":    FONT_SANS,
        "pattern":     "dots",
    },
    # Self-help / personal growth / habit / motivation
    "selfhelp": {
        "bg_top":      (50,  120, 180),  # steel blue
        "bg_bottom":   (20,  65,  120),
        "accent":      (255, 160, 60),   # orange
        "title_color": (255, 255, 255),
        "sub_color":   (210, 235, 255),
        "author_bg":   (255, 160, 60),
        "author_fg":   (20,  50,  100),
        "font_title":  FONT_SANS_BOLD,
        "font_sub":    FONT_SANS,
        "pattern":     "circles",
    },
    # Food / drink / recipe / cooking
    "food": {
        "bg_top":      (30,  130, 100),   # fresh green
        "bg_bottom":   (10,  70,  55),    # deep teal-green
        "accent":      (255, 220, 80),    # lemon yellow
        "title_color": (255, 255, 255),
        "sub_color":   (200, 255, 230),
        "author_bg":   (255, 220, 80),
        "author_fg":   (20,  70,  50),
        "font_title":  FONT_SERIF_BOLD,
        "font_sub":    FONT_SANS,
        "pattern":     "circles",
    },
    # Technology for seniors / education / step-by-step guides
    "seniors": {
        "bg_top":      (180, 120, 60),   # warm amber
        "bg_bottom":   (100, 55,  20),   # deep warm brown
        "accent":      (255, 220, 140),  # soft gold
        "title_color": (255, 255, 255),
        "sub_color":   (255, 235, 185),
        "author_bg":   (180, 120, 60),
        "author_fg":   (255, 255, 255),
        "font_title":  FONT_SANS_BOLD,
        "font_sub":    FONT_SANS,
        "pattern":     "circles",
    },
    # Fallback
    "default": {
        "bg_top":      (41,  65,  122),
        "bg_bottom":   (18,  30,  70),
        "accent":      (255, 200, 50),
        "title_color": (255, 255, 255),
        "sub_color":   (200, 220, 255),
        "author_bg":   (255, 200, 50),
        "author_fg":   (18,  30,  70),
        "font_title":  FONT_SANS_BOLD,
        "font_sub":    FONT_SANS,
        "pattern":     "diagonal",
    },
}


# ── Genre detection ─────────────────────────────────────────────────────────
_GENRE_KEYWORDS: dict[str, list[str]] = {
    "wellness": [
        "anxiety", "angst", "stress", "wellness", "mindfulness", "meditation",
        "mental health", "gelassenheit", "calm", "peace", "therapy", "healing",
        "emotional", "self-care", "depression", "burnout", "resilience",
        "trauma", "fear", "worry", "panic", "breath", "relaxation",
        "achtsamkeit", "entspannung", "wohlbefinden",
        # CJK
        "不安", "ストレス", "瞑想", "マインドフルネス", "メンタル", "心の健康",
        "癒し", "焦虑", "压力", "冥想",
    ],
    "tech": [
        "ai", "artificial intelligence", "digital", "technology", "computer",
        "software", "data", "programming", "machine learning", "algorithm",
        "automation", "coding", "developer", "database", "cloud", "cyber",
        "intelligenza artificiale", "tecnologia", "digitale",
        "künstliche intelligenz", "technologie",
        # CJK
        "デジタル", "プログラミング", "テクノロジー", "人工知能", "技術",
        "数字", "编程", "技术", "人工智能",
    ],
    "business": [
        "business", "entrepreneur", "startup", "management", "leadership",
        "strategy", "marketing", "productivity", "success", "corporate",
        "negotiation", "sales", "brand", "customer", "revenue", "profit",
        "azienda", "imprenditore", "strategie", "unternehmen",
        # CJK
        "ビジネス", "起業", "経営", "マーケティング", "営業", "副業",
        "リーダーシップ", "生産性", "商业", "创业", "管理",
    ],
    "finance": [
        "finance", "money", "invest", "wealth", "budget", "saving", "income",
        "financial", "economia", "finanza", "geld", "finanzen", "banque",
        "stock", "trading", "crypto", "asset", "retire", "debt", "tax",
        "เงิน", "การเงิน", "ลงทุน",
        # CJK
        "家計", "貯金", "貯蓄", "投資", "お金", "副業", "副収入", "収入",
        "節約", "予算", "資産", "借金", "年金", "税", "理财", "投资", "储蓄",
    ],
    "food": [
        "recipe", "rezept", "rezepte", "cook", "cooking", "food", "drink",
        "drinks", "cocktail", "mocktail", "beverage", "baking", "cuisine",
        "chef", "meal", "dish", "ingredient", "nutrition", "diet", "eating",
        "kitchen", "restaurant", "wine", "coffee", "tea", "smoothie", "juice",
        "getränk", "kochen", "küche", "backen", "ernährung",
        "ricetta", "cucina", "bevanda", "receta", "bebida", "cocina",
        "alkoholfrei", "sober",
        # CJK
        "レシピ", "料理", "食事", "ごはん", "お菓子", "飲み物", "食",
        "食谱", "烹饪", "美食",
    ],
    "creative": [
        "creative", "creativity", "workbook", "journal", "art", "design",
        "writing", "paint", "craft", "draw", "sketch", "poetry", "fiction",
        "story", "template", "quaderno", "creativo", "kreativ",
        # CJK
        "創作", "アート", "デザイン", "イラスト", "絵", "手帳", "創造",
        "创作", "艺术", "设计",
    ],
    "children": [
        "children", "kids", "juvenile", "child", "young adult", "teen",
        "toddler", "bambini", "kinder", "enfant", "niños",
    ],
    "selfhelp": [
        "self-help", "self help", "personal growth", "habit", "motivation",
        "confidence", "mindset", "goal", "overcome", "transform", "improve",
        "success", "happiness", "positive", "wachstum", "selbsthilfe",
        "sviluppo personale", "crescita personale",
        # CJK
        "自己啓発", "習慣", "モチベーション", "目標", "成長", "自信", "幸せ",
        "自我提升", "习惯", "目标",
    ],
    "seniors": [
        "senior", "senioren", "seniorin", "älter", "rentner", "ruhestand",
        "ältere menschen", "retiree", "elderly", "60 plus", "50 plus",
        "smartphone für senioren", "internet für senioren", "computer für senioren",
        "nonno", "nonna", "anziani",
        # CJK
        "シニア", "高齢者", "年配", "老後", "老年", "长辈",
    ],
}


def detect_genre(title: str, categories: list[str], keywords: list[str]) -> str:
    """Return the best-matching genre key for the given book metadata."""
    text = " ".join([title] + categories + keywords).lower()
    scores: dict[str, int] = {}
    for genre, words in _GENRE_KEYWORDS.items():
        scores[genre] = sum(1 for w in words if w in text)
    best = max(scores, key=lambda g: scores[g])
    return best if scores[best] > 0 else "default"


# ── Drawing helpers ─────────────────────────────────────────────────────────

def _gradient_bg(img, top_rgb: tuple, bot_rgb: tuple):
    """Fill `img` (RGB) with a vertical gradient from top_rgb to bot_rgb."""
    from PIL import ImageDraw
    draw = ImageDraw.Draw(img)
    tr, tg, tb = top_rgb
    br, bg_, bb = bot_rgb
    for y in range(H):
        t = y / H
        r = int(tr + (br - tr) * t)
        g = int(tg + (bg_ - tg) * t)
        b = int(tb + (bb - tb) * t)
        draw.line([(0, y), (W, y)], fill=(r, g, b))


def _draw_pattern(base_img, pattern: str, accent_rgb: tuple):
    """Overlay a semi-transparent decorative pattern onto base_img (RGB).
    Returns a new RGB image."""
    from PIL import Image, ImageDraw
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    ar, ag, ab = accent_rgb

    if pattern == "circles":
        # Large hollow circles, partially off-screen at corners
        specs = [
            (W + 220,   -220,  650, 28),
            (-220,      H + 180, 550, 22),
            (W // 2,    H // 2, 420, 12),
        ]
        for cx, cy, r, alpha in specs:
            d.ellipse([(cx - r, cy - r), (cx + r, cy + r)],
                      outline=(ar, ag, ab, alpha), width=6)
            d.ellipse([(cx - r + 30, cy - r + 30), (cx + r - 30, cy + r - 30)],
                      outline=(ar, ag, ab, alpha // 2), width=3)

    elif pattern == "grid":
        # Dot grid (circuit board feel)
        spacing = 90
        for gx in range(0, W + spacing, spacing):
            for gy in range(0, H + spacing, spacing):
                d.ellipse([(gx - 3, gy - 3), (gx + 3, gy + 3)],
                          fill=(ar, ag, ab, 45))
        # Sparse vertical lines
        for gx in range(0, W, spacing * 4):
            d.line([(gx, 0), (gx, H)], fill=(ar, ag, ab, 18), width=1)

    elif pattern == "diagonal":
        # Thin diagonal stripes
        spacing = 130
        for i in range(-H, W + H, spacing):
            d.line([(i, 0), (i + H, H)], fill=(ar, ag, ab, 22), width=2)

    elif pattern == "dots":
        # Scattered dots of varying sizes
        import random
        rng = random.Random(sum(accent_rgb))
        for _ in range(90):
            x = rng.randint(-40, W + 40)
            y = rng.randint(-40, H + 40)
            r = rng.randint(8, 55)
            alpha = rng.randint(18, 55)
            d.ellipse([(x - r, y - r), (x + r, y + r)],
                      fill=(ar, ag, ab, alpha))

    from PIL import Image
    result = Image.alpha_composite(base_img.convert("RGBA"), overlay)
    return result.convert("RGB")


_THAI_BREAKER = None


def _thai_words(text: str) -> list[str]:
    """Segment Thai text into word tokens using ICU's dictionary-based
    BreakIterator (the same engine xelatex uses for the book interior, so the
    cover wraps words consistently). Spaces are preserved as their own tokens.
    Falls back to a single token if PyICU is unavailable."""
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


def _wrap_text(draw, text: str, font, max_width: int) -> list[str]:
    """Word-wrap `text` so each line fits within max_width pixels.

    CJK languages (Japanese/Chinese/Korean) have no spaces between words, so
    splitting on whitespace yields a single un-wrappable run. For CJK text we
    wrap character-by-character instead (the normal line-breaking convention
    for those scripts)."""
    if _is_cjk(text):
        # Break on Japanese punctuation when possible, else any character.
        no_break_before = "。、）」』】〕》〉”’!?！？.,)"
        lines: list[str] = []
        line = ""
        for ch in text:
            candidate = line + ch
            bb = draw.textbbox((0, 0), candidate, font=font)
            if bb[2] - bb[0] > max_width and line and ch not in no_break_before:
                lines.append(line)
                line = ch
            else:
                line = candidate
        if line:
            lines.append(line)
        return lines

    if _is_thai(text):
        # Thai has no inter-word spaces. Break on real word boundaries (ICU
        # dictionary segmentation — same engine the book interior uses) so a
        # line never ends mid-word or orphans a leading vowel (เ แ โ ใ ไ).
        lines: list[str] = []
        line = ""
        for tok in _thai_words(text):
            candidate = line + tok
            bb = draw.textbbox((0, 0), candidate, font=font)
            if bb[2] - bb[0] > max_width and line:
                lines.append(line.rstrip())
                line = "" if tok == " " else tok
            elif tok == " " and not line:
                continue   # don't start a line with a space
            else:
                line = candidate
        if line.strip():
            lines.append(line.rstrip())
        # Safety net: if any single word was wider than max_width, char-split it
        # so it can never overflow the cover edge.
        fixed: list[str] = []
        for ln in lines:
            if draw.textbbox((0, 0), ln, font=font)[2] <= max_width:
                fixed.append(ln)
                continue
            cur = ""
            for ch in ln:
                if (draw.textbbox((0, 0), cur + ch, font=font)[2] > max_width
                        and cur and ord(ch) not in _THAI_COMBINING):
                    fixed.append(cur)
                    cur = ch
                else:
                    cur += ch
            if cur:
                fixed.append(cur)
        return fixed

    words = text.split()
    lines: list[str] = []
    line = ""
    for word in words:
        candidate = f"{line} {word}".strip()
        bb = draw.textbbox((0, 0), candidate, font=font)
        if bb[2] - bb[0] > max_width and line:
            lines.append(line)
            line = word
        else:
            line = candidate
    if line:
        lines.append(line)
    return lines


def _draw_centered(draw, text: str, y: int, font, color):
    """Draw text centered horizontally at y."""
    bb = draw.textbbox((0, 0), text, font=font)
    x = (W - (bb[2] - bb[0])) // 2
    draw.text((x, y), text, fill=color, font=font)
    return bb[3] - bb[1]   # return line height


# ── Top decorative shape ────────────────────────────────────────────────────

def _draw_top_shape(draw, theme: dict):
    """Draw a genre-appropriate decorative shape in the upper portion."""
    accent  = theme["accent"]
    pattern = theme["pattern"]
    light   = tuple(min(255, c + 60) for c in accent)

    if pattern == "circles":
        # Concentric arcs top-center
        for r, alpha_factor in [(220, 1.0), (310, 0.55), (400, 0.3)]:
            draw.arc([(W // 2 - r, 60 - r), (W // 2 + r, 60 + r)],
                     start=0, end=180, fill=accent, width=5)

    elif pattern == "grid":
        # Hexagon outline (tech feel)
        cx, cy, size = W // 2, 260, 160
        pts = [
            (cx + size * __import__('math').cos(__import__('math').radians(60 * i)),
             cy + size * __import__('math').sin(__import__('math').radians(60 * i)))
            for i in range(6)
        ]
        draw.polygon(pts, outline=accent, width=5)
        inner = int(size * 0.6)
        pts2 = [
            (cx + inner * __import__('math').cos(__import__('math').radians(60 * i)),
             cy + inner * __import__('math').sin(__import__('math').radians(60 * i)))
            for i in range(6)
        ]
        draw.polygon(pts2, outline=light, width=3)

    elif pattern in ("diagonal", "dots"):
        # Simple wide bar + thin bar
        draw.rectangle([(SAFE, 120), (W - SAFE, 145)], fill=accent)
        draw.rectangle([(SAFE + 80, 160), (W - SAFE - 80, 170)], fill=light)


def _draw_bottom_shape(draw, theme: dict):
    """Decorative line cluster above the author box."""
    accent = theme["accent"]
    light  = tuple(min(255, c + 60) for c in accent)
    y = H - 340
    draw.rectangle([(SAFE + 60, y), (W - SAFE - 60, y + 5)], fill=accent)
    draw.rectangle([(SAFE + 120, y + 18), (W - SAFE - 120, y + 21)], fill=light)


# ── DALL-E 3 background generator ──────────────────────────────────────────

# Design rules shared by every cover. Quality stays high and consistent while
# the *scene* itself is invented fresh per book (see _art_direction_prompt), so
# no two books — even in the same genre — ever get the same image.
_COVER_ART_RULES = (
    "Design rules (follow ALL): one single strong focal point, no busy or cluttered "
    "composition; photorealistic editorial/commercial photography style (not abstract "
    "AI art, not a collage); high contrast so it stays clear as a tiny 160x250px "
    "thumbnail; the upper 35-40% of the frame must be calm dark tones or clean negative "
    "space reserved for overlaid title text; cinematic premium lighting; portrait "
    "orientation. Absolutely NO text, words, letters, numbers, logos or UI in the image."
)


def _art_direction_prompt(genre: str, title: str, subtitle: str,
                          keywords: list[str]) -> "str | None":
    """Use GPT to invent a UNIQUE, content-specific cover scene for THIS exact
    book, so two books in the same genre never get the same image. Returns a
    scene-description string (rules appended), or None to fall back to template."""
    try:
        import openai
        api_key = _load_openai_key()
        if not api_key:
            return None
        client = openai.OpenAI(api_key=api_key)
        kw = ", ".join(keywords[:7]) if keywords else ""
        user = (
            f"Book title: {title}\n"
            f"Subtitle: {subtitle or '(none)'}\n"
            f"Keywords: {kw or '(none)'}\n"
            f"Genre: {genre}\n\n"
            "Describe ONE specific, concrete photographic cover scene that visually "
            "captures THIS book's exact topic and would make a browsing shopper stop and "
            "click. Be vivid and specific about the subject, props, setting, colour "
            "palette and lighting. 2-4 sentences. Make it distinctive and memorable — "
            "never a generic interchangeable stock photo."
        )
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content":
                 "You are an award-winning book-cover art director. " + _COVER_ART_RULES},
                {"role": "user", "content": user},
            ],
            temperature=0.9,      # high → fresh, non-repeating scenes
            max_tokens=320,
        )
        scene = (resp.choices[0].message.content or "").strip()
        if len(scene) < 40:
            return None
        return f"{scene}\n\n{_COVER_ART_RULES}"
    except Exception as e:
        print(f"  art-direction prompt failed ({e}), using genre template")
        return None


def _dalle_background(genre: str, title: str, subtitle: str = "",
                      keywords: list[str] | None = None) -> "Image | None":
    """
    Request a background image from gpt-image-1 (portrait 1024x1536).
    Returns a PIL Image resized to 1600x2560 RGB, or None on failure.
    Never requests text in the image — text is overlaid by Pillow later.
    The scene is generated per-book so covers never repeat.
    """
    try:
        import openai, base64, io
        from PIL import Image

        api_key = _load_openai_key()
        if not api_key:
            return None

        client = openai.OpenAI(api_key=api_key)

        # Bespoke, content-matched scene per book → no duplicate covers.
        # Falls back to the fixed genre template only if GPT is unavailable.
        base_prompt = _art_direction_prompt(genre, title, subtitle, keywords or [])
        if not base_prompt:
            base_prompt = (
                f"{DALLE_PROMPTS.get(genre, DALLE_PROMPTS['default'])} "
                f"The book is titled '{title}'. {_COVER_ART_RULES}"
            )
        prompt = (
            f"{base_prompt} "
            "Photorealistic, high quality, suitable for a published book cover. "
            "Absolutely no text, no words, no letters anywhere in the image."
        )

        response = client.images.generate(
            model   = "gpt-image-1",
            prompt  = prompt,
            size    = "1024x1536",   # portrait — gpt-image-1 max portrait size
            n       = 1,
        )

        b64_data = response.data[0].b64_json
        raw = base64.b64decode(b64_data)
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        img = img.resize((W, H), Image.LANCZOS)
        return img

    except Exception as e:
        print(f"  gpt-image-1 failed ({e}), using Pillow fallback")
        return None


def _darken_for_text(img: "Image") -> "Image":
    """
    Apply a gradient dark overlay optimised for the title-at-top layout:
      - Top 40%: heavy dark (title text zone)
      - Middle 30%: lighter (show the image — the visual hook)
      - Bottom 30%: medium dark (subtitle + author zone)
    """
    from PIL import Image, ImageDraw
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)

    top_end    = int(H * 0.40)   # 1024 px
    mid_end    = int(H * 0.70)   # 1792 px

    # Top band — heavy dark for title legibility
    for y in range(top_end):
        t = y / top_end            # 0 → 1 (top to bottom of band)
        alpha = int(165 - 55 * t)  # 165 → 110
        d.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))

    # Middle band — light vignette, let the image breathe
    for y in range(top_end, mid_end):
        t = (y - top_end) / (mid_end - top_end)
        # dips to 50 at center then rises to 90 at mid_end
        alpha = int(110 - 60 * (1 - abs(t - 0.5) * 2))
        d.line([(0, y), (W, y)], fill=(0, 0, 0, max(50, alpha)))

    # Bottom band — medium dark for subtitle + author
    for y in range(mid_end, H):
        t = (y - mid_end) / (H - mid_end)
        alpha = int(90 + 80 * t)   # 90 → 170 toward very bottom
        d.line([(0, y), (W, y)], fill=(0, 0, 0, min(180, alpha)))

    return Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")


# ── Main public function ────────────────────────────────────────────────────

def generate_cover(
    book_dir,
    title: str,
    subtitle: str,
    author: str,
    categories: list[str] | None = None,
    keywords: list[str] | None = None,
) -> Path:
    """
    Generate a professional KDP book cover and save it as cover.jpg
    inside `book_dir`.  Returns the Path to the saved file.

    Pipeline:
      1. Detect genre from title + categories + keywords
      2. Try DALL-E 3 for background image
      3. If DALL-E fails → Pillow gradient + pattern fallback
      4. Overlay title / subtitle / author with Pillow
    """
    from PIL import Image, ImageDraw, ImageFont

    categories = categories or []
    keywords   = keywords   or []

    genre = detect_genre(title, categories, keywords)
    theme = THEMES[genre]

    # ── 1. Background: DALL-E 3 → Pillow fallback ──────────────────────────
    img = _dalle_background(genre, title, subtitle, keywords)
    dalle_used = img is not None

    if dalle_used:
        img = _darken_for_text(img)
        # Overlay the background pattern lightly on top of the photo
        img = _draw_pattern(img, theme["pattern"], theme["accent"])
    else:
        # Fallback: jitter the gradient colours with a title-seeded RNG so two
        # books in the same genre never share an identical fallback cover.
        import random, hashlib
        rng = random.Random(int(hashlib.md5(title.encode("utf-8")).hexdigest(), 16))
        def _jit(rgb):
            return tuple(max(0, min(255, c + rng.randint(-22, 22))) for c in rgb)
        img = Image.new("RGB", (W, H))
        _gradient_bg(img, _jit(theme["bg_top"]), _jit(theme["bg_bottom"]))
        img = _draw_pattern(img, theme["pattern"], theme["accent"])

    draw = ImageDraw.Draw(img)

    # ── 2. Fonts ────────────────────────────────────────────────────────────
    # Non-Latin scripts need their own font; DejaVu renders them as tofu boxes.
    # Thai also needs raqm layout so tone marks / floating vowels position right.
    cjk  = _is_cjk(title) or _is_cjk(subtitle or "")
    thai = _is_thai(title) or _is_thai(subtitle or "")
    if thai:
        title_path, title_idx = FONT_THAI_BOLD, 0
        sub_path,   sub_idx   = FONT_THAI, 0
    elif cjk:
        title_path, title_idx = _cjk_font(bold=True)
        sub_path,   sub_idx   = _cjk_font(bold=False)
    else:
        title_path, title_idx = theme["font_title"], 0
        sub_path,   sub_idx   = theme["font_sub"], 0

    _LAYOUT = ImageFont.Layout.RAQM if thai else ImageFont.Layout.BASIC

    def _mkfont(path, size, index=0):
        return ImageFont.truetype(path, size, index=index, layout_engine=_LAYOUT)

    # Anti-tofu guard: warn loudly if the chosen fonts can't render the text.
    _tofu = (_missing_chars(title_path, title_idx, title)
             + _missing_chars(sub_path, sub_idx, subtitle))
    if _tofu:
        print(f"  ⚠️ COVER WARNING: {len(_tofu)} char(s) have no glyph in the "
              f"selected font and will render as boxes: {''.join(_tofu[:20])}")

    # Title: start large, shrink until it fits in ≤3 lines within the top zone
    # Target: title occupies roughly top 35% of cover (≈ 896 px)
    title_size  = 128
    font_title  = _mkfont(title_path, title_size, title_idx)
    lines_title = _wrap_text(draw, title, font_title, MAX_TW)

    while len(lines_title) > 3 and title_size > 72:
        title_size -= 6
        font_title  = _mkfont(title_path, title_size, title_idx)
        lines_title = _wrap_text(draw, title, font_title, MAX_TW)

    # Allow up to 4 lines for long titles before capping at 66
    while len(lines_title) > 4 and title_size > 66:
        title_size -= 6
        font_title  = _mkfont(title_path, title_size, title_idx)
        lines_title = _wrap_text(draw, title, font_title, MAX_TW)

    sub_size   = max(44, title_size - 36)
    font_sub   = _mkfont(sub_path, sub_size, sub_idx)
    font_auth  = _mkfont(FONT_SANS_BOLD, 54)

    line_h_title = title_size + 20
    line_h_sub   = sub_size + 16

    # ── 3. Title block — positioned in upper zone ───────────────────────────
    # Leave 110 px at top, center title within top 40% of the image
    top_zone_h  = int(H * 0.40)  # 1024 px
    title_block = len(lines_title) * line_h_title
    title_y     = max(110, (top_zone_h - title_block) // 2)

    y = title_y
    for line in lines_title:
        # Multi-layer drop shadow for photo backgrounds (5 px offset)
        bb = draw.textbbox((0, 0), line, font=font_title)
        sx = (W - (bb[2] - bb[0])) // 2
        for dx, dy, alpha in [(4, 4, 120), (2, 2, 80)]:
            draw.text((sx + dx, y + dy), line, fill=(0, 0, 0, alpha), font=font_title)
        _draw_centered(draw, line, y, font_title, theme["title_color"])
        y += line_h_title

    # ── 4. Accent rule below title ──────────────────────────────────────────
    rule_y = y + 16
    bar_w  = min(460, W // 3)
    draw.rectangle(
        [(W // 2 - bar_w // 2, rule_y), (W // 2 + bar_w // 2, rule_y + 7)],
        fill=theme["accent"],
    )

    # ── 5. Subtitle — below image zone, fitted into a guarded band so it can
    # never overlap the author line below or get silently truncated. ─────────
    # Band: from 72% of the height down to a safe margin above the author
    # accent line (author sits at H-210, its rule at H-232).
    sub_band_top    = int(H * 0.72)        # 1843 px
    sub_band_bottom = (H - 210) - 22 - 34  # ≈ 2294 px — keep clear of author rule
    band_h          = sub_band_bottom - sub_band_top

    lines_sub: list[str] = []
    if subtitle:
        # Shrink the subtitle font until the WHOLE subtitle fits in ≤3 lines
        # AND the block height fits the band — so nothing is dropped or collides.
        while sub_size > 34:
            font_sub   = _mkfont(sub_path, sub_size, sub_idx)
            line_h_sub = sub_size + 16
            lines_sub  = _wrap_text(draw, subtitle, font_sub, MAX_TW - 80)
            if len(lines_sub) <= 3 and len(lines_sub) * line_h_sub <= band_h:
                break
            sub_size -= 4
        lines_sub = lines_sub[:3]   # last-resort cap (only if min size reached)

    # Vertically center the subtitle block within its band.
    block_h = len(lines_sub) * line_h_sub
    y = sub_band_top + max(0, (band_h - block_h) // 2)
    for line in lines_sub:
        bb = draw.textbbox((0, 0), line, font=font_sub)
        sx = (W - (bb[2] - bb[0])) // 2
        draw.text((sx + 2, y + 2), line, fill=(0, 0, 0, 100), font=font_sub)
        _draw_centered(draw, line, y, font_sub, theme["sub_color"])
        y += line_h_sub

    # ── 6. Author — bottom strip, clean text with subtle accent line above ──
    author_text = author or "Unknown"
    auth_y      = H - 210

    # Thin accent line separating author from subtitle
    line_x1 = W // 2 - 120
    line_x2 = W // 2 + 120
    draw.rectangle([(line_x1, auth_y - 22), (line_x2, auth_y - 16)],
                   fill=theme["accent"])

    # Author name — clean white text, no box
    bb  = draw.textbbox((0, 0), author_text, font=font_auth)
    asx = (W - (bb[2] - bb[0])) // 2
    draw.text((asx + 2, auth_y + 2), author_text, fill=(0, 0, 0, 120), font=font_auth)
    draw.text((asx, auth_y), author_text, fill=(255, 255, 255), font=font_auth)

    # ── 7. Save ─────────────────────────────────────────────────────────────
    out = Path(book_dir) / "cover.jpg"
    img.save(str(out), "JPEG", quality=95)
    return out
