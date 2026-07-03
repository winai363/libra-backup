#!/usr/bin/env python3
"""
aplus_assets.py — Generate A+ Content assets for a book.

Produces /root/kdp/<slug>/aplus/:
  header_970x600.jpg   "Standard Image Header with Text" module image
  content.json         module texts (content name, headline, body, alt text)

Module copy comes from the book's own listing (subtitle + description bullets),
so it inherits the same policy-safe wording that passed metadata review.
No claims, no URLs — A+ moderation rejects both.

Usage:
  python3 aplus_assets.py <slug>            # one book
  python3 aplus_assets.py --all-live        # every LIVE book missing assets
"""
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

KDP = Path("/root/kdp")
W, H = 970, 600

# NOTE: libra/Montserrat-Black.ttf is mislabeled (contains Montserrat Thin) —
# ArchivoBlack is a true black weight.
FONT_BOLD = "/usr/share/fonts/truetype/libra/ArchivoBlack-Regular.ttf"
FONT_TEXT = "/usr/share/fonts/truetype/lato/Lato-Bold.ttf"
FONT_FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

HEADLINE = {
    "es": "En este libro", "fr": "Dans ce livre", "de": "In diesem Buch",
    "it": "In questo libro", "nl": "In dit boek", "pt": "Neste livro",
    "en": "Inside this book", "pl": "W tej książce", "ja": "本書の内容",
}

LANG_NAME = {
    "spanish": "es", "french": "fr", "german": "de", "italian": "it",
    "dutch": "nl", "portuguese": "pt", "english": "en", "polish": "pl",
    "japanese": "ja",
}

# default marketplace per language when the listing doesn't record one
LANG_MARKET = {
    "es": "amazon.es", "fr": "amazon.fr", "de": "amazon.de", "it": "amazon.it",
    "nl": "amazon.nl", "pt": "amazon.com", "en": "amazon.com",
    "pl": "amazon.pl", "ja": "amazon.co.jp",
}


def lang_code(listing: dict) -> str:
    raw = (listing.get("lang_code") or listing.get("language") or "en").strip().lower()
    if len(raw) == 2:
        return raw
    return LANG_NAME.get(raw, "en")


def _font(path, size):
    for p in (path, FONT_FALLBACK):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _dominant_color(img: Image.Image):
    small = img.convert("RGB").resize((32, 32))
    px = list(small.getdata())
    r = sum(p[0] for p in px) // len(px)
    g = sum(p[1] for p in px) // len(px)
    b = sum(p[2] for p in px) // len(px)
    return r, g, b


def _soften(rgb, mix_white=0.82):
    return tuple(int(c * (1 - mix_white) + 255 * mix_white) for c in rgb)


def _darken(rgb, k=0.45):
    return tuple(int(c * k) for c in rgb)


# Amazon A+ community guidelines reject health-claim verbs ("These keywords
# violate our community guidelines: prevenire."). Multilingual word families,
# matched on word boundaries.
_CLAIM_WORDS = re.compile(
    r"\b("
    r"prevent\w*|cure[sd]?|curing|treat(?:s|ed|ing|ment\w*)?|heal(?:s|ed|ing)?"
    r"|remed\w+|diagnos\w+"
    r"|prevenire|prevenzione|curare|guarire|trattare|trattament\w*|rimedi\w*"
    r"|prevenir|prevención|prevencion|curar|sanar|tratar|tratamient\w*"
    r"|remedio\w*|diagnóstico\w*"
    r"|prévenir|prévention|guérir|soigner|traiter|traitement\w*|remède\w*"
    r"|vorbeug\w*|verhinder\w*|heilen|behandel\w*|behandlung\w*|heilmittel"
    r"|voorkomen|genezen|prevenção|remédio\w*"
    r")\b", re.IGNORECASE)


def extract_bullets(listing: dict, n=3, max_len=90):
    """Prefer <li> items from description_html; fall back to sentences.
    html.unescape is mandatory — leaked entities like l&#x27;ansia fail
    Amazon's A+ content validation. Bullets containing health-claim verbs
    are dropped (A+ keyword filter rejects them)."""
    import html as _html
    html = listing.get("description_html") or ""
    items = [_html.unescape(re.sub(r"<[^>]+>", "", m)).strip()
             for m in re.findall(r"<li>(.*?)</li>", html, re.S)]
    items = [i for i in items if 15 <= len(i) <= 160]
    text = _html.unescape(re.sub(r"<[^>]+>", " ",
                                 listing.get("description") or ""))
    sents = [s.strip() for s in re.split(r"(?<=[.!?…])\s+", text)]
    items += [s for s in sents[1:] if 25 <= len(s) <= 160]
    items = [i for i in items if not _CLAIM_WORDS.search(i)]
    out = []
    for i in items[:n]:
        if len(i) > max_len:
            i = i[:max_len].rsplit(" ", 1)[0].rstrip(",;:") + "…"
        out.append(i)
    return out


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = f"{cur} {w}".strip()
        if draw.textlength(t, font=font) <= max_w:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def build(slug: str, force=False) -> Path | None:
    bdir = KDP / slug
    listing = json.loads((bdir / "listing.json").read_text())
    cover_path = bdir / "cover.jpg"
    if not cover_path.exists():
        print(f"SKIP {slug}: no cover.jpg")
        return None
    out_dir = bdir / "aplus"
    out_img = out_dir / "header_970x600.jpg"
    if out_img.exists() and not force:
        print(f"SKIP {slug}: assets exist (use force)")
        return out_img
    out_dir.mkdir(exist_ok=True)

    lang = lang_code(listing)
    headline = HEADLINE.get(lang, HEADLINE["en"])
    bullets = extract_bullets(listing)
    if not bullets:
        print(f"SKIP {slug}: no usable bullets")
        return None

    cover = Image.open(cover_path).convert("RGB")
    dom = _dominant_color(cover)
    bg_top, bg_bottom = _soften(dom, 0.86), _soften(dom, 0.72)
    ink = _darken(dom, 0.35)

    img = Image.new("RGB", (W, H))
    for y in range(H):  # vertical gradient
        t = y / H
        img.paste(tuple(int(a + (b - a) * t) for a, b in zip(bg_top, bg_bottom)),
                  (0, y, W, y + 1))
    draw = ImageDraw.Draw(img)

    # cover, left, with soft shadow
    ch = 520
    cw = int(cover.width * ch / cover.height)
    cover_r = cover.resize((cw, ch))
    cx, cy = 48, (H - ch) // 2
    shadow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rectangle([cx + 10, cy + 12, cx + cw + 10, cy + ch + 12],
                                     fill=(0, 0, 0, 90))
    img.paste(Image.alpha_composite(img.convert("RGBA"), shadow.filter(
        ImageFilter.GaussianBlur(8))).convert("RGB"), (0, 0))
    img.paste(cover_r, (cx, cy))

    # right column: headline + bullets
    tx = cx + cw + 56
    tw = W - tx - 48
    f_head = _font(FONT_BOLD, 46)
    f_body = _font(FONT_TEXT, 27)
    y = cy + 8
    for line in _wrap(draw, headline, f_head, tw):
        draw.text((tx, y), line, font=f_head, fill=ink)
        y += 58
    draw.rectangle([tx, y + 6, tx + 90, y + 12], fill=ink)
    y += 40
    for b in bullets:
        draw.ellipse([tx, y + 10, tx + 12, y + 22], fill=ink)
        for line in _wrap(draw, b, f_body, tw - 34):
            draw.text((tx + 26, y), line, font=f_body, fill=(40, 40, 40))
            y += 36
        y += 18

    img.save(out_img, "JPEG", quality=92)

    content = {
        "content_name": f"{slug}-aplus-v1",
        "language": lang,
        "marketplace": listing.get("marketplace") or LANG_MARKET.get(lang, "amazon.com"),
        "asin": listing.get("asin", ""),
        "headline": headline,
        "body": " • ".join(bullets),
        "bullets": bullets,
        "alt_text": listing.get("title", slug)[:100],
        "image": str(out_img),
    }
    (out_dir / "content.json").write_text(
        json.dumps(content, ensure_ascii=False, indent=2))
    print(f"OK {slug}: {out_img}")
    return out_img


def main():
    if "--all-live" in sys.argv:
        force = "--force" in sys.argv
        for lf in sorted(KDP.glob("*/listing.json")):
            d = json.loads(lf.read_text())
            if d.get("live_status") == "LIVE" and d.get("asin"):
                build(lf.parent.name, force=force)
    elif len(sys.argv) > 1:
        build(sys.argv[1], force="--force" in sys.argv)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
