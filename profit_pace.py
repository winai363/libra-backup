"""Pure commercial decision helpers for Libra's 90-day profit mode."""

from __future__ import annotations

from datetime import datetime, timedelta


STRETCH_MULTIPLIER = 1.10
ATTENTION_POLICY = {"exploit": 70, "explore": 20, "archive": 10}


def snapshot_revenue_windows(
    rows: list[tuple], now: datetime, started_at: datetime | None = None
) -> dict:
    """Convert calendar-cumulative KDP snapshots into mode/7d/14d deltas."""
    if started_at is not None:
        rows = [row for row in rows if datetime.fromisoformat(row[0]) >= started_at]
    if not rows:
        return {"mode": 0.0, "days_7": 0.0, "days_14": 0.0}
    effective = []
    first_month = rows[0][1]
    first_baseline = float(rows[0][2])
    current_month = first_month
    completed_total = 0.0
    previous_effective = 0.0
    for observed_at, month, royalties in rows:
        if month != current_month:
            completed_total = previous_effective
            current_month = month
        month_value = float(royalties)
        value = (completed_total + month_value if month != first_month
                 else month_value - first_baseline)
        previous_effective = max(0.0, value)
        effective.append((datetime.fromisoformat(observed_at), previous_effective))
    latest = effective[-1][1]

    def delta(days: int) -> float:
        cutoff = now - timedelta(days=days)
        prior = next((value for observed, value in reversed(effective)
                      if observed <= cutoff), 0.0)
        return max(0.0, latest - prior)

    return {
        "mode": round(latest, 2),
        "days_7": round(delta(7), 2),
        "days_14": round(delta(14), 2),
    }


def build_pace_controller(
    actual_revenue: float,
    approved_target: float,
    started_at: datetime,
    ends_at: datetime,
    now: datetime,
    recent_revenue_7d: float,
    recent_revenue_14d: float,
    data_fresh: bool = True,
) -> dict:
    total_days = max(1, (ends_at - started_at).days)
    elapsed_days = min(total_days, max(0, (now - started_at).days))
    remaining_days = max(0, total_days - elapsed_days)
    elapsed_target = approved_target * elapsed_days / total_days
    stretch_target = approved_target * STRETCH_MULTIPLIER
    pace_ratio = actual_revenue / elapsed_target if elapsed_target > 0 else None

    if not data_fresh or elapsed_days == 0:
        mode = "insufficient_data"
    elif pace_ratio >= STRETCH_MULTIPLIER:
        mode = "ahead"
    elif pace_ratio >= 1:
        mode = "on_pace"
    elif elapsed_days >= 3 and pace_ratio < 0.75:
        mode = "critical"
    else:
        mode = "recovery"

    run_rate_7d = recent_revenue_7d / 7
    projected = actual_revenue + run_rate_7d * remaining_days
    required = ((stretch_target - actual_revenue) / remaining_days
                if remaining_days else 0.0)
    return {
        "mode": mode,
        "approved_target": round(approved_target, 2),
        "stretch_target": round(stretch_target, 2),
        "elapsed_days": elapsed_days,
        "remaining_days": remaining_days,
        "elapsed_target": round(elapsed_target, 2),
        "actual_revenue": round(actual_revenue, 2),
        "variance": round(actual_revenue - elapsed_target, 2),
        "pace_ratio": round(pace_ratio, 3) if pace_ratio is not None else None,
        "required_daily_revenue": round(max(0.0, required), 2),
        "run_rate_7d": round(run_rate_7d, 2),
        "run_rate_14d": round(recent_revenue_14d / 14, 2),
        "projected_revenue_90d": round(projected, 2),
    }


def _signals(book: dict) -> tuple[float, float, int, int, bool]:
    contribution = book.get("contribution") or {}
    totals = book.get("totals_30d") or {}
    latest = book.get("latest_snapshot") or {}
    royalties = float(contribution.get("royalties_usd") or
                      totals.get("verified_revenue_usd") or 0)
    profit = float(contribution.get("contribution_profit_usd") or 0)
    orders = int(totals.get("units") or latest.get("delta_units") or 0)
    kenp = int(totals.get("kenp") or latest.get("delta_kenp") or 0)
    complete = bool(contribution.get("cost_complete"))
    return royalties, profit, orders, kenp, complete


def classify_portfolio(books: list[dict]) -> dict:
    buckets = {"exploit": [], "explore": [], "archive": []}
    for book in books:
        if str(book.get("live_status") or "").upper() != "LIVE":
            continue
        royalties, profit, orders, kenp, complete = _signals(book)
        if royalties > 0 and profit > 0 and complete and (orders > 0 or kenp > 0):
            lane = "exploit"
        elif royalties > 0 or orders > 0 or kenp > 0:
            lane = "explore"
        else:
            lane = "archive"
        buckets[lane].append(book["slug"])
    return {
        "policy": dict(ATTENTION_POLICY),
        "buckets": buckets,
        "counts": {lane: len(slugs) for lane, slugs in buckets.items()},
    }


def rank_opportunities(books: list[dict]) -> list[dict]:
    ranked = []
    for book in books:
        if str(book.get("live_status") or "").upper() != "LIVE":
            continue
        royalties, profit, orders, kenp, complete = _signals(book)
        fresh = book.get("data_fresh") is True
        latest = book.get("latest_snapshot") or {}
        recent_revenue = float(latest.get("delta_revenue_usd") or
                               latest.get("revenue_usd") or 0)
        meaningful_signal = recent_revenue > 0 and (orders >= 2 or kenp >= 50)
        confidence = "high" if complete and fresh else "low"
        score = royalties * 20 + min(orders, 20) * 2 + min(kenp, 300) / 15
        if meaningful_signal:
            score += 25
        if not complete:
            score *= 0.45
        if not fresh:
            score *= 0.5
        lane = "winner_watch" if meaningful_signal and complete and fresh else (
            "explore" if royalties > 0 or orders > 0 or kenp > 0 else "archive"
        )
        ranked.append({
            "slug": book["slug"],
            "score": round(score, 2),
            "lane": lane,
            "confidence": confidence,
            "recommended_action": (
                "observe_second_window" if lane == "winner_watch" else
                "collect_complete_costs" if not complete else
                "build_verified_distribution" if lane == "explore" else
                "hold"
            ),
            "evidence": {
                "verified_royalties_usd": round(royalties, 2),
                "recent_verified_revenue_usd": round(recent_revenue, 2),
                "contribution_profit_usd": round(profit, 2),
                "orders": orders,
                "kenp": kenp,
            },
        })
    return sorted(ranked, key=lambda item: (-item["score"], item["slug"]))
