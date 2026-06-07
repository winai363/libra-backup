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
CJK_CODES = {"ja", "zh", "zh-cn", "zh-tw", "ko"}


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


def _duplicates(values: list[str]) -> list[str]:
    normalized = [re.sub(r"\s+", " ", value.strip()).casefold() for value in values]
    return sorted({value for value in normalized if normalized.count(value) > 1})


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


def _unreachable_urls(urls: set[str]) -> list[str]:
    failed = []
    headers = {"User-Agent": "Mozilla/5.0 LibraQualityGate/2.0"}
    with httpx.Client(follow_redirects=True, timeout=10, headers=headers) as client:
        for url in sorted(urls):
            try:
                response = client.head(url)
                if response.status_code >= 400 and response.status_code not in {401, 403, 429}:
                    response = client.get(url, headers={**headers, "Range": "bytes=0-1024"})
                if response.status_code in {401, 403, 429}:
                    continue
                if response.status_code >= 400:
                    failed.append(f"{url} ({response.status_code})")
            except httpx.HTTPError as exc:
                failed.append(f"{url} ({type(exc).__name__})")
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
        unreachable = _unreachable_urls(valid_urls)
        report.metrics["unreachable_urls"] = len(unreachable)
        if unreachable:
            report.error(f"Unreachable reference URLs: {unreachable[:8]}")

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
    if required["ebook.epub"].stat().st_size < 5_000:
        report.error("EPUB file is too small or invalid.")

    pdf_pages = _pdf_pages(book_dir)
    report.metrics["pdf_pages"] = pdf_pages
    if require_pdf and pdf_pages is None:
        report.error("Paperback PDF is required before publishing.")
    elif pdf_pages is not None and pdf_pages < MIN_PAGES:
        report.error(f"Generated paperback has {pdf_pages} pages; minimum is {MIN_PAGES}.")
    elif pdf_pages is None and estimated_pages < MIN_PAGES:
        report.error(f"Estimated paperback length is {estimated_pages} pages; minimum is {MIN_PAGES}.")

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
