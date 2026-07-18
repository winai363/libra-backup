from datetime import datetime

from profit_pace import (
    build_pace_controller,
    classify_portfolio,
    rank_opportunities,
    snapshot_revenue_windows,
)


START = datetime.fromisoformat("2026-07-11T00:00:00+00:00")
END = datetime.fromisoformat("2026-10-09T00:00:00+00:00")


def test_pace_controller_uses_elapsed_target_and_110_percent_buffer():
    pace = build_pace_controller(
        actual_revenue=7.0,
        approved_target=75.0,
        started_at=START,
        ends_at=END,
        now=datetime.fromisoformat("2026-07-20T00:00:00+00:00"),
        recent_revenue_7d=5.6,
        recent_revenue_14d=7.0,
    )

    assert pace["elapsed_days"] == 9
    assert pace["elapsed_target"] == 7.5
    assert pace["stretch_target"] == 82.5
    assert pace["variance"] == -0.5
    assert pace["mode"] == "recovery"
    assert pace["required_daily_revenue"] == 0.93
    assert pace["projected_revenue_90d"] == 71.8


def test_pace_controller_distinguishes_ahead_and_critical():
    now = datetime.fromisoformat("2026-07-20T00:00:00+00:00")
    ahead = build_pace_controller(8.3, 75, START, END, now, 6.3, 8.3)
    critical = build_pace_controller(5.0, 75, START, END, now, 3.5, 5.0)

    assert ahead["mode"] == "ahead"
    assert critical["mode"] == "critical"


def _book(slug, royalties, profit, orders, kenp, *, fresh=True, cost_complete=True):
    return {
        "slug": slug,
        "status": "uploaded",
        "live_status": "LIVE",
        "contribution": {
            "royalties_usd": royalties,
            "contribution_profit_usd": profit,
            "cost_complete": cost_complete,
        },
        "latest_snapshot": {
            "delta_revenue_usd": royalties,
            "delta_units": orders,
            "delta_kenp": kenp,
        },
        "totals_30d": {
            "verified_revenue_usd": royalties,
            "units": orders,
            "kenp": kenp,
        },
        "data_fresh": fresh,
    }


def test_portfolio_allocator_separates_exploit_explore_and_archive():
    books = [
        _book("winner", 2.24, 2.20, 10, 69),
        _book("reader", 0, -0.04, 1, 90),
        _book("silent", 0, -0.10, 0, 0),
    ]

    allocation = classify_portfolio(books)

    assert allocation["policy"] == {"exploit": 70, "explore": 20, "archive": 10}
    assert allocation["buckets"]["exploit"] == ["winner"]
    assert allocation["buckets"]["explore"] == ["reader"]
    assert allocation["buckets"]["archive"] == ["silent"]


def test_opportunity_ranking_promotes_new_verified_winner_signal():
    books = [
        _book("new-signal", 2.24, 2.20, 10, 69),
        _book("old-small", 0.50, 0.45, 1, 0),
        _book("incomplete", 4.0, 3.5, 8, 100, cost_complete=False),
    ]

    ranked = rank_opportunities(books)

    assert ranked[0]["slug"] == "new-signal"
    assert ranked[0]["lane"] == "winner_watch"
    assert ranked[0]["recommended_action"] == "observe_second_window"
    assert ranked[0]["score"] > ranked[1]["score"]
    assert next(item for item in ranked if item["slug"] == "incomplete")["confidence"] == "low"


def test_snapshot_revenue_windows_handles_calendar_month_reset():
    rows = [
        ("2026-06-30T09:15:00+00:00", "2026-06", 20.00),
        ("2026-07-11T09:15:00+00:00", "2026-07", 7.63),
        ("2026-07-31T09:15:00+00:00", "2026-07", 15.00),
        ("2026-08-01T09:15:00+00:00", "2026-08", 1.00),
        ("2026-08-05T09:15:00+00:00", "2026-08", 4.00),
    ]

    windows = snapshot_revenue_windows(
        rows, datetime.fromisoformat("2026-08-05T10:00:00+00:00"), started_at=START
    )

    assert windows == {"mode": 11.37, "days_7": 11.37, "days_14": 11.37}


def test_stale_pace_is_insufficient_data_not_critical():
    from datetime import timedelta

    pace = build_pace_controller(
        0, 75, START, END, START + timedelta(days=10), 0, 0, data_fresh=False
    )
    assert pace["mode"] == "insufficient_data"


def test_any_verified_kenp_is_explore_not_archive():
    allocation = classify_portfolio([_book("one-page", 0, -0.1, 0, 1)])
    assert allocation["buckets"]["explore"] == ["one-page"]


def test_blocked_titles_are_excluded_from_allocation_and_opportunities():
    blocked = {**_book("blocked", 5, 4, 10, 100), "live_status": "BLOCKED"}
    allocation = classify_portfolio([blocked])

    assert allocation["counts"] == {"exploit": 0, "explore": 0, "archive": 0}
    assert rank_opportunities([blocked]) == []
