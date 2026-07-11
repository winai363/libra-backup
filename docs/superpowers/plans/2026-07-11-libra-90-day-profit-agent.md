# Libra 90-Day Profit Agent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and activate a no-paid-spend Libra agent that manages at most three KDP experiments from verified royalties and reports contribution profit and fully loaded net profit over 30/60/90-day checkpoints.

**Architecture:** Add an append-only SQLite business ledger beside the existing KDP JSON state, then make profit reporting and winner selection consume verified ledger data. Add a separate experiment/policy module with a deterministic state machine, and keep `kdp_auto_manager.py` as the orchestrator that writes audited decisions and only executes actions with verifiable completion.

**Tech Stack:** Python 3.12, standard-library `sqlite3`, FastAPI, pytest, existing KDP Playwright/report collectors, JSON-compatible API responses, system cron.

## Global Constraints

- No Amazon Ads, paid promotion, or other paid acquisition during the initial 90 days.
- New-title generation remains paused until repeatable positive contribution profit is proven.
- At most three experiments may be active concurrently.
- One commercial variable may change per experiment.
- Metadata/category changes cool down for at least 72 hours; final commercial evaluation may wait 14 days.
- KDP overview royalties are the portfolio money source of truth; free downloads never imply paid revenue.
- An action is `executed` only with a confirmation ID, URL, or verified external state change; otherwise use `manual_required` or `failed`.
- Use test-first RED -> GREEN cycles and commit after each task.

---

## File Map

- Create `business_ledger.py`: SQLite schema, idempotent snapshot writes, reconciliation, cost/profit calculations.
- Create `profit_agent.py`: experiment registry, policy checks, deterministic transitions, checkpoint summaries.
- Create `tests/test_business_ledger.py`: ledger/reconciliation/profit regression tests.
- Create `tests/test_profit_agent.py`: experiment limits, transitions, no-spend policy, confirmation semantics.
- Modify `kdp_sales_sync.py`: persist overview and partial title attribution without erasing missing baselines.
- Modify `profit_tracker.py`: remove inferred revenue from operational fields and consume verified values.
- Modify `winner_signals.py`: require verified royalties/contribution evidence.
- Modify `distribution_report.py`: separate operations readiness from commercial progress.
- Modify `scripts/kdp_auto_manager.py`: orchestrate policy-controlled experiment cycles and truthful results.
- Modify `app.py` and `templates/profit.html`: expose profit A/B, reconciliation, experiments, and checkpoints.
- Create `scripts/libra_profit_agent_daily.py`: one daily entry point for sync-independent evaluation/reporting.
- Modify tests in `tests/test_profit_tracker.py` and `tests/test_distribution_report.py`.

---

### Task 1: Verified Financial Ledger

**Files:**
- Create: `business_ledger.py`
- Create: `tests/test_business_ledger.py`

**Interfaces:**
- Produces: `init_ledger(path: Path) -> None`
- Produces: `record_kdp_snapshot(path: Path, snapshot: dict) -> int`
- Produces: `portfolio_financials(path: Path, month: str, overhead: dict | None = None) -> dict`
- Snapshot shape: `observed_at`, `month`, `overview`, `titles`; overview contains `royalties_usd`, `orders_all_types`, `kenp`; titles are partial ASIN attribution rows.

- [ ] **Step 1: Write failing tests for idempotency and reconciliation**

```python
from pathlib import Path
from business_ledger import record_kdp_snapshot, portfolio_financials

def test_same_observation_is_idempotent(tmp_path: Path):
    db = tmp_path / "ledger.db"
    snap = {
        "observed_at": "2026-07-11T09:15:09+07:00",
        "month": "2026-07",
        "overview": {"royalties_usd": 7.63, "orders_all_types": 252, "kenp": 361},
        "titles": [{"asin": "A", "royalties_usd": 6.84, "orders": 60, "kenp": 173}],
    }
    first = record_kdp_snapshot(db, snap)
    second = record_kdp_snapshot(db, snap)
    result = portfolio_financials(db, "2026-07")
    assert first == second
    assert result["verified_royalties_usd"] == 7.63
    assert result["attributed_royalties_usd"] == 6.84
    assert result["unattributed_royalties_usd"] == 0.79
    assert result["snapshot_count"] == 1
```

- [ ] **Step 2: Run the test and verify RED**

Run: `PYTHONPATH=. pytest tests/test_business_ledger.py -q`

Expected: FAIL because `business_ledger` does not exist.

- [ ] **Step 3: Implement the minimal SQLite schema and snapshot writer**

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS kdp_snapshots (
  id INTEGER PRIMARY KEY,
  observed_at TEXT NOT NULL UNIQUE,
  month TEXT NOT NULL,
  royalties_usd REAL NOT NULL,
  orders_all_types INTEGER NOT NULL,
  kenp INTEGER NOT NULL,
  raw_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kdp_title_attribution (
  snapshot_id INTEGER NOT NULL,
  asin TEXT NOT NULL,
  royalties_usd REAL NOT NULL,
  orders_count INTEGER NOT NULL,
  kenp INTEGER NOT NULL,
  PRIMARY KEY (snapshot_id, asin),
  FOREIGN KEY(snapshot_id) REFERENCES kdp_snapshots(id)
);
CREATE TABLE IF NOT EXISTS direct_costs (
  id INTEGER PRIMARY KEY,
  incurred_at TEXT NOT NULL,
  slug TEXT,
  category TEXT NOT NULL,
  amount_usd REAL NOT NULL,
  source_key TEXT NOT NULL UNIQUE
);
"""
```

Use `INSERT ... ON CONFLICT(observed_at) DO UPDATE` inside one transaction, replace attribution rows for that snapshot, and compute reconciliation only from the newest snapshot in the month.

- [ ] **Step 4: Add failing profit A/B tests**

```python
def test_profit_a_and_b_keep_unknown_overhead_incomplete(tmp_path):
    db = tmp_path / "ledger.db"
    record_kdp_snapshot(db, {
        "observed_at": "2026-07-11T09:15:09+07:00", "month": "2026-07",
        "overview": {"royalties_usd": 10.0, "orders_all_types": 4, "kenp": 20},
        "titles": [],
    })
    result = portfolio_financials(db, "2026-07")
    assert result["contribution_profit_usd"] == 10.0
    assert result["fully_loaded_net_profit_usd"] is None
    assert result["overhead_complete"] is False
```

- [ ] **Step 5: Implement direct cost ingestion and overhead calculation**

Add `record_direct_cost(path, *, incurred_at, slug, category, amount_usd, source_key)`. `portfolio_financials(..., overhead=None)` returns incomplete B; complete overhead keys are `newton_server_usd`, `ai_subscription_usd`, and `other_usd`.

- [ ] **Step 6: Run tests and commit**

Run: `PYTHONPATH=. pytest tests/test_business_ledger.py -q`

Expected: all tests PASS.

```bash
git add business_ledger.py tests/test_business_ledger.py
git commit -m "Add verified KDP business ledger"
```

### Task 2: Preserve KDP Overview and Partial Title Baselines

**Files:**
- Modify: `kdp_sales_sync.py:214-330`
- Create: `tests/test_kdp_sales_sync.py`

**Interfaces:**
- Consumes: `record_kdp_snapshot()` from Task 1.
- Produces: `merge_title_baselines(previous: dict, current_rows: list[dict]) -> dict`.
- Produces: `ledger_snapshot_from_kdp(data: dict, observed_at: str) -> dict`.

- [ ] **Step 1: Write regression tests for partial rows and re-entry**

```python
def test_partial_top_titles_preserves_missing_baseline():
    previous = {"A": {"orders": 5}, "B": {"orders": 7}}
    merged = merge_title_baselines(previous, [{"asin": "A", "orders": 6}])
    assert merged["A"]["orders"] == 6
    assert merged["B"]["orders"] == 7

def test_overview_snapshot_keeps_total_separate_from_attribution():
    snap = ledger_snapshot_from_kdp({
        "overview": {"digitalOrders": 252, "kenpRead": 361, "totalRoyalties": 7.63, "currency": "USD"},
        "titles": [{"asin": "A", "orders": 60, "pagesRead": 173, "royalties": 6.84, "currency": "USD"}],
    }, "2026-07-11T09:15:09+07:00")
    assert snap["overview"]["royalties_usd"] == 7.63
    assert snap["titles"][0]["royalties_usd"] == 6.84
```

- [ ] **Step 2: Run tests and verify RED**

Run: `PYTHONPATH=. pytest tests/test_kdp_sales_sync.py -q`

Expected: FAIL because the helper functions do not exist.

- [ ] **Step 3: Implement pure merge/conversion helpers**

Preserve absent ASIN baselines, replace only ASINs present in the current partial response, and convert both overview and title royalties through `_to_usd`.

- [ ] **Step 4: Persist the snapshot before feedback-history updates**

Set `LEDGER_FILE = LIBRA_DIR / "data" / "libra-business.db"`. In `sync()`, call `record_kdp_snapshot(LEDGER_FILE, ledger_snapshot_from_kdp(data, observed_at))`; in `--dry-run`, build and print reconciliation input but do not write.

- [ ] **Step 5: Run focused and existing sync-related tests**

Run: `PYTHONPATH=. pytest tests/test_kdp_sales_sync.py tests/test_profit_tracker.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add kdp_sales_sync.py tests/test_kdp_sales_sync.py
git commit -m "Preserve KDP financial source truth"
```

### Task 3: Remove False Revenue and Winner Signals

**Files:**
- Modify: `profit_tracker.py:87-250`
- Modify: `winner_signals.py:45-90`
- Modify: `tests/test_profit_tracker.py`
- Create: `tests/test_winner_signals.py`

**Interfaces:**
- Consumes: `portfolio_financials()` from Task 1.
- `build_portfolio()` returns `verified_royalties_mtd_usd`, `contribution_profit_usd`, `fully_loaded_net_profit_usd`, `overhead_complete`, and `reconciliation`.
- `get_winners()` requires positive verified royalty and positive contribution when attributable costs are known.

- [ ] **Step 1: Replace the old estimation test with a failing zero-royalty regression**

```python
def test_free_units_never_create_revenue_or_winner(tmp_path, monkeypatch):
    monkeypatch.setattr(profit_tracker, "KDP_DIR", tmp_path)
    write_json(tmp_path / "free-book" / "listing.json", {
        "title": "Free Book", "status": "uploaded", "uploaded_at": "2026-07-01",
    })
    write_json(tmp_path / "free-book" / "feedback-history.json", [{
        "date": "2026-07-10", "units_7d": 17, "kenp_7d": 0,
        "revenue_usd": 0.0,
    }])
    book = profit_tracker.build_portfolio(today=date(2026, 7, 11))["books"][0]
    assert book["totals_30d"]["verified_revenue_usd"] == 0.0
    assert book["action"] != "winner"
```

- [ ] **Step 2: Run and verify RED**

Run: `PYTHONPATH=. pytest tests/test_profit_tracker.py -q`

Expected: FAIL because current code estimates revenue from units.

- [ ] **Step 3: Implement verified-only operational fields**

Delete unit/price fallback from `_estimated_revenue`; rename output to `verified_revenue_usd`. Optional modeled values may remain only under `modeled_revenue_usd` and must not feed action/ranking logic. A winner requires `verified_revenue_usd > 0` and positive contribution where cost attribution is available.

- [ ] **Step 4: Write and satisfy winner-signal regression**

```python
def test_zero_royalty_units_are_not_proven_winner(tmp_path, monkeypatch):
    monkeypatch.setattr(winner_signals, "KDP_DIR", tmp_path)
    write_json(tmp_path / "free-book" / "listing.json", {"title": "Free Book"})
    write_json(tmp_path / "free-book" / "feedback-history.json", [{
        "date": "2026-07-10", "units_7d": 20, "kenp_7d": 0,
        "revenue_usd": 0.0,
    }])
    assert winner_signals.get_winners(today=date(2026, 7, 11)) == []
```

Update prompt wording from “sale(s)” to “orders/downloads” unless verified royalty is positive.

- [ ] **Step 5: Run tests and commit**

Run: `PYTHONPATH=. pytest tests/test_profit_tracker.py tests/test_winner_signals.py -q`

```bash
git add profit_tracker.py winner_signals.py tests/test_profit_tracker.py tests/test_winner_signals.py
git commit -m "Use verified royalties for KDP winners"
```

### Task 4: Experiment Registry and No-Spend Policy Engine

**Files:**
- Create: `profit_agent.py`
- Create: `tests/test_profit_agent.py`

**Interfaces:**
- Produces: `create_initial_experiments(db_path: Path, now: datetime) -> list[dict]`.
- Produces: `propose_transition(experiment: dict, financials: dict, now: datetime) -> dict`.
- Produces: `check_policy(action: dict, context: dict) -> tuple[bool, str]`.
- Produces: `record_action_result(db_path: Path, action: dict, result: dict) -> dict`.

- [ ] **Step 1: Write failing policy tests**

```python
def test_paid_action_is_blocked_during_90_day_mode():
    allowed, reason = check_policy({"kind": "amazon_ads", "cost_usd": 1}, {"no_spend": True})
    assert allowed is False
    assert reason == "paid actions disabled during 90-day organic mode"

def test_fourth_active_experiment_is_blocked():
    allowed, reason = check_policy({"kind": "start_experiment"}, {"active_experiments": 3})
    assert allowed is False
    assert reason == "active experiment limit reached"

def test_unconfirmed_external_action_is_manual_required(tmp_path):
    db = tmp_path / "ledger.db"
    action = {"kind": "free_post", "slug": "adhd-self-help-adults-es"}
    result = record_action_result(db, action, {"returncode": 0})
    assert result["status"] == "manual_required"
```

- [ ] **Step 2: Run and verify RED**

Run: `PYTHONPATH=. pytest tests/test_profit_agent.py -q`

Expected: FAIL because `profit_agent` does not exist.

- [ ] **Step 3: Implement schema and deterministic policy checks**

Add `experiments` and `agent_actions` tables to the same SQLite file. Active statuses are `planned`, `ready`, `executing`, `cooldown`, `evaluating`, `manual_required`. Seed exactly the three approved slugs with `max_direct_cost_usd=0`.

- [ ] **Step 4: Implement state transitions and cooldowns**

Allow only:

```python
TRANSITIONS = {
    "planned": {"ready"}, "ready": {"executing", "manual_required"},
    "executing": {"cooldown", "failed", "manual_required"},
    "cooldown": {"evaluating"},
    "evaluating": {"won", "lost", "inconclusive"},
}
```

Use 72 hours for metadata/category and 14 days when `evaluation_kind == "commercial"`.

- [ ] **Step 5: Run tests and commit**

Run: `PYTHONPATH=. pytest tests/test_profit_agent.py -q`

```bash
git add profit_agent.py tests/test_profit_agent.py
git commit -m "Add Libra profit experiment controller"
```

### Task 5: Truthful Profit Agent Orchestration

**Files:**
- Modify: `scripts/kdp_auto_manager.py:35-119`
- Create: `scripts/libra_profit_agent_daily.py`
- Modify: `tests/test_distribution_report.py`
- Create: `tests/test_kdp_auto_manager.py`

**Interfaces:**
- Consumes ledger and policy interfaces from Tasks 1 and 4.
- Daily script produces `data/profit-agent-state.json` and appends audited SQLite actions.
- Existing `free_post` without a post ID becomes `manual_required`.

- [ ] **Step 1: Write failing action-truth test**

```python
def test_free_post_without_external_evidence_is_not_executed():
    state = {"agent": {"free_growth_engine": {"decisions": [{
        "action": "free_post", "channel": "Pinterest/Reddit", "execute": True,
    }]}}}
    results = execute_free_actions(state)
    assert results[0]["status"] == "manual_required"
```

- [ ] **Step 2: Run and verify RED**

Run: `PYTHONPATH=. pytest tests/test_kdp_auto_manager.py -q`

Expected: FAIL because current result is `sent_digest`.

- [ ] **Step 3: Make action results evidence-based**

Require one of `confirmation_id`, `external_url`, or `verified_state_change=True` for `executed`. Store stdout/stderr only as diagnostics, not completion proof.

- [ ] **Step 4: Add daily controller entry point**

`libra_profit_agent_daily.py` loads current ledger financials, seeds/advances experiments, applies policy, writes state atomically, and optionally sends the existing Telegram digest. It must support `--dry-run` and `--send`.

- [ ] **Step 5: Split readiness and commercial verdict**

Change `distribution_report.build_monitor()` to emit:

```python
{
  "operations": {"score": 100, "status": "ready"},
  "commercial": {"status": "behind", "verified_royalties_usd": 7.63,
                 "contribution_profit_usd": -1.81},
}
```

Revenue pace below target cannot be `on_track` merely because royalties are above zero.

- [ ] **Step 6: Run tests and commit**

Run: `PYTHONPATH=. pytest tests/test_kdp_auto_manager.py tests/test_distribution_report.py tests/test_profit_agent.py -q`

```bash
git add scripts/kdp_auto_manager.py scripts/libra_profit_agent_daily.py distribution_report.py tests/test_kdp_auto_manager.py tests/test_distribution_report.py
git commit -m "Activate truthful Libra profit agent cycle"
```

### Task 6: Profit Dashboard and 30/60/90-Day Reports

**Files:**
- Modify: `app.py:112-210,778-807`
- Modify: `templates/profit.html`
- Create: `tests/test_profit_api.py`

**Interfaces:**
- `GET /api/profit/portfolio` returns `financials`, `experiments`, `operations`, `commercial`, and `checkpoints`.
- `GET /api/profit/agent` returns the latest state without secrets or raw session data.

- [ ] **Step 1: Write failing API contract tests**

```python
def test_profit_api_separates_a_and_b(client):
    payload = client.get("/api/profit/portfolio").json()
    assert "contribution_profit_usd" in payload["financials"]
    assert "fully_loaded_net_profit_usd" in payload["financials"]
    assert "overhead_complete" in payload["financials"]
    assert payload["policy"]["paid_spend_allowed"] is False
```

- [ ] **Step 2: Run and verify RED**

Run: `PYTHONPATH=. pytest tests/test_profit_api.py -q`

Expected: FAIL because the API lacks the new contract.

- [ ] **Step 3: Implement API fields and dashboard sections**

Show Profit A, Profit B or “overhead incomplete”, verified royalties, unattributed gap, data age, active experiment count, next evaluation, no-spend status, and day 30/60/90 dates. Remove estimated revenue from the primary KPI area.

- [ ] **Step 4: Add checkpoint outcome generation**

Day 30 reports reconciliation and false-signal removal; day 60 reports two-window contribution evidence; day 90 reports objective achieved/missed and the data needed for the 12-month THB 100,000/month plan.

- [ ] **Step 5: Run tests and commit**

Run: `PYTHONPATH=. pytest tests/test_profit_api.py tests/test_profit_tracker.py tests/test_distribution_report.py -q`

```bash
git add app.py templates/profit.html tests/test_profit_api.py
git commit -m "Show Libra profit agent business truth"
```

### Task 7: Shadow Verification, Activation, and Operations Hardening

**Files:**
- Modify: cron using system crontab
- Modify: `/root/memory.md` after production verification

**Interfaces:**
- Daily command: `cd /root/libra && /usr/bin/python3 scripts/libra_profit_agent_daily.py --send`.
- State file: `data/profit-agent-state.json`.
- Ledger: `data/libra-business.db`.

- [ ] **Step 1: Run the complete test and syntax suite**

```bash
cd /root/libra
PYTHONPATH=. pytest -q
python3 -m py_compile business_ledger.py profit_agent.py kdp_sales_sync.py profit_tracker.py distribution_report.py app.py scripts/kdp_auto_manager.py scripts/libra_profit_agent_daily.py
```

Expected: all tests PASS and compilation exits 0.

- [ ] **Step 2: Back up runtime state and run a dry-run cycle**

```bash
cp /root/kdp/sales-sync-state.json /root/kdp/sales-sync-state.json.bak-profit-agent-20260711
python3 scripts/libra_profit_agent_daily.py --dry-run
```

Expected: no KDP mutation, no spend, three or fewer experiments, and reconciliation gap shown explicitly.

- [ ] **Step 3: Run one shadow write cycle**

Run: `python3 scripts/libra_profit_agent_daily.py`

Expected: ledger/state updated locally; no external commercial mutation; actions are `planned`, `cooldown`, `manual_required`, or verified `executed`.

- [ ] **Step 4: Install the daily cron without duplicating it**

Add at 10:15 after sales sync/report generation:

```cron
15 10 * * * cd /root/libra && /usr/bin/python3 scripts/libra_profit_agent_daily.py --send >> /root/libra/logs/profit-agent.log 2>&1
```

Keep the new-title generation cron commented. Disable the old 10:05 auto-manager mutation cron only after the new shadow output is verified.

- [ ] **Step 5: Secure session state and restart dashboard**

```bash
chmod 600 /root/libra/kdp_session_aplus.json
systemctl restart libra.service
systemctl is-active libra.service
curl -fsS http://127.0.0.1:8200/api/profit/portfolio | jq '.financials,.policy,.experiments'
```

Expected: service `active`, endpoint HTTP 200, paid spend false, experiment count at most three.

- [ ] **Step 6: Verify production truth and logs**

```bash
tail -20 logs/sales-sync.log
tail -50 logs/profit-agent.log
sqlite3 data/libra-business.db 'select observed_at, royalties_usd, orders_all_types, kenp from kdp_snapshots order by id desc limit 3;'
crontab -l | rg 'libra_profit_agent_daily|auto-generate|kdp_auto_manager'
```

Confirm KDP overview royalties equal the ledger within one cent, no paid cron exists, and no action is falsely marked executed.

- [ ] **Step 7: Update memory, commit, and push**

Record activation date, initial A/B values, reconciliation gap, cohort, cron, tests, and remaining unknown overhead inputs in `/root/memory.md` and project memory where appropriate.

Push all verified Task 1-7 commits:

```bash
git status --short
git push backup main
```

## Final Acceptance Gate

- Full test suite passes.
- Verified royalty total matches the latest KDP overview within $0.01.
- Free downloads cannot create revenue or winner status.
- Contribution Profit A is displayed; Profit B is explicitly incomplete until overhead is supplied.
- No paid action is allowed for 90 days.
- Exactly three or fewer experiments are active and each changes one variable.
- Unconfirmed external work is `manual_required`, never `executed`.
- Operations readiness and commercial performance are separate.
- Daily agent cron is live, non-duplicated, and observable.
