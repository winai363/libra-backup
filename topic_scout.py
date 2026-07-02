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

DISCOVERY_PROMPT = """You are a KDP niche analyst. Use web search to find 5 HIGH-DEMAND, LOW-COMPETITION ebook opportunities.

PRIMARY GOAL: Find niches where REAL buyers are searching but FEW quality books exist.
A niche with 10,000 buyers and 20 competing books beats a niche with 100,000 buyers and 5,000 books.

{proven_winners}

{seasonal_context}

Existing books to AVOID duplicating:
{existing_books}

SEARCH STRATEGY — run ALL these searches:
1. amazon kindle ebook low competition high demand niche 2025 2026 non-fiction
2. "kindle direct publishing" underserved niche non-fiction {language_hint} 2026
3. amazon.de kindle ebook "wenige Ergebnisse" OR "nur wenige" — German niches with few results
4. amazon.fr kindle ebook "peu de résultats" OR niche sous-représentée — French gaps
5. amazon.es kindle ebook nicho poco competido — Spanish under-served niches
6. amazon.nl OR amazon.pl kindle ebook — Dutch/Polish market gaps (very few quality books exist)
7. "{niche_hint}" kindle ebook site:amazon.de — check real result counts in German market
8. self-help OR productivity OR finance OR parenting kindle niche gap 2026 non-English

FOR EACH CANDIDATE — you MUST verify:
- Search the niche on amazon.{{tld}} (the target marketplace, e.g. amazon.es): how many books appear with 10+ reviews and 4+ stars?
- If >500 quality competing books → REJECT this niche, pick another
- Does demand evidence exist? (search volume, buyer forums, reddit discussions)

HARD FILTER — only include if ALL pass:
✅ Non-fiction, practical (how-to, guide, text-based workbook with written exercises)
✅ Under 500 quality competing books in the target marketplace
✅ Under 40 quality books written IN the target language specifically
✅ Real buyer demand evidence found (not just "this topic exists")
✅ Target working adults 25-55, legally safe topic
✅ NOT in existing books list above
✅ TIMING: if the niche is seasonal/deadline-driven (taxes, New Year, back-to-school,
   holidays), its buying window must be OPEN or arriving within ~3 months given the
   publishing lead time above — REJECT a seasonal niche whose window just closed
   (e.g. an annual tax-RETURN guide right after the filing deadline). Evergreen
   niches pass anytime.

❌ HARD REJECT — these are NOT suited to the Kindle eBook format and Amazon will
   refuse to publish them. NEVER propose any of these, in any language:
   - Puzzle / game books: sudoku, crosswords, word search, mazes, logic puzzles,
     brain-teaser/activity books (FR: jeux de mots/mots mêlés/cahier d'activités;
     DE: Rätsel/Kreuzworträtsel; ES: sopa de letras/pasatiempos; IT: cruciverba;
     JP: クロスワード/数独/迷路)
   - Coloring books, dot-to-dot, anything to fill in by hand
   - Blank/lined journals, notebooks, planners, gratitude journals (no real text content)
   - Pattern books: knitting/crochet/cross-stitch/sewing/quilting patterns
   - Facing-page (bilingual side-by-side) translation books
   The book must be READABLE TEXT a person consumes on a Kindle screen.

Return JSON array of EXACTLY 5 candidates (already filtered to low-competition only):
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
    "demand_evidence": "Specific evidence: search volume, Reddit/forum discussions, buyer search terms found",
    "demand_timing": "evergreen, OR the specific upcoming demand window with its date (e.g. 'Spanish IVA trimestral deadline ~20 Jul' or 'New Year resolutions, peaks late Dec–Jan')",
    "verified_competing_books": <integer — actual count of quality books (10+ reviews, 4+ stars) you found>,
    "verified_books_in_language": <integer — quality books specifically in this language>,
    "competition_note": "Why this is low competition — what gap exists"
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


PUBLISH_LEAD_DAYS = 14  # write + QA + upload + KDP review before a book is live


def _seasonal_context() -> str:
    """Tell the model today's date and the publishing lead time, so it can favour
    niches whose demand window is open or arriving — and skip ones that just closed."""
    from datetime import datetime, timedelta
    today = datetime.now()
    live_by = today + timedelta(days=PUBLISH_LEAD_DAYS)
    return (
        "TIMING CONTEXT (demand is often seasonal — match supply to the calendar):\n"
        f"- Today is {today:%Y-%m-%d} ({today:%B %d, %Y}).\n"
        f"- A book picked now goes live around {live_by:%Y-%m-%d} "
        f"(~{PUBLISH_LEAD_DAYS}-day write/QA/upload/review lead time).\n"
        "- PREFER niches with a demand spike that is OPEN now or arriving within ~3 "
        "months of the live date (deadlines, holidays, season changes, annual events). "
        "A timely book beats a generic one.\n"
        "- AVOID seasonal niches whose window just passed — that demand collapses until "
        "next year. Evergreen niches are fine anytime."
    )


def discover_candidates(existing_books: str, proven_winners: str = "") -> list[dict]:
    """Ask GPT-4.1 with web search to find 5 candidate topics."""
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    # language_hint: push toward under-represented languages in existing library
    existing_langs = [ln for ln in ["German","French","Spanish","Italian","Dutch","Polish","Japanese"]
                      if ln.lower() not in existing_books.lower()]
    language_hint = ", ".join(existing_langs[:4]) if existing_langs else "non-English"
    niche_hint = "personal finance, career skills, productivity, parenting, health habits, sustainability"
    prompt = DISCOVERY_PROMPT.format(
        existing_books=existing_books,
        language_hint=language_hint,
        niche_hint=niche_hint,
        proven_winners=proven_winners or "PROVEN WINNERS: none yet.",
        seasonal_context=_seasonal_context(),
    )

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
    c.setdefault("demand_timing", "")
    slug = c.get("slug", "")
    slug = re.sub(r'[^a-z0-9\-]', '-', slug.lower().strip())
    slug = re.sub(r'-{2,}', '-', slug).strip('-')
    c["slug"] = slug
    return c


def _slug_exists(slug: str) -> bool:
    return (KDP_DIR / slug).exists()


def _opportunity_score(c: dict, proven: tuple[set, set] | None = None) -> float:
    """
    Weighted ranking score that prioritises low competition over raw total.
    competition (20pts) × 2.5 + language_saturation (10pts) × 2.0 + demand (20pts) × 1.0
    This ensures a blue-ocean topic with moderate demand ranks above a saturated
    topic with very high demand.

    Learning-loop boost: a candidate that serves a PROVEN buyer (same language as a
    book that already sold, or sharing words with a winning niche/title) gets a
    bonus so adjacent "double-down" topics rank above cold new niches — exploiting
    verified demand without overriding the hard competition/language gates.
    """
    ms = c.get("_market_score", {})
    scores = ms.get("scores", {})
    comp   = scores.get("competition",        {}).get("score", 0)
    lang   = scores.get("language_saturation",{}).get("score", 0)
    demand = scores.get("demand",             {}).get("score", 0)
    base = comp * 2.5 + lang * 2.0 + demand * 1.0

    # Unit-economics term: expected monthly royalty (GPT competitor estimate),
    # capped at $50/mo so a noisy estimate can't dominate the market gates.
    # Production cost is ~$0.20/book so break-even is trivial — this term exists
    # to rank "sellable" above "merely uncontested" among GO topics.
    try:
        royalty = float((ms.get("expected_royalty") or {}).get("estimated_monthly_royalty_usd") or 0)
    except (TypeError, ValueError):
        royalty = 0.0
    base += min(max(royalty, 0.0), 50.0) / 5.0   # up to +10 points

    boost = 0.0
    if proven:
        proven_langs, proven_words = proven
        if c.get("language", "").lower() in proven_langs:
            boost += 6.0
        text = f"{c.get('title','')} {c.get('title_en','')} {c.get('niche','')}".lower()
        if any(w in text for w in proven_words):
            boost += 6.0
    c["_winner_boost"] = boost
    return base + boost


def scout(save_candidates_to: Path | None = None) -> dict:
    """
    Main entry: discover, score, and return the best GO topic.
    Ranking is weighted toward low competition + language gap.
    Raises RuntimeError if no GO topic found.
    """
    from market_intelligence import score_topic, THRESHOLD, COMPETITION_MIN, LANGUAGE_SAT_MIN, DEMAND_MIN, print_summary
    from winner_signals import get_winners, proven_sets, format_for_prompt

    # Learning loop: let books that already sold steer discovery toward adjacent topics.
    winners = get_winners()
    proven = proven_sets(winners) if winners else None
    if winners:
        print(f"[topic_scout] {len(winners)} proven winner(s) feeding discovery: "
              f"{', '.join(w['slug'] for w in winners[:6])}")

    existing_books = get_existing_books()
    print(f"[topic_scout] Discovering candidates (existing: {existing_books.count(chr(10))+1} books)...")
    candidates = discover_candidates(existing_books, format_for_prompt(winners))
    print(f"[topic_scout] Got {len(candidates)} candidates from GPT-4.1")

    from quality_gate import kindle_unsuitable

    scored = []
    for i, c in enumerate(candidates):
        c = _enrich_candidate(c)
        print(f"\n[topic_scout] Scoring {i+1}/{len(candidates)}: {c.get('title','?')} ({c.get('language','?')})")

        # Drop Kindle-incompatible formats (puzzle/coloring/journal/pattern/activity)
        # before wasting a scoring/generation cycle — Amazon rejects these as eBooks.
        # Include both native-language title/subtitle AND English fields so the
        # detector catches e.g. "Planner di Produttività" (Italian) or "Diario Guidato"
        # that wouldn't appear in the English description_en.
        unsuitable = kindle_unsuitable({
            "title": c.get("title", ""),
            "subtitle": c.get("subtitle", ""),
            "description": (
                f"{c.get('title','')} {c.get('subtitle','')} "
                f"{c.get('title_en','')} {c.get('description_en','')} {c.get('niche','')}"
            ),
            "categories": [],
            "keywords": [],
        })
        if unsuitable:
            print(f"  SKIP: not suited to Kindle format — {unsuitable}")
            c["_skipped"] = "kindle_unsuitable"
            c["_go_no_go"] = "NO-GO"
            scored.append(c)
            continue

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
            c["_opportunity_score"] = _opportunity_score(c, proven)
            comp  = result["scores"]["competition"]["score"]
            lang  = result["scores"]["language_saturation"]["score"]
            print_summary(result)
            print(f"  Opportunity score: {c['_opportunity_score']:.1f} "
                  f"(competition={comp}/20, lang_gap={lang}/10"
                  f"{', winner_boost +%.0f' % c['_winner_boost'] if c.get('_winner_boost') else ''})")
            if result["go_no_go"] == "NO-GO":
                reasons = []
                if comp < COMPETITION_MIN:
                    reasons.append(f"competition {comp}/20 < {COMPETITION_MIN}")
                if lang < LANGUAGE_SAT_MIN:
                    reasons.append(f"lang_saturation {lang}/10 < {LANGUAGE_SAT_MIN}")
                demand_s = result["scores"]["demand"]["score"]
                if demand_s < DEMAND_MIN:
                    reasons.append(f"demand {demand_s}/20 < {DEMAND_MIN} (no proven buyers)")
                if reasons:
                    print(f"  BLOCKED by hard gate: {', '.join(reasons)}")
        except Exception as e:
            print(f"  WARNING: scoring failed: {e}")
            c["_total_score"] = 0
            c["_go_no_go"] = "ERROR"
            c["_opportunity_score"] = 0

        scored.append(c)
        time.sleep(2)  # brief pause between API calls

    if save_candidates_to:
        save_candidates_to.write_text(
            json.dumps(scored, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"\n[topic_scout] Candidates saved: {save_candidates_to}")

    # Pick best GO topic — ranked by opportunity score (weights competition heavily)
    go_topics = [
        c for c in scored
        if c.get("_go_no_go") == "GO" and not c.get("_skipped")
    ]
    if not go_topics:
        raise RuntimeError(
            f"No GO topics found among {len(scored)} candidates. "
            "All failed hard gates (competition/language saturation) or total score threshold."
        )

    best = max(go_topics, key=lambda c: c.get("_opportunity_score", 0))
    print(f"\n[topic_scout] ✅ Best topic: {best['title']} ({best['language']}) "
          f"— opportunity {best.get('_opportunity_score','?'):.1f}, total {best.get('_total_score','?')}/100")
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
        "demand_timing":  c.get("demand_timing", ""),
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
