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

MODE = "live"  # Payhip has no sandbox; authorised by Bui 22 Aug 2026

HUMAN_ONLY = [
    ("payhip_account", "Create the Payhip account (email + password) and put PAYHIP_EMAIL / PAYHIP_PASSWORD in .env"),
    ("stripe_account", "Create the Stripe account, pass KYC (ID card), link the Thai bank account"),
    ("payhip_stripe_connect", "In Payhip: Account → Settings → Payment Details → Connect Stripe (OAuth + 2FA)"),
    ("stripe_webhook_ready", "run --stripe once with STRIPE_SECRET_KEY_LIVE set; it creates the endpoint, stores the signing secret, and the secret key can then be deleted and rolled"),
]


def _present(env: dict, key: str) -> bool:
    return bool(env.get(key))


def report(env: dict) -> dict:
    steps = []
    for key, how in HUMAN_ONLY:
        done = {
            "payhip_account": _present(env, "PAYHIP_EMAIL") and _present(env, "PAYHIP_PASSWORD"),
            "stripe_account": _present(env, f"STRIPE_EXPECTED_ACCOUNT_{MODE.upper()}"),
            "payhip_stripe_connect": _present(env, "PAYHIP_STRIPE_CONNECTED"),
            # The runtime verifies signatures; it never needs the secret key.
            "stripe_webhook_ready": _present(env, f"STRIPE_WEBHOOK_SECRET_{MODE.upper()}")
                                    and _present(env, f"STRIPE_EXPECTED_ACCOUNT_{MODE.upper()}"),
        }[key]
        steps.append({"step": key, "state": "ok" if done else "manual", "how": how})

    readiness = CommerceSettings.readiness(env)
    steps.append({
        "step": "commerce_settings",
        "state": "ok" if readiness["ready"] else "missing",
        "reasons": readiness["reasons"],
        "how": f"set LIBRA_COMMERCE_MODE={MODE} plus the {MODE.upper()}-suffixed secrets, token and product ids",
    })

    token = env.get(f"PAYHIP_WEBHOOK_TOKEN_{MODE.upper()}", "")
    steps.append({
        "step": "callback_urls",
        "state": "ok" if token else "missing",
        "stripe_url": f"{PUBLIC_BASE}/api/webhooks/stripe",
        "payhip_url": f"{PUBLIC_BASE}/api/webhooks/payhip/<PAYHIP_WEBHOOK_TOKEN_{MODE.upper()}>" if token else None,
        "how": "generate with: python3 -c \"import secrets; print(secrets.token_urlsafe(36))\"",
    })

    ready = all(s["state"] == "ok" for s in steps)
    return {"ready_for_test_transaction": ready, "steps": steps}


def stripe_setup(env: dict) -> dict:
    import stripe

    import stripe_admin

    suffix = MODE.upper()
    key = env.get(f"STRIPE_SECRET_KEY_{suffix}", "")
    if not key:
        return {"state": "missing", "reason": f"STRIPE_SECRET_KEY_{suffix} not set"}
    prefix = "sk_live_" if MODE == "live" else "sk_test_"
    if not key.startswith(prefix):
        return {"state": "failed", "error": f"expected a {MODE} key starting with {prefix}"}
    expected = env.get(f"STRIPE_EXPECTED_ACCOUNT_{suffix}", "")
    if not expected:
        # One less thing for a human to copy correctly: the key already knows
        # which account it belongs to.
        stripe.api_key = key
        discovered = stripe.Account.retrieve().to_dict()
        expected = discovered["id"]
        stripe_admin.write_env_value(ENV_PATH, f"STRIPE_EXPECTED_ACCOUNT_{suffix}", expected)
    account = stripe_admin.verify_account(
        stripe, api_key=key, expected_account=expected, mode=MODE
    )
    endpoint = stripe_admin.ensure_webhook_endpoint(stripe, url=f"{PUBLIC_BASE}/api/webhooks/stripe")
    if endpoint.get("secret"):
        stripe_admin.write_env_value(ENV_PATH, f"STRIPE_WEBHOOK_SECRET_{suffix}", endpoint["secret"])
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
