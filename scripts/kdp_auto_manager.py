#!/usr/bin/env python3
"""Write the Libra KDP Auto Manager agent state.

This is read-only. It does not mutate KDP, buy promotions, change pricing,
or publish books. It refreshes the advisory state used to manage the July
distribution experiment.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIBRA_DIR))

from app import build_dashboard_overview  # noqa: E402
from distribution_report import (  # noqa: E402
    CATEGORY_HEALTH_STATE,
    build_monitor,
    build_report,
    _load_json,
)

STATE_FILE = LIBRA_DIR / "data" / "kdp-agent-state.json"


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


def main() -> dict:
    state = build_state()
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        "kdp_auto_manager "
        f"status={state['overall']['status']} "
        f"score={state['overall']['score']} "
        f"blockers={state['blockers']['count']}"
    )
    return state


if __name__ == "__main__":
    main()
