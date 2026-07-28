import pytest

from business_ledger import (
    growth_evidence,
    record_growth_evidence,
    record_growth_plan,
    record_hub_event,
)


def test_growth_evidence_is_idempotent_and_conflicts_fail(tmp_path):
    db = tmp_path / "ledger.db"
    item = {
        "source_key": "hub-click:abc", "kind": "hub_click", "slug": "book-a",
        "observed_at": "2026-07-29T09:00:00+00:00",
        "fresh_until": "2026-07-30T09:00:00+00:00",
        "confidence": 1.0, "payload": {"campaign": "organic-1"},
    }
    assert record_growth_evidence(db, item) == record_growth_evidence(db, item)
    with pytest.raises(ValueError, match="conflicting growth evidence"):
        record_growth_evidence(db, {**item, "payload": {"campaign": "changed"}})


def test_growth_evidence_confidence_int_and_float_replay_is_idempotent(tmp_path):
    db = tmp_path / "ledger.db"
    item = {
        "source_key": "hub-click:numeric", "kind": "hub_click", "slug": "book-a",
        "observed_at": "2026-07-29T09:00:00+00:00",
        "fresh_until": "2026-07-30T09:00:00+00:00",
        "confidence": 1, "payload": {"campaign": "organic-1"},
    }
    first = record_growth_evidence(db, item)
    second = record_growth_evidence(db, {**item, "confidence": 1.0})
    assert first == second


def test_growth_evidence_confidence_out_of_range_raises(tmp_path):
    db = tmp_path / "ledger.db"
    base = {
        "source_key": "hub-click:range", "kind": "hub_click", "slug": "book-a",
        "observed_at": "2026-07-29T09:00:00+00:00",
        "fresh_until": "2026-07-30T09:00:00+00:00",
        "payload": {},
    }
    with pytest.raises(ValueError, match="confidence"):
        record_growth_evidence(db, {**base, "source_key": "hub-click:high", "confidence": 1.1})
    with pytest.raises(ValueError, match="confidence"):
        record_growth_evidence(db, {**base, "source_key": "hub-click:low", "confidence": -0.1})


def test_growth_evidence_confidence_boundaries_are_accepted(tmp_path):
    db = tmp_path / "ledger.db"
    base = {
        "kind": "hub_click", "slug": "book-a",
        "observed_at": "2026-07-29T09:00:00+00:00",
        "fresh_until": "2026-07-30T09:00:00+00:00",
        "payload": {},
    }
    assert record_growth_evidence(db, {**base, "source_key": "hub-click:zero", "confidence": 0.0})
    assert record_growth_evidence(db, {**base, "source_key": "hub-click:one", "confidence": 1.0})


def test_growth_evidence_filters_by_slug_and_kind(tmp_path):
    db = tmp_path / "ledger.db"
    record_growth_evidence(db, {
        "source_key": "e1", "kind": "hub_click", "slug": "book-a",
        "observed_at": "2026-07-29T09:00:00+00:00", "fresh_until": "2026-07-30T09:00:00+00:00",
        "confidence": 0.5, "payload": {"n": 1},
    })
    record_growth_evidence(db, {
        "source_key": "e2", "kind": "ads_spend", "slug": "book-b",
        "observed_at": "2026-07-29T09:00:00+00:00", "fresh_until": "2026-07-30T09:00:00+00:00",
        "confidence": 0.5, "payload": {"n": 2},
    })

    assert [r["source_key"] for r in growth_evidence(db, slug="book-a")] == ["e1"]
    assert [r["source_key"] for r in growth_evidence(db, kind="ads_spend")] == ["e2"]
    assert len(growth_evidence(db)) == 2


def test_growth_plan_is_idempotent_and_conflicts_fail(tmp_path):
    db = tmp_path / "ledger.db"
    plan = {
        "action_key": "plan-1", "planned_at": "2026-07-29T09:00:00+00:00",
        "phase": "phase1", "status": "planned",
    }
    assert record_growth_plan(db, plan) == record_growth_plan(db, plan)
    with pytest.raises(ValueError, match="conflicting growth plan"):
        record_growth_plan(db, {**plan, "status": "done"})


def test_hub_event_is_idempotent_and_conflicts_fail(tmp_path):
    db = tmp_path / "ledger.db"
    event = {
        "event_key": "hub-1", "occurred_at": "2026-07-29T09:00:00+00:00",
        "slug": "book-a", "campaign": "organic-1", "event_kind": "click",
        "payload": {"ref": "reddit"},
    }
    assert record_hub_event(db, event) == record_hub_event(db, event)
    with pytest.raises(ValueError, match="conflicting hub event"):
        record_hub_event(db, {**event, "campaign": "organic-2"})
