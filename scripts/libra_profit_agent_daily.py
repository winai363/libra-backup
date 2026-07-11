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

from business_ledger import portfolio_financials  # noqa: E402
from distribution_report import send_telegram  # noqa: E402
from profit_agent import (  # noqa: E402
    APPROVED_EXPERIMENTS,
    check_policy,
    create_initial_experiments,
    propose_transition,
    record_action_result,
)

LEDGER_FILE = LIBRA_DIR / "data" / "libra-business.db"
STATE_FILE = LIBRA_DIR / "data" / "profit-agent-state.json"


def _latest_observation(db_path: Path) -> datetime | None:
    with sqlite3.connect(db_path) as connection:
        row = connection.execute(
            "SELECT observed_at FROM kdp_snapshots ORDER BY observed_at DESC LIMIT 1"
        ).fetchone()
    return datetime.fromisoformat(row[0]) if row else None


def _write_atomic(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


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
        financials = portfolio_financials(db_path, month)
        latest = _latest_observation(db_path)
        experiments = create_initial_experiments(db_path, now)

    freshness_open = bool(
        latest and (now.astimezone(timezone.utc) - latest.astimezone(timezone.utc)).total_seconds() <= 86400
    )
    reconciliation_open = bool(
        financials.get("snapshot_count")
        and abs(float(financials.get("unattributed_royalties_usd") or 0.0)) < 0.01
    )
    policy_open, policy_reason = check_policy(
        {"kind": "start_experiment", "cost_usd": 0},
        {"no_spend": True, "active_experiments": 0},
    )
    gates = {
        "policy": "open" if policy_open else "closed",
        "freshness": "open" if freshness_open else "closed",
        "reconciliation": "open" if reconciliation_open else "closed",
    }

    can_advance = policy_open and freshness_open and reconciliation_open
    advanced = []
    for experiment in experiments:
        changed = propose_transition(experiment, financials, now) if can_advance else experiment
        advanced.append(changed)
        if changed["status"] != experiment["status"] and not dry_run:
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    "UPDATE experiments SET status = ?, earliest_evaluation_at = ? WHERE id = ?",
                    (changed["status"], changed.get("earliest_evaluation_at"), changed["id"]),
                )
            record_action_result(
                db_path,
                {"kind": "start_experiment", "slug": changed["slug"]},
                {"verified_state_change": True, "observed_at": now.isoformat()},
            )

    state = {
        "generated_at": now.isoformat(),
        "mode": "dry_run" if dry_run else "live",
        "financials": financials,
        "gates": gates,
        "gate_reason": policy_reason,
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
