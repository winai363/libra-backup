#!/usr/bin/env python3
"""
topic_scout.py — Multi-marketplace topic discovery engine.

Searches trending niches across 9 KDP marketplaces, scores each candidate
with market_intelligence.py, and returns the highest-scoring GO topic.

Usage:
    python3 topic_scout.py                  # print best topic JSON to stdout
    python3 topic_scout.py --save /tmp/topic.json
    python3 topic_scout.py --candidates     # show all 5 candidates + scores
"""

import json
import os
import re
import sys
import time
from datetime import date
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv("/root/libra/.env")

LIBRA_DIR = Path(__file__).parent
KDP_DIR = LIBRA_DIR.parent / "kdp"

MARKETPLACES = [
    {"tld": "de", "language": "German",     "lang_code": "de", "marketplace": "amazon.de"},
    {"tld": "fr", "language": "French",     "lang_code": "fr", "marketplace": "amazon.fr"},
    {"tld": "es", "language": "Spanish",    "lang_code": "es", "marketplace": "amazon.es"},
    {"tld": "it", "language": "Italian",    "lang_code": "it", "marketplace": "amazon.it"},
    {"tld": "nl", "language": "Dutch",      "lang_code": "nl", "marketplace": "amazon.nl"},
    {"tld": "pl", "language": "Polish",     "lang_code": "pl", "marketplace": "amazon.pl"},
    {"tld": "com", "language": "English",   "lang_code": "en", "marketplace": "amazon.com"},
    {"tld": "co.uk","language": "English",  "lang_code": "en", "marketplace": "amazon.co.uk"},
    {"tld": "co.jp","language": "Japanese", "lang_code": "ja", "marketplace": "amazon.co.jp"},
]

DISCOVERY_PROMPT = """You are a KDP niche analyst. Use web search to find 5 profitable ebook opportunities.

Existing books to AVOID duplicating:
{existing_books}

SEARCH STRATEGY — run these searches:
1. "kindle ebook bestseller" amazon.de site:amazon.de 2025 2026 — trending German niches
2. "kindle ebook bestseller" amazon.fr 2025 2026 — French niches
3. "ebook à succès" OR "bestseller kindle" amazon.fr 2025 — French buyer demand
4. trending amazon kindle niche 2026 non-fiction — English market gaps
5. "kindle libro electronico" OR "ebook exito" amazon.es 2026 — Spanish niches
6. self-help productivity kindle bestseller underserved niche 2025 2026
7. amazon kindle low competition high demand niche 2026 non-fiction

SELECTION CRITERIA (must satisfy ALL):
- Non-fiction only (no romance/fantasy/fiction/novel)
- High search demand, low competition (under 500 quality competing books ideal)
- Practical, how-to, workbook, guide format preferred
- Target working adults (25-55 years)
- Under-served in target language (few quality books in that language)
- Legally safe (no medical advice, investment advice, supplements, children)

Return JSON array of EXACTLY 5 candidates:
[
  {{
    "title": "Book title in target language",
    "subtitle": "Subtitle in target language",
    "slug": "english-kebab-case-slug",
    "language": "Full Language Name",
    "lang_code": "xx",
    "marketplace": "amazon.xx",
    "niche": "niche category in English",
    "title_en": "English translation of title",
    "description_en": "2-sentence English description of what the book covers",
    "demand_evidence": "What search results showed about demand",
    "competition_note": "How saturated is this niche"
  }},
  ...
]

Return ONLY the JSON array, no other text."""


def get_existing_books() -> str:
    lines = []
    for f in sorted(KDP_DIR.glob("*/listing.json")):
        try:
            d = json.loads(f.read_text())
            lines.append(f"- {d.get('title','')} [{d.get('language','')}] ({d.get('status','')})")
        except Exception:
            pass
    return "\n".join(lines) if lines else "(none yet)"


def discover_candidates(existing_books: str) -> list[dict]:
    """Ask GPT-4.1 with web search to find 5 candidate topics."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    prompt = DISCOVERY_PROMPT.format(existing_books=existing_books)

    response = client.responses.create(
        model="gpt-4.1",
        tools=[{"type": "web_search_preview"}],
        input=[{"role": "user", "content": prompt}],
        max_output_tokens=4000,
    )

    raw = ""
    for item in response.output:
        if hasattr(item, "content"):
            for c in item.content:
                if hasattr(c, "text"):
                    raw += c.text

    # Extract JSON array
    m = re.search(r'\[.*\]', raw, re.DOTALL)
    if not m:
        raise ValueError(f"No JSON array in discovery response: {raw[:400]}")
    candidates = json.loads(m.group())
    if not isinstance(candidates, list):
        raise ValueError("Expected list of candidates")
    return candidates[:5]


def _enrich_candidate(c: dict) -> dict:
    """Fill missing fields with defaults so score_topic() won't fail."""
    c.setdefault("title_en", c.get("title", ""))
    c.setdefault("niche", "")
    c.setdefault("marketplace", "amazon.com")
    c.setdefault("description_en", "")
    slug = c.get("slug", "")
    slug = re.sub(r'[^a-z0-9\-]', '-', slug.lower().strip())
    slug = re.sub(r'-{2,}', '-', slug).strip('-')
    c["slug"] = slug
    return c


def _slug_exists(slug: str) -> bool:
    return (KDP_DIR / slug).exists()


def scout(save_candidates_to: Path | None = None) -> dict:
    """
    Main entry: discover, score, and return the best GO topic.
    Raises RuntimeError if no GO topic found.
    """
    from market_intelligence import score_topic, THRESHOLD, print_summary

    existing_books = get_existing_books()
    print(f"[topic_scout] Discovering candidates (existing: {existing_books.count(chr(10))+1} books)...")
    candidates = discover_candidates(existing_books)
    print(f"[topic_scout] Got {len(candidates)} candidates from GPT-4.1")

    scored = []
    for i, c in enumerate(candidates):
        c = _enrich_candidate(c)
        print(f"\n[topic_scout] Scoring {i+1}/{len(candidates)}: {c.get('title','?')} ({c.get('language','?')})")

        if _slug_exists(c["slug"]):
            print(f"  SKIP: slug '{c['slug']}' already exists")
            c["_skipped"] = "duplicate_slug"
            scored.append(c)
            continue

        try:
            result = score_topic(c)
            c["_market_score"] = result
            c["_total_score"] = result["total_score"]
            c["_go_no_go"] = result["go_no_go"]
            print_summary(result)
        except Exception as e:
            print(f"  WARNING: scoring failed: {e}")
            c["_total_score"] = 0
            c["_go_no_go"] = "ERROR"

        scored.append(c)
        time.sleep(2)  # brief pause between API calls

    if save_candidates_to:
        save_candidates_to.write_text(
            json.dumps(scored, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\n[topic_scout] Candidates saved: {save_candidates_to}")

    # Pick best GO topic
    go_topics = [
        c for c in scored
        if c.get("_go_no_go") == "GO" and not c.get("_skipped")
    ]
    if not go_topics:
        raise RuntimeError(
            f"No GO topics found among {len(scored)} candidates. "
            "All scored below threshold or were duplicates."
        )

    best = max(go_topics, key=lambda c: c.get("_total_score", 0))
    print(f"\n[topic_scout] ✅ Best topic: {best['title']} ({best['language']}) "
          f"— score {best.get('_total_score', '?')}/100")
    return best


def to_writer_topic(c: dict) -> dict:
    """Convert scout result to the format gpt_fallback_writer.py expects."""
    return {
        "language":       c["language"],
        "lang_code":      c["lang_code"],
        "marketplace":    c["marketplace"],
        "title":          c["title"],
        "subtitle":       c.get("subtitle", ""),
        "slug":           c["slug"],
        "niche":          c.get("niche", ""),
        "description_en": c.get("description_en", ""),
        "title_en":       c.get("title_en", c["title"]),
        "_market_score":  c.get("_market_score"),
    }


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--save",       type=Path, help="Save best topic JSON to this path")
    parser.add_argument("--candidates", type=Path, help="Save all 5 candidates to this path")
    parser.add_argument("--candidates-only", action="store_true",
                        help="Discover candidates without scoring (faster)")
    args = parser.parse_args()

    try:
        best = scout(save_candidates_to=args.candidates)
        topic = to_writer_topic(best)

        if args.save:
            args.save.write_text(
                json.dumps(topic, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            print(f"[topic_scout] Best topic saved to: {args.save}")
        else:
            print(json.dumps(topic, indent=2, ensure_ascii=False))

        sys.exit(0)
    except RuntimeError as e:
        print(f"[topic_scout] ERROR: {e}", file=sys.stderr)
        sys.exit(1)
