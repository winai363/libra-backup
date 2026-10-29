#!/usr/bin/env python3
"""Read-only scorecard for Libra's 90-day organic search release."""
from __future__ import annotations

import argparse
import json
import sys
from calendar import monthrange
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from organic_search import build_scorecard, load_search_portfolio


def _month_number(month: str) -> int:
    year, number = (int(part) for part in month.split("-"))
    return year * 12 + number


def _month_complete(month: str, as_of: date) -> bool:
    return month < as_of.strftime("%Y-%m")


def evaluate_goal(months: dict[str, float], *, as_of: date, target: float) -> str:
    completed = sorted(
        (month, value) for month, value in months.items()
        if _month_complete(month, as_of)
    )
    for (first_month, first_value), (second_month, second_value) in zip(completed, completed[1:]):
        if (
            _month_number(second_month) == _month_number(first_month) + 1
            and first_value > target
            and second_value > target
        ):
            return "TARGET_MET"
    return "IN_PROGRESS"


def _next_month_start(value: date) -> date:
    if value.month == 12:
        return date(value.year + 1, 1, 1)
    return date(value.year, value.month + 1, 1)


def _required_months(released: date, count: int) -> list[str]:
    cursor = _next_month_start(released)
    months = []
    for _ in range(count):
        months.append(cursor.strftime("%Y-%m"))
        cursor = _next_month_start(cursor)
    return months


def build_report(ledger: Path, portfolio_path: Path, *, as_of: date | None = None) -> dict:
    as_of = as_of or date.today()
    config = load_search_portfolio(portfolio_path)
    scorecard = build_scorecard(ledger, config)
    visible_months = {}
    for month, row in scorecard["kdp_months"].items():
        try:
            observed_date = datetime.fromisoformat(row["observed_at"]).date()
        except (KeyError, TypeError, ValueError):
            continue
        if observed_date <= as_of:
            visible_months[month] = row
    scorecard["kdp_months"] = visible_months
    release = config.get("release", {})
    target = float(release.get("target_royalties_usd", 20.0))
    required_count = int(release.get("required_consecutive_complete_months", 2))
    values = {
        month: float(row["royalties_usd"])
        for month, row in visible_months.items()
    }
    status = "PRE_RELEASE"
    reason = "release_not_active"
    target_months = []
    deadline = None
    if release.get("state") == "ACTIVE":
        try:
            released = datetime.fromisoformat(release["released_at"]).date()
            deadline = released + timedelta(days=int(release.get("goal_days", 90)))
            target_months = _required_months(released, required_count)
            measurement_values = {
                month: values[month] for month in target_months if month in values
            }
            status = evaluate_goal(measurement_values, as_of=as_of, target=target)
            reason = "measurement_window_open"
            if status == "TARGET_MET":
                reason = "two_consecutive_complete_months_above_target"
            elif as_of >= deadline:
                missing = [month for month in target_months if month not in values]
                if missing:
                    status = "UNKNOWN"
                    reason = "required_complete_month_data_missing"
                else:
                    status = "TARGET_NOT_MET"
                    reason = "deadline_reached_without_two_months_above_target"
        except (KeyError, TypeError, ValueError):
            status = "UNKNOWN"
            reason = "invalid_release_evidence"
        if scorecard.get("ledger", {}).get("status") != "AVAILABLE":
            status = "UNKNOWN"
            reason = "ledger_unavailable"

    latest_month = max(scorecard["kdp_months"], default=None)
    latest = scorecard["kdp_months"].get(latest_month) if latest_month else None
    current_royalties = float(latest["royalties_usd"]) if latest else None
    return {
        **scorecard,
        "as_of": as_of.isoformat(),
        "goal": {
            "status": status,
            "reason": reason,
            "target_royalties_usd": target,
            "required_consecutive_complete_months": required_count,
            "target_months": target_months,
            "deadline": deadline.isoformat() if deadline else None,
            "latest_month": latest_month,
            "latest_royalties_usd": current_royalties,
            "gap_to_20_usd": round(max(0.0, target - current_royalties), 2)
            if current_royalties is not None else None,
        },
    }


def human_summary(payload: dict) -> str:
    goal = payload["goal"]
    traffic = payload["traffic"]
    latest = goal["latest_month"] or "UNKNOWN"
    royalties = goal["latest_royalties_usd"]
    amount = "UNKNOWN" if royalties is None else f"USD {royalties:.2f}"
    observed = (
        payload["kdp_months"].get(latest, {}).get("observed_at")
        if latest != "UNKNOWN" else None
    ) or "UNKNOWN"
    gap = goal["gap_to_20_usd"]
    gap_text = "UNKNOWN" if gap is None else f"USD {gap:.2f}"
    ledger_status = payload.get("ledger", {}).get("status", "UNKNOWN")
    return "\n".join([
        f"Libra organic search — {payload['as_of']}",
        f"Goal: {goal['status']} ({goal['reason']})",
        f"Latest KDP estimate: {latest} = {amount}",
        f"KDP observed at: {observed}",
        f"Gap to strict USD 20 threshold: {gap_text}",
        f"Ledger source: {ledger_status}",
        f"Search-surface requests: {traffic['qualified_views'] if traffic['qualified_views'] is not None else 'UNKNOWN'}",
        f"Search-surface Amazon outbound clicks: {traffic['amazon_outbound'] if traffic['amazon_outbound'] is not None else 'UNKNOWN'}",
        "Traffic note: requests are not unique people; source filtering is incomplete.",
        "Purchase attribution: UNKNOWN",
        "Search Console: UNKNOWN (not connected)",
    ])


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--ledger", type=Path, default=ROOT / "data" / "libra-business.db")
    parser.add_argument("--portfolio", type=Path, default=ROOT / "data" / "search_portfolio.json")
    parser.add_argument("--as-of", type=date.fromisoformat)
    args = parser.parse_args(argv)
    payload = build_report(args.ledger, args.portfolio, as_of=args.as_of)
    print(json.dumps(payload, ensure_ascii=False, indent=2) if args.json else human_summary(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
