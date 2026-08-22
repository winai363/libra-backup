"""Libra Content Hub — signed first-party outbound tracking plus small
rendering helpers for public book/article hub pages.

Every tracked link is a self-contained HMAC-SHA256 signed token over
(slug, campaign, destination), keyed by the ``LIBRA_GROWTH_TRACKING_SECRET``
environment variable. There is no default secret: if the variable is unset,
token creation and resolution both raise rather than silently signing (or
trusting) with a guessed key — a growth-tracking outage is always safer
than a forgeable or unauthenticated tracking link.

Destinations are restricted to the approved Amazon marketplace hosts at
BOTH token creation time and resolution time (defense in depth) — a
tampered or hand-crafted token can never redirect anywhere else.

Privacy: the only data ever captured for an outbound click is a random
event key, the slug, the campaign, and a timestamp. No IP address, user
agent, cookie, or email is ever read or stored by this module.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from business_ledger import init_ledger

# Approved Amazon marketplace hosts (bare + "www." variant of each). Anything
# outside this set is rejected as a destination, both when a token is minted
# and again when it is resolved.
_AMAZON_MARKETPLACE_BASES = {
    "amazon.com", "amazon.co.uk", "amazon.de", "amazon.fr", "amazon.es",
    "amazon.it", "amazon.com.br", "amazon.ca", "amazon.com.mx",
    "amazon.co.jp", "amazon.in", "amazon.com.au",
}
APPROVED_AMAZON_HOSTS = frozenset(
    _AMAZON_MARKETPLACE_BASES | {f"www.{base}" for base in _AMAZON_MARKETPLACE_BASES}
)

# Each destination kind carries its own allowlist. Widening the Amazon set to
# fit a second storefront would weaken the check that protects the first.
# Our own Lemon Squeezy store only. Any other subdomain is somebody else's
# storefront, which is exactly what an attacker would substitute.
LEMONSQUEEZY_STORE_HOST = "wkbui.lemonsqueezy.com"
APPROVED_LEMONSQUEEZY_HOSTS = frozenset({LEMONSQUEEZY_STORE_HOST})

DEFAULT_ALLOWED_HOSTS = {
    "amazon": APPROVED_AMAZON_HOSTS,
    "lemonsqueezy": APPROVED_LEMONSQUEEZY_HOSTS,
}

TRACKING_SECRET_ENV = "LIBRA_GROWTH_TRACKING_SECRET"


class TrackingConfigError(RuntimeError):
    """Raised when LIBRA_GROWTH_TRACKING_SECRET is not set. Fail closed:
    never fall back to a default signing key."""


class InvalidDestinationError(ValueError):
    """Raised when a destination is not an approved Amazon marketplace URL."""


def _secret_key() -> bytes:
    import os
    secret = os.environ.get(TRACKING_SECRET_ENV)
    if not secret:
        raise TrackingConfigError(
            f"{TRACKING_SECRET_ENV} is not set — refusing to sign or verify tracking tokens"
        )
    return secret.encode("utf-8")


def _validate_destination(destination, allowed_hosts=None) -> str:
    """Reject anything that is not an exact-host https URL on the allowlist.

    urlsplit puts userinfo in `username`, so "https://payhip.com@evil.example/"
    resolves its hostname to evil.example — that trick is rejected explicitly
    rather than relied on. Fragments are rejected too: they never belong on a
    storefront link and are a cheap way to smuggle text past a reviewer.
    """
    allowed = frozenset(allowed_hosts) if allowed_hosts is not None else APPROVED_AMAZON_HOSTS
    if not isinstance(destination, str) or not destination:
        raise InvalidDestinationError("destination must be a non-empty URL string")
    parts = urlsplit(destination)
    if parts.scheme != "https" or not parts.hostname:
        raise InvalidDestinationError(f"destination must be an https URL: {destination!r}")
    if parts.username or parts.password:
        raise InvalidDestinationError("destination must not carry userinfo")
    if parts.fragment:
        raise InvalidDestinationError("destination must not carry a fragment")
    if parts.hostname.lower() not in allowed:
        raise InvalidDestinationError(f"destination host not approved: {parts.hostname!r}")
    return destination


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def make_tracking_token(slug: str, campaign: str, destination: str, *,
                        destination_kind: str = "amazon", allowed_hosts=None) -> str:
    """Mint a signed tracking token for one outbound Amazon link.

    Raises InvalidDestinationError if `destination` is not an approved
    Amazon marketplace https URL, and TrackingConfigError if
    LIBRA_GROWTH_TRACKING_SECRET is unset. Never silently signs with a
    fallback secret or accepts a disallowed destination.
    """
    if not isinstance(slug, str) or not slug:
        raise ValueError("slug is required")
    if not isinstance(campaign, str) or not campaign:
        raise ValueError("campaign is required")
    if allowed_hosts is None:
        allowed_hosts = DEFAULT_ALLOWED_HOSTS.get(destination_kind)
        if allowed_hosts is None:
            raise InvalidDestinationError(f"unknown destination kind: {destination_kind!r}")
    _validate_destination(destination, allowed_hosts)

    payload = {
        "slug": slug,
        "campaign": campaign,
        "destination": destination,
        "destination_kind": destination_kind,
        # Opaque per-click id, minted before the token is signed so it is
        # covered by the signature and can be matched to a hub event.
        "click_id": secrets.token_hex(16),
    }
    body = _b64encode(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8"))
    signature = hmac.new(_secret_key(), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64encode(signature)}"


def resolve_tracking_token(token: str, allowed_hosts=None) -> dict:
    """Verify a token's signature and re-check the destination allowlist
    (defense in depth against a hand-crafted or tampered token), returning
    the decoded {"slug", "campaign", "destination"} payload.

    Raises ValueError (or a subclass) on any malformed, forged, or
    disallowed token, and TrackingConfigError if the signing secret is
    unset.
    """
    if not isinstance(token, str) or "." not in token:
        raise ValueError("malformed tracking token")
    body, _, signature_b64 = token.partition(".")
    if not body or not signature_b64:
        raise ValueError("malformed tracking token")
    try:
        signature = _b64decode(signature_b64)
    except Exception as exc:
        raise ValueError("malformed tracking token signature") from exc

    expected = hmac.new(_secret_key(), body.encode("ascii"), hashlib.sha256).digest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("invalid tracking token signature")

    try:
        payload = json.loads(_b64decode(body))
    except Exception as exc:
        raise ValueError("malformed tracking token payload") from exc
    if not isinstance(payload, dict) or {"slug", "campaign", "destination"} - payload.keys():
        raise ValueError("malformed tracking token payload")

    kind = payload.get("destination_kind", "amazon")
    hosts_by_kind = allowed_hosts if allowed_hosts is not None else DEFAULT_ALLOWED_HOSTS
    hosts = hosts_by_kind.get(kind)
    if hosts is None:
        raise InvalidDestinationError(f"destination kind not allowed here: {kind!r}")
    _validate_destination(payload["destination"], hosts)
    payload.setdefault("destination_kind", "amazon")
    payload.setdefault("click_id", "")
    return payload


def build_outbound_event(slug: str, campaign: str, *, now: datetime | None = None,
                         event_kind: str = "amazon_outbound", click_id: str | None = None) -> dict:
    """Build one `amazon_outbound` hub event. Stores only a random event
    key, slug, campaign, and timestamp — never an IP address, user agent,
    cookie, or email. Each call returns a fresh event_key, so repeated
    clicks are recorded as separate events rather than deduplicated."""
    occurred_at = (now or datetime.now(timezone.utc)).isoformat()
    payload: dict = {}
    if click_id:
        payload = {
            "click_id": click_id,
            # A click is not a sale. Until a controlled transaction proves the
            # id survives Payhip checkout, nothing may be attributed to it.
            "attribution_status": "unknown",
        }
    return {
        "event_key": secrets.token_hex(16),
        "occurred_at": occurred_at,
        "slug": slug,
        "campaign": campaign,
        "event_kind": event_kind,
        "payload": payload,
    }


def growth_summary(ledger_path) -> dict:
    """Aggregate hub_events into totals for the /api/growth/summary API:
    overall total, totals by event kind, and per-slug totals/campaigns."""
    path = Path(ledger_path)
    init_ledger(path)
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            "SELECT slug, campaign, event_kind, COUNT(*) FROM hub_events "
            "GROUP BY slug, campaign, event_kind"
        ).fetchall()

    total_events = 0
    by_event_kind: dict[str, int] = {}
    by_slug: dict[str, dict] = {}
    for slug, campaign, event_kind, count in rows:
        total_events += count
        by_event_kind[event_kind] = by_event_kind.get(event_kind, 0) + count
        slug_entry = by_slug.setdefault(slug, {"total": 0, "campaigns": {}})
        slug_entry["total"] += count
        slug_entry["campaigns"][campaign] = slug_entry["campaigns"].get(campaign, 0) + count

    return {"total_events": total_events, "by_event_kind": by_event_kind, "by_slug": by_slug}


# ── Page rendering helpers ────────────────────────────────────────────────
# Templates use plain {{KEY}} placeholders. This is dumb string substitution,
# not an HTML-aware templating engine — callers must pass already-safe HTML
# strings (see escape_text / paragraphs_html below).

_PLACEHOLDER_RE = re.compile(r"\{\{([A-Z_]+)\}\}")


def render_hub_page(template: str, context: dict) -> str:
    def _sub(match: re.Match) -> str:
        key = match.group(1)
        if key not in context:
            raise KeyError(f"missing hub page placeholder: {key}")
        return context[key]
    return _PLACEHOLDER_RE.sub(_sub, template)


def escape_text(value) -> str:
    return html.escape(str(value if value is not None else ""))


def paragraphs_html(text) -> str:
    """Render plain text as escaped <p> paragraphs split on blank lines, so
    stored article bodies can never inject raw unescaped HTML into a hub
    page."""
    text = str(text or "")
    paragraphs = [escape_text(chunk.strip()) for chunk in text.split("\n\n") if chunk.strip()]
    return "\n".join(f"<p>{p}</p>" for p in paragraphs) or "<p></p>"
