"""Lemon Squeezy is a merchant of record: it IS the seller, so its signed
order event is the money fact — there is no second processor to cross-check.

That makes signature verification the whole defence, and it is checked against
the exact raw bytes.
"""

import hashlib
import hmac
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lemonsqueezy_webhook import (
    LemonSqueezyWebhookError,
    normalize_lemonsqueezy_event,
    verify_lemonsqueezy_signature,
)
from settings import CommerceSettings

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "commerce"
SECRET = "ls_signing_secret_fixture"
RECEIVED = "2026-08-22T15:00:01+00:00"


@pytest.fixture
def settings():
    return CommerceSettings.from_sources({
        "LIBRA_COMMERCE_MODE": "test",
        "STRIPE_WEBHOOK_SECRET_TEST": "whsec_test_fixture",
        "STRIPE_EXPECTED_ACCOUNT_TEST": "acct_test_fixture",
        "PAYHIP_WEBHOOK_TOKEN_TEST": "p" * 48,
        "PAYHIP_ALLOWED_HOSTS": "payhip.com",
        "PAYHIP_PRODUCT_IDS_TEST": "kit-fr-test",
        "LEMONSQUEEZY_WEBHOOK_SECRET": SECRET,
        "LEMONSQUEEZY_STORE_ID": "457485",
    })


def _raw(name="lemonsqueezy_order_created.json") -> bytes:
    return (FIXTURES / name).read_bytes()


def _sign(raw: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def test_a_correctly_signed_body_is_accepted(settings):
    raw = _raw()
    verify_lemonsqueezy_signature(raw, _sign(raw), settings)  # no raise


def test_a_reserialized_body_fails_because_bytes_differ(settings):
    raw = _raw()
    signature = _sign(raw)
    reserialized = json.dumps(json.loads(raw)).encode()

    with pytest.raises(LemonSqueezyWebhookError, match="signature_invalid"):
        verify_lemonsqueezy_signature(reserialized, signature, settings)


def test_missing_wrong_or_malformed_signature_is_refused(settings):
    raw = _raw()
    with pytest.raises(LemonSqueezyWebhookError, match="signature_missing"):
        verify_lemonsqueezy_signature(raw, "", settings)
    with pytest.raises(LemonSqueezyWebhookError, match="signature_invalid"):
        verify_lemonsqueezy_signature(raw, _sign(raw, "wrong_secret"), settings)
    with pytest.raises(LemonSqueezyWebhookError, match="signature_invalid"):
        verify_lemonsqueezy_signature(raw, "not-hex-at-all", settings)


def test_oversized_body_is_refused_before_hashing(settings):
    with pytest.raises(LemonSqueezyWebhookError, match="body_too_large"):
        verify_lemonsqueezy_signature(b"x" * (settings.max_webhook_bytes + 1), "sig", settings)


def test_order_normalizes_to_verified_money_in_minor_units(settings):
    raw = _raw()

    event = normalize_lemonsqueezy_event(raw, settings, received_at=RECEIVED)

    assert event["provider"] == "lemonsqueezy"
    assert event["event_id"] == "lemonsqueezy:order_created:89b36d62-4f5c-4353-853f-0c769d0535c8"
    assert event["event_type"] == "order_created"
    # The merchant of record's signed order IS the proof of payment.
    assert event["verification_state"] == "verified"
    assert event["mode"] == "test"
    payload = event["sanitized_payload"]
    assert payload["gross_minor"] == 50000
    assert payload["currency"] == "THB"
    assert payload["tax_minor"] == 0
    assert payload["status"] == "paid"
    assert payload["slug"] == "aquarelle-botanique-debutants-fr"
    assert payload["provider_order_id"] == "89b36d62-4f5c-4353-853f-0c769d0535c8"


def test_refund_event_carries_the_same_order_id(settings):
    event = normalize_lemonsqueezy_event(
        _raw("lemonsqueezy_order_refunded.json"), settings, received_at=RECEIVED
    )

    assert event["event_type"] == "order_refunded"
    assert event["sanitized_payload"]["provider_order_id"] == "89b36d62-4f5c-4353-853f-0c769d0535c8"
    assert event["sanitized_payload"]["refunded"] is True
    assert event["event_id"].endswith("89b36d62-4f5c-4353-853f-0c769d0535c8")
    assert event["event_id"] != "lemonsqueezy:order_created:89b36d62-4f5c-4353-853f-0c769d0535c8"


def test_live_and_test_events_report_their_own_mode(settings):
    payload = json.loads(_raw())
    payload["meta"]["test_mode"] = False
    live = normalize_lemonsqueezy_event(json.dumps(payload).encode(), settings, received_at=RECEIVED)

    assert live["mode"] == "live"
    assert normalize_lemonsqueezy_event(_raw(), settings, received_at=RECEIVED)["mode"] == "test"


def test_an_event_from_another_store_is_refused(settings):
    payload = json.loads(_raw())
    payload["data"]["attributes"]["store_id"] = 999999

    with pytest.raises(LemonSqueezyWebhookError, match="wrong_store"):
        normalize_lemonsqueezy_event(json.dumps(payload).encode(), settings, received_at=RECEIVED)


def test_unsupported_event_and_malformed_json_fail_closed(settings):
    payload = json.loads(_raw())
    payload["meta"]["event_name"] = "license_key_created"
    with pytest.raises(LemonSqueezyWebhookError, match="unsupported_event"):
        normalize_lemonsqueezy_event(json.dumps(payload).encode(), settings, received_at=RECEIVED)

    with pytest.raises(LemonSqueezyWebhookError, match="malformed_event"):
        normalize_lemonsqueezy_event(b"not json", settings, received_at=RECEIVED)


def test_non_integer_money_is_refused(settings):
    payload = json.loads(_raw())
    payload["data"]["attributes"]["total"] = "500.00"

    with pytest.raises(LemonSqueezyWebhookError, match="malformed_amount"):
        normalize_lemonsqueezy_event(json.dumps(payload).encode(), settings, received_at=RECEIVED)


def test_no_customer_identity_is_ever_stored(settings):
    payload = json.loads(_raw())
    payload["data"]["attributes"]["user_email"] = "buyer@example.test"
    payload["data"]["attributes"]["user_name"] = "Real Buyer"

    event = normalize_lemonsqueezy_event(json.dumps(payload).encode(), settings, received_at=RECEIVED)

    serialized = json.dumps(event).lower()
    for leak in ("buyer@example.test", "real buyer", "user_email", "user_name"):
        assert leak not in serialized
