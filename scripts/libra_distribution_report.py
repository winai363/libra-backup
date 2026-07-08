#!/usr/bin/env python3
"""Generate Libra distribution HTML/JSON and optionally send Telegram."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import distribution_report  # noqa: E402


if __name__ == "__main__":
    send = "--send" in sys.argv
    report = distribution_report.main(send=send)
    print(
        "distribution_report "
        f"royalties=${report['money']['mtd_royalties_usd']:.2f} "
        f"orders={report['money']['mtd_orders_all_types']} "
        f"lovelybooks={report['lovelybooks']['status']} "
        f"send={send}"
    )
