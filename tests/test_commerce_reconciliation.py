"""The truth model: Payhip observes, Stripe proves.

Every rule here exists to stop a number appearing in our revenue that no bank
ever moved — and to stop a refund, fee or payout being invented from a
coincidence of amounts.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commerce_ledger import commerce_event, open_incidents, record_provider_event
from commerce_reconciliation import (
    commerce_order,
    commerce_payout,
    commerce_refund,
    currency_totals,
    reconcile_event,
    retry_pending,
)

RECEIVED = "2026-08-22T10:00:00+00:00"


def _ingest(db, event):
    record_provider_event(db, event)
    return reconcile_event(db, event["provider"], event["event_id"])


def payhip_paid(*, sale_id="sale_test_1", payment_id="pi_test_1", gross_minor=1290,
                currency="EUR", product="kit-fr-test"):
    return {
        "provider": "payhip",
        "event_id": f"payhip:paid:{sale_id}",
        "event_type": "paid",
        "occurred_at": "2026-08-22T09:59:00+00:00",
        "received_at": RECEIVED,
        "mode": "test",
        "verification_state": "unverified",
        "payload_hash": f"hash-payhip-{sale_id}",
        "sanitized_payload": {
            "provider_order_id": sale_id,
            "provider_product_id": product,
            "gross_minor": gross_minor,
            "currency": currency,
            "status": "paid",
            "provider_payment_id": payment_id,
            "customer_country": "FR",
        },
    }


def stripe_payment(*, event_id="evt_pi_1", payment_id="pi_test_1", amount_minor=1290,
                   currency="EUR", order_id="sale_test_1"):
    return {
        "provider": "stripe",
        "event_id": event_id,
        "event_type": "payment_intent.succeeded",
        "occurred_at": "2026-08-22T10:00:00+00:00",
        "received_at": RECEIVED,
        "mode": "test",
        "verification_state": "verified",
        "payload_hash": f"hash-{event_id}",
        "sanitized_payload": {
            "kind": "payment",
            "provider_payment_id": payment_id,
            "amount_minor": amount_minor,
            "currency": currency,
            "status": "succeeded",
            "charge_id": "ch_test_1",
            "provider_order_id": order_id,
        },
    }


def stripe_refund(refund_id, payment_id, amount_minor, *, status="pending", event_id=None,
                  currency="EUR"):
    return {
        "provider": "stripe",
        "event_id": event_id or f"evt_{refund_id}_{status}",
        "event_type": f"refund.{'failed' if status == 'failed' else 'updated'}",
        "occurred_at": "2026-08-22T11:00:00+00:00",
        "received_at": RECEIVED,
        "mode": "test",
        "verification_state": "verified",
        "payload_hash": f"hash-{refund_id}-{status}",
        "sanitized_payload": {
            "kind": "refund",
            "provider_refund_id": refund_id,
            "provider_payment_id": payment_id,
            "amount_minor": amount_minor,
            "currency": currency,
            "status": status,
            "reason_code": "requested_by_customer",
        },
    }


def stripe_payout(payout_id, amount_minor, *, currency="EUR"):
    return {
        "provider": "stripe",
        "event_id": f"evt_{payout_id}",
        "event_type": "payout.paid",
        "occurred_at": "2026-08-23T10:00:00+00:00",
        "received_at": RECEIVED,
        "mode": "test",
        "verification_state": "verified",
        "payload_hash": f"hash-{payout_id}",
        "sanitized_payload": {
            "kind": "payout",
            "provider_payout_id": payout_id,
            "amount_minor": amount_minor,
            "currency": currency,
            "status": "paid",
            "arrival_date": 1787100000,
        },
    }


def stripe_balance_available(*, amount_minor=1240, currency="EUR"):
    return {
        "provider": "stripe",
        "event_id": "evt_balance_1",
        "event_type": "balance.available",
        "occurred_at": "2026-08-23T09:00:00+00:00",
        "received_at": RECEIVED,
        "mode": "test",
        "verification_state": "verified",
        "payload_hash": "hash-balance-1",
        "sanitized_payload": {
            "kind": "balance_available",
            "available": [{"amount_minor": amount_minor, "currency": currency}],
        },
    }


def stripe_dispute(*, payment_id="pi_test_1", amount_minor=1290):
    return {
        "provider": "stripe",
        "event_id": "evt_dispute_1",
        "event_type": "charge.dispute.created",
        "occurred_at": "2026-08-24T10:00:00+00:00",
        "received_at": RECEIVED,
        "mode": "test",
        "verification_state": "verified",
        "payload_hash": "hash-dispute-1",
        "sanitized_payload": {
            "kind": "dispute",
            "provider_dispute_id": "dp_test_1",
            "provider_payment_id": payment_id,
            "charge_id": "ch_test_1",
            "amount_minor": amount_minor,
            "currency": "EUR",
            "status": "needs_response",
        },
    }


def _seed_verified_order(db, *, gross_minor=1290, payment_id="pi_test_1", currency="EUR"):
    _ingest(db, payhip_paid(payment_id=payment_id, gross_minor=gross_minor, currency=currency))
    _ingest(db, stripe_payment(payment_id=payment_id, amount_minor=gross_minor, currency=currency))


# ── the core truth rule ──────────────────────────────────────────────────────

def test_payhip_paid_cannot_create_verified_revenue_until_stripe_match(tmp_path):
    db = tmp_path / "ledger.db"

    _ingest(db, payhip_paid(payment_id="pi_test_1", gross_minor=1290))

    assert currency_totals(db)["EUR"]["verified_gross_minor"] == 0
    assert commerce_order(db, "payhip", "sale_test_1")["status"] == "payment_pending"

    _ingest(db, stripe_payment(payment_id="pi_test_1", amount_minor=1290))

    assert commerce_order(db, "payhip", "sale_test_1")["status"] == "paid_verified"
    assert currency_totals(db)["EUR"]["verified_gross_minor"] == 1290


def test_stripe_payment_that_disagrees_on_amount_opens_an_incident(tmp_path):
    db = tmp_path / "ledger.db"
    _ingest(db, payhip_paid(gross_minor=1290))

    _ingest(db, stripe_payment(amount_minor=990))

    assert commerce_order(db, "payhip", "sale_test_1")["status"] == "reconciliation_failed"
    assert currency_totals(db)["EUR"]["verified_gross_minor"] == 0
    assert any(i["error_code"] == "amount_mismatch" for i in open_incidents(db))


def test_stripe_payment_arriving_first_still_reconciles_when_payhip_follows(tmp_path):
    db = tmp_path / "ledger.db"

    result = _ingest(db, stripe_payment())
    assert result["status"] == "pending_reconciliation"
    # No order exists yet, so there is nothing to report — not even a zero row.
    assert currency_totals(db) == {}

    _ingest(db, payhip_paid())
    retry_pending(db)

    assert commerce_order(db, "payhip", "sale_test_1")["status"] == "paid_verified"
    assert currency_totals(db)["EUR"]["verified_gross_minor"] == 1290


def test_an_unverified_stripe_event_can_never_establish_revenue(tmp_path):
    db = tmp_path / "ledger.db"
    _ingest(db, payhip_paid())
    forged = {**stripe_payment(), "verification_state": "unverified"}

    _ingest(db, forged)

    assert commerce_order(db, "payhip", "sale_test_1")["status"] == "payment_pending"
    assert currency_totals(db)["EUR"]["verified_gross_minor"] == 0


# ── refunds ─────────────────────────────────────────────────────────────────

def test_refund_same_id_reverses_revenue_only_after_succeeded(tmp_path):
    db = tmp_path / "ledger.db"
    _seed_verified_order(db, gross_minor=1290)

    _ingest(db, stripe_refund("re_test_1", "pi_test_1", 400, status="pending"))
    assert currency_totals(db)["EUR"]["refunded_minor"] == 0

    _ingest(db, stripe_refund("re_test_1", "pi_test_1", 400, status="succeeded"))
    _ingest(db, stripe_payout("po_test_1", 790))

    totals = currency_totals(db)["EUR"]
    assert totals["verified_gross_minor"] == 1290
    assert totals["refunded_minor"] == 400
    assert totals["verified_net_sales_minor"] == 890
    assert totals["payout_minor"] == 790
    assert commerce_order(db, "payhip", "sale_test_1")["status"] == "partially_refunded"


def test_failed_refund_same_id_does_not_reverse_revenue(tmp_path):
    db = tmp_path / "ledger.db"
    _seed_verified_order(db, gross_minor=1290)

    _ingest(db, stripe_refund("re_test_1", "pi_test_1", 400, status="pending"))
    _ingest(db, stripe_refund("re_test_1", "pi_test_1", 400, status="failed"))

    totals = currency_totals(db)["EUR"]
    assert totals["refunded_minor"] == 0
    assert totals["verified_net_sales_minor"] == 1290
    assert commerce_refund(db, "re_test_1")["status"] == "failed"


def test_a_full_refund_marks_the_order_refunded(tmp_path):
    db = tmp_path / "ledger.db"
    _seed_verified_order(db, gross_minor=1290)

    _ingest(db, stripe_refund("re_test_1", "pi_test_1", 1290, status="succeeded"))

    assert commerce_order(db, "payhip", "sale_test_1")["status"] == "refunded"
    assert currency_totals(db)["EUR"]["verified_net_sales_minor"] == 0


def test_a_contradictory_terminal_replay_changes_nothing_and_opens_an_incident(tmp_path):
    db = tmp_path / "ledger.db"
    _seed_verified_order(db, gross_minor=1290)
    _ingest(db, stripe_refund("re_test_1", "pi_test_1", 400, status="succeeded"))

    _ingest(db, stripe_refund("re_test_1", "pi_test_1", 400, status="failed",
                              event_id="evt_re_late_flip"))

    assert commerce_refund(db, "re_test_1")["status"] == "succeeded"
    assert currency_totals(db)["EUR"]["refunded_minor"] == 400
    assert any(i["error_code"] == "refund_terminal_conflict" for i in open_incidents(db))


def test_refunds_can_never_exceed_the_verified_gross(tmp_path):
    db = tmp_path / "ledger.db"
    _seed_verified_order(db, gross_minor=1290)

    _ingest(db, stripe_refund("re_big", "pi_test_1", 5000, status="succeeded"))

    assert currency_totals(db)["EUR"]["refunded_minor"] <= 1290
    assert any(i["error_code"] == "refund_exceeds_gross" for i in open_incidents(db))


def test_a_refund_before_any_payment_waits_instead_of_failing(tmp_path):
    db = tmp_path / "ledger.db"

    result = _ingest(db, stripe_refund("re_orphan", "pi_unknown", 400, status="succeeded"))

    assert result["status"] == "pending_reconciliation"
    assert currency_totals(db) == {}


# ── fees, payouts, disputes ─────────────────────────────────────────────────

def test_balance_available_and_payout_cannot_false_reconcile_without_source(tmp_path):
    db = tmp_path / "ledger.db"
    _seed_verified_order(db, gross_minor=1290)

    _ingest(db, stripe_balance_available(amount_minor=1240))
    _ingest(db, stripe_payout("po_test_1", 1240))

    payout = commerce_payout(db, "po_test_1")
    assert payout["status"] == "pending_reconciliation"
    assert payout["error_code"] == "balance_transaction_source_not_authorized"
    totals = currency_totals(db)["EUR"]
    assert totals["stripe_fee_minor"] is None
    assert totals["reconciled_payout_minor"] == 0


def test_a_payout_is_settlement_and_never_revenue(tmp_path):
    db = tmp_path / "ledger.db"
    _ingest(db, stripe_payout("po_alone", 5000))

    totals = currency_totals(db)["EUR"]
    assert totals["verified_gross_minor"] == 0
    assert totals["payout_minor"] == 5000


def test_a_dispute_freezes_the_order_and_requires_a_human(tmp_path):
    db = tmp_path / "ledger.db"
    _seed_verified_order(db)

    result = _ingest(db, stripe_dispute())

    assert result["status"] == "manual_required"
    assert commerce_order(db, "payhip", "sale_test_1")["status"] == "disputed"
    assert any(i["error_code"] == "dispute_opened" for i in open_incidents(db))


# ── bookkeeping hygiene ─────────────────────────────────────────────────────

def test_currencies_are_never_summed_together(tmp_path):
    db = tmp_path / "ledger.db"
    _seed_verified_order(db, gross_minor=1290, payment_id="pi_eur", currency="EUR")
    _ingest(db, payhip_paid(sale_id="sale_usd", payment_id="pi_usd", gross_minor=900, currency="USD"))
    _ingest(db, stripe_payment(event_id="evt_usd", payment_id="pi_usd", amount_minor=900,
                               currency="USD", order_id="sale_usd"))

    totals = currency_totals(db)

    assert totals["EUR"]["verified_gross_minor"] == 1290
    assert totals["USD"]["verified_gross_minor"] == 900
    assert "converted_total" not in totals


def test_reconciling_the_same_event_twice_is_a_no_op(tmp_path):
    db = tmp_path / "ledger.db"
    _seed_verified_order(db, gross_minor=1290)

    again = reconcile_event(db, "stripe", "evt_pi_1")

    assert again["status"] == "already_reconciled"
    assert currency_totals(db)["EUR"]["verified_gross_minor"] == 1290


def test_a_crash_after_the_inbox_write_leaves_the_event_retryable(tmp_path, monkeypatch):
    import commerce_reconciliation

    db = tmp_path / "ledger.db"
    _ingest(db, payhip_paid())
    event = stripe_payment()
    record_provider_event(db, event)

    def boom(*args, **kwargs):
        raise RuntimeError("projection exploded")

    monkeypatch.setattr(commerce_reconciliation, "_apply_event", boom)
    with pytest.raises(RuntimeError):
        reconcile_event(db, "stripe", "evt_pi_1")

    monkeypatch.undo()
    assert commerce_event(db, "stripe", "evt_pi_1")["processing_state"] == "received"
    reconcile_event(db, "stripe", "evt_pi_1")
    assert currency_totals(db)["EUR"]["verified_gross_minor"] == 1290


def test_growth_evidence_is_emitted_once_per_verified_sale(tmp_path):
    from business_ledger import growth_evidence

    db = tmp_path / "ledger.db"
    _seed_verified_order(db)
    reconcile_event(db, "stripe", "evt_pi_1")
    retry_pending(db)

    sales = [e for e in growth_evidence(db) if e["kind"] == "commerce_sale"]
    assert len(sales) == 1
    assert sales[0]["source_key"] == "commerce-sale:pi_test_1"


def test_commerce_modules_do_not_import_kdp_mutators():
    import ast

    forbidden = {
        "kdp_upload", "kdp_finish_publish", "kdp_fix_publish", "kdp_live_replace",
        "reupload_metadata", "set_price", "free_promo_auto", "kdp_action_executor",
    }
    root = Path(__file__).resolve().parent.parent
    for name in ("commerce_reconciliation.py", "commerce_ledger.py",
                 "stripe_webhook.py", "payhip_webhook.py"):
        tree = ast.parse((root / name).read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[-1] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[-1])
        assert imported.isdisjoint(forbidden), (name, imported & forbidden)
