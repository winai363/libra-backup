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


LIBRA_DIR = Path(__file__).parent
KDP_DIR = LIBRA_DIR.parent / "kdp"

KENP_RATE_USD = 0.0045
DEFAULT_EBOOK_PRICE_USD = 2.99
DEFAULT_EBOOK_ROYALTY_RATE = 0.70


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


def _price_usd(listing: dict) -> float:
    raw = listing.get("ebook_price") or listing.get("price") or DEFAULT_EBOOK_PRICE_USD
    text = str(raw).replace("$", "").replace("USD", "").strip()
    try:
        return float(text)
    except ValueError:
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


def _estimated_revenue(history: list[dict], days: int, today: date, listing: dict) -> float:
    direct = _sum_recent(history, "revenue_usd", days, today)
    if direct:
        return round(direct, 2)
    units = _sum_recent(history, "units_7d", days, today)
    kenp = _sum_recent(history, "kenp_7d", days, today)
    ebook_revenue = units * _price_usd(listing) * DEFAULT_EBOOK_ROYALTY_RATE
    kenp_revenue = kenp * KENP_RATE_USD
    return round(ebook_revenue + kenp_revenue, 2)


def _action_for_book(days_live: int | None, latest: dict, totals_30d: dict) -> tuple[str, str]:
    if not latest:
        if days_live is None or days_live < 7:
            return "warming_up", "ยังใหม่หรือยังไม่มีข้อมูล KDP พอ ให้รอดู 7 วันแรก"
        return "needs_data", "ยังไม่มี snapshot ยอดขาย/BSR ต้องบันทึกข้อมูลจาก KDP Reports"

    units = totals_30d["units"]
    kenp = totals_30d["kenp"]
    revenue = totals_30d["revenue_usd"]
    impressions = totals_30d["impressions"]
    bsr = int(latest.get("bsr", 0) or 0)
    rating = float(latest.get("avg_rating", 0) or 0)

    if revenue >= 10 or units >= 5 or kenp >= 1000:
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
        "revenue_usd": _estimated_revenue(history, 7, today, listing),
    }
    totals_30d = {
        "units": int(_sum_recent(history, "units_7d", 30, today)),
        "kenp": int(_sum_recent(history, "kenp_7d", 30, today)),
        "impressions": int(_sum_recent(history, "impressions_7d", 30, today)),
        "revenue_usd": _estimated_revenue(history, 30, today, listing),
    }
    action, reason = _action_for_book(days_live, latest, totals_30d)

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
        "price_usd": _price_usd(listing),
        "latest_snapshot": latest,
        "snapshots": len(history),
        "totals_7d": totals_7d,
        "totals_30d": totals_30d,
        "action": action,
        "reason": reason,
        "market_score": market.get("overall_score") or market.get("score"),
        "quality_passed": bool(quality.get("passed", False)),
        "editorial_passed": bool(editorial.get("passed", False)),
    }


def build_portfolio(today: date | None = None) -> dict:
    today = today or date.today()
    books = []
    for listing_file in sorted(KDP_DIR.glob("*/listing.json")):
        slug = listing_file.parent.name
        if slug == "logs":
            continue
        book = build_book_profit(slug, today)
        if book["status"] in {"uploaded", "ready", "live"} or book["kdp_book_id"]:
            books.append(book)

    total_7d = round(sum(b["totals_7d"]["revenue_usd"] for b in books), 2)
    total_30d = round(sum(b["totals_30d"]["revenue_usd"] for b in books), 2)
    actions: dict[str, int] = {}
    for b in books:
        actions[b["action"]] = actions.get(b["action"], 0) + 1

    ranked = sorted(
        books,
        key=lambda b: (
            b["totals_30d"]["revenue_usd"],
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
        "book_count": len(books),
        "summary": {
            "estimated_revenue_7d_usd": total_7d,
            "estimated_revenue_30d_usd": total_30d,
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
        f"Estimated revenue 7d: ${summary['estimated_revenue_7d_usd']:.2f} | "
        f"30d: ${summary['estimated_revenue_30d_usd']:.2f}"
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
