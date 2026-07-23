# Libra Critical Distribution Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Libra Profit Agent turn a critical revenue pace with unproved free-promo candidates into explicit, auditable distribution recovery work without weakening KDP safety gates.

**Architecture:** Extend the deterministic proposer with a blocker funnel and first-class `distribution_required` recovery items that never mutate KDP. Run the proposer before the final atomic state write and digest so production state exposes the real next action. Keep verified external proof as the only path that promotes a title into a free-promo experiment.

**Tech Stack:** Python 3.12, SQLite, pytest, JSON state files, system cron.

## Global Constraints

- Keep paid spend disabled throughout `organic_90_day`.
- Never republish or mutate metadata/categories for a listing with an ASIN.
- A reminder, declaration, or planned post is not external distribution proof.
- Only a real `post_url` or `post_id` can unlock a new free promo.
- Do not blindly increase cron frequency; recovery progresses on evidence changes.
- Limit KDP mutations to the existing experiment and executor caps.

---

### Task 1: Blocker Funnel And Distribution Recovery Items

**Files:**
- Modify: `scripts/experiment_proposer.py`
- Test: `tests/test_experiment_proposer.py`

**Interfaces:**
- Consumes: live KDP listings, experiment ledger, distribution evidence.
- Produces: `blocker_funnel` counts and `distribution_required` recovery items in `run_proposer()` output.

- [x] Write failing tests proving unverified promo candidates produce recovery items but no experiments.
- [x] Run `pytest tests/test_experiment_proposer.py -q` and confirm the new assertions fail.
- [x] Add a deterministic proposal analysis that counts raw candidates, missing proof, safe executable candidates, and returns bounded distribution recovery items.
- [x] Preserve existing `gather_proposals()` proof and executor gates.
- [x] Run the focused tests and confirm they pass.

### Task 2: Persist Final Agent State And Actionable Digest

**Files:**
- Modify: `scripts/libra_profit_agent_daily.py`
- Test: `tests/test_libra_profit_agent_daily.py`

**Interfaces:**
- Consumes: `run_daily()` state and proposer result.
- Produces: a final atomic state file containing `proposer`, plus a digest containing pace, blocker counts, and next recovery action.

- [x] Write failing tests proving the final state file includes proposer output and critical next action.
- [x] Run the focused test and confirm failure.
- [x] Move orchestration into a testable controller that runs proposer before the final atomic write and notification.
- [x] Ensure dry runs remain write-free and execution errors remain fail-soft.
- [x] Run focused tests and confirm pass.

### Task 3: Verification And Production Activation

**Files:**
- Modify only if required by verified behavior: system cron.
- Update: `/root/memory.md`

**Interfaces:**
- Consumes: production ledger, KDP listings, current cron.
- Produces: fresh production state and Telegram digest; no unsafe KDP mutation.

- [x] Run focused tests for proposer, daily controller, executor, and policy.
- [x] Run the full Libra test suite.
- [x] Run the production controller once with `--send --execute-actions`.
- [x] Verify state exposes the blocker funnel and recovery item while paid and republish gates remain closed.
- [x] Confirm the existing daily cron is sufficient; do not add blind reruns.
- [x] Record the verified result in `memory.md`, commit only task files, and push to the configured backup remote if available.
