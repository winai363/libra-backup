"""
feedback_loop.py — Post-publication feedback tracking for KDP books.

Tracks: impressions, rank, sales (units), KENP reads, reviews.
Runs weekly. Stores snapshots in kdp/<slug>/feedback-history.json.
Generates 7-day and 30-day review reports.
"""
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

LIBRA_DIR = Path(__file__).parent
KDP_DIR = LIBRA_DIR.parent / "kdp"

# KDP Performance thresholds for automated action triggers
THRESHOLDS = {
    "min_impressions_7d": 100,       # if below, flag for keyword/category review
    "min_units_30d": 5,              # if below after 30 days, flag for pricing review
    "min_kenp_30d": 500,             # Kindle Unlimited reads target per month
    "rank_warning_threshold": 500000, # BSR above this = very low visibility
    "review_flag_threshold": 3.5,    # average rating below this triggers attention
}


def load_history(slug: str) -> list:
    """Load feedback history for a book."""
    path = KDP_DIR / slug / "feedback-history.json"
    if path.exists():
        return json.loads(path.read_text())
    return []


def save_history(slug: str, history: list):
    """Save feedback history."""
    path = KDP_DIR / slug / "feedback-history.json"
    path.write_text(json.dumps(history, indent=2, ensure_ascii=False))


def record_snapshot(slug: str, snapshot: dict) -> dict:
    """
    Add a new data snapshot to feedback history.
    snapshot should include: date, bsr, units_7d, kenp_7d, reviews_count, avg_rating
    Returns the updated snapshot with computed deltas.
    """
    history = load_history(slug)
    snapshot["date"] = snapshot.get("date", date.today().isoformat())

    # Compute deltas vs previous snapshot
    if history:
        prev = history[-1]
        snapshot["delta_bsr"] = snapshot.get("bsr", 0) - prev.get("bsr", 0)
        snapshot["delta_units"] = snapshot.get("units_7d", 0) - prev.get("units_7d", 0)
        snapshot["delta_kenp"] = snapshot.get("kenp_7d", 0) - prev.get("kenp_7d", 0)
        snapshot["delta_reviews"] = snapshot.get("reviews_count", 0) - prev.get("reviews_count", 0)
    else:
        snapshot["delta_bsr"] = 0
        snapshot["delta_units"] = 0
        snapshot["delta_kenp"] = 0
        snapshot["delta_reviews"] = 0

    history.append(snapshot)
    save_history(slug, history)
    return snapshot


def analyze(slug: str) -> dict:
    """
    Analyze feedback history and generate recommendations.
    Returns analysis dict with flags and recommended actions.
    """
    history = load_history(slug)
    if not history:
        return {"slug": slug, "status": "no_data", "flags": [], "recommendations": []}

    today = date.today()
    flags = []
    recommendations = []

    # Get recent snapshots
    recent_7d = [s for s in history if (today - date.fromisoformat(s["date"])).days <= 7]
    recent_30d = [s for s in history if (today - date.fromisoformat(s["date"])).days <= 30]
    latest = history[-1]

    # BSR check
    bsr = latest.get("bsr", 0)
    if bsr > THRESHOLDS["rank_warning_threshold"]:
        flags.append(f"LOW_VISIBILITY: BSR {bsr:,} > {THRESHOLDS['rank_warning_threshold']:,}")
        recommendations.append("Review keywords and categories — book has very low visibility")

    # 7-day impressions
    total_impressions_7d = sum(s.get("impressions_7d", 0) for s in recent_7d)
    if total_impressions_7d < THRESHOLDS["min_impressions_7d"] and len(recent_7d) > 0:
        flags.append(f"LOW_IMPRESSIONS: {total_impressions_7d} impressions in 7 days")
        recommendations.append("Consider A/B testing title/subtitle or adding backend keywords")

    # 30-day sales
    total_units_30d = sum(s.get("units_7d", 0) for s in recent_30d)
    pub_date = history[0]["date"]
    days_published = (today - date.fromisoformat(pub_date)).days
    if days_published >= 30 and total_units_30d < THRESHOLDS["min_units_30d"]:
        flags.append(f"LOW_SALES: {total_units_30d} units in 30 days after {days_published} days live")
        recommendations.append("Evaluate price point — consider temporary discount or KDP Select enrollment")

    # KENP reads
    total_kenp_30d = sum(s.get("kenp_7d", 0) for s in recent_30d)
    if days_published >= 30 and total_kenp_30d < THRESHOLDS["min_kenp_30d"]:
        flags.append(f"LOW_KENP: {total_kenp_30d} KENP reads in 30 days")
        recommendations.append("Consider running a KDP countdown deal to boost Kindle Unlimited enrollment")

    # Review rating
    avg_rating = latest.get("avg_rating", 0)
    if avg_rating > 0 and avg_rating < THRESHOLDS["review_flag_threshold"]:
        flags.append(f"LOW_RATING: {avg_rating:.1f} stars")
        recommendations.append("Review reader comments — editorial/content quality may need addressing")

    # BSR trend (improving or declining)
    if len(history) >= 3:
        bsr_trend = [s.get("bsr", 0) for s in history[-3:]]
        if all(b > 0 for b in bsr_trend):
            if bsr_trend[-1] > bsr_trend[0] * 1.5:
                flags.append("BSR_DECLINING: rank worsening over last 3 snapshots")
                recommendations.append("Consider running an ad campaign or updating metadata")
            elif bsr_trend[-1] < bsr_trend[0] * 0.7:
                flags.append("BSR_IMPROVING: rank improving — organic momentum")

    return {
        "slug": slug,
        "analyzed_at": today.isoformat(),
        "days_published": days_published,
        "latest_snapshot": latest,
        "totals_30d": {
            "units": total_units_30d,
            "kenp": total_kenp_30d,
            "impressions": total_impressions_7d,
        },
        "flags": flags,
        "recommendations": recommendations,
        "needs_attention": len([f for f in flags if "IMPROVING" not in f]) > 0,
    }


def weekly_review_all() -> list:
    """Run feedback analysis for all published books. Returns list of analyses."""
    results = []
    for d in sorted(KDP_DIR.iterdir()):
        if not d.is_dir():
            continue
        listing_path = d / "listing.json"
        if not listing_path.exists():
            continue
        listing = json.loads(listing_path.read_text())
        if listing.get("status") not in ("uploaded", "ready"):
            continue
        if not listing.get("kdp_book_id"):
            continue
        analysis = analyze(d.name)
        if analysis["flags"]:
            results.append(analysis)
    return results


def print_analysis(analysis: dict):
    print(f"\n{'='*50}")
    print(f"Feedback: {analysis['slug']} ({analysis.get('days_published', '?')} days live)")
    if analysis.get("latest_snapshot"):
        s = analysis["latest_snapshot"]
        bsr = s.get("bsr", "n/a")
        bsr_str = f"{bsr:,}" if isinstance(bsr, int) else str(bsr)
        print(f"  BSR: {bsr_str}  |  Units 7d: {s.get('units_7d', 'n/a')}  "
              f"|  KENP 7d: {s.get('kenp_7d', 'n/a')}  |  Rating: {s.get('avg_rating', 'n/a')}")
    if analysis["flags"]:
        print(f"  FLAGS: {'; '.join(analysis['flags'])}")
    if analysis["recommendations"]:
        for r in analysis["recommendations"]:
            print(f"  → {r}")
    if not analysis["flags"]:
        print("  ✓ No issues detected")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--all":
        print("=== Weekly Feedback Review (all published books) ===")
        results = weekly_review_all()
        if not results:
            print("No published books with feedback data or no issues.")
        for r in results:
            print_analysis(r)
    elif len(sys.argv) >= 2 and sys.argv[1] == "--record":
        # Record a snapshot: --record slug bsr units_7d kenp_7d reviews avg_rating
        if len(sys.argv) < 8:
            print("Usage: --record <slug> <bsr> <units_7d> <kenp_7d> <reviews> <avg_rating>")
            sys.exit(1)
        slug = sys.argv[2]
        snap = {
            "bsr": int(sys.argv[3]),
            "units_7d": int(sys.argv[4]),
            "kenp_7d": int(sys.argv[5]),
            "reviews_count": int(sys.argv[6]),
            "avg_rating": float(sys.argv[7]),
        }
        result = record_snapshot(slug, snap)
        print(f"Recorded snapshot for {slug}: {json.dumps(result, indent=2)}")
        analysis = analyze(slug)
        print_analysis(analysis)
    elif len(sys.argv) >= 2:
        slug = sys.argv[1]
        analysis = analyze(slug)
        print_analysis(analysis)
    else:
        print("Usage:")
        print("  python3 feedback_loop.py <slug>          # analyze one book")
        print("  python3 feedback_loop.py --all           # weekly review all books")
        print("  python3 feedback_loop.py --record <slug> <bsr> <units_7d> <kenp_7d> <reviews> <avg_rating>")
