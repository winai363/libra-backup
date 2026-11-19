import json
import sqlite3
from datetime import date

from business_ledger import init_ledger, record_hub_event
from organic_search import build_view_event
from scripts.search_organic_report import (
    build_report,
    evaluate_goal,
    latest_search_console_snapshot,
)


def seed_snapshot(ledger, month, royalties, observed_at):
    init_ledger(ledger)
    with sqlite3.connect(ledger) as connection:
        connection.execute(
            "INSERT INTO kdp_snapshots "
            "(observed_at, month, royalties_usd, orders_all_types, kenp, raw_json, source_key, content_hash) "
            "VALUES (?, ?, ?, 0, 0, '{}', ?, ?)",
            (observed_at, month, royalties, f"source:{observed_at}", observed_at),
        )


def write_portfolio(path, *, state="ACTIVE", released_at="2026-10-30T10:00:00+07:00"):
    path.write_text(json.dumps({
        "schema": "libra-search-portfolio/1",
        "campaign": "search-surface",
        "release": {
            "state": state,
            "released_at": released_at,
            "goal_days": 90,
            "target_royalties_usd": 20.0,
            "required_consecutive_complete_months": 2,
        },
        "books": [],
    }))


def test_json_report_keeps_traffic_and_revenue_separate(tmp_path):
    ledger = tmp_path / "ledger.db"
    portfolio = tmp_path / "portfolio.json"
    write_portfolio(portfolio)
    seed_snapshot(ledger, "2026-11", 21.0, "2026-11-30T09:00:00+07:00")
    record_hub_event(ledger, build_view_event("book-a", "search-surface", "article:a1"))

    payload = build_report(ledger, portfolio, as_of=date(2026, 12, 1))

    assert payload["purchase_attribution"] == "UNKNOWN"
    assert payload["traffic"] == {"qualified_views": 1, "amazon_outbound": 0}
    assert "conversion_rate" not in json.dumps(payload)
    assert payload["search_console"]["status"] == "UNKNOWN"


def test_two_complete_months_above_target_are_target_met():
    result = evaluate_goal(
        {"2026-11": 20.01, "2026-12": 24.50},
        as_of=date(2027, 1, 1),
        target=20.0,
    )

    assert result == "TARGET_MET"


def test_exactly_twenty_does_not_meet_strict_above_target():
    result = evaluate_goal(
        {"2026-11": 20.0, "2026-12": 24.50},
        as_of=date(2027, 1, 1),
        target=20.0,
    )

    assert result == "IN_PROGRESS"


def test_missing_months_at_deadline_are_unknown(tmp_path):
    ledger = tmp_path / "ledger.db"
    portfolio = tmp_path / "portfolio.json"
    write_portfolio(portfolio)
    seed_snapshot(ledger, "2026-11", 25.0, "2026-11-30T09:00:00+07:00")

    payload = build_report(ledger, portfolio, as_of=date(2027, 1, 29))

    assert payload["goal"]["status"] == "UNKNOWN"
    assert payload["goal"]["reason"] == "required_complete_month_data_missing"


def test_months_before_release_cannot_satisfy_goal(tmp_path):
    ledger = tmp_path / "ledger.db"
    portfolio = tmp_path / "portfolio.json"
    write_portfolio(portfolio)
    seed_snapshot(ledger, "2026-07", 30.0, "2026-07-31T09:00:00+07:00")
    seed_snapshot(ledger, "2026-08", 31.0, "2026-08-31T09:00:00+07:00")
    seed_snapshot(ledger, "2026-11", 10.0, "2026-11-30T09:00:00+07:00")
    seed_snapshot(ledger, "2026-12", 11.0, "2026-12-31T09:00:00+07:00")

    payload = build_report(ledger, portfolio, as_of=date(2027, 1, 29))

    assert payload["goal"]["status"] == "TARGET_NOT_MET"


def test_as_of_excludes_snapshots_observed_in_the_future(tmp_path):
    ledger = tmp_path / "ledger.db"
    portfolio = tmp_path / "portfolio.json"
    write_portfolio(portfolio)
    seed_snapshot(ledger, "2026-11", 21.0, "2026-12-05T09:00:00+07:00")

    payload = build_report(ledger, portfolio, as_of=date(2026, 12, 1))

    assert payload["kdp_months"] == {}
    assert payload["goal"]["latest_royalties_usd"] is None


def test_active_release_with_unavailable_ledger_is_unknown_immediately(tmp_path):
    portfolio = tmp_path / "portfolio.json"
    write_portfolio(portfolio)

    payload = build_report(tmp_path / "missing.db", portfolio, as_of=date(2026, 11, 1))

    assert payload["goal"]["status"] == "UNKNOWN"
    assert payload["goal"]["reason"] == "ledger_unavailable"


def test_latest_search_console_snapshot_returns_fresh_final_metrics(tmp_path):
    snapshots = tmp_path / "snapshots.jsonl"
    snapshots.write_text("\n".join([
        json.dumps({
            "observed_on": "2026-11-12",
            "search_console": {"status": "UNKNOWN", "reason": "provider_error"},
        }),
        json.dumps({
            "observed_on": "2026-11-18",
            "search_console": {
                "status": "CONNECTED",
                "window": {"start": "2026-10-19", "end": "2026-11-15"},
                "metrics": {"impressions": 4, "clicks": 1, "ctr": 0.25, "position": 8.0},
            },
        }),
    ]) + "\n")

    result = latest_search_console_snapshot(snapshots, date(2026, 11, 19))

    assert result == {
        "status": "AVAILABLE",
        "observed_on": "2026-11-18",
        "age_days": 1,
        "window": {"start": "2026-10-19", "end": "2026-11-15"},
        "metrics": {"impressions": 4, "clicks": 1, "ctr": 0.25, "position": 8.0},
    }


def test_latest_search_console_snapshot_marks_stale_or_malformed_unknown(tmp_path):
    snapshots = tmp_path / "snapshots.jsonl"
    snapshots.write_text("not-json\n" + json.dumps({
        "observed_on": "2026-11-01",
        "search_console": {
            "status": "CONNECTED",
            "metrics": {"impressions": 1, "clicks": 0, "ctr": 0.0, "position": 70.0},
        },
    }) + "\n")

    result = latest_search_console_snapshot(snapshots, date(2026, 11, 19))

    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "snapshot_stale"
    assert result["observed_on"] == "2026-11-01"
    assert result["age_days"] == 18


def test_build_report_includes_latest_search_console_snapshot(tmp_path):
    ledger = tmp_path / "ledger.db"
    portfolio = tmp_path / "portfolio.json"
    snapshots = tmp_path / "snapshots.jsonl"
    write_portfolio(portfolio)
    seed_snapshot(ledger, "2026-11", 0.72, "2026-11-18T09:00:00+07:00")
    snapshots.write_text(json.dumps({
        "observed_on": "2026-11-18",
        "search_console": {
            "status": "CONNECTED",
            "window": {"start": "2026-10-19", "end": "2026-11-15"},
            "metrics": {"impressions": 4, "clicks": 1, "ctr": 0.25, "position": 8.0},
        },
    }) + "\n")

    payload = build_report(
        ledger, portfolio, as_of=date(2026, 11, 19), snapshots_path=snapshots,
    )

    assert payload["search_console"]["status"] == "AVAILABLE"
    assert payload["search_console"]["metrics"]["impressions"] == 4
