# Libra Risk-Aware Recovery Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn KDP category notices and flat revenue into explicit, safe recovery signals.

**Architecture:** Store verified notices in a small JSON registry, merge them into category health, and calculate revenue stalls from the existing ledger. The daily agent consumes both signals without changing live metadata.

**Tech Stack:** Python 3, JSON, SQLite, pytest

## Global Constraints

- Never mutate a LIVE KDP listing.
- Never re-suggest a category removed by KDP for the same ASIN.
- Keep paid spend disabled.
- External state changes require browser/API evidence.

---

### Task 1: KDP Metadata Incident Registry

**Files:**
- Create: `data/kdp_metadata_incidents.json`
- Modify: `category_health_manager.py`
- Create: `tests/test_category_health_manager.py`

**Interfaces:**
- Consumes: incident records with `asin`, `category`, `noticed_at`, and `source`
- Produces: `load_metadata_incidents()` and report fields `metadata_risk`, `metadata_incidents`

- [x] Write tests proving a notice prevents `ok` and blacklists removed categories.
- [x] Run the focused test and confirm it fails because incident support is absent.
- [x] Implement incident loading and report integration without KDP mutation.
- [x] Run the focused tests and confirm they pass.

### Task 2: Revenue Stall Signal

**Files:**
- Modify: `profit_pace.py`
- Modify: `scripts/libra_profit_agent_daily.py`
- Modify: `tests/test_profit_pace.py`
- Modify: `tests/test_libra_profit_agent_daily.py`

**Interfaces:**
- Produces: `detect_revenue_stall(rows, minimum_days=3, growth_threshold_usd=0.25)`
- Agent state: `recovery_signals.revenue_stall` and `recovery_signals.metadata_risk`

- [x] Write tests for flat and growing three-day windows.
- [x] Run the focused tests and confirm the missing signal fails.
- [x] Implement the pure detector and connect current health state to the daily agent.
- [x] Include both signals in the Telegram digest.
- [x] Run focused tests and confirm they pass.

### Task 3: Runtime Verification And Documentation

**Files:**
- Modify: `memory.md`
- Modify: `/root/memory.md`

- [x] Run the category health manager and daily agent against production data.
- [x] Confirm no KDP mutation command ran and inspect generated JSON.
- [x] Run the complete Libra test suite.
- [x] Record the evidence and operating decision in memory.
- [ ] Commit only task-related files and release the `ai-work` lock.
