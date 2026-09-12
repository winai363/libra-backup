"""Unit tests for scripts/organic_experiment_report.py — a read-only report on
the organic experiment. The report must never invent a figure: a month KDP did
not attribute stays unknown, a click count of zero reads as INCONCLUSIVE rather
than failure, and the ledger is opened read-only."""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import organic_experiment_report as report_module  # noqa: E402

from business_ledger import init_ledger, record_hub_event  # noqa: E402
from content_hub import build_outbound_event  # noqa: E402


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "libra-business.db"
    init_ledger(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO kdp_snapshots(id, observed_at, month, royalties_usd, "
            "orders_all_types, kenp, raw_json, source_key, content_hash) "
            "VALUES (1, '2026-09-12T09:15:10+07:00', '2026-09', 13.81, 2, 205, '{}', 'k1', 'h1')"
        )
        connection.execute(
            "INSERT INTO kdp_snapshots(id, observed_at, month, royalties_usd, "
            "orders_all_types, kenp, raw_json, source_key, content_hash) "
            "VALUES (2, '2026-09-01T09:15:10+07:00', '2026-09', 9.00, 1, 100, '{}', 'k0', 'h0')"
        )
        connection.executemany(
            "INSERT INTO kdp_title_attribution(snapshot_id, asin, royalties_usd, orders_count, kenp) "
            "VALUES (?, ?, ?, ?, ?)",
            [(1, "B0BOOKONE01", 7.92, 3, 100), (2, "B0BOOKONE01", 4.00, 1, 40)],
        )
    return path


@pytest.fixture
def kdp_dir(tmp_path):
    path = tmp_path / "kdp"
    for slug, listing in {
        "book-one": {"asin": "B0BOOKONE01", "live_status": "LIVE"},
        "book-two": {"live_status": "LIVE"},  # never got an ASIN back from KDP
    }.items():
        (path / slug).mkdir(parents=True)
        (path / slug / "listing.json").write_text(json.dumps(listing))
    return path


def write_experiment(tmp_path, **overrides) -> Path:
    experiment = {
        "name": "organic-test",
        "active": True,
        "books": ["book-one", "book-two"],
        "started_on": "2026-09-01",
        "ends_on": "2026-10-01",
        "primary_metric": "tracked reader clicks per book per campaign",
        "continue_if": "at least 25 clicks",
        "stop_if": "zero clicks by day 14",
        "baseline": {"books": {"book-one": {"2026-08": {"royalties_usd": 1.44}}}},
    }
    experiment.update(overrides)
    path = tmp_path / "organic_experiment.json"
    path.write_text(json.dumps(experiment))
    return path


def build(tmp_path, ledger, kdp_dir, *, today=date(2026, 9, 12), **overrides) -> dict:
    return report_module.build_report(
        experiment_file=write_experiment(tmp_path, **overrides),
        ledger=ledger, kdp_dir=kdp_dir, today=today,
    )


def test_inactive_experiment_reports_inactive(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir, active=False)

    assert result["state"] == "inactive"
    assert "books" not in result


def test_missing_experiment_file_reports_inactive(tmp_path, ledger, kdp_dir):
    result = report_module.build_report(
        experiment_file=tmp_path / "absent.json", ledger=ledger, kdp_dir=kdp_dir)

    assert result["state"] == "inactive"


def test_missing_ledger_is_blocked_not_zero(tmp_path, kdp_dir):
    result = report_module.build_report(
        experiment_file=write_experiment(tmp_path), ledger=tmp_path / "absent.db",
        kdp_dir=kdp_dir)

    assert result["state"] == "blocked"
    assert "ledger_not_found" in result["reason"]


def test_royalties_use_the_latest_observation_per_month(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir)
    book_one = next(b for b in result["books"] if b["slug"] == "book-one")

    # 7.92 is the later observation; 4.00 + 7.92 would be double counting.
    assert book_one["royalties_by_month"] == {
        "2026-09": {"royalties_usd": 7.92, "orders": 3, "kenp": 100}
    }
    assert result["account_royalties_by_month"]["2026-09"]["royalties_usd"] == 13.81


def test_book_without_asin_reports_unknown_royalties(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir)
    book_two = next(b for b in result["books"] if b["slug"] == "book-two")

    assert book_two["asin"] is None
    assert book_two["royalties_by_month"] is None


def test_no_clicks_is_inconclusive(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir)

    assert result["tracked_clicks_total"] == 0
    assert result["verdict"] == "INCONCLUSIVE"


def test_clicks_are_grouped_by_campaign_and_counted(tmp_path, ledger, kdp_dir):
    record_hub_event(ledger, build_outbound_event("book-one", "organic-pinterest"))
    record_hub_event(ledger, build_outbound_event("book-one", "organic-pinterest"))
    record_hub_event(ledger, build_outbound_event("book-one", "organic-owner-post"))

    result = build(tmp_path, ledger, kdp_dir)
    book_one = next(b for b in result["books"] if b["slug"] == "book-one")

    assert book_one["clicks_by_campaign"] == {
        "organic-pinterest": {"amazon_outbound": 2},
        "organic-owner-post": {"amazon_outbound": 1},
    }
    assert result["tracked_clicks_total"] == 3
    assert result["verdict"] == "HAS_CLICK_DATA"


def test_clicks_before_the_window_are_excluded(tmp_path, ledger, kdp_dir):
    from datetime import datetime, timezone
    old = datetime(2026, 8, 1, tzinfo=timezone.utc)
    record_hub_event(ledger, build_outbound_event("book-one", "organic-pinterest", now=old))

    result = build(tmp_path, ledger, kdp_dir)

    assert result["tracked_clicks_total"] == 0


def test_window_closes_on_the_end_date(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir, today=date(2026, 10, 1))

    assert result["window_closed"] is True
    assert result["days_elapsed"] == 30


def test_purchases_are_never_attributed_to_a_click(tmp_path, ledger, kdp_dir):
    record_hub_event(ledger, build_outbound_event("book-one", "organic-pinterest"))

    result = build(tmp_path, ledger, kdp_dir)

    assert all(b["purchase_attribution"] == "not_attributable" for b in result["books"])
    assert "no purchase may be attributed" in result["attribution_note"]


def test_report_never_writes_to_the_ledger(tmp_path, ledger, kdp_dir):
    with sqlite3.connect(ledger) as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master").fetchone()[0]

    build(tmp_path, ledger, kdp_dir)

    with sqlite3.connect(ledger) as connection:
        after = connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
        events = connection.execute("SELECT COUNT(*) FROM hub_events").fetchone()[0]
    assert (before, events) == (after, 0)


def test_format_lines_renders_an_inactive_report(tmp_path, ledger, kdp_dir):
    text = report_module.format_lines(build(tmp_path, ledger, kdp_dir, active=False))

    assert "inactive" in text


def test_format_lines_renders_an_active_report(tmp_path, ledger, kdp_dir):
    record_hub_event(ledger, build_outbound_event("book-one", "organic-pinterest"))

    text = report_module.format_lines(build(tmp_path, ledger, kdp_dir))

    assert "organic-test" in text
    assert "book-one" in text
    assert "not_attributable" in text
