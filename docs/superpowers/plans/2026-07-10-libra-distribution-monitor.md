# Libra Distribution Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-glance monitor for the active Libra July distribution experiment.

**Architecture:** Reuse the existing `distribution_report.py` data model as source of truth, then derive monitor status, blockers, Actual vs Plan targets, and recommendation from the report plus dashboard overview/category health. Expose the monitor through `/api/distribution/monitor`, `/distribution/monitor`, and a read-only KDP agent API.

**Tech Stack:** FastAPI, plain Python report builders, server-rendered HTML, pytest.

## Global Constraints

- Keep KDP royalties as the money source of truth; orders/downloads are not revenue.
- Do not create new paid promotion or ad automation.
- Keep the UI read-only and public like the existing distribution report.
- Do not add a top-level `/api/` nginx rule; this is an in-app route only.
- KDP Auto Manager may recommend actions but must not mutate KDP, buy paid promo, change pricing, or publish books automatically.

---

### Task 1: Monitor Builder And Tests

**Files:**
- Modify: `distribution_report.py`
- Modify: `tests/test_distribution_report.py`

**Interfaces:**
- Produces: `build_monitor(report: dict, *, overview: dict | None = None, category_health: dict | None = None) -> dict`
- Produces: `render_monitor_html(monitor: dict) -> str`

- [x] **Step 1: Write failing tests**

Add tests that require the monitor to summarize on-track status, setup completion, Pinterest progress, blockers, and decision guidance.

- [x] **Step 2: Run test to verify it fails**

Run:

```bash
PYTHONPATH=. pytest tests/test_distribution_report.py::test_build_monitor_summarizes_on_track_distribution_plan tests/test_distribution_report.py::test_render_monitor_html_contains_status_and_next_actions -q
```

Expected: fail because `build_monitor` and `render_monitor_html` do not exist.

- [x] **Step 3: Implement monitor builder and HTML renderer**

Add status scoring, blocker calculation, system health fields, and a responsive read-only HTML monitor.

- [x] **Step 4: Run test to verify it passes**

Run the same focused pytest command.

Expected: `2 passed`.

### Task 2: FastAPI Routes

**Files:**
- Modify: `app.py`
- Create: `scripts/kdp_auto_manager.py`
- Modify: `.gitignore`

**Interfaces:**
- Produces: `GET /api/distribution/monitor`
- Produces: `GET /distribution/monitor`
- Produces: `GET /api/kdp-agent`
- Produces: daily cron `5 10 * * * cd /root/libra && /usr/bin/python3 scripts/kdp_auto_manager.py >> /root/libra/logs/kdp-agent.log 2>&1`

- [x] **Step 1: Add route implementation**

Wire routes to `build_report()`, `build_dashboard_overview()`, category health state, and the monitor renderer.

- [x] **Step 2: Verify route locally**

Run:

```bash
python3 -m py_compile app.py distribution_report.py
sudo systemctl restart libra.service
curl -sS http://127.0.0.1:8200/api/distribution/monitor
curl -sS http://127.0.0.1:8200/distribution/monitor
```

Expected: service active, JSON includes `overall`, HTML includes `Libra Monitor`.

### Task 3: Actual Vs Plan And KDP Agent

**Files:**
- Modify: `distribution_report.py`
- Modify: `tests/test_distribution_report.py`

**Interfaces:**
- Produces: `monitor["actual_vs_plan"]` with target metrics and role verdicts
- Produces: `monitor["kdp_agent"]` with agent mode, guardrails, and next actions

- [x] **Step 1: Write failing tests**

Extend monitor tests to require:

```python
assert monitor["actual_vs_plan"]["metrics"][0]["name"] == "Revenue"
assert monitor["actual_vs_plan"]["roles"]["CFO"]["status"] == "early"
assert "อย่าเพิ่งซื้อ paid promo" in monitor["kdp_agent"]["next_actions"][0]
assert "bar-fill" in dr.render_monitor_html(monitor)
```

- [x] **Step 2: Implement targets and role logic**

Add conservative learning targets for the 2026-07-31 checkpoint:

```python
DEFAULT_PLAN_TARGETS = {
    "revenue_usd": 25.0,
    "orders_downloads": 120,
    "kenp": 500,
    "free_downloads": 100,
}
```

- [x] **Step 3: Render bar chart and role cards**

Add `Actual vs Plan`, bar chart rows, CFO/COO/CMO/KDP Strategist cards, and KDP Auto Manager next actions to the monitor HTML.

- [x] **Step 4: Add read-only agent endpoint and cron refresh**

Add `/api/kdp-agent`, `scripts/kdp_auto_manager.py`, and system cron at 10:05 daily. The cron only writes advisory state; it does not mutate KDP.
