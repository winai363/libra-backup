"""Lemon Squeezy webhook verification and normalization.

Lemon Squeezy is a **merchant of record**: it is the legal seller, it collects
the money, and it remits VAT. That changes the truth model compared to the
Payhip/Stripe lane — there is no second processor to cross-check against, so a
correctly signed order event *is* the proof that money moved.

Which puts all the weight on the signature. It is an HMAC-SHA256 of the exact
raw body, so the route must verify before it parses, and the store id on the
event must be ours.
"""

from __future__ import annotations

import hashlib
import hmac
import json

SUPPORTED_EVENTS = frozenset({
    "order_created",
    "order_refunded",
    "subscription_created",
    "subscription_cancelled",
    "subscription_expired",
})

# Events that move money. Everything else is an observation.
MONEY_EVENTS = frozenset({"order_created", "order_refunded"})


class LemonSqueezyWebhookError(RuntimeError):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


def _secret(settings) -> str:
    secret = getattr(settings, "lemonsqueezy_webhook_secret", "")
    if not secret:
        raise LemonSqueezyWebhookError("not_configured")
    return secret


def verify_lemonsqueezy_signature(raw_body: bytes, signature: str, settings) -> None:
    if len(raw_body) > settings.max_webhook_bytes:
        raise LemonSqueezyWebhookError("body_too_large")
    if not signature:
        raise LemonSqueezyWebhookError("signature_missing")

    expected = hmac.new(_secret(settings).encode(), raw_body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(signature).strip()):
        raise LemonSqueezyWebhookError("signature_invalid")


def _int(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise LemonSqueezyWebhookError("malformed_amount")
    return value


def _projection(event_name: str, attributes: dict, meta: dict) -> dict:
    """Only ids, money and status. Buyer identity is dropped, never hashed."""
    item = attributes.get("first_order_item") or {}
    custom = meta.get("custom_data") or {}
    return {
        "kind": "order",
        "provider_order_id": str(attributes["identifier"]),
        "order_number": attributes.get("order_number"),
        "gross_minor": _int(attributes.get("total")),
        "subtotal_minor": _int(attributes.get("subtotal")),
        "tax_minor": _int(attributes.get("tax")),
        "discount_minor": _int(attributes.get("discount_total", 0)),
        "currency": str(attributes.get("currency") or "").upper(),
        "status": str(attributes.get("status") or ""),
        "refunded": bool(attributes.get("refunded")),
        "refunded_at": attributes.get("refunded_at"),
        "provider_product_id": str(item.get("product_id") or "") or None,
        "product_name": item.get("product_name"),
        # our own slug, passed through checkout custom data
        "slug": str(custom.get("slug") or "") or None,
    }


def normalize_lemonsqueezy_event(raw_body: bytes, settings, *, received_at: str) -> dict:
    try:
        payload = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise LemonSqueezyWebhookError("malformed_event") from exc
    if not isinstance(payload, dict):
        raise LemonSqueezyWebhookError("malformed_event")

    meta = payload.get("meta") or {}
    event_name = str(meta.get("event_name") or "")
    if event_name not in SUPPORTED_EVENTS:
        raise LemonSqueezyWebhookError("unsupported_event")

    try:
        attributes = dict(payload["data"]["attributes"])
    except (KeyError, TypeError) as exc:
        raise LemonSqueezyWebhookError("malformed_event") from exc

    expected_store = str(getattr(settings, "lemonsqueezy_store_id", "") or "")
    if expected_store and str(attributes.get("store_id") or "") != expected_store:
        raise LemonSqueezyWebhookError("wrong_store")

    identifier = str(attributes.get("identifier") or "")
    if not identifier:
        raise LemonSqueezyWebhookError("missing_event_id")

    occurred_at = str(attributes.get("updated_at") or attributes.get("created_at") or "")
    if not occurred_at:
        raise LemonSqueezyWebhookError("missing_timestamp")

    return {
        "provider": "lemonsqueezy",
        # The event name is part of the id: one order produces a created event
        # and later a refunded event, and both must be stored, not deduplicated.
        "event_id": f"lemonsqueezy:{event_name}:{identifier}",
        "event_type": event_name,
        "occurred_at": occurred_at,
        "received_at": received_at,
        # The event states its own mode; configuration does not override it.
        "mode": "test" if meta.get("test_mode") else "live",
        # A merchant of record's signed order is itself the settlement proof.
        "verification_state": "verified" if event_name in MONEY_EVENTS else "unverified",
        "payload_hash": hashlib.sha256(raw_body).hexdigest(),
        "sanitized_payload": _projection(event_name, attributes, meta),
    }
