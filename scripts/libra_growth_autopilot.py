#!/usr/bin/env python3
"""CLI entrypoint for the Libra Growth Autopilot controller.

Wires growth_autopilot.run_growth_controller to real production paths and
adapters. Shadow is the safe default — --execute must be passed explicitly
to authorize and execute anything.

Evidence-aggregation gap (documented, not hidden): portfolio_scorer needs
per-title verified deltas (royalty_delta_usd, kenp_delta, tracked_clicks,
conversion_signal, verified_placements, risk_active). No prior task built a
collector that turns raw growth_evidence rows into those per-title deltas
yet, so `_default_titles` below defaults every LIVE title to all-zero
signals (score 0, classification "test") until that aggregation exists —
an honest reflection of "no growth evidence has accumulated yet", not a
guess. This keeps today's shadow/execute runs safe (zero paid spend, only
bounded organic_test proposals) while remaining ready to score real
evidence the moment an aggregator supplies it via config["titles"].
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(LIBRA_DIR))

from distribution_report import send_telegram  # noqa: E402
from growth_autopilot import (  # noqa: E402
    build_growth_digest,
    build_growth_gate_report,
    collect_growth_observations,
    format_growth_gate_report,
    run_growth_controller,
    verify_growth_state,
)

# LIBRA_LEDGER lets a verification run point at a copied ledger (see the
# plan's Task 11 dry-run step) without touching the real production
# database — state/lock paths move alongside it so a shadow run against a
# copy never contends with a real run's lock or overwrites its state.
_LEDGER_OVERRIDE = os.environ.get("LIBRA_LEDGER")
LEDGER_FILE = Path(_LEDGER_OVERRIDE) if _LEDGER_OVERRIDE else LIBRA_DIR / "data" / "libra-business.db"
STATE_FILE = LEDGER_FILE.with_name("growth-autopilot-state.json") if _LEDGER_OVERRIDE else LIBRA_DIR / "data" / "growth-autopilot-state.json"
LOCK_FILE = LEDGER_FILE.with_name("growth-autopilot.lock") if _LEDGER_OVERRIDE else LIBRA_DIR / "data" / "growth-autopilot.lock"
KDP_DIR = LIBRA_DIR.parent / "kdp"


def _persisted_started_at(state_path: Path, now: datetime) -> datetime:
    """The Growth Gate's 30-day organic window starts the first time this
    controller ever runs — persisted in the state file and re-read on every
    later run so restarting the process never resets the clock."""
    try:
        value = json.loads(state_path.read_text(encoding="utf-8")).get("started_at")
        if value:
            return datetime.fromisoformat(value)
    except (OSError, ValueError):
        pass
    return now


def _default_titles() -> list[dict]:
    """One row per LIVE KDP title, all-zero verified signals until a real
    evidence aggregator exists (see module docstring)."""
    titles = []
    for listing_path in sorted(KDP_DIR.glob("*/listing.json")):
        try:
            listing = json.loads(listing_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if str(listing.get("live_status") or "").upper() != "LIVE":
            continue
        titles.append({
            "slug": listing_path.parent.name,
            "royalty_delta_usd": 0, "kenp_delta": 0, "tracked_clicks": 0,
            "conversion_signal": 0, "verified_placements": 0, "risk_active": False,
        })
    return titles


def _default_config(now: datetime) -> dict:
    started_at = _persisted_started_at(STATE_FILE, now)
    return {
        "ledger_path": LEDGER_FILE,
        "lock_path": LOCK_FILE,
        "state_path": STATE_FILE,
        "started_at": started_at,
        "titles": _default_titles(),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--shadow", action="store_true", help="plan only, never authorize or execute (safe default)")
    mode.add_argument("--execute", action="store_true", help="authorize and execute planned actions")
    mode.add_argument("--collect", action="store_true",
                       help="ingest observations only (09:30, before planning); no planning/authorization/execution")
    mode.add_argument("--verify", action="store_true",
                       help="reconcile the day's executed actions against verifiable evidence (20:30, after the day's run); read-only externally")
    mode.add_argument("--growth-gate-report", action="store_true",
                       help="read-only per-title Growth Gate status report (day-30 gate check); no state mutation")
    parser.add_argument("--send", action="store_true", help="send a Telegram digest (only valid with --shadow/--execute)")
    args = parser.parse_args()

    # --send composes only with --shadow/--execute -- the only pairing the
    # plan's own Task 11 Step 5 cron lines ever use (`--shadow --send` at
    # 10:00). --collect and --verify run unattended at 09:30/20:30 with no
    # --send in the plan, and --growth-gate-report is a manual, read-only
    # check with nothing "digest-worthy" to send (build_growth_digest's
    # shape assumes a run_growth_controller state dict, which none of these
    # three new modes produce).
    if args.send and not (args.shadow or args.execute):
        parser.error("--send may only be combined with --shadow or --execute")

    now = datetime.now(timezone.utc)

    if args.collect:
        config = _default_config(now)
        result = collect_growth_observations(config, now)
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return

    if args.verify:
        config = _default_config(now)
        result = verify_growth_state(config, now)
        print(json.dumps(result, indent=2, sort_keys=True, default=str))
        return

    if args.growth_gate_report:
        config = _default_config(now)
        report = build_growth_gate_report(config["titles"], config["started_at"], now)
        print(format_growth_gate_report(report))
        return

    config = _default_config(now)
    state = run_growth_controller(config, now=now, shadow=not args.execute)

    if args.send:
        state["telegram_sent"] = send_telegram(build_growth_digest(state))

    print(json.dumps(state, indent=2, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
