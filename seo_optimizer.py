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

from metadata_polish import sanitize_keywords, to_html_description

load_dotenv("/root/libra/.env")

KDP_DIR = Path("/root/kdp")
LIBRA_DIR = Path(__file__).parent

_GPT41_IN  = 2.0  / 1_000_000
_GPT41_OUT = 8.0  / 1_000_000
_SEARCH    = 25.0 / 1_000


def _append_step_cost(slug: str, step: str, response) -> None:
    usage = getattr(response, "usage", None)
    inp  = (getattr(usage, "input_tokens", 0) or getattr(usage, "prompt_tokens", 0)) if usage else 0
    out  = (getattr(usage, "output_tokens", 0) or getattr(usage, "completion_tokens", 0)) if usage else 0
    srch = sum(1 for item in response.output
               if getattr(item, "type", "") in ("web_search_call", "web_search_preview_call"))
    cost_usd = inp * _GPT41_IN + out * _GPT41_OUT + srch * _SEARCH
    report_path = KDP_DIR / slug / "cost-report.json"
    try:
        report = json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
    except (OSError, json.JSONDecodeError):
        report = {}
    report[step] = {"input_tokens": inp, "output_tokens": out, "web_searches": srch, "cost_usd": round(cost_usd, 4)}
    total = sum(s.get("cost_usd", 0) for s in report.values() if isinstance(s, dict) and "cost_usd" in s)
    report["total_usd"] = round(total, 4)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")

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
    "Best category path 1 — LOWEST competition (target the category whose #100 book has a weak Best Sellers Rank, so a few sales/day can win a #1 badge), still highly relevant",
    "Best category path 2 — second-lowest competition, relevant",
    "Best category path 3 — third option, relevant"
  ],
  "_categories_rule": "CRITICAL: category paths MUST use the official Amazon Kindle Store browse-category names IN ENGLISH (e.g. 'Business & Money > Accounting > Taxation', 'Health, Fitness & Dieting > Mental Health > Anxiety Disorders', 'Self-Help > Time Management'), even when the book and its keywords are in another language. The KDP category picker only offers English names — non-English category paths cannot be selected and will be discarded.",
  "_audience_consistency_rule": "CRITICAL: keep ALL 3 categories within ONE audience band — never mix. If the book is for children, every category must be under 'Children's eBooks > …'. If it is for teens/young adults, every category must be under 'Teen & Young Adult > …'. If it is for adults, none may be Children's or Teen & Young Adult. Mixing a juvenile category with an adult one (e.g. 'Self-Help > Emotions' together with 'Teen & Young Adult > …') makes KDP reject the listing as having conflicting Adult and Young Adult categories.",
  "category_book_counts": [<est. books in cat 1>, <est. books in cat 2>, <est. books in cat 3>],
  "optimized_title": "Improved title if needed (keep in {language}, return original if already good)",
  "optimized_subtitle": "Improved subtitle if needed (keep in {language})",
  "optimized_description": "Full SEO-optimized description in {language} (700-1500 chars), written for CONVERSION in this exact structure: LINE 1 = a single punchy hook sentence (no label). Then a blank line. Then 2-3 short paragraphs of benefit-driven prose (blank line between each). Then a blank line and 3-5 benefit bullet lines each starting with '- '. Then a blank line and a final one-line call-to-action. Include top buyer-intent phrases naturally. Plain text only — no HTML, no markdown headers.",
  "top_competitors": [
    {{"title": "...", "bsr": "...", "price": "...", "review_count": "..."}}
  ],
  "keyword_reasoning": "Why these 7 keywords — what buyer intent they cover",
  "category_reasoning": "Why these categories — competition level found",
  "seo_score_before": <1-10>,
  "seo_score_after": <1-10>,
  "changes_made": ["change 1", "change 2"]
}}

Title/subtitle rule (MUST follow — Amazon 2026):
- optimized title + optimized subtitle COMBINED must stay under 200 characters total

Keyword rules (MUST follow — Amazon 2026):
- Exactly 7 phrases
- Each 2-50 characters, where the 50 limit is counted in UTF-8 BYTES (accented characters cost 2 bytes — keep accented-language phrases under ~45 letters or Amazon ignores the whole slot)
- Localized buyer-intent phrases in {language} (not English unless marketplace is US/UK)
- NO Amazon program names (Kindle Unlimited, KDP Select, Prime Reading)
- NO subjective/claim words (bestseller, free, #1, top rated, new release, award winning)
- NO brand names or competitor author names
- NO commas, quotes, or punctuation inside a phrase (wastes characters)
- Do NOT just repeat the title; each phrase should add NEW search words a buyer would type
- Cover different intent angles: beginner, advanced, specific use-case, gift, workbook
- COSMO 2026 indexes intent: each phrase should map to a distinct who/when/why/use-case"""


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

    _append_step_cost(slug, "seo", response)

    raw = ""
    for item in response.output:
        if hasattr(item, "content"):
            for c in item.content:
                if hasattr(c, "text"):
                    raw += c.text

    result = _extract_json(raw)

    # Validate categories first (up to 3 now — Amazon allows 3 per format).
    cats = [str(c).strip() for c in result.get("categories", []) if str(c).strip()][:3]
    cats = cats if cats else categories
    # Snap GPT's paths onto KDP's REAL category tree. GPT names the right concepts
    # but invents the nesting/leaf (e.g. "Arts & Photography > Painting > Watercolor"
    # vs the real "Arts & Photography > Art > Painting > General"), which made books
    # ship with 0–2 of 3 categories. The resolver is deterministic and falls back to
    # the original path when a top-level isn't in the snapshot yet.
    try:
        from category_resolver import resolve_paths
        resolved = resolve_paths(cats)
        if resolved and resolved != cats:
            print(f"[seo_optimizer] categories resolved to KDP tree:\n  from {cats}\n  to   {resolved}")
        cats = resolved or cats
    except Exception as e:
        print(f"[seo_optimizer] category resolver skipped: {e}")
    result["categories"] = cats

    # Keywords: take the LLM's, pad from existing, then enforce Amazon 2026 rules
    # deterministically (drop program names / hype claims / generic / title-dupes,
    # strip punctuation). The sanitizer is the final word — GPT compliance is unverified.
    kws = [str(k).strip() for k in result.get("keywords", []) if str(k).strip()]
    for k in current_keywords:
        if k not in kws:
            kws.append(k)
    clean_kws, dropped_kws = sanitize_keywords(
        kws, title=title, subtitle=subtitle, categories=result["categories"]
    )
    result["keywords"] = clean_kws
    if dropped_kws:
        result["keywords_dropped"] = dropped_kws
        print(f"[seo_optimizer] keyword sanitizer dropped {len(dropped_kws)}: "
              + ", ".join(f"{p!r}({r})" for p, r in dropped_kws[:5]))

    # Build whitelisted-HTML description for the KDP blurb box (hook/bullets/CTA).
    plain_desc = result.get("optimized_description", "")
    if plain_desc:
        result["description_html"] = to_html_description(plain_desc)

    analysis = {
        "slug": slug,
        "optimized_at": __import__("datetime").date.today().isoformat(),
        **result,
    }

    # Save analysis
    out_path = book_dir / "seo-analysis.json"
    out_path.write_text(json.dumps(analysis, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[seo_optimizer] Analysis saved: {out_path}")

    # Update listing.json. The SEO score decides whether to ADOPT the LLM's new
    # keywords/categories/description — but the deterministic polish (keyword
    # sanitizing + HTML blurb) ALWAYS applies, so every book that passes through
    # here ships Amazon-2026-compliant metadata regardless of the score verdict.
    improved = result.get("seo_score_after", 0) > result.get("seo_score_before", 0)
    updated = listing.copy()

    if improved:
        updated["keywords"] = result["keywords"]
        if result.get("categories"):
            updated["categories"] = result["categories"]
        if result.get("optimized_description"):
            updated["description"] = result["optimized_description"]
        if result.get("optimized_subtitle") and result["optimized_subtitle"] != subtitle:
            updated["subtitle"] = result["optimized_subtitle"]
    else:
        # Keep original content, but still sanitize whatever keywords it has.
        kept_clean, _ = sanitize_keywords(
            updated.get("keywords", []), title=updated.get("title", ""),
            subtitle=updated.get("subtitle", ""), categories=updated.get("categories", []),
        )
        if kept_clean:
            updated["keywords"] = kept_clean

    # Always (re)build the HTML blurb from the final description.
    final_desc = updated.get("description", "")
    if final_desc:
        updated["description_html"] = to_html_description(final_desc)

    if dry_run:
        print("[seo_optimizer] DRY RUN — would write:")
        print(f"  Keywords ({len(updated.get('keywords', []))}): {updated.get('keywords', [])}")
        print(f"  Categories ({len(updated.get('categories', []))}): {updated.get('categories', [])}")
        print(f"  HTML blurb: {updated.get('description_html', '')[:160]}...")
        print(f"  SEO score: {result.get('seo_score_before','?')} → {result.get('seo_score_after','?')} (adopt={improved})")
    else:
        listing_path.write_text(
            json.dumps(updated, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        verdict = (f"adopted LLM SEO (score {result.get('seo_score_before','?')} → "
                   f"{result.get('seo_score_after','?')})" if improved
                   else "kept original content, applied deterministic polish")
        print(f"[seo_optimizer] listing.json updated — {verdict}")
        if improved:
            for c in result.get("changes_made", []):
                print(f"  • {c}")

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
