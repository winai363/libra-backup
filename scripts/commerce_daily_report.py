#!/usr/bin/env python3
"""Daily Telegram digest for the direct-sales lane — verified money only.

Sends nothing when there is nothing (no orders, no incidents), so a quiet lane
stays quiet. Never includes payloads, tokens, or customer details.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRA_DIR))
sys.path.insert(0, str(LIBRA_DIR / "scripts"))

from commerce_reporting import commerce_summary  # noqa: E402
from commerce_growth import commerce_growth_decision  # noqa: E402
from distribution_report import send_telegram  # noqa: E402

LEDGER = LIBRA_DIR / "data" / "libra-business.db"


def _money(minor: int | None, currency: str) -> str:
    if minor is None:
        return "unknown"
    return f"{minor // 100}.{minor % 100:02d} {currency}"


def build_message(summary: dict) -> str | None:
    lines = []
    for currency, row in sorted(summary["by_currency"].items()):
        if not (row["orders"] or row["unverified_orders"] or row["payout_minor"]):
            continue
        lines.append(
            f"{currency}: verified {_money(row['verified_gross_minor'], currency)}"
            f" · refunded {_money(row['refunded_minor'], currency)}"
            f" · net {_money(row['verified_net_sales_minor'], currency)}"
            f" · orders {row['orders']}"
            + (f" · unverified {row['unverified_orders']}" if row["unverified_orders"] else "")
            + (f" · payout {_money(row['payout_minor'], currency)}" if row["payout_minor"] else "")
        )
    incidents = summary["open_incidents"]
    if incidents:
        codes = sorted({str(i["error_code"]) for i in incidents})
        lines.append(f"⚠️ open incidents: {len(incidents)} ({', '.join(codes)})")
    if not lines:
        return None
    decision = commerce_growth_decision({
        "verified_sales": sum(r["orders"] for r in summary["by_currency"].values()),
        "open_incidents": incidents,
    })
    lines.append(f"growth: {decision['status']} (paid spend 0)")
    return "Libra direct sales (test mode)\n" + "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    message = build_message(commerce_summary(LEDGER))
    if message is None:
        print(json.dumps({"sent": False, "reason": "nothing_to_report"}))
        return 0
    if args.dry_run:
        print(message)
        return 0
    sent = send_telegram(message)
    print(json.dumps({"sent": bool(sent)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
