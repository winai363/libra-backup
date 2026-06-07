#!/usr/bin/env python3
"""
pricing_engine.py — Smart pricing strategy by marketplace, genre, and competition.

Analyzes competitor prices for the target niche/marketplace and recommends
an optimal price. Saves pricing-recommendation.json to the book directory.

Usage:
    python3 pricing_engine.py <slug>
"""

import json
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

load_dotenv("/root/libra/.env")

KDP_DIR = Path("/root/kdp")

# Royalty tier breakpoints (Amazon KDP rules)
ROYALTY_35_BELOW = 2.98   # below $2.99 → 35% royalty
ROYALTY_70_MIN   = 2.99   # $2.99–$9.99 → 70% royalty
ROYALTY_70_MAX   = 9.99
ROYALTY_35_ABOVE = 10.00  # above $9.99 → 35% royalty

# Default fallback prices by marketplace (USD equivalent)
MARKETPLACE_DEFAULTS = {
    "amazon.de":    3.99,
    "amazon.fr":    3.99,
    "amazon.es":    3.49,
    "amazon.it":    3.49,
    "amazon.nl":    3.49,
    "amazon.pl":    2.99,
    "amazon.co.uk": 2.99,
    "amazon.co.jp": 2.99,
    "amazon.com":   2.99,
}

PRICING_PROMPT = """You are a KDP pricing analyst. Research the optimal price for this ebook.

Book: {title}
Language: {language}
Marketplace: {marketplace}
Niche: {niche}
Page count estimate: {pages}

SEARCH TASKS:
1. Search "{title_en}" amazon.{tld} kindle — find similar books and their prices
2. Search "{niche_en}" kindle ebook amazon.{tld} — find top 10 books and prices
3. Note: kindle ebook pricing in {marketplace}, what do books in this niche typically cost?
4. Search "{niche_en}" kindle ebook bestseller price — what price point converts best?

PRICING ANALYSIS:
- Collect prices of 5-10 competing books (as exact as possible)
- Identify the price distribution (low/median/high)
- Consider: {pages} pages is a {size_label} book
- Consider marketplace purchasing power ({marketplace})
- Consider KDP royalty tiers: 35% below $2.99, 70% at $2.99-$9.99, 35% above $9.99
- The sweet spot for new authors is usually just below the median competitor price

Return ONLY valid JSON:
{{
  "competitor_prices": [
    {{"title": "...", "price_local": "€X.XX or £X.XX", "price_usd_approx": X.XX}}
  ],
  "price_analysis": {{
    "median_usd": X.XX,
    "low_usd": X.XX,
    "high_usd": X.XX,
    "most_common_price_usd": X.XX
  }},
  "recommended_price_usd": X.XX,
  "recommended_price_local": "X.XX",
  "local_currency": "EUR|GBP|USD|JPY|PLN",
  "royalty_tier": "70%|35%",
  "estimated_monthly_royalty_usd": X.XX,
  "launch_price_usd": X.XX,
  "launch_price_local": "X.XX",
  "launch_duration_days": 7,
  "reasoning": "2-3 sentence explanation of the pricing strategy"
}}

Note: launch_price should be 20-30% below recommended_price for the first 7 days (promotional).
If recommended_price is already at minimum ($2.99), keep launch_price the same."""


def _extract_json(text: str) -> dict:
    start = text.find("{")
    if start < 0:
        raise ValueError("No JSON in pricing response")
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
                return json.loads(text[start:i+1])
    raise ValueError("Incomplete JSON in pricing response")


def _royalty(price_usd: float, tier: str = None) -> float:
    """Estimate monthly royalty for a given price (assumes 10 units/month baseline)."""
    if price_usd < ROYALTY_70_MIN or price_usd > ROYALTY_70_MAX:
        rate = 0.35
    else:
        rate = 0.70
    # Rough estimate: 10 units/month for a new book
    return round(price_usd * rate * 10, 2)


def _clamp_price(price: float) -> float:
    """Clamp to a valid KDP price that lands in the 70% royalty tier."""
    price = round(price, 2)
    if price < ROYALTY_70_MIN:
        price = ROYALTY_70_MIN
    if price > ROYALTY_70_MAX:
        price = ROYALTY_70_MAX
    return price


def analyze(slug: str) -> dict:
    """Research competitor prices and return pricing recommendation."""
    book_dir = KDP_DIR / slug
    listing_path = book_dir / "listing.json"
    if not listing_path.exists():
        raise FileNotFoundError(f"No listing.json for {slug}")

    listing = json.loads(listing_path.read_text(encoding="utf-8"))
    title = listing.get("title", "")
    language = listing.get("language", "English")
    categories = listing.get("categories", [])
    niche = categories[1] if len(categories) > 1 else (categories[0] if categories else "")
    marketplace = listing.get("marketplace", "amazon.com")
    tld = marketplace.split(".")[-1] if "." in marketplace else "com"

    # Estimate pages from EPUB/PDF if available
    pages = 40
    pdf = book_dir / "ebook.pdf"
    if pdf.exists():
        try:
            import subprocess
            out = subprocess.check_output(
                ["pdfinfo", str(pdf)], text=True, stderr=subprocess.DEVNULL
            )
            m = re.search(r'Pages:\s*(\d+)', out)
            if m:
                pages = int(m.group(1))
        except Exception:
            pass

    size_label = "short" if pages < 50 else "medium" if pages < 100 else "long"
    title_en = re.sub(r'[-_]', ' ', slug)
    niche_en = niche if niche else title_en

    prompt = PRICING_PROMPT.format(
        title=title,
        language=language,
        marketplace=marketplace,
        niche=niche,
        pages=pages,
        size_label=size_label,
        tld=tld,
        title_en=title_en,
        niche_en=niche_en,
    )

    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.responses.create(
        model="gpt-4.1",
        tools=[{"type": "web_search_preview"}],
        input=[{"role": "user", "content": prompt}],
        max_output_tokens=3000,
    )

    raw = ""
    for item in response.output:
        if hasattr(item, "content"):
            for c in item.content:
                if hasattr(c, "text"):
                    raw += c.text

    result = _extract_json(raw)

    # Validate & clamp prices
    rec = _clamp_price(float(result.get("recommended_price_usd") or MARKETPLACE_DEFAULTS.get(marketplace, 2.99)))
    launch = float(result.get("launch_price_usd") or rec)
    launch = _clamp_price(max(launch, ROYALTY_70_MIN))
    result["recommended_price_usd"] = rec
    result["launch_price_usd"] = launch
    result["royalty_tier"] = "70%" if ROYALTY_70_MIN <= rec <= ROYALTY_70_MAX else "35%"
    result["estimated_monthly_royalty_usd"] = _royalty(rec)

    analysis = {
        "slug": slug,
        "marketplace": marketplace,
        "analyzed_at": __import__("datetime").date.today().isoformat(),
        "pages": pages,
        **result,
    }

    out_path = book_dir / "pricing-recommendation.json"
    out_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[pricing_engine] Recommendation saved: {out_path}")
    return analysis


def print_report(r: dict):
    print(f"\n{'='*50}")
    print(f"Pricing: {r['slug']} ({r['marketplace']})")
    print(f"{'='*50}")
    pa = r.get("price_analysis", {})
    print(f"Competitor range: ${pa.get('low_usd','?')} – ${pa.get('high_usd','?')} "
          f"(median ${pa.get('median_usd','?')})")
    print(f"Recommended:  ${r['recommended_price_usd']} ({r.get('local_currency','USD')} "
          f"{r.get('recommended_price_local','?')}) — {r['royalty_tier']} royalty")
    print(f"Launch price: ${r['launch_price_usd']} for {r.get('launch_duration_days', 7)} days")
    print(f"Est. royalty: ${r['estimated_monthly_royalty_usd']}/mo (10 units)")
    print(f"Reasoning: {r.get('reasoning','')}")
    print(f"{'='*50}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 pricing_engine.py <slug>")
        sys.exit(1)
    slug = sys.argv[1]
    try:
        result = analyze(slug)
        print_report(result)
        sys.exit(0)
    except Exception as e:
        print(f"[pricing_engine] ERROR: {e}", file=sys.stderr)
        sys.exit(1)
