#!/usr/bin/env python3
"""Deterministic pre-publish quality and SEO gate for Libra books."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import httpx
from PIL import Image
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlparse


KDP_DIR = Path("/root/kdp")
MIN_WORDS = 10_000
MIN_CJK_CHARS = 30_000
MIN_FICTION_WORDS = 20_000
MIN_PAGES = 40
MIN_REFERENCES = 8
MIN_SECTIONS = 12
MIN_CHARS_PER_PAGE = 200   # catastrophic content-drop floor (lowest real book ≈880)
CJK_CODES = {"ja", "zh", "zh-cn", "zh-tw", "ko"}
EPUBCHECK_JAR = Path("/opt/epubcheck-5.1.0/epubcheck.jar")


@dataclass
class GateReport:
    slug: str
    passed: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)

    def error(self, message: str) -> None:
        self.errors.append(message)
        self.passed = False

    def warning(self, message: str) -> None:
        self.warnings.append(message)


def _language_code(listing: dict, metadata: str) -> str:
    match = re.search(r"(?m)^lang:\s*['\"]?([^'\"\s]+)", metadata)
    if match:
        return match.group(1).lower()
    language = str(listing.get("language", "")).lower()
    return {"chinese": "zh", "japanese": "ja", "korean": "ko"}.get(language, language[:2])


def _content_units(content: str, lang_code: str) -> tuple[int, str]:
    if lang_code in CJK_CODES:
        return len(re.sub(r"[\s`*#\[\]()!_~|]", "", content)), "characters"
    return len(re.findall(r"\b[\wÀ-ž'-]+\b", content, re.UNICODE)), "words"


def _reference_numbers(content: str) -> tuple[set[int], set[int]]:
    cited = {int(n) for n in re.findall(r"\[(\d+)\]", content)}
    references = set()
    pattern = re.compile(
        r"(?m)^\s*(?:\[(\d+)\]|(\d+)[.)])\s+.*?"
        r"(?:https?://|Available from:|Verfügbar|Disponible)"
    )
    for match in pattern.finditer(content):
        references.add(int(match.group(1) or match.group(2)))
    return cited, references


def _urls(content: str) -> list[str]:
    # Also strip typographic/curly quotes which German prose uses around „https://"
    return [
        value.rstrip(".,;:!?)]}>\"'‘’“”„‟")
        for value in re.findall(r'https?://[^\s<>()\]‘’“”„‟"]+', content)
    ]


def _pdf_pages(book_dir: Path) -> int | None:
    pdfs = sorted(book_dir.glob("*paperback*.pdf"))
    if not pdfs:
        return None
    result = subprocess.run(
        ["pdfinfo", str(pdfs[0])],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if result.returncode != 0:
        return None
    match = re.search(r"(?m)^Pages:\s+(\d+)", result.stdout)
    return int(match.group(1)) if match else None


def _pdf_script_counts(book_dir: Path) -> dict | None:
    """Extract the paperback PDF's text layer and count CJK / Thai characters.
    Catches the silent failure where xelatex drops glyphs the font lacks (e.g.
    DejaVu has no CJK/Thai), producing a page-correct but text-empty PDF."""
    pdfs = sorted(book_dir.glob("*paperback*.pdf"))
    if not pdfs:
        return None
    try:
        result = subprocess.run(
            ["pdftotext", str(pdfs[0]), "-"],
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    text = result.stdout
    cjk = thai = 0
    for ch in text:
        o = ord(ch)
        if (0x3040 <= o <= 0x30FF or 0x4E00 <= o <= 0x9FFF
                or 0x3400 <= o <= 0x4DBF or 0xAC00 <= o <= 0xD7A3):
            cjk += 1
        elif 0x0E00 <= o <= 0x0E7F:
            thai += 1
    nonspace = sum(1 for ch in text if not ch.isspace())
    return {"cjk": cjk, "thai": thai, "nonspace": nonspace}


def _pdf_unembedded_fonts(book_dir: Path) -> list[str] | None:
    """Names of fonts NOT embedded in the paperback PDF. KDP rejects PDFs with
    unembedded fonts. [] = all embedded; None if no PDF / pdffonts unavailable."""
    pdfs = sorted(book_dir.glob("*paperback*.pdf"))
    if not pdfs:
        return None
    try:
        r = subprocess.run(["pdffonts", str(pdfs[0])],
                           capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    if r.returncode != 0:
        return None
    bad = []
    for line in r.stdout.splitlines()[2:]:
        cols = line.split()
        # cols: name [type...] encoding emb sub uni objNum objGen
        # The first yes/no token is the 'emb' column (robust to multi-word type).
        yn = [c for c in cols if c in ("yes", "no")]
        if yn and yn[0] == "no" and cols:
            bad.append(cols[0])
    return bad


def _epubcheck(book_dir: Path) -> dict | None:
    """Run the official W3C epubcheck. Returns {fatals,errors,warnings} or None
    if the validator/EPUB is unavailable (degrades gracefully)."""
    epub = book_dir / "ebook.epub"
    if not epub.exists() or not EPUBCHECK_JAR.exists():
        return None
    try:
        r = subprocess.run(
            ["java", "-jar", str(EPUBCHECK_JAR), str(epub)],
            capture_output=True, text=True, timeout=180,
        )
    except Exception:
        return None
    out = (r.stdout or "") + (r.stderr or "")
    m = re.search(r"(\d+)\s+fatals?\s*/\s*(\d+)\s+errors?\s*/\s*(\d+)\s+warnings?", out)
    if m:
        return {"fatals": int(m.group(1)), "errors": int(m.group(2)),
                "warnings": int(m.group(3))}
    if r.returncode == 0:
        return {"fatals": 0, "errors": 0, "warnings": 0}
    return None


def _duplicates(values: list[str]) -> list[str]:
    normalized = [re.sub(r"\s+", " ", value.strip()).casefold() for value in values]
    return sorted({value for value in normalized if normalized.count(value) > 1})


# Back-matter section titles across the languages this catalog ships in.
_BACK_MATTER_RE = re.compile(
    r'(?im)^#{1,2}\s+(?:'
    r'References?|Bibliograph|Resources?|Acknowledg|About\s+the\s+Author|'
    r'Disclaimer|Glossary|'
    r'参考文献|著者について|免責事項|リソース|謝辞|用語集|レビュー|'
    r'Referencias|Sobre\s+el\s+autor|Aviso\s+legal|Recursos|Bibliografía|'
    r'Referências|Sobre\s+o\s+autor|Isenção|'
    r'Literatur|Über\s+den\s+Autor|Haftungsausschluss|Danksagung|'
    r'Bibliographie|À\s+propos|Avertissement|Remerciements'
    r')'
)
# Chapter/part headings. One appearing AFTER back matter has started means a
# second outline was grafted past the book's ending (writer "continuation after
# back matter" bug — produces a repetitive, two-book manuscript).
_MAIN_CONTENT_RE = re.compile(
    r'(?im)^#{1,2}\s+(?:'
    r'Part\s*\d|Teil\s*\d|Parte\s*\d|Partie\s*\d|Deel\s*\d|Bagian\s*\d|'
    r'パート\s*\d|第\s*\d+\s*[章部]|'
    r'Chapter\s*\d|Kapitel\s*\d|Cap[íi]tulo\s*\d|Capitolo\s*\d|Chapitre\s*\d'
    r')'
)


def _back_matter_then_content(content: str) -> str | None:
    """Return the first chapter/part heading that appears AFTER back matter has
    started (a grafted-on second book), or None if structure is sound."""
    bms = [m.start() for m in _BACK_MATTER_RE.finditer(content)]
    if not bms:
        return None
    first_bm = min(bms)
    for m in _MAIN_CONTENT_RE.finditer(content):
        if m.start() > first_bm:
            end = content.find("\n", m.start())
            return content[m.start():end if end != -1 else m.start() + 60].strip()
    return None


# Numbered chapter/part headings, for detecting the SAME number appearing twice
# (a verbatim-duplicated block — e.g. "Capitolo 19" or "Part 13" written twice).
_NUMBERED_HEADING_RE = re.compile(
    r'(?im)^#{1,3}\s+('
    r'Part|Chapter|Capitolo|Cap[íi]tulo|Kapitel|Chapitre|Teil|Parte|Partie|'
    r'Deel|Bagian|Bölüm|パート'
    r')\s*(\d+)\b'
)
# Capture the unit character (章=chapter / 部=part) separately so "第1部" and
# "第1章" are NOT treated as the same heading — a book legitimately has both
# a Part 1 (第1部) and a Chapter 1 (第1章).
_JP_CHAPTER_RE = re.compile(r'(?im)^#{1,3}\s+第\s*(\d+)\s*([章部編篇巻])')


def _duplicate_chapter_numbers(content: str) -> list[str]:
    """Return labels of chapter/part numbers that appear more than once (a book
    written in circles / a duplicated block). Empty list = no repeats."""
    seen: dict[str, int] = {}
    for m in _NUMBERED_HEADING_RE.finditer(content):
        key = f"{m.group(1).title()} {m.group(2)}"
        seen[key] = seen.get(key, 0) + 1
    for m in _JP_CHAPTER_RE.finditer(content):
        key = f"第{m.group(1)}{m.group(2)}"
        seen[key] = seen.get(key, 0) + 1
    return [k for k, n in seen.items() if n > 1]


def _is_fiction(listing: dict) -> bool:
    text = " ".join(
        [
            str(listing.get("title", "")),
            str(listing.get("subtitle", "")),
            " ".join(map(str, listing.get("categories", []))),
            " ".join(map(str, listing.get("keywords", []))),
        ]
    ).casefold()
    return bool(
        re.search(
            r"\b(?:fiction|romance|fantasy|romantasy|novel|roman historique)\b",
            text,
        )
    )


# --- Kindle-format suitability -------------------------------------------------
# Amazon KDP rejects books "not suited to the Kindle format" — puzzle books,
# blank journals, pattern books, coloring books, and facing-page translations.
# Libra is a Kindle-eBook-only pipeline, so these MUST never be generated/uploaded
# (they belong in paperback-only workflows). Each book that slips through is a
# guaranteed Amazon rejection + a strike against the account.

# Unambiguous game/puzzle names — safe to match bare (no generic-word collisions).
_PUZZLE_TERMS = [
    # English / international
    r"sudoku", r"crosswords?", r"word ?search(?:es)?", r"word find", r"word fill",
    r"kakuro", r"kenken", r"nonograms?", r"cryptograms?", r"cryptic crosswords?",
    r"dot[- ]to[- ]dot", r"spot the difference", r"find the difference",
    r"seek and find", r"search and find", r"hidden words?", r"hidden pictures?",
    r"logic puzzles?", r"brain games?", r"brain teasers?", r"anagram",
    # French
    r"mots mêlés", r"mots fléchés", r"mots croisés", r"jeux de mots",
    r"jeux de réflexion", r"casse-têtes?", r"points à relier",
    # German
    r"kreuzworträtsel", r"suchsel", r"wortsuche", r"zahlenrätsel", r"rätselbuch",
    # Spanish
    r"sopa de letras", r"crucigramas?", r"pasatiempos",
    # Italian
    r"parole crociate", r"cruciverba", r"enigmistica", r"giochi di parole",
    # Dutch
    r"kruiswoordpuzzels?", r"woordzoekers?",
    # Polish
    r"krzyżówk", r"łamigłówk",
    # Japanese
    r"クロスワード", r"ナンプレ", r"数独", r"言葉遊び",
]

# Generic words that are only unsuitable as a *book/notebook* — require a
# book-type qualifier so we don't false-positive on metaphorical prose
# ("solve life's puzzles", "the maze of bureaucracy", "journal article").
_NEEDS_BOOK_QUALIFIER = (
    r"(?:puzzles?|coloring|colouring|color by number|colour by number|maze|mazes|"
    r"labyrinth|labyrinthe|coloriage|colorier|malbuch|ausmalbuch|libro para colorear|"
    r"libro da colorare|kleurboek|kolorowank|塗り絵|ぬりえ|パズル|迷路|"
    r"activity|activité|aktivität|attività|actividad|"
    r"cross stitch|knitting|crochet|quilting|sewing|pattern|"
    r"blank|lined|dot grid|guided|gratitude)"
)
_BOOK_QUALIFIER = (
    r"(?:books?|pads?|journals?|notebooks?|planners?|"
    r"livres?|cahiers?|carnets?|buch|bücher|heft|libros?|libro|libri|quaderni?|"
    r"boek|boeken|książk|帳|ノート)"
)

_PUZZLE_RE = re.compile("|".join(_PUZZLE_TERMS), re.IGNORECASE)
# qualifier within ~3 words of the generic term, either order
_QUALIFIED_RE = re.compile(
    rf"\b{_NEEDS_BOOK_QUALIFIER}\b(?:\W+\w+){{0,3}}\W+{_BOOK_QUALIFIER}\b"
    rf"|\b{_BOOK_QUALIFIER}\b(?:\W+\w+){{0,3}}\W+{_NEEDS_BOOK_QUALIFIER}\b",
    re.IGNORECASE,
)


def kindle_unsuitable(listing: dict) -> str | None:
    """Return a reason string if this book is not suited to the Kindle format
    (puzzle/coloring/blank-journal/pattern/activity book), else None."""
    metadata_text = " ".join(
        [
            str(listing.get("title", "")),
            str(listing.get("subtitle", "")),
            str(listing.get("description", "")),
            " ".join(map(str, listing.get("categories", []))),
            " ".join(map(str, listing.get("keywords", []))),
        ]
    )
    m = _PUZZLE_RE.search(metadata_text)
    if m:
        return f"puzzle/game content detected ('{m.group(0)}')"
    m = _QUALIFIED_RE.search(metadata_text)
    if m:
        return f"activity/coloring/journal/pattern book detected ('{m.group(0).strip()}')"
    return None


# Status codes where the server clearly exists but is blocking/restricting
# automated access (bot protection, auth walls, rate limits, method limits).
# These are NOT dead links — never fail the upload on them.
_BLOCKED_NOT_DEAD = {401, 403, 405, 406, 429, 451, 999}


def _check_urls(urls: set[str]) -> tuple[list[str], list[str]]:
    failed = []
    transient = []
    # Use a realistic browser User-Agent — many sites (e.g. nih.gov) return
    # 403/405 to non-browser agents, which is a false positive for "dead link".
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    with httpx.Client(follow_redirects=True, timeout=10, headers=headers) as client:
        for url in sorted(urls):
            try:
                response = client.head(url)
                # HEAD is often unsupported/blocked → retry with a real GET.
                if response.status_code >= 400 and response.status_code not in _BLOCKED_NOT_DEAD:
                    response = client.get(url, headers={**headers, "Range": "bytes=0-1024"})
                if response.status_code in _BLOCKED_NOT_DEAD:
                    continue
                if response.status_code >= 400:
                    failed.append(f"{url} ({response.status_code})")
            except httpx.HTTPError as exc:
                transient.append(f"{url} ({type(exc).__name__})")
    return failed, transient


def _unreachable_urls(urls: set[str]) -> list[str]:
    """Compatibility wrapper used by existing tests and callers."""
    failed, _ = _check_urls(urls)
    return failed


def validate_book(
    slug: str,
    require_pdf: bool = False,
    check_urls: bool = False,
    require_editorial: bool = False,
) -> GateReport:
    report = GateReport(slug=slug)
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,100}", slug):
        report.error("Invalid slug format.")
        return report

    book_dir = KDP_DIR / slug
    required = {
        "listing.json": book_dir / "listing.json",
        "ebook.md": book_dir / "ebook.md",
        "metadata.yaml": book_dir / "metadata.yaml",
        "ebook.epub": book_dir / "ebook.epub",
        "cover.jpg": book_dir / "cover.jpg",
        "market-research.md": book_dir / "market-research.md",
        "content-research.md": book_dir / "content-research.md",
    }
    for name, path in required.items():
        if not path.exists() or path.stat().st_size == 0:
            report.error(f"Missing required file: {name}")
    if report.errors:
        return report

    try:
        listing = json.loads(required["listing.json"].read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        report.error(f"Invalid listing.json: {exc}")
        return report

    unsuitable = kindle_unsuitable(listing)
    if unsuitable:
        report.error(
            f"Not suited to the Kindle format: {unsuitable}. Amazon rejects "
            "puzzle/coloring/blank-journal/pattern/activity books as eBooks. "
            "Libra is Kindle-only — do not generate or upload this type."
        )

    content = required["ebook.md"].read_text(encoding="utf-8")
    metadata = required["metadata.yaml"].read_text(encoding="utf-8")
    fiction = _is_fiction(listing)
    lang_code = _language_code(listing, metadata)
    units, unit_name = _content_units(content, lang_code)
    estimated_pages = units // (750 if lang_code in CJK_CODES else 300)
    minimum_units = (
        MIN_CJK_CHARS
        if lang_code in CJK_CODES
        else MIN_FICTION_WORDS if fiction else MIN_WORDS
    )
    report.metrics.update(
        units=units,
        unit_name=unit_name,
        estimated_pages=estimated_pages,
        language_code=lang_code,
        fiction=fiction,
    )
    if units < minimum_units:
        report.error(
            f"Book is too short: {units:,} {unit_name}; minimum is "
            f"{minimum_units:,} for at least {MIN_PAGES} estimated pages."
        )

    headings = re.findall(r"(?m)^#{1,3}\s+(.+)$", content)
    sections = re.findall(r"(?m)^##\s+.+$", content)
    report.metrics.update(headings=len(headings), sections=len(sections))
    if len(sections) < MIN_SECTIONS:
        report.error(f"Only {len(sections)} sections found; minimum is {MIN_SECTIONS}.")
    duplicate_headings = _duplicates(headings)
    if duplicate_headings:
        report.warning(f"Repeated headings need editorial review: {', '.join(duplicate_headings[:5])}")

    grafted = _back_matter_then_content(content)
    if grafted:
        report.metrics["grafted_content_heading"] = grafted
        report.error(
            "Chapter/part content appears AFTER the book's back matter "
            f"(\"{grafted[:60]}\") — a second outline was grafted on, producing a "
            "repetitive/duplicated manuscript. The book must end with its back matter."
        )

    dup_chapters = _duplicate_chapter_numbers(content)
    if dup_chapters:
        report.metrics["duplicate_chapter_numbers"] = dup_chapters
        report.error(
            "Duplicated chapter/part numbers (a repeated block / book written in "
            f"circles): {', '.join(sorted(dup_chapters)[:8])}."
        )

    cited, references = _reference_numbers(content)
    valid_urls = {
        url for url in _urls(content)
        if urlparse(url).scheme in {"http", "https"} and urlparse(url).netloc
    }
    report.metrics.update(citations=len(cited), references=len(references), urls=len(valid_urls))
    if not fiction and len(references) < MIN_REFERENCES:
        report.error(f"Only {len(references)} numbered references found; minimum is {MIN_REFERENCES}.")
    missing = sorted(cited - references)
    if not fiction and missing:
        report.error(f"Citations without matching references: {missing[:12]}")
    if not fiction and len(valid_urls) < MIN_REFERENCES:
        report.error(f"Only {len(valid_urls)} valid reference URLs found; minimum is {MIN_REFERENCES}.")
    if not fiction and check_urls and valid_urls:
        unreachable, transient_url_errors = _check_urls(valid_urls)
        report.metrics["unreachable_urls"] = len(unreachable)
        report.metrics["transient_url_errors"] = len(transient_url_errors)
        if unreachable:
            report.error(f"Unreachable reference URLs: {unreachable[:8]}")
        if transient_url_errors:
            report.warning(
                f"Reference URLs could not be verified due to temporary network errors: "
                f"{transient_url_errors[:8]}"
            )

    title = str(listing.get("title", "")).strip()
    subtitle = str(listing.get("subtitle", "")).strip()
    description = str(listing.get("description", "")).strip()
    keywords = listing.get("keywords", [])
    categories = listing.get("categories", [])
    if not 3 <= len(title) <= 200:
        report.error("SEO title must contain 3-200 characters.")
    if len(subtitle) > 200:
        report.error("SEO subtitle exceeds 200 characters.")
    if not 150 <= len(description) <= 4000:
        report.error("SEO description must contain 150-4,000 characters.")
    valid_keywords = (
        isinstance(keywords, list)
        and len(keywords) == 7
        and all(isinstance(keyword, str) and 2 <= len(keyword.strip()) <= 50 for keyword in keywords)
    )
    if not valid_keywords:
        report.error("Exactly 7 SEO keyword phrases are required; each must contain 2-50 characters.")
    elif len({keyword.strip().casefold() for keyword in keywords}) != 7:
        report.error("SEO keyword phrases must be unique.")
    if not isinstance(categories, list) or len(categories) < 2:
        report.error("At least 2 relevant KDP categories are required.")
    report.metrics["seo_keywords"] = len(keywords) if isinstance(keywords, list) else 0

    if required["cover.jpg"].stat().st_size < 10_000:
        report.error("Cover file is too small or invalid.")
    else:
        try:
            with Image.open(required["cover.jpg"]) as cover:
                report.metrics["cover_size"] = list(cover.size)
                if cover.size != (1600, 2560):
                    report.error(
                        f"Cover dimensions are {cover.size[0]}x{cover.size[1]}; "
                        "required size is 1600x2560."
                    )
                if cover.mode != "RGB":
                    report.error(f"Cover color mode is {cover.mode}; RGB is required.")
        except Exception as exc:
            report.error(f"Invalid cover image: {exc}")

        # Anti-tofu: a valid JPEG can still show □ boxes if the cover font lacks
        # glyphs for the title's script (e.g. DejaVu has no CJK/Thai). Catch it.
        try:
            from cover_generator import unrenderable_chars
            author = str(listing.get("author", ""))
            tofu = unrenderable_chars(title, subtitle, author)
            if tofu:
                report.metrics["cover_unrenderable_chars"] = "".join(tofu)
                report.error(
                    "Cover text has characters with no font glyph (would render "
                    f"as boxes): {''.join(tofu[:20])}"
                )
        except Exception as exc:
            report.warning(f"Could not run cover glyph check: {exc}")
    if required["ebook.epub"].stat().st_size < 5_000:
        report.error("EPUB file is too small or invalid.")
    else:
        epub_result = _epubcheck(book_dir)
        if epub_result is not None:
            report.metrics["epubcheck"] = epub_result
            if epub_result["fatals"] or epub_result["errors"]:
                report.error(
                    f"EPUB failed epubcheck: {epub_result['fatals']} fatal / "
                    f"{epub_result['errors']} error(s) — KDP will likely reject it."
                )
            elif epub_result["warnings"]:
                report.warning(
                    f"epubcheck reported {epub_result['warnings']} warning(s)."
                )

    pdf_pages = _pdf_pages(book_dir)
    report.metrics["pdf_pages"] = pdf_pages
    if require_pdf and pdf_pages is None:
        report.error("Paperback PDF is required before publishing.")
    elif pdf_pages is not None and pdf_pages < MIN_PAGES:
        report.error(f"Generated paperback has {pdf_pages} pages; minimum is {MIN_PAGES}.")
    elif pdf_pages is None and estimated_pages < MIN_PAGES:
        report.error(f"Estimated paperback length is {estimated_pages} pages; minimum is {MIN_PAGES}.")

    # Interior render checks (only meaningful once a PDF exists).
    if pdf_pages is not None:
        lang = str(listing.get("language", "")).lower()
        expect_cjk = any(k in lang for k in ("japanese", "chinese", "korean",
                                              "mandarin", "cantonese"))
        expect_thai = "thai" in lang

        counts = _pdf_script_counts(book_dir)
        if counts is not None:
            report.metrics["pdf_script_counts"] = counts
            # (a) CJK/Thai glyph-drop: a book in that language with no such text
            #     means xelatex dropped the glyphs (wrong font) → blank paperback.
            if expect_cjk and counts["cjk"] < 50:
                report.error(
                    f"Paperback PDF has almost no CJK text ({counts['cjk']} chars) "
                    "for a CJK-language book — the font likely dropped the glyphs."
                )
            if expect_thai and counts["thai"] < 50:
                report.error(
                    f"Paperback PDF has almost no Thai text ({counts['thai']} chars) "
                    "for a Thai-language book — the font likely dropped the glyphs."
                )
            # (b) Catastrophic content-drop net for ANY language: a page-correct
            #     but near-empty PDF (extraction far below any real book).
            if pdf_pages > 0:
                cpp = counts["nonspace"] // pdf_pages
                report.metrics["pdf_chars_per_page"] = cpp
                if cpp < MIN_CHARS_PER_PAGE:
                    report.error(
                        f"Paperback PDF averages only {cpp} characters/page "
                        f"(min {MIN_CHARS_PER_PAGE}) — interior content may be "
                        "missing or failed to render."
                    )

        # (c) Font embedding — KDP rejects PDFs with non-embedded fonts.
        unembedded = _pdf_unembedded_fonts(book_dir)
        if unembedded:
            report.metrics["pdf_unembedded_fonts"] = unembedded
            report.error(
                "Paperback PDF has non-embedded font(s): "
                f"{', '.join(unembedded[:5])} — KDP requires all fonts embedded."
            )

    editorial_file = book_dir / "editorial-review.json"
    if require_editorial:
        if not editorial_file.exists():
            report.error("Missing editorial-review.json.")
        else:
            try:
                editorial = json.loads(editorial_file.read_text(encoding="utf-8"))
                report.metrics["editorial_passed"] = bool(editorial.get("passed"))
                if not editorial.get("passed"):
                    report.error("AI editorial board did not approve this book.")
            except (OSError, json.JSONDecodeError) as exc:
                report.error(f"Invalid editorial-review.json: {exc}")
    return report


def write_report(report: GateReport) -> Path:
    output = KDP_DIR / report.slug / "quality-report.json"
    output.write_text(json.dumps(asdict(report), ensure_ascii=False, indent=2), encoding="utf-8")
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("--require-pdf", action="store_true")
    parser.add_argument("--check-urls", action="store_true")
    parser.add_argument("--require-editorial", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    report = validate_book(
        args.slug,
        require_pdf=args.require_pdf,
        check_urls=args.check_urls,
        require_editorial=args.require_editorial,
    )
    write_report(report)
    if args.json:
        print(json.dumps(asdict(report), ensure_ascii=False, indent=2))
    else:
        print(f"{'PASS' if report.passed else 'FAIL'}: {report.slug}")
        for error in report.errors:
            print(f"ERROR: {error}")
    return 0 if report.passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
