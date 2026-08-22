"""Payhip links are tracked separately from Amazon, and attribution stays honest."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from content_hub import (
    InvalidDestinationError,
    build_outbound_event,
    make_tracking_token,
    resolve_tracking_token,
)

PAYHIP_HOSTS = frozenset({"payhip.com", "www.payhip.com"})
PAYHIP_URL = "https://payhip.com/b/testkit"
AMAZON_URL = "https://amazon.fr/dp/B000000000"


@pytest.fixture(autouse=True)
def tracking_secret(monkeypatch):
    monkeypatch.setenv("LIBRA_GROWTH_TRACKING_SECRET", "test-secret-value")


def test_payhip_destination_uses_its_own_allowlist():
    token = make_tracking_token(
        "kit-fr", "organic", PAYHIP_URL,
        destination_kind="payhip", allowed_hosts=PAYHIP_HOSTS,
    )

    resolved = resolve_tracking_token(token, allowed_hosts={"payhip": PAYHIP_HOSTS})

    assert resolved["destination"] == PAYHIP_URL
    assert resolved["destination_kind"] == "payhip"
    assert len(resolved["click_id"]) >= 32


@pytest.mark.parametrize("url", [
    "http://payhip.com/b/test",
    "https://payhip.com.evil.example/b/test",
    "https://payhip.com@evil.example/b/test",
    "https://evil.example/b/test",
    "https://payhip.com/b/test#fragment",
])
def test_payhip_destination_bypass_is_rejected(url):
    with pytest.raises(InvalidDestinationError):
        make_tracking_token(
            "kit-fr", "organic", url,
            destination_kind="payhip", allowed_hosts=PAYHIP_HOSTS,
        )


def test_a_payhip_token_cannot_be_resolved_as_an_amazon_one():
    token = make_tracking_token(
        "kit-fr", "organic", PAYHIP_URL,
        destination_kind="payhip", allowed_hosts=PAYHIP_HOSTS,
    )

    with pytest.raises(InvalidDestinationError):
        resolve_tracking_token(token)  # default: Amazon allowlist only


def test_every_approved_amazon_marketplace_still_works():
    from content_hub import APPROVED_AMAZON_HOSTS

    for host in sorted(APPROVED_AMAZON_HOSTS):
        token = make_tracking_token("book", "organic", f"https://{host}/dp/B000000000")
        assert resolve_tracking_token(token)["destination_kind"] == "amazon"


def test_amazon_events_keep_their_existing_kind_and_payload():
    event = build_outbound_event("book", "organic")

    assert event["event_kind"] == "amazon_outbound"
    assert event["payload"] == {}


def test_payhip_click_event_records_an_opaque_id_and_unknown_attribution():
    event = build_outbound_event(
        "kit-fr", "organic", event_kind="payhip_outbound", click_id="c" * 32
    )

    assert event["event_kind"] == "payhip_outbound"
    assert event["payload"]["click_id"] == "c" * 32
    # Until a controlled transaction proves the id survives checkout, a click
    # cannot be credited with a sale.
    assert event["payload"]["attribution_status"] == "unknown"


def test_the_click_id_is_not_appended_to_the_destination_yet():
    """Adding an unproven query parameter risks breaking the checkout link."""
    token = make_tracking_token(
        "kit-fr", "organic", PAYHIP_URL,
        destination_kind="payhip", allowed_hosts=PAYHIP_HOSTS,
    )

    resolved = resolve_tracking_token(token, allowed_hosts={"payhip": PAYHIP_HOSTS})

    assert resolved["destination"] == PAYHIP_URL
    assert resolved["click_id"] not in resolved["destination"]


def test_a_tampered_token_is_refused():
    token = make_tracking_token(
        "kit-fr", "organic", PAYHIP_URL,
        destination_kind="payhip", allowed_hosts=PAYHIP_HOSTS,
    )
    body, _, signature = token.partition(".")

    with pytest.raises(Exception):
        resolve_tracking_token(f"{body}.{'a' * len(signature)}",
                               allowed_hosts={"payhip": PAYHIP_HOSTS})


def test_no_click_event_stores_personal_data():
    event = build_outbound_event(
        "kit-fr", "organic", event_kind="payhip_outbound", click_id="d" * 32
    )

    serialized = str(event).lower()
    for leak in ("ip_address", "user_agent", "email", "cookie", "referer"):
        assert leak not in serialized
