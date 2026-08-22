"""Stripe account setup through the API — the part of onboarding a machine can do.

What a human still has to do once: create the Stripe account, pass KYC, link a
Thai bank account, and click "Connect" inside Payhip. After that, everything
here is idempotent and test-mode only:

- verify the key is a TEST key and belongs to the expected account
- create (or complete) the webhook endpoint for our callback URL
- hand the signing secret straight into `.env` without printing it

The stripe module is injected so tests can run without the network.
"""

from __future__ import annotations

import re
from pathlib import Path

# Must stay equal to stripe_webhook.STRIPE_EVENT_TYPES — a test enforces it.
REQUIRED_EVENTS = (
    "payment_intent.succeeded",
    "refund.created",
    "refund.updated",
    "refund.failed",
    "charge.dispute.created",
    "balance.available",
    "payout.paid",
    "payout.failed",
)

ENV_LINE_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=")


class StripeAdminError(RuntimeError):
    def __init__(self, code: str, detail: str = ""):
        self.code = code
        super().__init__(f"{code}: {detail}" if detail else code)


def verify_account(stripe_module, *, api_key: str, expected_account: str) -> dict:
    if not str(api_key).startswith("sk_test_"):
        raise StripeAdminError("test_key_required", "only sk_test_ keys are accepted in this lane")
    stripe_module.api_key = api_key
    account = stripe_module.Account.retrieve()
    account_id = account.get("id") if hasattr(account, "get") else getattr(account, "id", None)
    livemode = account.get("livemode") if hasattr(account, "get") else getattr(account, "livemode", None)
    if livemode:
        raise StripeAdminError("live_mode", "account reports livemode=true")
    if account_id != expected_account:
        raise StripeAdminError("wrong_account", f"key belongs to {account_id}, expected {expected_account}")
    return {"account": account_id, "livemode": bool(livemode)}


def _get(obj, key, default=None):
    if hasattr(obj, "get"):
        return obj.get(key, default)
    return getattr(obj, key, default)


def ensure_webhook_endpoint(stripe_module, *, url: str) -> dict:
    """Create the endpoint if missing; otherwise make sure it carries every event.

    Stripe reveals the signing secret only at creation. If the endpoint already
    exists the secret is not retrievable — rotate it in the dashboard instead.
    """
    listing = stripe_module.WebhookEndpoint.list(limit=100)
    existing = None
    for endpoint in _get(listing, "data", []) or []:
        if _get(endpoint, "url") == url:
            existing = endpoint
            break

    if existing is None:
        created = stripe_module.WebhookEndpoint.create(
            url=url,
            enabled_events=list(REQUIRED_EVENTS),
            description="Libra commerce test-mode webhook",
        )
        return {
            "created": True,
            "id": _get(created, "id"),
            "url": url,
            "enabled_events": list(_get(created, "enabled_events", REQUIRED_EVENTS)),
            "secret": _get(created, "secret"),
            "status": _get(created, "status"),
        }

    have = set(_get(existing, "enabled_events", []) or [])
    missing = [event for event in REQUIRED_EVENTS if event not in have]
    enabled = sorted(have)
    if missing:
        updated = stripe_module.WebhookEndpoint.modify(
            _get(existing, "id"), enabled_events=sorted(have | set(REQUIRED_EVENTS))
        )
        enabled = list(_get(updated, "enabled_events", []))
    return {
        "created": False,
        "id": _get(existing, "id"),
        "url": url,
        "enabled_events": enabled,
        "secret": None,
        "status": _get(existing, "status"),
        "added_events": missing,
    }


def describe(result: dict) -> str:
    """Human-readable summary that can never leak the signing secret."""
    safe = {k: v for k, v in result.items() if k != "secret"}
    safe["secret_returned"] = bool(result.get("secret"))
    return ", ".join(f"{k}={v}" for k, v in sorted(safe.items()))


def write_env_value(env_path: Path, key: str, value: str) -> None:
    """Set KEY=value in a .env file in place. Never echoes the value."""
    env_path = Path(env_path)
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    replaced = False
    for index, line in enumerate(lines):
        match = ENV_LINE_RE.match(line)
        if match and match.group(1) == key:
            lines[index] = f"{key}={value}"
            replaced = True
    if not replaced:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines).rstrip("\n") + "\n", encoding="utf-8")
