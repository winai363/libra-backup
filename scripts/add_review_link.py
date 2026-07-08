#!/usr/bin/env python3
"""Inject a per-marketplace clickable 'leave a review' link into a book's back
matter and rebuild its EPUB. Idempotent + surgical (only adds the link line).

The catalog is 88% non-US, so the review URL MUST use the marketplace the reader
bought on (amazon.es/.de/...), not amazon.com — a .com link is dead for an ES/DE
reader. Marketplace + localized CTA are derived from listing.json language.

Usage: python3 scripts/add_review_link.py <slug>
"""
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

KDP = Path("/root/kdp")

# language -> (amazon tld, localized 1-line-review CTA)
MARKET = {
    "english":    ("com",    "Leave a 1-line review here"),
    "spanish":    ("es",     "Deja tu reseña de 1 línea aquí"),
    "german":     ("de",     "Jetzt eine kurze Bewertung abgeben"),
    "french":     ("fr",     "Laissez un avis en une ligne ici"),
    "italian":    ("it",     "Lascia una recensione qui"),
    "dutch":      ("nl",     "Laat hier een korte review achter"),
    "portuguese": ("com.br", "Deixe sua avaliação aqui"),
}
# a back-matter review heading in any of our languages
REVIEW_HEAD = re.compile(
    r"^#{1,3}\s+.*(review|rese[ñn]a|bewertung|avis|recensione|beoordeling|avalia)",
    re.I)
DISCLAIMER_HEAD = re.compile(r"^#{1,3}\s+.*(disclaimer|aviso legal|haftung|avertissement)", re.I)


def build_epub(book: Path) -> bool:
    cmd = [
        "pandoc", str(book / "ebook.md"), "-f", "markdown-yaml_metadata_block",
        "-o", str(book / "ebook.epub"),
        "--resource-path", str(book),
        "--metadata-file", str(book / "metadata.yaml"),
        "--epub-cover-image", str(book / "cover.jpg"),
        "--css", "/root/libra/epub.css",
        "--toc", "--toc-depth=2",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print(f"  EPUB build FAILED: {r.stderr[:300]}")
        return False
    return True


def main(slug: str) -> bool:
    book = KDP / slug
    d = json.loads((book / "listing.json").read_text())
    asin = d.get("asin")
    lang = (d.get("language") or "english").lower().split("-")[0].strip()
    if not asin:
        print(f"  {slug}: no ASIN — skip")
        return False
    tld, cta = MARKET.get(lang, MARKET["english"])
    url = f"https://www.amazon.{tld}/review/create-review/?asin={asin}"
    link = f"👉 **[{cta}]({url})**"

    md = (book / "ebook.md").read_text(encoding="utf-8")
    if "review/create-review" in md:
        print(f"  {slug}: already has review link — skip")
        return False

    lines = md.splitlines()
    out, injected = [], False
    for ln in lines:
        out.append(ln)
        if not injected and REVIEW_HEAD.match(ln):
            out += ["", link, ""]      # prominent link right under the heading
            injected = True

    if not injected:
        # no review section — create one just before the Disclaimer (or at end)
        block = ["", "* * *", "", "## Please Leave a Review", "", link, ""]
        pos = next((i for i, ln in enumerate(out) if DISCLAIMER_HEAD.match(ln)), None)
        if pos is not None:
            out[pos:pos] = block
        else:
            out += block
        injected = True

    shutil.copy(book / "ebook.md", book / "ebook.md.bak-reviewlink")
    (book / "ebook.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"  {slug}: injected [{lang}→amazon.{tld}] {url}")
    return build_epub(book)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 scripts/add_review_link.py <slug>")
        sys.exit(1)
    ok = main(sys.argv[1])
    print("OK" if ok else "DONE(no-change/failed)")
    sys.exit(0 if ok else 1)
