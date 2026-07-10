# Libra Distribution Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-glance monitor for the active Libra July distribution experiment.

**Architecture:** Reuse the existing `distribution_report.py` data model as source of truth, then derive monitor status, blockers, and recommendation from the report plus dashboard overview/category health. Expose the monitor through `/api/distribution/monitor` and `/distribution/monitor`.

**Tech Stack:** FastAPI, plain Python report builders, server-rendered HTML, pytest.

## Global Constraints

- Keep KDP royalties as the money source of truth; orders/downloads are not revenue.
- Do not create new paid promotion or ad automation.
- Keep the UI read-only and public like the existing distribution report.
- Do not add a top-level `/api/` nginx rule; this is an in-app route only.

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

**Interfaces:**
- Produces: `GET /api/distribution/monitor`
- Produces: `GET /distribution/monitor`

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
