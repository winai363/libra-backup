"""Currency-separated commerce reporting.

Two rules carry the whole module:

- An unknown is reported as ``None`` and flagged incomplete. Coercing a missing
  fee to zero would overstate profit, which is the failure mode that makes a
  business think a loss-making product is fine.
- Currencies are never summed or converted. Without a verified FX rate, source
  and timestamp, a combined total is a fabrication.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from commerce_ledger import open_incidents

ORDER_COUNTED_STATES = ("paid_verified", "partially_refunded", "refunded")


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(path))
    connection.row_factory = sqlite3.Row
    return connection


def _window(start: str | None, end: str | None) -> tuple:
    clause = ""
    params: list = []
    if start:
        clause += " AND ordered_at >= ?"
        params.append(start)
    if end:
        clause += " AND ordered_at <= ?"
        params.append(end + "T23:59:59+00:00" if len(end) == 10 else end)
    return clause, params


def commerce_summary(path: Path, *, start: str | None = None, end: str | None = None) -> dict:
    path = Path(path)
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "test",
        "window": {"start": start, "end": end},
        "by_currency": {},
        "open_incidents": [],
        # A campaign cannot be credited with a sale until a controlled
        # transaction proves the click id survives Payhip checkout.
        "attribution": {
            "status": "unknown",
            "verified_sales": 0,
            "reason": "click id round trip not yet proven",
        },
    }
    if not path.exists():
        return result

    clause, params = _window(start, end)
    with _connect(path) as connection:
        try:
            orders = connection.execute(
                "SELECT currency,"
                " SUM(gross_minor) gross,"
                " COUNT(*) orders,"
                " SUM(CASE WHEN payhip_fee_minor IS NULL THEN 1 ELSE 0 END) payhip_missing,"
                " SUM(CASE WHEN stripe_fee_minor IS NULL THEN 1 ELSE 0 END) stripe_missing,"
                " SUM(COALESCE(payhip_fee_minor, 0)) payhip_fee,"
                " SUM(COALESCE(stripe_fee_minor, 0)) stripe_fee"
                f" FROM commerce_orders WHERE status IN {ORDER_COUNTED_STATES}{clause}"
                " GROUP BY currency",
                params,
            ).fetchall()
        except sqlite3.OperationalError:
            return result

        # Currencies where money was attempted but is not verified must still
        # appear: a vanished row reads as "nothing happened", when in fact an
        # order exists and is stuck.
        unverified = connection.execute(
            "SELECT currency, COUNT(*) stuck FROM commerce_orders"
            f" WHERE status NOT IN {ORDER_COUNTED_STATES}{clause} GROUP BY currency",
            params,
        ).fetchall()

        refunds = connection.execute(
            "SELECT r.currency, SUM(r.amount_minor) refunded FROM commerce_refunds r"
            " JOIN commerce_orders o ON o.provider_order_id = r.provider_order_id"
            f" WHERE r.status='succeeded'{clause.replace('ordered_at', 'o.ordered_at')}"
            " GROUP BY r.currency",
            params,
        ).fetchall()

        payouts = connection.execute(
            "SELECT currency, SUM(amount_minor) paid,"
            " SUM(CASE WHEN status='reconciled' THEN amount_minor ELSE 0 END) reconciled"
            " FROM commerce_payouts GROUP BY currency"
        ).fetchall()

    refunded_by_currency = {row["currency"]: int(row["refunded"] or 0) for row in refunds}
    payout_by_currency = {
        row["currency"]: (int(row["paid"] or 0), int(row["reconciled"] or 0)) for row in payouts
    }

    for row in orders:
        currency = row["currency"]
        gross = int(row["gross"] or 0)
        refunded = refunded_by_currency.get(currency, 0)
        payhip_complete = int(row["payhip_missing"] or 0) == 0
        stripe_complete = int(row["stripe_missing"] or 0) == 0
        payhip_fee = int(row["payhip_fee"] or 0) if payhip_complete else None
        stripe_fee = int(row["stripe_fee"] or 0) if stripe_complete else None
        paid, reconciled = payout_by_currency.get(currency, (0, 0))

        net_sales = gross - refunded
        contribution = (
            net_sales - payhip_fee - stripe_fee
            if payhip_fee is not None and stripe_fee is not None
            else None
        )
        result["by_currency"][currency] = {
            "orders": int(row["orders"] or 0),
            "verified_gross_minor": gross,
            "refunded_minor": refunded,
            "verified_net_sales_minor": net_sales,
            "payhip_fee_minor": payhip_fee,
            "stripe_fee_minor": stripe_fee,
            "payhip_fee_complete": payhip_complete,
            "stripe_fee_complete": stripe_complete,
            "contribution_minor": contribution,
            "payout_minor": paid,
            "reconciled_payout_minor": reconciled,
        }

    for row in unverified:
        entry = result["by_currency"].setdefault(row["currency"], {
            "orders": 0,
            "verified_gross_minor": 0,
            "refunded_minor": refunded_by_currency.get(row["currency"], 0),
            "verified_net_sales_minor": 0,
            "payhip_fee_minor": None,
            "stripe_fee_minor": None,
            "payhip_fee_complete": False,
            "stripe_fee_complete": False,
            "contribution_minor": None,
            "payout_minor": payout_by_currency.get(row["currency"], (0, 0))[0],
            "reconciled_payout_minor": payout_by_currency.get(row["currency"], (0, 0))[1],
        })
        entry["unverified_orders"] = int(row["stuck"] or 0)

    for entry in result["by_currency"].values():
        entry.setdefault("unverified_orders", 0)

    # A payout in a currency with no counted orders still has to be visible.
    for currency, (paid, reconciled) in payout_by_currency.items():
        if currency not in result["by_currency"] and not (start or end):
            result["by_currency"][currency] = {
                "orders": 0,
                "verified_gross_minor": 0,
                "refunded_minor": 0,
                "verified_net_sales_minor": 0,
                "payhip_fee_minor": None,
                "stripe_fee_minor": None,
                "payhip_fee_complete": False,
                "stripe_fee_complete": False,
                "contribution_minor": None,
                "payout_minor": paid,
                "reconciled_payout_minor": reconciled,
            }

    result["open_incidents"] = open_incidents(path)
    return result
