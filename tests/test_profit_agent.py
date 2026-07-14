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
def test_executing_change_without_external_evidence_stays_executing(evaluation_kind, wait):
    experiment = {"status": "executing", "evaluation_kind": evaluation_kind}

    changed = propose_transition(experiment, {}, NOW)

    assert changed["status"] == "executing"


def test_commercial_change_without_external_evidence_stays_executing():
    experiment = {"status": "executing", "evaluation_kind": "commercial"}

    changed = propose_transition(experiment, {}, NOW)

    assert changed["status"] == "executing"


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
        {"verified_state_change": {"before": {"title": "old"}, "after": {"title": "new"}, "before_snapshot_id": 1, "after_snapshot_id": 2}},
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
        {"verified_state_change": True},
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


def test_title_boundary_treats_bounded_absence_as_attributed_zero(tmp_path: Path):
    # KDP's per-title table is the top-N earners widget: a zero-sale title has
    # no row by construction. While the snapshot's unattributed remainder is
    # within ATTRIBUTION_ABSENT_ZERO_BOUND_USD, absence = zero (complete, with
    # the bound recorded); above it the boundary must stay incomplete.
    from business_ledger import record_kdp_snapshot
    from profit_agent import ATTRIBUTION_ABSENT_ZERO_BOUND_USD, title_financial_boundary

    db = tmp_path / "ledger.db"
    bounded = record_kdp_snapshot(db, {
        "observed_at": NOW.isoformat(),
        "month": "2026-07",
        "overview": {"royalties_usd": 7.63, "orders_all_types": 4, "kenp": 20},
        "titles": [{"asin": "TOP", "royalties_usd": 6.9, "orders": 4, "kenp": 20}],
    })

    absent = title_financial_boundary(db, "ZERO-SALE", bounded, slug="zero-sale-book")

    assert absent["royalties_usd"] == 0.0
    assert absent["attribution_complete"] is True
    assert absent["attribution_bound_usd"] == 0.73
    assert 0.73 <= ATTRIBUTION_ABSENT_ZERO_BOUND_USD

    present = title_financial_boundary(db, "TOP", bounded, slug="top-book")
    assert present["royalties_usd"] == 6.9
    assert present["attribution_complete"] is True


def test_title_boundary_refuses_zero_attribution_above_the_bound(tmp_path: Path):
    from business_ledger import record_kdp_snapshot
    from profit_agent import title_financial_boundary

    db = tmp_path / "ledger.db"
    gappy = record_kdp_snapshot(db, {
        "observed_at": NOW.isoformat(),
        "month": "2026-07",
        "overview": {"royalties_usd": 7.63, "orders_all_types": 4, "kenp": 20},
        "titles": [{"asin": "TOP", "royalties_usd": 4.0, "orders": 4, "kenp": 20}],
    })

    absent = title_financial_boundary(db, "ZERO-SALE", gappy, slug="zero-sale-book")

    assert absent["attribution_complete"] is False
    assert absent["complete"] is False
