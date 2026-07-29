"""Tests for growth_autopilot — the single controller that composes
collection, readiness, scoring, planning, authorization, and execution for
the Libra Growth Autopilot. Two tests below are verbatim from the Task 9
brief; the rest cover lock contention, shadow-vs-execute divergence, an
authorize-refusal path, and state-file atomicity, per the task instructions.
"""
from __future__ import annotations

import fcntl
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from business_ledger import growth_evidence, init_ledger, record_growth_evidence
from growth_autopilot import (
    build_growth_digest,
    build_growth_gate_report,
    collect_growth_observations,
    format_growth_gate_report,
    growth_authority_transferred,
    run_growth_controller,
    verify_growth_state,
)
from growth_policy import ads_eligibility
from scripts.libra_growth_autopilot import _default_titles, _persisted_started_at

NOW = datetime(2026, 7, 29, 9, 0, tzinfo=timezone.utc)
ORGANIC_STARTED_AT = NOW - timedelta(days=5)  # well inside the 30-day organic window
GROWTH_STARTED_AT = NOW - timedelta(days=40)  # past day 31 — Growth Gate window open


def _seed_evidence(db_path: Path) -> None:
    """A collector always has SOMETHING on record — seed one real evidence
    row so observations_collected is genuinely > 0, not an artifact of an
    empty ledger."""
    record_growth_evidence(db_path, {
        "source_key": "test-seed:book-a",
        "kind": "hub_click",
        "slug": "book-a",
        "observed_at": NOW.isoformat(),
        "fresh_until": (NOW + timedelta(days=30)).isoformat(),
        "confidence": 1.0,
        "payload": {},
    })


def _untested_title(slug: str = "book-a") -> dict:
    """All-zero verified signals, fewer than 3 placements — scores 0 and
    classifies "test" (portfolio_scorer.score_title), which is exactly what
    growth_planner proposes an organic_test action for."""
    return {
        "slug": slug, "royalty_delta_usd": 0, "kenp_delta": 0,
        "tracked_clicks": 0, "conversion_signal": 0, "verified_placements": 0,
        "risk_active": False,
    }


def config(tmp_path, *, incidents=None, started_at=ORGANIC_STARTED_AT, **overrides) -> dict:
    db_path = tmp_path / "ledger.db"
    init_ledger(db_path)
    _seed_evidence(db_path)
    base = {
        "ledger_path": db_path,
        "lock_path": tmp_path / "growth-autopilot.lock",
        "state_path": tmp_path / "growth-autopilot-state.json",
        "started_at": started_at,
        "titles": [_untested_title()],
        "active_experiments": [],
        "incidents": incidents or [],
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Verbatim tests from the Task 9 brief.
# ---------------------------------------------------------------------------

def test_shadow_mode_writes_plan_but_executes_nothing(tmp_path):
    state = run_growth_controller(config(tmp_path), now=NOW, shadow=True)
    assert state["mode"] == "shadow"
    assert state["executed"] == []
    assert state["plan"]["actions"]


def test_account_incident_stops_mutation_but_keeps_collection(tmp_path):
    cfg = config(tmp_path, incidents=[{"severity": "critical", "scope": "account", "detail": {}}])
    state = run_growth_controller(cfg, now=NOW, shadow=False)
    assert state["readiness"]["mutation_allowed"] is False
    assert state["observations_collected"] > 0
    assert state["executed"] == []


# ---------------------------------------------------------------------------
# Shadow-vs-execute divergence: the same config, actually exercised through
# a full translate -> authorize -> execute -> reconcile -> record cycle for
# a price_update action, via an injected fake executor standing in for
# scripts/kdp_action_executor.py's real build_executor().
# ---------------------------------------------------------------------------

def _fake_price_executor(calls):
    def executor(pending):
        calls.append(pending)
        return {
            "returncode": 0,
            "confirmation_id": "kdp-price-update:book-a:2.99",
            "verified_state_change": {
                "before": {"price": 9.99}, "after": {"price": 2.99},
                "before_snapshot_id": 1, "after_snapshot_id": 2,
            },
        }
    return executor


def test_shadow_vs_execute_diverge_on_the_same_config(tmp_path):
    calls = []
    cfg = config(tmp_path, price_proposals={"book-a": 2.99}, adapters={"price_executor": _fake_price_executor(calls)})

    shadow_state = run_growth_controller(cfg, now=NOW, shadow=True)
    assert shadow_state["mode"] == "shadow"
    assert shadow_state["executed"] == []
    assert calls == []  # the adapter must never be touched in shadow mode

    execute_state = run_growth_controller(cfg, now=NOW, shadow=False)
    assert execute_state["mode"] == "execute"
    assert len(execute_state["executed"]) == 1
    executed = execute_state["executed"][0]
    assert executed["slug"] == "book-a"
    assert executed["kind"] == "price_update"
    assert executed["status"] == "executed"
    assert calls  # the adapter WAS called this time

    # Reconciled evidence is recorded to the ledger, source-keyed so a same-
    # day replay is idempotent rather than a duplicate row.
    recorded = growth_evidence(cfg["ledger_path"], slug="book-a", kind="price_update_executed")
    assert len(recorded) == 1


def test_execute_mode_with_no_adapter_configured_is_manual_required_not_executed(tmp_path):
    cfg = config(tmp_path, price_proposals={"book-a": 2.99})  # no adapters at all
    state = run_growth_controller(cfg, now=NOW, shadow=False)
    assert state["executed"] == []
    assert any(item["slug"] == "book-a" and item["reason"] == "no_price_executor_configured" for item in state["blocked"])


# ---------------------------------------------------------------------------
# Authorize-refusal path: the plan proposes a price_update action, but the
# planned variable has no wired controller ("distribution" is not one of
# VARIABLE_ACTION_KIND's entries) — the action must be refused BEFORE ever
# reaching authorize_growth_action/check_policy, not silently dropped.
# ---------------------------------------------------------------------------

def test_unmapped_variable_is_refused_before_authorization_not_silently_dropped(tmp_path):
    cfg = config(tmp_path, active_experiments=[{"slug": "book-a", "variable": "distribution"}])
    state = run_growth_controller(cfg, now=NOW, shadow=False)
    assert state["executed"] == []
    assert any(
        item["slug"] == "book-a" and item["reason"] == "unsupported_growth_action_variable"
        for item in state["blocked"]
    )


def test_growth_action_context_never_carries_stale_no_spend_or_policy_keys(tmp_path):
    """Regression guard for the Task-8 review note: a context built for
    profit_agent.check_policy's opted-in growth path must never carry
    "policy"/"no_spend" — either would fall through to the legacy 90-day
    gate and re-deny an action growth_policy already authorized."""
    from growth_autopilot import _growth_action_context
    context = _growth_action_context(GROWTH_STARTED_AT, {"now": NOW})
    assert "no_spend" not in context
    assert "policy" not in context
    assert set(context) == {"growth_policy", "growth_state"}


def _scale_title(slug: str = "book-scale") -> dict:
    """Same shape as test_portfolio_scorer.py's revenue-winner example —
    scores 100, classifies "scale"."""
    return {
        "slug": slug, "royalty_delta_usd": 5, "kenp_delta": 120,
        "tracked_clicks": 25, "conversion_signal": 1, "verified_placements": 5,
        "risk_active": False,
    }


def _fake_ads_adapter(calls):
    class Adapter:
        def publish(self, action):
            calls.append(action)
            return {
                "campaign_id": "camp-1", "budget_thb": action["daily_budget_thb"],
                "status": "active", "after_state": {"budget_thb": action["daily_budget_thb"]},
            }
    return Adapter()


def test_amazon_ads_executes_end_to_end_once_growth_phase_and_gate_signal_are_both_present(tmp_path):
    """Full compose-through-execute proof for the Ads path (the gap an
    earlier review found: ads_decision's own proposed budget and this
    title's Growth Gate evidence must both actually reach authorize, not
    just be computed and discarded)."""
    calls = []
    cfg = config(
        tmp_path,
        started_at=GROWTH_STARTED_AT,
        titles=[_scale_title()],
        ads_metrics={"book-scale": {"royalty_growth_usd": 0, "kenp_delta": 0, "tracked_clicks": 25}},
        adapters={"ads": _fake_ads_adapter(calls)},
    )
    state = run_growth_controller(cfg, now=NOW, shadow=False)
    assert state["phase"] == "growth"
    ads_entries = [item for item in state["executed"] if item["kind"] == "amazon_ads"]
    assert len(ads_entries) == 1
    assert ads_entries[0]["slug"] == "book-scale"
    assert ads_entries[0]["status"] == "executed"
    assert calls and calls[0]["daily_budget_thb"] == 50.0  # INITIAL_TITLE_CAP_THB, new title

    recorded = growth_evidence(cfg["ledger_path"], slug="book-scale", kind="amazon_ads_executed")
    assert len(recorded) == 1


def test_amazon_ads_stop_decision_executes_end_to_end(tmp_path):
    """Regression: an earlier review found _authorize_ads unconditionally
    denied any daily_budget_thb<=0 action with "invalid_budget" — the
    EXACT decision meant to turn OFF a losing campaign (no-order-stop,
    always budget 0) could never be authorized, so the adapter was never
    called and the campaign kept burning its last-authorized budget. The
    title itself stays Growth-Gate eligible (tracked_clicks=25) — the
    campaign's OWN numbers (0 verified orders at full spend) are what
    trigger the stop."""
    calls = []
    cfg = config(
        tmp_path,
        started_at=GROWTH_STARTED_AT,
        titles=[_scale_title()],
        ads_metrics={"book-scale": {"royalty_growth_usd": 0, "kenp_delta": 0, "tracked_clicks": 25}},
        ads_campaigns={"book-scale": {
            "campaign_id": "camp-1", "daily_budget_thb": 50, "orders": 0, "direct_cost_thb": 50,
            "net_royalty_thb": 0,
        }},
        advertised_title_slugs=["book-scale"],
        adapters={"ads": _fake_ads_adapter(calls)},
    )
    state = run_growth_controller(cfg, now=NOW, shadow=False)
    ads_entries = [item for item in state["executed"] if item["kind"] == "amazon_ads"]
    assert len(ads_entries) == 1
    assert ads_entries[0]["slug"] == "book-scale"
    assert ads_entries[0]["status"] == "executed"
    assert calls and calls[0]["action"] == "stop" and calls[0]["daily_budget_thb"] == 0.0

    recorded = growth_evidence(cfg["ledger_path"], slug="book-scale", kind="amazon_ads_executed")
    assert len(recorded) == 1


def test_amazon_ads_reduce_decision_executes_end_to_end(tmp_path):
    """Companion coverage for the "reduce" branch (a partial, still-positive
    budget cut on an unprofitable-ACOS campaign) flowing through the same
    pipeline to a fake adapter."""
    calls = []
    cfg = config(
        tmp_path,
        started_at=GROWTH_STARTED_AT,
        titles=[_scale_title()],
        ads_metrics={"book-scale": {"royalty_growth_usd": 0, "kenp_delta": 0, "tracked_clicks": 25}},
        ads_campaigns={"book-scale": {
            "campaign_id": "camp-1", "daily_budget_thb": 50, "net_royalty_thb": 10, "direct_cost_thb": 20,
        }},
        advertised_title_slugs=["book-scale"],
        adapters={"ads": _fake_ads_adapter(calls)},
    )
    state = run_growth_controller(cfg, now=NOW, shadow=False)
    ads_entries = [item for item in state["executed"] if item["kind"] == "amazon_ads"]
    assert len(ads_entries) == 1
    assert ads_entries[0]["slug"] == "book-scale"
    assert ads_entries[0]["status"] == "executed"
    assert calls and calls[0]["action"] == "reduce" and calls[0]["daily_budget_thb"] == 25.0

    recorded = growth_evidence(cfg["ledger_path"], slug="book-scale", kind="amazon_ads_executed")
    assert len(recorded) == 1


def test_amazon_ads_title_scoped_incident_blocks_ads_too(tmp_path):
    """Regression: an earlier review found _run_ads_decisions ignored
    readiness["blocked_slugs"] entirely, so a title-scoped critical
    incident blocked organic actions but NOT an Ads decision on the same
    slug."""
    cfg = config(
        tmp_path,
        started_at=GROWTH_STARTED_AT,
        titles=[_scale_title()],
        ads_metrics={"book-scale": {"royalty_growth_usd": 0, "kenp_delta": 0, "tracked_clicks": 25}},
        adapters={"ads": _fake_ads_adapter([])},
        incidents=[{"severity": "critical", "scope": "title", "detail": {"slug": "book-scale"}}],
    )
    state = run_growth_controller(cfg, now=NOW, shadow=False)
    assert state["executed"] == []
    assert any(
        item["slug"] == "book-scale" and item["kind"] == "amazon_ads" and item["reason"] == "title_incident_blocked"
        for item in state["blocked"]
    )


def test_amazon_ads_action_authorized_through_check_policy_in_growth_phase(tmp_path):
    """End-to-end proof the context hygiene above actually matters: an
    amazon_ads action that growth_policy.authorize_growth_action would
    allow (Growth Gate open, clean caps) must also be allowed by
    profit_agent.check_policy — not re-blocked by a stale legacy key."""
    from growth_autopilot import _authorize
    allowed, reason = _authorize(
        "amazon_ads", "book-a", GROWTH_STARTED_AT,
        {
            "now": NOW, "advertised_title_slugs": [], "portfolio_daily_spend_thb": 0,
            "portfolio_monthly_spend_thb": 0, "title_daily_budget_thb": 0,
            "last_budget_increase_at": None, "tracked_clicks": 20,
        },
    )
    # No daily_budget_thb on the action itself -> authorize_growth_action's
    # own "invalid_budget" refusal proves the growth_policy branch was
    # actually reached (not silently skipped) rather than asserting success.
    assert reason == "invalid_budget"
    assert allowed is False


# ---------------------------------------------------------------------------
# Lock contention: a second run must not double-write while a first run
# (real or otherwise) holds the lock.
# ---------------------------------------------------------------------------

def test_concurrent_run_reports_locked_and_does_not_write_a_new_plan(tmp_path):
    cfg = config(tmp_path)
    lock_path = cfg["lock_path"]
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    holder = open(lock_path, "a+")
    fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        state = run_growth_controller(cfg, now=NOW, shadow=True)
    finally:
        fcntl.flock(holder.fileno(), fcntl.LOCK_UN)
        holder.close()

    assert state["locked"] is True
    assert state["executed"] == []
    assert state["plan"] is None
    assert not cfg["state_path"].exists()
    with sqlite3.connect(cfg["ledger_path"]) as connection:
        count = connection.execute("SELECT COUNT(*) FROM growth_plans").fetchone()[0]
    assert count == 0


def test_sequential_replay_is_idempotent_not_a_duplicate_write(tmp_path):
    cfg = config(tmp_path)
    run_growth_controller(cfg, now=NOW, shadow=True)
    run_growth_controller(cfg, now=NOW, shadow=True)
    with sqlite3.connect(cfg["ledger_path"]) as connection:
        count = connection.execute("SELECT COUNT(*) FROM growth_plans").fetchone()[0]
    assert count == 1


def test_same_day_replay_with_different_wall_clock_times_is_still_idempotent(tmp_path):
    """Regression: found via a manual CLI smoke test against a copied real
    ledger — two real invocations minutes apart on the same calendar day
    raised "conflicting growth plan" because `planned_at` used `now`'s full
    wall-clock precision while growth_planner's action_key is scoped to
    the calendar DATE only. A real cron running --collect then --shadow
    --send (or a retried run) would hit this every single day."""
    cfg = config(tmp_path)
    first = run_growth_controller(cfg, now=NOW, shadow=True)
    second = run_growth_controller(cfg, now=NOW + timedelta(minutes=7), shadow=True)
    assert first["plan"]["action_key"] == second["plan"]["action_key"]
    with sqlite3.connect(cfg["ledger_path"]) as connection:
        count = connection.execute("SELECT COUNT(*) FROM growth_plans").fetchone()[0]
    assert count == 1


def test_same_day_plan_drift_with_identical_actions_does_not_crash(tmp_path):
    """Regression: business_ledger.record_growth_plan's content hash covers
    the full plan_json, including portfolio.active (score included per
    title) -- but growth_planner's action_key covers only the selected
    slug/variable pairs (see growth_planner._stable_action_key). If the
    roster's SCORES drift intra-day (a fresh score_portfolio call sees new
    evidence) while the selected actions stay byte-identical, a same-day
    second run must not crash inside the ledger conflict check -- action_key
    already proves the actions match, so this is the benign case."""
    cfg = config(tmp_path)
    first = run_growth_controller(cfg, now=NOW, shadow=True)

    # Same slug still classifies "test" (score well under MAINTAIN's 40)
    # but its score changed -- portfolio.active[0]["score"] drifts while
    # actions (slug/variable) stay identical.
    drifted_cfg = {**cfg, "titles": [{**_untested_title(), "royalty_delta_usd": 1}]}
    second = run_growth_controller(drifted_cfg, now=NOW + timedelta(hours=2), shadow=True)

    assert first["plan"]["action_key"] == second["plan"]["action_key"]
    assert first["plan"]["portfolio"]["active"][0]["score"] != second["plan"]["portfolio"]["active"][0]["score"]
    assert second["plan"]["actions"] == first["plan"]["actions"]
    assert second["executed"] == []
    assert second["locked"] is False

    with sqlite3.connect(cfg["ledger_path"]) as connection:
        count = connection.execute("SELECT COUNT(*) FROM growth_plans").fetchone()[0]
    assert count == 1  # ledger append-only: no duplicate row, original untouched

    assert cfg["state_path"].exists()
    on_disk = json.loads(cfg["state_path"].read_text(encoding="utf-8"))
    assert on_disk["plan"]["action_key"] == second["plan"]["action_key"]


def test_conflicting_growth_plan_with_genuinely_different_actions_still_raises(tmp_path, monkeypatch):
    """Whitebox guard: if the ledger read-back used to check "same actions"
    ever disagreed with what this cycle actually selected, the controller
    must still raise -- never silently swap in some other day's actions. In
    practice this can't happen through record_growth_plan itself
    (action_key already covers every slug/variable pair), so the read-back
    helper is monkeypatched to force the mismatch and prove the re-raise
    path is real, not dead code."""
    import growth_autopilot

    cfg = config(tmp_path)
    run_growth_controller(cfg, now=NOW, shadow=True)

    drifted_cfg = {**cfg, "titles": [{**_untested_title(), "royalty_delta_usd": 1}]}
    monkeypatch.setattr(
        growth_autopilot, "_growth_plan_actions_for_key",
        lambda ledger_path, action_key: [{"slug": "book-z", "kind": "organic_test", "variable": "price"}],
    )

    with pytest.raises(ValueError):
        run_growth_controller(drifted_cfg, now=NOW + timedelta(hours=1), shadow=True)


# ---------------------------------------------------------------------------
# State-file atomicity.
# ---------------------------------------------------------------------------

def test_state_file_is_written_atomically_and_no_tmp_file_is_left_behind(tmp_path):
    cfg = config(tmp_path)
    state = run_growth_controller(cfg, now=NOW, shadow=True)
    state_path = cfg["state_path"]
    assert state_path.exists()
    assert not state_path.with_suffix(".json.tmp").exists()
    on_disk = json.loads(state_path.read_text(encoding="utf-8"))
    assert on_disk["mode"] == state["mode"]
    assert on_disk["plan"]["action_key"] == state["plan"]["action_key"]


def test_state_file_is_fully_overwritten_not_merged_with_a_stale_file(tmp_path):
    cfg = config(tmp_path)
    cfg["state_path"].parent.mkdir(parents=True, exist_ok=True)
    cfg["state_path"].write_text(json.dumps({"stale_marker": True, "mode": "execute"}), encoding="utf-8")
    run_growth_controller(cfg, now=NOW, shadow=True)
    on_disk = json.loads(cfg["state_path"].read_text(encoding="utf-8"))
    assert "stale_marker" not in on_disk
    assert on_disk["mode"] == "shadow"


# ---------------------------------------------------------------------------
# Emergency stop, title scope: a title-scoped critical incident blocks only
# that title, not the whole account.
# ---------------------------------------------------------------------------

def test_title_scoped_incident_with_no_detail_key_fails_closed_not_crash(tmp_path):
    """Regression: a malformed/minimal incident row (no "detail" key at
    all) must be tolerated, not raise KeyError from inside readiness
    derivation — fail closed by simply not matching any slug."""
    cfg = config(tmp_path, incidents=[{"severity": "critical", "scope": "title"}])
    state = run_growth_controller(cfg, now=NOW, shadow=True)
    assert state["readiness"]["blocked_slugs"] == []


def test_organic_test_actions_still_proposed_once_growth_phase_opens(tmp_path):
    """Regression: organic testing of "test"-classified titles and the
    Amazon Ads Growth Gate are independent levers — reaching day 31 must
    not silently stop organic_test proposals for the titles that will
    never qualify for the 2-title Ads cap."""
    cfg = config(tmp_path, started_at=GROWTH_STARTED_AT)
    state = run_growth_controller(cfg, now=NOW, shadow=True)
    assert state["phase"] == "growth"
    assert state["plan"]["actions"]


def test_title_scoped_incident_blocks_only_that_title(tmp_path):
    cfg = config(
        tmp_path,
        incidents=[{"severity": "critical", "scope": "title", "detail": {"slug": "book-a"}}],
        price_proposals={"book-a": 2.99},
        adapters={"price_executor": _fake_price_executor([])},
    )
    state = run_growth_controller(cfg, now=NOW, shadow=False)
    assert state["readiness"]["mutation_allowed"] is True
    assert state["readiness"]["blocked_slugs"] == ["book-a"]
    assert state["executed"] == []
    assert any(item["slug"] == "book-a" and item["reason"] == "title_incident_blocked" for item in state["blocked"])


# ---------------------------------------------------------------------------
# growth_authority_transferred — used by scripts/libra_profit_agent_daily.py
# to stay read-only after Task 11's authority transfer. This is a plain
# marker FILE (never written by run_growth_controller itself) so a single
# ad-hoc `--execute` invocation — e.g. a manual smoke test — can never
# silently and irreversibly flip it; only a deliberate, separate,
# documented operational step (Task 11) creates the marker.
# ---------------------------------------------------------------------------

def test_growth_authority_transferred_false_when_marker_missing(tmp_path):
    assert growth_authority_transferred(tmp_path / "missing-marker") is False


def test_growth_authority_transferred_true_once_the_marker_exists(tmp_path):
    marker = tmp_path / "growth-autopilot-authority-transferred"
    marker.write_text("", encoding="utf-8")
    assert growth_authority_transferred(marker) is True


def test_a_real_execute_run_never_creates_the_authority_marker_itself(tmp_path):
    """The bug this guards against: run_growth_controller must never treat
    running with shadow=False as self-authorizing authority transfer —
    only a separate, deliberate marker file (created outside this module)
    may do that."""
    cfg = config(tmp_path)
    marker = tmp_path / "growth-autopilot-authority-transferred"
    run_growth_controller(cfg, now=NOW, shadow=False)
    assert not marker.exists()
    assert growth_authority_transferred(marker) is False


# ---------------------------------------------------------------------------
# scripts/libra_growth_autopilot.py — CLI config-building helpers. Added
# after an independent review found the persisted `started_at` round-trip
# and the LIVE-title filter had no dedicated coverage (the bug this caught:
# run_growth_controller's state dict originally omitted "started_at"
# entirely, so every real CLI run silently reset the Growth Gate's 30-day
# window to "now" and the gate could never open — see
# test_same_day_replay_with_different_wall_clock_times_is_still_idempotent
# above for the sibling bug this same review pass caught).
# ---------------------------------------------------------------------------

def test_persisted_started_at_falls_back_to_now_when_state_file_is_missing(tmp_path):
    assert _persisted_started_at(tmp_path / "missing.json", NOW) == NOW


def test_persisted_started_at_falls_back_to_now_on_corrupt_json(tmp_path):
    path = tmp_path / "state.json"
    path.write_text("{not json", encoding="utf-8")
    assert _persisted_started_at(path, NOW) == NOW


def test_persisted_started_at_re_reads_a_previously_persisted_value(tmp_path):
    path = tmp_path / "state.json"
    earlier = NOW - timedelta(days=10)
    path.write_text(json.dumps({"mode": "shadow", "started_at": earlier.isoformat()}), encoding="utf-8")
    assert _persisted_started_at(path, NOW) == earlier


def test_default_titles_only_includes_live_status(tmp_path, monkeypatch):
    kdp_dir = tmp_path / "kdp"
    for slug, status in (("book-live", "LIVE"), ("book-draft", "DRAFT"), ("book-blocked", "BLOCKED")):
        book_dir = kdp_dir / slug
        book_dir.mkdir(parents=True)
        (book_dir / "listing.json").write_text(json.dumps({"live_status": status}), encoding="utf-8")

    import scripts.libra_growth_autopilot as cli
    monkeypatch.setattr(cli, "KDP_DIR", kdp_dir)

    titles = _default_titles()
    assert [t["slug"] for t in titles] == ["book-live"]
    assert titles[0]["risk_active"] is False
    assert titles[0]["royalty_delta_usd"] == 0


def test_default_titles_skips_missing_or_malformed_listing_json(tmp_path, monkeypatch):
    kdp_dir = tmp_path / "kdp"
    ok_dir = kdp_dir / "book-ok"
    ok_dir.mkdir(parents=True)
    (ok_dir / "listing.json").write_text(json.dumps({"live_status": "LIVE"}), encoding="utf-8")
    bad_dir = kdp_dir / "book-bad-json"
    bad_dir.mkdir(parents=True)
    (bad_dir / "listing.json").write_text("{not json", encoding="utf-8")

    import scripts.libra_growth_autopilot as cli
    monkeypatch.setattr(cli, "KDP_DIR", kdp_dir)

    titles = _default_titles()
    assert [t["slug"] for t in titles] == ["book-ok"]


# ---------------------------------------------------------------------------
# build_growth_digest (Task 10) — plain-language digest, pure state -> text.
# Moved here from tests/test_growth_dashboard.py per the controller's
# layering decision: the digest lives in this pure module (not app.py) so
# the cron-driven CLI never has to import the FastAPI app.
# ---------------------------------------------------------------------------

_DIGEST_STATE = {
    "generated_at": "2026-07-29T09:00:00+07:00",
    "mode": "shadow",
    "locked": False,
    "phase": "organic",
    "started_at": "2026-07-01T09:00:00+07:00",
    "readiness": {"mutation_allowed": True, "reason": "ready", "open_incidents": 0, "blocked_slugs": []},
    "observations_collected": 4,
    "scored_titles": [],
    "plan": {
        "action_key": "abc123", "phase": "organic_test",
        "portfolio": {"active": []},
        "actions": [{"slug": "book-b", "kind": "organic_test", "variable": "price"}],
    },
    "executed": [
        {"slug": "book-a", "kind": "free_promo", "status": "executed", "reason": "verified_after_state",
         "evidence": {"confirmation_id": "conf-1", "verified_state_change": {"before": "off", "after": "on"}}},
    ],
    "blocked": [
        {"slug": "book-c", "kind": "price_update", "reason": "title_incident_blocked"},
    ],
}


def test_growth_digest_separates_planned_from_executed_in_plain_language():
    text = build_growth_digest(_DIGEST_STATE)

    assert "Planned" in text
    assert "Executed with evidence" in text
    assert "book-b" in text  # the planned action's slug
    assert "book-a" in text  # the executed action's slug
    assert text.index("Planned") < text.index("book-b")
    assert text.index("Executed with evidence") < text.index("book-a")


def test_growth_digest_handles_no_run_yet():
    text = build_growth_digest({})
    assert "No" in text
    assert "run" in text.lower()


def test_growth_digest_flags_blocked_mutation_plainly():
    state = {
        **_DIGEST_STATE,
        "readiness": {
            "mutation_allowed": False, "reason": "account_critical_incident",
            "open_incidents": 1, "blocked_slugs": ["book-a"],
        },
    }
    text = build_growth_digest(state)
    assert "account_critical_incident" in text
    assert "book-c" in text  # still reports the blocked action


def test_growth_digest_is_silent_about_verification_when_absent():
    """_DIGEST_STATE has no "verification" key -- the digest must not
    fabricate or mention a verification section that never ran."""
    text = build_growth_digest(_DIGEST_STATE)
    assert "Verification" not in text
    assert "FLAGGED" not in text


def test_growth_digest_mentions_verification_status_when_present():
    state = {
        **_DIGEST_STATE,
        "verification": {
            "status": "verified", "checked": 3, "verified": [{"slug": "book-a"}], "flagged": [],
        },
    }
    text = build_growth_digest(state)
    assert "Verification" in text
    assert "verified" in text.lower()


def test_growth_digest_calls_out_flagged_verification_entries_prominently():
    """A flagged entry means an action claimed "executed" without
    verifiable before/after proof -- this must be impossible to miss in
    the digest, not buried in cron log JSON only."""
    state = {
        **_DIGEST_STATE,
        "verification": {
            "status": "verified", "checked": 2, "verified": [],
            "flagged": [{"slug": "book-b", "kind": "free_promo", "reason": "missing_verifiable_evidence"}],
        },
    }
    text = build_growth_digest(state)
    assert "FLAGGED" in text
    assert "book-b" in text
    assert "missing_verifiable_evidence" in text


# ---------------------------------------------------------------------------
# scripts/libra_growth_autopilot.py --send wiring (Task 10) — the CLI's
# send path must hand the transport build_growth_digest's own output for
# the state it just wrote, not some other/older format.
# ---------------------------------------------------------------------------

def test_cli_send_path_uses_build_growth_digest_output(tmp_path, monkeypatch):
    import sys

    import scripts.libra_growth_autopilot as cli

    ledger_path = tmp_path / "libra-business.db"
    kdp_dir = tmp_path / "kdp"
    kdp_dir.mkdir()
    monkeypatch.setattr(cli, "LEDGER_FILE", ledger_path)
    monkeypatch.setattr(cli, "STATE_FILE", ledger_path.with_name("growth-autopilot-state.json"))
    monkeypatch.setattr(cli, "LOCK_FILE", ledger_path.with_name("growth-autopilot.lock"))
    monkeypatch.setattr(cli, "KDP_DIR", kdp_dir)

    sent = {}
    monkeypatch.setattr(cli, "send_telegram", lambda text: sent.setdefault("text", text) or True)
    monkeypatch.setattr(sys, "argv", ["libra_growth_autopilot.py", "--shadow", "--send"])

    cli.main()

    assert "text" in sent
    written_state = json.loads(cli.STATE_FILE.read_text())
    # Prove the transport received build_growth_digest's own output for the
    # state this run actually wrote -- not a stale/alternate format.
    assert sent["text"] == build_growth_digest(written_state)
    assert "Planned (not yet done):" in sent["text"]
    assert "Executed with evidence:" in sent["text"]


# ---------------------------------------------------------------------------
# Task 11a: --collect, --verify, --growth-gate-report — the three CLI modes
# the plan's cron lines and Step 10 already reference but no prior task
# built. See docs/superpowers/plans/2026-07-28-libra-growth-autopilot.md
# Task 11 Steps 5 and 10.
# ---------------------------------------------------------------------------

def _collect_config(tmp_path, *, incidents=None) -> dict:
    db_path = tmp_path / "ledger.db"
    init_ledger(db_path)
    return {
        "ledger_path": db_path,
        "lock_path": tmp_path / "growth-autopilot.lock",
        "state_path": tmp_path / "growth-autopilot-state.json",
        "titles": [_untested_title()],
        # A stray account-critical incident in config must NOT stop
        # collection -- collect_growth_observations never reads this key at
        # all, which is itself the emergency-stop proof: there is no gate
        # here to short-circuit.
        "incidents": incidents or [],
    }


def test_collect_records_heartbeat_evidence_and_reports_visible_counts(tmp_path):
    cfg = _collect_config(tmp_path)
    result = collect_growth_observations(cfg, now=NOW)

    assert result["locked"] is False
    assert result["observations_visible"] >= 1
    assert result["titles_visible"] == 1

    rows = growth_evidence(cfg["ledger_path"], kind="collection_heartbeat")
    assert len(rows) == 1


def test_collect_does_not_plan_authorize_or_execute(tmp_path):
    """--collect is observation-only: it must never touch growth_plans."""
    cfg = _collect_config(tmp_path)
    collect_growth_observations(cfg, now=NOW)

    with sqlite3.connect(cfg["ledger_path"]) as connection:
        plan_count = connection.execute("SELECT COUNT(*) FROM growth_plans").fetchone()[0]
    assert plan_count == 0


def test_collect_double_run_same_day_is_idempotent_not_duplicated(tmp_path):
    cfg = _collect_config(tmp_path)
    first = collect_growth_observations(cfg, now=NOW)
    later_same_day = NOW + timedelta(hours=3)
    second = collect_growth_observations(cfg, now=later_same_day)

    assert first["locked"] is False
    assert second["locked"] is False
    rows = growth_evidence(cfg["ledger_path"], kind="collection_heartbeat")
    assert len(rows) == 1  # not duplicated despite different wall-clock times


def test_collect_runs_during_account_critical_incident_emergency_stop(tmp_path):
    """Plan: 'emergency stop keeps collection.' An open account-critical
    incident must not prevent --collect from recording evidence."""
    cfg = _collect_config(tmp_path, incidents=[{"severity": "critical", "scope": "account", "detail": {}}])
    result = collect_growth_observations(cfg, now=NOW)
    assert result["locked"] is False
    assert result["observations_visible"] >= 1


def test_collect_reports_locked_when_lock_is_contended(tmp_path):
    cfg = _collect_config(tmp_path)
    lock_path = Path(cfg["lock_path"])
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = collect_growth_observations(cfg, now=NOW)
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()

    assert result["locked"] is True
    rows = growth_evidence(cfg["ledger_path"], kind="collection_heartbeat")
    assert rows == []  # never wrote while contended


def test_collect_defensively_handles_a_ledger_conflict_without_crashing(tmp_path, monkeypatch):
    """Symmetric defensive fix (T11a deferred Minor): the heartbeat's
    source_key and payload are both derived from `now`'s calendar date
    only, so a genuine conflict is unreachable today -- but the write must
    still degrade gracefully (never crash out of the lock) if
    record_growth_evidence ever raises here, mirroring verify_growth_state's
    same defensive posture."""
    import growth_autopilot

    cfg = _collect_config(tmp_path)

    def _raise(*args, **kwargs):
        raise ValueError("conflicting growth evidence for growth-collect-heartbeat:2026-07-29")
    monkeypatch.setattr(growth_autopilot, "record_growth_evidence", _raise)

    result = collect_growth_observations(cfg, now=NOW)

    assert result["locked"] is False  # must not crash out of the lock block


# ---------------------------------------------------------------------------
# --verify
# ---------------------------------------------------------------------------

def _verify_config(tmp_path) -> dict:
    db_path = tmp_path / "ledger.db"
    init_ledger(db_path)
    return {
        "ledger_path": db_path,
        "lock_path": tmp_path / "growth-autopilot.lock",
        "state_path": tmp_path / "growth-autopilot-state.json",
    }


def _write_prior_state(state_path: Path, executed: list) -> dict:
    state = {
        "generated_at": NOW.isoformat(),
        "mode": "execute",
        "phase": "organic",
        "started_at": ORGANIC_STARTED_AT.isoformat(),
        "readiness": {"mutation_allowed": True, "reason": "ready", "open_incidents": 0, "blocked_slugs": []},
        "plan": {"action_key": "abc", "phase": "organic_test", "portfolio": {"active": []}, "actions": []},
        "executed": executed,
        "blocked": [],
    }
    state_path.write_text(json.dumps(state), encoding="utf-8")
    return state


def test_verify_with_missing_state_file_is_clean_nothing_to_verify(tmp_path):
    cfg = _verify_config(tmp_path)
    result = verify_growth_state(cfg, now=NOW)
    assert result["status"] == "nothing_to_verify"
    assert result["checked"] == 0
    assert result["verified"] == []
    assert result["flagged"] == []


def test_verify_confirms_an_executed_action_that_has_verifiable_evidence(tmp_path):
    cfg = _verify_config(tmp_path)
    _write_prior_state(cfg["state_path"], executed=[{
        "slug": "book-a", "kind": "price_update", "status": "executed", "reason": "verified_after_state",
        "evidence": {
            "confirmation_id": "kdp-price-update:book-a:2.99",
            "verified_state_change": {"before": {"price": 9.99}, "after": {"price": 2.99}},
        },
    }])

    result = verify_growth_state(cfg, now=NOW)

    assert result["status"] == "verified"
    assert result["checked"] == 1
    assert [row["slug"] for row in result["verified"]] == ["book-a"]
    assert result["flagged"] == []


def test_verify_flags_an_executed_action_that_has_no_verifiable_evidence_never_upgrades(tmp_path):
    """The action was recorded as 'executed' upstream, but carries no
    readable before/after proof. Verify must FLAG it, not silently accept
    it and never invent proof it doesn't have -- it can only downgrade."""
    cfg = _verify_config(tmp_path)
    _write_prior_state(cfg["state_path"], executed=[{
        "slug": "book-b", "kind": "free_promo", "status": "executed", "reason": "verified_after_state",
        "evidence": {},
    }])

    result = verify_growth_state(cfg, now=NOW)

    assert result["checked"] == 1
    assert result["verified"] == []
    assert [row["slug"] for row in result["flagged"]] == ["book-b"]
    assert result["flagged"][0]["reason"] == "missing_verifiable_evidence"

    # Verify must never rewrite the original executed entry itself -- only
    # add findings alongside it.
    rewritten = json.loads(cfg["state_path"].read_text(encoding="utf-8"))
    assert rewritten["executed"][0]["status"] == "executed"
    assert rewritten["executed"][0]["evidence"] == {}


def test_verify_writes_findings_into_verification_section_without_disturbing_plan_or_executed(tmp_path):
    cfg = _verify_config(tmp_path)
    original = _write_prior_state(cfg["state_path"], executed=[{
        "slug": "book-a", "kind": "amazon_ads", "status": "executed",
        "evidence": {"campaign_id": "camp-1", "budget_thb": 50, "status": "active", "after_state": {"budget_thb": 50}},
    }])

    verify_growth_state(cfg, now=NOW)

    rewritten = json.loads(cfg["state_path"].read_text(encoding="utf-8"))
    assert rewritten["plan"] == original["plan"]
    assert rewritten["executed"] == original["executed"]
    assert "verification" in rewritten
    assert rewritten["verification"]["checked"] == 1


def test_verify_records_ledger_evidence_for_the_run(tmp_path):
    cfg = _verify_config(tmp_path)
    _write_prior_state(cfg["state_path"], executed=[])
    verify_growth_state(cfg, now=NOW)
    rows = growth_evidence(cfg["ledger_path"], kind="verification_run")
    assert len(rows) == 1


def test_verify_never_calls_an_adapter_or_authorizes_anything(tmp_path):
    """Side-effect-free externally: verify only reads state/ledger and
    writes ledger evidence + the state file's verification section. Plant a
    poisoned adapter in config that raises on any call -- if verify ever
    dispatched to it, this test would fail with that exception instead of
    passing normally."""
    class _PoisonedAdapter:
        def publish(self, action):
            raise AssertionError("verify_growth_state must never call an adapter")

    cfg = _verify_config(tmp_path)
    cfg["adapters"] = {
        "price_executor": _PoisonedAdapter().publish,
        "promotion": _PoisonedAdapter(),
        "ads": _PoisonedAdapter(),
    }
    _write_prior_state(cfg["state_path"], executed=[{
        "slug": "book-a", "kind": "price_update", "status": "executed",
        "evidence": {"confirmation_id": "x", "verified_state_change": {"before": 1, "after": 2}},
    }])

    result = verify_growth_state(cfg, now=NOW)

    assert result["status"] == "verified"  # no exception was raised


def test_verify_runs_during_account_critical_incident_emergency_stop(tmp_path):
    """Plan: verification may still run (read-only reconciliation) even
    during an emergency stop. verify_growth_state never reads incidents at
    all, so a stray incidents key changes nothing."""
    cfg = _verify_config(tmp_path)
    cfg["incidents"] = [{"severity": "critical", "scope": "account", "detail": {}}]
    _write_prior_state(cfg["state_path"], executed=[])
    result = verify_growth_state(cfg, now=NOW)
    assert result["status"] == "verified"


def test_verify_reports_locked_when_lock_is_contended(tmp_path):
    cfg = _verify_config(tmp_path)
    _write_prior_state(cfg["state_path"], executed=[])
    lock_path = Path(cfg["lock_path"])
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = verify_growth_state(cfg, now=NOW)
    finally:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()
    assert result["status"] == "locked"


def test_verify_same_day_rerun_with_different_counts_does_not_crash(tmp_path):
    """Regression: growth-verify's ledger evidence row is keyed one-per-day
    (source_key f"growth-verify:{date}") with a payload built from the
    CURRENT state's checked/verified/flagged counts. A manual re-run later
    the same day, after the state file's executed set (and therefore this
    run's counts) changed, used to raise an unhandled "conflicting growth
    evidence" from inside the lock. The ledger stays append-only -- the run
    must degrade gracefully and stay honest instead of crashing."""
    cfg = _verify_config(tmp_path)
    _write_prior_state(cfg["state_path"], executed=[{
        "slug": "book-a", "kind": "price_update", "status": "executed",
        "evidence": {"confirmation_id": "x", "verified_state_change": {"before": 1, "after": 2}},
    }])
    first = verify_growth_state(cfg, now=NOW)
    assert first["status"] == "verified"
    assert "ledger_note" not in first

    # Same calendar day, but the state file's executed set changed in
    # between -- this run's own checked/verified/flagged counts differ from
    # the row already recorded for today.
    _write_prior_state(cfg["state_path"], executed=[{
        "slug": "book-a", "kind": "price_update", "status": "executed",
        "evidence": {"confirmation_id": "x", "verified_state_change": {"before": 1, "after": 2}},
    }, {
        "slug": "book-b", "kind": "free_promo", "status": "executed", "evidence": {},
    }])

    second = verify_growth_state(cfg, now=NOW + timedelta(hours=3))

    assert second["status"] == "verified"  # no crash
    assert second["checked"] == 2  # honest -- reflects THIS run's own findings
    assert second["ledger_note"] == "already_recorded_today_with_different_counts"

    rows = growth_evidence(cfg["ledger_path"], kind="verification_run")
    assert len(rows) == 1  # ledger append-only: no duplicate/second row this day

    on_disk = json.loads(cfg["state_path"].read_text(encoding="utf-8"))
    assert on_disk["verification"]["checked"] == 2  # state file honestly reflects the latest run
    assert on_disk["verification"]["ledger_note"] == "already_recorded_today_with_different_counts"


# ---------------------------------------------------------------------------
# --growth-gate-report
# ---------------------------------------------------------------------------

def test_growth_gate_report_matches_growth_policy_ads_eligibility_verdicts():
    titles = [
        {"slug": "book-royalty", "royalty_delta_usd": 5, "kenp_delta": 0, "tracked_clicks": 0},
        {"slug": "book-kenp", "royalty_delta_usd": 0, "kenp_delta": 150, "tracked_clicks": 0},
        {"slug": "book-clicks", "royalty_delta_usd": 0, "kenp_delta": 0, "tracked_clicks": 25},
        {"slug": "book-none", "royalty_delta_usd": 0, "kenp_delta": 0, "tracked_clicks": 0},
    ]
    report = build_growth_gate_report(titles, ORGANIC_STARTED_AT, NOW)
    rows = {row["slug"]: row for row in report["titles"]}

    for title in titles:
        expected = ads_eligibility({
            "royalty_growth_usd": title["royalty_delta_usd"],
            "kenp_delta": title["kenp_delta"],
            "tracked_clicks": title["tracked_clicks"],
        })
        assert rows[title["slug"]]["eligible"] == expected["eligible"]
        assert rows[title["slug"]]["reason"] == expected["reason"]

    assert report["eligible_count"] == 3


def test_growth_gate_report_includes_phase_from_growth_policy():
    from growth_policy import growth_phase
    report = build_growth_gate_report([], ORGANIC_STARTED_AT, NOW)
    assert report["phase"] == growth_phase(ORGANIC_STARTED_AT, NOW)

    growth_report = build_growth_gate_report([], GROWTH_STARTED_AT, NOW)
    assert growth_report["phase"] == growth_phase(GROWTH_STARTED_AT, NOW)


def test_growth_gate_report_handles_empty_titles():
    report = build_growth_gate_report([], ORGANIC_STARTED_AT, NOW)
    assert report["titles"] == []
    assert report["eligible_count"] == 0


def test_format_growth_gate_report_is_human_readable_text():
    report = build_growth_gate_report(
        [{"slug": "book-a", "royalty_delta_usd": 5, "kenp_delta": 0, "tracked_clicks": 0}],
        ORGANIC_STARTED_AT, NOW,
    )
    text = format_growth_gate_report(report)
    assert isinstance(text, str)
    assert "book-a" in text
    assert "ELIGIBLE" in text


# ---------------------------------------------------------------------------
# CLI wiring: --collect / --verify / --growth-gate-report, and mutual
# exclusivity with --shadow/--execute/--send.
# ---------------------------------------------------------------------------

def _patch_cli_paths(monkeypatch, tmp_path):
    import scripts.libra_growth_autopilot as cli
    ledger_path = tmp_path / "libra-business.db"
    kdp_dir = tmp_path / "kdp"
    kdp_dir.mkdir()
    monkeypatch.setattr(cli, "LEDGER_FILE", ledger_path)
    monkeypatch.setattr(cli, "STATE_FILE", ledger_path.with_name("growth-autopilot-state.json"))
    monkeypatch.setattr(cli, "LOCK_FILE", ledger_path.with_name("growth-autopilot.lock"))
    monkeypatch.setattr(cli, "KDP_DIR", kdp_dir)
    return cli


def test_cli_collect_mode_prints_json_and_never_writes_the_shadow_execute_state_file(tmp_path, monkeypatch, capsys):
    import sys
    cli = _patch_cli_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["libra_growth_autopilot.py", "--collect"])

    cli.main()

    result = json.loads(capsys.readouterr().out)
    assert result["locked"] is False
    assert not cli.STATE_FILE.exists()


def test_cli_verify_mode_with_no_prior_run_reports_nothing_to_verify(tmp_path, monkeypatch, capsys):
    import sys
    cli = _patch_cli_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["libra_growth_autopilot.py", "--verify"])

    cli.main()

    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "nothing_to_verify"


def test_cli_growth_gate_report_mode_prints_text_and_mutates_nothing(tmp_path, monkeypatch, capsys):
    import sys
    cli = _patch_cli_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["libra_growth_autopilot.py", "--growth-gate-report"])

    cli.main()

    out = capsys.readouterr().out
    assert "Growth Gate" in out
    assert not cli.LEDGER_FILE.exists()
    assert not cli.STATE_FILE.exists()


def test_cli_rejects_collect_combined_with_shadow(tmp_path, monkeypatch, capsys):
    import sys
    cli = _patch_cli_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["libra_growth_autopilot.py", "--collect", "--shadow"])
    with pytest.raises(SystemExit):
        cli.main()


def test_cli_rejects_verify_combined_with_execute(tmp_path, monkeypatch, capsys):
    import sys
    cli = _patch_cli_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["libra_growth_autopilot.py", "--verify", "--execute"])
    with pytest.raises(SystemExit):
        cli.main()


def test_cli_rejects_growth_gate_report_combined_with_collect(tmp_path, monkeypatch, capsys):
    import sys
    cli = _patch_cli_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["libra_growth_autopilot.py", "--growth-gate-report", "--collect"])
    with pytest.raises(SystemExit):
        cli.main()


def test_cli_rejects_send_combined_with_collect(tmp_path, monkeypatch, capsys):
    """--send composes only with --shadow/--execute (the only modes the
    plan's own cron lines ever pair it with -- see Task 11 Step 5)."""
    import sys
    cli = _patch_cli_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["libra_growth_autopilot.py", "--collect", "--send"])
    with pytest.raises(SystemExit):
        cli.main()


def test_cli_rejects_send_combined_with_verify(tmp_path, monkeypatch, capsys):
    import sys
    cli = _patch_cli_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["libra_growth_autopilot.py", "--verify", "--send"])
    with pytest.raises(SystemExit):
        cli.main()


def test_cli_rejects_send_combined_with_growth_gate_report(tmp_path, monkeypatch, capsys):
    import sys
    cli = _patch_cli_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "argv", ["libra_growth_autopilot.py", "--growth-gate-report", "--send"])
    with pytest.raises(SystemExit):
        cli.main()


def test_cli_still_accepts_send_combined_with_shadow(tmp_path, monkeypatch):
    """Regression guard: the new mutual-exclusivity/--send validation must
    not break the pre-existing, plan-documented `--shadow --send` pairing."""
    import sys
    cli = _patch_cli_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(cli, "send_telegram", lambda text: True)
    monkeypatch.setattr(sys, "argv", ["libra_growth_autopilot.py", "--shadow", "--send"])
    cli.main()  # must not raise
