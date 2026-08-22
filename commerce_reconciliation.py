"""Turn provider events into money facts — one atomic transaction per event.

The rules, in order of importance:

1. Payhip observes, Stripe proves. A Payhip "paid" opens an order in
   `payment_pending`; only a Stripe-verified payment of the same id, amount and
   currency moves it to `paid_verified`.
2. Only a refund whose projection reached `succeeded` reverses revenue.
3. `balance.available` is an observation. It itemises nothing, so it can never
   create a balance transaction, a Stripe fee, or a payout item.
4. A payout is settlement, never revenue, and cannot be "reconciled" until an
   authorised itemised source exists.
5. Anything we cannot resolve yet waits as `pending_reconciliation`; anything
   contradictory opens an incident and changes no projection.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from business_ledger import init_ledger, record_growth_evidence
from commerce_ledger import commerce_event, mark_provider_event, open_incident

ORDER_OPEN_STATES = ("paid_verified", "partially_refunded", "refunded")
REFUND_TERMINAL = ("succeeded", "failed")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(path))
    connection.row_factory = sqlite3.Row
    return connection


# ── read models ─────────────────────────────────────────────────────────────

def commerce_order(path: Path, provider: str, provider_order_id: str) -> dict | None:
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT * FROM commerce_orders WHERE provider=? AND provider_order_id=?",
            (provider, provider_order_id),
        ).fetchone()
    return dict(row) if row else None


def commerce_refund(path: Path, provider_refund_id: str) -> dict | None:
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT * FROM commerce_refunds WHERE provider_refund_id=?",
            (provider_refund_id,),
        ).fetchone()
    return dict(row) if row else None


def commerce_payout(path: Path, provider_payout_id: str) -> dict | None:
    with _connect(path) as connection:
        row = connection.execute(
            "SELECT * FROM commerce_payouts WHERE provider_payout_id=?",
            (provider_payout_id,),
        ).fetchone()
    return dict(row) if row else None


def currency_totals(path: Path) -> dict:
    """Per-currency money facts. Currencies are never summed together."""
    path = Path(path)
    if not path.exists():
        return {}
    totals: dict = {}
    with _connect(path) as connection:
        # Every currency we have seen appears, even at zero — a currency that
        # silently vanishes from the report reads as "no orders" rather than
        # "orders that are not verified yet".
        for row in connection.execute(
            "SELECT DISTINCT currency FROM commerce_orders"
            " UNION SELECT DISTINCT currency FROM commerce_refunds"
            " UNION SELECT DISTINCT currency FROM commerce_payouts"
        ):
            totals.setdefault(row["currency"], {})

        for row in connection.execute(
            "SELECT currency, SUM(gross_minor) gross FROM commerce_orders"
            f" WHERE status IN {ORDER_OPEN_STATES} GROUP BY currency"
        ):
            totals.setdefault(row["currency"], {})["verified_gross_minor"] = int(row["gross"] or 0)

        for row in connection.execute(
            "SELECT currency, SUM(amount_minor) refunded FROM commerce_refunds"
            " WHERE status='succeeded' GROUP BY currency"
        ):
            totals.setdefault(row["currency"], {})["refunded_minor"] = int(row["refunded"] or 0)

        for row in connection.execute(
            "SELECT currency, SUM(amount_minor) paid, "
            " SUM(CASE WHEN status='reconciled' THEN amount_minor ELSE 0 END) reconciled"
            " FROM commerce_payouts GROUP BY currency"
        ):
            entry = totals.setdefault(row["currency"], {})
            entry["payout_minor"] = int(row["paid"] or 0)
            entry["reconciled_payout_minor"] = int(row["reconciled"] or 0)

        fees = connection.execute(
            "SELECT currency, SUM(stripe_fee_minor) fee FROM commerce_orders"
            " WHERE stripe_fee_minor IS NOT NULL GROUP BY currency"
        ).fetchall()
    fee_by_currency = {row["currency"]: int(row["fee"] or 0) for row in fees}

    for currency, entry in totals.items():
        gross = entry.setdefault("verified_gross_minor", 0)
        refunded = entry.setdefault("refunded_minor", 0)
        entry.setdefault("payout_minor", 0)
        entry.setdefault("reconciled_payout_minor", 0)
        entry["verified_net_sales_minor"] = gross - refunded
        # Absent fees stay unknown. Coercing them to zero would overstate profit.
        entry["stripe_fee_minor"] = fee_by_currency.get(currency)
        entry["payhip_fee_minor"] = None
    return totals


# ── projection helpers ──────────────────────────────────────────────────────

def _incident(connection, *, key, severity, scope, error_code, detail):
    connection.execute(
        "INSERT OR IGNORE INTO commerce_incidents"
        "(incident_key, opened_at, severity, scope, error_code, detail_json)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (key, _now(), severity, scope, error_code,
         json.dumps(detail, ensure_ascii=False, sort_keys=True)),
    )


def _order_by_payment(connection, payment_id: str):
    return connection.execute(
        "SELECT * FROM commerce_orders WHERE provider_payment_id=?", (payment_id,)
    ).fetchone()


def _succeeded_refund_total(connection, payment_id: str) -> int:
    row = connection.execute(
        "SELECT SUM(amount_minor) total FROM commerce_refunds"
        " WHERE provider_payment_id=? AND status='succeeded'",
        (payment_id,),
    ).fetchone()
    return int(row["total"] or 0)


def _refresh_order_refund_state(connection, payment_id: str) -> None:
    order = _order_by_payment(connection, payment_id)
    if order is None or order["status"] not in ORDER_OPEN_STATES:
        return
    refunded = _succeeded_refund_total(connection, payment_id)
    if refunded <= 0:
        status = "paid_verified"
    elif refunded >= order["gross_minor"]:
        status = "refunded"
    else:
        status = "partially_refunded"
    connection.execute(
        "UPDATE commerce_orders SET status=?, updated_at=? WHERE provider=? AND provider_order_id=?",
        (status, _now(), order["provider"], order["provider_order_id"]),
    )


class _Result:
    def __init__(self, state: str, error_code: str | None = None, evidence: dict | None = None):
        self.state = state
        self.error_code = error_code
        self.evidence = evidence

    def as_dict(self) -> dict:
        return {"status": self.state, "error_code": self.error_code}


# ── per-event rules ─────────────────────────────────────────────────────────

def _apply_payhip_paid(connection, event: dict, payload: dict) -> _Result:
    now = _now()
    connection.execute(
        "INSERT OR IGNORE INTO commerce_orders"
        "(provider, provider_order_id, slug, status, currency, gross_minor,"
        " provider_payment_id, customer_country, ordered_at, updated_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "payhip",
            payload["provider_order_id"],
            payload.get("provider_product_id"),
            "payment_pending",
            payload["currency"],
            int(payload["gross_minor"]),
            payload.get("provider_payment_id"),
            payload.get("customer_country"),
            event["occurred_at"],
            now,
        ),
    )
    return _Result("observed")


def _apply_stripe_payment(connection, event: dict, payload: dict) -> _Result:
    # Task 3 already proved signature, account and test mode. What is checked
    # here is that this money belongs to the order we think it does.
    if event["verification_state"] != "verified" or event["mode"] != "test":
        return _Result("pending_reconciliation", "unverified_payment_event")

    payment_id = payload["provider_payment_id"]
    order = _order_by_payment(connection, payment_id)
    if order is None:
        return _Result("pending_reconciliation", "no_matching_order")

    if int(payload["amount_minor"]) != int(order["gross_minor"]) or \
            payload["currency"] != order["currency"]:
        connection.execute(
            "UPDATE commerce_orders SET status='reconciliation_failed', updated_at=?"
            " WHERE provider=? AND provider_order_id=?",
            (_now(), order["provider"], order["provider_order_id"]),
        )
        _incident(
            connection,
            key=f"amount_mismatch:{payment_id}",
            severity="critical",
            scope=f"order:{order['provider_order_id']}",
            error_code="amount_mismatch",
            detail={
                "expected_minor": order["gross_minor"],
                "stripe_minor": payload["amount_minor"],
                "expected_currency": order["currency"],
                "stripe_currency": payload["currency"],
            },
        )
        return _Result("failed", "amount_mismatch")

    if order["status"] not in ORDER_OPEN_STATES:
        connection.execute(
            "UPDATE commerce_orders SET status='paid_verified', updated_at=?"
            " WHERE provider=? AND provider_order_id=?",
            (_now(), order["provider"], order["provider_order_id"]),
        )
    _refresh_order_refund_state(connection, payment_id)
    return _Result(
        "reconciled",
        None,
        {
            "source_key": f"commerce-sale:{payment_id}",
            "slug": order["slug"],
            "amount_minor": int(payload["amount_minor"]),
            "currency": payload["currency"],
        },
    )


def _apply_stripe_refund(connection, event: dict, payload: dict) -> _Result:
    if event["verification_state"] != "verified":
        return _Result("pending_reconciliation", "unverified_refund_event")

    refund_id = payload["provider_refund_id"]
    payment_id = payload.get("provider_payment_id")
    status = payload["status"]
    order = _order_by_payment(connection, payment_id) if payment_id else None
    if order is None:
        return _Result("pending_reconciliation", "no_matching_payment")

    existing = connection.execute(
        "SELECT * FROM commerce_refunds WHERE provider_refund_id=?", (refund_id,)
    ).fetchone()

    if existing and existing["status"] in REFUND_TERMINAL and status != existing["status"]:
        # A terminal refund cannot change its mind. Record the contradiction and
        # touch nothing.
        _incident(
            connection,
            key=f"refund_conflict:{refund_id}",
            severity="critical",
            scope=f"refund:{refund_id}",
            error_code="refund_terminal_conflict",
            detail={"stored": existing["status"], "incoming": status},
        )
        return _Result("failed", "refund_terminal_conflict")

    if status == "succeeded":
        already = _succeeded_refund_total(connection, payment_id)
        if existing and existing["status"] == "succeeded":
            already -= int(existing["amount_minor"])
        if already + int(payload["amount_minor"]) > int(order["gross_minor"]):
            _incident(
                connection,
                key=f"refund_exceeds:{refund_id}",
                severity="critical",
                scope=f"order:{order['provider_order_id']}",
                error_code="refund_exceeds_gross",
                detail={
                    "gross_minor": order["gross_minor"],
                    "already_refunded_minor": already,
                    "incoming_minor": payload["amount_minor"],
                },
            )
            return _Result("failed", "refund_exceeds_gross")

    connection.execute(
        "INSERT INTO commerce_refunds"
        "(provider, provider_refund_id, provider_order_id, provider_payment_id,"
        " amount_minor, currency, status, reason_code, occurred_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)"
        " ON CONFLICT(provider, provider_refund_id) DO UPDATE SET"
        " status=excluded.status, amount_minor=excluded.amount_minor,"
        " occurred_at=excluded.occurred_at",
        (
            "stripe", refund_id, order["provider_order_id"], payment_id,
            int(payload["amount_minor"]), payload["currency"], status,
            payload.get("reason_code"), event["occurred_at"],
        ),
    )
    _refresh_order_refund_state(connection, payment_id)
    return _Result("reconciled")


def _apply_stripe_payout(connection, event: dict, payload: dict) -> _Result:
    connection.execute(
        "INSERT INTO commerce_payouts"
        "(provider_payout_id, status, currency, amount_minor, arrival_date, error_code, created_at)"
        " VALUES (?,?,?,?,?,?,?)"
        " ON CONFLICT(provider_payout_id) DO UPDATE SET"
        " status=excluded.status, amount_minor=excluded.amount_minor",
        (
            payload["provider_payout_id"],
            # Settlement observed. It cannot advance past this state until an
            # authorised balance-transaction source can itemise it.
            "pending_reconciliation",
            payload["currency"],
            int(payload["amount_minor"]),
            str(payload.get("arrival_date") or ""),
            "balance_transaction_source_not_authorized",
            _now(),
        ),
    )
    return _Result("reconciled")


def _apply_balance_available(connection, event: dict, payload: dict) -> _Result:
    # Deliberately writes nothing: an availability figure is not an itemisation.
    return _Result("observed")


def _apply_dispute(connection, event: dict, payload: dict) -> _Result:
    payment_id = payload.get("provider_payment_id")
    order = _order_by_payment(connection, payment_id) if payment_id else None
    if order is not None:
        connection.execute(
            "UPDATE commerce_orders SET status='disputed', updated_at=?"
            " WHERE provider=? AND provider_order_id=?",
            (_now(), order["provider"], order["provider_order_id"]),
        )
    _incident(
        connection,
        key=f"dispute:{payload.get('provider_dispute_id')}",
        severity="critical",
        scope=f"payment:{payment_id}",
        error_code="dispute_opened",
        detail={"amount_minor": payload.get("amount_minor"), "status": payload.get("status")},
    )
    return _Result("manual_required", "dispute_opened")


_HANDLERS = {
    ("payhip", "paid"): _apply_payhip_paid,
    ("stripe", "payment_intent.succeeded"): _apply_stripe_payment,
    ("stripe", "refund.created"): _apply_stripe_refund,
    ("stripe", "refund.updated"): _apply_stripe_refund,
    ("stripe", "refund.failed"): _apply_stripe_refund,
    ("stripe", "payout.paid"): _apply_stripe_payout,
    ("stripe", "payout.failed"): _apply_stripe_payout,
    ("stripe", "balance.available"): _apply_balance_available,
    ("stripe", "charge.dispute.created"): _apply_dispute,
}


def _apply_event(connection, event: dict) -> _Result:
    handler = _HANDLERS.get((event["provider"], event["event_type"]))
    if handler is None:
        return _Result("quarantined", "unsupported_event_type")
    return handler(connection, event, event["sanitized_payload"])


def reconcile_event(path: Path, provider: str, event_id: str) -> dict:
    path = Path(path)
    init_ledger(path)
    event = commerce_event(path, provider, event_id)
    if event is None:
        return {"status": "unknown_event"}
    if event["processing_state"] not in ("received", "pending_reconciliation"):
        return {"status": "already_reconciled"}

    connection = _connect(path)
    try:
        connection.execute("BEGIN IMMEDIATE")
        result = _apply_event(connection, event)
        connection.execute(
            "UPDATE commerce_events SET processing_state=?, error_code=?"
            " WHERE provider=? AND event_id=?",
            (result.state, result.error_code, provider, event_id),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    # Growth evidence is emitted only after the order is durably verified, and
    # keyed on the payment so retries cannot double count it.
    if result.evidence:
        record_growth_evidence(path, {
            "source_key": result.evidence["source_key"],
            "kind": "commerce_sale",
            "slug": result.evidence.get("slug"),
            "observed_at": event["occurred_at"],
            "fresh_until": event["occurred_at"],
            "confidence": 1.0,
            "payload": {
                "amount_minor": result.evidence["amount_minor"],
                "currency": result.evidence["currency"],
            },
        })
    return result.as_dict()


def retry_pending(path: Path, *, limit: int = 100) -> dict:
    """Revisit events that arrived before their counterpart."""
    path = Path(path)
    if not path.exists():
        return {"revisited": 0, "reconciled": 0}
    with _connect(path) as connection:
        # 'received' is included deliberately: an event whose projection never
        # ran (a crash after the inbox write) is exactly what this sweep is for.
        rows = connection.execute(
            "SELECT provider, event_id FROM commerce_events"
            " WHERE processing_state IN ('pending_reconciliation', 'received')"
            " ORDER BY id LIMIT ?",
            (limit,),
        ).fetchall()

    reconciled = 0
    for row in rows:
        outcome = reconcile_event(path, row["provider"], row["event_id"])
        if outcome.get("status") == "reconciled":
            reconciled += 1
    return {"revisited": len(rows), "reconciled": reconciled}
