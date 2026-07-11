#!/usr/bin/env python3
"""Run one guarded, auditable Libra profit-agent cycle."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIBRA_DIR))

from business_ledger import ingest_uploaded_title_costs, portfolio_financials  # noqa: E402
from distribution_report import send_telegram  # noqa: E402
from profit_agent import (  # noqa: E402
    ACTIVE_STATUSES,
    APPROVED_EXPERIMENTS,
    check_policy,
    create_initial_experiments,
    ensure_no_spend_mode,
    evaluate_experiment,
    propose_transition,
    record_action_result,
)

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
    try:
        listing = json.loads((KDP_DIR / slug / "listing.json").read_text(encoding="utf-8"))
        asin = str(listing.get("asin") or "").strip()
    except (OSError, json.JSONDecodeError):
        return False
    if not asin:
        return False
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT 1 FROM kdp_title_attribution WHERE snapshot_id = (SELECT id FROM kdp_snapshots ORDER BY observed_at DESC, id DESC LIMIT 1) AND asin = ?",
            (asin,),
        ).fetchone()
    return bool(row)


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


def run_daily(
    db_path: Path,
    state_path: Path,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
    send: bool = False,
) -> dict:
    now = now or datetime.now(timezone.utc)
    month = now.strftime("%Y-%m")

    if dry_run:
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
        experiments = create_initial_experiments(db_path, now)
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
        action = {
            "kind": "start_experiment",
            "slug": experiment["slug"],
            "variable": experiment["variable"],
            "cost_usd": 0,
        }
        context = {
            "no_spend": True,
            "active_experiments": sum(
                item["slug"] != experiment["slug"] for item in active
            ),
            "active_variable": experiment["variable"],
            "cooldown_slugs": cooldown_slugs,
        }
        policy_open, policy_reason = check_policy(action, context)
        policy_reasons.append(policy_reason)
        experiment_attribution_open = _title_attribution_complete(db_path, experiment["slug"])
        title_allows = experiment_attribution_open or not _needs_complete_attribution(experiment)
        can_advance = policy_open and freshness_open and overview_open and title_allows
        transition_input = financials
        if experiment["status"] == "evaluating" and can_advance:
            snapshot_id = _latest_snapshot_id(db_path)
            result = evaluate_experiment(
                experiment,
                {"contribution_profit_usd": financials["contribution_profit_usd"],
                 "attribution_complete": experiment_attribution_open, "cost_complete": financials.get("cost_complete")},
                [value for value in (experiment.get("baseline_snapshot_id"), snapshot_id) if value is not None],
            )
            transition_input = {"outcome": result["outcome"]}
        else:
            result = None
        changed = propose_transition(experiment, transition_input, now) if can_advance else experiment.copy()
        if experiment["status"] == "planned" and changed["status"] == "ready":
            changed["baseline_snapshot_id"] = _latest_snapshot_id(db_path)
            changed["baseline"] = {"period": "lifetime", "contribution_profit_usd": financials["contribution_profit_usd"]}
        changed["policy_reason"] = policy_reason
        advanced.append(changed)
        if changed["status"] != experiment["status"] and not dry_run:
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    "UPDATE experiments SET status = ?, earliest_evaluation_at = ?, baseline_json = ?, baseline_snapshot_id = ?, result_json = COALESCE(?, result_json) WHERE id = ?",
                    (changed["status"], changed.get("earliest_evaluation_at"), json.dumps(changed.get("baseline") or {}, sort_keys=True),
                     changed.get("baseline_snapshot_id"), json.dumps(result, sort_keys=True) if result else None, changed["id"]),
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
    args = parser.parse_args()
    state = run_daily(LEDGER_FILE, STATE_FILE, dry_run=args.dry_run, send=args.send)
    print(json.dumps(state, sort_keys=True))


if __name__ == "__main__":
    main()
