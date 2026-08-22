# Libra direct-sales lane — test mode runbook

Selling straight to the reader through Payhip, with Stripe as the only thing
that can prove money moved. Nothing in this lane touches KDP.

**Current state: `implemented_test_mode`.** The code and its tests are done and
no provider account has been touched. Live selling is blocked until the
activation gate at the bottom of this page is satisfied.

---

## The one rule that shapes everything

**Payhip observes. Stripe proves.**

A Payhip "paid" callback proves only that someone reached our callback URL. It
opens an order in `payment_pending` and contributes **zero** revenue. Revenue
exists only when a Stripe-verified `payment_intent.succeeded` matches the same
payment id, amount and currency.

Everything else follows from that: refunds reverse revenue only at `succeeded`,
fees stay `null` until an authorised source itemises them, and a payout is
settlement — never revenue.

---

## 1. Configuration

Add to `/root/libra/.env`. All values are **test-mode** credentials.

| Name | What it is |
|---|---|
| `LIBRA_COMMERCE_MODE` | must be exactly `test` |
| `STRIPE_WEBHOOK_SECRET_TEST` | endpoint signing secret (`whsec_…`) from the Stripe **test** dashboard |
| `STRIPE_EXPECTED_ACCOUNT_TEST` | the `acct_…` id events must come from |
| `PAYHIP_WEBHOOK_TOKEN_TEST` | a high-entropy string ≥32 chars that you generate; it becomes part of the callback URL |
| `PAYHIP_ALLOWED_HOSTS` | `payhip.com,www.payhip.com` |
| `PAYHIP_PRODUCT_IDS_TEST` | comma-separated Payhip product ids we accept |

Generate the Payhip token without ever printing a real secret to a shared log:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(36))"
```

Check readiness — this prints booleans and reason codes, never values:

```bash
cd /root/libra && python3 -c "
from settings import CommerceSettings, load_env_file
from pathlib import Path
print(CommerceSettings.readiness(load_env_file(Path('.env'))))"
```

Anything missing makes the webhook routes return `503 commerce_not_configured`
instead of guessing a default.

## 2. Callback URLs

External (through nginx):

- `https://<domain>/libra/api/webhooks/stripe`
- `https://<domain>/libra/api/webhooks/payhip/<PAYHIP_WEBHOOK_TOKEN_TEST>`

The Payhip secret lives **in the path**. A wrong token returns a generic `404`
so the endpoint's existence is never confirmed to a prober.

> Do not add a top-level `location /api/` block to nginx — it would shadow the
> chat app's own `/api/` routes. Keep everything under `/libra/`.

## 3. Rotating a secret

1. Generate the new value and put it in `.env`.
2. Restart `libra.service`.
3. **Register the new Payhip callback URL, then delete the old one.** Until the
   old URL is removed, anyone holding it can still post events.
4. For Stripe, roll the endpoint secret in the dashboard and update
   `STRIPE_WEBHOOK_SECRET_TEST`. Events signed with the old secret start failing
   as `signature_invalid`, which is the intended outcome.

## 4. Replaying a signed event without touching the network

```bash
cd /root/libra && python3 - <<'PY'
import json, time, stripe
from pathlib import Path
from fastapi.testclient import TestClient
import app as libra_app
from settings import CommerceSettings, load_env_file

settings = CommerceSettings.from_sources(load_env_file(Path(".env")))
raw = Path("tests/fixtures/commerce/stripe_payment_intent_succeeded.json").read_bytes()
ts = int(time.time())
sig = stripe.WebhookSignature._compute_signature(f"{ts}.{raw.decode()}", settings.stripe_webhook_secret)
response = TestClient(libra_app.app).post(
    "/api/webhooks/stripe", content=raw, headers={"Stripe-Signature": f"t={ts},v1={sig}"}
)
print(response.status_code, response.json())
PY
```

Fixtures are synthetic and PII-free. Never paste a real customer's payload into
a fixture file.

## 5. Reconciling

```bash
cd /root/libra
python3 scripts/libra_commerce_reconcile.py --ledger data/libra-business.db --mode test --dry-run
python3 scripts/libra_commerce_reconcile.py --ledger data/libra-business.db --mode test --apply
```

`--dry-run` opens the database read-only. `--apply` sweeps events whose
counterpart arrived later. Exit `3` means something needs a human: a conflict, a
`manual_required` event, or an open incident.

Reading the money:

```bash
curl -fsS --cookie "libra_token=$SESSION_TOKEN" http://127.0.0.1:8200/api/commerce/summary | python3 -m json.tool
```

`payhip_fee_minor: null` with `payhip_fee_complete: false` means *unknown*, not
zero — and `contribution_minor` stays `null` until both fee sides are known.

## 6. Back up before the first real transaction

```bash
sqlite3 /root/libra/data/libra-business.db ".backup '/root/backups/libra-business-before-commerce.db'"
```

Check `/root/backups/` exists and the target path is what you intend before
running it.

## 7. Deployment check (test mode, no live registration)

```bash
systemctl status libra.service --no-pager
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8200/api/commerce/summary   # expect 401
cd /root/libra && python3 -m pytest tests/ -q
```

## 8. What stays manual — forever, or until explicitly changed

Refund initiation · disputes · OTP · CAPTCHA · KYC · account review · bank
setup · webhook registration · Payhip product creation. The code returns
`manual_required` for these rather than pretending to handle them.

Paid spend is hard-coded to zero in `commerce_growth.py`. Every decision it
returns carries `paid_spend_minor: 0`.

---

## Activation gate — implementation is not activation

| State | Meaning |
|---|---|
| `implemented_test_mode` | ← **we are here.** Code and tests pass; no provider account touched. |
| `manual_setup_required` | Owner completes: Payhip product, Stripe Thailand KYC + bank, Payhip↔Stripe connection, test webhook registration, secrets in `.env`. |
| `controlled_transaction_required` | One approved test transaction proves: delivery, Payhip receipt, Stripe financial match, refund observation, balance transaction, payout reconciliation. |
| `live_activation_blocked` | Live mode stays refused until that proof is recorded, incidents are zero, product/account ids match, and Bui gives a fresh explicit authorisation. |

The controlled transaction must record provider ids, timestamps, integer
amounts and currency, delivery status and reconciliation status. It must **not**
record the buyer's email, name, address, card data, webhook secret, or
signature.

## Known gaps, stated plainly

- **Fees are unknown.** Stripe's fee lives in balance transactions, which needs
  an authorised read-only API source we do not have yet. Until then
  `stripe_fee_minor` is `null` and `contribution_minor` is `null`.
- **Payouts cannot reconcile.** They stop at `pending_reconciliation` with
  `balance_transaction_source_not_authorized` for the same reason. An amount
  that happens to match is not proof.
- **Attribution is unknown.** We do not yet know whether a click id survives
  Payhip checkout, so no sale is credited to a campaign. The summary says
  `unknown` rather than showing a misleading zero.

---

## What is automated now (22 Aug 2026) — and the three things that are not

Run the readiness check any time; it prints booleans and reason codes, never values:

```bash
cd /root/libra && python3 scripts/commerce_setup_check.py
```

| Step | Who | How |
|---|---|---|
| Payhip account (email + password) | **human, once** | sign up at payhip.com, then put `PAYHIP_EMAIL` / `PAYHIP_PASSWORD` in `.env` |
| Stripe account + KYC + Thai bank | **human, once** | identity documents and 2FA cannot be delegated |
| Payhip → Connect Stripe | **human, once** | Payhip: Account → Settings → Payment Details → Connect (OAuth + 2FA); then set `PAYHIP_STRIPE_CONNECTED=1` in `.env` |
| Stripe TEST secret key + account id | human copies once | `STRIPE_SECRET_KEY_TEST`, `STRIPE_EXPECTED_ACCOUNT_TEST` in `.env` |
| Stripe webhook endpoint + signing secret | **auto** | `python3 scripts/commerce_setup_check.py --stripe` (idempotent; writes `STRIPE_WEBHOOK_SECRET_TEST` into `.env` itself) |
| Payhip webhook URL | **auto** (browser) | `payhip_admin.set_webhook(...)` — runs inside the publish flow once credentials exist |
| Product creation on Payhip | **auto** (browser) | `python3 scripts/payhip_publish.py --slug SLUG --price-minor 1290 --currency EUR --execute` |
| Buyer bundle (PDF + EPUB + README) | auto | built by the publish flow; never includes working files |
| Product page with tracked link | auto | `/libra/growth/products/<slug>` appears as soon as the product is recorded live |
| Ingest → reconcile → report | auto | webhooks + cron every 30 min + daily Telegram digest 09:35 |
| Growth decision | auto | zero paid spend, stops on any money incident |

Payhip's public API covers coupons and license keys only (checked at
payhip.com/api-reference on 22 Aug 2026) — there is no endpoint to create a
product or register a webhook, which is why those two steps go through a
browser with before/after screenshots, exactly like the KDP uploader.

**First real login:** run `python3 scripts/payhip_publish.py --inspect` once. It
logs in, opens the new-product form, and dumps the real field names so
`payhip_admin.SELECTORS` can be corrected before the first `--execute`. Do not
skip this: the selectors were written without a live account to test against.

### Order of operations for Bui

1. Payhip sign-up → `.env` credentials
2. Stripe sign-up + KYC + bank → copy the TEST key and `acct_…` id into `.env`
3. Payhip → Connect Stripe → `PAYHIP_STRIPE_CONNECTED=1`
4. `python3 scripts/commerce_setup_check.py --stripe` → everything should read `ok`
5. `python3 scripts/payhip_publish.py --inspect` → fix selectors if needed
6. `python3 scripts/payhip_publish.py --slug aquarelle-botanique-debutants-fr --price-minor 1290 --currency EUR --execute`
7. One controlled test purchase (Stripe test card) → watch the digest and `/api/commerce/summary`
8. Only then: live keys, with a fresh explicit authorisation
