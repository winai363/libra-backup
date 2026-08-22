#!/usr/bin/env python3
"""Offline commerce reconciliation — replays the durable inbox, calls nobody.

    python3 scripts/libra_commerce_reconcile.py --ledger PATH --mode test --dry-run
    python3 scripts/libra_commerce_reconcile.py --ledger PATH --mode test --apply

`--dry-run` opens the database read-only and counts candidates. `--apply` is
permitted only in test mode. Output is a single JSON object and never contains a
payload, signature, secret, or customer detail.

Exit codes: 0 clean · 2 usage/config refused · 3 attention required
(conflicts, manual_required, or open incidents).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRA_DIR))

from commerce_ledger import open_incidents  # noqa: E402
from commerce_reconciliation import retry_pending  # noqa: E402


def _counts(ledger: Path) -> dict:
    """Read-only tally. Opens the file with mode=ro so it cannot create schema."""
    uri = f"file:{ledger.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT processing_state, COUNT(*) n FROM commerce_events GROUP BY processing_state"
        ).fetchall()
        conflicts = connection.execute(
            "SELECT COUNT(*) FROM commerce_event_conflicts"
        ).fetchone()[0]
    by_state = {row["processing_state"]: int(row["n"]) for row in rows}
    return {
        "events_seen": sum(by_state.values()),
        "pending": by_state.get("pending_reconciliation", 0) + by_state.get("received", 0),
        "reconciled": by_state.get("reconciled", 0),
        "manual_required": by_state.get("manual_required", 0),
        "conflicts": int(conflicts),
    }


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Offline commerce reconciliation")
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--mode", required=True)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)

    if args.mode != "test":
        print("live_mode_disabled: this lane runs in test mode only", file=sys.stderr)
        return 2

    ledger = Path(args.ledger)
    if not ledger.exists():
        print(f"ledger_not_found: {ledger}", file=sys.stderr)
        return 2

    reconciled_now = 0
    if args.apply:
        outcome = retry_pending(ledger)
        reconciled_now = outcome["reconciled"]

    try:
        counts = _counts(ledger)
    except sqlite3.OperationalError as exc:
        print(f"ledger_unreadable: {exc}", file=sys.stderr)
        return 2

    incidents = [i for i in open_incidents(ledger)]
    report = {
        "mode": "test",
        "events_seen": counts["events_seen"],
        "reconciled": reconciled_now if args.apply else counts["reconciled"],
        "pending": counts["pending"],
        "conflicts": counts["conflicts"],
        "manual_required": counts["manual_required"],
        "open_incidents": len(incidents),
        "incident_codes": sorted({str(i["error_code"]) for i in incidents}),
        "external_calls": 0,
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))

    needs_attention = report["conflicts"] or report["manual_required"] or report["open_incidents"]
    return 3 if needs_attention else 0


if __name__ == "__main__":
    raise SystemExit(main())
