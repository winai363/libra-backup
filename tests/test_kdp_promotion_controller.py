"""Tests for kdp_promotion_controller — the paired, one-day KDP free-promo
lane. House rule this enforces (CLAUDE.md / memory.md, 14 Jul 2026): a free
promo without a verified external traffic channel measured 0 downloads in
13 of 13 cases. propose_promotion refuses anything that isn't a fresh
before-state + a verified external-post evidence item + no overlapping
experiment on the slug — and only ever proposes exactly one calendar day.
evaluate_promotion then closes the loop: a verified zero-download result
exhausts the cycle (matches the measured pattern) rather than being retried.
"""
from datetime import datetime, timedelta, timezone

import pytest

from kdp_promotion_controller import (
    evaluate_promotion,
    propose_promotion,
    reconcile_promotion,
)


def kdp_state(**overrides) -> dict:
    """A fresh, clean before-state: just observed, no overlapping
    experiment on the slug. Individual tests override fields to exercise
    the staleness/overlap gates."""
    state = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "live_status": "LIVE",
        "free_promo": None,
        "active_experiment": None,
    }
    state.update(overrides)
    return state


EVIDENCE_OK = [{"kind": "external_post", "post_url": "https://example.test/p/1"}]


# --- propose_promotion -------------------------------------------------

def test_free_promo_requires_verified_distribution_and_one_day():
    blocked = propose_promotion("book-a", kdp_state(), evidence=[])
    assert blocked["status"] == "blocked"
    allowed = propose_promotion(
        "book-a", kdp_state(),
        evidence=[{"kind": "external_post", "post_url": "https://example.test/p/1"}],
    )
    assert allowed["days"] == 1


def test_propose_promotion_allowed_carries_slug_and_status():
    allowed = propose_promotion("book-a", kdp_state(), evidence=EVIDENCE_OK)
    assert allowed["status"] == "allowed"
    assert allowed["slug"] == "book-a"


def test_propose_promotion_blocked_reason_for_empty_evidence():
    blocked = propose_promotion("book-a", kdp_state(), evidence=[])
    assert blocked["reason"] == "missing_verified_distribution_evidence"


def test_propose_promotion_rejects_evidence_without_kind_external_post():
    blocked = propose_promotion(
        "book-a", kdp_state(),
        evidence=[{"kind": "reminder", "post_url": "https://example.test/p/1"}],
    )
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "missing_verified_distribution_evidence"


def test_propose_promotion_rejects_placeholder_proof():
    blocked = propose_promotion(
        "book-a", kdp_state(),
        evidence=[{"kind": "external_post", "post_id": "pending"}],
    )
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "missing_verified_distribution_evidence"


def test_propose_promotion_accepts_verified_post_id():
    allowed = propose_promotion(
        "book-a", kdp_state(),
        evidence=[{"kind": "external_post", "post_id": "t3_abc123xyz"}],
    )
    assert allowed["status"] == "allowed"
    assert allowed["days"] == 1


def test_propose_promotion_requires_fresh_before_state():
    stale = kdp_state(observed_at=(datetime.now(timezone.utc) - timedelta(hours=6)).isoformat())
    blocked = propose_promotion("book-a", stale, evidence=EVIDENCE_OK)
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "missing_fresh_before_state"


def test_propose_promotion_requires_observed_at_present():
    missing = kdp_state()
    del missing["observed_at"]
    blocked = propose_promotion("book-a", missing, evidence=EVIDENCE_OK)
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "missing_fresh_before_state"


def test_propose_promotion_refuses_overlapping_experiment():
    state = kdp_state(active_experiment={"id": 7, "status": "executing"})
    blocked = propose_promotion("book-a", state, evidence=EVIDENCE_OK)
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "overlapping_experiment_on_slug"


def test_propose_promotion_rejects_future_dated_observed_at():
    # A future timestamp is bad data (clock skew, fabricated read) — must
    # fail closed exactly like a stale one, not be treated as "even fresher".
    future = kdp_state(observed_at=(datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat())
    blocked = propose_promotion("book-a", future, evidence=EVIDENCE_OK)
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "missing_fresh_before_state"


def test_propose_promotion_rejects_evidence_for_a_different_slug():
    # Evidence proving a channel for book B must never authorize a promo
    # for book A — the whole point of scoping evidence to the slug.
    evidence = [{"kind": "external_post", "post_url": "https://example.test/p/1", "slug": "book-b"}]
    blocked = propose_promotion("book-a", kdp_state(), evidence=evidence)
    assert blocked["status"] == "blocked"
    assert blocked["reason"] == "missing_verified_distribution_evidence"


def test_propose_promotion_accepts_evidence_with_matching_slug():
    evidence = [{"kind": "external_post", "post_url": "https://example.test/p/1", "slug": "book-a"}]
    allowed = propose_promotion("book-a", kdp_state(), evidence=evidence)
    assert allowed["status"] == "allowed"


# --- evaluate_promotion --------------------------------------------------

def test_zero_download_result_exhausts_cycle():
    assert evaluate_promotion({"downloads": 0, "verified": True})["allow_more_days"] is False


def test_evaluate_promotion_allows_more_days_on_verified_downloads():
    result = evaluate_promotion({"downloads": 12, "verified": True})
    assert result["allow_more_days"] is True


def test_evaluate_promotion_refuses_unverified_result():
    result = evaluate_promotion({"downloads": 12, "verified": False})
    assert result["allow_more_days"] is False
    assert result["reason"] == "unverified_result"


# --- reconcile_promotion --------------------------------------------------

class FakeAdapter:
    """Test double for the browser/KDP adapter — mirrors distribution_executor's
    FakeAdapter (tests/test_distribution_executor.py): reconcile_promotion
    calls adapter.publish(action) and never trusts the response as success
    on its own."""

    def __init__(self, response):
        self.response = response

    def publish(self, action):
        return self.response


class RaisingAdapter:
    def publish(self, action):
        raise RuntimeError("browser session crashed")


def test_reconcile_promotion_executed_with_verified_state_change():
    result = reconcile_promotion(
        {"slug": "book-a", "days": 1},
        adapter=FakeAdapter({
            "returncode": 0,
            "confirmation_id": "kdp-free-promo:book-a:2026-07-29:2026-07-29",
            "external_url": "https://kdpreports.amazon.com/",
            "verified_state_change": {
                "before": {"status": "none"},
                "after": {"status": "Scheduled", "start": "2026-07-29", "end": "2026-07-29"},
            },
        }),
    )
    assert result["status"] == "executed"
    assert result["verified_state_change"]["after"]["status"] == "Scheduled"


def test_reconcile_promotion_unverified_after_state_is_manual_required():
    result = reconcile_promotion(
        {"slug": "book-a", "days": 1},
        adapter=FakeAdapter({"returncode": 1, "error": "could not verify a Scheduled promo"}),
    )
    assert result["status"] == "manual_required"
    assert result["reason"] == "could not verify a Scheduled promo"


def test_reconcile_promotion_auth_barrier_is_manual_required():
    result = reconcile_promotion(
        {"slug": "book-a", "days": 1},
        adapter=FakeAdapter({"login_required": True}),
    )
    assert result["status"] == "manual_required"
    assert result["reason"] == "auth_barrier"


def test_reconcile_promotion_policy_rejection_is_blocked():
    result = reconcile_promotion(
        {"slug": "book-a", "days": 1},
        adapter=FakeAdapter({"policy_rejected": True}),
    )
    assert result["status"] == "blocked"
    assert result["reason"] == "policy_rejected"


def test_reconcile_promotion_adapter_exception_is_manual_required():
    result = reconcile_promotion({"slug": "book-a", "days": 1}, adapter=RaisingAdapter())
    assert result["status"] == "manual_required"
    assert result["reason"] == "adapter_error"


def test_reconcile_promotion_never_invents_success_without_before_state():
    # A response missing the "before" half of verified_state_change is not
    # proof of anything — must fail closed, same as an ordinary error.
    result = reconcile_promotion(
        {"slug": "book-a", "days": 1},
        adapter=FakeAdapter({
            "returncode": 0,
            "verified_state_change": {"after": {"status": "Scheduled"}},
        }),
    )
    assert result["status"] == "manual_required"


# --- KdpPromotionAdapter hook in scripts/kdp_action_executor.py ----------

def _pair_slug(tmp_path, monkeypatch, executor_module, slug):
    """Same pairing helper as tests/test_kdp_action_executor.py's _pair_slug
    — stamp a verified reddit_promo_schedule.json entry so validate_action's
    has_distribution_pairing check passes for `slug`."""
    import json

    schedule = tmp_path / "reddit_promo_schedule.json"
    schedule.write_text(json.dumps({"posts": [
        {"slug": slug, "post_url": f"https://example.com/{slug}"}
    ]}))
    monkeypatch.setattr(executor_module, "PAIRINGS_FILE", tmp_path / "missing_pairings.json")
    monkeypatch.setattr(executor_module, "REDDIT_SCHEDULE_FILE", schedule)


def test_kdp_promotion_adapter_reuses_existing_free_promo_browser_flow(tmp_path, monkeypatch):
    """The additive hook (scripts.kdp_action_executor.KdpPromotionAdapter)
    must route through the SAME _execute_free_promo audit flow used by the
    existing free_promo action lane — never a new browser path — and never
    make a real call in tests (the coroutine itself is monkeypatched). It
    must ALSO run validate_action's pairing gate first — this test pairs
    the slug so that gate passes and the browser flow is reached."""
    import json

    import scripts.kdp_action_executor as executor_module

    kdp_dir = tmp_path / "kdp" / "book-a"
    kdp_dir.mkdir(parents=True)
    listing = {"kdp_book_id": "B01", "title": "Book A", "free_promo": None}
    (kdp_dir / "listing.json").write_text(json.dumps(listing))
    monkeypatch.setattr(executor_module, "KDP_DIR", tmp_path / "kdp")
    _pair_slug(tmp_path, monkeypatch, executor_module, "book-a")

    captured = {}

    async def fake_execute_free_promo(action, listing_arg, days):
        captured["action"] = action
        captured["days"] = days
        return {
            "returncode": 0,
            "confirmation_id": "kdp-free-promo:book-a:x",
            "external_url": "https://kdpreports.amazon.com/",
            "verified_state_change": {"before": {"status": "none"}, "after": {"status": "Scheduled"}},
        }

    monkeypatch.setattr(executor_module, "_execute_free_promo", fake_execute_free_promo)

    adapter = executor_module.KdpPromotionAdapter()
    result = adapter.publish({"slug": "book-a", "days": 1})

    assert result["verified_state_change"]["after"]["status"] == "Scheduled"
    assert captured["days"] == 1


def test_kdp_promotion_adapter_missing_listing_fails_closed(monkeypatch, tmp_path):
    import scripts.kdp_action_executor as executor_module

    monkeypatch.setattr(executor_module, "KDP_DIR", tmp_path / "kdp")
    adapter = executor_module.KdpPromotionAdapter()
    result = adapter.publish({"slug": "no-such-book", "days": 1})
    assert result["returncode"] == 1
    assert "no listing.json" in result["error"]


def test_kdp_promotion_adapter_blocks_unpaired_promo_via_validate_action(tmp_path, monkeypatch):
    """IMPORTANT fix: publish() must run validate_action (and therefore
    has_distribution_pairing, the production file-based pairing gate)
    BEFORE ever touching the browser. An unpaired slug must be refused here
    exactly as it would be for the legacy free_promo action kind — never
    silently bypassed just because this is the new controller's adapter."""
    import json

    import scripts.kdp_action_executor as executor_module

    kdp_dir = tmp_path / "kdp" / "unpaired-book"
    kdp_dir.mkdir(parents=True)
    listing = {"kdp_book_id": "B02", "title": "Unpaired Book", "free_promo": None}
    (kdp_dir / "listing.json").write_text(json.dumps(listing))
    monkeypatch.setattr(executor_module, "KDP_DIR", tmp_path / "kdp")
    # No pairing stamped anywhere for this slug.
    monkeypatch.setattr(executor_module, "PAIRINGS_FILE", tmp_path / "missing_pairings.json")
    monkeypatch.setattr(executor_module, "REDDIT_SCHEDULE_FILE", tmp_path / "missing_schedule.json")

    def fail_if_called(*a, **k):
        pytest.fail("_execute_free_promo must not run when validate_action refuses")

    monkeypatch.setattr(executor_module, "_execute_free_promo", fail_if_called)

    adapter = executor_module.KdpPromotionAdapter()
    response = adapter.publish({"slug": "unpaired-book", "days": 1})
    assert response.get("policy_rejected") is True

    result = reconcile_promotion({"slug": "unpaired-book", "days": 1}, adapter=adapter)
    assert result["status"] == "blocked"
