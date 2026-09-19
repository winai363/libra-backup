#!/usr/bin/env python3
"""make_pin_image.py — one Pin image per hub article (1000x1500 JPEG).

Pinterest distributes "fresh" Pins: an image it has not seen before. The first
six articles all used the book cover, so each book had one image repeated three
times. This draws the article title over the book's own cover instead, so every
article carries an image of its own. No third-party image, no model call.

    python3 scripts/make_pin_image.py data/growth_articles_drafts/<id>.json [...]

Writes data/growth_pins/<id>.jpg. The article's image_url must already be
/libra/growth/pins/<id>.jpg; anything else is refused, so a file cannot point
the feed at an image this script did not make.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

LIBRA_DIR = Path(__file__).resolve().parent.parent
KDP_DIR = LIBRA_DIR.parent / "kdp"
PINS_DIR = LIBRA_DIR / "data" / "growth_pins"
PIN_PREFIX = "/libra/growth/pins/"
WIDTH, HEIGHT = 1000, 1500
MARGIN = 80

TITLE_FONT = "/usr/share/fonts/truetype/libra/Montserrat-Black.ttf"
LABEL_FONT = "/usr/share/fonts/truetype/lato/Lato-Heavy.ttf"
FOOTER_FONT = "/usr/share/fonts/truetype/lato/Lato-Bold.ttf"

# Per book: the label over the title, the honest format line under the cover,
# and colour schemes (background, text, accent) matched to the cover.
BOOKS = {
    "adhd-adults-workbook-es": {
        "label": "TDAH EN ADULTOS",
        "footer": "Cuaderno de ejercicios · Kindle",
        "schemes": [("#0e4450", "#ffffff", "#e8a33d"),
                    ("#f3efe6", "#0e4450", "#d98e2b"),
                    ("#e8a33d", "#102f38", "#0e4450")],
    },
    "bilingual-english-spanish-kids-vocab": {
        "label": "BILINGUAL AT HOME · EN / ES",
        "footer": "Bilingual vocabulary book for kids · Kindle",
        "schemes": [("#fbf8f1", "#1d2340", "#f0645a"),
                    ("#f6c945", "#1d2340", "#e0402f"),
                    ("#1f6fb2", "#ffffff", "#f6c945")],
    },
}


def _wrap(draw, text, font, width):
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def _fit_title(draw, text, width, max_height):
    for size in range(92, 47, -4):
        font = ImageFont.truetype(TITLE_FONT, size)
        font.set_variation_by_axes([800])  # variable font; its default is Thin
        lines = _wrap(draw, text, font, width)
        line_height = int(size * 1.18)
        if len(lines) * line_height <= max_height and all(
                draw.textlength(line, font=font) <= width for line in lines):
            return font, lines, line_height
    raise ValueError(f"title does not fit the pin: {text!r}")


def render(article: dict) -> Image.Image:
    slug = article["target_slug"]
    book = BOOKS[slug]
    variant = int(hashlib.sha1(article["id"].encode()).hexdigest(), 16) % len(book["schemes"])
    background, ink, accent = book["schemes"][variant]

    pin = Image.new("RGB", (WIDTH, HEIGHT), background)
    draw = ImageDraw.Draw(pin)
    text_width = WIDTH - 2 * MARGIN

    label_font = ImageFont.truetype(LABEL_FONT, 30)
    draw.text((MARGIN, MARGIN), book["label"], font=label_font, fill=accent)

    title = article.get("pin_title") or article["title"]
    title_top = MARGIN + 70
    font, lines, line_height = _fit_title(draw, title, text_width, 560)
    for index, line in enumerate(lines):
        draw.text((MARGIN, title_top + index * line_height), line, font=font, fill=ink)
    bar_top = title_top + len(lines) * line_height + 30
    draw.rectangle((MARGIN, bar_top, MARGIN + 140, bar_top + 10), fill=accent)

    cover = Image.open(KDP_DIR / slug / "cover.jpg").convert("RGB")
    footer_font = ImageFont.truetype(FOOTER_FONT, 30)
    cover_bottom = HEIGHT - MARGIN - 60
    cover_height = min(620, cover_bottom - (bar_top + 60))
    cover_width = round(cover.width * cover_height / cover.height)
    cover = cover.resize((cover_width, cover_height), Image.LANCZOS)
    left = (WIDTH - cover_width) // 2
    top = cover_bottom - cover_height

    shadow = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle((left + 14, top + 18, left + cover_width + 14,
                                      top + cover_height + 18), fill=(0, 0, 0, 90))
    shadow = shadow.filter(ImageFilter.GaussianBlur(14))
    pin.paste(shadow, (0, 0), shadow)
    pin.paste(cover, (left, top))
    # A thin frame keeps a dark cover visible on a background of its own colour.
    draw.rectangle((left - 4, top - 4, left + cover_width + 3, top + cover_height + 3),
                   outline=accent, width=4)

    footer = book["footer"]
    footer_width = draw.textlength(footer, font=footer_font)
    draw.text(((WIDTH - footer_width) / 2, HEIGHT - MARGIN - 30), footer,
              font=footer_font, fill=ink)
    return pin


def make(article_file: Path) -> Path:
    article = json.loads(Path(article_file).read_text(encoding="utf-8"))
    expected = f"{PIN_PREFIX}{article['id']}.jpg"
    if article.get("image_url") != expected:
        raise ValueError(f"{article['id']}: image_url must be {expected}, found {article.get('image_url')!r}")
    PINS_DIR.mkdir(parents=True, exist_ok=True)
    target = PINS_DIR / f"{article['id']}.jpg"
    render(article).save(target, "JPEG", quality=88, optimize=True)
    return target


def main(argv=None) -> int:
    files = argv if argv is not None else sys.argv[1:]
    if not files:
        print(__doc__)
        return 2
    for file in files:
        print(make(Path(file)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
