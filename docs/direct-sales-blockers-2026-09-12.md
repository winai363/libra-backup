# Direct sales (Payhip/Stripe) — blockers, kept separate from the Amazon experiment

The organic experiment sends traffic to Amazon listings. It does not depend on
direct sales, and nothing below was changed to unblock it. No Stripe key was
added, and no payment verification was loosened.

## Two different blockers, often confused

**1. Payment collection — NOT blocked.**
Payhip is live and can take real money today (`LIBRA_COMMERCE_MODE=live`, live
product ids and live webhook token are present, `commerce_setup_check.py` reports
every step `ok`). Payhip has no sandbox, so any purchase there is real money.

**2. Payment verification — BLOCKED.**
The standing rule is "Payhip observes, Stripe proves": a Payhip event may not
create revenue on its own; a Stripe transaction matching id, amount and currency
must confirm it. `STRIPE_SECRET_KEY_LIVE` is absent from `.env`, so a real sale
would be collected but unverifiable, and the ledger would hold an unconfirmed
event rather than revenue.

Consequence if someone buys before this is fixed: the money arrives, our books do
not record it as verified revenue, and the reconciliation shows a pending event.
That is the safe failure direction (no invented revenue) and it is why the key
must be added by the owner rather than worked around here.

**Owner action:** put `STRIPE_SECRET_KEY_LIVE=sk_live_…` in `/root/libra/.env`
yourself — never through chat — then run
`python3 scripts/commerce_setup_check.py --stripe` once to create the webhook
endpoint and store the signing secret. The secret key can then be rolled.

## Rights blocker, which is separate again

A book enrolled in KDP Select may not be sold as an EPUB anywhere else. Current
file evidence for the three experiment books:

| Book | Select record | Term recorded | Source |
|---|---|---|---|
| adhd-adults-workbook-es | Enrolled | 2026-06-28 → 2026-09-25 | KDP promotion-manager, verified 2026-06-28 |
| ai-workflows-accountants-pt | Enrolled | 2026-07-02 → 2026-09-29 | kdp_enroll_batch retry |
| bilingual-english-spanish-kids-vocab | Enrolled | 2026-07-02 → 2026-09-29 | kdp_enroll_batch |

These are **file records from June and July, not a current reading of the KDP
page**, and Select renews automatically unless renewal was turned off. Historical
KENP is not proof of enrollment either: pages read can appear in a month the term
already ended.

**Rule:** before any old title is offered for direct sale, re-read the book's
Select status on the KDP page (read-only) and record what was seen, with the
date. `payhip_catalog.guard_book_for_payhip` already refuses enrolled books; that
guard stays, and the check above is what feeds it honestly.

Only `aquarelle-botanique-debutants-fr` was never enrolled — which is why it is
the one title on Payhip. That does not generalise to the catalogue.
