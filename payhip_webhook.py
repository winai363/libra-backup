"""Payhip callback ingestion — operational observation only.

Possession of the secret callback URL proves the caller reached our endpoint;
it proves nothing about money. Every event normalized here is financially
`unverified`; only a Stripe-verified event can establish revenue.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from decimal import Decimal, InvalidOperation

SUPPORTED_EVENT_TYPES = frozenset(
    {"paid", "refunded", "subscription.created", "subscription.deleted"}
)
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class PayhipWebhookError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def verify_payhip_callback_token(received: str, expected: str) -> None:
    if not received or not expected or not secrets.compare_digest(received, expected):
        raise PayhipWebhookError("callback_token_invalid")


def _strict_json_object(raw_body: bytes) -> dict:
    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise PayhipWebhookError("malformed_event") from exc
    if not isinstance(payload, dict):
        raise PayhipWebhookError("malformed_event")
    return payload


def _minor_units(value) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError):
        raise PayhipWebhookError("malformed_amount")
    minor = amount * 100
    if minor != minor.to_integral_value():
        raise PayhipWebhookError("malformed_amount")
    minor = int(minor)
    if minor < 0:
        raise PayhipWebhookError("malformed_amount")
    return minor


def _currency(payload: dict) -> str:
    currency = str(payload.get("currency") or "").upper()
    if not CURRENCY_RE.match(currency):
        raise PayhipWebhookError("malformed_currency")
    return currency


def _safe_projection(payload: dict, event_type: str) -> dict:
    """Only stable identifiers and money. Buyer identity is dropped, not hashed."""
    projection = {
        "provider_product_id": str(payload["product_id"]),
        "gross_minor": _minor_units(payload.get("price")),
        "currency": _currency(payload),
        "status": event_type,
        "customer_country": str(payload.get("country") or "") or None,
        "provider_payment_id": str(payload.get("payment_id") or "") or None,
        "payment_processor": str(payload.get("payment_processor") or "") or None,
    }
    if event_type == "refunded":
        projection["provider_order_id"] = str(payload.get("sale_id") or "") or None
        projection["provider_refund_id"] = str(payload["id"])
    else:
        projection["provider_order_id"] = str(payload["id"])
    return projection


def normalize_payhip_event(raw_body: bytes, settings, *, received_at: str) -> dict:
    if len(raw_body) > settings.max_webhook_bytes:
        raise PayhipWebhookError("body_too_large")

    payload = _strict_json_object(raw_body)

    event_type = str(payload.get("type") or "")
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise PayhipWebhookError("unsupported_event")

    event_ref = str(payload.get("id") or "")
    if not event_ref:
        raise PayhipWebhookError("missing_event_id")

    product_id = str(payload.get("product_id") or "")
    if not product_id:
        raise PayhipWebhookError("unknown_product")
    if product_id not in settings.payhip_product_ids:
        raise PayhipWebhookError("unknown_product")

    occurred_at = str(payload.get("date") or "")
    if not occurred_at:
        raise PayhipWebhookError("missing_timestamp")

    return {
        "provider": "payhip",
        "event_id": f"payhip:{event_type}:{event_ref}",
        "event_type": event_type,
        "occurred_at": occurred_at,
        "received_at": received_at,
        "mode": "test",
        # Payhip can never establish money on its own — Stripe is the source of truth.
        "verification_state": "unverified",
        "payload_hash": hashlib.sha256(raw_body).hexdigest(),
        "sanitized_payload": _safe_projection(payload, event_type),
    }
