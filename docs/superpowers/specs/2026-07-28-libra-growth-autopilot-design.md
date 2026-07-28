# Libra Growth Autopilot Design

## Goal

Turn Libra from a broad automated book factory into a verified growth and
profit controller for the existing KDP portfolio.

The controller operates with:

- zero paid spend for the first 30 days;
- a maximum Amazon Ads budget of THB 3,000 per month after day 30;
- automatic price, promotion, content, distribution, and ad decisions;
- no authority to edit or republish live-book metadata, cover, interior, title,
  subtitle, or categories.

The system is autonomous wherever it can verify the resulting state. OTP,
CAPTCHA, expired sessions, unsupported APIs, or an unreadable confirmation
produce `manual_required`; they never produce a false success.

## Current Evidence

- The live KDP Bookshelf contains 44 matched titles.
- Attributed July royalties are concentrated in five ASINs.
- Thirty-five titles are currently classified as distribution-starved.
- Multiple unpaired free promotions produced zero downloads.
- The KDP account has accumulated two content blocks.
- Republishing a previously accepted title caused a subsequent rejection.

These facts make distribution, portfolio concentration, and account safety the
primary constraints. New-title production and live metadata optimization are
outside this design.

## Operating Principles

1. Evidence before action: no external action is successful without a stable
   post URL, post ID, campaign ID, KDP confirmation, or verified before/after
   state.
2. One commercial controller: no other Libra process may change price,
   promotion, portfolio status, or ad spend.
3. One variable per experiment window.
4. Profit before activity: clicks, downloads, and orders are intermediate
   signals; verified royalty and contribution are the economic outcomes.
5. Fail closed: stale, incomplete, conflicting, or unverified data can reduce
   or stop activity but cannot increase spend or risk.
6. Portfolio concentration: at most eight titles are active and at most three
   titles receive simultaneous organic tests.
7. Account preservation: content safety overrides every growth decision.

## Authority Boundary

### Allowed Automatically

- read KDP, Bookshelf, promotion, price, royalty, KENP, and ad state;
- score and classify titles;
- create and publish Content Hub pages;
- create localized promotional content;
- publish to channels with a supported, verifiable execution path;
- create tracked outbound Amazon links;
- schedule, stop, or evaluate safe KDP price promotions;
- create, pause, adjust, or stop Amazon Ads within the approved policy;
- freeze or reactivate titles inside the local active portfolio;
- send reports and incident notifications.

### Permanently Forbidden

- edit title, subtitle, author, description, keywords, categories, cover, or
  interior of a live title;
- republish a title that already has an ASIN;
- appeal or resubmit previously abandoned blocked titles;
- publish diet or meal-plan books;
- buy reviews, manipulate engagement, spam communities, or misrepresent a
  promotion;
- exceed the daily or monthly paid budget;
- claim execution from a reminder, process exit code, or browser click alone.

## Architecture

### Evidence Collector

Collects immutable observations from:

- KDP reports and Bookshelf;
- current price and promotion state;
- Amazon Ads reports and campaign state;
- Content Hub page views and outbound clicks;
- external distribution post URLs and post IDs;
- review and BSR observations when reliably available;
- account, category, and content-risk incidents.

Every observation includes `source`, `observed_at`, `fresh_until`,
`confidence`, and the raw evidence reference.

### Portfolio Scorer

Produces a deterministic score from:

- verified revenue: 30%;
- reader intent, including KENP and paid-order evidence: 25%;
- tracked traffic: 20%;
- conversion proxies: 15%;
- account and metadata risk: 10%.

Missing inputs receive no positive score. Estimated market demand, generated
competitor data, reminders, and unverified posts do not count.

The scorer assigns one state:

- `scale`: repeatable downstream signal exists;
- `test`: evidence is promising but incomplete;
- `maintain`: earns passively without enough evidence to scale;
- `freeze`: no useful signal after the required verified tests;
- `blocked`: action is unsafe or externally unavailable.

### Growth Planner

Creates one bounded daily plan after collecting fresh evidence. It selects:

- no more than eight active titles;
- no more than three concurrent organic experiments;
- no more than two concurrent advertised titles;
- exactly one experimental variable per title and measurement window.

Plans are idempotent and carry a stable action key. Re-running a plan cannot
duplicate a post, promotion, price change, or campaign.

### Content Engine

Creates useful, localized Content Hub material grounded in the actual book:

- problem-solving articles;
- short checklists or samples;
- localized promotional copy;
- one clear, tracked Amazon call to action.

Content must pass language, factuality, duplication, policy, and book-alignment
checks. It may not mass-produce thin doorway pages, invent testimonials, expose
substantial copyrighted book content, or make unsupported health, legal, tax, or
financial claims.

### Libra Content Hub

The Hub is the owned organic acquisition layer. Each active title receives:

- a canonical localized landing page;
- supporting articles only when they add distinct search or reader value;
- tracked outbound links carrying book, language, channel, and campaign IDs;
- privacy-respecting first-party event collection;
- internal links only between genuinely related titles and content.

The Hub is mounted under the existing Libra namespace. Its API must remain
under `/libra/` and must never introduce a top-level `/api/` nginx location.

### Distribution Executor

Publishes only through supported channels that provide verifiable results.
Each completed distribution action stores its final URL or post ID. Login,
OTP, CAPTCHA, moderation, or confirmation ambiguity returns
`manual_required`.

The executor applies language, community-rule, cadence, duplicate-content, and
link-policy gates before publishing.

### KDP Promotion Controller

May manage price, one-day Free Book Promotions, and eligible Countdown Deals.
It never modifies the live book package or metadata.

A free promotion requires:

- a verified external distribution placement;
- a fresh KDP promotion-state read;
- a one-day initial window;
- baseline and post-window measurements;
- no overlapping price or channel experiment.

Zero downloads after a verified one-day test prevents use of the remaining
promotion days for that experiment cycle.

### Ads Controller

Paid activity remains disabled for the first 30 complete days. On day 31, a
title is eligible only if it has at least one of:

- verified paid royalty growth during the organic period;
- at least 100 incremental KENP;
- at least 20 verified tracked outbound clicks.

If no title qualifies, paid spend remains zero and the organic phase continues.

The Ads Controller prefers the Amazon Ads API when authorized. Otherwise it
may use browser automation with verified before/after state. It never bypasses
OTP or CAPTCHA.

## Organic Phase: Days 1-30

### Days 1-3: Baseline

- snapshot the portfolio and commercial state;
- select up to three evidence-backed titles;
- create tracking dimensions and canonical Hub pages;
- record the no-action baseline.

### Days 4-14: Distribution Tests

- publish two useful localized content assets per title per week;
- execute only verifiable distribution;
- change one hook or channel variable per experiment;
- withhold free promotion until an external placement is live.

### Day 15: Midpoint Decision

- `scale` when traffic and royalty or KENP increase;
- continue testing when verified traffic exists but downstream evidence is
  incomplete;
- `freeze` after at least three verified placements produce no tracked click;
- replace a frozen title with the next eligible candidate.

### Days 16-30: Compound Winners

- increase winners to at most three useful assets per week;
- build relevant same-language internal links;
- run a one-day paired promotion only when its gate is open;
- measure baseline, promotion, and seven-day post-promotion results.

### Organic Success Gate

The first phase succeeds when all account-safety conditions hold and:

- at least ten external placements are verified;
- at least twenty outbound Amazon clicks are tracked;
- at least two titles gain royalty or KENP;
- seven-day revenue run rate reaches at least USD 0.50 per day.

Failing the gate does not authorize ads. It extends organic testing or freezes
the portfolio when continued effort has no measurable value.

## Paid Phase

### Budget

- monthly portfolio cap: THB 3,000;
- daily portfolio cap: THB 100;
- maximum two advertised titles;
- initial title cap: THB 50 per day;
- 20% of the monthly budget remains reserve until a winner is proven;
- the controller cannot increase the approved monthly cap.

### Campaign Progression

1. `explore`: low-budget automatic targeting.
2. `harvest`: convert verified search/product targets into isolated manual
   exact or product-target campaigns.
3. `promote`: increase a profitable winner by no more than 15%.
4. `hold`: retain the current state during the seven-day learning window.
5. `stop`: pause when spend reaches the verified royalty value of two expected
   sales without an order.
6. `kill`: terminate when break-even ACOS is exceeded or contribution remains
   negative beyond the learning window.

Budget may increase no more than once every 72 hours. Stale data may reduce or
stop spend but may never increase it.

### Profit Gate

Break-even ACOS is calculated per title from verified net royalty, delivery
cost, direct cost, and ad spend. A campaign cannot be promoted solely from
click-through rate, impressions, or orders. Contribution after ad spend must be
positive.

## Data And Control Plane

A single SQLite ledger stores:

- immutable observations;
- portfolio scores and classifications;
- experiment definitions and windows;
- planned and executed actions;
- external evidence;
- tracked Hub events;
- price and promotion history;
- ad campaigns, spend, and contribution;
- policy versions and incidents.

All commercial mutation uses a single-writer lock. Every action records its
policy decision, before state, attempted change, after state, evidence, and
terminal status.

## Schedule

- 08:30: session and Bookshelf readiness;
- 09:15: KDP sales sync;
- 09:30: Hub, distribution, and ad evidence sync;
- 10:00: one portfolio planning cycle;
- 10:15: execute approved organic, promotion, price, and ad actions;
- 20:30: verify external posts and campaign state;
- every ten minutes: health checks with no authority to increase spend;
- daily: concise Telegram operating report;
- weekly: portfolio and experiment decision report.

Exact times may be shifted to avoid existing Libra cron collisions, but their
dependency order must remain unchanged.

## Dashboard Contract

The dashboard must answer:

1. What verified revenue was earned today and by which title?
2. Which titles are `scale`, `test`, `maintain`, `freeze`, or `blocked`?
3. Which channels produced verified traffic and downstream value?
4. What did the controller do, and what evidence proves it?
5. What was spent, and what is contribution after ads?
6. What is blocked and why?

Plans, reminders, and attempts are visually distinct from verified outcomes.

## Emergency Stop

Commercial mutation stops immediately when:

- a new content or account warning is observed;
- a daily or monthly budget boundary is reached or cannot be verified;
- KDP attribution conflicts with the latest report;
- a post is duplicated, in the wrong language, or violates channel policy;
- campaign or promotion after-state cannot be verified;
- session, Bookshelf, database integrity, backup, or single-writer readiness
  fails.

Read-only collection, health reporting, and incident notification continue
during an emergency stop.

## Failure Handling

- Transient network and rate-limit errors use bounded retry.
- Authentication and human-verification barriers do not retry blindly.
- Partial execution is reconciled from external state before any new attempt.
- Conflicting evidence opens an incident and freezes the affected title or
  channel.
- No error path silently falls back to paid spend, metadata mutation, or
  untracked publication.

## Acceptance Criteria

- The organic 30-day phase cannot spend money.
- Ads remain disabled on day 31 unless a title passes the approved Growth Gate.
- Total paid spend cannot exceed THB 100 per day or THB 3,000 per month.
- At most three organic and two paid title experiments run concurrently.
- Live-book metadata and files cannot be mutated through any controller path.
- Every completed external action has verified after-state evidence.
- OTP, CAPTCHA, stale data, and unreadable confirmation fail closed.
- One experiment variable is active per title and window.
- Free promotions require verified paired distribution and begin with one day.
- The system freezes zero-signal titles and reallocates bounded capacity.
- Dashboard, ledger, Telegram, and weekly reports distinguish plans from
  verified outcomes.
- Focused policy, ledger, executor, recovery, and end-to-end tests pass before
  production activation.

## Rollout Boundary

Activation is staged:

1. shadow mode with no external mutation;
2. organic execution with paid policy hard-disabled;
3. day-15 decision audit;
4. day-30 Growth Gate audit;
5. paid execution only for eligible titles and only after all readiness checks
   pass.

The rollout must not reactivate legacy commercial writers or create a second
strategist. Existing data collectors may remain, but all commercial authority
is transferred to the single Growth Autopilot controller.
