# Libra Payhip And Stripe Automation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fail-closed, test-mode commerce lane that tracks Payhip acquisition, observes Payhip orders, verifies money through Stripe, reconciles refunds/fees/payouts, and feeds honest zero-budget growth decisions into Libra.

**Architecture:** Keep provider receipt, normalization, reconciliation, and read models separate. Public FastAPI routes durably store small sanitized inbox records; Payhip events remain operational observations, while only Stripe-verified events can establish revenue. Extend the existing SQLite ledger and Content Hub without changing current KDP financial logic or importing any KDP mutation module.

**Tech Stack:** Python 3.12, FastAPI, SQLite, official Stripe Python SDK, pytest, systemd, existing Libra Content Hub.

## Global Constraints

- Work only in `/root/libra`; before edits run `ai-work status` and respect the active owner.
- Run pytest only from `/root/libra` and always give an explicit test path. Never run bare `pytest` from `/root`.
- Follow strict TDD for every production behavior: write one failing test, observe the intended failure, implement only enough to pass, then run focused regressions.
- Test mode only. `LIBRA_COMMERCE_MODE=test` is required and any missing, malformed, live-mode, or wrong-account configuration fails closed.
- No live Payhip/Stripe calls, webhook registration, product creation, purchase, refund, payout change, cron activation, or credential mutation during implementation.
- KDP remains passive/read-only. Commerce modules must not import or invoke `kdp_upload`, `kdp_finish_publish`, `scripts.kdp_action_executor`, price, promotion, metadata, or other KDP mutation code.
- Product creation/replacement in Payhip remains a one-time manual task; do not claim Payhip product publishing automation.
- Public callback URLs remain under `/libra/api/webhooks/...`; do not add a top-level nginx `location /api/`.
- Stripe is the financial source of truth. A Payhip-only event must never create verified revenue.
- Verify Stripe against exact raw bytes, `Stripe-Signature`, endpoint secret, five-minute tolerance, expected Stripe account, and expected test mode.
- Payhip callback authentication is a constant-time comparison against a high-entropy secret embedded in the registered callback path. This proves possession of the callback URL only; all Payhip events remain `unverified` financially.
- Webhook bodies are capped at 256 KiB and must be durably recorded before a successful response.
- Duplicate provider ID plus identical content is a no-op. The same ID with different content creates a critical conflict and performs no projection mutation.
- Store money as integer minor units in the original uppercase ISO currency. Never use floating point for commerce money.
- Do not store or log card data, webhook secrets/signatures, raw customer emails, names, addresses, IP addresses, or user agents.
- Orders, refunds, fees, and payouts are separate facts. A payout is settlement, never revenue.
- Cross-currency totals remain separated; conversion is excluded until a verified FX rate, source, and timestamp are available.
- Campaign-to-sale attribution stays `unknown` until a controlled transaction proves Payhip returns the opaque click ID.
- Refund initiation, disputes, OTP, CAPTCHA, KYC, account review, bank setup, and webhook registration return or remain `manual_required`.
- Zero paid spend is hard-coded for this lane.
- Before any claim of completion, use the verification-before-completion skill, run focused tests plus `pytest tests/ -q`, and inspect the diff.

---

## File Map

- Create `settings.py`: one source of truth for `.env`, strict commerce configuration, redacted diagnostics.
- Modify `content_hub.py`: accept an injected tracking secret and separate Payhip destination allowlist; emit `payhip_outbound` with opaque click ID.
- Modify `business_ledger.py`: append-only commerce inbox/conflicts and commerce projection schema.
- Create `commerce_ledger.py`: transactional inbox, projection, reconciliation, and read-model functions.
- Create `stripe_webhook.py`: official-SDK raw-body verification and Stripe event normalization.
- Create `payhip_webhook.py`: secret-path verification, strict parsing, PII-free Payhip normalization.
- Create `commerce_reconciliation.py`: order/refund/fee/payout state transitions and incident creation.
- Modify `app.py`: public provider callback routes and authenticated commerce read API.
- Create `commerce_reporting.py`: currency-separated revenue/refund/fee/payout summaries.
- Create `commerce_growth.py`: deterministic zero-budget decision rules.
- Create `scripts/libra_commerce_reconcile.py`: test-mode replay/reconciliation CLI with no provider network calls.
- Modify `requirements.txt`: add a pinned compatible Stripe SDK after validating installation in the project environment.
- Create `tests/fixtures/commerce/*.json`: synthetic, PII-free provider fixtures.
- Create focused tests listed in each task.
- Create `docs/runbooks/libra-commerce-test-mode.md`: setup, secret rotation, replay, proof, and activation gates.

---

### Task 1: Unified Fail-Closed Commerce Settings

**Files:**
- Create: `settings.py`
- Create: `tests/test_settings.py`
- Modify: `app.py:25-48`

**Interfaces:**
- Produces: `load_env_file(path: Path) -> dict[str, str]`.
- Produces: `CommerceSettings.from_sources(env: Mapping[str, str]) -> CommerceSettings`.
- Produces immutable fields `mode`, `stripe_webhook_secret`, `stripe_expected_account`, `payhip_webhook_token`, `payhip_allowed_hosts`, `payhip_product_ids`, and `max_webhook_bytes`.
- Produces: `CommerceSettings.readiness() -> dict` containing booleans and stable reason codes, never secret values.

- [ ] **Step 1: Write failing settings tests**

```python
from pathlib import Path

import pytest

from settings import CommerceConfigError, CommerceSettings, load_env_file


def test_commerce_settings_require_explicit_test_mode_and_secrets():
    with pytest.raises(CommerceConfigError, match="commerce_mode_missing"):
        CommerceSettings.from_sources({})

    settings = CommerceSettings.from_sources({
        "LIBRA_COMMERCE_MODE": "test",
        "STRIPE_WEBHOOK_SECRET_TEST": "whsec_test_fixture",
        "STRIPE_EXPECTED_ACCOUNT_TEST": "acct_test_fixture",
        "PAYHIP_WEBHOOK_TOKEN_TEST": "p" * 48,
        "PAYHIP_ALLOWED_HOSTS": "payhip.com,www.payhip.com",
        "PAYHIP_PRODUCT_IDS_TEST": "kit-fr-test",
    })
    assert settings.mode == "test"
    assert settings.max_webhook_bytes == 262144
    assert "whsec_test_fixture" not in repr(settings)


def test_env_file_parser_does_not_write_process_environment(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text("LIBRA_COMMERCE_MODE=test\n", encoding="utf-8")
    monkeypatch.delenv("LIBRA_COMMERCE_MODE", raising=False)
    assert load_env_file(path) == {"LIBRA_COMMERCE_MODE": "test"}
    assert "LIBRA_COMMERCE_MODE" not in __import__("os").environ
```

- [ ] **Step 2: Run the tests and confirm RED**

Run: `cd /root/libra && pytest tests/test_settings.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'settings'`.

- [ ] **Step 3: Implement the minimal immutable settings object**

```python
@dataclass(frozen=True, repr=False)
class CommerceSettings:
    mode: str
    stripe_webhook_secret: str
    stripe_expected_account: str
    payhip_webhook_token: str
    payhip_allowed_hosts: frozenset[str]
    payhip_product_ids: frozenset[str]
    max_webhook_bytes: int = 256 * 1024

    @classmethod
    def from_sources(cls, env: Mapping[str, str]) -> "CommerceSettings":
        mode = env.get("LIBRA_COMMERCE_MODE", "")
        if mode != "test":
            raise CommerceConfigError("commerce_mode_missing_or_not_test")
        required = {
            "stripe_webhook_secret": env.get("STRIPE_WEBHOOK_SECRET_TEST", ""),
            "stripe_expected_account": env.get("STRIPE_EXPECTED_ACCOUNT_TEST", ""),
            "payhip_webhook_token": env.get("PAYHIP_WEBHOOK_TOKEN_TEST", ""),
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise CommerceConfigError("missing:" + ",".join(sorted(missing)))
        return cls(
            mode=mode,
            payhip_allowed_hosts=_csv_hosts(env.get("PAYHIP_ALLOWED_HOSTS", "")),
            payhip_product_ids=_csv_values(env.get("PAYHIP_PRODUCT_IDS_TEST", "")),
            **required,
        )

    def __repr__(self) -> str:
        return "CommerceSettings(mode='test', secrets='<redacted>')"
```

Keep existing non-commerce settings behavior unchanged. Replace the ad hoc `.env` parser in `app.py` with `load_env_file(Path(__file__).parent / ".env")`, but do not require commerce configuration at import time; construct it inside commerce routes so a missing commerce setup returns a clean `503` without breaking the existing dashboard.

- [ ] **Step 4: Run settings and existing route regressions**

Run: `cd /root/libra && pytest tests/test_settings.py tests/test_growth_routes.py tests/test_profit_api.py -q`

Expected: PASS with no environment leakage.

- [ ] **Step 5: Commit**

```bash
git add settings.py app.py tests/test_settings.py
git commit -m "feat: add fail-closed commerce settings"
```

---

### Task 2: Commerce Ledger Schema And Immutable Inbox

**Files:**
- Modify: `business_ledger.py`
- Create: `commerce_ledger.py`
- Create: `tests/test_commerce_ledger.py`

**Interfaces:**
- Consumes: existing `business_ledger._canonical`, `_hash`, and `init_ledger` conventions.
- Produces: `record_provider_event(path: Path, event: dict) -> dict` with status `inserted`, `duplicate`, or `conflict`.
- Produces: `mark_provider_event(path, provider, event_id, status, *, error_code=None) -> None`.
- Produces: `commerce_event(path, provider, event_id) -> dict | None`.
- Produces schema tables `commerce_events`, `commerce_event_conflicts`, `commerce_products`, `commerce_orders`, `commerce_refunds`, `stripe_balance_transactions`, `commerce_payouts`, `commerce_payout_items`, and `commerce_incidents`.

- [ ] **Step 1: Write failing schema, replay, and conflict tests**

```python
import sqlite3

from business_ledger import init_ledger
from commerce_ledger import commerce_event, record_provider_event


def _event(event_id="evt_1", payload_hash="abc"):
    return {
        "provider": "stripe", "event_id": event_id,
        "event_type": "payment_intent.succeeded",
        "occurred_at": "2026-08-21T10:00:00+00:00",
        "received_at": "2026-08-21T10:00:01+00:00",
        "mode": "test", "verification_state": "verified",
        "payload_hash": payload_hash, "sanitized_payload": {"id": event_id},
    }


def test_provider_event_replay_is_idempotent_and_conflict_is_immutable(tmp_path):
    db = tmp_path / "ledger.db"
    assert record_provider_event(db, _event())["status"] == "inserted"
    assert record_provider_event(db, _event())["status"] == "duplicate"
    assert record_provider_event(db, _event(payload_hash="changed"))["status"] == "conflict"
    assert commerce_event(db, "stripe", "evt_1")["payload_hash"] == "abc"
    with sqlite3.connect(db) as connection:
        assert connection.execute("SELECT COUNT(*) FROM commerce_event_conflicts").fetchone()[0] == 1


def test_commerce_money_columns_are_integer_and_currency_is_required(tmp_path):
    db = tmp_path / "ledger.db"
    init_ledger(db)
    with sqlite3.connect(db) as connection:
        sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='commerce_orders'"
        ).fetchone()[0]
    assert "gross_minor INTEGER" in sql
    assert "currency TEXT NOT NULL" in sql
```

- [ ] **Step 2: Run the focused tests and confirm RED**

Run: `cd /root/libra && pytest tests/test_commerce_ledger.py -q`

Expected: FAIL because `commerce_ledger` does not exist.

- [ ] **Step 3: Add schema and the transaction-safe inbox**

Use `BEGIN IMMEDIATE` for one writer. `commerce_events` stores only a sanitized JSON projection, never the raw provider body:

```sql
CREATE TABLE IF NOT EXISTS commerce_events (
  id INTEGER PRIMARY KEY,
  provider TEXT NOT NULL,
  event_id TEXT NOT NULL,
  event_type TEXT NOT NULL,
  occurred_at TEXT NOT NULL,
  received_at TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode IN ('test','live')),
  verification_state TEXT NOT NULL,
  payload_hash TEXT NOT NULL,
  processing_state TEXT NOT NULL DEFAULT 'received',
  error_code TEXT,
  sanitized_payload_json TEXT NOT NULL,
  UNIQUE(provider, event_id)
);
CREATE TABLE IF NOT EXISTS commerce_event_conflicts (
  id INTEGER PRIMARY KEY,
  provider TEXT NOT NULL,
  event_id TEXT NOT NULL,
  original_event_id INTEGER NOT NULL,
  conflicting_hash TEXT NOT NULL,
  recorded_at TEXT NOT NULL,
  UNIQUE(provider, event_id, conflicting_hash)
);
CREATE TABLE IF NOT EXISTS commerce_products (
  slug TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  provider_product_id TEXT NOT NULL,
  status TEXT NOT NULL,
  currency TEXT NOT NULL,
  price_minor INTEGER NOT NULL CHECK(price_minor >= 0),
  updated_at TEXT NOT NULL,
  UNIQUE(provider, provider_product_id)
);
CREATE TABLE IF NOT EXISTS commerce_orders (
  provider TEXT NOT NULL,
  provider_order_id TEXT NOT NULL,
  slug TEXT,
  status TEXT NOT NULL,
  currency TEXT NOT NULL,
  gross_minor INTEGER NOT NULL CHECK(gross_minor >= 0),
  discount_minor INTEGER NOT NULL DEFAULT 0 CHECK(discount_minor >= 0),
  tax_minor INTEGER NOT NULL DEFAULT 0 CHECK(tax_minor >= 0),
  payhip_fee_minor INTEGER,
  stripe_fee_minor INTEGER,
  net_minor INTEGER,
  provider_payment_id TEXT,
  customer_country TEXT,
  attribution_key TEXT,
  ordered_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(provider, provider_order_id)
);
CREATE TABLE IF NOT EXISTS commerce_refunds (
  provider TEXT NOT NULL,
  provider_refund_id TEXT NOT NULL,
  provider_order_id TEXT,
  provider_payment_id TEXT,
  amount_minor INTEGER NOT NULL CHECK(amount_minor > 0),
  currency TEXT NOT NULL,
  status TEXT NOT NULL,
  reason_code TEXT,
  occurred_at TEXT NOT NULL,
  PRIMARY KEY(provider, provider_refund_id)
);
CREATE TABLE IF NOT EXISTS stripe_balance_transactions (
  balance_transaction_id TEXT PRIMARY KEY,
  source_id TEXT,
  type TEXT NOT NULL,
  amount_minor INTEGER NOT NULL,
  fee_minor INTEGER NOT NULL,
  net_minor INTEGER NOT NULL,
  currency TEXT NOT NULL,
  available_on TEXT
);
CREATE TABLE IF NOT EXISTS commerce_payouts (
  provider_payout_id TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  currency TEXT NOT NULL,
  amount_minor INTEGER NOT NULL,
  arrival_date TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS commerce_payout_items (
  provider_payout_id TEXT NOT NULL,
  balance_transaction_id TEXT NOT NULL,
  PRIMARY KEY(provider_payout_id, balance_transaction_id)
);
CREATE TABLE IF NOT EXISTS commerce_incidents (
  incident_key TEXT PRIMARY KEY,
  opened_at TEXT NOT NULL,
  severity TEXT NOT NULL,
  scope TEXT NOT NULL,
  error_code TEXT NOT NULL,
  detail_json TEXT NOT NULL,
  resolved_at TEXT
);
```

On conflicting replay, insert the conflict and a `critical` incident in the same transaction; do not update the original event or any order projection.

- [ ] **Step 4: Run ledger regressions**

Run: `cd /root/libra && pytest tests/test_commerce_ledger.py tests/test_business_ledger.py tests/test_growth_ledger.py -q`

Expected: PASS and existing KDP ledger calculations remain unchanged.

- [ ] **Step 5: Commit**

```bash
git add business_ledger.py commerce_ledger.py tests/test_commerce_ledger.py
git commit -m "feat: add commerce event inbox and ledger schema"
```

---

### Task 3: Stripe Raw-Body Verification And Normalization

**Files:**
- Modify: `requirements.txt`
- Create: `stripe_webhook.py`
- Create: `tests/test_stripe_webhook.py`
- Create: `tests/fixtures/commerce/stripe_payment_intent_succeeded.json`
- Create: `tests/fixtures/commerce/stripe_refund_created.json`
- Create: `tests/fixtures/commerce/stripe_refund_updated.json`
- Create: `tests/fixtures/commerce/stripe_refund_failed.json`
- Create: `tests/fixtures/commerce/stripe_balance_available.json`
- Create: `tests/fixtures/commerce/stripe_payout_paid.json`

**Interfaces:**
- Consumes: `CommerceSettings` and exact `bytes` request body.
- Produces: `verify_stripe_event(raw_body: bytes, signature: str, settings, *, now: int) -> dict`.
- Produces: `normalize_stripe_event(event: Mapping) -> dict` with PII-free event and projection payloads.
- Raises: `StripeWebhookError(code)` where code is one of `body_too_large`, `signature_missing`, `signature_invalid`, `signature_stale`, `wrong_mode`, `wrong_account`, `unsupported_event`, or `malformed_event`.

- [ ] **Step 1: Pin the installed Stripe SDK version without making a network call**

Run: `cd /root/libra && python3 -c "import stripe; print(stripe.VERSION)"`

Expected: print the installed version. If import fails, stop and report `manual_required: stripe_sdk_not_installed`; package installation is a separate authorized environment change, not something to hide inside implementation.

If the installed version is `12.5.0`, add `stripe==12.5.0` to `requirements.txt`. If a different version is installed, use that exact numeric output in both `requirements.txt` and the test evidence; never use an unpinned `stripe` entry and never invent a version that was not imported successfully.

- [ ] **Step 2: Write failing raw-body and policy tests**

```python
import json
import time

import pytest
import stripe

from stripe_webhook import StripeWebhookError, verify_stripe_event


def _signature(raw: bytes, secret: str, timestamp: int) -> str:
    return stripe.WebhookSignature._compute_signature(
        f"{timestamp}.{raw.decode('utf-8')}", secret
    )


def test_stripe_verifies_exact_raw_body_and_expected_test_account(settings):
    raw = (FIXTURES / "stripe_payment_intent_succeeded.json").read_bytes()
    timestamp = int(time.time())
    header = f"t={timestamp},v1={_signature(raw, settings.stripe_webhook_secret, timestamp)}"
    event = verify_stripe_event(raw, header, settings, now=timestamp + 10)
    assert event["livemode"] is False
    assert event["account"] == settings.stripe_expected_account


def test_stripe_rejects_reserialized_or_stale_payload(settings):
    raw = (FIXTURES / "stripe_payment_intent_succeeded.json").read_bytes()
    changed = json.dumps(json.loads(raw), indent=2).encode()
    timestamp = int(time.time())
    header = f"t={timestamp},v1={_signature(raw, settings.stripe_webhook_secret, timestamp)}"
    with pytest.raises(StripeWebhookError, match="signature_invalid"):
        verify_stripe_event(changed, header, settings, now=timestamp + 10)
    with pytest.raises(StripeWebhookError, match="signature_stale"):
        verify_stripe_event(raw, header, settings, now=timestamp + 301)
```

Add separate tests for missing/invalid signature, `livemode=true`, wrong top-level `account`, unsupported type, malformed amount, all three refund lifecycle event types, and fixture sanitization. Synthetic fixtures contain no real emails, names, addresses, card fields, IPs, or user agents.

- [ ] **Step 3: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_stripe_webhook.py -q`

Expected: FAIL because `stripe_webhook` does not exist.

- [ ] **Step 4: Implement verification and the narrow event allowlist**

```python
STRIPE_EVENT_TYPES = frozenset({
    "payment_intent.succeeded",
    "refund.created",
    "refund.updated",
    "refund.failed",
    "charge.dispute.created",
    "balance.available",
    "payout.paid",
    "payout.failed",
})


def verify_stripe_event(raw_body, signature, settings, *, now):
    if len(raw_body) > settings.max_webhook_bytes:
        raise StripeWebhookError("body_too_large")
    if not signature:
        raise StripeWebhookError("signature_missing")
    try:
        event = stripe.Webhook.construct_event(
            raw_body, signature, settings.stripe_webhook_secret,
            tolerance=300,
        )
    except stripe.error.SignatureVerificationError as exc:
        code = "signature_stale" if abs(now - _signature_timestamp(signature)) > 300 else "signature_invalid"
        raise StripeWebhookError(code) from exc
    if event.get("livemode") is not False:
        raise StripeWebhookError("wrong_mode")
    if event.get("account") != settings.stripe_expected_account:
        raise StripeWebhookError("wrong_account")
    if event.get("type") not in STRIPE_EVENT_TYPES:
        raise StripeWebhookError("unsupported_event")
    return event
```

Do not persist the Stripe object verbatim. `normalize_stripe_event` copies only provider IDs, event/object types, integer amounts, currency, timestamps, payment/order linkage IDs, fee/balance/payout IDs, and dispute status. Assert `isinstance(amount, int)` and normalized currency matches `[A-Z]{3}`.

- [ ] **Step 5: Run focused and dependency tests**

Run: `cd /root/libra && pytest tests/test_stripe_webhook.py tests/test_commerce_ledger.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add requirements.txt stripe_webhook.py tests/test_stripe_webhook.py tests/fixtures/commerce/stripe_*.json
git commit -m "feat: verify and normalize Stripe test webhooks"
```

---

### Task 4: Payhip Operational Webhook Normalization

**Files:**
- Create: `payhip_webhook.py`
- Create: `tests/test_payhip_webhook.py`
- Create: `tests/fixtures/commerce/payhip_paid.json`
- Create: `tests/fixtures/commerce/payhip_refunded.json`

**Interfaces:**
- Consumes: raw body, callback path token, `CommerceSettings`.
- Produces: `verify_payhip_callback_token(received: str, expected: str) -> None` using `secrets.compare_digest`.
- Produces: `normalize_payhip_event(raw_body: bytes, settings, *, received_at: str) -> dict`.
- Financial verification state is always `unverified`; no function in this module can return `verified`.

- [ ] **Step 1: Write failing authentication, product, and PII tests**

```python
import json

import pytest

from payhip_webhook import PayhipWebhookError, normalize_payhip_event, verify_payhip_callback_token


def test_payhip_token_is_required_but_event_remains_financially_unverified(settings):
    verify_payhip_callback_token(settings.payhip_webhook_token, settings.payhip_webhook_token)
    raw = (FIXTURES / "payhip_paid.json").read_bytes()
    event = normalize_payhip_event(raw, settings, received_at="2026-08-21T10:00:01+00:00")
    assert event["verification_state"] == "unverified"
    assert event["sanitized_payload"]["provider_product_id"] == "kit-fr-test"


def test_payhip_rejects_wrong_token_unknown_product_and_strips_pii(settings):
    with pytest.raises(PayhipWebhookError, match="callback_token_invalid"):
        verify_payhip_callback_token("wrong", settings.payhip_webhook_token)
    payload = json.loads((FIXTURES / "payhip_paid.json").read_text())
    payload["product_id"] = "unknown-product"
    with pytest.raises(PayhipWebhookError, match="unknown_product"):
        normalize_payhip_event(json.dumps(payload).encode(), settings, received_at="2026-08-21T10:00:01+00:00")
    assert "email" not in json.dumps(normalize_payhip_event(
        (FIXTURES / "payhip_paid.json").read_bytes(), settings,
        received_at="2026-08-21T10:00:01+00:00",
    )).lower()
```

Add tests for malformed JSON, oversize body, unsupported event, missing stable sale/refund ID, non-integer-normalizable amount, and lowercase currency normalization.

- [ ] **Step 2: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_payhip_webhook.py -q`

Expected: FAIL because `payhip_webhook` does not exist.

- [ ] **Step 3: Implement strict operational normalization**

Allow only `paid`, `refunded`, `subscription.created`, and `subscription.deleted`. Normalize only stable IDs, product ID, amount minor, currency, status, timestamp, country code, and Stripe payment linkage if actually present in the fixture contract. Omit email/name/address fields rather than hashing them.

```python
def verify_payhip_callback_token(received: str, expected: str) -> None:
    if not received or not expected or not secrets.compare_digest(received, expected):
        raise PayhipWebhookError("callback_token_invalid")


def normalize_payhip_event(raw_body, settings, *, received_at):
    if len(raw_body) > settings.max_webhook_bytes:
        raise PayhipWebhookError("body_too_large")
    payload = _strict_json_object(raw_body)
    event_type = _required_event_type(payload)
    product_id = _required_product_id(payload)
    if product_id not in settings.payhip_product_ids:
        raise PayhipWebhookError("unknown_product")
    return {
        "provider": "payhip",
        "event_id": _stable_event_id(payload, event_type),
        "event_type": event_type,
        "occurred_at": _required_timestamp(payload),
        "received_at": received_at,
        "mode": "test",
        "verification_state": "unverified",
        "payload_hash": hashlib.sha256(raw_body).hexdigest(),
        "sanitized_payload": _safe_projection(payload),
    }
```

If real configured-account fixtures differ from these synthetic fixtures, adapt the parser only after saving a manually redacted fixture and adding a failing test. Never loosen validation to accept arbitrary shapes.

- [ ] **Step 4: Run focused tests**

Run: `cd /root/libra && pytest tests/test_payhip_webhook.py tests/test_commerce_ledger.py -q`

Expected: PASS; all Payhip normalized events remain financially unverified.

- [ ] **Step 5: Commit**

```bash
git add payhip_webhook.py tests/test_payhip_webhook.py tests/fixtures/commerce/payhip_*.json
git commit -m "feat: ingest Payhip operational webhooks safely"
```

---

### Task 5: Transactional Order, Refund, Fee, And Payout Reconciliation

**Files:**
- Create: `commerce_reconciliation.py`
- Modify: `commerce_ledger.py`
- Create: `tests/test_commerce_reconciliation.py`

**Interfaces:**
- Consumes normalized provider event dicts from Tasks 3 and 4.
- Trust boundary: Task 3 has already verified exact raw bytes, signature age, expected Stripe account, and test mode. Task 5 requires `provider='stripe'`, `verification_state='verified'`, and `mode='test'` but does not re-check the Stripe account unless Task 2 explicitly persists a `verification_scope` field containing the checked account ID.
- Produces: `reconcile_event(path: Path, provider: str, event_id: str) -> dict`.
- Produces: `retry_pending(path: Path, *, limit: int = 100) -> dict`.
- Produces order states `observed`, `payment_pending`, `paid_verified`, `partially_refunded`, `refunded`, `disputed`, and `reconciliation_failed`.
- Produces refund projection states `created`, `updated`, `succeeded`, and `failed`; repeated events for one refund ID advance the same projection instead of creating a second refund.
- Produces payout states `observed`, `pending_reconciliation`, `items_matched`, `reconciled`, and `mismatch`. In this implementation phase payouts cannot advance beyond `pending_reconciliation` because there is no owner-authorized read-only balance-transaction source.

- [ ] **Step 1: Write failing truth-model tests**

```python
def test_payhip_paid_cannot_create_verified_revenue_until_stripe_match(tmp_path):
    db = tmp_path / "ledger.db"
    ingest_normalized(db, payhip_paid(payment_id="pi_test_1", gross_minor=1290))
    assert commerce_summary(db)["EUR"]["verified_gross_minor"] == 0
    assert order(db, "payhip", "sale_test_1")["status"] == "payment_pending"

    ingest_normalized(db, stripe_payment(payment_id="pi_test_1", amount_minor=1290))
    assert order(db, "payhip", "sale_test_1")["status"] == "paid_verified"
    assert commerce_summary(db)["EUR"]["verified_gross_minor"] == 1290


def test_refund_same_id_reverses_revenue_only_after_succeeded(tmp_path):
    db = tmp_path / "ledger.db"
    seed_verified_order(db, gross_minor=1290, payment_id="pi_test_1")
    ingest_normalized(db, stripe_refund("re_test_1", "pi_test_1", 400, status="pending"))
    assert commerce_summary(db)["EUR"]["refunded_minor"] == 0
    ingest_normalized(db, stripe_refund("re_test_1", "pi_test_1", 400, status="succeeded"))
    ingest_normalized(db, stripe_payout("po_test_1", 790))
    summary = commerce_summary(db)["EUR"]
    assert summary["verified_gross_minor"] == 1290
    assert summary["refunded_minor"] == 400
    assert summary["verified_net_sales_minor"] == 890
    assert summary["payout_minor"] == 790


def test_failed_refund_same_id_does_not_reverse_revenue(tmp_path):
    db = tmp_path / "ledger.db"
    seed_verified_order(db, gross_minor=1290, payment_id="pi_test_1")
    ingest_normalized(db, stripe_refund("re_test_1", "pi_test_1", 400, status="pending"))
    ingest_normalized(db, stripe_refund("re_test_1", "pi_test_1", 400, status="failed"))
    summary = commerce_summary(db)["EUR"]
    assert summary["refunded_minor"] == 0
    assert summary["verified_net_sales_minor"] == 1290


def test_balance_available_and_payout_cannot_false_reconcile_without_source(tmp_path):
    db = tmp_path / "ledger.db"
    seed_verified_order(db, gross_minor=1290, payment_id="pi_test_1")
    ingest_normalized(db, stripe_balance_available(currency="EUR", amount_minor=1240))
    ingest_normalized(db, stripe_payout("po_test_1", 1240))
    payout = commerce_payout(db, "po_test_1")
    assert payout["status"] == "pending_reconciliation"
    assert payout["error_code"] == "balance_transaction_source_not_authorized"
    assert commerce_summary(db)["EUR"]["stripe_fee_minor"] is None
    assert commerce_summary(db)["EUR"]["reconciled_payout_minor"] == 0
```

Add individual tests for refund-before-payment; duplicate lifecycle events; `created -> updated -> succeeded`; `created -> failed`; a late contradictory transition after terminal `succeeded` or `failed` opening an incident; full refund; succeeded refund over gross; dispute opening an incident; separate EUR/USD totals; trusted Task 3 verification without a second account lookup; optional persisted `verification_scope` mismatch if that field exists; and a simulated exception after inbox persistence followed by successful retry. Tests for fee and payout logic must prove that `balance.available` alone never manufactures a balance transaction, Stripe fee, payout item, `items_matched`, or `reconciled` state.

- [ ] **Step 2: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_commerce_reconciliation.py -q`

Expected: FAIL because reconciliation functions do not exist.

- [ ] **Step 3: Implement one atomic projection transaction per event**

```python
def reconcile_event(path, provider, event_id):
    with sqlite3.connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        event = _load_unprocessed_event(connection, provider, event_id)
        if event is None:
            return {"status": "already_reconciled"}
        result = _apply_event(connection, event)
        connection.execute(
            "UPDATE commerce_events SET processing_state=?, error_code=? "
            "WHERE provider=? AND event_id=?",
            (result.state, result.error_code, provider, event_id),
        )
        connection.commit()
        return result.as_dict()
```

Rules in `_apply_event`:

- Payhip `paid`: upsert `observed/payment_pending`; never `paid_verified` by itself.
- Stripe payment: accept only an event already marked `verification_state='verified'` and `mode='test'` by Task 3, then match exact payment ID, currency, and amount. Do not require or invent a second account check after this trusted boundary. Only re-check account when an explicit persisted `verification_scope` exists; a mismatching persisted scope opens an incident.
- Stripe refund lifecycle: upsert one projection keyed by the same Stripe refund ID while retaining every provider event in the immutable inbox. Map provider lifecycle into `created`, `updated`, `succeeded`, or `failed`; permit monotonic nonterminal transitions and make `succeeded`/`failed` terminal. A contradictory terminal replay opens an incident and changes neither order nor refund projection.
- Revenue reversal: aggregate only refunds whose current projection status is `succeeded`. `created`, `updated`, `pending`, `requires_action`, and `failed` reverse zero revenue. A succeeded aggregate must never exceed verified gross.
- `balance.available`: record only an availability observation. It contains no itemized balance transactions and must not create `stripe_balance_transactions`, infer Stripe fees/net, or attach payout items.
- Fee/net reconciliation: remain incomplete (`stripe_fee_minor=NULL`, `net_minor=NULL`) until a separately owner-authorized read-only source supplies exact balance-transaction records linked by provider IDs.
- Payout: store the settlement observation, then set `pending_reconciliation` with `balance_transaction_source_not_authorized`. Do not emit `items_matched`, `reconciled`, or `mismatch` from amount coincidence. Once a future authorized source exists, reconciliation may compare exact linked balance-transaction IDs, currency, and net sum; that source is outside this task.
- Dispute: set order `disputed`, open critical incident, return `manual_required`.
- Missing counterpart: set event `pending_reconciliation`, not failure; `retry_pending` revisits it after later events.

Do not emit growth revenue evidence inside a partially applied transaction. Emit it only after the order first transitions to `paid_verified`, with source key `commerce-sale:<provider_payment_id>` so retries are idempotent.

- [ ] **Step 4: Run focused and ledger regressions**

Run: `cd /root/libra && pytest tests/test_commerce_reconciliation.py tests/test_commerce_ledger.py tests/test_business_ledger.py -q`

Expected: PASS; only succeeded refunds reverse revenue, `balance.available` remains observational, and payouts cannot falsely reconcile without an authorized itemized source.

- [ ] **Step 5: Commit**

```bash
git add commerce_reconciliation.py commerce_ledger.py tests/test_commerce_reconciliation.py
git commit -m "feat: reconcile commerce payments refunds and payouts"
```

---

### Task 6: Public Test-Mode Webhook Routes

**Files:**
- Modify: `app.py` after the existing growth routes
- Create: `tests/test_commerce_routes.py`

**Interfaces:**
- Adds backend routes `POST /api/webhooks/stripe` and `POST /api/webhooks/payhip/{callback_token}`.
- External nginx URLs are `/libra/api/webhooks/stripe` and `/libra/api/webhooks/payhip/{callback_token}`.
- Adds authenticated `GET /api/commerce/summary` using existing `check_auth`.
- Consumes Tasks 1-5 and `PROFIT_LEDGER_FILE`.

- [ ] **Step 1: Write failing HTTP tests**

```python
def test_stripe_webhook_is_public_but_requires_valid_signature(client, signed_stripe_request):
    response = client.post(
        "/api/webhooks/stripe",
        content=signed_stripe_request.body,
        headers={"Stripe-Signature": signed_stripe_request.signature},
    )
    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}


def test_webhooks_reject_oversized_and_bad_auth_without_storing_event(client, ledger, settings):
    response = client.post(
        "/api/webhooks/payhip/wrong",
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 404
    response = client.post(
        "/api/webhooks/stripe",
        content=b"x" * (settings.max_webhook_bytes + 1),
        headers={"Stripe-Signature": "invalid"},
    )
    assert response.status_code == 413
    assert commerce_event_count(ledger) == 0
```

Add tests for malformed JSON (`400`), stale Stripe signature (`400`), wrong account/mode (`403`), unsupported event (`202` quarantined), identical replay (`200`), conflicting replay (`409`), crash after inbox persistence (`500` then retry without duplicate), summary auth (`401`), and response/log bodies containing no secret, signature, email, or raw payload.

- [ ] **Step 2: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_commerce_routes.py -q`

Expected: FAIL with route `404`.

- [ ] **Step 3: Implement thin routes that preserve raw bytes**

```python
@app.post("/api/webhooks/stripe")
async def stripe_webhook_route(request: Request):
    settings = commerce_settings()
    raw_body = await request.body()
    if len(raw_body) > settings.max_webhook_bytes:
        raise HTTPException(status_code=413, detail="body_too_large")
    normalized = normalize_stripe_event(verify_stripe_event(
        raw_body,
        request.headers.get("Stripe-Signature", ""),
        settings,
        now=int(time.time()),
    ))
    receipt = record_provider_event(PROFIT_LEDGER_FILE, normalized)
    return _commerce_receipt_response(receipt, normalized)


@app.post("/api/webhooks/payhip/{callback_token}")
async def payhip_webhook_route(callback_token: str, request: Request):
    settings = commerce_settings()
    verify_payhip_callback_token(callback_token, settings.payhip_webhook_token)
    normalized = normalize_payhip_event(
        await request.body(), settings, received_at=datetime.now(timezone.utc).isoformat()
    )
    receipt = record_provider_event(PROFIT_LEDGER_FILE, normalized)
    return _commerce_receipt_response(receipt, normalized)
```

Return a generic `404` for an invalid Payhip callback token so the response does not reveal endpoint validity. Never pass request headers, client IP, or raw body to logs. For accepted events, commit the inbox event before reconciliation; a projection failure returns `500` but leaves the inbox record retryable.

- [ ] **Step 4: Run route and application regressions**

Run: `cd /root/libra && pytest tests/test_commerce_routes.py tests/test_growth_routes.py tests/test_profit_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_commerce_routes.py
git commit -m "feat: expose fail-closed commerce webhook routes"
```

---

### Task 7: Payhip Content Hub Tracking Without False Attribution

**Files:**
- Modify: `content_hub.py`
- Modify: `app.py`
- Modify: `business_ledger.py` only if `hub_events` needs a nullable opaque `click_id` migration
- Create: `tests/test_payhip_tracking.py`

**Interfaces:**
- Produces: `make_tracking_token(slug, campaign, destination, *, destination_kind, secret) -> str` while retaining backward-compatible Amazon calls.
- Produces: `build_outbound_event(..., event_kind="payhip_outbound", click_id=...) -> dict`.
- Adds `GET /growth/products/{slug}` and uses configured Payhip hosts/product URL only.

- [ ] **Step 1: Write failing allowlist and attribution tests**

```python
def test_payhip_destination_uses_separate_allowlist_and_click_event(client, ledger):
    response = client.get("/growth/products/kit-autonomie-numerique-fr")
    assert response.status_code == 200
    token = extract_tracking_token(response.text)
    outbound = client.get(f"/growth/out/{token}", follow_redirects=False)
    assert outbound.status_code == 307
    row = latest_hub_event(ledger)
    assert row["event_kind"] == "payhip_outbound"
    assert len(row["payload"]["click_id"]) >= 32
    assert row["payload"]["attribution_status"] == "unknown"


@pytest.mark.parametrize("url", [
    "http://payhip.com/b/test",
    "https://payhip.com.evil.example/b/test",
    "https://payhip.com@evil.example/b/test",
    "https://evil.example/b/test",
])
def test_payhip_destination_bypass_is_rejected(url, settings):
    with pytest.raises(InvalidDestinationError):
        make_tracking_token("kit", "organic", url, destination_kind="payhip", settings=settings)
```

Add a regression proving every approved Amazon marketplace still works and still records `amazon_outbound`.

- [ ] **Step 2: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_payhip_tracking.py -q`

Expected: FAIL because Payhip destinations are not approved.

- [ ] **Step 3: Generalize validation without weakening Amazon checks**

Use an explicit `destination_kind` and separate allowlists. Parse with `urlsplit`, require HTTPS, reject username/password/userinfo, require exact lowercased hostname membership, and reject fragments. Generate click ID with `secrets.token_hex(16)` before token minting and include it in the signed payload and hub event.

Do not append the click ID to the Payhip destination until a controlled test proves the exact query parameter survives checkout and is returned in a provider event. The product page and summary must display attribution as `unknown`, not `unattributed` or zero.

- [ ] **Step 4: Run tracking regressions**

Run: `cd /root/libra && pytest tests/test_payhip_tracking.py tests/test_content_hub.py tests/test_growth_routes.py -q`

Expected: PASS for both Payhip and existing Amazon tracking.

- [ ] **Step 5: Commit**

```bash
git add content_hub.py app.py business_ledger.py tests/test_payhip_tracking.py
git commit -m "feat: add safe Payhip outbound tracking"
```

---

### Task 8: Currency-Separated Commerce Reporting

**Files:**
- Create: `commerce_reporting.py`
- Modify: `app.py`
- Create: `tests/test_commerce_reporting.py`
- Modify: `tests/test_commerce_routes.py`

**Interfaces:**
- Produces: `commerce_summary(path: Path, *, start=None, end=None) -> dict`.
- Produces per-currency fields `verified_gross_minor`, `refunded_minor`, `verified_net_sales_minor`, `payhip_fee_minor`, `stripe_fee_minor`, `contribution_minor`, `payout_minor`, and completeness flags.
- Adds authenticated `GET /api/commerce/summary` response with `generated_at`, `mode`, `by_currency`, incidents, and explicit attribution state.

- [ ] **Step 1: Write failing reporting tests**

```python
def test_summary_separates_currency_and_never_counts_payout_as_revenue(tmp_path):
    db = tmp_path / "ledger.db"
    seed_verified_order(db, currency="EUR", gross_minor=1290, stripe_fee_minor=50)
    seed_verified_order(db, currency="USD", gross_minor=900, stripe_fee_minor=40)
    seed_payout(db, currency="EUR", amount_minor=1240)
    result = commerce_summary(db)
    assert result["by_currency"]["EUR"]["verified_gross_minor"] == 1290
    assert result["by_currency"]["USD"]["verified_gross_minor"] == 900
    assert "converted_total" not in result
    assert result["by_currency"]["EUR"]["payout_minor"] == 1240


def test_missing_payhip_fee_is_unknown_not_zero(tmp_path):
    db = tmp_path / "ledger.db"
    seed_verified_order(db, gross_minor=1290, payhip_fee_minor=None)
    eur = commerce_summary(db)["by_currency"]["EUR"]
    assert eur["payhip_fee_complete"] is False
    assert eur["contribution_minor"] is None
```

Add tests for partial/full refunds, no orders, open disputes, payout mismatch, date bounds, product totals, and `attribution.status == "unknown"` when no round-trip proof exists.

- [ ] **Step 2: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_commerce_reporting.py -q`

Expected: FAIL because `commerce_reporting` does not exist.

- [ ] **Step 3: Implement SQL aggregation with completeness flags**

Query only `paid_verified`, `partially_refunded`, and `refunded` orders for gross revenue. Sum only verified refunds. Keep absent fees as `None`; do not coerce them to zero. Report payouts from `commerce_payouts` separately and include `reconciled_payout_minor` only for payout state `reconciled`.

API response example:

```json
{
  "mode": "test",
  "by_currency": {
    "EUR": {
      "verified_gross_minor": 1290,
      "refunded_minor": 0,
      "verified_net_sales_minor": 1290,
      "payhip_fee_minor": null,
      "stripe_fee_minor": 50,
      "contribution_minor": null,
      "payout_minor": 1240,
      "payhip_fee_complete": false
    }
  },
  "attribution": {"status": "unknown", "verified_sales": 0}
}
```

- [ ] **Step 4: Run reporting and route tests**

Run: `cd /root/libra && pytest tests/test_commerce_reporting.py tests/test_commerce_routes.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add commerce_reporting.py app.py tests/test_commerce_reporting.py tests/test_commerce_routes.py
git commit -m "feat: report verified commerce financials"
```

---

### Task 9: Zero-Budget Commerce Growth Decisions

**Files:**
- Create: `commerce_growth.py`
- Create: `tests/test_commerce_growth.py`
- Modify: `growth_autopilot.py` only to consume the new read model; do not add provider writes

**Interfaces:**
- Produces: `commerce_growth_decision(metrics: dict) -> dict`.
- Produces statuses `collecting_distribution`, `fix_offer`, `fix_checkout_or_value`, `eligible_for_next_organic_experiment`, `freeze_angle`, and `manual_required`.
- All returned actions contain `paid_spend_minor: 0`.

- [ ] **Step 1: Write failing decision-table tests**

```python
@pytest.mark.parametrize(("metrics", "expected"), [
    ({"verified_visits": 99, "product_clicks": 20, "verified_sales": 0}, "collecting_distribution"),
    ({"verified_visits": 100, "product_clicks": 0, "verified_sales": 0}, "fix_offer"),
    ({"verified_visits": 100, "product_clicks": 10, "verified_sales": 0}, "fix_checkout_or_value"),
    ({"verified_visits": 100, "product_clicks": 10, "verified_sales": 3}, "eligible_for_next_organic_experiment"),
    ({"verified_placements": 3, "verified_visits": 100, "product_clicks": 0, "verified_sales": 0}, "freeze_angle"),
])
def test_commerce_growth_decision_table(metrics, expected):
    decision = commerce_growth_decision(metrics)
    assert decision["status"] == expected
    assert decision["paid_spend_minor"] == 0


def test_incident_stops_scaling():
    decision = commerce_growth_decision({
        "verified_visits": 100, "product_clicks": 10, "verified_sales": 3,
        "open_incidents": [{"error_code": "payout_mismatch"}],
    })
    assert decision["status"] == "manual_required"
```

Add a test that Payhip-observed sales do not count as `verified_sales` and attribution remains unknown.

- [ ] **Step 2: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_commerce_growth.py -q`

Expected: FAIL because `commerce_growth` does not exist.

- [ ] **Step 3: Implement a pure deterministic decision function**

Prioritize open refund/dispute/payout/conflict incidents before all scaling rules. Apply `freeze_angle` when three verified placements have zero clicks before applying the generic 100-visit offer rule. Permit at most two organic assets per seven-day window. Return proposals only; publication still requires an existing adapter to return stable external `post_id`/`post_url` evidence.

Do not import any KDP upload/action executor. `growth_autopilot.py` may read the commerce summary and expose the decision, but commerce actions remain in their own allowlist and cannot inherit KDP action types.

- [ ] **Step 4: Add an architectural guard test**

```python
def test_commerce_modules_do_not_import_kdp_mutators():
    forbidden = {
        "kdp_upload", "kdp_finish_publish", "kdp_fix_publish",
        "kdp_live_replace", "reupload_metadata", "set_price",
        "free_promo_auto", "kdp_action_executor",
    }
    for path in COMMERCE_MODULE_PATHS:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imports = imported_module_names(tree)
        assert imports.isdisjoint(forbidden), (path, imports & forbidden)
```

- [ ] **Step 5: Run growth and KDP safety regressions**

Run: `cd /root/libra && pytest tests/test_commerce_growth.py tests/test_growth_autopilot.py tests/test_kdp_action_executor.py tests/test_kdp_auto_manager.py -q`

Expected: PASS; no KDP mutation path is reachable.

- [ ] **Step 6: Commit**

```bash
git add commerce_growth.py growth_autopilot.py tests/test_commerce_growth.py
git commit -m "feat: add zero-budget commerce growth decisions"
```

---

### Task 10: Offline Reconciliation CLI, Runbook, And Test-Mode Deployment Gate

**Files:**
- Create: `scripts/libra_commerce_reconcile.py`
- Create: `tests/test_libra_commerce_reconcile.py`
- Create: `docs/runbooks/libra-commerce-test-mode.md`
- Modify: `memory.md` only after verification and deployment are actually complete

**Interfaces:**
- CLI: `python3 scripts/libra_commerce_reconcile.py --ledger PATH --mode test --dry-run`.
- CLI: `python3 scripts/libra_commerce_reconcile.py --ledger PATH --mode test --apply`.
- Produces JSON with `mode`, `events_seen`, `reconciled`, `pending`, `conflicts`, `manual_required`, and `external_calls: 0`.
- Never accepts `--mode live` in this implementation phase.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_dry_run_is_read_only_and_live_mode_is_refused(tmp_path):
    db = tmp_path / "ledger.db"
    seed_pending_event(db)
    before = db.read_bytes()
    dry = run_cli("--ledger", str(db), "--mode", "test", "--dry-run")
    assert dry.returncode == 0
    assert json.loads(dry.stdout)["external_calls"] == 0
    assert db.read_bytes() == before

    live = run_cli("--ledger", str(db), "--mode", "live", "--apply")
    assert live.returncode == 2
    assert "live_mode_disabled" in live.stderr
```

Add tests for `--apply` idempotency, conflict exit status, manual-required exit status, missing ledger, and no imported `httpx`, `requests`, `stripe.Customer`, refund, payout, or provider mutation calls.

- [ ] **Step 2: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_libra_commerce_reconcile.py -q`

Expected: FAIL because the CLI does not exist.

- [ ] **Step 3: Implement the offline CLI**

The CLI reads the durable inbox and invokes `retry_pending`. `--dry-run` opens SQLite with `file:<absolute-path>?mode=ro` and computes candidate counts without schema creation. `--apply` is allowed only with `--mode test`. Print one JSON object and never print payloads, signatures, tokens, or customer data.

- [ ] **Step 4: Write the exact test-mode runbook**

Document:

1. Required test-only env names and how to confirm readiness without printing values.
2. External callback paths under `/libra/api/webhooks/...`.
3. Owner-only Payhip/Stripe setup boundaries.
4. How to rotate each webhook secret and why the old Payhip URL must be removed.
5. Synthetic signed fixture replay using FastAPI `TestClient`, not a network request.
6. Offline `--dry-run` then `--apply` reconciliation commands.
7. SQLite backup command using `sqlite3 data/libra-business.db ".backup '/root/backups/libra-business-before-commerce.db'"` after validating the explicit target path.
8. Test-mode service restart/readiness checks, with no live webhook registration.
9. Controlled real-transaction checklist marked as a future manual activation gate: purchase, delivery, Payhip receipt, Stripe match, refund observation, balance transaction, payout reconciliation.
10. Live activation remains blocked until that proof is recorded and the user explicitly authorizes it.

- [ ] **Step 5: Run the full verification suite**

Run:

```bash
cd /root/libra
pytest tests/test_settings.py tests/test_commerce_ledger.py tests/test_stripe_webhook.py tests/test_payhip_webhook.py tests/test_commerce_reconciliation.py tests/test_commerce_routes.py tests/test_payhip_tracking.py tests/test_commerce_reporting.py tests/test_commerce_growth.py tests/test_libra_commerce_reconcile.py -q
pytest tests/ -q
git diff --check
git status --short
```

Expected: every test passes, `git diff --check` has no output, and status lists only intended project files plus pre-existing user changes. Do not restart `libra.service`, edit nginx, add cron, register webhooks, or perform a test purchase in this task.

- [ ] **Step 6: Commit implementation documentation**

```bash
git add scripts/libra_commerce_reconcile.py tests/test_libra_commerce_reconcile.py docs/runbooks/libra-commerce-test-mode.md
git commit -m "docs: add commerce test-mode operations"
```

- [ ] **Step 7: Create the deployment checkpoint without activating live mode**

Run read-only checks:

```bash
cd /root/libra
systemctl status libra.service --no-pager
curl -fsS http://127.0.0.1:8200/api/commerce/summary -o /dev/null -w '%{http_code}\n'
git log -10 --oneline
```

Expected: service status is observable; unauthenticated summary returns `401`; commits for Tasks 1-10 are visible. Actual test-mode deployment/restart requires the active owner to review the diff first.

---

## Activation Gate After Implementation

Implementation completion is not commercial activation. Keep the following states explicit:

- `implemented_test_mode`: code and automated tests pass; no provider account touched.
- `manual_setup_required`: owner must complete Payhip product template, Stripe Thailand KYC/bank, Payhip-to-Stripe connection, test webhook registration, and secret entry.
- `controlled_transaction_required`: one owner-approved transaction must prove delivery, Payhip event receipt, Stripe financial match, refund observation, balance transaction, and payout reconciliation.
- `live_activation_blocked`: live mode remains refused until the controlled proof passes, incidents are zero, product/account IDs match, and the user gives a fresh explicit authorization.

The controlled transaction must record provider IDs, timestamps, integer amounts/currency, delivery status, reconciliation status, and redacted evidence. It must not record the buyer's raw email, name, address, card data, webhook secret, or signature.

## Success Criteria

- All provider callbacks fail closed on missing configuration, bad authentication, wrong mode/account, stale Stripe signature, malformed/oversized input, and unknown product.
- Same-event replay is idempotent; conflicting replay creates a critical incident without changing projections.
- Payhip alone produces zero verified revenue.
- Stripe-verified payment creates revenue exactly once; refunds reverse exactly once; fees reduce contribution only when complete; payouts never increase revenue.
- Multi-currency values are never summed or silently converted.
- Payhip clicks are tracked distinctly and sale attribution remains explicitly unknown.
- Growth decisions spend exactly zero and stop on conflicts, disputes, refunds requiring action, or payout mismatch.
- Commerce code cannot invoke a KDP mutation path.
- Full Libra tests pass from `/root/libra` with explicit `tests/` path.
- No live external mutation occurs during implementation or test-mode deployment.
