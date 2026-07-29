"""Single Growth Autopilot Controller for Libra.

Composes every module built for the Growth Autopilot (business_ledger,
growth_policy, portfolio_scorer, growth_planner, kdp_promotion_controller,
amazon_ads_controller) into one pipeline: collect -> derive readiness ->
score -> plan -> persist -> authorize each action immediately before its
own adapter call -> execute -> reconcile -> atomically write state. No
module's own logic is reimplemented here — this file only wires them
together and fails closed at every seam.

Pipeline order (see run_growth_controller):
  1. Collection (pure reads, no lock): growth_evidence rows from the
     ledger, plus the caller-supplied, already-shaped `titles` rows
     (portfolio_scorer's input shape) and open growth_incidents. This
     always runs, even when mutation is later refused, so a dashboard or
     operator can see what the controller observed regardless of whether
     it acted.
  2. Readiness: an open growth_incidents row with severity "critical" and
     scope "account" is an emergency stop — readiness["mutation_allowed"]
     becomes False and every downstream action is skipped (but collection
     above already ran). A "critical"/"title"-scoped incident instead adds
     its slug to readiness["blocked_slugs"] — defense in depth alongside
     portfolio_scorer's own freeze/blocked classification, never a
     substitute for it (see "Freeze rule wins" below).
  3. Score + plan: portfolio_scorer.score_portfolio, then
     growth_planner.build_growth_plan. growth_policy's phase vocabulary
     ("organic"/"growth") is NOT growth_planner's phase vocabulary (only
     the literal string "organic_test" ever unlocks a planned action —
     see growth_planner.ORGANIC_TEST_PHASES) — POLICY_PHASE_TO_PLANNER_PHASE
     maps explicitly rather than feeding one module's phase string into
     the other.
  4. Everything from here runs inside one file lock (see _file_lock): the
     plan is persisted via business_ledger.record_growth_plan (itself
     idempotent by action_key, so a same-day replay is a no-op, not a
     duplicate row) and, only when shadow is False AND mutation is
     allowed, each planned action is translated, authorized, and executed.
  5. Translate + authorize + execute, per action: growth_planner only
     ever emits actions with kind "organic_test" (see growth_planner.py's
     own docstring) — that literal kind is never in
     growth_policy.GROWTH_ACTION_KINDS, so it is never passed to an
     authorize call directly. VARIABLE_ACTION_KIND re-expresses the
     action's `variable` (e.g. "price") as the kind growth_policy actually
     recognizes (e.g. "price_update") first. A variable with no mapping
     (no controller/adapter exists for it yet) fails closed to
     "manual_required" without ever calling authorize — there is nothing
     to authorize a kind growth_policy doesn't define.

     Authorization itself goes through profit_agent.check_policy, not
     growth_policy.authorize_growth_action directly: check_policy already
     wraps authorize_growth_action for any action whose kind is in
     GROWTH_ACTION_KINDS AND whose context carries a "growth_policy" key
     (see profit_agent.check_policy's own comment — that opt-in path was
     built in Task 2 specifically for this controller). _growth_action_context
     deliberately builds ONLY {"growth_policy", "growth_state"} and never
     copies in "policy" or "no_spend" — a stale no_spend=True (or a
     persisted legacy "policy" dict) would fall through to check_policy's
     legacy 90-day organic-mode block and re-deny an action
     growth_policy.authorize_growth_action itself already approved,
     including amazon_ads once the Growth Gate opens. Every action is
     authorized fresh, immediately before its own adapter call, even
     though the plan itself was persisted already — a plan being on
     record is not the same as it still being authorized right now.
  6. Execute + reconcile: price_update reuses
     scripts/kdp_action_executor.py's existing build_executor() (the SAME
     validate_action-gated browser executor the legacy Profit Agent
     uses — never a second, parallel implementation), re-classified
     through the project's fail-closed "executed only with a distinct
     verified before/after readback" rule. free_promo goes through
     kdp_promotion_controller.propose_promotion -> reconcile_promotion,
     whose adapter (KdpPromotionAdapter) itself runs validate_action
     FIRST before touching the browser (see kdp_promotion_controller.py's
     module docstring "Task-9 wiring contract"). amazon_ads (growth phase
     only, day 31+) goes through amazon_ads_controller.ads_decision ->
     (translate/authorize as above) -> amazon_ads_controller.reconcile_ads_action.
     Every adapter is caller-injected via config["adapters"] — if a
     required adapter is not configured, the action fails closed to
     manual_required rather than fabricating a result. Only an "executed"
     result (never a plan, a reminder, or a bare adapter response) is
     recorded as business_ledger growth_evidence.

Freeze rule wins: growth_planner already excludes "freeze"/"blocked"
titles from the active portfolio before this controller ever sees a
title, and this controller adds no code path that could put one back in —
a title the scorer classifies frozen/blocked is never rescued here
regardless of score.

Emergency stop: see readiness above. Collection always runs; mutation
(persisting authorized side effects to KDP/Ads) never runs once an
account-scope critical incident is open, until it is resolved.
"""
from __future__ import annotations

import fcntl
import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

from amazon_ads_controller import ads_decision, reconcile_ads_action
from business_ledger import growth_evidence, init_ledger, record_growth_evidence, record_growth_plan
from growth_planner import build_growth_plan
from growth_policy import growth_phase
from kdp_promotion_controller import propose_promotion, reconcile_promotion
from portfolio_scorer import score_portfolio
from profit_agent import check_policy

MODE_SHADOW = "shadow"
MODE_EXECUTE = "execute"

# growth_planner only ever emits kind "organic_test" (see growth_planner.py
# docstring); this maps its `variable` field to the kind growth_policy
# actually authorizes. A variable with no entry here has no controller/
# adapter wired to it yet and fails closed rather than being guessed at.
VARIABLE_ACTION_KIND = {
    "price": "price_update",
    "free_promo": "free_promo",
    "countdown_deal": "countdown_deal",
}

# growth_policy's phase vocabulary ("organic"/"growth") is not
# growth_planner's ("organic_test" is the only phase that ever unlocks a
# planned action — see growth_planner.ORGANIC_TEST_PHASES). Organic testing
# of "test"-classified titles and the Amazon Ads Growth Gate are two
# INDEPENDENT levers (bounded titles keep needing organic experiments to
# improve regardless of whether Ads has opened for the separate, capped set
# of "scale" titles) — nothing in the plan says testing should stop once
# Ads opens, so both policy phases map to the planner's one phase that ever
# proposes actions. This is a deliberate, total mapping (not a lookup with
# a silent same-string fallback) precisely so a future new policy phase
# must be added here explicitly rather than accidentally suppressing every
# organic_test action the way an unmapped fallback would.
POLICY_PHASE_TO_PLANNER_PHASE = {"organic": "organic_test", "growth": "organic_test"}

# How long a growth-action evidence row stays "fresh" for downstream growth
# signals — same window distribution_executor.py uses for the same reason.
EVIDENCE_FRESH_DAYS = 30


# ---------------------------------------------------------------------------
# Atomic state write + single-writer file lock
# ---------------------------------------------------------------------------

def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    os.replace(temporary, path)


@contextmanager
def _file_lock(lock_path: Path):
    """Yield True with an exclusive, non-blocking lock held, or False
    immediately if another run already holds it. Never blocks — a
    contended run reports itself as locked rather than waiting, so a
    stuck cron invocation can never pile up a queue of blocked processes."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(lock_path, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        yield False
        return
    try:
        yield True
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


# ---------------------------------------------------------------------------
# Collection (pure reads — never gated by readiness or the lock)
# ---------------------------------------------------------------------------

def _open_incidents(ledger_path: Path) -> list[dict]:
    init_ledger(ledger_path)
    with sqlite3.connect(ledger_path) as connection:
        rows = connection.execute(
            "SELECT incident_key, severity, scope, detail_json FROM growth_incidents "
            "WHERE resolved_at IS NULL"
        ).fetchall()
    incidents = []
    for incident_key, severity, scope, detail_json in rows:
        try:
            detail = json.loads(detail_json)
        except (TypeError, ValueError):
            detail = {}
        incidents.append({"incident_key": incident_key, "severity": severity, "scope": scope, "detail": detail})
    return incidents


def _collect_observations(ledger_path: Path, titles: list[dict]) -> dict:
    """Read every growth_evidence row currently on record. This always
    runs, independent of readiness/lock state, so an emergency stop still
    leaves the operator with fresh observations — only mutation is
    withheld."""
    init_ledger(ledger_path)
    evidence_rows = growth_evidence(ledger_path)
    return {"evidence_rows": evidence_rows, "count": len(evidence_rows) + len(titles or [])}


def _derive_readiness(incidents: list[dict]) -> dict:
    account_critical = [
        item for item in incidents
        if item.get("severity") == "critical" and item.get("scope") == "account"
    ]
    blocked_slugs = sorted({
        (item.get("detail") or {}).get("slug")
        for item in incidents
        if item.get("severity") == "critical" and item.get("scope") == "title" and (item.get("detail") or {}).get("slug")
    })
    return {
        "mutation_allowed": not account_critical,
        "reason": "account_critical_incident" if account_critical else "ready",
        "open_incidents": len(incidents),
        "blocked_slugs": blocked_slugs,
    }


def _latest_active_experiments(ledger_path: Path) -> list[dict]:
    """The most recently persisted plan's actions ARE this cycle's active
    experiments — an organic_test proposed yesterday is a running test
    today, and growth_planner's one-variable-per-window rule must see it
    to avoid proposing a second variable for the same slug mid-window."""
    init_ledger(ledger_path)
    with sqlite3.connect(ledger_path) as connection:
        row = connection.execute(
            "SELECT plan_json FROM growth_plans ORDER BY id DESC LIMIT 1"
        ).fetchone()
    if not row:
        return []
    try:
        plan = json.loads(row[0])
    except (TypeError, ValueError):
        return []
    return [
        {"slug": action.get("slug"), "variable": action.get("variable")}
        for action in plan.get("actions", [])
        if isinstance(action, dict)
    ]


# ---------------------------------------------------------------------------
# Authorization (profit_agent.check_policy, clean opted-in context only)
# ---------------------------------------------------------------------------

def _growth_action_context(started_at: datetime, growth_state: dict) -> dict:
    """Build the context for profit_agent.check_policy's opted-in
    growth-policy path. Deliberately carries ONLY "growth_policy" and
    "growth_state" — never "policy" or "no_spend". Either of those, if
    stale/leftover, falls through to check_policy's legacy 90-day
    organic-mode block and re-denies an action growth_policy.
    authorize_growth_action already approved (e.g. an authorized
    amazon_ads action gets refused as "paid actions disabled during
    90-day organic mode" by a stale no_spend=True)."""
    return {"growth_policy": {"started_at": started_at}, "growth_state": growth_state}


def _authorize(kind: str, slug: str, started_at: datetime, growth_state: dict, extra_action: dict | None = None) -> tuple[bool, str]:
    action = {"kind": kind, "slug": slug, "cost_usd": 0, **(extra_action or {})}
    context = _growth_action_context(started_at, growth_state)
    return check_policy(action, context)


# ---------------------------------------------------------------------------
# Evidence recording (mirrors distribution_executor._record_executed)
# ---------------------------------------------------------------------------

def _record_action_evidence(ledger_path: Path, kind: str, slug: str, evidence: dict, now: datetime) -> None:
    source_key = f"growth-action:{kind}:{slug}:{now.date().isoformat()}"
    try:
        record_growth_evidence(ledger_path, {
            "source_key": source_key,
            "kind": f"{kind}_executed",
            "slug": slug,
            "observed_at": now.isoformat(),
            "fresh_until": (now + timedelta(days=EVIDENCE_FRESH_DAYS)).isoformat(),
            "confidence": 1.0,
            "payload": evidence,
        })
    except ValueError:
        existing = next(
            (row for row in growth_evidence(ledger_path, slug=slug) if row["source_key"] == source_key),
            None,
        )
        if existing is None or existing["payload"] != evidence:
            raise


# ---------------------------------------------------------------------------
# Per-kind execution
# ---------------------------------------------------------------------------

def _reconcile_price_update(slug: str, price, executor) -> dict:
    if executor is None:
        return {"status": "manual_required", "reason": "no_price_executor_configured"}
    pending = {"kind": "price_update", "slug": slug, "cost_usd": 0, "proposed_value": price}
    try:
        result = executor(pending)
    except Exception:
        return {"status": "manual_required", "reason": "adapter_error"}
    if not isinstance(result, dict):
        return {"status": "manual_required", "reason": "invalid_adapter_response"}
    if result.get("executor_skip_reason"):
        return {"status": "manual_required", "reason": result["executor_skip_reason"]}
    change = result.get("verified_state_change")
    if (
        result.get("returncode") == 0
        and isinstance(change, dict)
        and change.get("before") is not None
        and change.get("after") is not None
    ):
        return {
            "status": "executed",
            "reason": "verified_after_state",
            "evidence": {"confirmation_id": result.get("confirmation_id"), "verified_state_change": change},
        }
    return {"status": "manual_required", "reason": result.get("error") or "unverified_after_state"}


def _reconcile_free_promo(slug: str, config: dict) -> dict:
    state = (config.get("promotion_state") or {}).get(slug, {})
    evidence_items = (config.get("promotion_evidence") or {}).get(slug, [])
    proposal = propose_promotion(slug, state, evidence_items)
    if proposal["status"] != "allowed":
        return {"status": "blocked", "reason": proposal["reason"]}
    adapter = (config.get("adapters") or {}).get("promotion")
    if adapter is None:
        return {"status": "manual_required", "reason": "no_promotion_adapter_configured"}
    result = reconcile_promotion({"slug": slug, "kind": "kdp_promotion", "days": proposal["days"]}, adapter)
    if result["status"] == "executed":
        evidence = {k: v for k, v in result.items() if k not in {"status", "reason"}}
        return {"status": "executed", "reason": result["reason"], "evidence": evidence}
    return result


def _execute_organic_action(kind: str, slug: str, config: dict) -> dict:
    if kind == "price_update":
        price = (config.get("price_proposals") or {}).get(slug)
        if price is None:
            return {"status": "manual_required", "reason": "missing_price_proposal"}
        executor = (config.get("adapters") or {}).get("price_executor")
        return _reconcile_price_update(slug, price, executor)
    if kind == "free_promo":
        return _reconcile_free_promo(slug, config)
    # countdown_deal: no controller/adapter has been built for this lever yet
    # (only price_update and free_promo have one) — fail closed rather than
    # invent an execution path.
    return {"status": "manual_required", "reason": "no_controller_for_action_kind"}


def _run_organic_actions(plan: dict, readiness: dict, started_at: datetime, now: datetime, config: dict, ledger_path: Path) -> tuple[list, list]:
    executed, blocked = [], []
    for action in plan["actions"]:
        slug = action["slug"]
        # Computed up front (before the incident check) so every blocked
        # entry below uniformly carries "kind" — a downstream consumer
        # (e.g. Task 10's dashboard) should never need to special-case one
        # blocked-entry shape over another.
        kind = VARIABLE_ACTION_KIND.get(action.get("variable"))
        if slug in readiness["blocked_slugs"]:
            blocked.append({"slug": slug, "kind": kind, "reason": "title_incident_blocked"})
            continue
        if kind is None:
            blocked.append({"slug": slug, "kind": None, "variable": action.get("variable"), "reason": "unsupported_growth_action_variable"})
            continue
        allowed, reason = _authorize(kind, slug, started_at, {"now": now})
        if not allowed:
            blocked.append({"slug": slug, "kind": kind, "reason": reason})
            continue
        result = _execute_organic_action(kind, slug, config)
        entry = {"slug": slug, "kind": kind, **result}
        if result["status"] == "executed":
            _record_action_evidence(ledger_path, kind, slug, result.get("evidence") or {}, now)
            executed.append(entry)
        else:
            blocked.append(entry)
    return executed, blocked


def _run_ads_decisions(plan: dict, readiness: dict, policy_phase: str, started_at: datetime, now: datetime, config: dict, ledger_path: Path) -> tuple[list, list]:
    """amazon_ads is never planned by growth_planner (it only ever emits
    organic_test) — this is a second, independent decision path that only
    engages once the Growth Gate's phase window is open, for titles the
    scorer already classified "scale" this cycle."""
    if policy_phase != "growth":
        return [], []
    executed, blocked = [], []
    portfolio = config.get("ads_portfolio") or {
        "daily_spend_thb": 0, "monthly_spend_thb": 0, "month": None,
        "advertised_slugs": config.get("advertised_title_slugs") or [],
    }
    campaigns = config.get("ads_campaigns") or {}
    metrics = config.get("ads_metrics") or {}
    adapter = (config.get("adapters") or {}).get("ads")
    policy = {"started_at": started_at}
    scale_slugs = [row["slug"] for row in plan["portfolio"]["active"] if row["classification"] == "scale"]
    for slug in scale_slugs:
        if slug in readiness["blocked_slugs"]:
            blocked.append({"slug": slug, "kind": "amazon_ads", "reason": "title_incident_blocked"})
            continue
        campaign = campaigns.get(slug)
        title_metrics = metrics.get(slug, {})
        decision = ads_decision(title_metrics, campaign, portfolio, policy, now)
        if decision["action"] == "hold":
            continue
        # growth_state must carry BOTH this title's Growth Gate evidence
        # (royalty_growth_usd/kenp_delta/tracked_clicks — same key names
        # amazon_ads_controller already uses for the SAME purpose, see its
        # module docstring) for authorize_growth_action's own internal
        # ads_eligibility re-check, AND the portfolio/campaign budget
        # fields for its cap checks — omitting either makes an otherwise
        # Gate-eligible decision fail closed with the wrong reason.
        growth_state = {
            **title_metrics,
            "now": now,
            "advertised_title_slugs": portfolio.get("advertised_slugs") or [],
            "portfolio_daily_spend_thb": portfolio.get("daily_spend_thb", 0),
            "portfolio_monthly_spend_thb": portfolio.get("monthly_spend_thb", 0),
            "title_daily_budget_thb": (campaign or {}).get("daily_budget_thb", 0),
            "last_budget_increase_at": (campaign or {}).get("last_increase_at"),
        }
        # "stop"/"reduce" are risk-REDUCING mutations (amazon_ads_controller's
        # no-order-stop and break-even-ACOS paths) — they must be able to
        # turn off/down a losing campaign even when its growth signal has
        # gone cold (which is often WHY it's being stopped) or its proposed
        # budget is exactly 0. growth_policy.authorize_growth_action only
        # honors this when it's independently verifiable against
        # growth_state (title already advertised, proposed budget not
        # actually higher than current) — see its own docstring. "start"/
        # "increase" never set this, so every existing cap/eligibility/
        # cooldown check stays exactly as strict as before.
        extra_action = {"daily_budget_thb": decision["budget_thb"]}
        if decision["action"] in ("stop", "reduce"):
            extra_action["ads_intent"] = "decrease"
        allowed, reason = _authorize(
            "amazon_ads", slug, started_at, growth_state,
            extra_action=extra_action,
        )
        if not allowed:
            blocked.append({"slug": slug, "kind": "amazon_ads", "reason": reason})
            continue
        if adapter is None:
            blocked.append({"slug": slug, "kind": "amazon_ads", "reason": "no_ads_adapter_configured"})
            continue
        action = {
            "kind": "amazon_ads", "slug": slug, "action": decision["action"],
            "daily_budget_thb": decision["budget_thb"], "campaign_id": (campaign or {}).get("campaign_id"),
        }
        result = reconcile_ads_action(action, adapter)
        entry = {"slug": slug, "kind": "amazon_ads", **result}
        if result["status"] == "executed":
            _record_action_evidence(ledger_path, "amazon_ads", slug, result.get("evidence") or {}, now)
            executed.append(entry)
        else:
            blocked.append(entry)
    return executed, blocked


# ---------------------------------------------------------------------------
# Controller entrypoint
# ---------------------------------------------------------------------------

def run_growth_controller(config: dict, now: datetime, shadow: bool = True) -> dict:
    """Run one Growth Autopilot cycle. Pure with respect to its own module
    scope: every input (ledger/lock/state paths, titles, experiments,
    incidents, adapters, `now`) is injected via `config`/`now`, and nothing
    at module scope holds state between calls.

    shadow=True (the default) always writes a plan but never authorizes or
    executes anything — the safe default described in the CLI. shadow=False
    additionally authorizes and executes each planned action, but only if
    readiness["mutation_allowed"] is True (see _derive_readiness).
    """
    ledger_path = Path(config["ledger_path"])
    lock_path = Path(config.get("lock_path") or ledger_path.with_name("growth-autopilot.lock"))
    state_path = Path(config.get("state_path") or ledger_path.with_name("growth-autopilot-state.json"))
    started_at = config["started_at"]

    titles = config.get("titles") or []
    observations = _collect_observations(ledger_path, titles)

    incidents = _open_incidents(ledger_path) + list(config.get("incidents") or [])
    readiness = _derive_readiness(incidents)

    policy_phase = growth_phase(started_at, now)
    planner_phase = POLICY_PHASE_TO_PLANNER_PHASE.get(policy_phase, policy_phase)

    scored_titles = score_portfolio(titles, now)
    active_experiments = config.get("active_experiments")
    if active_experiments is None:
        active_experiments = _latest_active_experiments(ledger_path)
    plan = build_growth_plan(
        scored_titles=scored_titles,
        active_experiments=active_experiments,
        phase=planner_phase,
        now=now,
    )

    mode = MODE_SHADOW if shadow else MODE_EXECUTE

    with _file_lock(lock_path) as acquired:
        if not acquired:
            return {
                "generated_at": now.isoformat(),
                "mode": mode,
                "locked": True,
                "phase": policy_phase,
                "started_at": started_at.isoformat(),
                "readiness": readiness,
                "observations_collected": observations["count"],
                "scored_titles": scored_titles,
                "plan": None,
                "executed": [],
                "blocked": [],
                "reason": "lock_contention",
            }

        # record_growth_plan's idempotent-replay contract rides entirely on
        # plan["action_key"], which growth_planner scopes to the CALENDAR
        # DATE of `now` (not full wall-clock precision) — so two same-day
        # calls must persist byte-IDENTICAL content, or record_growth_plan
        # correctly (and loudly) raises "conflicting growth plan" on the
        # second one. planned_at must therefore be derived from that same
        # date, never from `now`'s exact wall-clock time — a real CLI
        # invoked twice in one day (e.g. --collect then --shadow --send)
        # would otherwise get a different planned_at each time and trip
        # that same false conflict.
        planned_at = datetime.combine(now.date(), datetime.min.time(), tzinfo=now.tzinfo).isoformat()
        plan_record = {**plan, "planned_at": planned_at, "status": "planned"}
        record_growth_plan(ledger_path, plan_record)

        executed: list = []
        blocked: list = []
        if not shadow and readiness["mutation_allowed"]:
            organic_executed, organic_blocked = _run_organic_actions(
                plan, readiness, started_at, now, config, ledger_path
            )
            ads_executed, ads_blocked = _run_ads_decisions(
                plan, readiness, policy_phase, started_at, now, config, ledger_path
            )
            executed = organic_executed + ads_executed
            blocked = organic_blocked + ads_blocked

        state = {
            "generated_at": now.isoformat(),
            "mode": mode,
            "locked": False,
            "phase": policy_phase,
            # Persisted so the CLI's _persisted_started_at can re-read the
            # Growth Gate's 30-day organic-window start on every later run —
            # without this, the window start would silently reset to "now"
            # every single invocation and the gate could never open.
            "started_at": started_at.isoformat(),
            "readiness": readiness,
            "observations_collected": observations["count"],
            "scored_titles": scored_titles,
            "plan": plan,
            "executed": executed,
            "blocked": blocked,
        }
        _write_atomic(state_path, state)

    return state


# ---------------------------------------------------------------------------
# Plain-language digest (Task 10)
# ---------------------------------------------------------------------------

def build_growth_digest(state: dict) -> str:
    """Plain-language Telegram digest for one Growth Autopilot state
    snapshot -- the SAME dict shape run_growth_controller writes to
    data/growth-autopilot-state.json. Pure function: state dict in, text
    out, no I/O. Lives in this pure module (not app.py) specifically so
    the cron-driven CLI (scripts/libra_growth_autopilot.py) can import it
    without pulling in the FastAPI app and its import-time side effects --
    the same coupling problem Task 9 already fixed once (see
    growth_authority_transferred's docstring). Mirrors the Task 10
    dashboard's own separation rule so an operator reading this on a phone
    can tell "Planned" (proposed, nothing happened) from "Executed with
    evidence" (adapter proof + verified before/after state) in one glance.
    Sending stays behind the project's existing Telegram transport
    (distribution_report.send_telegram) -- this function only ever builds
    the text."""
    if not state:
        return "Libra Growth Autopilot\nNo run recorded yet."

    plan = state.get("plan") or {}
    planned = plan.get("actions") or []
    executed = state.get("executed") or []
    blocked = state.get("blocked") or []
    readiness = state.get("readiness") or {}

    lines = [
        "Libra Growth Autopilot -- daily digest",
        f"Mode: {state.get('mode', 'unknown')}"
        + (" (LOCKED -- another run in progress)" if state.get("locked") else ""),
        f"Phase: {state.get('phase', 'unknown')}",
    ]
    if readiness.get("mutation_allowed"):
        lines.append("Readiness: OK, mutations allowed")
    else:
        lines.append(f"Readiness: BLOCKED -- {readiness.get('reason', 'unknown')}")
    if readiness.get("blocked_slugs"):
        lines.append(f"Blocked titles: {', '.join(readiness['blocked_slugs'])}")

    lines.append(f"Planned (not yet done): {len(planned)}")
    for item in planned[:10]:
        lines.append(f"  - {item.get('slug')} / {item.get('variable')}")

    lines.append(f"Executed with evidence: {len(executed)}")
    for item in executed[:10]:
        lines.append(f"  - {item.get('slug')} / {item.get('kind')}: {item.get('reason', 'executed')}")

    lines.append(f"Blocked actions: {len(blocked)}")
    for item in blocked[:10]:
        lines.append(f"  - {item.get('slug')} / {item.get('kind')}: {item.get('reason', 'unknown')}")

    return "\n".join(lines)


def growth_authority_transferred(marker_path: Path) -> bool:
    """True once authority has been deliberately transferred to the Growth
    Autopilot (Task 11) — the signal scripts/libra_profit_agent_daily.py
    uses to stay read-only. This is a plain marker FILE, not an inference
    from growth-autopilot-state.json: state.json's "mode" reflects only the
    most recent invocation's CLI flag, so inferring transfer from it would
    let a single ad-hoc/manual `--execute` smoke test (even one that
    mutates nothing) silently and irreversibly disable the legacy agent
    with no revert path. A marker file requires a deliberate, separate,
    documented operational step (Task 11's authority-transfer step) to
    create it, and is trivially reversible (delete the file). Fails closed
    to False on a missing/unreadable marker, since ABSENCE of evidence that
    authority transferred must never be read as transferred — today, with
    no such marker ever created, legacy behavior is provably unchanged."""
    return Path(marker_path).is_file()
