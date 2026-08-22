"""Stripe webhook verification and normalization — the financial source of truth.

Nothing else in this system may create verified revenue. Verification happens
against the exact bytes Stripe signed, so callers must hand over the raw body
before parsing it.

The Stripe object is never persisted verbatim: only provider IDs, integer
amounts, currency, timestamps and linkage IDs are copied out.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone

import stripe

CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
TOLERANCE_SECONDS = 300

STRIPE_EVENT_TYPES = frozenset({
    "payment_intent.succeeded",
    "refund.created",
    "refund.updated",
    "refund.failed",
    "charge.dispute.created",
    "balance.available",
    "payout.paid",
    "payout.failed",
})


class StripeWebhookError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _signature_timestamp(signature: str) -> int | None:
    for part in signature.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            try:
                return int(value)
            except ValueError:
                return None
    return None


def verify_stripe_event(raw_body: bytes, signature: str, settings, *, now: int) -> dict:
    if len(raw_body) > settings.max_webhook_bytes:
        raise StripeWebhookError("body_too_large")
    if not signature:
        raise StripeWebhookError("signature_missing")

    # Verify with the SDK against the exact bytes, then read the event from our
    # own json.loads. The SDK's StripeObject is not a plain mapping, and every
    # decision below is easier to audit on ordinary dicts.
    try:
        # verify_header formats the payload with %s: handing it bytes would
        # sign the repr (b'{...}'), never the body Stripe signed. Decode first.
        decoded = raw_body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise StripeWebhookError("malformed_event") from exc

    # Signature first, freshness second — a body with a bad signature is
    # forged, not merely late, and must be reported as such.
    try:
        stripe.WebhookSignature.verify_header(
            decoded, signature, settings.stripe_webhook_secret
        )
    except stripe.SignatureVerificationError as exc:
        raise StripeWebhookError("signature_invalid") from exc

    sent_at = _signature_timestamp(signature)
    if sent_at is None or abs(now - sent_at) > TOLERANCE_SECONDS:
        raise StripeWebhookError("signature_stale")

    try:
        event = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise StripeWebhookError("malformed_event") from exc
    if not isinstance(event, dict):
        raise StripeWebhookError("malformed_event")

    # The event's mode must equal the mode we are configured for. Accepting a
    # test event while live would let anyone with the test secret manufacture
    # revenue; accepting a live event while test would book real money into a
    # ledger that believes it is a rehearsal.
    if event.get("livemode") is not settings.expect_livemode:
        raise StripeWebhookError("wrong_mode")
    if event.get("account") != settings.stripe_expected_account:
        raise StripeWebhookError("wrong_account")
    if event.get("type") not in STRIPE_EVENT_TYPES:
        raise StripeWebhookError("unsupported_event")
    return event


def _int(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise StripeWebhookError("malformed_event")
    return value


def _currency(value) -> str:
    currency = str(value or "").upper()
    if not CURRENCY_RE.match(currency):
        raise StripeWebhookError("malformed_event")
    return currency


def _payment_projection(obj: dict) -> dict:
    metadata = obj.get("metadata") or {}
    return {
        "kind": "payment",
        "provider_payment_id": str(obj["id"]),
        "amount_minor": _int(obj.get("amount_received", obj.get("amount"))),
        "currency": _currency(obj.get("currency")),
        "status": str(obj.get("status") or ""),
        "charge_id": str(obj.get("latest_charge") or "") or None,
        # Payhip puts its sale id in metadata; that is the only link we get.
        "provider_order_id": str(metadata.get("payhip_sale_id") or "") or None,
    }


def _refund_projection(obj: dict) -> dict:
    return {
        "kind": "refund",
        "provider_refund_id": str(obj["id"]),
        "provider_payment_id": str(obj.get("payment_intent") or "") or None,
        "amount_minor": _int(obj.get("amount")),
        "currency": _currency(obj.get("currency")),
        "status": str(obj.get("status") or ""),
        "reason_code": str(obj.get("reason") or "") or None,
    }


def _dispute_projection(obj: dict) -> dict:
    return {
        "kind": "dispute",
        "provider_dispute_id": str(obj["id"]),
        "provider_payment_id": str(obj.get("payment_intent") or "") or None,
        "charge_id": str(obj.get("charge") or "") or None,
        "amount_minor": _int(obj.get("amount")),
        "currency": _currency(obj.get("currency")),
        "status": str(obj.get("status") or ""),
    }


def _payout_projection(obj: dict) -> dict:
    return {
        "kind": "payout",
        "provider_payout_id": str(obj["id"]),
        "amount_minor": _int(obj.get("amount")),
        "currency": _currency(obj.get("currency")),
        "status": str(obj.get("status") or ""),
        "arrival_date": obj.get("arrival_date"),
    }


def _balance_projection(obj: dict) -> dict:
    # An availability observation only: it itemises nothing, so it must never
    # be turned into fees, balance transactions, or payout items.
    return {
        "kind": "balance_available",
        "available": [
            {"amount_minor": _int(entry.get("amount")), "currency": _currency(entry.get("currency"))}
            for entry in (obj.get("available") or [])
        ],
    }


_PROJECTIONS = {
    "payment_intent.succeeded": _payment_projection,
    "refund.created": _refund_projection,
    "refund.updated": _refund_projection,
    "refund.failed": _refund_projection,
    "charge.dispute.created": _dispute_projection,
    "payout.paid": _payout_projection,
    "payout.failed": _payout_projection,
    "balance.available": _balance_projection,
}


def normalize_stripe_event(event, raw_body: bytes, *, received_at: str) -> dict:
    """The stored `mode` comes from the event itself, never from configuration."""
    event = dict(event)
    event_type = str(event.get("type") or "")
    projection = _PROJECTIONS.get(event_type)
    if projection is None:
        raise StripeWebhookError("unsupported_event")

    try:
        obj = dict(event["data"]["object"])
    except (KeyError, TypeError) as exc:
        raise StripeWebhookError("malformed_event") from exc

    created = event.get("created")
    if not isinstance(created, int) or isinstance(created, bool):
        raise StripeWebhookError("malformed_event")
    # Store timestamps the same way Payhip events do, so the ledger compares
    # like with like instead of epoch ints against ISO strings.
    occurred_at = datetime.fromtimestamp(created, tz=timezone.utc).isoformat()

    return {
        "provider": "stripe",
        "event_id": str(event["id"]),
        "event_type": event_type,
        "occurred_at": occurred_at,
        "received_at": received_at,
        "mode": "live" if event.get("livemode") else "test",
        "verification_state": "verified",
        "payload_hash": hashlib.sha256(raw_body).hexdigest(),
        "sanitized_payload": projection(obj),
    }
