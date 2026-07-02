"""
market_intelligence.py — Structured market scoring for KDP topic selection.

Produces market-score.json with numeric scores and hard go/no-go threshold.
"""
import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from openai import OpenAI

LIBRA_DIR = Path(__file__).parent
KDP_DIR = LIBRA_DIR.parent / "kdp"

THRESHOLD = 65          # minimum total score out of 100 to proceed
COMPETITION_MIN = 12    # competition must score >= 12/20 (20=blue ocean, 12=moderate-low)
LANGUAGE_SAT_MIN = 5    # language_saturation must score >= 5/10 (real gap in target language)
DEMAND_MIN = 8          # demand must score >= 8/20 — BSR-anchored proof that buyers exist
                        # (added 2026-07-02: catalog of 39 LIVE books sold ~0 because gates
                        # only measured "gap", never "proven buyers"; a blue ocean with no
                        # fish is still worthless)

HIGH_RISK_KEYWORDS = [
    "medical advice", "cure", "treat", "diagnose", "prescription",
    "legal advice", "tax advice", "investment advice", "financial advice",
    "children", "kids", "minors", "ages 0", "ages 1", "ages 2",
    "weight loss", "diet pill", "supplement", "fasting extreme",
    "suicid", "self-harm", "drug", "narcotic",
    "trademark", "copyright", "official guide",
]

SCORING_PROMPT = """You are a KDP market intelligence analyst. Score this ebook topic using web search.

Topic: {title}
Language: {language}
Marketplace: {marketplace}
Niche: {niche}

Search for evidence to score each dimension below. USE WEB SEARCH to find real data.
IMPORTANT: competition and language saturation are the PRIMARY filters. A topic with high
demand but also high competition is worthless — we need demand WITH a real gap.

Searches to run:
1. "{niche}" kindle ebook amazon.{tld} — count how many books appear in results
2. "{title_en}" site:amazon.{tld} kindle — how many direct competitors?
3. "{niche}" amazon.{tld} ebook reviews — are top books well-reviewed (signals quality gap if not)?
4. "{niche}" kindle ebook {language} — books specifically in {language} (saturation check)
5. "{niche}" ebook demand 2025 2026 — buyer demand evidence
6. "{title_en}" amazon kindle bestseller rank — is there already a bestseller here?

Return ONLY valid JSON:
{{
  "demand": {{
    "score": <0-20>,
    "note": "Anchor the score on Best Sellers Rank (BSR) evidence, not vibes. Rubric for amazon.com Kindle: 16-20 = 3+ comparable books with BSR <100,000 (each selling ~1+/day); 11-15 = 3+ comparable books with BSR <300,000; 6-10 = only 1-2 books under BSR 300,000 or several in 300k-700k; 0-5 = no comparable book under BSR ~700,000 (no proven buyers — do not be generous). For smaller marketplaces (de/fr/es/it/nl/pl/co.jp) the same sales velocity sits at roughly HALF those BSR numbers — use <50k / <150k / <350k. Search the actual product pages of the closest competitors and read their BSR.",
    "evidence": "<what search found: bestseller ranks, category depth, buyer search terms>",
    "amazon_category_depth": "<how many books in the niche>",
    "top_competitor_bsr": "<bestseller rank of #1 competitor if found, else null>",
    "comparable_bsrs": [<BSR integers of comparable books found, best first>]
  }},
  "buyer_intent": {{
    "score": <0-15>,
    "evidence": "<paid search terms, 'buy' queries, review volume found>"
  }},
  "trend": {{
    "score": <0-15>,
    "direction": "<growing|stable|declining>",
    "evidence": "<trend data found: news, search volume, publication dates>"
  }},
  "competition": {{
    "score": <0-20>,
    "note": "Higher score = LOWER competition (inverse). 20=blue ocean, 0=saturated. Rubric: 17-20=<50 quality books (blue ocean), 13-16=50-200 books (low), 9-12=200-500 books (moderate), 5-8=500-2000 books (high), 0-4=>2000 books (saturated). Quality = books with 10+ reviews & 4+ stars.",
    "evidence": "<exact count of competing books found, their review counts, avg rating>",
    "estimated_competing_books": <integer or null>
  }},
  "differentiation": {{
    "score": <0-10>,
    "angle": "<unique angle available that competitors lack>",
    "evidence": "<gap in existing books found>"
  }},
  "legal_risk": {{
    "score": <0-10>,
    "note": "Higher score = LOWER risk. 10=no risk, 0=high risk.",
    "flags": [],
    "evidence": "<any regulatory, trademark, or content policy concerns found>"
  }},
  "language_saturation": {{
    "score": <0-10>,
    "note": "Higher score = LESS saturation in target language. Rubric: 9-10=0-5 quality books in {language}, 7-8=5-15 books, 5-6=15-40 books, 3-4=40-100 books, 0-2=>100 books. Search specifically for books in {language} not just translated titles.",
    "evidence": "<exact count of quality books found in {language}, titles if any>",
    "estimated_books_in_language": <integer or null>
  }},
  "expected_royalty": {{
    "estimated_price_usd": <float>,
    "estimated_monthly_units": <integer>,
    "estimated_monthly_royalty_usd": <float>,
    "evidence": "<pricing and sales velocity of comparable books>"
  }},
  "recommendation": "<proceed|reject|conditional>",
  "reasoning": "<2-3 sentence summary of why>",
  "risks": ["<risk 1>", "<risk 2>"]
}}"""


def get_existing_topics():
    """Get list of existing book slugs and titles."""
    books = []
    for d in KDP_DIR.iterdir():
        if not d.is_dir():
            continue
        listing = d / "listing.json"
        if listing.exists():
            try:
                data = json.loads(listing.read_text())
                books.append(f"{d.name}: {data.get('title', '?')} ({data.get('language', '?')})")
            except Exception:
                pass
    return books


def score_topic(topic: dict) -> dict:
    """
    Score a topic for market viability.
    topic must have: title, language, marketplace, niche
    Returns market-score dict with total_score and go_no_go.
    """
    client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

    # Extract TLD from marketplace (amazon.de → de)
    tld = topic.get("marketplace", "amazon.com").split(".")[-1]
    title_en = topic.get("title_en", topic.get("title", ""))

    prompt = SCORING_PROMPT.format(
        title=topic["title"],
        language=topic["language"],
        marketplace=topic.get("marketplace", "amazon.com"),
        niche=topic.get("niche", ""),
        title_en=title_en,
        tld=tld,
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

    # Extract JSON
    m = re.search(r'\{.*\}', raw, re.DOTALL)
    if not m:
        raise ValueError(f"No JSON in market score response: {raw[:300]}")
    data = json.loads(m.group())

    # Calculate total score
    total = (
        data["demand"]["score"]
        + data["buyer_intent"]["score"]
        + data["trend"]["score"]
        + data["competition"]["score"]
        + data["differentiation"]["score"]
        + data["legal_risk"]["score"]
        + data["language_saturation"]["score"]
    )

    # High-risk keyword check (deterministic override)
    title_lower = (topic["title"] + " " + topic.get("niche", "")).lower()
    detected_risks = [k for k in HIGH_RISK_KEYWORDS if k in title_lower]
    if detected_risks:
        data["legal_risk"]["flags"] = list(set(data["legal_risk"].get("flags", []) + detected_risks))
        data["legal_risk"]["score"] = min(data["legal_risk"]["score"], 3)
        total = sum([
            data["demand"]["score"],
            data["buyer_intent"]["score"],
            data["trend"]["score"],
            data["competition"]["score"],
            data["differentiation"]["score"],
            data["legal_risk"]["score"],
            data["language_saturation"]["score"],
        ])

    go_no_go = "GO" if total >= THRESHOLD else "NO-GO"
    if data.get("recommendation") == "reject":
        go_no_go = "NO-GO"

    # Hard gates — competition and language gap must meet minimums regardless of total score
    comp_score = data["competition"]["score"]
    lang_sat_score = data["language_saturation"]["score"]
    if comp_score < COMPETITION_MIN:
        go_no_go = "NO-GO"
        data["risks"] = list(data.get("risks", [])) + [
            f"Competition too high: score {comp_score}/20 < required {COMPETITION_MIN} "
            f"(~{data['competition'].get('estimated_competing_books','?')} quality competing books)"
        ]
    if lang_sat_score < LANGUAGE_SAT_MIN:
        go_no_go = "NO-GO"
        data["risks"] = list(data.get("risks", [])) + [
            f"Language market saturated: score {lang_sat_score}/10 < required {LANGUAGE_SAT_MIN} "
            f"(~{data['language_saturation'].get('estimated_books_in_language','?')} books already in {topic['language']})"
        ]
    demand_score = data["demand"]["score"]
    if demand_score < DEMAND_MIN:
        go_no_go = "NO-GO"
        data["risks"] = list(data.get("risks", [])) + [
            f"No proven buyers: demand score {demand_score}/20 < required {DEMAND_MIN} "
            f"(top competitor BSR: {data['demand'].get('top_competitor_bsr', '?')})"
        ]

    result = {
        "slug": topic.get("slug", ""),
        "title": topic["title"],
        "language": topic["language"],
        "marketplace": topic.get("marketplace", ""),
        "niche": topic.get("niche", ""),
        "scored_at": date.today().isoformat(),
        "scores": data,
        "total_score": total,
        "threshold": THRESHOLD,
        "go_no_go": go_no_go,
        "expected_royalty": data.get("expected_royalty", {}),
        "risks": data.get("risks", []),
        "reasoning": data.get("reasoning", ""),
    }
    return result


def score_and_save(topic: dict, output_path: Path | None = None) -> dict:
    """Score a topic and save market-score.json to book directory or output_path."""
    result = score_topic(topic)

    if output_path is None and topic.get("slug"):
        book_dir = KDP_DIR / topic["slug"]
        book_dir.mkdir(exist_ok=True)
        output_path = book_dir / "market-score.json"

    if output_path:
        output_path.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print(f"Market score saved: {output_path}")

    return result


def print_summary(result: dict):
    s = result["scores"]
    print(f"\n{'='*50}")
    print(f"Market Intelligence: {result['title']} ({result['language']})")
    print(f"{'='*50}")
    print(f"Demand:              {s['demand']['score']:>3}/20  — {s['demand']['evidence'][:60]}")
    print(f"Buyer Intent:        {s['buyer_intent']['score']:>3}/15  — {s['buyer_intent']['evidence'][:60]}")
    print(f"Trend:               {s['trend']['score']:>3}/15  — {s['trend']['direction']}: {s['trend']['evidence'][:40]}")
    print(f"Competition:         {s['competition']['score']:>3}/20  — {s['competition']['evidence'][:60]}")
    print(f"Differentiation:     {s['differentiation']['score']:>3}/10  — {s['differentiation']['angle'][:60]}")
    print(f"Legal Risk:          {s['legal_risk']['score']:>3}/10  — flags={s['legal_risk']['flags']}")
    print(f"Language Saturation: {s['language_saturation']['score']:>3}/10  — {s['language_saturation']['evidence'][:60]}")
    print(f"{'─'*50}")
    print(f"TOTAL:               {result['total_score']:>3}/100  (threshold: {result['threshold']})")
    print(f"DECISION:            {result['go_no_go']}")
    if result.get("expected_royalty"):
        r = result["expected_royalty"]
        print(f"Est. Royalty:        ${r.get('estimated_monthly_royalty_usd', '?')}/mo")
    print(f"{'='*50}")
    print(f"Reasoning: {result['reasoning']}")
    if result["risks"]:
        print(f"Risks: {'; '.join(result['risks'])}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 market_intelligence.py <slug>")
        print("       or pipe JSON topic to stdin")
        sys.exit(1)

    slug = sys.argv[1]
    book_dir = KDP_DIR / slug
    listing_file = book_dir / "listing.json"

    if listing_file.exists():
        listing = json.loads(listing_file.read_text())
        topic = {
            "slug": slug,
            "title": listing["title"],
            "language": listing["language"],
            "marketplace": listing.get("categories", [""])[0],
            "niche": listing.get("categories", ["", ""])[1] if len(listing.get("categories", [])) > 1 else "",
            "title_en": listing["title"],
        }
    else:
        print(f"No listing.json found for {slug}", file=sys.stderr)
        sys.exit(1)

    result = score_and_save(topic)
    print_summary(result)
    print(f"\nGO/NO-GO: {result['go_no_go']} ({result['total_score']}/100)")
