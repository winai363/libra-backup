"""Public webhook endpoints — fail closed, log nothing sensitive."""

import json
import sqlite3
import sys
import time
from pathlib import Path

import pytest
import stripe
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as libra_app
from settings import CommerceSettings

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "commerce"

ENV = {
    "LIBRA_COMMERCE_MODE": "test",
    "STRIPE_WEBHOOK_SECRET_TEST": "whsec_test_fixture",
    "STRIPE_EXPECTED_ACCOUNT_TEST": "acct_test_fixture",
    "PAYHIP_WEBHOOK_TOKEN_TEST": "p" * 48,
    "PAYHIP_ALLOWED_HOSTS": "payhip.com",
    "PAYHIP_PRODUCT_IDS_TEST": "kit-fr-test",
}


@pytest.fixture
def settings():
    return CommerceSettings.from_sources(ENV)


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "ledger.db"
    monkeypatch.setattr(libra_app, "PROFIT_LEDGER_FILE", path)
    monkeypatch.setattr(libra_app, "ENV", {**libra_app.ENV, **ENV})
    return path


@pytest.fixture
def client(ledger):
    return TestClient(libra_app.app)


def _sign(raw: bytes, secret: str, timestamp: int | None = None) -> str:
    timestamp = timestamp or int(time.time())
    computed = stripe.WebhookSignature._compute_signature(
        f"{timestamp}.{raw.decode('utf-8')}", secret
    )
    return f"t={timestamp},v1={computed}"


def _stripe_body(name="stripe_payment_intent_succeeded.json") -> bytes:
    return (FIXTURES / name).read_bytes()


def _event_count(ledger) -> int:
    if not Path(ledger).exists():
        return 0
    with sqlite3.connect(ledger) as connection:
        try:
            return connection.execute("SELECT COUNT(*) FROM commerce_events").fetchone()[0]
        except sqlite3.OperationalError:
            return 0


def test_stripe_webhook_is_public_but_requires_valid_signature(client, settings, ledger):
    raw = _stripe_body()

    response = client.post(
        "/api/webhooks/stripe",
        content=raw,
        headers={"Stripe-Signature": _sign(raw, settings.stripe_webhook_secret)},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert _event_count(ledger) == 1


def test_unsigned_or_forged_stripe_requests_store_nothing(client, ledger):
    raw = _stripe_body()

    assert client.post("/api/webhooks/stripe", content=raw).status_code == 400
    assert client.post(
        "/api/webhooks/stripe", content=raw, headers={"Stripe-Signature": "t=1,v1=bad"}
    ).status_code == 400
    assert _event_count(ledger) == 0


def test_oversized_body_is_rejected_without_storing(client, settings, ledger):
    response = client.post(
        "/api/webhooks/stripe",
        content=b"x" * (settings.max_webhook_bytes + 1),
        headers={"Stripe-Signature": "t=1,v1=bad"},
    )

    assert response.status_code == 413
    assert _event_count(ledger) == 0


def test_wrong_account_or_live_mode_is_forbidden(client, settings, ledger):
    payload = json.loads(_stripe_body())
    payload["account"] = "acct_someone_else"
    raw = json.dumps(payload).encode()

    response = client.post(
        "/api/webhooks/stripe",
        content=raw,
        headers={"Stripe-Signature": _sign(raw, settings.stripe_webhook_secret)},
    )

    assert response.status_code == 403
    assert _event_count(ledger) == 0


def test_unsupported_event_is_quarantined_not_stored(client, settings, ledger):
    payload = json.loads(_stripe_body())
    payload["type"] = "customer.created"
    raw = json.dumps(payload).encode()

    response = client.post(
        "/api/webhooks/stripe",
        content=raw,
        headers={"Stripe-Signature": _sign(raw, settings.stripe_webhook_secret)},
    )

    assert response.status_code == 202
    assert _event_count(ledger) == 0


def test_identical_replay_is_accepted_once_and_conflicting_replay_is_flagged(
    client, settings, ledger
):
    raw = _stripe_body()
    header = _sign(raw, settings.stripe_webhook_secret)

    assert client.post("/api/webhooks/stripe", content=raw, headers={"Stripe-Signature": header}).status_code == 200
    replay = client.post("/api/webhooks/stripe", content=raw, headers={"Stripe-Signature": header})
    assert replay.status_code == 200
    assert replay.json()["status"] == "duplicate"

    changed = json.loads(raw)
    changed["data"]["object"]["amount"] = 9900
    changed["data"]["object"]["amount_received"] = 9900
    forged = json.dumps(changed).encode()
    conflict = client.post(
        "/api/webhooks/stripe",
        content=forged,
        headers={"Stripe-Signature": _sign(forged, settings.stripe_webhook_secret)},
    )

    assert conflict.status_code == 409
    assert _event_count(ledger) == 1


def test_payhip_callback_needs_the_secret_path_and_never_reveals_it(client, settings, ledger):
    raw = (FIXTURES / "payhip_paid.json").read_bytes()

    wrong = client.post("/api/webhooks/payhip/wrong-token", content=raw)
    assert wrong.status_code == 404
    assert "token" not in wrong.text.lower()
    assert _event_count(ledger) == 0

    right = client.post(
        f"/api/webhooks/payhip/{settings.payhip_webhook_token}", content=raw
    )
    assert right.status_code == 200
    assert _event_count(ledger) == 1


def test_payhip_unknown_product_and_malformed_json_are_refused(client, settings, ledger):
    path = f"/api/webhooks/payhip/{settings.payhip_webhook_token}"

    assert client.post(path, content=b"not json").status_code == 400

    payload = json.loads((FIXTURES / "payhip_paid.json").read_text())
    payload["product_id"] = "someone-elses-product"
    assert client.post(path, content=json.dumps(payload).encode()).status_code == 400
    assert _event_count(ledger) == 0


def test_missing_commerce_configuration_returns_503_not_a_crash(client, monkeypatch, ledger):
    monkeypatch.setattr(libra_app, "ENV", {})

    response = client.post("/api/webhooks/stripe", content=b"{}",
                           headers={"Stripe-Signature": "t=1,v1=x"})

    assert response.status_code == 503


def test_commerce_summary_requires_auth(client):
    assert client.get("/api/commerce/summary").status_code == 401


def test_no_response_body_leaks_a_secret_or_customer_detail(client, settings, ledger):
    raw = (FIXTURES / "payhip_paid.json").read_bytes()
    responses = [
        client.post("/api/webhooks/payhip/wrong", content=raw),
        client.post(f"/api/webhooks/payhip/{settings.payhip_webhook_token}", content=raw),
        client.post("/api/webhooks/stripe", content=_stripe_body()),
    ]

    for response in responses:
        body = response.text.lower()
        for leak in (settings.payhip_webhook_token.lower(), "whsec_", "buyer@example.test", "first_name"):
            assert leak not in body
