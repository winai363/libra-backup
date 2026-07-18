# Libra Profit-Pace Agent Design

## Objective

Move Libra from a policy-compliant experiment runner to a profit-paced portfolio manager that aims for an internal 110% buffer over the existing 90-day target while preserving every current KDP account-safety rule.

## Business Rules

- Verified KDP overview royalties remain the portfolio revenue source of truth.
- The internal stretch target is 110% of the approved 90-day revenue target; the approved target itself is unchanged.
- Paid spend, new-title generation, and metadata/category mutation on published ASINs remain disabled.
- At most three experiments may be active, one variable per experiment.
- Free promotion eligibility requires `post_url` or `post_id` evidence. A capability declaration, reminder, or planned queue entry is not proof of publication.

## Components

### Pace Controller

Calculate elapsed-window target, variance, required daily revenue, 7/14-day run rates, projected Day-90 revenue, and one of four modes: `ahead`, `on_pace`, `recovery`, or `critical`. `ahead` requires at least 110% of elapsed approved-target pace. `critical` means the portfolio is below 75% of elapsed pace after the first three days.

### Portfolio Allocator

Classify each live title using verified evidence:

- `exploit`: positive verified contribution and current commercial activity.
- `explore`: measurable traffic or KENP without repeatable positive contribution.
- `archive`: no meaningful traffic and no verified revenue.

The agent reports a 70/20/10 attention policy. It does not mutate listings merely to enforce the allocation.

### Opportunity Scoring and Winner Fast Lane

Rank titles using verified royalties, recent orders, KENP, data completeness, action speed, and account risk. A title with a new verified royalty increase plus meaningful orders or KENP becomes a `winner_watch` candidate. It must be observed across two windows before becoming repeatable; the fast lane accelerates observation, not KDP mutation.

### Distribution Evidence

Distinguish `planned`, `reminded`, and `verified` distribution. Only a record containing an external `post_url` or `post_id` is verified. New free-promotion proposals and executor actions require verified external evidence before KDP scheduling. Existing completed experiments are not rewritten retroactively.

### Reporting

Expose the pace controller, allocation summary, ranked opportunities, and winner watchlist through the profit portfolio API and profit-agent state. The portfolio headline uses overview royalties; attributed and unattributed royalties remain visible as reconciliation details.

## Failure Handling

- Missing or stale snapshots produce `insufficient_data`; they never trigger recovery mutations.
- Incomplete title costs reduce confidence and prevent a repeatable-winner conclusion.
- Missing distribution proof blocks new free-promotion proposals with an auditable reason.
- No component may weaken executor safety validation.

## Verification

- Unit tests cover pace boundaries, baseline-window math, allocation, opportunity ranking, winner fast lane, and distribution proof.
- Existing 178 tests remain green.
- Production API returns current overview royalties and the new decision blocks.
- A dry-run proposer cannot create an unverified free-promotion candidate.
