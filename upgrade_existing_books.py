#!/usr/bin/env python3
"""Rewrite existing Libra titles to the current quality standard."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv
from openai import OpenAI, RateLimitError

import book_validator
import md_cleaner
from quality_gate import MIN_FICTION_WORDS, MIN_WORDS, validate_book, write_report


KDP_DIR = Path("/root/kdp")
LIBRA_DIR = Path("/root/libra")


def is_fiction(listing: dict) -> bool:
    text = json.dumps(listing, ensure_ascii=False).casefold()
    return bool(re.search(r"\b(?:fiction|romance|fantasy|romantasy|novel)\b", text))


def call_writer(prompt: str, max_tokens: int = 32000) -> str:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = None
    for attempt in range(4):
        try:
            response = client.chat.completions.create(
                model="gpt-4.1",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.55,
                max_tokens=max_tokens,
            )
            break
        except RateLimitError:
            if attempt == 3:
                raise
            time.sleep(65)
    if response is None:
        raise RuntimeError("Writer produced no response")
    content = response.choices[0].message.content or ""
    content = re.sub(r"^```(?:markdown)?\s*", "", content.strip())
    content = re.sub(r"\s*```$", "", content)
    return content


def word_count(content: str) -> int:
    return len(re.findall(r"\b[\wÀ-ž'-]+\b", content, re.UNICODE))


def rewrite_prompt(listing: dict, current: str, research: str, fiction: bool) -> str:
    language = listing.get("language", "English")
    if fiction:
        return f"""Rewrite this mismatched draft as a complete professional novella in {language}.

LISTING:
{json.dumps(listing, ensure_ascii=False)}

CURRENT DRAFT:
{current}

Requirements:
- Deliver an actual coherent story matching the title, subtitle, description, and categories
- Minimum {MIN_FICTION_WORDS:,} words; target 22,000-25,000 words
- Strong opening, clear character arcs, escalating but cozy conflict, satisfying ending
- Respectful disability representation; the character is not reduced to a lesson or cure narrative
- Historically plausible setting and internally consistent magic
- Show through scenes and dialogue; do not write a guide, essay, workbook, or commentary
- Write entirely in {language}
- Markdown only, with # for major parts and ## for chapter titles
- Do not include a title heading or manual table of contents
- End with About the Author and a short review request; fiction does not need references
- No image tags and no notes to the editor

Return the complete novella only."""

    return f"""Act as a senior international non-fiction editor. Rewrite and expand this complete
ebook in {language} so it is publication-ready, useful, accurate, and non-repetitive.

LISTING:
{json.dumps(listing, ensure_ascii=False)}

VERIFIED RESEARCH MATERIAL:
{research[:50000]}

CURRENT MANUSCRIPT:
{current}

Requirements:
- Minimum {MIN_WORDS:,} words; target 11,000-13,000 words
- Preserve the core promise but rebuild weak or repetitive sections
- Use practical examples, checklists, exercises, and step-by-step applications
- Write entirely in {language}, except unavoidable brand names and technical terms
- Every factual claim must use numbered citations from the supplied real sources
- At least 8 real references with working URLs; never invent a source
- Clear progression from beginner foundations to practical implementation
- Markdown only: # for major parts, ## for chapters, ### sparingly
- No title heading, no manual table of contents, no image tags
- End in this order: Resources, References, About the Author, Review Request, Disclaimer
- Do not include process notes or claim that content was AI-generated

Return the complete revised manuscript only."""


def continuation_prompt(listing: dict, content: str, research: str, fiction: bool, needed: int) -> str:
    language = listing.get("language", "English")
    # Send only last ~4000 words to stay within TPM limits as manuscript grows
    words = content.split()
    tail = " ".join(words[-4000:]) if len(words) > 4000 else content
    if fiction:
        return f"""Continue this {language} novella with {needed:,}+ additional words.
Add complete scenes and chapters that deepen character arcs and move naturally to a satisfying ending.
Do not repeat existing scenes, do not add a title or table of contents, and do not include commentary.

RECENT MANUSCRIPT (last portion):
{tail}

Return only the additional Markdown chapters."""
    return f"""Add {needed:,}+ substantive words to this {language} non-fiction manuscript.
Write new chapters only, with practical examples and citations limited to the research supplied below.
Do not repeat existing material and do not include back matter.

RESEARCH:
{research[:15000]}

RECENT MANUSCRIPT (last portion):
{tail}

Return only additional Markdown chapters."""


def revision_prompt(
    listing: dict,
    content: str,
    research: str,
    fiction: bool,
    editorial: dict,
) -> str:
    target = MIN_FICTION_WORDS if fiction else MIN_WORDS
    return f"""Revise this complete {"fiction" if fiction else "non-fiction"} manuscript in
{listing.get("language", "English")} to resolve every editorial issue below.

EDITORIAL REVIEW:
{json.dumps(editorial, ensure_ascii=False)}

LISTING:
{json.dumps(listing, ensure_ascii=False)}

RESEARCH:
{research[:40000]}

MANUSCRIPT:
{content}

Requirements:
- Return a complete replacement manuscript, not notes or a partial patch
- Minimum {target:,} words
- Eliminate repetition and deepen concrete reader value
- Resolve every critical issue and score failure from the review
- Preserve only verifiable facts and real source URLs
- No title heading, manual table of contents, image tags, or process commentary

Return Markdown only."""


def improve_listing(listing: dict, editorial: dict) -> dict:
    prompt = f"""Improve this Amazon KDP listing using the editorial SEO feedback.
Keep the same book identity and language. Return JSON only with title, subtitle,
description, author, language, keywords, categories, status, created_at, and retain
any kdp_book_id/uploaded_at fields supplied.

Requirements: accurate natural title, outcome-focused subtitle, 700-1500 character
description, exactly 7 unique localized long-tail keyword phrases of 2-50 characters,
and exactly 2 relevant category paths. No keyword stuffing or unsupported claims.

LISTING:
{json.dumps(listing, ensure_ascii=False)}

EDITORIAL:
{json.dumps(editorial, ensure_ascii=False)}
"""
    result = call_writer(prompt, max_tokens=3000)
    start = result.find("{")
    end = result.rfind("}")
    if start < 0 or end < start:
        raise ValueError("SEO listing revision returned invalid JSON")
    revised = json.loads(result[start:end + 1])
    for key in ("kdp_book_id", "uploaded_at", "content_updated_at"):
        if listing.get(key):
            revised[key] = listing[key]
    revised["status"] = "upgrading"
    # Enforce keyword length constraint (quality_gate requires 2-50 chars)
    _stop_words = {
        "a", "an", "the", "and", "or", "of", "in", "on", "at", "for",
        "to", "by", "with", "from", "using", "via", "as", "its", "their",
        "de", "da", "do", "em", "no", "na", "para", "com", "e", "o", "a",
        "los", "las", "el", "la", "en", "con", "y",
        "die", "der", "das", "für", "und", "mit", "von", "im",
    }
    if "keywords" in revised:
        fixed = []
        for kw in revised["keywords"]:
            if len(kw) > 50:
                s = kw[:50]
                last_space = s.rfind(" ")
                kw = s[:last_space] if last_space > 5 else s
                # Remove dangling stop-word at end
                words = kw.split()
                while words and words[-1].lower() in _stop_words:
                    words.pop()
                kw = " ".join(words)
            fixed.append(kw.strip())
        revised["keywords"] = fixed
    return revised


def backup_book(book_dir: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = book_dir / "backups" / f"before-quality-upgrade-{stamp}"
    backup.mkdir(parents=True)
    for name in ("ebook.md", "ebook.epub", "listing.json", "metadata.yaml", "cover.jpg"):
        source = book_dir / name
        if source.exists():
            shutil.copy2(source, backup / name)
    for pdf in book_dir.glob("*paperback*.pdf"):
        shutil.copy2(pdf, backup / pdf.name)
    return backup


def rebuild_epub(book_dir: Path) -> None:
    result = subprocess.run(
        [
            "pandoc",
            str(book_dir / "ebook.md"),
            "-f", "markdown-yaml_metadata_block",
            "-o",
            str(book_dir / "ebook.epub"),
            "--resource-path",
            str(book_dir),
            "--metadata-file",
            str(book_dir / "metadata.yaml"),
            "--epub-cover-image",
            str(book_dir / "cover.jpg"),
            "--css",
            str(LIBRA_DIR / "epub.css"),
            "--toc",
            "--toc-depth=2",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(f"EPUB build failed: {result.stderr[:500]}")


def build_pdf(slug: str) -> None:
    env = {}
    for line in (LIBRA_DIR / ".env").read_text().splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    response = httpx.post(
        f"http://127.0.0.1:8200/api/books/{slug}/generate-pdf",
        params={"force": "true"},
        headers={"Cookie": f"libra_token={env['SESSION_TOKEN']}"},
        timeout=240,
    )
    if response.status_code != 200:
        raise RuntimeError(f"PDF build failed ({response.status_code}): {response.text[:500]}")


def upgrade(slug: str, queue: bool = False, repair: bool = False) -> bool:
    book_dir = KDP_DIR / slug
    listing_path = book_dir / "listing.json"
    listing = json.loads(listing_path.read_text(encoding="utf-8"))
    fiction = is_fiction(listing)
    target = MIN_FICTION_WORDS if fiction else MIN_WORDS
    backup = backup_book(book_dir)
    print(f"[{slug}] backup: {backup}")

    listing["status"] = "upgrading"
    listing_path.write_text(json.dumps(listing, ensure_ascii=False, indent=2), encoding="utf-8")
    current = (book_dir / "ebook.md").read_text(encoding="utf-8")
    research = (book_dir / "content-research.md").read_text(encoding="utf-8")
    content = current if repair else call_writer(rewrite_prompt(listing, current, research, fiction))

    max_passes = 10 if fiction else 5
    for _ in range(max_passes):
        words = word_count(content)
        if words >= target:
            break
        main, back = book_validator.split_back_matter(content)
        extra = call_writer(
            continuation_prompt(listing, main, research, fiction, target - words + 1000),
            max_tokens=20000,
        )
        content = main.rstrip() + "\n\n" + extra.strip()
        if back:
            content += "\n\n" + back.strip()
    if word_count(content) < target:
        raise RuntimeError(
            f"Writer could not reach minimum length: {word_count(content):,}/{target:,} words"
        )

    content = md_cleaner.clean(content)
    content, structure = book_validator.validate_and_fix(content)
    if not structure.passed:
        raise RuntimeError(structure.summary())
    (book_dir / "ebook.md").write_text(content, encoding="utf-8")
    rebuild_epub(book_dir)
    build_pdf(slug)

    editorial = None
    report = None
    for revision_round in range(3):
        editorial = subprocess.run(
            ["python3", str(LIBRA_DIR / "editorial_review.py"), slug],
            timeout=900,
        )
        report = validate_book(
            slug,
            require_pdf=True,
            check_urls=not fiction,
            require_editorial=True,
        )
        if editorial.returncode == 0 and report.passed:
            break
        if revision_round == 2:
            break
        editorial_data = json.loads((book_dir / "editorial-review.json").read_text())
        listing = json.loads(listing_path.read_text(encoding="utf-8"))
        listing = improve_listing(listing, editorial_data)
        listing_path.write_text(
            json.dumps(listing, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        content = call_writer(
            revision_prompt(listing, content, research, fiction, editorial_data),
            max_tokens=32000,
        )
        for _ in range(max_passes):
            if word_count(content) >= target:
                break
            main, back = book_validator.split_back_matter(content)
            extra = call_writer(
                continuation_prompt(
                    listing,
                    main,
                    research,
                    fiction,
                    target - word_count(content) + 1000,
                ),
                max_tokens=20000,
            )
            content = main.rstrip() + "\n\n" + extra.strip()
            if back:
                content += "\n\n" + back.strip()
        content = md_cleaner.clean(content)
        content, structure = book_validator.validate_and_fix(content)
        if not structure.passed:
            raise RuntimeError(structure.summary())
        (book_dir / "ebook.md").write_text(content, encoding="utf-8")
        rebuild_epub(book_dir)
        build_pdf(slug)

    assert report is not None and editorial is not None
    write_report(report)
    listing = json.loads(listing_path.read_text(encoding="utf-8"))
    listing["status"] = "ready" if report.passed and editorial.returncode == 0 else "quality_failed"
    listing["quality_errors"] = report.errors
    listing_path.write_text(json.dumps(listing, ensure_ascii=False, indent=2), encoding="utf-8")
    if report.passed and editorial.returncode == 0 and queue:
        queue_path = LIBRA_DIR / "queue.txt"
        queued = queue_path.read_text().splitlines() if queue_path.exists() else []
        if slug not in queued:
            with queue_path.open("a") as handle:
                handle.write(slug + "\n")
    return report.passed and editorial.returncode == 0


def main() -> int:
    load_dotenv("/root/libra/.env")
    parser = argparse.ArgumentParser()
    parser.add_argument("slugs", nargs="*")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--queue", action="store_true")
    parser.add_argument("--repair", action="store_true")
    args = parser.parse_args()
    slugs = args.slugs
    if args.all:
        slugs = sorted(path.parent.name for path in KDP_DIR.glob("*/listing.json"))
    if not slugs:
        parser.error("Provide one or more slugs, or use --all")

    failures = []
    for slug in slugs:
        try:
            if not upgrade(slug, queue=args.queue, repair=args.repair):
                failures.append(slug)
        except Exception as exc:
            failures.append(slug)
            print(f"[{slug}] FAILED: {exc}")
            listing_path = KDP_DIR / slug / "listing.json"
            if listing_path.exists():
                data = json.loads(listing_path.read_text())
                data["status"] = "quality_failed"
                data["quality_errors"] = [str(exc)]
                listing_path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
        time.sleep(2)
    print(f"Completed: {len(slugs) - len(failures)}/{len(slugs)} passed")
    if failures:
        print("Failed:", ", ".join(failures))
    return 0 if not failures else 2


if __name__ == "__main__":
    raise SystemExit(main())
