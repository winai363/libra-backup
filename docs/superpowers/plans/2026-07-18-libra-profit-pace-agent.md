# Libra Profit-Pace Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Libra manage daily revenue pace and concentrate zero-cost effort on the highest-evidence titles without weakening KDP safety gates.

**Architecture:** Add a pure decision module that consumes the existing ledger/dashboard title records and returns pace, allocation, and ranked opportunity blocks. Integrate those blocks into the portfolio API and daily agent state, then tighten new free-promotion proposals to require verifiable distribution evidence.

**Tech Stack:** Python 3, FastAPI, SQLite, pytest, existing Libra business ledger and experiment registry.

## Global Constraints

- Verified overview royalties are the portfolio revenue source of truth.
- Internal stretch target is 110%; the approved `$75` target remains unchanged.
- No paid spend, new-title generation, or published-ASIN metadata/category mutation.
- Maximum three active experiments and one variable per experiment.
- Reminder timestamps are not external publication evidence.

---

### Task 1: Pure Profit-Pace Decisions

**Files:**
- Create: `profit_pace.py`
- Create: `tests/test_profit_pace.py`

**Interfaces:**
- Produces: `build_pace_controller(...) -> dict`
- Produces: `classify_portfolio(books: list[dict]) -> dict`
- Produces: `rank_opportunities(books: list[dict]) -> list[dict]`

- [ ] Write failing tests for elapsed pace, recovery/critical/ahead boundaries, 110% stretch target, portfolio buckets, and evidence-based ranking.
- [ ] Run `PYTHONPATH=. pytest tests/test_profit_pace.py -q` and confirm failures are caused by the missing module/functions.
- [ ] Implement deterministic pure functions with no network, file, or database writes.
- [ ] Run the focused tests and confirm they pass.

### Task 2: Portfolio API and Daily Agent Integration

**Files:**
- Modify: `app.py`
- Modify: `scripts/libra_profit_agent_daily.py`
- Modify: `tests/test_profit_api.py`
- Modify: `tests/test_libra_profit_agent_daily.py`

**Interfaces:**
- Consumes: Task 1 decision functions.
- Produces: `pace`, `allocation`, `opportunities`, and `winner_watch` in API/agent state.

- [ ] Write failing API and daily-agent tests asserting the new decision blocks and overview-royalty headline.
- [ ] Run focused tests and confirm the expected missing-key failures.
- [ ] Integrate the pure decision module without changing the experiment state machine or executor gates.
- [ ] Run focused tests and confirm they pass.

### Task 3: Verified Distribution Gate

**Files:**
- Modify: `scripts/experiment_proposer.py`
- Modify: `tests/test_experiment_proposer.py`

**Interfaces:**
- Produces: `distribution_evidence(slug, pairings, schedule) -> dict` with `planned`, `reminded`, or `verified` status.
- Consumes: current pairing and Reddit schedule JSON structures.

- [ ] Write failing tests proving `reminded_at` alone is not verified and `post_url`/`post_id` is verified.
- [ ] Run focused tests and confirm the new proof requirements fail.
- [ ] Require usable distribution evidence for new free-promotion proposals while leaving existing experiment records unchanged.
- [ ] Run proposer tests and confirm they pass.

### Task 4: Plan, Full Verification, and Deployment

**Files:**
- Modify: `plans/2026-07-11-profit-first-kdp-operating-system.md`
- Modify: `memory.md`
- Modify: `/root/memory.md`
- Modify: `/root/telos.md` only if business status materially changes.

- [ ] Update the operating plan with the profit-pace layer, stretch target, portfolio allocation, and distribution proof rule.
- [ ] Run `PYTHONPATH=. pytest -q` and require zero failures.
- [ ] Run `python3 -m py_compile app.py profit_pace.py scripts/libra_profit_agent_daily.py scripts/experiment_proposer.py`.
- [ ] Restart `libra.service`, verify it is active, and inspect `/api/profit/portfolio`.
- [ ] Commit and push only Libra source changes; preserve existing runtime changes in `data/reddit_promo_schedule.json`.
- [ ] Update memory immediately, refresh handoff, and release ownership with `ai-work finish`.

