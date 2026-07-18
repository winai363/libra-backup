#!/usr/bin/env python3
"""Run one guarded, auditable Libra profit-agent cycle."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIBRA_DIR))

from business_ledger import ingest_uploaded_title_costs, portfolio_financials  # noqa: E402
from distribution_report import send_telegram  # noqa: E402
from profit_agent import (  # noqa: E402
    ACTIVE_STATUSES,
    APPROVED_EXPERIMENTS,
    ATTRIBUTION_ABSENT_ZERO_BOUND_USD,
    check_policy,
    create_pending_action,
    create_initial_experiments,
    ensure_no_spend_mode,
    evaluate_experiment,
    latest_experiment_action,
    load_all_experiments,
    propose_transition,
    record_action_result,
    title_financial_boundary,
)
from profit_pace import build_pace_controller, snapshot_revenue_windows  # noqa: E402

LEDGER_FILE = LIBRA_DIR / "data" / "libra-business.db"
STATE_FILE = LIBRA_DIR / "data" / "profit-agent-state.json"
KDP_DIR = LIBRA_DIR.parent / "kdp"


def _latest_observation(db_path: Path) -> datetime | None:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT observed_at FROM kdp_snapshots ORDER BY observed_at DESC LIMIT 1"
        ).fetchone()
    return datetime.fromisoformat(row[0]) if row else None


def _latest_snapshot_id(db_path: Path) -> int | None:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute("SELECT id FROM kdp_snapshots ORDER BY observed_at DESC, id DESC LIMIT 1").fetchone()
    return row[0] if row else None


def _title_attribution_complete(db_path: Path, slug: str) -> bool:
    """KDP only lists titles with top-N earning activity per snapshot, so an
    absent ASIN means "at most the snapshot's unattributed remainder", not
    "unknown". Absence counts as attributed-zero while that remainder stays
    within ATTRIBUTION_ABSENT_ZERO_BOUND_USD — otherwise zero-sale titles
    (exactly the books the free-promo lane exists for) can never advance."""
    try:
        listing = json.loads((KDP_DIR / slug / "listing.json").read_text(encoding="utf-8"))
        asin = str(listing.get("asin") or "").strip()
    except (OSError, json.JSONDecodeError):
        return False
    if not asin:
        return False
    if not db_path.exists():
        return False
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as connection:
            snapshot = connection.execute(
                "SELECT id, royalties_usd FROM kdp_snapshots ORDER BY observed_at DESC, id DESC LIMIT 1"
            ).fetchone()
            if not snapshot:
                return False
            row = connection.execute(
                "SELECT 1 FROM kdp_title_attribution WHERE snapshot_id = ? AND asin = ?",
                (snapshot[0], asin),
            ).fetchone()
            if row:
                return True
            attributed = connection.execute(
                "SELECT COALESCE(SUM(royalties_usd), 0) FROM kdp_title_attribution WHERE snapshot_id = ?",
                (snapshot[0],),
            ).fetchone()[0]
    except sqlite3.Error:
        return False
    unattributed = max(0.0, float(snapshot[1]) - float(attributed))
    return unattributed <= ATTRIBUTION_ABSENT_ZERO_BOUND_USD


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def _persisted_mode_start(state_path: Path) -> str | None:
    try:
        value = json.loads(state_path.read_text(encoding="utf-8")).get("mode_started_at")
        return datetime.fromisoformat(value).isoformat() if value else None
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return None


def _mode_revenue(db_path: Path, started_at: datetime | None = None) -> tuple[float, float, float]:
    """Return mode-window revenue plus conservative 7/14-day deltas."""
    if not db_path.exists():
        return 0.0, 0.0, 0.0
    try:
        with sqlite3.connect(db_path) as connection:
            rows = connection.execute(
                "SELECT observed_at, month, royalties_usd FROM kdp_snapshots "
                "ORDER BY observed_at ASC, id ASC"
            ).fetchall()
    except sqlite3.Error:
        return 0.0, 0.0, 0.0
    if not rows:
        return 0.0, 0.0, 0.0
    windows = snapshot_revenue_windows(
        rows, datetime.fromisoformat(rows[-1][0]), started_at=started_at
    )
    return windows["mode"], windows["days_7"], windows["days_14"]


def _policy_registry(db_path: Path, experiments: list[dict]) -> list[dict]:
    if not db_path.exists():
        return experiments
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'experiments'"
        ).fetchone()
        if not table:
            return experiments
        return [dict(row) for row in connection.execute(
            "SELECT slug, variable, status, earliest_evaluation_at FROM experiments"
        )]


def _needs_complete_attribution(experiment: dict) -> bool:
    return experiment["status"] in {"ready", "evaluating"}


def complete_manual_action(
    db_path: Path,
    action_key: str,
    evidence: dict,
    *,
    now: datetime | None = None,
) -> dict:
    """Record operator/browser evidence and resume a manual experiment safely."""
    now = now or datetime.now(timezone.utc)
    with sqlite3.connect(db_path) as connection:
        connection.row_factory = sqlite3.Row
        action_row = connection.execute(
            "SELECT * FROM agent_actions WHERE action_key=? ORDER BY attempt DESC,id DESC LIMIT 1",
            (action_key,),
        ).fetchone()
        if not action_row:
            raise ValueError("unknown manual action key")
        experiment = connection.execute(
            "SELECT id,status,evaluation_kind FROM experiments WHERE id=?",
            (action_row["experiment_id"],),
        ).fetchone()
    if not experiment or experiment["status"] not in {"manual_required", "failed"}:
        raise ValueError("experiment is not awaiting manual completion")
    if int(action_row["attempt"]) >= 3:
        raise ValueError("manual action retry limit reached")

    original = json.loads(action_row["action_json"])
    retry = {
        **original,
        "attempt": int(action_row["attempt"]) + 1,
        "manual_completion": True,
    }
    recorded = record_action_result(
        db_path,
        retry,
        {**evidence, "observed_at": now.isoformat()},
    )
    if recorded["status"] == "executed":
        delay = timedelta(days=14) if experiment["evaluation_kind"] in ("commercial", "price") else timedelta(hours=72)
        with sqlite3.connect(db_path) as connection:
            connection.execute(
                "UPDATE experiments SET status='cooldown', earliest_evaluation_at=?, action_executed_at=? WHERE id=?",
                ((now + delay).isoformat(), now.isoformat(), experiment["id"]),
            )
    return recorded


def run_daily(
    db_path: Path,
    state_path: Path,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    send: bool = False,
    executor=None,
) -> dict:
    now = now or datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")

    if dry_run:
        policy_mode = None
        if db_path.exists():
            financials = portfolio_financials(db_path, month)
            latest = _latest_observation(db_path)
        else:
            financials = {
                "verified_royalties_usd": 0.0,
                "attributed_royalties_usd": 0.0,
                "unattributed_royalties_usd": 0.0,
                "snapshot_count": 0,
                "contribution_profit_usd": 0.0,
            }
            latest = None
        experiments = [
            {
                "id": None,
                **item,
                "baseline": {},
                "started_at": now.isoformat(),
                "earliest_evaluation_at": None,
                "success_threshold": {},
                "stop_threshold": {},
                "max_direct_cost_usd": 0,
                "status": "planned",
                "result": None,
            }
            for item in APPROVED_EXPERIMENTS
        ]
    else:
        ingest_uploaded_title_costs(db_path, KDP_DIR, checked_at=now.isoformat())
        financials = portfolio_financials(db_path, month)
        latest = _latest_observation(db_path)
        create_initial_experiments(db_path, now)
        experiments = load_all_experiments(db_path)
        policy_mode = ensure_no_spend_mode(db_path, now)

    mode_started_at = _persisted_mode_start(state_path)
    if mode_started_at is None:
        mode_started_at = min(
            (item["started_at"] for item in experiments),
            default=now.isoformat(),
        )

    freshness_open = bool(
        latest and (now.astimezone(timezone.utc) - latest.astimezone(timezone.utc)).total_seconds() <= 86400
    )
    overview_open = bool(financials.get("overview_ingestion_complete"))
    attribution_open = bool(financials.get("title_attribution_complete"))
    registry = _policy_registry(db_path, experiments)
    active = [item for item in registry if item["status"] in ACTIVE_STATUSES]
    cooldown_slugs = {
        item["slug"] for item in registry if item["status"] == "cooldown"
    }
    gates = {
        "policy": "open",
        "freshness": "open" if freshness_open else "closed",
        "overview_ingestion": "open" if overview_open else "closed",
        "title_attribution": "open" if attribution_open else "partial",
        "cost_completeness": "open" if financials.get("cost_complete") else "closed",
    }

    advanced = []
    policy_reasons = []
    for experiment in experiments:
        action = experiment.get("action") or {"kind": "start_experiment", "cost_usd": 0}
        if experiment["status"] == "planned":
            action = {"kind": "start_experiment", "slug": experiment["slug"], "variable": experiment["variable"], "cost_usd": 0}
        elif experiment["status"] in {"cooldown", "evaluating"}:
            action = {"kind": "evaluate_experiment", "slug": experiment["slug"], "cost_usd": 0}
        context = {
            "policy": policy_mode,
            "no_spend": dry_run,
            "now": now,
            "active_experiments": sum(
                item["slug"] != experiment["slug"] for item in active
            ),
            "active_variable": experiment["variable"],
            "cooldown_slugs": cooldown_slugs,
            "title_in_cooldown": experiment["status"] == "cooldown" and action["kind"] != "evaluate_experiment",
        }
        policy_open, policy_reason = check_policy(action, context)
        policy_reasons.append(policy_reason)
        try:
            listing = json.loads((KDP_DIR / experiment["slug"] / "listing.json").read_text(encoding="utf-8"))
            asin = str(listing.get("asin") or experiment.get("asin") or "").strip()
        except (OSError, json.JSONDecodeError):
            asin = str(experiment.get("asin") or "").strip()
        experiment_attribution_open = bool(asin and _title_attribution_complete(db_path, experiment["slug"]))
        title_allows = experiment_attribution_open or not _needs_complete_attribution(experiment)
        cost_allows = experiment["status"] != "ready" or bool((experiment.get("baseline") or {}).get("cost_complete"))
        can_advance = policy_open and freshness_open and overview_open and title_allows and cost_allows
        transition_input = {}
        result = None
        changed = experiment.copy()
        if dry_run:
            changed = experiment.copy()
        elif experiment["status"] == "planned" and can_advance:
            snapshot_id = _latest_snapshot_id(db_path)
            baseline = title_financial_boundary(db_path, asin, snapshot_id, slug=experiment["slug"]) if asin and snapshot_id else {"complete": False}
            changed = propose_transition(experiment, {}, now)
            changed.update({"asin": asin, "baseline_snapshot_id": snapshot_id, "baseline": baseline})
        elif experiment["status"] == "ready" and can_advance:
            pending = create_pending_action(db_path, experiment, now)
            changed = propose_transition(experiment, {}, now)
            if asin and experiment.get("baseline_snapshot_id"):
                # Same frozen snapshot, recomputed flags: a baseline stored under
                # the old absence=incomplete rule would poison the evaluation
                # ("inconclusive" forever) even though its numbers are unchanged.
                changed["baseline"] = title_financial_boundary(
                    db_path, asin, experiment["baseline_snapshot_id"], slug=experiment["slug"]
                )
            execution_result = executor(pending) if executor else {"returncode": 0}
            recorded = record_action_result(db_path, pending, {**(execution_result or {"returncode": 0}), "observed_at": now.isoformat()})
            if recorded["status"] == "executed":
                external = {"status": "executed"}
                state_change = recorded["evidence"].get("verified_state_change")
                if state_change:
                    external.update({"before_snapshot_id": state_change["before_snapshot_id"], "after_snapshot_id": state_change["after_snapshot_id"], "before": state_change["before"], "after": state_change["after"]})
                changed = propose_transition(changed, {"external_action": external}, now)
                changed["action_executed_at"] = now.isoformat()
            elif recorded["status"] in {"failed", "manual_required"}:
                changed["status"] = recorded["status"]
        elif experiment["status"] == "executing":
            recorded = latest_experiment_action(db_path, experiment["id"])
            if recorded and recorded["status"] == "executed":
                state_change = recorded["evidence"].get("verified_state_change") or {}
                changed = propose_transition(experiment, {"external_action": {"status": "executed", **state_change}}, now)
            elif recorded and recorded["status"] in {"failed", "manual_required"}:
                changed["status"] = recorded["status"]
        elif experiment["status"] in {"failed", "manual_required"} and executor and can_advance:
            previous = latest_experiment_action(db_path, experiment["id"])
            if previous and int(previous["attempt"]) < 3:
                retry = {
                    **previous["action"],
                    "attempt": int(previous["attempt"]) + 1,
                    "manual_completion": experiment["status"] == "manual_required",
                }
                execution_result = executor(retry)
                recorded = record_action_result(
                    db_path, retry,
                    {**(execution_result or {"returncode": 0}), "observed_at": now.isoformat()},
                )
                if recorded["status"] == "executed":
                    delay = timedelta(days=14) if experiment.get("evaluation_kind") in ("commercial", "price") else timedelta(hours=72)
                    changed.update({
                        "status": "cooldown",
                        "earliest_evaluation_at": (now + delay).isoformat(),
                        "action_executed_at": now.isoformat(),
                    })
                else:
                    changed["status"] = recorded["status"]
        if experiment["status"] == "evaluating" and can_advance:
            snapshot_id = _latest_snapshot_id(db_path)
            current = title_financial_boundary(db_path, experiment["asin"], snapshot_id, slug=experiment["slug"])
            result = evaluate_experiment(
                experiment,
                current,
                [value for value in (experiment.get("baseline_snapshot_id"), snapshot_id) if value is not None],
                observed_at=now,
            )
            transition_input = {"outcome": result["outcome"]}
            changed = propose_transition(experiment, transition_input, now)
        elif experiment["status"] == "cooldown" and can_advance:
            changed = propose_transition(experiment, {}, now)
        changed["policy_reason"] = policy_reason
        advanced.append(changed)
        if changed["status"] != experiment["status"] and not dry_run:
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    "UPDATE experiments SET status = ?, earliest_evaluation_at = ?, baseline_json = ?, baseline_snapshot_id = ?, result_json = COALESCE(?, result_json), asin=?, action_executed_at=? WHERE id = ?",
                    (changed["status"], changed.get("earliest_evaluation_at"), json.dumps(changed.get("baseline") or {}, sort_keys=True),
                     changed.get("baseline_snapshot_id"), json.dumps(result, sort_keys=True) if result else None, changed.get("asin"), changed.get("action_executed_at"), changed["id"]),
                )
            record_action_result(
                db_path,
                {"kind": "internal_transition", "slug": changed["slug"], "experiment_id": changed["id"],
                 "action_key": f"experiment:{changed['id']}:{experiment['status']}:{changed['status']}"},
                {"observed_at": now.isoformat(), "internal_transition": {"before": experiment["status"], "after": changed["status"]}},
            )

    if any(reason != "allowed" for reason in policy_reasons):
        gates["policy"] = "closed"

    state = {
        "generated_at": now.isoformat(),
        "mode_started_at": mode_started_at,
        "mode": "dry_run" if dry_run else "live",
        "policy": None if dry_run else policy_mode,
        "financials": financials,
        "gates": gates,
        "gate_reason": next((reason for reason in policy_reasons if reason != "allowed"), "allowed"),
        "experiments": advanced,
    }
    try:
        pace_start = datetime.fromisoformat(mode_started_at)
        pace_end = datetime.fromisoformat(policy_mode["ends_at"]) if policy_mode else pace_start + timedelta(days=90)
    except (KeyError, TypeError, ValueError):
        pace_start = now
        pace_end = now + timedelta(days=90)
    mode_revenue, revenue_7d, revenue_14d = _mode_revenue(db_path, pace_start)
    state["pace"] = build_pace_controller(
        mode_revenue, 75.0, pace_start, pace_end, now, revenue_7d, revenue_14d,
        data_fresh=freshness_open,
    )
    if not dry_run:
        _write_atomic(state_path, state)
        if send:
            sent = send_telegram(
                "Libra Profit Agent\n"
                f"Operations gates: {gates}\n"
                f"Verified royalties: ${financials['verified_royalties_usd']:.2f}"
            )
            state["telegram_sent"] = sent
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="calculate without writing files or SQLite")
    parser.add_argument("--send", action="store_true", help="send the existing Telegram digest")
    parser.add_argument("--complete-action", help="action key awaiting verified manual completion")
    parser.add_argument("--evidence-json", help="JSON object containing confirmation or before/after evidence")
    parser.add_argument("--execute-actions", action="store_true",
                        help="auto-execute validated zero-cost actions on KDP (safety gates + real-state verification)")
    args = parser.parse_args()
    if args.complete_action:
        if not args.evidence_json:
            parser.error("--complete-action requires --evidence-json")
        evidence = json.loads(args.evidence_json)
        if not isinstance(evidence, dict):
            parser.error("--evidence-json must decode to an object")
        print(json.dumps(complete_manual_action(LEDGER_FILE, args.complete_action, evidence), sort_keys=True))
        return
    executor = None
    if args.execute_actions and not args.dry_run:
        from kdp_action_executor import build_executor
        executor = build_executor()
    state = run_daily(LEDGER_FILE, STATE_FILE, dry_run=args.dry_run, send=args.send, executor=executor)
    if executor is not None:
        # Propose the next experiment from observable state under the same
        # gates the executor enforces. Never allowed to break the daily run.
        try:
            from experiment_proposer import run_proposer
            state["proposer"] = run_proposer()
        except Exception as exc:
            state["proposer"] = {"error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(state, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
