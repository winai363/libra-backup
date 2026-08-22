"""Stripe setup through the API — idempotent, test-mode only, secrets never printed."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import stripe_admin

WEBHOOK_URL = "https://example.test/libra/api/webhooks/stripe"


class FakeEndpoint(dict):
    pass


class FakeStripe:
    """Just enough of the SDK surface to prove our calls are right."""

    def __init__(self, *, account_id="acct_test_fixture", livemode=False, endpoints=None):
        self.api_key = None
        self._account = {"id": account_id, "livemode": livemode}
        self._endpoints = list(endpoints or [])
        self.calls = []

        outer = self

        class Account:
            @staticmethod
            def retrieve():
                outer.calls.append(("account.retrieve",))
                return outer._account

        class WebhookEndpoint:
            @staticmethod
            def list(limit=100):
                outer.calls.append(("endpoint.list",))
                return {"data": list(outer._endpoints)}

            @staticmethod
            def create(**kwargs):
                outer.calls.append(("endpoint.create", kwargs))
                endpoint = {"id": f"we_{len(outer._endpoints) + 1}", "secret": "whsec_new_secret",
                            "url": kwargs["url"], "enabled_events": kwargs["enabled_events"],
                            "status": "enabled"}
                outer._endpoints.append(endpoint)
                return endpoint

            @staticmethod
            def modify(endpoint_id, **kwargs):
                outer.calls.append(("endpoint.modify", endpoint_id, kwargs))
                for endpoint in outer._endpoints:
                    if endpoint["id"] == endpoint_id:
                        endpoint.update(kwargs)
                        return endpoint
                raise KeyError(endpoint_id)

        self.Account = Account
        self.WebhookEndpoint = WebhookEndpoint


def test_refuses_a_live_key_or_the_wrong_account():
    with pytest.raises(stripe_admin.StripeAdminError, match="test_key_required"):
        stripe_admin.verify_account(FakeStripe(), api_key="sk_live_abc", expected_account="acct_test_fixture")

    with pytest.raises(stripe_admin.StripeAdminError, match="wrong_account"):
        stripe_admin.verify_account(FakeStripe(account_id="acct_other"), api_key="sk_test_abc",
                                    expected_account="acct_test_fixture")

    with pytest.raises(stripe_admin.StripeAdminError, match="live_mode"):
        stripe_admin.verify_account(FakeStripe(livemode=True), api_key="sk_test_abc",
                                    expected_account="acct_test_fixture")


def test_ensure_webhook_creates_once_and_returns_the_secret_only_on_creation():
    fake = FakeStripe()

    first = stripe_admin.ensure_webhook_endpoint(fake, url=WEBHOOK_URL)

    assert first["created"] is True
    assert first["secret"] == "whsec_new_secret"
    assert set(first["enabled_events"]) == set(stripe_admin.REQUIRED_EVENTS)

    second = stripe_admin.ensure_webhook_endpoint(fake, url=WEBHOOK_URL)

    assert second["created"] is False
    assert second["secret"] is None  # Stripe only reveals it once; we never store it here
    assert sum(1 for c in fake.calls if c[0] == "endpoint.create") == 1


def test_ensure_webhook_adds_missing_events_to_an_existing_endpoint():
    fake = FakeStripe(endpoints=[{
        "id": "we_old", "url": WEBHOOK_URL, "status": "enabled",
        "enabled_events": ["payment_intent.succeeded"],
    }])

    result = stripe_admin.ensure_webhook_endpoint(fake, url=WEBHOOK_URL)

    assert result["created"] is False
    assert any(c[0] == "endpoint.modify" for c in fake.calls)
    assert set(result["enabled_events"]) >= set(stripe_admin.REQUIRED_EVENTS)


def test_summary_never_contains_the_secret():
    fake = FakeStripe()
    result = stripe_admin.ensure_webhook_endpoint(fake, url=WEBHOOK_URL)

    assert "whsec_new_secret" not in stripe_admin.describe(result)


def test_required_events_match_what_the_webhook_route_accepts():
    from stripe_webhook import STRIPE_EVENT_TYPES

    assert set(stripe_admin.REQUIRED_EVENTS) == set(STRIPE_EVENT_TYPES)


def test_env_writer_adds_or_replaces_a_key_without_printing_it(tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_text("KEEP=1\nSTRIPE_WEBHOOK_SECRET_TEST=old\n")

    stripe_admin.write_env_value(env, "STRIPE_WEBHOOK_SECRET_TEST", "whsec_fresh")
    stripe_admin.write_env_value(env, "STRIPE_EXPECTED_ACCOUNT_TEST", "acct_x")

    text = env.read_text()
    assert "KEEP=1" in text
    assert "STRIPE_WEBHOOK_SECRET_TEST=whsec_fresh" in text
    assert text.count("STRIPE_WEBHOOK_SECRET_TEST=") == 1
    assert "STRIPE_EXPECTED_ACCOUNT_TEST=acct_x" in text
    assert "whsec_fresh" not in capsys.readouterr().out
