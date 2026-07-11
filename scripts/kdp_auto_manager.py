#!/usr/bin/env python3
"""Write the Libra KDP Auto Manager agent state.

This is read-only. It does not mutate KDP, buy promotions, change pricing,
or publish books. It refreshes the advisory state used to manage the July
distribution experiment.
"""

from __future__ import annotations

import json
import sys
import argparse
import subprocess
from datetime import date, timedelta
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIBRA_DIR))

from app import build_dashboard_overview  # noqa: E402
from distribution_report import (  # noqa: E402
    CATEGORY_HEALTH_STATE,
    build_monitor,
    build_report,
    kdp_agent_digest,
    send_telegram,
    _load_json,
)

STATE_FILE = LIBRA_DIR / "data" / "kdp-agent-state.json"
ACTION_LOG = LIBRA_DIR / "data" / "kdp-agent-actions.jsonl"


def build_state() -> dict:
    monitor = build_monitor(
        build_report(),
        overview=build_dashboard_overview(),
        category_health=_load_json(CATEGORY_HEALTH_STATE, {}),
    )
    return {
        "generated_at": monitor["generated_at"],
        "agent": monitor["kdp_agent"],
        "roles": monitor["actual_vs_plan"]["roles"],
        "actual_vs_plan": monitor["actual_vs_plan"]["metrics"],
        "overall": monitor["overall"],
        "blockers": monitor["blockers"],
    }


def _append_action_log(row: dict) -> None:
    ACTION_LOG.parent.mkdir(parents=True, exist_ok=True)
    ACTION_LOG.open("a", encoding="utf-8").write(json.dumps(row, ensure_ascii=False) + "\n")


def execute_free_actions(state: dict) -> list[dict]:
    results = []
    decisions = state.get("agent", {}).get("free_growth_engine", {}).get("decisions", [])
    for decision in decisions:
        if not decision.get("execute"):
            continue
        action = decision.get("action")
        if action == "free_post":
            evidence = {
                key: decision[key]
                for key in ("confirmation_id", "external_url")
                if isinstance(decision.get(key), str) and decision[key].strip()
            }
            if decision.get("verified_state_change") is True:
                evidence["verified_state_change"] = True
            result = {
                "action": action,
                "status": "executed" if evidence else "manual_required",
                "evidence": evidence,
                "detail": (
                    "external post confirmed"
                    if evidence
                    else "posting requires user/session context and has no external confirmation"
                ),
            }
            results.append(result)
            _append_action_log({"generated_at": state.get("generated_at"), "decision": decision, "result": result})
        elif action == "free_promo":
            slug = decision.get("slug")
            if not slug:
                result = {"action": action, "status": "skipped", "detail": "missing slug"}
            else:
                start = date.today() + timedelta(days=int(decision.get("start_offset_days") or 1))
                days = int(decision.get("days") or 2)
                cmd = [
                    "/usr/bin/python3",
                    "scripts/free_promo_auto.py",
                    "--force",
                    "--only",
                    slug,
                    "--start",
                    start.isoformat(),
                    "--days",
                    str(days),
                ]
                proc = subprocess.run(cmd, cwd=str(LIBRA_DIR), capture_output=True, text=True, timeout=1800)
                result = {
                    "action": action,
                    "slug": slug,
                    "status": "executed" if proc.returncode == 0 else "failed",
                    "returncode": proc.returncode,
                    "stdout_tail": proc.stdout[-1200:],
                    "stderr_tail": proc.stderr[-1200:],
                }
            results.append(result)
            _append_action_log({"generated_at": state.get("generated_at"), "decision": decision, "result": result})
    return results


def main(send: bool = False, execute_free: bool = False) -> dict:
    state = build_state()
    execution_results = execute_free_actions(state) if execute_free else []
    if execution_results:
        state["execution_results"] = execution_results
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    sent = send_telegram(kdp_agent_digest(state)) if send else False
    print(
        "kdp_auto_manager "
        f"status={state['overall']['status']} "
        f"score={state['overall']['score']} "
        f"blockers={state['blockers']['count']} "
        f"send={send} sent={sent} "
        f"execute_free={execute_free} executed={len(execution_results)}"
    )
    return state


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--send", action="store_true", help="send Telegram digest")
    parser.add_argument("--execute-free-actions", action="store_true", help="execute guarded free growth actions")
    args = parser.parse_args()
    main(send=args.send, execute_free=args.execute_free_actions)
