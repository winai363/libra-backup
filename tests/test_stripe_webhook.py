"""Stripe is the only thing that can prove money moved.

Verification is against the exact raw bytes we received — re-serialising the
JSON changes the signature, which is why the route must never parse before it
verifies.
"""

import json
import sys
import time
from pathlib import Path

import pytest
import stripe

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from settings import CommerceSettings
from stripe_webhook import (
    StripeWebhookError,
    normalize_stripe_event,
    verify_stripe_event,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "commerce"


@pytest.fixture
def settings():
    return CommerceSettings.from_sources({
        "LIBRA_COMMERCE_MODE": "test",
        "STRIPE_WEBHOOK_SECRET_TEST": "whsec_test_fixture",
        "STRIPE_EXPECTED_ACCOUNT_TEST": "acct_test_fixture",
        "PAYHIP_WEBHOOK_TOKEN_TEST": "p" * 48,
        "PAYHIP_ALLOWED_HOSTS": "payhip.com",
        "PAYHIP_PRODUCT_IDS_TEST": "kit-fr-test",
    })


def _signature(raw: bytes, secret: str, timestamp: int) -> str:
    computed = stripe.WebhookSignature._compute_signature(
        f"{timestamp}.{raw.decode('utf-8')}", secret
    )
    return f"t={timestamp},v1={computed}"


def _raw(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _signed(name, settings, *, timestamp=None, mutate=None):
    payload = json.loads(_raw(name))
    if mutate:
        mutate(payload)
        raw = json.dumps(payload).encode()
    else:
        raw = _raw(name)
    timestamp = timestamp or int(time.time())
    return raw, _signature(raw, settings.stripe_webhook_secret, timestamp), timestamp


def test_stripe_verifies_exact_raw_body_and_expected_test_account(settings):
    raw, header, timestamp = _signed("stripe_payment_intent_succeeded.json", settings)

    event = verify_stripe_event(raw, header, settings, now=timestamp + 10)

    assert event["livemode"] is False
    assert event["account"] == settings.stripe_expected_account
    assert event["type"] == "payment_intent.succeeded"


def test_stripe_rejects_reserialized_or_stale_payload(settings):
    raw, header, timestamp = _signed("stripe_payment_intent_succeeded.json", settings)
    reserialized = json.dumps(json.loads(raw), indent=2).encode()

    with pytest.raises(StripeWebhookError, match="signature_invalid"):
        verify_stripe_event(reserialized, header, settings, now=timestamp + 10)
    with pytest.raises(StripeWebhookError, match="signature_stale"):
        verify_stripe_event(raw, header, settings, now=timestamp + 301)


def test_missing_or_malformed_signature_is_refused(settings):
    raw = _raw("stripe_payment_intent_succeeded.json")
    with pytest.raises(StripeWebhookError, match="signature_missing"):
        verify_stripe_event(raw, "", settings, now=int(time.time()))
    with pytest.raises(StripeWebhookError, match="signature_invalid"):
        verify_stripe_event(raw, "t=1,v1=deadbeef", settings, now=int(time.time()))


def test_oversized_body_is_refused_before_any_parsing(settings):
    with pytest.raises(StripeWebhookError, match="body_too_large"):
        verify_stripe_event(
            b"x" * (settings.max_webhook_bytes + 1), "sig", settings, now=int(time.time())
        )


def test_live_mode_and_wrong_account_are_refused(settings):
    def go_live(payload):
        payload["livemode"] = True

    raw, header, timestamp = _signed(
        "stripe_payment_intent_succeeded.json", settings, mutate=go_live
    )
    with pytest.raises(StripeWebhookError, match="wrong_mode"):
        verify_stripe_event(raw, header, settings, now=timestamp + 5)

    def other_account(payload):
        payload["account"] = "acct_someone_else"

    raw, header, timestamp = _signed(
        "stripe_payment_intent_succeeded.json", settings, mutate=other_account
    )
    with pytest.raises(StripeWebhookError, match="wrong_account"):
        verify_stripe_event(raw, header, settings, now=timestamp + 5)


def test_unsupported_event_type_is_refused(settings):
    def change_type(payload):
        payload["type"] = "customer.created"

    raw, header, timestamp = _signed(
        "stripe_payment_intent_succeeded.json", settings, mutate=change_type
    )
    with pytest.raises(StripeWebhookError, match="unsupported_event"):
        verify_stripe_event(raw, header, settings, now=timestamp + 5)


def test_normalized_payment_carries_ids_and_integer_money(settings):
    raw, header, timestamp = _signed("stripe_payment_intent_succeeded.json", settings)
    event = verify_stripe_event(raw, header, settings, now=timestamp + 5)

    normalized = normalize_stripe_event(event, raw, received_at="2026-08-22T10:00:00+00:00")

    assert normalized["provider"] == "stripe"
    assert normalized["event_id"] == "evt_test_pi_1"
    assert normalized["verification_state"] == "verified"
    assert normalized["mode"] == "test"
    assert normalized["occurred_at"] == "2026-08-17T20:53:20+00:00"
    payload = normalized["sanitized_payload"]
    assert payload["provider_payment_id"] == "pi_test_1"
    assert payload["amount_minor"] == 1290
    assert isinstance(payload["amount_minor"], int)
    assert payload["currency"] == "EUR"
    assert payload["provider_order_id"] == "sale_test_1"


@pytest.mark.parametrize(
    ("fixture", "expected_status"),
    [
        ("stripe_refund_created.json", "pending"),
        ("stripe_refund_updated.json", "succeeded"),
        ("stripe_refund_failed.json", "failed"),
    ],
)
def test_every_refund_lifecycle_event_normalizes_to_one_refund_id(
    settings, fixture, expected_status
):
    raw, header, timestamp = _signed(fixture, settings)
    event = verify_stripe_event(raw, header, settings, now=timestamp + 5)

    normalized = normalize_stripe_event(event, raw, received_at="2026-08-22T10:00:00+00:00")

    payload = normalized["sanitized_payload"]
    assert payload["provider_refund_id"] == "re_test_1"
    assert payload["provider_payment_id"] == "pi_test_1"
    assert payload["amount_minor"] == 400
    assert payload["status"] == expected_status


def test_payout_and_balance_events_normalize_without_inventing_fees(settings):
    raw, header, timestamp = _signed("stripe_payout_paid.json", settings)
    payout = normalize_stripe_event(
        verify_stripe_event(raw, header, settings, now=timestamp + 5), raw,
        received_at="2026-08-22T10:00:00+00:00",
    )
    assert payout["sanitized_payload"]["provider_payout_id"] == "po_test_1"
    assert payout["sanitized_payload"]["amount_minor"] == 1240

    raw, header, timestamp = _signed("stripe_balance_available.json", settings)
    balance = normalize_stripe_event(
        verify_stripe_event(raw, header, settings, now=timestamp + 5), raw,
        received_at="2026-08-22T10:00:00+00:00",
    )
    payload = balance["sanitized_payload"]
    assert payload["available"] == [{"amount_minor": 1240, "currency": "EUR"}]
    assert "fee_minor" not in payload  # balance.available itemises nothing


def test_malformed_amount_is_refused(settings):
    def break_amount(payload):
        payload["data"]["object"]["amount"] = "1290"
        payload["data"]["object"]["amount_received"] = "1290"

    raw, header, timestamp = _signed(
        "stripe_payment_intent_succeeded.json", settings, mutate=break_amount
    )
    event = verify_stripe_event(raw, header, settings, now=timestamp + 5)
    with pytest.raises(StripeWebhookError, match="malformed_event"):
        normalize_stripe_event(event, raw, received_at="2026-08-22T10:00:00+00:00")


def test_fixtures_and_normalized_output_carry_no_personal_data(settings):
    for fixture in sorted(FIXTURES.glob("stripe_*.json")):
        text = fixture.read_text().lower()
        for leak in ("@", "card", "cvc", "last4", "address", "ip_address", "user_agent"):
            assert leak not in text, (fixture.name, leak)

    raw, header, timestamp = _signed("stripe_payment_intent_succeeded.json", settings)
    normalized = normalize_stripe_event(
        verify_stripe_event(raw, header, settings, now=timestamp + 5), raw,
        received_at="2026-08-22T10:00:00+00:00",
    )
    assert "email" not in json.dumps(normalized).lower()
