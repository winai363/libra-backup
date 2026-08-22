#!/usr/bin/env python3
"""Is the direct-sales lane ready? One command, honest answer, no secrets printed.

    python3 scripts/commerce_setup_check.py            # report
    python3 scripts/commerce_setup_check.py --stripe   # also verify the Stripe key and
                                                       # create/complete the webhook endpoint

Each step reports `ok`, `missing`, or `manual` — "manual" means only a human
can do it (identity checks, 2FA, OAuth). Nothing here guesses a default.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRA_DIR))

from settings import CommerceSettings, load_env_file  # noqa: E402

ENV_PATH = LIBRA_DIR / ".env"
PUBLIC_BASE = "https://newton-winai-klinprasom.incomeinclick.in.th/libra"

HUMAN_ONLY = [
    ("payhip_account", "Create the Payhip account (email + password) and put PAYHIP_EMAIL / PAYHIP_PASSWORD in .env"),
    ("stripe_account", "Create the Stripe account, pass KYC (ID card), link the Thai bank account"),
    ("payhip_stripe_connect", "In Payhip: Account → Settings → Payment Details → Connect Stripe (OAuth + 2FA)"),
    ("stripe_test_key", "Stripe dashboard (TEST mode) → Developers → API keys → put STRIPE_SECRET_KEY_TEST and STRIPE_EXPECTED_ACCOUNT_TEST (acct_…) in .env"),
]


def _present(env: dict, key: str) -> bool:
    return bool(env.get(key))


def report(env: dict) -> dict:
    steps = []
    for key, how in HUMAN_ONLY:
        done = {
            "payhip_account": _present(env, "PAYHIP_EMAIL") and _present(env, "PAYHIP_PASSWORD"),
            "stripe_account": _present(env, "STRIPE_SECRET_KEY_TEST"),
            "payhip_stripe_connect": _present(env, "PAYHIP_STRIPE_CONNECTED"),
            "stripe_test_key": _present(env, "STRIPE_SECRET_KEY_TEST") and _present(env, "STRIPE_EXPECTED_ACCOUNT_TEST"),
        }[key]
        steps.append({"step": key, "state": "ok" if done else "manual", "how": how})

    readiness = CommerceSettings.readiness(env)
    steps.append({
        "step": "commerce_settings",
        "state": "ok" if readiness["ready"] else "missing",
        "reasons": readiness["reasons"],
        "how": "set LIBRA_COMMERCE_MODE=test, STRIPE_WEBHOOK_SECRET_TEST, PAYHIP_WEBHOOK_TOKEN_TEST, PAYHIP_PRODUCT_IDS_TEST",
    })

    token = env.get("PAYHIP_WEBHOOK_TOKEN_TEST", "")
    steps.append({
        "step": "callback_urls",
        "state": "ok" if token else "missing",
        "stripe_url": f"{PUBLIC_BASE}/api/webhooks/stripe",
        "payhip_url": f"{PUBLIC_BASE}/api/webhooks/payhip/<PAYHIP_WEBHOOK_TOKEN_TEST>" if token else None,
        "how": "generate with: python3 -c \"import secrets; print(secrets.token_urlsafe(36))\"",
    })

    ready = all(s["state"] == "ok" for s in steps)
    return {"ready_for_test_transaction": ready, "steps": steps}


def stripe_setup(env: dict) -> dict:
    import stripe

    import stripe_admin

    key = env.get("STRIPE_SECRET_KEY_TEST", "")
    expected = env.get("STRIPE_EXPECTED_ACCOUNT_TEST", "")
    if not key or not expected:
        return {"state": "missing", "reason": "STRIPE_SECRET_KEY_TEST / STRIPE_EXPECTED_ACCOUNT_TEST not set"}
    account = stripe_admin.verify_account(stripe, api_key=key, expected_account=expected)
    endpoint = stripe_admin.ensure_webhook_endpoint(stripe, url=f"{PUBLIC_BASE}/api/webhooks/stripe")
    if endpoint.get("secret"):
        stripe_admin.write_env_value(ENV_PATH, "STRIPE_WEBHOOK_SECRET_TEST", endpoint["secret"])
        stored = "written_to_env"
    else:
        stored = "not_returned_by_stripe (rotate in dashboard if .env lacks it)"
    return {"state": "ok", "account": account, "endpoint": stripe_admin.describe(endpoint),
            "signing_secret": stored}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stripe", action="store_true", help="verify key + ensure webhook endpoint")
    args = parser.parse_args(argv)

    env = load_env_file(ENV_PATH)
    out = report(env)
    if args.stripe:
        try:
            out["stripe_setup"] = stripe_setup(env)
        except Exception as exc:  # surfaced, never swallowed
            out["stripe_setup"] = {"state": "failed", "error": type(exc).__name__, "detail": str(exc)[:200]}
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0 if out["ready_for_test_transaction"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
