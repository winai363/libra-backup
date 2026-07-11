"""
profit_tracker.py — Libra portfolio profit and traction analytics.

This module does not guess real KDP sales. It summarizes recorded KDP feedback
snapshots from feedback-history.json and turns them into clear action buckets.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

from business_ledger import direct_costs_for_slug, portfolio_financials


LIBRA_DIR = Path(__file__).parent
KDP_DIR = LIBRA_DIR.parent / "kdp"
LEDGER_FILE = LIBRA_DIR / "data" / "libra-business.db"

DEFAULT_EBOOK_PRICE_USD = 2.99
COST_PER_BOOK_USD = 0.68   # fallback estimate for books without cost-report.json
THB_RATE = 35.5            # USD → THB exchange rate (update if needed)


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    return default


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(text[: len(fmt.replace("%z", "+0000"))], fmt).date()
        except ValueError:
            continue
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _days_since(value: Any, today: date) -> int | None:
    parsed = _parse_date(value)
    if not parsed:
        return None
    return max(0, (today - parsed).days)


def _cost_usd(book_dir: Path) -> float:
    """Return actual book production cost from cost-report.json, or estimate."""
    report = _load_json(book_dir / "cost-report.json", {})
    if report and "total_usd" in report:
        return float(report["total_usd"])
    return COST_PER_BOOK_USD


def _price_usd(listing: dict, book_dir: Path | None = None) -> float:
    # 1. ราคาที่บันทึกไว้ใน listing.json (ถ้ามี)
    raw = listing.get("ebook_price") or listing.get("price")
    if raw:
        text = str(raw).replace("$", "").replace("USD", "").strip()
        try:
            return float(text)
        except ValueError:
            pass
    # 2. ราคาจริงที่ pricing_engine แนะนำ (ใช้ตอน upload จริง)
    if book_dir:
        rec = _load_json(book_dir / "pricing-recommendation.json", {})
        price = rec.get("recommended_price_usd")
        if price:
            try:
                return float(price)
            except (TypeError, ValueError):
                pass
    return DEFAULT_EBOOK_PRICE_USD


def _sum_recent(history: list[dict], field: str, days: int, today: date) -> float:
    total = 0.0
    for snap in history:
        snap_date = _parse_date(snap.get("date"))
        if snap_date and (today - snap_date).days <= days:
            try:
                total += float(snap.get(field, 0) or 0)
            except (TypeError, ValueError):
                pass
    return total


def _verified_revenue(history: list[dict], days: int, today: date) -> float:
    return round(_sum_recent(history, "revenue_usd", days, today), 2)


def _action_for_book(
    days_live: int | None,
    latest: dict,
    totals_30d: dict,
    attributable_cost_usd: float | None = None,
) -> tuple[str, str]:
    if not latest:
        if days_live is None or days_live < 7:
            return "warming_up", "ยังใหม่หรือยังไม่มีข้อมูล KDP พอ ให้รอดู 7 วันแรก"
        return "needs_data", "ยังไม่มี snapshot ยอดขาย/BSR ต้องบันทึกข้อมูลจาก KDP Reports"

    units = totals_30d["units"]
    kenp = totals_30d["kenp"]
    revenue = totals_30d["verified_revenue_usd"]
    impressions = totals_30d["impressions"]
    bsr = int(latest.get("bsr", 0) or 0)
    rating = float(latest.get("avg_rating", 0) or 0)

    contribution_is_positive = (
        attributable_cost_usd is None or revenue - attributable_cost_usd > 0
    )
    if revenue > 0 and contribution_is_positive:
        return "winner", "มี traction แล้ว ควรทำเล่มต่อยอดใน niche/keyword ใกล้เคียง"
    if units > 0 or kenp > 0 or (0 < bsr <= 200_000):
        return "momentum", "เริ่มมีสัญญาณ ควรรอดูต่อและพิจารณาปรับ cover/keyword แบบเบา"
    if rating and rating < 3.5:
        return "fix_quality", "rating ต่ำ ต้องอ่าน review และแก้คุณภาพก่อน scale"
    if days_live is not None and days_live >= 14 and impressions < 100:
        return "optimize_metadata", "ผ่าน 14 วันแต่ impression ต่ำ ควรปรับ title/subtitle/keyword/category"
    if days_live is not None and days_live >= 30 and units < 5:
        return "review_or_kill", "ผ่าน 30 วันแล้วยอดต่ำ ควรปรับหนักหรือหยุดทำ niche นี้"
    if bsr > 500_000:
        return "low_visibility", "BSR สูงมาก แปลว่า visibility ต่ำ ควรปรับ keyword/category"
    return "watch", "ยังไม่มีปัญหาชัดเจน ให้ติดตามต่อ"


def build_book_profit(slug: str, today: date | None = None) -> dict:
    today = today or date.today()
    book_dir = KDP_DIR / slug
    listing = _load_json(book_dir / "listing.json", {})
    history = _load_json(book_dir / "feedback-history.json", [])
    market = _load_json(book_dir / "market-score.json", {})
    quality = _load_json(book_dir / "quality-report.json", {})
    editorial = _load_json(book_dir / "editorial-review.json", {})

    live_date = (
        listing.get("content_updated_at")
        or listing.get("uploaded_at")
        or listing.get("published_at")
        or listing.get("created_at")
    )
    days_live = _days_since(live_date, today)
    latest = history[-1] if history else {}
    totals_7d = {
        "units": int(_sum_recent(history, "units_7d", 7, today)),
        "kenp": int(_sum_recent(history, "kenp_7d", 7, today)),
        "impressions": int(_sum_recent(history, "impressions_7d", 7, today)),
        "verified_revenue_usd": _verified_revenue(history, 7, today),
    }
    totals_30d = {
        "units": int(_sum_recent(history, "units_7d", 30, today)),
        "kenp": int(_sum_recent(history, "kenp_7d", 30, today)),
        "impressions": int(_sum_recent(history, "impressions_7d", 30, today)),
        "verified_revenue_usd": _verified_revenue(history, 30, today),
    }

    price_usd = _price_usd(listing, book_dir)
    cost_report = _load_json(book_dir / "cost-report.json", {})
    ledger_cost_usd = direct_costs_for_slug(LEDGER_FILE, slug)
    if ledger_cost_usd > 0:
        cost_usd = ledger_cost_usd
        cost_is_real = True
    else:
        cost_usd = round(float(cost_report["total_usd"]) if cost_report and "total_usd" in cost_report else COST_PER_BOOK_USD, 4)
        cost_is_real = bool(cost_report and "total_usd" in cost_report)
    action, reason = _action_for_book(
        days_live,
        latest,
        totals_30d,
        cost_usd if cost_is_real else None,
    )
    # Compatibility aliases carry the same verified value and are never modeled.
    totals_7d["revenue_usd"] = totals_7d["verified_revenue_usd"]
    totals_30d["revenue_usd"] = totals_30d["verified_revenue_usd"]
    return {
        "slug": slug,
        "title": listing.get("title", slug),
        "status": listing.get("status", ""),
        "language": listing.get("language", ""),
        "asin": listing.get("asin", ""),
        "kdp_book_id": listing.get("kdp_book_id", ""),
        "created_at": listing.get("created_at", ""),
        "live_date": str(_parse_date(live_date) or ""),
        "days_live": days_live,
        "price_usd": price_usd,
        "price_thb": round(price_usd * THB_RATE, 0),
        "cost_usd": cost_usd,
        "cost_thb": round(cost_usd * THB_RATE, 0),
        "cost_is_real": cost_is_real,
        "latest_snapshot": latest,
        "snapshots": len(history),
        "totals_7d": totals_7d,
        "totals_7d_thb": {k: round(v * THB_RATE, 2) if "revenue_usd" in k else v for k, v in totals_7d.items()},
        "totals_30d": totals_30d,
        "totals_30d_thb": {k: round(v * THB_RATE, 2) if "revenue_usd" in k else v for k, v in totals_30d.items()},
        "action": action,
        "reason": reason,
        "market_score": market.get("overall_score") or market.get("score"),
        "quality_passed": bool(quality.get("passed", False)),
        "editorial_passed": bool(editorial.get("passed", False)),
    }


def build_portfolio(today: date | None = None) -> dict:
    today = today or date.today()
    financials = portfolio_financials(LEDGER_FILE, today.strftime("%Y-%m"))
    books = []
    for listing_file in sorted(KDP_DIR.glob("*/listing.json")):
        slug = listing_file.parent.name
        if slug == "logs":
            continue
        book = build_book_profit(slug, today)
        # Only include books confirmed uploaded to KDP (Live or In Review on Amazon)
        if book["status"] == "uploaded":
            books.append(book)

    total_7d = round(sum(b["totals_7d"]["verified_revenue_usd"] for b in books), 2)
    total_30d = round(sum(b["totals_30d"]["verified_revenue_usd"] for b in books), 2)
    total_cost_usd = round(sum(b["cost_usd"] for b in books), 2)
    total_cost_thb = round(total_cost_usd * THB_RATE, 0)
    actions: dict[str, int] = {}
    for b in books:
        actions[b["action"]] = actions.get(b["action"], 0) + 1

    ranked = sorted(
        books,
        key=lambda b: (
            b["totals_30d"]["verified_revenue_usd"],
            b["totals_30d"]["units"],
            b["totals_30d"]["kenp"],
        ),
        reverse=True,
    )
    attention = [
        b for b in books
        if b["action"] in {"needs_data", "optimize_metadata", "review_or_kill", "fix_quality", "low_visibility"}
    ]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "verified_royalties_mtd_usd": financials["verified_royalties_usd"],
        "contribution_profit_usd": financials["contribution_profit_usd"],
        "fully_loaded_net_profit_usd": financials["fully_loaded_net_profit_usd"],
        "overhead_complete": financials["overhead_complete"],
        "reconciliation": {
            "attributed_royalties_usd": financials["attributed_royalties_usd"],
            "unattributed_royalties_usd": financials["unattributed_royalties_usd"],
            "snapshot_count": financials["snapshot_count"],
        },
        "book_count": len(books),
        "thb_rate": THB_RATE,
        "summary": {
            "verified_revenue_7d_usd": total_7d,
            "verified_revenue_7d_thb": round(total_7d * THB_RATE, 0),
            "verified_revenue_30d_usd": total_30d,
            "verified_revenue_30d_thb": round(total_30d * THB_RATE, 0),
            "estimated_revenue_7d_usd": total_7d,
            "estimated_revenue_7d_thb": round(total_7d * THB_RATE, 0),
            "estimated_revenue_30d_usd": total_30d,
            "estimated_revenue_30d_thb": round(total_30d * THB_RATE, 0),
            "total_cost_usd": total_cost_usd,
            "total_cost_thb": total_cost_thb,
            "units_30d": sum(b["totals_30d"]["units"] for b in books),
            "kenp_30d": sum(b["totals_30d"]["kenp"] for b in books),
            "books_with_data": sum(1 for b in books if b["snapshots"] > 0),
            "action_counts": actions,
        },
        "top_books": ranked[:10],
        "attention": attention[:20],
        "books": ranked,
    }


def print_portfolio(portfolio: dict) -> None:
    summary = portfolio["summary"]
    print("=== Libra Profit Portfolio ===")
    print(f"Books: {portfolio['book_count']} | With data: {summary['books_with_data']}")
    print(
        f"Verified revenue 7d: ${summary['verified_revenue_7d_usd']:.2f} | "
        f"30d: ${summary['verified_revenue_30d_usd']:.2f}"
    )
    print(f"Units 30d: {summary['units_30d']} | KENP 30d: {summary['kenp_30d']}")
    print("\nNeeds attention:")
    for b in portfolio["attention"][:10]:
        print(f"- {b['slug']}: {b['action']} — {b['reason']}")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--json":
        print(json.dumps(build_portfolio(), ensure_ascii=False, indent=2))
    else:
        print_portfolio(build_portfolio())
