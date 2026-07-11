# Libra 90-Day Profit Agent Design

## Purpose

Turn Libra from a high-throughput KDP automation system into a profit-controlled business operation. For the first 90 days, the system must use organic and no-cost KDP levers only. Its primary outcome is positive contribution profit from verified KDP royalties. Fully loaded net profit is tracked separately to decide whether Libra is economically sustainable.

The longer-term objective is to use evidence from this 90-day period to build a credible plan toward THB 100,000 monthly revenue within 12 months. The system must not represent this target as guaranteed.

## Business Truth

Libra maintains two profit views:

### A. Contribution Profit

Used by the agent for title-level and daily operating decisions.

```text
Contribution Profit = verified KDP royalties
                    - title production API cost
                    - title promotion cost
                    - title advertising cost
                    - other directly attributable title cost
```

### B. Fully Loaded Net Profit

Used for monthly portfolio-level decisions.

```text
Fully Loaded Net Profit = Contribution Profit
                        - allocated Newton/server cost
                        - allocated AI subscription cost
                        - other recurring Libra overhead
```

The initial implementation may leave allocated overhead at zero until the user provides actual monthly amounts. It must display this as incomplete, not treat zero as a verified cost.

Verified KDP overview royalties are the financial source of truth. Paid orders, free downloads, KENP, paperback activity, and royalties are separate measures. The system must never infer paid revenue from unit count.

## Operating Constraints

- No Amazon Ads, paid promotion, or other paid acquisition during the initial 90 days.
- New-title generation remains paused until the system identifies repeatable positive contribution profit.
- At most three commercial experiments may run concurrently.
- Each experiment changes one commercial variable at a time.
- Metadata/category changes require at least 72 hours before evaluation.
- Sales and advertising outcomes may require up to 14 days before final evaluation.
- Browser-driven KDP changes require before/after evidence and an audit record.
- An external action is `executed` only when the system records a confirmation identifier, URL, or verified state change.
- Actions requiring login confirmation, CAPTCHA, OTP, unavailable APIs, or user-owned browser context are `manual_required`.

## Initial Experiment Cohort

The agent starts with three evidence-backed candidates:

1. `adhd-self-help-adults-es`: strongest current verified royalty signal.
2. `ai-augmented-productivity-toolkit`: verified royalty exists, but free activity must be separated before scaling.
3. `acuarela-para-principiantes-guia-paso-a-paso`: continued KENP signal with low royalties, suitable for a positioning/read-through test.

The cohort is not permanent. A title may be replaced after its controlled observation window if it fails the defined threshold. Replacement must be based on verified royalties, KENP, paid-order evidence, and data completeness, not LLM market forecasts.

## Architecture

### 1. Financial Ledger

Store immutable dated snapshots of:

- KDP overview royalties, all-type orders/downloads, and KENP
- Per-title attribution returned by KDP
- Unattributed difference between overview and title totals
- Direct production and promotion costs
- Allocated recurring overhead
- Data freshness and reconciliation status

The KDP overview and per-title attribution are separate records because `topEarningTitles` is a partial list. Re-running the same snapshot must be idempotent.

### 2. Experiment Registry

Every experiment contains:

- title/ASIN
- hypothesis
- one variable being changed
- baseline snapshot
- start date and earliest evaluation date
- success threshold
- kill/stop threshold
- maximum direct cost, fixed at zero during the initial 90 days
- status and result

Valid statuses are `planned`, `ready`, `executing`, `cooldown`, `evaluating`, `won`, `lost`, `inconclusive`, and `manual_required`.

### 3. Profit Agent

The agent follows this state machine:

```text
OBSERVE -> DIAGNOSE -> RECOMMEND -> POLICY_CHECK
        -> EXECUTE or MANUAL_REQUIRED
        -> COOLDOWN -> EVALUATE -> KEEP / ITERATE / STOP
```

The agent may autonomously:

- sync and reconcile reports
- rank titles using verified commercial evidence
- create and close zero-cost experiments
- generate compliant metadata recommendations
- schedule eligible guarded free promotions
- prepare organic promotional assets
- publish only through an already verified supported channel
- pause failing jobs or experiments
- send daily and checkpoint reports

The agent may not during the initial 90 days:

- spend money
- enable ads
- create new titles
- claim an unverified manual action was completed
- use modeled revenue as actual revenue
- make multiple simultaneous changes to the same experiment

### 4. Policy Engine

Every proposed action passes deterministic rules before execution:

- financial data is fresh
- KDP overview is reconciled or the attribution gap is disclosed
- no paid-spend action is allowed
- experiment capacity is available
- title is not already in cooldown
- required KDP Select/promotion eligibility is confirmed
- action does not violate the one-variable rule
- action channel supports verified completion

Policy rejection creates an audit event with the reason.

### 5. Reporting

Daily report:

- verified royalties and change
- contribution profit A
- fully loaded net profit B and whether overhead is complete
- reconciliation gap and data freshness
- active experiments and next evaluation date
- actions executed, failed, or requiring user action
- no-spend policy status

Checkpoint reports at days 30, 60, and 90:

- Day 30: ledger accuracy, experiment baseline, removal of false winner signals
- Day 60: whether contribution profit is positive and repeatable
- Day 90: contribution profit result, fully loaded economics, winning segments, and evidence-based 12-month plan

Operations readiness and commercial performance are displayed as separate scores. A perfect operations score cannot produce an `on_track` commercial verdict when profit is behind.

## Success Criteria

### Day 30

- KDP overview royalties reconcile to the ledger within one cent.
- Free activity cannot create verified revenue or a winner label.
- Three or fewer controlled experiments are active.
- All agent actions have truthful execution states.

### Day 60

- At least one title or series has positive contribution profit in two observation windows, or the report states that no winner has been proven.
- The agent has completed at least two clean state-machine cycles without false execution claims.
- No paid spend has occurred.

### Day 90

- Portfolio contribution profit A is positive for a sustained observation window, or Libra explicitly records that the objective was missed.
- Fully loaded net profit B is calculated when overhead amounts are available; otherwise the exact missing inputs are shown.
- The system identifies winning and losing segments from verified evidence.
- The 12-month plan toward THB 100,000 monthly revenue is generated from observed conversion, royalty, production capacity, and cost data.

## Failure Handling

- Stale or incomplete sales data blocks commercial mutations.
- A reconciliation gap does not block reporting but blocks scale decisions.
- A failed external action stays failed and is retried only under a bounded retry policy.
- Authentication or unsupported browser actions become `manual_required` and notify the user once.
- Overlapping changes or experiments are rejected.
- If no title proves positive contribution profit by day 90, the agent recommends pivot, consolidation, or shutdown rather than generating optimistic forecasts.

## Testing Strategy

- Unit tests for free versus paid revenue, profit A/B, reconciliation, idempotency, and stale data
- State-machine tests for every valid transition and policy rejection
- Tests proving an unconfirmed post cannot be marked executed
- Tests for one-variable and three-experiment limits
- Integration tests using captured redacted KDP payload fixtures
- Regression tests for partial `topEarningTitles` responses and title re-entry
- Dry-run production check before enabling each mutating action

## Deployment

Implementation is delivered incrementally:

1. Repair the ledger and commercial truth.
2. Replace winner scoring and dashboard verdicts.
3. Add experiment registry, policy engine, and state machine.
4. Run in shadow mode against live data.
5. Enable zero-cost autonomous actions after shadow verification.
6. Add daily and 30/60/90-day reporting.

Existing production automation remains available, but unsafe or misleading growth actions stay disabled until their corresponding verification gate passes.
