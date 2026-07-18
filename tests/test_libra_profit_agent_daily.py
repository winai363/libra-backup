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

    assert state["gates"]["overview_ingestion"] == "open"
    assert state["gates"]["title_attribution"] == "open"
    assert all(item["status"] == "ready" for item in state["experiments"])
    assert state_path.exists()
    assert state["mode_started_at"] == NOW.isoformat()
    assert state["pace"]["stretch_target"] == 82.5
    assert state["pace"]["mode"] == "insufficient_data"
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM agent_actions").fetchone()[0] == 3


def test_mode_start_is_preserved_across_controller_runs(tmp_path):
    db = tmp_path / "ledger.db"
    state_path = tmp_path / "state.json"
    _snapshot(db)

    first = run_daily(db, state_path, now=NOW)
    second = run_daily(db, state_path, now=NOW + timedelta(days=5))

    assert second["mode_started_at"] == first["mode_started_at"] == NOW.isoformat()


def test_stale_financials_hold_experiments(tmp_path):
    stale_db = tmp_path / "stale.db"
    _snapshot(stale_db, observed_at=NOW - timedelta(days=2))
    stale = run_daily(stale_db, tmp_path / "stale.json", now=NOW)
    assert stale["gates"]["freshness"] == "closed"
    assert all(item["status"] == "planned" for item in stale["experiments"])



def test_attribution_gap_allows_observation_but_blocks_commercial_mutation(tmp_path):
    # Unattributed remainder above ATTRIBUTION_ABSENT_ZERO_BOUND_USD: an absent
    # title could be hiding real money, so ready experiments must hold.
    gap_db = tmp_path / "gap.db"
    _snapshot(gap_db, attributed=4.0)
    first = run_daily(gap_db, tmp_path / "gap.json", now=NOW)

    assert first["gates"]["overview_ingestion"] == "open"
    assert first["gates"]["title_attribution"] == "partial"
    assert all(item["status"] == "ready" for item in first["experiments"])
    assert (tmp_path / "gap.json").exists()

    second = run_daily(gap_db, tmp_path / "gap.json", now=NOW + timedelta(minutes=1))

    assert all(item["status"] == "ready" for item in second["experiments"])


def test_bounded_attribution_gap_does_not_deadlock_absent_titles(tmp_path):
    # Regression (found 2026-07-14): KDP's top-N widget only lists titles with
    # earning activity, so zero-sale titles NEVER get an attribution row. The
    # old presence-only gate held their experiments "ready" forever. With the
    # remainder within the zero bound, absence counts as attributed-zero and
    # the experiment must advance out of "ready" on the next run.
    db = tmp_path / "ledger.db"
    _snapshot(db, attributed=6.9)  # remainder 0.73 <= 2.00 bound
    first = run_daily(db, tmp_path / "state.json", now=NOW)
    assert all(item["status"] == "ready" for item in first["experiments"])

    second = run_daily(db, tmp_path / "state.json", now=NOW + timedelta(minutes=1))

    assert all(item["status"] != "ready" for item in second["experiments"])


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

    seeded = [item for item in state["experiments"] if item["slug"] != "existing-active"]
    assert seeded and all(item["status"] == "planned" for item in seeded)
    assert all(
        item["policy_reason"] == "active experiment limit reached"
        for item in seeded
    )
    # The over-capacity row itself is now part of the registry and holds state.
    extra = [item for item in state["experiments"] if item["slug"] == "existing-active"]
    assert extra and extra[0]["status"] == "ready"


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
    assert by_slug[blocked_slug]["policy_reason"] == "allowed"
    assert all(
        item["status"] == "ready"
        for slug, item in by_slug.items()
        if slug != blocked_slug
    )


def test_proposer_created_experiments_are_processed(tmp_path):
    from datetime import timezone as _tz

    from profit_agent import create_experiment

    db = tmp_path / "ledger.db"
    state_path = tmp_path / "state.json"
    _snapshot(db)
    create_experiment(
        db, slug="proposer-book", asin="PX1", variable="promotion",
        action={"kind": "free_promo", "cost_usd": 0,
                "proposed_value": "2-day KDP Select free promotion"},
        now=NOW,
    )
    # Retire the seeded APPROVED experiments so capacity can't mask the
    # registry behavior under test.
    create_initial_experiments(db, NOW)
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE experiments SET status='inconclusive' WHERE slug != 'proposer-book'"
        )

    state = run_daily(db, state_path, now=NOW)

    proposer_rows = [e for e in state["experiments"] if e["slug"] == "proposer-book"]
    assert proposer_rows, "non-approved-slug experiment missing from the daily registry"
    assert proposer_rows[0]["status"] != "planned", "proposer experiment never advanced"
