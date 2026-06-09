#!/usr/bin/env python3
"""
seo_optimizer.py — Competitive keyword & category research for KDP listings.

Searches real competing books, extracts proven buyer-intent keywords,
finds low-competition categories, and upgrades the book's listing.json.

Usage:
    python3 seo_optimizer.py <slug>          # optimize and update listing.json
    python3 seo_optimizer.py <slug> --dry-run  # show changes, don't write
"""

import json
import os
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI, RateLimitError

load_dotenv("/root/libra/.env")

KDP_DIR = Path("/root/kdp")
LIBRA_DIR = Path(__file__).parent

SEO_PROMPT = """You are a KDP SEO specialist. Analyze competing books and optimize this listing.

BOOK TO OPTIMIZE:
Title: {title}
Subtitle: {subtitle}
Language: {language}
Marketplace: {marketplace}
Niche: {niche}
Current keywords: {current_keywords}
Current description (first 300 chars): {description_preview}

RESEARCH TASKS — use web search:
1. Search "{niche_en}" kindle ebook amazon.{tld} — count how many quality books (10+ reviews) appear
2. Search "{title_en}" site:amazon.{tld} kindle — find direct competitors and their review counts
3. Search "{niche_en}" kindle ebook {language} — how many written in {language} specifically?
4. Search "amazon kindle categories {niche_en}" site:kdp.amazon.com OR site:amazon.{tld} — find sub-categories
5. For each sub-category found: estimate how many books it has (smaller = easier to rank)
6. Search buyer phrases: how do {language}-speaking readers search for this topic? What exact phrases?
7. Check top 5 competitors: what keywords do their titles/subtitles use?

After research, provide a full SEO upgrade:

Return ONLY valid JSON:
{{
  "keywords": [
    "keyword phrase 1",
    "keyword phrase 2",
    "keyword phrase 3",
    "keyword phrase 4",
    "keyword phrase 5",
    "keyword phrase 6",
    "keyword phrase 7"
  ],
  "categories": [
    "Best category path 1 — lowest book count, still highly relevant",
    "Best category path 2 — second best option"
  ],
  "category_book_counts": [<estimated books in category 1>, <estimated books in category 2>],
  "optimized_title": "Improved title if needed (keep in {language}, return original if already good)",
  "optimized_subtitle": "Improved subtitle if needed (keep in {language})",
  "optimized_description": "Full SEO-optimized description in {language} (700-1500 chars). Must include top buyer-intent phrases naturally. Use short paragraphs.",
  "top_competitors": [
    {{"title": "...", "bsr": "...", "price": "...", "review_count": "..."}}
  ],
  "keyword_reasoning": "Why these 7 keywords — what buyer intent they cover",
  "category_reasoning": "Why these categories — competition level found",
  "seo_score_before": <1-10>,
  "seo_score_after": <1-10>,
  "changes_made": ["change 1", "change 2"]
}}

Keyword rules (MUST follow):
- Exactly 7 phrases
- Each 2-50 characters
- Localized buyer-intent phrases in {language} (not English unless marketplace is US/UK)
- No brand names or competitor author names
- Cover different intent angles: beginner, advanced, specific use-case, gift, workbook
- Must NOT repeat the exact title verbatim"""


def _extract_json(text: str) -> dict:
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON in SEO response")
    depth, in_string, escaped = 0, False, False
    for i in range(start, len(text)):
        ch = text[i]
        if in_string:
            escaped = not escaped and ch == "\\"
            if not escaped and ch == '"':
                in_string = False
        elif ch == '"':
            in_string = True
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                blob = text[start:i+1]
                try:
                    return json.loads(blob)
                except json.JSONDecodeError:
                    # GPT often emits trailing commas before } or ] — strip them
                    # and retry rather than discarding the whole optimization.
                    cleaned = re.sub(r",(\s*[}\]])", r"\1", blob)
                    return json.loads(cleaned)
    raise ValueError("Incomplete JSON in SEO response")


def optimize(slug: str, dry_run: bool = False) -> dict:
    """Run SEO optimization for a book. Returns the seo-analysis dict."""
    book_dir = KDP_DIR / slug
    listing_path = book_dir / "listing.json"
    if not listing_path.exists():
        raise FileNotFoundError(f"No listing.json for {slug}")

    listing = json.loads(listing_path.read_text(encoding="utf-8"))
    title = listing.get("title", "")
    subtitle = listing.get("subtitle", "")
    language = listing.get("language", "English")
    current_keywords = listing.get("keywords", [])
    description = listing.get("description", "")
    categories = listing.get("categories", [])
    niche = categories[1] if len(categories) > 1 else categories[0] if categories else ""
    marketplace = listing.get("marketplace", "amazon.com")
    tld = marketplace.split(".")[-1] if "." in marketplace else "com"
    # title_en: for non-English books, use slug as proxy for niche
    title_en = re.sub(r'[-_]', ' ', slug)
    niche_en = niche if niche else title_en

    prompt = SEO_PROMPT.format(
        title=title,
        subtitle=subtitle,
        language=language,
        marketplace=marketplace,
        niche=niche,
        current_keywords=", ".join(current_keywords),
        description_preview=description[:300],
        tld=tld,
        title_en=title_en,
        niche_en=niche_en,
    )

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = None
    for attempt in range(4):
        try:
            response = client.responses.create(
                model="gpt-4.1",
                tools=[{"type": "web_search_preview"}],
                input=[{"role": "user", "content": prompt}],
                max_output_tokens=5000,
            )
            break
        except RateLimitError:
            if attempt == 3:
                raise
            print(f"  Rate limit hit, waiting 65s (attempt {attempt+1})...")
            time.sleep(65)

    if response is None:
        raise RuntimeError("SEO optimizer got no response")

    raw = ""
    for item in response.output:
        if hasattr(item, "content"):
            for c in item.content:
                if hasattr(c, "text"):
                    raw += c.text

    result = _extract_json(raw)

    # Validate & clean keywords (2-50 chars, exactly 7)
    kws = [str(k).strip() for k in result.get("keywords", []) if str(k).strip()]
    kws = [k for k in kws if 2 <= len(k) <= 50][:7]
    if len(kws) < 7:
        # Pad with existing keywords if needed
        for k in current_keywords:
            if k not in kws and 2 <= len(k) <= 50:
                kws.append(k)
            if len(kws) == 7:
                break
    result["keywords"] = kws

    # Validate categories
    cats = [str(c).strip() for c in result.get("categories", []) if str(c).strip()][:2]
    result["categories"] = cats if cats else categories

    analysis = {
        "slug": slug,
        "optimized_at": __import__("datetime").date.today().isoformat(),
        **result,
    }

    # Save analysis
    out_path = book_dir / "seo-analysis.json"
    out_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[seo_optimizer] Analysis saved: {out_path}")

    # Update listing.json
    if not dry_run and result.get("seo_score_after", 0) > result.get("seo_score_before", 0):
        updated = listing.copy()
        updated["keywords"] = result["keywords"]
        if result.get("categories"):
            updated["categories"] = result["categories"]
        if result.get("optimized_description"):
            updated["description"] = result["optimized_description"]
        if result.get("optimized_subtitle") and result["optimized_subtitle"] != subtitle:
            updated["subtitle"] = result["optimized_subtitle"]
        listing_path.write_text(
            json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"[seo_optimizer] listing.json updated (SEO score {result['seo_score_before']} → {result['seo_score_after']})")
        changes = result.get("changes_made", [])
        for c in changes:
            print(f"  • {c}")
    elif dry_run:
        print(f"[seo_optimizer] DRY RUN — would update listing.json:")
        print(f"  Keywords: {result['keywords']}")
        print(f"  Categories: {result.get('categories', [])}")
        print(f"  SEO score: {result.get('seo_score_before','?')} → {result.get('seo_score_after','?')}")
    else:
        print(f"[seo_optimizer] No improvement detected (score {result.get('seo_score_before','?')} → {result.get('seo_score_after','?')}), keeping original")

    return analysis


def print_report(analysis: dict):
    print(f"\n{'='*55}")
    print(f"SEO Report: {analysis['slug']}")
    print(f"{'='*55}")
    print(f"Score before → after: {analysis.get('seo_score_before','?')} → {analysis.get('seo_score_after','?')}")
    print(f"Keywords: {', '.join(analysis.get('keywords', []))}")
    print(f"Categories: {analysis.get('categories', [])}")
    if analysis.get("top_competitors"):
        print("Top competitors:")
        for c in analysis["top_competitors"][:3]:
            print(f"  • {c.get('title','?')} — BSR {c.get('bsr','?')} @ {c.get('price','?')}")
    print(f"Keyword reasoning: {analysis.get('keyword_reasoning','')[:120]}")
    print(f"{'='*55}")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("slug")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    try:
        result = optimize(args.slug, dry_run=args.dry_run)
        print_report(result)
        sys.exit(0)
    except Exception as e:
        print(f"[seo_optimizer] ERROR: {e}", file=sys.stderr)
        sys.exit(1)
