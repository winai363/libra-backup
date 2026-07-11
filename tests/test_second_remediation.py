import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import profit_agent
from business_ledger import ingest_uploaded_title_costs, portfolio_financials, record_kdp_snapshot
from profit_agent import check_policy, create_experiment, record_action_result
from scripts import libra_profit_agent_daily as daily


NOW = datetime(2026, 7, 11, 9, tzinfo=timezone.utc)


def snap(db, key, when, experiment_royalty, other_royalty=0):
    record_kdp_snapshot(db, {"source_key": key, "observed_at": when.isoformat(), "month": when.strftime("%Y-%m"),
        "overview": {"royalties_usd": experiment_royalty + other_royalty, "orders_all_types": 1, "kenp": 0},
        "titles": [{"asin": "EXP", "royalties_usd": experiment_royalty, "orders": 1, "kenp": 0},
                   {"asin": "OTHER", "royalties_usd": other_royalty, "orders": 1, "kenp": 0}]})


def test_cost_report_versions_select_current_cumulative_total(tmp_path):
    books = tmp_path / "kdp"; folder = books / "book"; folder.mkdir(parents=True)
    (folder / "listing.json").write_text(json.dumps({"status": "uploaded"}))
    report = folder / "cost-report.json"
    report.write_text('{"total_usd": 2}')
    db = tmp_path / "db"
    ingest_uploaded_title_costs(db, books, checked_at=NOW.isoformat())
    report.write_text('{  "total_usd" : 2.0 }\n')
    ingest_uploaded_title_costs(db, books, checked_at=(NOW + timedelta(hours=1)).isoformat())
    report.write_text('{"total_usd": 3}')
    ingest_uploaded_title_costs(db, books, checked_at=(NOW + timedelta(hours=2)).isoformat())
    assert portfolio_financials(db, "2026-07")["direct_costs_usd"] == 3
    with sqlite3.connect(db) as con:
        assert con.execute("select count(*) from cost_report_versions").fetchone()[0] == 2


def test_persisted_policy_is_enforced_exactly():
    action = {"kind": "metadata_update", "cost_usd": 0}
    policy = {"enabled": True, "started_at": NOW.isoformat(), "ends_at": (NOW + timedelta(days=1)).isoformat(),
              "allowed_action_kinds": ["category_update"]}
    assert check_policy(action, {"policy": policy, "now": NOW})[0] is False
    policy["allowed_action_kinds"] = ["metadata_update"]
    assert check_policy(action, {"policy": policy, "now": NOW})[0] is True
    policy["enabled"] = False
    assert check_policy(action, {"policy": policy, "now": NOW})[0] is False
    policy["enabled"] = True
    assert check_policy(action, {"policy": policy, "now": NOW + timedelta(days=1)})[0] is False


def test_action_conflict_and_terminal_retry_are_rejected(tmp_path):
    db = tmp_path / "db"; action = {"kind": "metadata_update", "cost_usd": 0, "action_key": "a", "attempt": 1}
    record_action_result(db, action, {"returncode": 1})
    with pytest.raises(ValueError, match="conflicting action replay"):
        record_action_result(db, action, {"returncode": 0, "confirmation_id": "ok"})
    executed = {**action, "action_key": "b"}
    record_action_result(db, executed, {"returncode": 0, "confirmation_id": "ok"})
    with pytest.raises(ValueError, match="terminal action"):
        record_action_result(db, {**executed, "attempt": 2}, {"returncode": 1})


def test_terminal_experiment_allows_new_same_slug_cycle_but_overlap_does_not(tmp_path):
    db = tmp_path / "db"
    first = create_experiment(db, slug="book", asin="EXP", variable="title", action={"kind": "metadata_update", "cost_usd": 0}, now=NOW)
    with pytest.raises(ValueError, match="active experiment"):
        create_experiment(db, slug="book", asin="EXP", variable="category", action={"kind": "category_update", "cost_usd": 0}, now=NOW)
    with sqlite3.connect(db) as con:
        con.execute("update experiments set status='lost' where id=?", (first["id"],))
    second = create_experiment(db, slug="book", asin="EXP", variable="category", action={"kind": "category_update", "cost_usd": 0}, now=NOW + timedelta(days=1))
    assert second["id"] != first["id"]


def test_one_post_change_comparison_is_at_most_one_positive_window():
    result = profit_agent.evaluate_experiment(
        {"baseline": {"royalties_usd": 1, "direct_costs_usd": 1, "contribution_profit_usd": 0, "complete": True},
         "success_threshold": {"min_contribution_delta_usd": .01}, "stop_threshold": {"max_contribution_delta_usd": -.01},
         "action_executed_at": NOW.isoformat()},
        {"royalties_usd": 3, "direct_costs_usd": 1, "contribution_profit_usd": 2, "complete": True},
        [1, 2], observed_at=NOW + timedelta(days=3))
    assert result["positive_contribution_windows"] == 1
    assert len(result["observation_windows"]) == 1


@pytest.mark.parametrize("executor_result,expected", [
    ({"returncode": 0, "verified_state_change": {"before": {"title": "old"}, "after": {"title": "new"}, "before_snapshot_id": 1, "after_snapshot_id": 2}}, "cooldown"),
    ({"returncode": 1, "stderr": "failed"}, "failed"),
    (None, "manual_required"),
])
def test_controller_consumes_action_result_end_to_end(tmp_path, monkeypatch, executor_result, expected):
    db = tmp_path / "db"; state = tmp_path / "state"
    books = tmp_path / "kdp"; folder = books / "adhd-self-help-adults-es"; folder.mkdir(parents=True)
    (folder / "listing.json").write_text(json.dumps({"status": "uploaded", "asin": "EXP"}))
    (folder / "cost-report.json").write_text('{"total_usd": 1}')
    monkeypatch.setattr(daily, "KDP_DIR", books)
    snap(db, "s1", NOW, 1)
    daily.run_daily(db, state, now=NOW)
    outcome = daily.run_daily(db, state, now=NOW + timedelta(minutes=1), executor=(lambda action: executor_result) if executor_result else None)
    experiment = next(e for e in outcome["experiments"] if e["slug"] == "adhd-self-help-adults-es")
    assert experiment["status"] == expected


def test_non_experiment_title_revenue_cannot_make_experiment_win(tmp_path):
    db = tmp_path / "db"
    snap(db, "s1", NOW, 1, 0)
    baseline = profit_agent.title_financial_boundary(db, "EXP", 1)
    snap(db, "s2", NOW + timedelta(days=4), 1, 20)
    current = profit_agent.title_financial_boundary(db, "EXP", 2)
    result = profit_agent.evaluate_experiment({"baseline": baseline,
        "success_threshold": {"min_contribution_delta_usd": .01}, "stop_threshold": {"max_contribution_delta_usd": -.01},
        "action_executed_at": NOW.isoformat()}, current, [1, 2], observed_at=NOW + timedelta(days=4))
    assert result["outcome"] != "won"


def test_two_distinct_cycles_for_same_title_prove_two_windows():
    from app import _checkpoint_outcomes

    checkpoints = _checkpoint_outcomes(
        NOW - timedelta(days=61), NOW,
        {"contribution_profit_usd": 2, "overhead_complete": False},
        {"snapshot_count": 3, "fresh": True, "overview_ingestion_complete": True},
        [
            {"slug": "book", "result": {"positive_contribution_windows": 1}},
            {"slug": "book", "result": {"positive_contribution_windows": 1}},
            {"slug": "other", "result": {"positive_contribution_windows": 1}},
        ],
    )
    assert checkpoints[1]["outcome"] == "passed"


def test_cooldown_can_advance_to_evaluating_after_deadline(tmp_path, monkeypatch):
    db = tmp_path / "db"; state = tmp_path / "state"
    books = tmp_path / "kdp"; folder = books / "adhd-self-help-adults-es"; folder.mkdir(parents=True)
    (folder / "listing.json").write_text(json.dumps({"status": "uploaded", "asin": "EXP"}))
    (folder / "cost-report.json").write_text('{"total_usd": 1}')
    monkeypatch.setattr(daily, "KDP_DIR", books)
    snap(db, "cooldown-s1", NOW, 2)
    daily.run_daily(db, state, now=NOW)
    with sqlite3.connect(db) as con:
        con.execute("update experiments set status='cooldown', earliest_evaluation_at=? where slug='adhd-self-help-adults-es'",
                    ((NOW - timedelta(minutes=1)).isoformat(),))
    result = daily.run_daily(db, state, now=NOW + timedelta(minutes=1))
    experiment = next(e for e in result["experiments"] if e["slug"] == "adhd-self-help-adults-es")
    assert experiment["status"] == "evaluating"


@pytest.mark.parametrize("first_result", [None, {"returncode": 1}])
def test_controller_recovers_manual_or_failed_action_with_second_attempt(tmp_path, monkeypatch, first_result):
    db = tmp_path / "db"; state = tmp_path / "state"
    books = tmp_path / "kdp"; folder = books / "adhd-self-help-adults-es"; folder.mkdir(parents=True)
    (folder / "listing.json").write_text(json.dumps({"status": "uploaded", "asin": "EXP"}))
    (folder / "cost-report.json").write_text('{"total_usd": 1}')
    monkeypatch.setattr(daily, "KDP_DIR", books)
    snap(db, "retry-s1", NOW, 2)
    daily.run_daily(db, state, now=NOW)
    daily.run_daily(db, state, now=NOW + timedelta(minutes=1),
                    executor=(lambda action: first_result) if first_result is not None else None)
    evidence = {"returncode": 0, "verified_state_change": {
        "before": {"title": "old"}, "after": {"title": "new"},
        "before_snapshot_id": 1, "after_snapshot_id": 2,
    }}
    result = daily.run_daily(db, state, now=NOW + timedelta(minutes=2), executor=lambda action: evidence)
    experiment = next(e for e in result["experiments"] if e["slug"] == "adhd-self-help-adults-es")
    assert experiment["status"] == "cooldown"
    with sqlite3.connect(db) as con:
        attempts = con.execute("select attempt,status from agent_actions where experiment_id=? and kind!='internal_transition' order by attempt",
                               (experiment["id"],)).fetchall()
    assert attempts[-1] == (2, "executed")


def test_executed_manual_completion_cannot_accept_third_attempt(tmp_path):
    db = tmp_path / "db"
    base = {"kind": "metadata_update", "slug": "book", "experiment_id": 1,
            "action_key": "manual:1", "cost_usd": 0}
    record_action_result(db, {**base, "attempt": 1}, {"returncode": 0})
    evidence = {"returncode": 0, "confirmation_id": "confirmed"}
    record_action_result(db, {**base, "attempt": 2, "manual_completion": True}, evidence)
    with pytest.raises(ValueError, match="terminal action"):
        record_action_result(db, {**base, "attempt": 3, "manual_completion": True}, evidence)


def test_production_manual_completion_command_records_evidence_and_cooldown(tmp_path):
    db = tmp_path / "db"; state = tmp_path / "state"
    profit_agent._init_schema(db)
    experiment = profit_agent.create_experiment(
        db, slug="book", asin="EXP", variable="metadata",
        action={"kind": "metadata_update", "cost_usd": 0}, now=NOW,
    )
    with sqlite3.connect(db) as con:
        con.execute("update experiments set status='manual_required' where id=?", (experiment["id"],))
    pending = profit_agent.create_pending_action(db, {**experiment, "status": "ready"}, NOW)
    record_action_result(db, pending, {"returncode": 0})
    evidence = {"returncode": 0, "verified_state_change": {
        "before": {"title": "old"}, "after": {"title": "new"},
        "before_snapshot_id": 1, "after_snapshot_id": 2,
    }}
    result = daily.complete_manual_action(db, "experiment:%s:%s" % (experiment["id"], experiment["cycle_key"]), evidence, now=NOW)
    assert result["status"] == "executed"
    with sqlite3.connect(db) as con:
        row = con.execute("select status,action_executed_at from experiments where id=?", (experiment["id"],)).fetchone()
    assert row[0] == "cooldown"
    assert row[1] == NOW.isoformat()
