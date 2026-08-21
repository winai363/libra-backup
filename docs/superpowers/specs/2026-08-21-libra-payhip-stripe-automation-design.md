# Libra Payhip And Stripe Automation Design

**Date:** 2026-08-21

## Goal

Add a fail-closed commerce lane for the premium French senior digital kit: tracked acquisition, verified Payhip/Stripe sales ingestion, automatic delivery observation, refunds, fees, payouts, revenue reporting, and evidence-driven organic growth with zero paid-ad budget.

## Definition Of Automation

The owner performs the external one-time setup that cannot be automated safely: Payhip account and product template creation, Stripe Thailand KYC and bank connection, Payhip-to-Stripe connection, webhook registration, and secret entry. Payhip's public API currently supports coupons and license keys, not product creation, so the system must not claim product publishing automation.

After setup, event ingestion, reconciliation, reporting, content planning, tracked links, coupons, alerts, and growth decisions run unattended. OTP, CAPTCHA, account review, refund initiation, and dispute responses return `manual_required`.

## Product Contract

- Product: `Kit Autonomie Numerique pour Seniors et Aidants`
- Price target: EUR 12.90
- Contents: setup workbook, WhatsApp/online-services guides, scam-prevention cards, caregiver checklist, and emergency-information template
- The Payhip files must add substantial standalone value and must not reproduce a KDP Select eBook.
- Product creation and replacement in Payhip use an owner-created template until Payhip provides an official product-management API.

## Architecture

### Settings And Secrets

A single settings module loads `.env` for both the FastAPI process and imported modules. Required secrets have no defaults. Secrets, signatures, raw card data, and raw customer emails are never logged or stored in the business ledger.

### Webhook Inbox

Public routes are namespaced under `/libra/api/webhooks/payhip` and `/libra/api/webhooks/stripe`; no top-level nginx `/api/` location is added. Routes impose a small application-level body limit and durably record an inbox event before returning success.

Stripe events are verified against the exact raw body, `Stripe-Signature`, endpoint secret, five-minute timestamp tolerance, expected account, and expected live/test mode using the official Stripe SDK.

Payhip sends `paid`, `refunded`, `subscription.created`, and `subscription.deleted` events. Its official help page documents use of the account API key for webhook validation but does not establish an independently signed raw-body contract in the evidence reviewed. Payhip events therefore remain operational signals until reconciled with a verified Stripe charge or balance transaction. A Payhip-only event cannot create verified revenue.

### Commerce Ledger

Append-only provider events are separated from mutable order projections. All money uses integer minor units and retains original currency.

The ledger stores:

- provider event ID, type, time, mode, verification state, payload hash, and processing state;
- product provider IDs and current price contract;
- order gross, discount, tax, Payhip fee, Stripe fee, net, payment ID, status, country, and optional attribution key;
- refunds and partial refunds;
- Stripe balance transactions;
- payouts and payout-to-balance-transaction reconciliation;
- immutable conflicts and reconciliation incidents.

It stores no card data. Customer email is omitted unless a one-way keyed identifier is later proven necessary.

### Truth Model

- A Payhip `paid` event is an observed order, not verified cash.
- A matching verified Stripe payment establishes paid revenue.
- A refund reverses revenue by the verified refunded amount.
- Payhip and Stripe fees are costs.
- A payout is cash settlement and never new revenue.
- EUR, USD, and THB remain separate. Any conversion records rate, source, and timestamp.
- Duplicate event ID plus identical content is a no-op; the same ID with different content creates a critical conflict and no business-state mutation.
- Out-of-order refund/payment events remain pending until reconciliation can prove the sequence.

### Acquisition And Attribution

The Content Hub gains a separate Payhip destination allowlist and `payhip_outbound` event type while retaining Amazon destination checks. Each click receives an opaque click ID.

Campaign-to-sale attribution remains `unknown` until a real transaction proves that Payhip preserves and returns the click ID. Until then, reports show campaign clicks and product sales separately and never claim causal attribution.

### Growth Controller

The controller creates useful French content from the product's verified source material, at most two assets per week initially. It publishes only through adapters that return stable external evidence. Zero paid spend is hard-coded for this lane.

Decision rules:

- fewer than 100 verified visits: distribution problem;
- at least 100 visits with no product click: message/offer problem;
- product clicks with no verified sale: checkout/value problem;
- at least three verified sales: permit a second related asset or product experiment;
- three verified placements with zero clicks: freeze that content angle;
- refunds, disputes, or payout mismatches open an incident and stop scaling.

## State Model

Commerce event: `received -> signature_checked -> normalized -> reconciled`, or `unverified`, `conflict`, `manual_required`.

Order: `observed -> payment_pending -> paid_verified -> partially_refunded -> refunded`, with `disputed` and `reconciliation_failed` as blocking states.

Payout: `observed -> items_matched -> reconciled`, or `mismatch`.

## Error Handling

Malformed, oversized, stale-signature, wrong-account, wrong-mode, conflicting, or unsupported events are refused or quarantined with stable error codes. Processing is transaction-safe: a crash after inbox persistence can be retried without duplicating an order, refund, fee, payout, or growth signal.

## Verification

Automated tests must cover:

- schema migration, idempotent replay, and conflicting replay;
- Stripe valid, invalid, stale, wrong-mode, and wrong-account signatures over raw bodies;
- verified Payhip fixtures from the configured account;
- duplicate and out-of-order payment/refund events, including partial refunds;
- integer money and multi-currency separation;
- fee and payout reconciliation without double-counting revenue;
- transactional crash and retry;
- webhook body limits, malformed JSON, public callback routing, and secret/PII redaction;
- Payhip host allowlist bypass attempts and distinct click event kinds;
- absent attribution remaining explicitly unknown;
- commerce code being unable to invoke any KDP mutation adapter.

Before activation, one controlled real transaction must prove purchase, delivery, Payhip event receipt, verified Stripe match, refund observation, and balance/payout reconciliation. No revenue claim is made before this proof.

## Deployment Boundary

The service may deploy webhook ingestion in test mode before the owner completes KYC. Live mode remains closed until secrets, expected account IDs, product IDs, and the controlled transaction are verified. Product creation remains manual once; ongoing product-file automation requires a supported official API or a separately approved browser workflow with live before/after evidence.

