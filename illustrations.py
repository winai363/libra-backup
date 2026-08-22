#!/usr/bin/env python3
"""Instructional illustrations for visual-niche books.

A how-to book without pictures of the how is what Amazon rejects as a
"disappointing customer experience" — the reason `acuarela-para-principiantes`
was lost. This module produces images that show the step being described, each
placed inside the section that describes it, each with provenance the quality
gate can verify.

Failure is loud: if one image cannot be produced, the whole run aborts. A
silently dropped image is exactly how a "visual" book ships as text.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

FILENAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,60}\.(png|jpg|jpeg)$")
HEADING_RE = re.compile(r"(?m)^(#{1,3})\s+(.+?)\s*$")

# Sections that exist for the publisher, not the learner — never illustrate them.
BACK_MATTER_MARKERS = (
    "référence", "references", "referencias", "quellen", "bibliograf",
    "ressources", "resources", "recursos", "avertissement", "disclaimer",
    "à propos", "about the author", "sobre el autor", "avis", "faq",
    "preface", "préface", "prefacio", "índice", "sommaire",
)


class IllustrationError(RuntimeError):
    pass


def build_image_prompt(brief: str, *, niche: str) -> str:
    """One prompt rule matters above all: no text inside the image.

    Generated lettering comes out malformed, and in a French or Spanish book
    malformed words on the page read as a defect to the first reviewer who
    opens it.
    """
    return (
        f"Instructional demonstration illustration for a printed how-to book about {niche}. "
        f"Show this step clearly: {brief}. "
        "Depict the actual technique in progress so a beginner can copy it — hands, tools, "
        "and the work surface where relevant, with the result visible. "
        "Clean, well-lit, uncluttered composition on a plain background, book-interior quality. "
        "Absolutely no text, no letters, no numbers, no labels, no captions, no watermarks, "
        "no logos, no brand marks, and no identifiable faces anywhere in the image."
    )


def _headings(manuscript: str) -> list:
    return [match.group(2).strip() for match in HEADING_RE.finditer(manuscript)]


def _is_back_matter(heading: str) -> bool:
    """Match on word starts — "avis" must not fire inside "lavis" (a wash)."""
    lowered = heading.casefold()
    return any(
        re.search(rf"(?<![^\W\d_]){re.escape(marker)}", lowered)
        for marker in BACK_MATTER_MARKERS
    )


def validate_briefs(briefs: list, manuscript: str) -> None:
    headings = _headings(manuscript)
    seen = set()
    for index, brief in enumerate(briefs):
        for field in ("heading", "filename", "alt_text", "prompt"):
            if not str(brief.get(field) or "").strip():
                raise IllustrationError(f"brief {index} is missing '{field}'")
        filename = brief["filename"]
        if not FILENAME_RE.match(filename):
            raise IllustrationError(f"brief {index} has an unsafe filename: {filename}")
        if filename in seen:
            raise IllustrationError(f"duplicate filename in briefs: {filename}")
        seen.add(filename)
        heading = brief["heading"]
        if heading not in headings:
            raise IllustrationError(
                f"brief {index} points at a heading that is not in the manuscript: {heading}"
            )
        if _is_back_matter(heading):
            raise IllustrationError(
                f"brief {index} targets back matter, which teaches nothing: {heading}"
            )


def insert_into_manuscript(manuscript: str, briefs: list) -> str:
    """Place each image just after the first paragraph of its own section."""
    validate_briefs(briefs, manuscript)
    lines = manuscript.splitlines()

    by_heading: dict = {}
    for brief in briefs:
        by_heading.setdefault(brief["heading"], []).append(brief)

    output: list = []
    pending: list = []
    seen_body = False

    def flush() -> None:
        nonlocal pending, seen_body
        for brief in pending:
            output.append("")
            output.append(f"![{brief['alt_text']}](images/{brief['filename']})")
            output.append("")
        pending = []
        seen_body = False

    for line in lines:
        match = HEADING_RE.match(line)
        if match:
            if pending:
                flush()
            output.append(line)
            pending = by_heading.get(match.group(2).strip(), [])
            seen_body = False
            continue
        output.append(line)
        if pending:
            if line.strip():
                seen_body = True
            elif seen_body:
                flush()
    if pending:
        flush()
    return "\n".join(output)


def render_illustrations(book_dir: Path, briefs: list, *, renderer: Callable, now: str) -> list:
    """Render every brief. One failure aborts the whole set."""
    images_dir = Path(book_dir) / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    rows = []
    for brief in briefs:
        destination = images_dir / brief["filename"]
        try:
            result = renderer(brief["prompt"], destination) or {}
        except Exception as exc:
            raise IllustrationError(
                f"illustration '{brief['filename']}' could not be produced: {exc}"
            ) from exc
        if not destination.exists() or destination.stat().st_size == 0:
            raise IllustrationError(
                f"illustration '{brief['filename']}' was not written to disk"
            )
        rows.append({
            "file": f"images/{brief['filename']}",
            "source_kind": "ai_generated",
            "source": result.get("model", "unknown-image-model"),
            "model": result.get("model", "unknown-image-model"),
            "prompt": brief["prompt"],
            "generated_at": now,
            "license": "generated-for-this-title",
            "contains_personal_data": False,
            "alt_text": brief["alt_text"],
            "heading": brief["heading"],
        })
    return rows


def write_provenance(book_dir: Path, rows: list) -> Path:
    path = Path(book_dir) / "image-provenance.json"
    path.write_text(
        json.dumps(
            {
                "images": rows,
                "disclosure": {
                    "ai_generated_images": True,
                    "note": (
                        "Interior illustrations were produced with an AI image model. "
                        "Declare AI-generated content when uploading to KDP."
                    ),
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path
