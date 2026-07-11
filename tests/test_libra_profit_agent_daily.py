import sqlite3
from datetime import datetime, timedelta, timezone

from business_ledger import record_kdp_snapshot
from profit_agent import create_initial_experiments
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


def test_stale_financials_hold_experiments(tmp_path):
    stale_db = tmp_path / "stale.db"
    _snapshot(stale_db, observed_at=NOW - timedelta(days=2))
    stale = run_daily(stale_db, tmp_path / "stale.json", now=NOW)
    assert stale["gates"]["freshness"] == "closed"
    assert all(item["status"] == "planned" for item in stale["experiments"])



def test_attribution_gap_allows_observation_but_blocks_commercial_mutation(tmp_path):
    gap_db = tmp_path / "gap.db"
    _snapshot(gap_db, attributed=6.0)
    first = run_daily(gap_db, tmp_path / "gap.json", now=NOW)

    assert first["gates"]["reconciliation"] == "closed"
    assert all(item["status"] == "ready" for item in first["experiments"])
    assert (tmp_path / "gap.json").exists()

    second = run_daily(gap_db, tmp_path / "gap.json", now=NOW + timedelta(minutes=1))

    assert all(item["status"] == "ready" for item in second["experiments"])


def test_persisted_active_capacity_blocks_each_new_transition(tmp_path):
    db = tmp_path / "capacity.db"
    _snapshot(db)
    create_initial_experiments(db, NOW)
    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            INSERT INTO experiments (
                slug, hypothesis, variable, evaluation_kind, started_at,
                max_direct_cost_usd, status
            ) VALUES ('existing-active', 'existing', 'metadata', 'metadata', ?, 0, 'ready')
            """,
            (NOW.isoformat(),),
        )

    state = run_daily(db, tmp_path / "capacity.json", now=NOW)

    assert all(item["status"] == "planned" for item in state["experiments"])
    assert all(
        item["policy_reason"] == "active experiment limit reached"
        for item in state["experiments"]
    )


def test_persisted_title_cooldown_blocks_only_conflicting_experiment(tmp_path):
    db = tmp_path / "cooldown.db"
    _snapshot(db)
    experiments = create_initial_experiments(db, NOW)
    blocked_slug = experiments[0]["slug"]
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE experiments SET status = 'cooldown', earliest_evaluation_at = ? WHERE slug = ?",
            ((NOW + timedelta(days=1)).isoformat(), blocked_slug),
        )

    state = run_daily(db, tmp_path / "cooldown.json", now=NOW)

    by_slug = {item["slug"]: item for item in state["experiments"]}
    assert by_slug[blocked_slug]["status"] == "cooldown"
    assert by_slug[blocked_slug]["policy_reason"] == "title is already in cooldown"
    assert all(
        item["status"] == "ready"
        for slug, item in by_slug.items()
        if slug != blocked_slug
    )
