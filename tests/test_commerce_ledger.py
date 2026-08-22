"""Immutable commerce inbox and money-safe schema."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from business_ledger import init_ledger
from commerce_ledger import (
    commerce_event,
    mark_provider_event,
    record_provider_event,
)


def _event(event_id="evt_1", payload_hash="abc", provider="stripe"):
    return {
        "provider": provider,
        "event_id": event_id,
        "event_type": "payment_intent.succeeded",
        "occurred_at": "2026-08-21T10:00:00+00:00",
        "received_at": "2026-08-21T10:00:01+00:00",
        "mode": "test",
        "verification_state": "verified",
        "payload_hash": payload_hash,
        "sanitized_payload": {"id": event_id},
    }


def test_provider_event_replay_is_idempotent_and_conflict_is_immutable(tmp_path):
    db = tmp_path / "ledger.db"
    assert record_provider_event(db, _event())["status"] == "inserted"
    assert record_provider_event(db, _event())["status"] == "duplicate"
    assert record_provider_event(db, _event(payload_hash="changed"))["status"] == "conflict"
    assert commerce_event(db, "stripe", "evt_1")["payload_hash"] == "abc"
    with sqlite3.connect(db) as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM commerce_event_conflicts"
        ).fetchone()[0] == 1
        assert connection.execute(
            "SELECT COUNT(*) FROM commerce_incidents WHERE severity='critical'"
        ).fetchone()[0] == 1


def test_same_event_id_across_providers_is_not_a_conflict(tmp_path):
    db = tmp_path / "ledger.db"
    assert record_provider_event(db, _event())["status"] == "inserted"
    assert record_provider_event(db, _event(provider="payhip"))["status"] == "inserted"
    assert commerce_event(db, "payhip", "evt_1")["provider"] == "payhip"


def test_commerce_money_columns_are_integer_and_currency_is_required(tmp_path):
    db = tmp_path / "ledger.db"
    init_ledger(db)
    with sqlite3.connect(db) as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='commerce_orders'"
        ).fetchone()[0]
    assert "gross_minor INTEGER" in sql
    assert "currency TEXT NOT NULL" in sql
    assert "REAL" not in sql


def test_every_commerce_table_exists_after_init(tmp_path):
    db = tmp_path / "ledger.db"
    init_ledger(db)
    with sqlite3.connect(db) as connection:
        names = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {
        "commerce_events",
        "commerce_event_conflicts",
        "commerce_products",
        "commerce_orders",
        "commerce_refunds",
        "stripe_balance_transactions",
        "commerce_payouts",
        "commerce_payout_items",
        "commerce_incidents",
    } <= names


def test_mark_provider_event_tracks_processing_state(tmp_path):
    db = tmp_path / "ledger.db"
    record_provider_event(db, _event())
    assert commerce_event(db, "stripe", "evt_1")["processing_state"] == "received"

    mark_provider_event(db, "stripe", "evt_1", "pending_reconciliation", error_code="no_order")
    row = commerce_event(db, "stripe", "evt_1")
    assert row["processing_state"] == "pending_reconciliation"
    assert row["error_code"] == "no_order"

    mark_provider_event(db, "stripe", "evt_1", "reconciled")
    row = commerce_event(db, "stripe", "evt_1")
    assert row["processing_state"] == "reconciled"
    assert row["error_code"] is None


def test_sanitized_payload_round_trips_and_raw_body_is_never_stored(tmp_path):
    db = tmp_path / "ledger.db"
    record_provider_event(db, _event())
    row = commerce_event(db, "stripe", "evt_1")
    assert row["sanitized_payload"] == {"id": "evt_1"}
    with sqlite3.connect(db) as connection:
        columns = {
            r[1] for r in connection.execute("PRAGMA table_info(commerce_events)")
        }
    assert "raw_body" not in columns


def test_unknown_event_returns_none(tmp_path):
    db = tmp_path / "ledger.db"
    init_ledger(db)
    assert commerce_event(db, "stripe", "missing") is None


def test_live_events_are_accepted_and_test_events_stay_separable(tmp_path):
    """Both modes are storable; the mode column keeps them distinguishable."""
    db = tmp_path / "ledger.db"
    live = {**_event(event_id="evt_live_1"), "mode": "live"}

    assert record_provider_event(db, live)["status"] == "inserted"
    assert commerce_event(db, "stripe", "evt_live_1")["mode"] == "live"

    assert record_provider_event(db, _event(event_id="evt_test_1"))["status"] == "inserted"
    assert commerce_event(db, "stripe", "evt_test_1")["mode"] == "test"


def test_an_unknown_mode_is_still_refused(tmp_path):
    db = tmp_path / "ledger.db"
    try:
        record_provider_event(db, {**_event(), "mode": "sandbox"})
    except ValueError as exc:
        assert "mode" in str(exc)
    else:
        raise AssertionError("unknown mode must be refused")
