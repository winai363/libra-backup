"""Payhip callbacks are operational observations only — never verified money."""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from payhip_webhook import (
    PayhipWebhookError,
    normalize_payhip_event,
    verify_payhip_callback_token,
)
from settings import CommerceSettings

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "commerce"
RECEIVED_AT = "2026-08-21T10:00:01+00:00"


@pytest.fixture
def settings():
    return CommerceSettings.from_sources({
        "LIBRA_COMMERCE_MODE": "test",
        "STRIPE_WEBHOOK_SECRET_TEST": "whsec_test_fixture",
        "STRIPE_EXPECTED_ACCOUNT_TEST": "acct_test_fixture",
        "PAYHIP_WEBHOOK_TOKEN_TEST": "p" * 48,
        "PAYHIP_ALLOWED_HOSTS": "payhip.com,www.payhip.com",
        "PAYHIP_PRODUCT_IDS_TEST": "kit-fr-test",
    })


def _normalize(name, settings, **overrides):
    payload = json.loads((FIXTURES / name).read_text())
    payload.update(overrides)
    return normalize_payhip_event(json.dumps(payload).encode(), settings, received_at=RECEIVED_AT)


def test_payhip_token_is_required_but_event_remains_financially_unverified(settings):
    verify_payhip_callback_token(settings.payhip_webhook_token, settings.payhip_webhook_token)

    event = normalize_payhip_event(
        (FIXTURES / "payhip_paid.json").read_bytes(), settings, received_at=RECEIVED_AT
    )

    assert event["verification_state"] == "unverified"
    assert event["mode"] == "test"
    assert event["provider"] == "payhip"
    assert event["event_id"] == "payhip:paid:sale_test_1"
    assert event["sanitized_payload"]["provider_product_id"] == "kit-fr-test"
    assert event["sanitized_payload"]["gross_minor"] == 1290
    assert event["sanitized_payload"]["currency"] == "EUR"
    assert event["sanitized_payload"]["provider_payment_id"] == "pi_test_1"


def test_payhip_rejects_wrong_token_unknown_product_and_strips_pii(settings):
    with pytest.raises(PayhipWebhookError, match="callback_token_invalid"):
        verify_payhip_callback_token("wrong", settings.payhip_webhook_token)

    with pytest.raises(PayhipWebhookError, match="unknown_product"):
        _normalize("payhip_paid.json", settings, product_id="unknown-product")

    serialised = json.dumps(
        normalize_payhip_event(
            (FIXTURES / "payhip_paid.json").read_bytes(), settings, received_at=RECEIVED_AT
        )
    ).lower()
    for leaked in ("email", "buyer@example.test", "first_name", "last_name", "test buyer"):
        assert leaked not in serialised


def test_empty_token_never_authenticates(settings):
    with pytest.raises(PayhipWebhookError):
        verify_payhip_callback_token("", "")


def test_refund_event_keeps_its_own_stable_id(settings):
    event = normalize_payhip_event(
        (FIXTURES / "payhip_refunded.json").read_bytes(), settings, received_at=RECEIVED_AT
    )
    assert event["event_type"] == "refunded"
    assert event["event_id"] == "payhip:refunded:refund_test_1"
    assert event["sanitized_payload"]["provider_order_id"] == "sale_test_1"
    assert event["verification_state"] == "unverified"


def test_malformed_oversize_and_unsupported_input_fails_closed(settings):
    with pytest.raises(PayhipWebhookError, match="malformed_event"):
        normalize_payhip_event(b"not json", settings, received_at=RECEIVED_AT)
    with pytest.raises(PayhipWebhookError, match="malformed_event"):
        normalize_payhip_event(b"[1,2,3]", settings, received_at=RECEIVED_AT)
    with pytest.raises(PayhipWebhookError, match="body_too_large"):
        normalize_payhip_event(
            b"x" * (settings.max_webhook_bytes + 1), settings, received_at=RECEIVED_AT
        )
    with pytest.raises(PayhipWebhookError, match="unsupported_event"):
        _normalize("payhip_paid.json", settings, type="chargeback")


def test_missing_stable_id_or_bad_amount_is_refused(settings):
    payload = json.loads((FIXTURES / "payhip_paid.json").read_text())
    del payload["id"]
    with pytest.raises(PayhipWebhookError, match="missing_event_id"):
        normalize_payhip_event(json.dumps(payload).encode(), settings, received_at=RECEIVED_AT)

    with pytest.raises(PayhipWebhookError, match="malformed_amount"):
        _normalize("payhip_paid.json", settings, price="twelve")


def test_currency_is_normalized_to_uppercase(settings):
    event = _normalize("payhip_paid.json", settings, currency="usd")
    assert event["sanitized_payload"]["currency"] == "USD"


def test_module_can_never_return_verified(settings):
    source = (Path(__file__).resolve().parent.parent / "payhip_webhook.py").read_text()
    assert '"verified"' not in source
    assert "'verified'" not in source


def test_payhip_event_mode_follows_configuration_and_stays_unverified():
    """Live Payhip sales are still financially unverified — only the mode changes."""
    live = CommerceSettings.from_sources({
        "LIBRA_COMMERCE_MODE": "live",
        "STRIPE_WEBHOOK_SECRET_LIVE": "whsec_live_fixture",
        "STRIPE_EXPECTED_ACCOUNT_LIVE": "acct_live_fixture",
        "PAYHIP_WEBHOOK_TOKEN_LIVE": "L" * 48,
        "PAYHIP_ALLOWED_HOSTS": "payhip.com",
        "PAYHIP_PRODUCT_IDS_LIVE": "kit-fr-test",
    })

    event = normalize_payhip_event(
        (FIXTURES / "payhip_paid.json").read_bytes(), live, received_at=RECEIVED_AT
    )

    assert event["mode"] == "live"
    assert event["verification_state"] == "unverified"
