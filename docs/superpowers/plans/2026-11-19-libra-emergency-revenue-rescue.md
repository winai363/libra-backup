# Libra Emergency Revenue Rescue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Restore continuous Search Console evidence, produce evidence-backed retail-readiness verdicts for the four candidate books, and either launch one bounded Amazon Sponsored Products pilot safely or close it as `BLOCKED_WITH_EVIDENCE`.

**Architecture:** Keep the existing Day-7 checkpoint and add a separate weekly scheduling decision around the same collector. Add a read-only retail-readiness module that combines local artifact checks with dated public/account observations. Add an Amazon pilot gate that emits a deterministic draft/missing-input report and never writes remotely unless an explicitly injected adapter proves the exact write and readback capabilities.

**Tech Stack:** Python 3, pytest, JSON/JSONL state, SQLite business ledger, Google Search Console API, existing Libra scripts.

## Global Constraints

- Preserve TOTAL KDP FREEZE except for the explicitly approved Amazon Sponsored Products pilot.
- Do not modify KDP price, metadata, cover, manuscript, category, appeal, publication state, or create a new book.
- Do not restart Pinterest, LinkedIn routine, email outreach, or another paid channel.
- The pilot ceiling is USD 10 total, USD 2 daily, one ASIN, and at most 14 days.
- A provider error or non-final Search Console window must stay UNKNOWN; never convert it to zero.
- Do not store credentials, cookies, tokens, or customer data in the repository or logs.
- Amazon writes require a fresh account snapshot, exact operation capability, owner-approved mutation plan, idempotency key, audit destination, reversible pause, and remote readback.

---

### Task 1: Rolling Search Console monitor

**Files:**
- Modify: `scripts/search_organic_checkpoint.py`
- Modify: `scripts/search_organic_report.py`
- Test: `tests/test_search_organic_checkpoint.py`
- Test: `tests/test_search_organic_report.py`

**Interfaces:**
- Produces: `rolling_window(as_of: date) -> tuple[date, date]`, `weekly_collection_due(state: dict, as_of: date) -> bool`, and `latest_search_console_snapshot(path: Path, as_of: date, stale_after_days: int = 9) -> dict`.
- Persists: `state["rolling"]` with the last successful final-data observation and appends schema `libra-search-console-snapshot/2` only for useful observations.

- [x] **Step 1: Write failing scheduling tests**

```python
def test_completed_day7_state_collects_again_when_weekly_due(tmp_path, monkeypatch):
    state.write_text(json.dumps({"sitemap": {"state": "CONFIRMED"}, "checkpoint": {"state": "SENT"}, "metrics": {"state": "FINAL"}, "rolling": {"last_final_on": "2026-11-12"}}))
    assert checkpoint.main(common_for(date(2026, 11, 19))) == 0
    assert calls == [1]

def test_completed_day7_state_noops_before_weekly_due(tmp_path, monkeypatch):
    assert checkpoint.main(common_for(date(2026, 11, 18))) == 0
    assert calls == []
```

- [x] **Step 2: Run tests and confirm RED**

Run: `python3 -m pytest -q tests/test_search_organic_checkpoint.py -k 'weekly_due or completed_day7'`

Expected: FAIL because the current `monitor_complete` branch stops forever.

- [x] **Step 3: Implement rolling scheduling and dynamic 28-day final window**

```python
def rolling_window(as_of: date) -> tuple[date, date]:
    end = as_of - timedelta(days=3)
    return end - timedelta(days=27), end

def weekly_collection_due(state: dict, as_of: date) -> bool:
    raw = ((state.get("rolling") or {}).get("last_final_on"))
    if not raw:
        return True
    try:
        return as_of >= date.fromisoformat(raw) + timedelta(days=7)
    except (TypeError, ValueError):
        return True
```

Use the fixed release window only until the Day-7 checkpoint becomes final. Thereafter query `rolling_window`, retain old snapshots on provider failure, and send at most one weekly owner message per final observation.

- [x] **Step 4: Add latest snapshot reader tests**

```python
def test_report_uses_latest_fresh_final_search_console_snapshot(tmp_path):
    snapshots.write_text(json.dumps({"observed_on": "2026-11-18", "search_console": {"status": "CONNECTED", "metrics": {"impressions": 4, "clicks": 1}}}) + "\n")
    assert latest_search_console_snapshot(snapshots, date(2026, 11, 19))["status"] == "AVAILABLE"

def test_report_marks_stale_or_malformed_snapshot_unknown(tmp_path):
    assert latest_search_console_snapshot(snapshots, date(2026, 11, 19))["status"] == "UNKNOWN"
```

- [x] **Step 5: Implement report integration and run focused tests**

Run: `python3 -m pytest -q tests/test_search_organic_checkpoint.py tests/test_search_organic_report.py tests/test_libra_search_console.py`

Expected: PASS.

- [x] **Step 6: Commit Task 1 files only**

```bash
git add scripts/search_organic_checkpoint.py scripts/search_organic_report.py tests/test_search_organic_checkpoint.py tests/test_search_organic_report.py
git commit -m "fix: keep Libra Search Console monitoring active"
```

---

### Task 2: Product retail-readiness QA

**Files:**
- Create: `libra_retail_readiness.py`
- Create: `scripts/libra_retail_readiness.py`
- Create: `data/retail_readiness_evidence.json`
- Create: `data/retail_readiness_latest.json`
- Test: `tests/test_libra_retail_readiness.py`

**Interfaces:**
- Consumes: local `/root/kdp/<slug>/listing.json`, EPUB/PDF/cover/A+ artifact metadata, ledger monthly results, and dated public/account evidence rows.
- Produces: `audit_candidate(slug: str, evidence: dict, kdp_root: Path, ledger: Path) -> dict` with verdict `PASS`, `HOLD`, or `UNKNOWN`, per-control evidence, freshness, and blockers.

- [x] **Step 1: Write failing verdict tests**

```python
def test_missing_live_or_price_evidence_is_unknown(candidate):
    result = audit_candidate(candidate, evidence={}, kdp_root=kdp_root, ledger=ledger)
    assert result["verdict"] == "UNKNOWN"

def test_visual_promise_without_matching_interior_is_hold(candidate):
    evidence = live_evidence(candidate, promise_visuals=True, interior_visuals=0)
    assert audit_candidate(candidate, evidence, kdp_root, ledger)["verdict"] == "HOLD"

def test_pass_requires_live_price_url_content_rights_and_policy(candidate):
    result = audit_candidate(candidate, complete_pass_evidence(), kdp_root, ledger)
    assert result["verdict"] == "PASS"
```

- [x] **Step 2: Run tests and confirm RED**

Run: `python3 -m pytest -q tests/test_libra_retail_readiness.py`

Expected: ERROR because the module does not exist.

- [x] **Step 3: Implement minimal deterministic auditor**

```python
REQUIRED_CONTROLS = ("live", "marketplace", "price", "landing", "promise_match", "language", "claims", "rights", "policy_eligibility")

def verdict_for(controls: dict) -> str:
    if any(row.get("result") == "fail" for row in controls.values()):
        return "HOLD"
    if any(controls.get(name, {}).get("result") != "pass" for name in REQUIRED_CONTROLS):
        return "UNKNOWN"
    return "PASS"
```

The CLI writes one atomic JSON report, never edits book artifacts, and records source paths/dates without secret values.

- [x] **Step 4: Collect dated evidence and generate the four verdicts**

Run: `python3 scripts/libra_retail_readiness.py --evidence data/retail_readiness_evidence.json --output data/retail_readiness_latest.json`

Expected: Three candidate verdicts plus `senior-smartphone-french=HOLD`; every non-PASS verdict names exact missing or failed controls.

- [x] **Step 5: Run focused tests and validate output schema**

Run: `python3 -m pytest -q tests/test_libra_retail_readiness.py`

Run: `python3 -m json.tool data/retail_readiness_latest.json >/dev/null`

Expected: PASS and valid JSON.

- [x] **Step 6: Commit Task 2 files only**

```bash
git add libra_retail_readiness.py scripts/libra_retail_readiness.py tests/test_libra_retail_readiness.py data/retail_readiness_evidence.json data/retail_readiness_latest.json
git commit -m "feat: audit Libra retail readiness"
```

---

### Task 3: Amazon Sponsored Products pilot gate

**Files:**
- Create: `amazon_ads_pilot.py`
- Create: `scripts/amazon_ads_pilot.py`
- Create: `data/amazon_ads_pilot_inputs.json`
- Create: `data/amazon_ads_pilot_latest.json`
- Test: `tests/test_amazon_ads_pilot.py`

**Interfaces:**
- Consumes: retail-readiness report plus account/profile/capability snapshot containing only non-secret references.
- Produces: `build_pilot_gate(inputs: dict, retail: dict, as_of: datetime) -> dict` with status `DRAFT_READY`, `BLOCKED_WITH_EVIDENCE`, or `APPLY_READY`; exact mutation plan is capped at USD 10 total, USD 2/day, 14 days, one ASIN.
- Apply boundary: `apply_pilot(plan: dict, adapter) -> dict` accepts only an adapter whose manifest marks create/pause/readback exact operations as `live-write-verified` and returns verified remote before/after evidence.

- [x] **Step 1: Write failing gate tests**

```python
def test_missing_account_or_adapter_blocks_with_evidence(pass_retail):
    result = build_pilot_gate({}, pass_retail, AS_OF)
    assert result["status"] == "BLOCKED_WITH_EVIDENCE"
    assert "account_profile" in result["missing_inputs"]

def test_draft_never_exceeds_approved_caps(complete_inputs, pass_retail):
    result = build_pilot_gate(complete_inputs, pass_retail, AS_OF)
    assert result["plan"]["total_loss_cap_usd"] == 10
    assert result["plan"]["daily_budget_usd"] <= 2
    assert result["plan"]["duration_days"] <= 14

def test_apply_refuses_unverified_exact_operation(plan):
    assert apply_pilot(plan, UnverifiedAdapter())["status"] == "BLOCKED"
```

- [x] **Step 2: Run tests and confirm RED**

Run: `python3 -m pytest -q tests/test_amazon_ads_pilot.py`

Expected: ERROR because the module does not exist.

- [x] **Step 3: Implement gate, deterministic mutation plan, and fail-closed adapter boundary**

```python
APPROVED_CAPS = {"total_loss_cap_usd": 10.0, "daily_budget_usd": 2.0, "duration_days": 14, "max_asins": 1}

def apply_pilot(plan, adapter):
    manifest = adapter.capability_manifest()
    required = {"create_sponsored_products", "pause_campaign", "read_campaign"}
    if any(manifest.get(op) != "live-write-verified" for op in required):
        return {"status": "BLOCKED", "reason": "exact_operation_not_live_write_verified"}
    # create paused or smallest reversible state, read back, activate only if exact state matches
```

- [x] **Step 4: Inspect live account capability or record exact blockers**

Run: `python3 scripts/amazon_ads_pilot.py --inputs data/amazon_ads_pilot_inputs.json --retail data/retail_readiness_latest.json --output data/amazon_ads_pilot_latest.json`

Expected: `APPLY_READY` only with fresh account/profile/currency/timezone/write/readback evidence; otherwise `BLOCKED_WITH_EVIDENCE` listing each missing input. No remote mutation occurs from this command.

- [x] **Step 5: Apply only when the exact gate is complete and the owner-approved plan still matches remote state**

Run only if `APPLY_READY`: `python3 scripts/amazon_ads_pilot.py --apply --confirm '<idempotency-key>'`

Expected: verified remote campaign/ad group/target before/after IDs plus rollback=pause. If the gate is incomplete, do not run `--apply`.

- [x] **Step 6: Run focused tests and secret scan**

Run: `python3 -m pytest -q tests/test_amazon_ads_pilot.py tests/test_amazon_ads_controller.py`

Run: `rg -n '(client_secret|refresh_token|access_token|session_cookie)[" ]*[:=][" ]*[^$<]' data/amazon_ads_pilot_*.json`

Expected: tests PASS; secret scan returns no matches.

- [x] **Step 7: Commit Task 3 files only**

```bash
git add amazon_ads_pilot.py scripts/amazon_ads_pilot.py tests/test_amazon_ads_pilot.py data/amazon_ads_pilot_inputs.json data/amazon_ads_pilot_latest.json
git commit -m "feat: gate Libra Amazon Ads pilot"
```

---

### Task 4: Production readback, documentation, and handoff

**Files:**
- Modify: `memory.md`
- Modify: `/root/memory.md`
- Modify: `/root/telos.md` only if the Libra operating status changes
- Modify: `/root/CURRENT_HANDOFF.md` via `ai-work finish`

**Interfaces:**
- Consumes: fresh monitor output, QA report, Ads gate/apply evidence, full test output, Git diff.
- Produces: durable current-state memory, backup push, and released coordination lock.

- [x] **Step 1: Run the monitor against production and read back the snapshot/state**

Run: `python3 scripts/search_organic_checkpoint.py --as-of 2026-11-19`

Expected: either a fresh schema-v2 snapshot/final rolling state or explicit pending/UNKNOWN without destroying prior final evidence.

- [x] **Step 2: Run the full Libra test suite**

Run: `python3 -m pytest -q`

Expected: zero failures.

- [x] **Step 3: Inspect exact diff and runtime files**

Run: `git status --short && git diff --check && git diff --stat`

Expected: no accidental staging or modification of unrelated runtime files.

- [x] **Step 4: Update memories with facts, decisions, tests, and blockers**

Record the monitor window/results, every book verdict, Ads status and missing capability/evidence, commits, test count, and follow-up date. Do not claim the pilot launched unless remote readback proves it.

- [x] **Step 5: Commit only task-owned documentation and push backup main**

```bash
git add memory.md docs/superpowers/plans/2026-11-19-libra-emergency-revenue-rescue.md
git commit -m "docs: close Libra emergency rescue handoff"
git push backup main
```

- [x] **Step 6: Refresh and release ownership**

Run: `ai-work refresh`

Run: `ai-work finish "Libra emergency rescue: rolling Search Console monitoring verified, retail QA recorded, Amazon Ads pilot applied with readback or blocked with exact evidence, tests passed, backup pushed"`

Expected: no active Libra owner remains and `CURRENT_HANDOFF.md` reflects the result.
