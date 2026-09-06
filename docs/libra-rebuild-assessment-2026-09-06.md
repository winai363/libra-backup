# Libra rebuild assessment

2026-09-06. Initial assessment, not approved implementation specification.
No publishing, pricing, appeals, cron or application code changed.

## Evidence

Read local rules, project memory, demand_analysis.py, kdp_sales_sync.py,
listing.json files and data/libra-business.db (SQLite mode=ro).
Latest stored sales observation: 2026-09-05 09:15:10 +07.

| Period | Estimated royalties USD | All-type orders | KENP |
| --- | ---: | ---: | ---: |
| July latest observation | 16.15 | 325 | 934 |
| August latest observation | 13.89 | 3 | 794 |
| September through September 5 observation | 2.54 | 1 | 100 |

August versus July royalties decreased about 14%. August 5 observation was
$0.48 versus September 5 $2.54. September deterioration is not established.
Orders include unseparated types: do not call these paid purchases. Royalties
use static currency conversion and are estimates, not payouts or net profit.
July snapshots begin July 11 but contain month-to-date estimates.

Local catalogue: 64 records, 38 LIVE, 5 BLOCKED, 21 UNKNOWN. This is not a fresh
Amazon bookshelf verification. Latest recorded block: aquarelle-botanique-debutants-fr,
August 22, submission 67406856, disappointing customer experience.
User was asked for any subsequent blocks and latest Amazon rejection text.
hub_events contains zero rows: missing tracked events do not prove zero Amazon
traffic, no demand, or functioning instrumentation.

## Problems verified

- CLAUDE.md treats AI disclosure/account history as proven rejection causes.
  Rejection despite an internal editorial score and illustrations does not prove
  that causal claim or eliminate content defects. Preserve operational freeze.
- demand_analysis.product_opportunities divides all-status revenue by LIVE title
  count. ADHD's $8.75 includes $4.77 from a BLOCKED title. Displayed $4.38 per
  LIVE title is therefore not revenue earned by the LIVE cohort.
- Latest July overview $16.15 versus attributed $13.04; August $13.89 versus
  $13.76. Attribution gaps must remain explicit rather than silently allocated.
- Per-metric monthly maxima in demand_analysis need review: KDP estimates can
  decrease (September moved $2.08 to $2.07 before rising). Maxima are not
  necessarily the final corrected monthly truth.
- Zero sales without exposure measurements cannot distinguish poor discovery,
  poor product appeal and poor purchase conversion.

## Recommended rebuild scope

1. Evidence ledger: rejection cases linked to title/version, date and exact
   Amazon message. Separate observed facts, hypotheses and unknowns.
2. Revenue reporting: latest monthly observations, equal-period comparisons,
   paid/free/unknown orders, native currency amounts, attribution gaps, freshness,
   and LIVE-only cohort performance. Never sum cumulative daily snapshots.
3. Editorial review: inspect representative blocked and LIVE manuscripts, EPUBs,
   covers and descriptions for accuracy, repetition, translation, usefulness,
   rights and rendering. Internal scores never imply Amazon acceptance.
4. Demand and distribution: focus on one reader group after reviewing seller
   evidence; measure exposure and conversion before scaling production.
5. Publishing remains separate: no uploads, retries, appeals, price changes or
   freeze bypass included in this assessment or proposed internal rebuild.

Alternatives: analytics-only repair is smaller but leaves editorial questions;
a separate direct-sales lane requires customer acquisition and fresh storefront
readiness verification. Check KDP Select commitments before distributing ebooks
elsewhere. Existing storefront setup is not evidence of current live readiness.

## Acceptance checks for later implementation

- Corrected monthly estimates and equal-period comparisons; missing data explicit.
- BLOCKED revenue excluded from LIVE-cohort ranking but retained in history.
- Overview = attributed + explicit unattributed amount for every observation.
- Analysis cannot invoke publishing, paid services or external messaging.
- Content findings cite actual defects; no guaranteed approval or revenue claims.

## Policy references checked

- https://kdp.amazon.com/en_US/help/topic/G200635600 : official indexed text
  requires AI-generated content disclosure; it does not establish the cause of
  these rejections. Direct page body was unavailable in the browser extract.
- https://kdp.amazon.com/en_US/help/topic/G200952510 : official quality guide
  covers metadata mismatch, duplication, missing content, images and formatting;
  recommends Kindle Previewer.

Outstanding: latest rejection/account notice, blocks after August 22, and user's
sales target. Implementation design awaits this clarification. Rebuild is not complete.
