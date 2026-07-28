"""Unit tests for content_hub.py: signed tracking tokens, the Amazon
marketplace destination allowlist, outbound event construction, and the
growth summary aggregation. No FastAPI app involved — see
tests/test_growth_routes.py for the HTTP-level behavior."""
import sqlite3

import pytest

from business_ledger import record_hub_event
from content_hub import (
    APPROVED_AMAZON_HOSTS,
    InvalidDestinationError,
    TrackingConfigError,
    build_outbound_event,
    growth_summary,
    make_tracking_token,
    paragraphs_html,
    render_hub_page,
    resolve_tracking_token,
)


AMAZON_URL = "https://www.amazon.com/dp/B0EXAMPLE1"


@pytest.fixture(autouse=True)
def tracking_secret(monkeypatch):
    monkeypatch.setenv("LIBRA_GROWTH_TRACKING_SECRET", "unit-test-secret")


def test_make_and_resolve_tracking_token_round_trips():
    token = make_tracking_token("book-a", "organic-1", AMAZON_URL)

    payload = resolve_tracking_token(token)

    assert payload == {"slug": "book-a", "campaign": "organic-1", "destination": AMAZON_URL}


@pytest.mark.parametrize("host", sorted(APPROVED_AMAZON_HOSTS))
def test_all_approved_marketplace_hosts_are_accepted(host):
    token = make_tracking_token("book-a", "c", f"https://{host}/dp/B0EXAMPLE1")

    payload = resolve_tracking_token(token)

    assert payload["destination"] == f"https://{host}/dp/B0EXAMPLE1"


def test_make_tracking_token_rejects_non_amazon_destination():
    with pytest.raises(InvalidDestinationError):
        make_tracking_token("book-a", "organic-1", "https://evil.example.com/dp/ASIN")


def test_make_tracking_token_rejects_lookalike_host():
    with pytest.raises(InvalidDestinationError):
        make_tracking_token("book-a", "organic-1", "https://www.amazon.com.evil.com/dp/ASIN")


def test_make_tracking_token_rejects_userinfo_host_spoofing():
    with pytest.raises(InvalidDestinationError):
        make_tracking_token("book-a", "organic-1", "https://www.amazon.com@evil.com/dp/ASIN")


def test_make_tracking_token_rejects_non_https_scheme():
    with pytest.raises(InvalidDestinationError):
        make_tracking_token("book-a", "organic-1", "http://www.amazon.com/dp/ASIN")


def test_make_tracking_token_requires_secret_env(monkeypatch):
    monkeypatch.delenv("LIBRA_GROWTH_TRACKING_SECRET", raising=False)

    with pytest.raises(TrackingConfigError):
        make_tracking_token("book-a", "organic-1", AMAZON_URL)


def test_resolve_tracking_token_requires_secret_env(monkeypatch):
    token = make_tracking_token("book-a", "organic-1", AMAZON_URL)
    monkeypatch.delenv("LIBRA_GROWTH_TRACKING_SECRET", raising=False)

    with pytest.raises(TrackingConfigError):
        resolve_tracking_token(token)


def test_resolve_tracking_token_rejects_tampered_payload():
    token = make_tracking_token("book-a", "organic-1", AMAZON_URL)
    body, _, signature = token.partition(".")
    tampered = make_tracking_token("book-b", "organic-1", AMAZON_URL).partition(".")[0] + "." + signature

    with pytest.raises(ValueError):
        resolve_tracking_token(tampered)


def test_resolve_tracking_token_rejects_malformed_token():
    with pytest.raises(ValueError):
        resolve_tracking_token("not-a-valid-token")


def test_resolve_tracking_token_defense_in_depth_rejects_forged_destination():
    """Even a *correctly signed* token must be rejected at resolve time if
    its destination is outside the allowlist — this is the second,
    independent check that protects against a bug or bypass in the
    creation-time check."""
    import base64
    import hashlib
    import hmac
    import json

    payload = {"slug": "book-a", "campaign": "organic-1", "destination": "https://evil.example.com/x"}
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    ).decode("ascii").rstrip("=")
    signature = hmac.new(b"unit-test-secret", body.encode("ascii"), hashlib.sha256).digest()
    sig_b64 = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    forged_token = f"{body}.{sig_b64}"

    with pytest.raises(InvalidDestinationError):
        resolve_tracking_token(forged_token)


def test_build_outbound_event_has_privacy_safe_fields_only_and_unique_keys():
    first = build_outbound_event("book-a", "organic-1")
    second = build_outbound_event("book-a", "organic-1")

    assert first["event_kind"] == "amazon_outbound"
    assert first["slug"] == "book-a"
    assert first["campaign"] == "organic-1"
    assert set(first) == {"event_key", "occurred_at", "slug", "campaign", "event_kind", "payload"}
    assert first["payload"] == {}
    assert first["event_key"] != second["event_key"]


def test_growth_summary_aggregates_hub_events(tmp_path):
    ledger = tmp_path / "ledger.db"
    record_hub_event(ledger, build_outbound_event("book-a", "organic-1"))
    record_hub_event(ledger, build_outbound_event("book-a", "organic-1"))
    record_hub_event(ledger, build_outbound_event("book-b", "reddit-1"))

    summary = growth_summary(ledger)

    assert summary["total_events"] == 3
    assert summary["by_event_kind"] == {"amazon_outbound": 3}
    assert summary["by_slug"]["book-a"] == {"total": 2, "campaigns": {"organic-1": 2}}
    assert summary["by_slug"]["book-b"] == {"total": 1, "campaigns": {"reddit-1": 1}}


def test_growth_summary_empty_ledger(tmp_path):
    ledger = tmp_path / "empty-ledger.db"

    summary = growth_summary(ledger)

    assert summary == {"total_events": 0, "by_event_kind": {}, "by_slug": {}}


def test_render_hub_page_substitutes_placeholders():
    page = render_hub_page(
        "<h1>{{TITLE}}</h1><a href=\"{{CTA_URL}}\">{{CTA_LABEL}}</a>",
        {"TITLE": "My Book", "CTA_URL": "/growth/out/abc", "CTA_LABEL": "Buy now"},
    )

    assert page == '<h1>My Book</h1><a href="/growth/out/abc">Buy now</a>'


def test_render_hub_page_raises_on_missing_placeholder():
    with pytest.raises(KeyError):
        render_hub_page("{{MISSING}}", {})


def test_paragraphs_html_escapes_and_splits_on_blank_lines():
    result = paragraphs_html("Hello <b>world</b>\n\nSecond paragraph.")

    assert result == "<p>Hello &lt;b&gt;world&lt;/b&gt;</p>\n<p>Second paragraph.</p>"
