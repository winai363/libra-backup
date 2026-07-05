#!/usr/bin/env python3
"""paperback_cover.py — build a KDP full-wrap paperback cover PDF from the
existing ebook cover + listing.json.

Layout (300 dpi, RGB, flattened single-page PDF):
  [bleed 0.125"][back 6"][spine pages*0.002252"][front 6"][bleed 0.125"] x 9.25" tall
Front  = existing cover.jpg scaled to fill panel (small symmetric vertical crop).
Spine  = solid colour sampled from the cover background (no spine text — all
         current books are under KDP's 79-page minimum for spine text).
Back   = cover background colour, title + blurb from listing.json, author at
         bottom-left; KDP barcode zone (2" x 1.2", 0.25" inset bottom-right)
         is kept clear.

Usage: python3 scripts/paperback_cover.py <slug> [--force]
Writes /root/kdp/<slug>/<Safe-Title>-paperback-cover.pdf and a -preview.png
"""
import json
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageStat

KDP_DIR = Path("/root/kdp")
DPI = 300
TRIM_W, TRIM_H = 6.0, 9.0
BLEED = 0.125
SPINE_PER_PAGE = 0.002252  # B&W, white paper

FONT_TITLE = "/usr/share/fonts/truetype/libra/Montserrat-Black.ttf"
FONT_BODY = "/usr/share/fonts/truetype/lato/Lato-Regular.ttf"
FONT_BODY_BOLD = "/usr/share/fonts/truetype/lato/Lato-Bold.ttf"


def px(inches: float) -> int:
    return round(inches * DPI)


def _page_count(book_dir: Path) -> int:
    import subprocess
    pdfs = sorted(book_dir.glob("*paperback.pdf"))
    if not pdfs:
        raise SystemExit(f"no interior *-paperback.pdf in {book_dir}")
    out = subprocess.run(["pdfinfo", str(pdfs[0])], capture_output=True, text=True).stdout
    m = re.search(r"Pages:\s+(\d+)", out)
    return int(m.group(1))


def _bg_and_accent(cover: Image.Image):
    """Background = average of left edge; accent = most saturated palette colour."""
    edge = cover.crop((0, cover.height // 4, 24, cover.height * 3 // 4))
    bg = tuple(int(c) for c in ImageStat.Stat(edge).mean[:3])
    pal = cover.convert("P", palette=Image.ADAPTIVE, colors=16).convert("RGB").getcolors(16 * 16)
    def score(c):
        r, g, b = c
        mx, mn = max(r, g, b), min(r, g, b)
        sat = 0 if mx == 0 else (mx - mn) / mx
        dist = sum((a - b_) ** 2 for a, b_ in zip(c, bg)) ** 0.5
        return sat * (0.4 + mx / 255) * min(1.0, dist / 150)
    accent = max((c for _, c in pal), key=score)
    if score(accent) < 0.25:
        accent = (233, 168, 90)  # series orange fallback
    return bg, accent


def _wrap(draw, text, font, maxw):
    lines = []
    for para in text.split("\n"):
        para = para.strip()
        if not para:
            lines.append("")
            continue
        cur = ""
        for w in para.split():
            t = f"{cur} {w}".strip()
            if draw.textlength(t, font=font) <= maxw:
                cur = t
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
    return lines


def build(slug: str, force: bool = False) -> Path:
    book_dir = KDP_DIR / slug
    listing = json.loads((book_dir / "listing.json").read_text())
    pages = _page_count(book_dir)
    interior = sorted(book_dir.glob("*paperback.pdf"))[0]
    out_pdf = book_dir / (interior.stem + "-cover.pdf")
    if out_pdf.exists() and not force:
        print(f"exists: {out_pdf}")
        return out_pdf

    spine_in = pages * SPINE_PER_PAGE
    W = px(BLEED + TRIM_W + spine_in + TRIM_W + BLEED)
    H = px(TRIM_H + 2 * BLEED)
    spine_x0 = px(BLEED + TRIM_W)
    spine_x1 = spine_x0 + px(spine_in)

    cover = Image.open(book_dir / "cover.jpg").convert("RGB")
    bg, accent = _bg_and_accent(cover)

    img = Image.new("RGB", (W, H), bg)
    draw = ImageDraw.Draw(img)

    # ── front panel (right): scale cover to panel HEIGHT (no crop — keeps
    # badges/arcs intact), pad the width deficit by extending the cover's own
    # edge columns (background gradient is vertical, so this is seamless)
    fw = W - spine_x1
    scale = H / cover.height
    scaled = cover.resize((round(cover.width * scale), H), Image.LANCZOS)
    pad = fw - scaled.width
    lpad = max(0, pad - px(BLEED))  # bias: keep art flush toward trim, bleed on right
    left_col = scaled.crop((0, 0, 1, H)).resize((max(1, lpad), H))
    right_col = scaled.crop((scaled.width - 1, 0, scaled.width, H)).resize((max(1, pad - lpad), H))
    img.paste(left_col, (spine_x1, 0))
    img.paste(scaled, (spine_x1 + lpad, 0))
    img.paste(right_col, (spine_x1 + lpad + scaled.width, 0))

    # ── back panel: text block inside 0.5" margins, clear of barcode zone
    mx = px(BLEED + 0.5)
    maxw = px(BLEED + TRIM_W - 0.5) - mx
    title = listing.get("title", slug)
    series = (listing.get("series") or {}).get("title")

    y = px(BLEED + 0.7)
    if series and not title.lower().startswith(series.lower()):
        f_strap = ImageFont.truetype(FONT_BODY_BOLD, 40)
        draw.text((mx, y), series.upper(), font=f_strap, fill=accent)
        y += 70
    f_title = ImageFont.truetype(FONT_TITLE, 64)
    for line in _wrap(draw, title, f_title, maxw):
        draw.text((mx, y), line, font=f_title, fill=(255, 255, 255))
        y += 82
    y += 20
    draw.rectangle([mx, y, mx + 260, y + 8], fill=accent)
    y += 70

    desc = listing.get("description", "").strip()
    f_body = ImageFont.truetype(FONT_BODY, 41)
    body_lines = _wrap(draw, desc, f_body, maxw)
    # keep text above the barcode zone: stop 0.35" above it on the right side,
    # and above the author line
    author_y = H - px(BLEED + 0.55)
    limit_y = author_y - 80
    lh = 56
    max_lines = (limit_y - y) // lh
    if len(body_lines) > max_lines:
        body_lines = body_lines[: max_lines - 1]
        # trim trailing blank + end cleanly
        while body_lines and not body_lines[-1]:
            body_lines.pop()
        if body_lines and not body_lines[-1].rstrip().endswith((".", "!", "?", ":")):
            body_lines[-1] = body_lines[-1].rstrip().rstrip(",;") + "…"
    for line in body_lines:
        draw.text((mx, y), line, font=f_body, fill=(245, 243, 240))
        y += lh
    f_auth = ImageFont.truetype(FONT_BODY_BOLD, 44)
    draw.text((mx, author_y), (listing.get("author") or "WK Bui").upper(), font=f_auth, fill=(255, 255, 255))

    # barcode zone sanity: nothing drawn there (we only ever draw left of it)
    # zone: right edge of back panel, 2" x 1.2", inset 0.25" from trim
    bz_x1 = px(BLEED + TRIM_W - 0.25)
    bz_x0 = bz_x1 - px(2.0)
    bz_y1 = H - px(BLEED + 0.25)
    bz_y0 = bz_y1 - px(1.2)
    assert mx + maxw <= bz_x0 or author_y < bz_y0 or mx < bz_x0, "layout overlaps barcode zone"

    preview = book_dir / (interior.stem + "-cover-preview.png")
    img.save(preview)
    img.save(out_pdf, "PDF", resolution=DPI)
    print(f"built {out_pdf.name}: {W}x{H}px, spine {spine_in:.3f}\" ({pages}pp), bg={bg}, accent={accent}")
    return out_pdf


if __name__ == "__main__":
    force = "--force" in sys.argv
    slugs = [a for a in sys.argv[1:] if not a.startswith("--")]
    for s in slugs:
        build(s, force=force)
