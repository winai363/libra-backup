#!/usr/bin/env python3
"""Auto-repair dead reference links before the quality gate runs.

Why this exists: the AI writer sometimes cites URLs that 404 (pages moved or
removed). The quality gate (correctly) refuses to publish a book that contains a
dead link, but it failed the WHOLE book over a single bad URL — so the daily
publisher silently stopped putting up new books.

This step runs first. It checks every http(s) URL in ebook.md, and for the ones
that are genuinely dead (404 etc. — NOT bot-blocks like 403/405) it removes just
that link from the manuscript, then rebuilds the EPUB. The gate then sees a clean
book. If too few live references remain, the gate still fails the book on its own
(minimum-references rule) — that part is unchanged and intentional.

Usage:  python3 repair_links.py <slug>
Exit 0 always (repair is best-effort; the gate is the real arbiter).
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from quality_gate import _check_urls, _urls  # reuse the exact same liveness logic

KDP_DIR = Path("/root/kdp")

# Multi-language "available at:" connectors, so that after we strip a dead URL
# from a numbered reference we don't leave a dangling "Available via:" tail.
_CONNECTOR_RE = re.compile(
    r"(?i)\s*(beschikbaar via|available (?:at|via|from)|disponible (?:en|à|sur)|"
    r"verfügbar unter|disponibile (?:presso|su)|dostępne pod adresem|"
    r"入手先|出处)\s*:\s*$"
)


def _dead_urls(content: str) -> list[str]:
    """Return the genuinely-dead URLs in the content (404-style, not bot-blocks)."""
    urls = {
        u for u in _urls(content)
        if u.startswith("http://") or u.startswith("https://")
    }
    if not urls:
        return []
    failed, _transient = _check_urls(urls)  # transient/network errors are NOT removed
    # _check_urls formats entries as "<url> (<code>)"; recover the raw URL.
    return [entry.rsplit(" (", 1)[0] for entry in failed]


def _strip_url(content: str, url: str) -> str:
    u = re.escape(url)
    # A) Whole bullet line whose only purpose is a dead markdown link → drop it.
    content = re.sub(
        rf"(?m)^[ \t]*[-*][ \t]*\[[^\]]*\]\({u}\)[^\n]*\n?",
        "",
        content,
    )
    # B) Inline markdown link [text](dead) → keep the visible text, drop the link.
    content = re.sub(rf"\[([^\]]*)\]\({u}\)", r"\1", content)
    # C) Bare URL left anywhere (e.g. "Available via: <dead>") → remove the URL,
    #    then tidy a now-dangling connector label at end of line.
    content = re.sub(u, "", content)
    content = _CONNECTOR_RE.sub("", content)
    return content


def _rebuild_epub(book: Path) -> bool:
    cmd = [
        "pandoc", str(book / "ebook.md"), "-f", "markdown-yaml_metadata_block",
        "-o", str(book / "ebook.epub"),
        "--resource-path", str(book),
        "--metadata-file", str(book / "metadata.yaml"),
        "--epub-cover-image", str(book / "cover.jpg"),
        "--css", "/root/libra/epub.css",
        "--toc", "--toc-depth=2",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"repair_links: EPUB rebuild FAILED for {book.name}: {res.stderr[:300]}")
        return False
    return True


def repair(slug: str) -> int:
    book = KDP_DIR / slug
    md_file = book / "ebook.md"
    if not md_file.exists():
        print(f"repair_links: no ebook.md for {slug} — nothing to do")
        return 0

    content = md_file.read_text(encoding="utf-8")
    dead = _dead_urls(content)
    if not dead:
        print(f"repair_links: {slug} — no dead links, nothing to repair")
        return 0

    print(f"repair_links: {slug} — removing {len(dead)} dead link(s): {dead}")
    for url in dead:
        content = _strip_url(content, url)
    # Collapse blank lines left behind by removed bullets.
    content = re.sub(r"\n{3,}", "\n\n", content)

    md_file.write_text(content, encoding="utf-8")
    _rebuild_epub(book)
    print(f"repair_links: {slug} — manuscript cleaned and EPUB rebuilt")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("usage: python3 repair_links.py <slug>")
        sys.exit(1)
    sys.exit(repair(sys.argv[1]))
