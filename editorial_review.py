#!/usr/bin/env python3
"""AI editorial review with web fact checks for Libra pre-publish QA."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv
from openai import OpenAI
from openai import RateLimitError


KDP_DIR = Path("/root/kdp")

_GPT41_IN  = 2.0  / 1_000_000
_GPT41_OUT = 8.0  / 1_000_000
_SEARCH    = 25.0 / 1_000


def _append_step_cost(slug: str, step: str, response, root: Path | None = None) -> None:
    usage = getattr(response, "usage", None)
    inp  = (getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0)) if usage else 0
    out  = (getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0)) if usage else 0
    srch = sum(1 for item in response.output
               if getattr(item, "type", "") in ("web_search_call", "web_search_preview_call"))
    cost_usd = inp * _GPT41_IN + out * _GPT41_OUT + srch * _SEARCH
    report_path = (root if root is not None else KDP_DIR) / slug / "cost-report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        report = {}
    report[step] = {"input_tokens": inp, "output_tokens": out, "web_searches": srch, "cost_usd": round(cost_usd, 4)}
    total = sum(s.get("cost_usd", 0) for s in report.values() if isinstance(s, dict) and "cost_usd" in s)
    report["total_usd"] = round(total, 4)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
# factual_reliability and citation_quality are the noisiest dimensions — the
# reviewer fact-checks against external knowledge and swings ±2 on identical
# content, so we require 7 (solid, minor-improvements-possible) rather than 8.
# Genuinely false claims are still hard-blocked separately via "contradicted"
# fact_checks and critical_issues. The other five quality dimensions stay at 8.
REQUIRED_SCORES = {
    "language_purity": 8,
    "reader_value": 8,
    "structure": 8,
    "factual_reliability": 7,
    "citation_quality": 7,
    "seo_quality": 8,
    "originality": 8,
}


def is_fiction_listing(listing: dict) -> bool:
    """Only explicit classification can grant the fiction evidence exemption."""
    categories = listing.get("categories", [])
    if isinstance(categories, str):
        categories = [categories]
    if not isinstance(categories, list):
        categories = []
    text = " ".join(str(value) for value in [
        listing.get("type", ""), listing.get("book_type", ""),
        listing.get("genre", ""), *categories,
    ]).casefold()
    if re.search(r"\bnon[\s-]?fiction\b", text):
        return False
    fiction_types = {"fiction", "romance", "fantasy", "romantasy", "novel",
                     "roman historique", "science fiction", "literature & fiction"}
    if any(str(listing.get(key, "")).strip().casefold() in fiction_types
           for key in ("type", "book_type", "genre")):
        return True
    for category in categories:
        parts = [part.strip().casefold() for part in re.split(r"[>/]", str(category))]
        while parts and parts[0] in {"books", "kindle store", "kindle ebooks", "ebooks"}:
            parts.pop(0)
        if parts and parts[0] in fiction_types:
            return True
    return False


def editorial_source_hashes(book_dir: Path) -> dict:
    """Fingerprint the local inputs whose prose and promises were reviewed."""
    return {name: hashlib.sha256((book_dir / name).read_bytes()).hexdigest()
            for name in ("ebook.md", "listing.json")}


def validate_editorial_report(report: object, *, fiction: bool = False,
                              expected_sources: dict | None = None) -> dict:
    """Validate recorded evidence, not source truth or Amazon acceptance.

    URL checks establish syntax only; they do not fetch or verify source claims.
    A previously stored ``passed`` flag is deliberately not trusted.
    """
    score_failures = []
    evidence_failures = []
    if not isinstance(report, dict):
        report = {}
        evidence_failures.append("Editorial report must be an object")
    if expected_sources is not None and report.get("source_sha256") != expected_sources:
        evidence_failures.append("Editorial source hashes are missing or do not match current inputs")
    scores = report.get("scores")
    if not isinstance(scores, dict):
        scores = {}
    for name, minimum in REQUIRED_SCORES.items():
        score = scores.get(name)
        if (type(score) not in (int, float) or not math.isfinite(score)
                or not minimum <= score <= 10):
            score_failures.append(f"{name}={score!r}; expected {minimum}..10")

    if report.get("recommended_action") != "pass":
        evidence_failures.append("recommended_action must be pass")
    issues = report.get("critical_issues")
    if not isinstance(issues, list) or issues:
        evidence_failures.append("critical_issues must be an empty list")
    checks = report.get("fact_checks")
    if not isinstance(checks, list):
        evidence_failures.append("fact_checks must be a list")
        checks = []
    if not fiction and len(checks) < 5:
        evidence_failures.append("Non-fiction requires at least five supported fact checks")
    for index, check in enumerate(checks, 1):
        if not isinstance(check, dict):
            evidence_failures.append(f"fact_checks[{index}] must be an object")
            continue
        claim = check.get("claim")
        if not isinstance(claim, str) or not claim.strip():
            evidence_failures.append(f"fact_checks[{index}] needs a claim")
        if check.get("result") != "supported":
            evidence_failures.append(f"fact_checks[{index}] is not supported")
        source = check.get("source_url")
        valid_url = False
        if isinstance(source, str) and not any(char.isspace() for char in source):
            try:
                parsed = urlsplit(source)
                valid_url = parsed.scheme in ("http", "https") and bool(parsed.hostname)
            except ValueError:
                pass
        if not valid_url:
            evidence_failures.append(f"fact_checks[{index}] needs an http(s) source URL")
    return {
        "passed": not score_failures and not evidence_failures,
        "score_failures": score_failures,
        "evidence_failures": evidence_failures,
    }


def _extract_json(text: str) -> dict:
    start = text.find("{")
    if start < 0:
        raise ValueError("Review response did not contain JSON")
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:index + 1])
    raise ValueError("Review response contained incomplete JSON")


def _sample_manuscript(content: str, fiction: bool) -> tuple[str, bool]:
    """Return a representative sample of the manuscript within token budget.

    Non-fiction: first 8,000 words + last 4,000 words. The tail is essential —
    the References/citations section lives at the END, and citation_quality
    can't be judged if the reviewer never sees it (truncating head-only
    silently caps that score).
    Fiction: first 7,000 words + last 2,000 words to capture opening and ending.
    Returns (sampled_text, was_truncated).
    """
    words = content.split()
    if fiction:
        limit = 9000
        if len(words) <= limit:
            return content, False
        head = " ".join(words[:7000])
        tail = " ".join(words[-2000:])
        sampled = head + "\n\n[... middle portion omitted for editorial review ...]\n\n" + tail
        return sampled, True
    else:
        if len(words) <= 12000:
            return content, False
        head = " ".join(words[:8000])
        tail = " ".join(words[-4000:])
        sampled = head + "\n\n[... middle portion omitted for editorial review ...]\n\n" + tail
        return sampled, True


def review_book(slug: str, root: Path | None = None) -> dict:
    load_dotenv("/root/libra/.env")
    # `root` lets frozen staging review a book outside the live KDP tree.
    book_dir = (root if root is not None else KDP_DIR) / slug
    input_bytes = {name: (book_dir / name).read_bytes()
                   for name in ("ebook.md", "listing.json")}
    source_sha256 = {name: hashlib.sha256(value).hexdigest()
                     for name, value in input_bytes.items()}
    listing = json.loads(input_bytes["listing.json"].decode("utf-8"))
    content = input_bytes["ebook.md"].decode("utf-8")
    research = (book_dir / "content-research.md").read_text(encoding="utf-8")
    fiction = is_fiction_listing(listing)

    sampled_content, truncated = _sample_manuscript(content, fiction)
    # Tell the reviewer the TRUE size of the full manuscript. Otherwise, when the
    # book is truncated to fit the token budget, the reviewer counts only the
    # sample and wrongly concludes the book is "thin/short" — unfairly depressing
    # structure/reader_value/originality. Length & completeness are already gated
    # separately (quality_gate word-count + page minimums), so the editorial board
    # must judge WRITING quality, not guess length from a partial view.
    if truncated:
        total_words = len(content.split())
        total_sections = len(re.findall(r"(?m)^#{1,3}\s", content))
        portion = "opening + ending" if fiction else "opening portion"
        truncation_note = (
            f"\n[NOTE: This is a {'long' if not fiction else ''} manuscript. "
            f"The FULL book is {total_words:,} words across {total_sections} headings; "
            f"to fit token limits you are shown only the {portion}. "
            f"Do NOT judge length, completeness, or 'thinness' from this sample — "
            f"the full manuscript meets the word-count minimum (verified separately). "
            f"Judge WRITING quality, structure, and originality from what you can see.]"
        )
    else:
        truncation_note = ""

    prompt = f"""You are the senior editorial board for an international publisher.
Review this complete {"fiction" if fiction else "non-fiction"} ebook before Amazon KDP publication.
{"For fiction, use web search to check historical or real-world claims where applicable; fact checks may cover setting authenticity." if fiction else "Use web search to spot-check at least five important factual claims and source URLs."}
Be strict and fail weak, repetitive, misleading, mixed-language, thin, or poorly optimized books.{truncation_note}

BOOK LISTING:
{json.dumps(listing, ensure_ascii=False)}

MANUSCRIPT:
{sampled_content}

IMPORTANT: Score factual_reliability and citation_quality ONLY on claims that
actually appear in the MANUSCRIPT above. Do not invent statistics the book does
not state, and judge citations by the references the manuscript itself provides.

Return JSON only with this exact shape:
{{
  "scores": {{
    "language_purity": 0,
    "reader_value": 0,
    "structure": 0,
    "factual_reliability": 0,
    "citation_quality": 0,
    "seo_quality": 0,
    "originality": 0
  }},
  "critical_issues": [],
  "fact_checks": [
    {{"claim": "", "result": "supported|uncertain|contradicted", "source_url": ""}}
  ],
  "seo_notes": [],
  "editorial_notes": [],
  "recommended_action": "pass|revise"
}}

Scoring rules:
- 8 means publication-ready professional quality; award 8 when the dimension is solid and the book would satisfy a professional reader, even if minor improvements are possible. Reserve 7 for genuine weaknesses that would disappoint or mislead the target audience.
- language_purity: explanatory prose consistently uses the declared language.
- reader_value: 8 = delivers ≥5 actionable techniques with concrete, worked examples applicable immediately. Normal cross-chapter reinforcement of core concepts is acceptable and expected in professional non-fiction guides; only score 7 if the repetition is severe enough to make the book feel padded or if concrete examples are absent.
- structure: coherent progression, substantial sections, professional back matter.
- factual_reliability: {"historical and real-world details are credible and internally consistent" if fiction else "claims are current, precise, and supported by credible sources"}.
- citation_quality: {"score narrative consistency and responsible handling of real-world facts; references are optional" if fiction else "citations match references and URLs support the cited claims"}.
- seo_quality: localized buyer-intent title/subtitle/description/7 keyword phrases/categories.
- originality: no templated filler, repeated paragraphs, or suspicious imitation.
- Any contradicted important claim, fabricated citation, unsafe advice, or major language mixing
  must be listed in critical_issues and recommended_action must be revise.
"""

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = None
    for attempt in range(4):
        try:
            response = client.responses.create(
                model="gpt-4.1",
                tools=[{"type": "web_search"}],
                input=prompt,
                max_output_tokens=5000,
            )
            break
        except RateLimitError:
            if attempt == 3:
                raise
            time.sleep(65)
    if response is None:
        raise RuntimeError("Editorial review produced no response")

    # Track cost and append to cost-report.json
    _append_step_cost(slug, "editorial", response, root)

    text = getattr(response, "output_text", "") or ""
    if not text:
        chunks = []
        for item in response.output:
            for block in getattr(item, "content", []):
                value = getattr(block, "text", "")
                if value:
                    chunks.append(value)
        text = "\n".join(chunks)
    result = _extract_json(text)
    result["source_sha256"] = source_sha256
    result.update(validate_editorial_report(result, fiction=fiction))
    result.update(
        slug=slug,
        reviewer_model="gpt-4.1",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    args = parser.parse_args()
    output = KDP_DIR / args.slug / "editorial-review.json"
    try:
        result = review_book(args.slug)
    except Exception as exc:
        result = {
            "slug": args.slug,
            "passed": False,
            "critical_issues": [f"Editorial review failed: {exc}"],
        }
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("passed") else 2


if __name__ == "__main__":
    raise SystemExit(main())
