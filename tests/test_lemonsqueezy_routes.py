"""The public Lemon Squeezy endpoint: signed or nothing."""

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as libra_app

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "commerce"
SECRET = "ls_route_secret"

ENV = {
    "LIBRA_COMMERCE_MODE": "live",
    "STRIPE_WEBHOOK_SECRET_LIVE": "whsec_live_fixture",
    "STRIPE_EXPECTED_ACCOUNT_LIVE": "acct_live_fixture",
    "PAYHIP_WEBHOOK_TOKEN_LIVE": "L" * 48,
    "PAYHIP_ALLOWED_HOSTS": "payhip.com",
    "PAYHIP_PRODUCT_IDS_LIVE": "GDRi5",
    "LEMONSQUEEZY_WEBHOOK_SECRET": SECRET,
    "LEMONSQUEEZY_STORE_ID": "457485",
}


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    path = tmp_path / "ledger.db"
    monkeypatch.setattr(libra_app, "PROFIT_LEDGER_FILE", path)
    monkeypatch.setattr(libra_app, "ENV", {**libra_app.ENV, **ENV})
    return path


@pytest.fixture
def client(ledger):
    return TestClient(libra_app.app)


def _raw(name="lemonsqueezy_order_created.json") -> bytes:
    return (FIXTURES / name).read_bytes()


def _sign(raw: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()


def _count(ledger) -> int:
    if not Path(ledger).exists():
        return 0
    with sqlite3.connect(ledger) as c:
        try:
            return c.execute("SELECT COUNT(*) FROM commerce_events").fetchone()[0]
        except sqlite3.OperationalError:
            return 0


def test_a_signed_order_is_accepted_and_becomes_revenue(client, ledger):
    raw = _raw()

    response = client.post("/api/webhooks/lemonsqueezy", content=raw,
                           headers={"X-Signature": _sign(raw)})

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    with sqlite3.connect(ledger) as c:
        row = c.execute("SELECT status, gross_minor, currency FROM commerce_orders").fetchone()
    assert row == ("paid_verified", 50000, "THB")


def test_an_unsigned_or_forged_body_stores_nothing(client, ledger):
    raw = _raw()

    assert client.post("/api/webhooks/lemonsqueezy", content=raw).status_code == 400
    assert client.post("/api/webhooks/lemonsqueezy", content=raw,
                       headers={"X-Signature": _sign(raw, "wrong")}).status_code == 400
    assert _count(ledger) == 0


def test_an_event_for_another_store_is_forbidden(client, ledger):
    payload = json.loads(_raw())
    payload["data"]["attributes"]["store_id"] = 111111
    raw = json.dumps(payload).encode()

    response = client.post("/api/webhooks/lemonsqueezy", content=raw,
                           headers={"X-Signature": _sign(raw)})

    assert response.status_code == 403
    assert _count(ledger) == 0


def test_replaying_the_same_order_is_idempotent(client, ledger):
    raw = _raw()
    headers = {"X-Signature": _sign(raw)}

    first = client.post("/api/webhooks/lemonsqueezy", content=raw, headers=headers)
    second = client.post("/api/webhooks/lemonsqueezy", content=raw, headers=headers)

    assert first.json()["status"] == "accepted"
    assert second.json()["status"] == "duplicate"
    assert _count(ledger) == 1


def test_a_refund_after_the_order_reverses_the_sale(client, ledger):
    order = _raw()
    client.post("/api/webhooks/lemonsqueezy", content=order, headers={"X-Signature": _sign(order)})
    refund = _raw("lemonsqueezy_order_refunded.json")

    response = client.post("/api/webhooks/lemonsqueezy", content=refund,
                           headers={"X-Signature": _sign(refund)})

    assert response.status_code == 200
    with sqlite3.connect(ledger) as c:
        status = c.execute("SELECT status FROM commerce_orders").fetchone()[0]
        refunded = c.execute("SELECT SUM(amount_minor) FROM commerce_refunds").fetchone()[0]
    assert status == "refunded"
    assert refunded == 50000


def test_no_response_leaks_the_signing_secret(client, ledger):
    raw = _raw()
    for response in (
        client.post("/api/webhooks/lemonsqueezy", content=raw),
        client.post("/api/webhooks/lemonsqueezy", content=raw, headers={"X-Signature": _sign(raw)}),
    ):
        assert SECRET not in response.text
