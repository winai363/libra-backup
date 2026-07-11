import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from profit_agent import (
    check_policy,
    create_initial_experiments,
    propose_transition,
    record_action_result,
)


NOW = datetime(2026, 7, 11, 9, 0, tzinfo=timezone.utc)


def test_paid_action_is_blocked_during_90_day_mode():
    allowed, reason = check_policy(
        {"kind": "amazon_ads", "cost_usd": 1}, {"no_spend": True}
    )
    assert allowed is False
    assert reason == "paid actions disabled during 90-day organic mode"


def test_paid_action_kind_is_blocked_even_when_cost_is_omitted():
    allowed, reason = check_policy({"kind": "paid_promotion"}, {"no_spend": True})
    assert allowed is False
    assert reason == "paid actions disabled during 90-day organic mode"


def test_fourth_active_experiment_is_blocked():
    allowed, reason = check_policy(
        {"kind": "start_experiment"}, {"active_experiments": 3}
    )
    assert allowed is False
    assert reason == "active experiment limit reached"


def test_unconfirmed_external_action_is_manual_required(tmp_path):
    db = tmp_path / "ledger.db"
    action = {"kind": "free_post", "slug": "adhd-self-help-adults-es"}
    result = record_action_result(db, action, {"returncode": 0})
    assert result["status"] == "manual_required"


def test_initial_registry_seeds_exact_approved_zero_cost_cohort(tmp_path: Path):
    db = tmp_path / "ledger.db"

    experiments = create_initial_experiments(db, NOW)

    assert {item["slug"] for item in experiments} == {
        "adhd-self-help-adults-es",
        "ai-augmented-productivity-toolkit",
        "acuarela-para-principiantes-guia-paso-a-paso",
    }
    assert len(experiments) == 3
    assert all(item["max_direct_cost_usd"] == 0 for item in experiments)
    assert all(item["status"] == "planned" for item in experiments)
    assert create_initial_experiments(db, NOW + timedelta(days=1)) == experiments

    with sqlite3.connect(db) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert {"experiments", "agent_actions"}.issubset(tables)


def test_initial_registry_does_not_return_unrelated_existing_experiment(tmp_path: Path):
    db = tmp_path / "ledger.db"
    create_initial_experiments(db, NOW)
    with sqlite3.connect(db) as connection:
        connection.execute(
            """
            INSERT INTO experiments (
                slug, hypothesis, variable, evaluation_kind, started_at,
                max_direct_cost_usd, status
            ) VALUES ('other-book', 'other', 'metadata', 'metadata', ?, 0, 'planned')
            """,
            (NOW.isoformat(),),
        )

    experiments = create_initial_experiments(db, NOW)

    assert len(experiments) == 3
    assert "other-book" not in {item["slug"] for item in experiments}


def test_one_variable_violation_is_blocked():
    allowed, reason = check_policy(
        {"kind": "start_experiment", "variables": ["title", "category"]},
        {"active_experiments": 0},
    )
    assert allowed is False
    assert reason == "experiments may change only one variable"


def test_second_variable_for_active_experiment_is_blocked():
    allowed, reason = check_policy(
        {"kind": "update_experiment", "variable": "category"},
        {"active_variable": "metadata"},
    )
    assert allowed is False
    assert reason == "experiments may change only one variable"


def test_title_in_cooldown_is_blocked():
    allowed, reason = check_policy(
        {"kind": "metadata", "slug": "book-a"},
        {"cooldown_slugs": ["book-a"]},
    )
    assert allowed is False
    assert reason == "title is already in cooldown"


def test_explicit_title_cooldown_flag_is_blocked():
    allowed, reason = check_policy(
        {"kind": "metadata", "slug": "book-a"}, {"title_in_cooldown": True}
    )
    assert allowed is False
    assert reason == "title is already in cooldown"


@pytest.mark.parametrize("evaluation_kind,wait", [("metadata", 72), ("category", 72)])
def test_executing_change_enters_72_hour_cooldown(evaluation_kind, wait):
    experiment = {"status": "executing", "evaluation_kind": evaluation_kind}

    changed = propose_transition(experiment, {}, NOW)

    assert changed["status"] == "cooldown"
    assert changed["earliest_evaluation_at"] == (NOW + timedelta(hours=wait)).isoformat()


def test_commercial_change_enters_14_day_cooldown():
    experiment = {"status": "executing", "evaluation_kind": "commercial"}

    changed = propose_transition(experiment, {}, NOW)

    assert changed["status"] == "cooldown"
    assert changed["earliest_evaluation_at"] == (NOW + timedelta(days=14)).isoformat()


def test_cooldown_does_not_evaluate_before_deadline():
    experiment = {
        "status": "cooldown",
        "earliest_evaluation_at": (NOW + timedelta(minutes=1)).isoformat(),
    }
    assert propose_transition(experiment, {}, NOW) == experiment


@pytest.mark.parametrize(
    "evaluation_kind,wait",
    [("metadata", timedelta(hours=72)), ("commercial", timedelta(days=14))],
)
def test_explicit_evaluating_target_cannot_bypass_cooldown(evaluation_kind, wait):
    experiment = {
        "status": "cooldown",
        "evaluation_kind": evaluation_kind,
        "earliest_evaluation_at": (NOW + wait).isoformat(),
    }

    assert propose_transition(experiment, {"target_status": "evaluating"}, NOW) == experiment


def test_invalid_transition_request_is_rejected():
    with pytest.raises(ValueError, match="invalid experiment transition"):
        propose_transition({"status": "planned"}, {"target_status": "won"}, NOW)


@pytest.mark.parametrize(
    "evidence",
    [
        {"confirmation_id": "post-1"},
        {"external_url": "https://example.com/post/1"},
        {"verified_state_change": True},
    ],
)
def test_required_external_evidence_marks_action_executed(tmp_path: Path, evidence):
    result = record_action_result(
        tmp_path / "ledger.db",
        {"kind": "free_post", "slug": "adhd-self-help-adults-es"},
        {"returncode": 0, **evidence},
    )
    assert result["status"] == "executed"
    assert result["evidence"] == evidence


@pytest.mark.parametrize(
    "evidence",
    [
        {"confirmation_identifier": "post-1"},
        {"confirmation_url": "https://example.com/post/1"},
        {"url": "https://example.com/post/1"},
        {"verified_state_change": "true"},
        {"verified_state_change": 1},
        {"verified_state_change": {"before": "draft", "after": "live"}},
        {"confirmation_id": ""},
        {"external_url": ""},
    ],
)
def test_unsupported_or_empty_evidence_requires_manual_action(tmp_path: Path, evidence):
    result = record_action_result(
        tmp_path / "ledger.db",
        {"kind": "free_post", "slug": "adhd-self-help-adults-es"},
        {"returncode": 0, **evidence},
    )
    assert result["status"] == "manual_required"
    assert result["evidence"] == {}


def test_failed_action_is_recorded_as_failed(tmp_path: Path):
    result = record_action_result(
        tmp_path / "ledger.db",
        {"kind": "free_post", "slug": "adhd-self-help-adults-es"},
        {"returncode": 1, "stderr": "network error"},
    )
    assert result["status"] == "failed"
