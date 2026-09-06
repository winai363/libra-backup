"""Read-only sales observations and local rejection evidence. No publishing API."""

import json
import sqlite3
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path


def readonly_connection(path: Path):
    return sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)


def sales_evidence(path: Path, *, today: str) -> dict:
    cutoff = date.fromisoformat(today)
    rows = []
    if Path(path).exists():
        with readonly_connection(path) as connection:
            connection.row_factory = sqlite3.Row
            rows = [dict(row) for row in connection.execute(
                "SELECT s.id, s.observed_at, s.month, s.royalties_usd,"
                " s.orders_all_types, s.kenp,"
                " COALESCE(SUM(a.royalties_usd), 0) AS attributed_royalties_usd"
                " FROM kdp_snapshots s LEFT JOIN kdp_title_attribution a"
                " ON a.snapshot_id=s.id WHERE substr(s.observed_at,1,10)<=?"
                " GROUP BY s.id ORDER BY julianday(s.observed_at), s.id", (today,)
            )]
    months = {}
    for row in rows:
        row["attributed_royalties_usd"] = round(row["attributed_royalties_usd"], 2)
        row["attribution_gap_usd"] = round(
            row["royalties_usd"] - row["attributed_royalties_usd"], 2)
        row.update(paid_orders=None, free_orders=None, profit_usd=None)
        months[row["month"]] = row
    latest = rows[-1] if rows else None
    comparison = {"status": "unavailable", "current": latest,
                  "previous": None, "change_pct": None,
                  "reason": "matching_previous_month_observation_missing"}
    if latest and latest["month"] == latest["observed_at"][:7]:
        observed = datetime.fromisoformat(latest["observed_at"])
        previous_month = (observed.date().replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
        candidates = [row for row in rows if row["month"] == previous_month
                      and row["month"] == row["observed_at"][:7]
                      and row["observed_at"][8:10] == latest["observed_at"][8:10]
                      and abs((datetime.fromisoformat(row["observed_at"]).hour * 60
                               + datetime.fromisoformat(row["observed_at"]).minute)
                              - (observed.hour * 60 + observed.minute)) <= 30]
        if candidates:
            previous = min(candidates, key=lambda row: abs(
                (datetime.fromisoformat(row["observed_at"]).hour * 60
                 + datetime.fromisoformat(row["observed_at"]).minute)
                - (observed.hour * 60 + observed.minute)))
            base = previous["royalties_usd"]
            comparison.update(status="available", previous=previous,
                              change_pct=round((latest["royalties_usd"] - base) / base * 100, 2)
                              if base > 0 else None,
                              reason=None if base > 0 else "nonpositive_baseline")
    age = (cutoff - date.fromisoformat(latest["observed_at"][:10])).days if latest else None
    return {
        "status": "available" if latest else "unavailable",
        "observed_at": latest["observed_at"] if latest else None,
        "age_days": age,
        "stale": age > 2 if age is not None else None,
        "estimated_royalties_usd": round(sum(r["royalties_usd"] for r in months.values()), 2)
        if latest else None,
        "months": [months[month] for month in sorted(months)],
        "comparison": comparison,
        "caveats": ["Estimates converted using the collector's static exchange rates; not payouts.",
                    "Paid/free order split and net profit are unknown.",
                    "Comparison uses observation day and local clock within 30 minutes; not order dates.",
                    "Historical months use the latest stored observation, not final settlement."],
    }


def rejection_evidence(books_dir: Path) -> dict:
    counts = Counter()
    cases, unreadable = [], []
    for path in sorted(Path(books_dir).glob("*/listing.json")):
        try:
            listing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(listing, dict):
                raise ValueError("listing must be an object")
        except (OSError, ValueError):
            unreadable.append(str(path))
            continue
        status = str(listing.get("live_status") or "UNKNOWN").upper()
        counts[status] += 1
        if status != "BLOCKED":
            continue
        note = listing.get("blocked")
        note = note if isinstance(note, dict) else {}
        cases.append({
            "slug": path.parent.name, "asin": listing.get("asin"),
            "date": note.get("date"), "case_id": note.get("case_id"),
            "local_note": note.get("reason"), "confirmed_cause": None,
            "source": str(path),
            "review_needed": ["original_amazon_notice", "submitted_epub_and_metadata",
                              "independent_editorial_review"],
        })
    return {"status_source": "local_listings_not_live_verification",
            "counts": dict(counts), "cases": cases, "unreadable_listings": unreadable,
            "catalogue_available": Path(books_dir).is_dir()}
