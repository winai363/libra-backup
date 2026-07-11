import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from business_ledger import (
    ingest_uploaded_title_costs,
    portfolio_financials,
    record_kdp_snapshot,
)
from profit_agent import check_policy, evaluate_experiment, propose_transition
from profit_agent import record_action_result
from scripts.kdp_auto_manager import execute_free_actions


NOW = datetime(2026, 7, 11, 9, tzinfo=timezone.utc)


def _snapshot(royalties=1.0, titles=None):
    return {
        "source_key": "kdp:2026-07:report-1",
        "observed_at": NOW.isoformat(),
        "month": "2026-07",
        "overview": {"royalties_usd": royalties, "orders_all_types": 1, "kenp": 0},
        "titles": titles or [],
    }


def test_snapshot_replay_is_immutable_and_conflict_is_recorded(tmp_path):
    db = tmp_path / "ledger.db"
    first = record_kdp_snapshot(db, _snapshot())
    assert record_kdp_snapshot(db, _snapshot()) == first
    changed = _snapshot(2.0)
    with pytest.raises(ValueError, match="conflicting snapshot replay"):
        record_kdp_snapshot(db, changed)
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT royalties_usd FROM kdp_snapshots").fetchall() == [(1.0,)]
        assert connection.execute("SELECT COUNT(*) FROM snapshot_conflicts").fetchone()[0] == 1


def test_uploaded_cost_reports_are_idempotent_and_missing_cost_blocks_profit_claim(tmp_path):
    books = tmp_path / "kdp"
    for slug, cost in (("known", 2.0), ("missing", None)):
        folder = books / slug
        folder.mkdir(parents=True)
        (folder / "listing.json").write_text(json.dumps({"status": "uploaded", "asin": slug.upper()}))
        if cost is not None:
            (folder / "cost-report.json").write_text(json.dumps({"total_usd": cost}))
    db = tmp_path / "ledger.db"
    record_kdp_snapshot(db, _snapshot(1.0))
    assert ingest_uploaded_title_costs(db, books)["complete"] is False
    ingest_uploaded_title_costs(db, books)
    result = portfolio_financials(db, "2026-07")
    assert result["direct_costs_usd"] == 2.0
    assert result["contribution_profit_usd"] == -1.0
    assert result["cost_complete"] is False
    assert result["positive_contribution_proven"] is False


@pytest.mark.parametrize("action", [
    {"kind": "metadata_update"},
    {"kind": "metadata_update", "cost_usd": "free"},
    {"kind": "metadata_update", "cost_usd": -1},
    {"kind": "unknown", "cost_usd": 0},
])
def test_no_spend_mode_requires_allowlisted_explicit_zero_cost(action):
    allowed, _ = check_policy(action, {"no_spend": True})
    assert allowed is False


def test_experiment_transitions_require_action_and_external_before_after_evidence():
    ready = {"status": "ready", "action": {}}
    assert propose_transition(ready, {}, NOW) == ready
    executing = {"status": "executing", "evaluation_kind": "metadata"}
    assert propose_transition(executing, {}, NOW) == executing
    changed = propose_transition(executing, {"external_action": {
        "status": "executed", "before_snapshot_id": 1, "after_snapshot_id": 2,
        "before": {"title": "old"}, "after": {"title": "new"},
    }}, NOW)
    assert changed["status"] == "cooldown"


def test_deterministic_experiment_evaluation_covers_win_loss_and_incomplete():
    experiment = {"baseline": {"contribution_profit_usd": 1.0},
                  "success_threshold": {"min_contribution_delta_usd": 1.0},
                  "stop_threshold": {"max_contribution_delta_usd": -0.5}}
    won = evaluate_experiment(experiment, {"contribution_profit_usd": 2.5, "attribution_complete": True}, [1, 2])
    lost = evaluate_experiment(experiment, {"contribution_profit_usd": 0.0, "attribution_complete": True}, [1, 3])
    unclear = evaluate_experiment(experiment, {"contribution_profit_usd": 3.0, "attribution_complete": False}, [1, 4])
    assert (won["outcome"], won["positive_contribution_windows"]) == ("won", 2)
    assert lost["outcome"] == "lost"
    assert unclear["outcome"] == "inconclusive"


def test_legacy_free_promo_executor_is_disabled_without_spawning(monkeypatch):
    monkeypatch.setattr("scripts.kdp_auto_manager.subprocess.run", lambda *a, **k: pytest.fail("spawned"))
    state = {"agent": {"free_growth_engine": {"decisions": [
        {"execute": True, "action": "free_promo", "slug": "book"}
    ]}}}
    result = execute_free_actions(state)
    assert result[0]["status"] == "disabled"


def test_action_retry_identity_is_idempotent_and_attempts_are_linked(tmp_path):
    db = tmp_path / "ledger.db"
    action = {"kind": "metadata_update", "slug": "book", "experiment_id": 7,
              "action_key": "experiment:7:title", "attempt": 1}
    first = record_action_result(db, action, {"returncode": 1})
    replay = record_action_result(db, action, {"returncode": 1})
    second = record_action_result(db, {**action, "attempt": 2}, {"returncode": 1})
    assert first["id"] == replay["id"]
    assert second["id"] != first["id"]
    with pytest.raises(ValueError, match="between 1 and 3"):
        record_action_result(db, {**action, "attempt": 4}, {"returncode": 1})
