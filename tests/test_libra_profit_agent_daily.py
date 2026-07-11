import sqlite3
from datetime import datetime, timedelta, timezone

from business_ledger import record_kdp_snapshot
from scripts.libra_profit_agent_daily import run_daily


NOW = datetime(2026, 7, 11, 9, 30, tzinfo=timezone.utc)


def _snapshot(db, *, observed_at=NOW, attributed=7.63):
    record_kdp_snapshot(db, {
        "observed_at": observed_at.isoformat(),
        "month": "2026-07",
        "overview": {"royalties_usd": 7.63, "orders_all_types": 4, "kenp": 20},
        "titles": [{"asin": "A", "royalties_usd": attributed, "orders": 4, "kenp": 20}],
    })


def test_dry_run_has_zero_writes(tmp_path):
    db = tmp_path / "ledger.db"
    state_path = tmp_path / "state.json"

    state = run_daily(db, state_path, now=NOW, dry_run=True)

    assert state["mode"] == "dry_run"
    assert not db.exists()
    assert not state_path.exists()


def test_dry_run_does_not_mutate_existing_ledger(tmp_path):
    db = tmp_path / "ledger.db"
    _snapshot(db)
    with sqlite3.connect(db) as connection:
        before = connection.total_changes
        tables_before = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()

    run_daily(db, tmp_path / "state.json", now=NOW, dry_run=True)

    with sqlite3.connect(db) as connection:
        tables_after = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    assert before == 0
    assert tables_after == tables_before
    assert ("experiments",) not in tables_after
    assert ("agent_actions",) not in tables_after


def test_fresh_reconciled_ledger_advances_and_audits_experiments(tmp_path):
    db = tmp_path / "ledger.db"
    state_path = tmp_path / "state.json"
    _snapshot(db)

    state = run_daily(db, state_path, now=NOW)

    assert state["gates"] == {"policy": "open", "freshness": "open", "reconciliation": "open"}
    assert all(item["status"] == "ready" for item in state["experiments"])
    assert state_path.exists()
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_actions").fetchone()[0] == 3


def test_stale_or_unreconciled_financials_hold_experiments(tmp_path):
    stale_db = tmp_path / "stale.db"
    _snapshot(stale_db, observed_at=NOW - timedelta(days=2))
    stale = run_daily(stale_db, tmp_path / "stale.json", now=NOW)
    assert stale["gates"]["freshness"] == "closed"
    assert all(item["status"] == "planned" for item in stale["experiments"])

    gap_db = tmp_path / "gap.db"
    _snapshot(gap_db, attributed=6.0)
    gap = run_daily(gap_db, tmp_path / "gap.json", now=NOW)
    assert gap["gates"]["reconciliation"] == "closed"
    assert all(item["status"] == "planned" for item in gap["experiments"])
