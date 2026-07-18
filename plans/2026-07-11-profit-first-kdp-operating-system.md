# Libra Profit-First KDP Operating System

## Objective

Make Libra optimize for verified KDP royalties and net contribution profit, not generated-book count, free downloads, or completed distribution tasks. The first milestone is a trustworthy daily decision loop for the existing catalog; new-book generation and paid ads remain off until the data gates below pass.

## Current Operating Layer — Profit Pace (2026-07-18)

The 90-day policy remains the source of truth. Libra now adds an internal 110% buffer (`$82.50` against the approved `$75` revenue target) so management aims above the minimum without changing the approved target.

- Daily mode is `ahead`, `on_pace`, `recovery`, `critical`, or `insufficient_data`, based on verified mode-window royalties versus elapsed approved-target pace.
- The controller reports variance, required daily revenue to reach the stretch target, 7/14-day run rates, and projected Day-90 revenue.
- The live portfolio is allocated `70% exploit / 20% explore / 10% archive`; this is attention allocation, not automatic listing mutation.
- New verified royalty signals with meaningful orders or KENP enter `winner_watch`. They require a second observation window before repeatable-winner status.
- Free-promotion distribution distinguishes planned, reminded, and verified. `reminded_at` and publisher capability declarations are not publication evidence; the proposer and executor both require an external `post_url` or `post_id` before consuming a promotion slot.
- Overview royalties remain the headline. Attributed and unattributed royalties are reconciliation details and must not replace the account total.

Implementation reference: `docs/superpowers/specs/2026-07-18-libra-profit-pace-agent-design.md` and `docs/superpowers/plans/2026-07-18-libra-profit-pace-agent.md`.

## Current Evidence

- KDP Reports log on 2026-07-11 shows 252 all-type orders/downloads, 361 KENP, and $7.6301905904 MTD royalties.
- `/api/dashboard/overview` shows $6.84 real MTD royalties and $59.29 estimated 30-day revenue. These numbers disagree with KDP Reports.
- `profit_tracker.py:99-107` estimates revenue from units when a snapshot has zero royalties, so free downloads can be valued as paid sales.
- `profit_tracker.py:123-126` labels a title a winner at five units even when royalties are zero.
- `kdp_sales_sync.py:229-235` reads `topEarningTitles`; the partial set is then used as the stored baseline, which can omit titles and produce re-entry/delta errors.
- `distribution_report.py:596-609` can award an On Track score of 100 when revenue is only above zero; setup completion dominates commercial progress.
- `scripts/kdp_auto_manager.py:63-69` records `free_post` as `sent_digest`; it does not publish a post.
- Auto-generation is commented out in cron, but `app.py:200-209` still presents a 01:00 generation schedule and can report automation as unpaused.
- Direct tracked creation cost is $9.44 versus $7.63 MTD royalties, before server cost and owner time. This is not yet a proven profitable system.
- The repository has about 97 Python/shell scripts, 22,089 lines, and 22 Libra-related cron entries. Operational complexity is high relative to current revenue.
- Baseline verification: `PYTHONPATH=. pytest -q` passes 47 tests. Plain `pytest -q` fails during collection because imports depend on `PYTHONPATH=.`.

## Non-Negotiable Business Rules

1. `verified_royalties` from the KDP overview/report is the money source of truth.
2. Free downloads, paid orders, KENP, paperback orders, and royalties are separate measures. Never infer paid revenue from download count.
3. A winner requires verified contribution profit or a defined leading signal followed by a cooldown window. Units alone cannot create winner status.
4. Setup completion is an operations score, not a business score. Display both separately.
5. Paid actions, price changes, metadata changes, enrollment changes, and browser-driven KDP mutations require an explicit policy gate and audit record.
6. New-book generation remains paused until at least one repeatable profitable segment is proven.
7. KDP reporting and ad attribution can lag. Do not make same-day scale/kill decisions.

## Phase 0: Freeze False Decisions

### Files

- `profit_tracker.py`
- `winner_signals.py`
- `distribution_report.py`
- `app.py`
- `scripts/kdp_auto_manager.py`
- Tests under `tests/`

### Changes

1. Remove fallback revenue estimation from operational decisions. Keep any modeled value only under an explicitly named field such as `modeled_revenue_usd`, never `revenue_usd`.
2. Mark existing winner and scale recommendations as unavailable whenever verified royalty data is absent or stale.
3. Rename the current score to `operations_readiness_score`. Add a separate `commercial_progress` block driven by verified royalties, net contribution, royalties per live title, and target pace.
4. Change `free_post` execution result from `sent_digest` to `manual_required` unless a real platform post ID/URL is returned.
5. Make the dashboard derive generation/publishing state from actual cron/config state rather than hardcoded schedule labels.

### Acceptance

- A title with 17 free downloads and $0 royalties reports $0 verified revenue and cannot be a winner.
- The dashboard and agent show the same MTD royalty total as the KDP overview source, within one cent.
- A 100 operations score cannot display as overall business On Track when revenue pace is behind target.
- No action is labeled executed without an external confirmation ID or a verified KDP state change.

## Phase 1: Repair the Revenue Ledger

### Files

- `kdp_sales_sync.py`
- `profit_tracker.py`
- `app.py`
- `distribution_report.py`
- New migration/reconciliation script under `scripts/`
- New focused tests under `tests/`

### Changes

1. Store the KDP account overview snapshot separately from per-title rows: timestamp, reporting window, total royalties, orders/downloads, and KENP.
2. Treat `topEarningTitles` as a partial attribution table, not the complete account ledger. Preserve prior title baselines when a title is absent from a partial response.
3. Store snapshots as cumulative observations keyed by date/reporting window. Derive deltas idempotently; rerunning a sync must not add revenue twice.
4. Add reconciliation fields: overview total, attributed-title total, unattributed remainder, freshness, and reconciliation status.
5. Backfill/reconcile the existing July state from retained logs/state without inventing per-title allocation.
6. Compute per-title contribution as verified royalty minus attributable creation/promotion/ad cost. Keep server overhead separate at portfolio level.

### Acceptance

- Re-running the same source snapshot produces no financial changes.
- A title dropping out of and re-entering `topEarningTitles` does not become first-seen and does not double count MTD results.
- Portfolio verified royalties equal the KDP overview figure; any title attribution gap is displayed explicitly.
- Tests cover zero-royalty free downloads, partial title sets, re-entry, non-USD conversion, stale data, and duplicate sync runs.

## Phase 2: Replace the Agent With a Profit Experiment Controller

### State Machine

`OBSERVE -> DIAGNOSE -> RECOMMEND -> APPROVE -> EXECUTE -> COOLDOWN -> EVALUATE`

### Changes

1. Select no more than three experiments at once from the existing catalog:
   - Spanish adult ADHD series: strongest verified royalty signal.
   - Spanish beginner watercolor: KENP signal but low royalties; test read-through/positioning.
   - AI productivity toolkit: verified royalty exists, but separate paid demand from free activity first.
2. Give each experiment one hypothesis, one variable, a start/end date, baseline, success threshold, kill threshold, and maximum cost.
3. Use a 72-hour cooldown for metadata/category propagation and up to 14 days for ad-attribution evaluation.
4. Define winner as positive verified contribution profit across at least two observation windows, not one burst of downloads.
5. Keep price, metadata, Select enrollment, paid promotion, and ads behind human approval until three clean agent cycles produce no false actions.
6. Log every recommendation and action with evidence snapshot IDs, expected impact, actual result, and rollback/stop condition.

### Acceptance

- The agent cannot scale from free downloads alone.
- Every recommendation names its source data age and confidence.
- At most three active commercial experiments exist.
- The agent waits for the correct cooldown before evaluating a change.

## Phase 3: Lowest-Cost Growth Loop

### Portfolio Policy

1. Keep new-title generation off for 30 days or until one segment has repeatable positive contribution profit.
2. Concentrate free effort on product-page conversion: accurate categories, seven relevant keyword slots, clear descriptions, series linking, and compliant A+ content for the three experiment titles.
3. Use KDP Select free promotion only with a measurable follow-on goal such as KU pages, series read-through, reviews, or post-promo paid royalties. A free-download target by itself is not success.
4. Prefer eligible 70% royalty pricing after checking marketplace, VAT/delivery cost, and paperback-price constraints. Do not use permanent $0.99 pricing as a default volume tactic.
5. Do not open Amazon Ads until the ledger is repaired and a title has organic buyer evidence. When opened, use a very small Sponsored Products test, dynamic bids down only, hard spend cap, and break-even ACoS based on actual royalty per sale.
6. Evaluate ads after attribution lag; harvest converting terms into manual exact/product targeting and exclude proven waste. KU royalties require separate analysis because ad-console ACoS does not include them.

### 30-Day Commercial Targets

- 100% reconciliation between KDP overview royalties and the Libra ledger.
- Zero false winner labels from free downloads.
- Three or fewer active experiments.
- At least one title/series with positive verified contribution profit in two observation windows.
- No paid spend until the above gates pass.
- Reduce recurring jobs that produce neither fresh data nor verified external action.

## Phase 4: Reliability, Security, and Cost Cleanup

1. Add a central heartbeat for sales sync, roster, category scan, promo manager, distribution report, and agent run. Alert once on state transition, not on every retry.
2. Make cron jobs fail loudly and expose last success, last failure, duration, and next run.
3. Remove duplicate/no-value schedules only after a 14-day usage and dependency review. Keep rollback notes.
4. Change `kdp_session_aplus.json` to owner-only permissions and run Libra under a dedicated non-root service account after confirming file/network requirements.
5. Pin dependencies and make plain `pytest -q` work without an environment-specific import workaround.
6. Add tests around sales sync, financial truth, agent state transitions, action verification, and cron freshness before expanding automation.

## Verification Commands

```bash
cd /root/libra
PYTHONPATH=. pytest -q
python3 -m py_compile app.py profit_tracker.py distribution_report.py kdp_sales_sync.py scripts/kdp_auto_manager.py
curl -sS http://127.0.0.1:8200/api/dashboard/overview | jq '.sales'
curl -sS http://127.0.0.1:8200/api/profit/portfolio | jq '.summary'
tail -20 logs/sales-sync.log
stat -c '%a %n' .env kdp_session*.json
crontab -l | rg '/root/libra|cd /root/libra'
systemctl status libra.service --no-pager -l
```

## Failure Conditions

- KDP overview cannot be reconciled to stored ledger snapshots.
- Free and paid orders cannot be distinguished enough to prevent false revenue inference.
- Browser automation changes KDP state without a verifiable before/after record.
- The agent acts on stale or lagging data.
- Ads are started before unit economics and attribution are trustworthy.
- More titles are generated before a profitable segment is demonstrated.

## Official Policy References Checked 2026-07-11

- eBook royalties and pricing: https://kdp.amazon.com/en_US/help/topic/G200634500 and https://kdp.amazon.com/en_US/help/topic/G200634560
- AI-generated content disclosure: https://kdp.amazon.com/en_US/help/topic/G200672390
- Content and quality rules: https://kdp.amazon.com/en_US/help/topic/G200952510
- Categories and keywords: https://kdp.amazon.com/en_US/help/topic/G200652170 and https://kdp.amazon.com/en_US/help/topic/G201298500
- KDP Select and promotions: https://kdp.amazon.com/en_US/help/topic/GD9PMU58BV24QFZ7 and https://kdp.amazon.com/en_US/help/topic/G200798990/
- Sponsored Products: https://advertising.amazon.com/library/guides/authors-guide-to-sponsored-products and https://advertising.amazon.com/library/guides/targeting-with-sponsored-products
- Reporting lag: https://kdp.amazon.com/en_US/help/topic/G202173620
