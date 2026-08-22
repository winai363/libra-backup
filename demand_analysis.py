#!/usr/bin/env python3
"""What our catalogue actually sold — measured, never guessed.

Every number here comes from the KDP snapshots we recorded and the listings on
disk. No model is asked for an opinion, because a model's opinion about our
demand is not evidence (that mistake is on record: buibook's "research" turned
out to be the LLM inventing sources).

Two facts shape every reading of this report:

1. KDP snapshots are *month-to-date cumulative*, so revenue per month is the
   maximum observed in that month, not the sum of daily rows.
2. We have **no traffic data**. A title earning nothing may have no demand, or
   may simply never have been seen. This module refuses to conflate the two.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import date
from pathlib import Path

LEDGER_FILE = Path(__file__).parent / "data" / "libra-business.db"
BOOKS_DIR = Path("/root/kdp")

# Tiers are deliberately capped at "moderate": the whole catalogue has earned
# tens of dollars, which can never support a "strong"/"proven" claim.
WEAK_KENP = 1
MODERATE_KENP = 100
MODERATE_ROYALTIES = 1.0

# A niche needs this many titles before repeated zeroes mean anything.
SCALE_THRESHOLD = 3

# Amazon category names come back localised per marketplace ("Informatique et
# Internet", "Negocios y dinero", "本"), which shatters any grouping by
# category. Themes are matched on the slug instead: an explicit, auditable
# keyword table — first match wins, order matters, and every row records which
# keyword matched so a human can check the classification.
THEME_RULES = (
    ("senior_tech", ("senior",)),
    ("adhd", ("adhd",)),
    ("anxiety_mental_health", ("anxiety", "stress", "postpartum", "stoicism", "wellness", "sleep")),
    ("tax_accounting_spain", ("tax", "iva", "modelo-130", "autonomos", "deducciones", "fiscal",
                              "contabilidad", "financiera")),
    ("art_craft", ("watercolor", "acuarela", "creative-workbook")),
    ("kids_language", ("bilingual", "kids", "vocab")),
    ("health_food", ("gut-health", "meal-plan", "mocktails", "sober")),
    ("home_diy", ("dripping-tap", "home-repair")),
    ("fiction", ("romantasy", "romance")),
    ("ai_productivity", ("ai-", "prompt", "productivity", "workflow")),
)
UNCLASSIFIED = "unclassified"


def theme_for_slug(slug: str) -> tuple:
    """Return (theme, matched_keyword). Deterministic and inspectable."""
    lowered = slug.lower()
    for theme, keywords in THEME_RULES:
        for keyword in keywords:
            if keyword in lowered:
                return theme, keyword
    return UNCLASSIFIED, None


def evidence_strength(*, royalties: float, kenp: int) -> str:
    if royalties >= MODERATE_ROYALTIES or kenp >= MODERATE_KENP:
        return "moderate"
    if royalties > 0 or kenp >= WEAK_KENP:
        return "weak"
    return "no_signal"


def _monthly_totals(ledger_path: Path) -> dict:
    """{asin: {month: {royalties_usd, orders, kenp}}} using month maxima."""
    totals: dict = defaultdict(dict)
    if not Path(ledger_path).exists():
        return totals
    with sqlite3.connect(ledger_path) as connection:
        rows = connection.execute(
            "SELECT a.asin, s.month, MAX(a.royalties_usd), MAX(a.orders_count), MAX(a.kenp)"
            " FROM kdp_title_attribution a"
            " JOIN kdp_snapshots s ON s.id = a.snapshot_id"
            " GROUP BY a.asin, s.month"
        ).fetchall()
    for asin, month, royalties, orders, kenp in rows:
        totals[asin][month] = {
            "royalties_usd": round(float(royalties or 0), 2),
            "orders": int(orders or 0),
            "kenp": int(kenp or 0),
        }
    return totals


def _age_days(published: str, today: str) -> int | None:
    try:
        return (date.fromisoformat(today) - date.fromisoformat(published[:10])).days
    except (TypeError, ValueError):
        return None


def _category_root(categories) -> str:
    if not isinstance(categories, list) or not categories:
        return "uncategorised"
    return str(categories[0]).split(">")[0].strip() or "uncategorised"


def title_performance(ledger_path: Path, books_dir: Path, *, today: str) -> list:
    """One row per catalogue title, sales attached where we have them."""
    monthly = _monthly_totals(Path(ledger_path))
    rows = []
    for listing_file in sorted(Path(books_dir).glob("*/listing.json")):
        try:
            listing = json.loads(listing_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        asin = listing.get("asin")
        months = monthly.get(asin, {})
        royalties = round(sum(m["royalties_usd"] for m in months.values()), 2)
        kenp = sum(m["kenp"] for m in months.values())
        orders = sum(m["orders"] for m in months.values())
        published = (
            listing.get("publish_submission_confirmed_at")
            or listing.get("uploaded_at")
            or listing.get("created_at")
            or ""
        )
        slug = listing_file.parent.name
        theme, matched = theme_for_slug(slug)
        rows.append({
            "slug": slug,
            "theme": theme,
            "theme_matched_on": matched,
            "asin": asin,
            "language": listing.get("language"),
            "category_root": _category_root(listing.get("categories")),
            "categories": listing.get("categories") or [],
            "live_status": str(listing.get("live_status") or "UNKNOWN").upper(),
            "age_days": _age_days(published, today),
            "royalties_usd": royalties,
            "orders": orders,
            "kenp": kenp,
            "months": months,
            "evidence": evidence_strength(royalties=royalties, kenp=kenp),
        })
    return rows


def demand_clusters(performance: list) -> list:
    """Group measured performance by product theme.

    Theme is the unit because that is where our one statistically meaningful
    pattern lives: a whole niche repeated across many languages, all at zero.
    The per-language split is kept inside each cluster, never lost.
    """
    grouped: dict = defaultdict(list)
    for row in performance:
        grouped[row["theme"]].append(row)

    clusters = []
    for theme, rows in grouped.items():
        royalties = round(sum(r["royalties_usd"] for r in rows), 2)
        kenp = sum(r["kenp"] for r in rows)
        earning = [r for r in rows if r["evidence"] != "no_signal"]
        blocked = [r for r in rows if r["live_status"] == "BLOCKED"]
        live_earning = [r for r in earning if r["live_status"] != "BLOCKED"]
        if earning:
            verdict = "signal_present"
        elif len(rows) >= SCALE_THRESHOLD:
            verdict = "no_signal_at_scale"
        else:
            verdict = "insufficient_data"

        languages: dict = {}
        for row in rows:
            entry = languages.setdefault(
                str(row["language"]), {"titles": 0, "royalties_usd": 0.0, "kenp": 0, "slugs": []}
            )
            entry["titles"] += 1
            entry["royalties_usd"] = round(entry["royalties_usd"] + row["royalties_usd"], 2)
            entry["kenp"] += row["kenp"]
            entry["slugs"].append(row["slug"])

        # A niche whose only evidence comes from a blocked title must not be
        # reused on this account: the block is about the account, not demand.
        safe = not (blocked and not live_earning)
        clusters.append({
            "theme": theme,
            "categories": sorted({r["category_root"] for r in rows}),
            "titles": len(rows),
            "live_titles": sum(1 for r in rows if r["live_status"] == "LIVE"),
            "sample_size": len(rows),
            "earning_titles": len(earning),
            "blocked_titles": len(blocked),
            "royalties_usd": royalties,
            "kenp": kenp,
            "verdict": verdict,
            # Our sample is tens of dollars across weeks — confidence never rises.
            "confidence": "low",
            "safe_to_reuse": safe,
            "blocked_reason": None if safe else "only_evidence_is_a_blocked_title",
            "languages": languages,
            "slugs": sorted(r["slug"] for r in rows),
        })
    return sorted(clusters, key=lambda c: (-c["royalties_usd"], -c["kenp"], c["theme"]))


def demand_report(ledger_path: Path, books_dir: Path, *, today: str) -> dict:
    performance = title_performance(ledger_path, books_dir, today=today)
    clusters = demand_clusters(performance)
    signal_titles = [row for row in performance if row["evidence"] != "no_signal"]
    return {
        "generated_for": today,
        "totals": {
            "titles": len(performance),
            "titles_with_any_signal": len(signal_titles),
            "royalties_usd": round(sum(r["royalties_usd"] for r in performance), 2),
            "kenp": sum(r["kenp"] for r in performance),
            "months_observed": sorted({m for r in performance for m in r["months"]}),
        },
        "caveats": {
            "traffic_data": "absent",
            "interpretation": (
                "zero revenue does not prove absent demand — with no impression or "
                "click data we cannot tell an unwanted book from an unseen one"
            ),
            "sample": "tens of dollars over weeks; every verdict is directional only",
            "snapshot_semantics": "month-to-date cumulative; monthly maxima used",
        },
        "clusters": clusters,
        "titles": sorted(performance, key=lambda r: (-r["royalties_usd"], -r["kenp"], r["slug"])),
    }


# Themes where the reader is being taught to DO something. A text-only book
# here is what Amazon calls a "disappointing customer experience" — that is
# exactly how acuarela was rejected and lost (11 Jul 2026).
VISUAL_THEMES = frozenset({"senior_tech", "art_craft", "kids_language", "home_diy"})

# Permanently closed regardless of measured revenue: the diet/meal-plan niche
# cost this account a content block.
NO_GO_THEMES = frozenset({"health_food"})
NO_GO_SLUG_MARKERS = ("meal-plan", "diet", "high-protein")


def product_opportunities(report: dict) -> list:
    """Themes worth a new product, ranked by revenue per LIVE title.

    Raw revenue rewards volume; revenue per live title asks the question that
    matters — did each book we shipped pull its weight?
    """
    opportunities = []
    for cluster in report["clusters"]:
        if cluster["verdict"] != "signal_present" or not cluster["safe_to_reuse"]:
            continue
        if cluster["theme"] in NO_GO_THEMES or cluster["theme"] == UNCLASSIFIED:
            continue
        earners = [
            row for row in report["titles"]
            if row["theme"] == cluster["theme"] and row["evidence"] != "no_signal"
        ]
        if any(marker in row["slug"] for row in earners for marker in NO_GO_SLUG_MARKERS):
            continue
        live = max(cluster["live_titles"], 1)
        visual = cluster["theme"] in VISUAL_THEMES
        must_have = []
        if visual:
            must_have.append(
                "real instructional images with provenance — a how-to book without "
                "them is the exact 'disappointing customer experience' rejection"
            )
        opportunities.append({
            "theme": cluster["theme"],
            "royalties_usd": cluster["royalties_usd"],
            "live_titles": cluster["live_titles"],
            "royalties_per_live_title": round(cluster["royalties_usd"] / live, 2),
            "kenp": cluster["kenp"],
            "visual_required": visual,
            "must_have": must_have,
            "languages_with_signal": sorted(
                language for language, stats in cluster["languages"].items()
                if stats["royalties_usd"] > 0 or stats["kenp"] > 0
            ),
            "confidence": "low",
            "evidence": {
                "titles": [
                    {
                        "slug": row["slug"],
                        "asin": row["asin"],
                        "royalties_usd": row["royalties_usd"],
                        "kenp": row["kenp"],
                        "live_status": row["live_status"],
                    }
                    for row in sorted(earners, key=lambda r: -r["royalties_usd"])
                ],
                "months_observed": report["totals"]["months_observed"],
                "caveat": report["caveats"]["sample"],
            },
        })
    return sorted(
        opportunities,
        key=lambda o: (-o["royalties_per_live_title"], -o["kenp"], o["theme"]),
    )


def themes_to_avoid(report: dict) -> list:
    """Where we already spent titles and measured nothing back."""
    return [
        {
            "theme": cluster["theme"],
            "titles_spent": cluster["titles"],
            "live_titles": cluster["live_titles"],
            "royalties_usd": cluster["royalties_usd"],
            "reason": cluster["verdict"],
            "slugs": cluster["slugs"],
        }
        for cluster in report["clusters"]
        if cluster["verdict"] == "no_signal_at_scale" and cluster["theme"] != UNCLASSIFIED
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description="Measured KDP demand report (read-only)")
    parser.add_argument("--ledger", default=str(LEDGER_FILE))
    parser.add_argument("--books", default=str(BOOKS_DIR))
    parser.add_argument("--today", default=date.today().isoformat())
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    report = demand_report(Path(args.ledger), Path(args.books), today=args.today)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    totals = report["totals"]
    print(f"Measured demand — {report['generated_for']} "
          f"({', '.join(totals['months_observed']) or 'no data'})")
    print(f"  titles: {totals['titles']}  with any signal: {totals['titles_with_any_signal']}"
          f"  royalties: ${totals['royalties_usd']:.2f}  KENP: {totals['kenp']}")
    print(f"  caveat: {report['caveats']['interpretation']}")
    print()
    print(f"{'theme':24} {'n':>3} {'live':>4} {'earn':>4} {'$':>7} {'KENP':>6}  verdict")
    for cluster in report["clusters"]:
        flag = "" if cluster["safe_to_reuse"] else "  [blocked-niche]"
        print(f"{cluster['theme'][:24]:24} {cluster['titles']:3} {cluster['live_titles']:4} "
              f"{cluster['earning_titles']:4} {cluster['royalties_usd']:7.2f} "
              f"{cluster['kenp']:6}  {cluster['verdict']}{flag}")

    print("\nWHERE A NEW PRODUCT HAS MEASURED SUPPORT (per live title, best first)")
    opportunities = product_opportunities(report)
    if not opportunities:
        print("  none — no theme has a safe, non-blocked earning title")
    for opportunity in opportunities:
        visual = " visual-required" if opportunity["visual_required"] else ""
        print(f"  {opportunity['theme']:24} ${opportunity['royalties_per_live_title']:6.2f}/live title"
              f"  ({opportunity['live_titles']} live, ${opportunity['royalties_usd']:.2f} total,"
              f" {opportunity['kenp']} KENP){visual}")
        for title in opportunity["evidence"]["titles"]:
            mark = "" if title["live_status"] == "LIVE" else f"  [{title['live_status']}]"
            print(f"      ← {title['slug']} ({title['asin']}): "
                  f"${title['royalties_usd']:.2f}, {title['kenp']} KENP{mark}")

    print("\nWHERE WE ALREADY SPENT TITLES AND MEASURED NOTHING")
    for avoid in themes_to_avoid(report):
        print(f"  {avoid['theme']:24} {avoid['titles_spent']:2} titles "
              f"({avoid['live_titles']} live) → ${avoid['royalties_usd']:.2f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
