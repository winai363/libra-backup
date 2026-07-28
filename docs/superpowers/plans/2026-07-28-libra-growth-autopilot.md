# Libra Growth Autopilot Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single evidence-driven controller that grows the existing Libra KDP portfolio organically for 30 days, then permits at most THB 3,000/month of Amazon Ads only for titles that pass the approved Growth Gate.

**Architecture:** Extend the existing SQLite business ledger and Profit Agent instead of creating a parallel strategist. Add focused modules for evidence, scoring, planning, owned Content Hub acquisition, verified distribution, KDP promotion, and Ads policy; compose them in one controller that starts in shadow mode and fails closed.

**Tech Stack:** Python 3.12, SQLite, FastAPI, Jinja2, Playwright, pytest, system cron.

## Global Constraints

- Paid spend is exactly zero for the first 30 complete days.
- On day 31, Ads require paid royalty growth, incremental KENP >= 100, or verified tracked outbound clicks >= 20.
- Paid caps are THB 100/day and THB 3,000/month; at most two advertised titles and THB 50/day per title initially.
- At most eight active titles and three concurrent organic experiments.
- Exactly one experimental variable may be active per title and measurement window.
- Never edit or republish title, subtitle, author, description, keywords, category, cover, interior, or any live-book file.
- No external action is `executed` without stable after-state evidence.
- OTP, CAPTCHA, expired session, stale data, or unreadable confirmation returns `manual_required`.
- Use one commercial controller and one SQLite single-writer lock.
- Do not add a top-level nginx `/api/` location; Hub APIs remain under the Libra application namespace.
- Run pytest only from `/root/libra` with an explicit path such as `tests/test_growth_policy.py` or `tests/`.

---

## File Map

- Modify `business_ledger.py`: immutable growth evidence, plans, Hub events, campaigns, incidents.
- Create `growth_policy.py`: constants, phase calculation, authority and budget gates.
- Create `portfolio_scorer.py`: deterministic score and portfolio classification.
- Create `growth_planner.py`: bounded, idempotent daily plan.
- Create `content_hub.py`: Hub page records, tracking tokens, click ingestion.
- Create `growth_content.py`: localized content request and quality gates.
- Create `distribution_executor.py`: verified publication contract.
- Create `kdp_promotion_controller.py`: paired one-day promotion policy and reconciliation.
- Create `amazon_ads_controller.py`: eligibility, budget, ACOS, and campaign decisions.
- Create `growth_autopilot.py`: single controller, readiness, lock, state output.
- Create `scripts/libra_growth_autopilot.py`: CLI and Telegram entrypoint.
- Modify `app.py`: namespaced Hub, tracking, and growth APIs/pages.
- Create `templates/growth.html`, `templates/hub_book.html`, `templates/hub_article.html`.
- Modify `templates/profit.html`: link to the new growth view.
- Create focused tests named after each module.

---

### Task 1: Immutable Growth Evidence Ledger

**Files:**
- Modify: `business_ledger.py`
- Create: `tests/test_growth_ledger.py`

**Interfaces:**
- Consumes: existing `init_ledger(path: Path)`.
- Produces: `record_growth_evidence(path, evidence) -> int`, `record_growth_plan(path, plan) -> int`, `record_hub_event(path, event) -> int`, `growth_evidence(path, *, slug=None, kind=None) -> list[dict]`.

- [ ] **Step 1: Write failing idempotency and conflict tests**

```python
def test_growth_evidence_is_idempotent_and_conflicts_fail(tmp_path):
    db = tmp_path / "ledger.db"
    item = {
        "source_key": "hub-click:abc", "kind": "hub_click", "slug": "book-a",
        "observed_at": "2026-07-29T09:00:00+00:00",
        "fresh_until": "2026-07-30T09:00:00+00:00",
        "confidence": 1.0, "payload": {"campaign": "organic-1"},
    }
    assert record_growth_evidence(db, item) == record_growth_evidence(db, item)
    with pytest.raises(ValueError, match="conflicting growth evidence"):
        record_growth_evidence(db, {**item, "payload": {"campaign": "changed"}})
```

- [ ] **Step 2: Run the focused test and confirm RED**

Run: `cd /root/libra && pytest tests/test_growth_ledger.py -q`

Expected: FAIL because `record_growth_evidence` is not defined.

- [ ] **Step 3: Add append-only tables and canonical record/read functions**

```python
GROWTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS growth_evidence (
  id INTEGER PRIMARY KEY, source_key TEXT NOT NULL UNIQUE, kind TEXT NOT NULL,
  slug TEXT, observed_at TEXT NOT NULL, fresh_until TEXT NOT NULL,
  confidence REAL NOT NULL, payload_json TEXT NOT NULL, content_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS growth_plans (
  id INTEGER PRIMARY KEY, action_key TEXT NOT NULL UNIQUE, planned_at TEXT NOT NULL,
  phase TEXT NOT NULL, status TEXT NOT NULL, plan_json TEXT NOT NULL,
  content_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS hub_events (
  id INTEGER PRIMARY KEY, event_key TEXT NOT NULL UNIQUE, occurred_at TEXT NOT NULL,
  slug TEXT NOT NULL, campaign TEXT NOT NULL, event_kind TEXT NOT NULL,
  payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS growth_incidents (
  id INTEGER PRIMARY KEY, incident_key TEXT NOT NULL UNIQUE, opened_at TEXT NOT NULL,
  severity TEXT NOT NULL, scope TEXT NOT NULL, detail_json TEXT NOT NULL,
  resolved_at TEXT
);
"""
```

Use `_canonical()` and `_hash()` for all replay comparison. Validate confidence is between `0.0` and `1.0`.

- [ ] **Step 4: Run ledger regression tests**

Run: `cd /root/libra && pytest tests/test_growth_ledger.py tests/test_business_ledger.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add business_ledger.py tests/test_growth_ledger.py
git commit -m "feat: add immutable growth evidence ledger"
```

---

### Task 2: Phase, Authority, and Budget Policy

**Files:**
- Create: `growth_policy.py`
- Create: `tests/test_growth_policy.py`
- Modify: `profit_agent.py`

**Interfaces:**
- Consumes: `now`, `started_at`, evidence totals, proposed action.
- Produces: `growth_phase(started_at, now) -> str`, `ads_eligibility(metrics) -> dict`, `authorize_growth_action(policy, action, state) -> dict`.

- [ ] **Step 1: Write failing policy boundary tests**

```python
def test_ads_stay_closed_until_day_31_and_growth_signal():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    action = {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 50}
    assert authorize_growth_action(
        policy_for(started), action,
        state_at(started + timedelta(days=29), clicks=100),
    )["allowed"] is False
    assert authorize_growth_action(
        policy_for(started), action,
        state_at(started + timedelta(days=30), clicks=19),
    )["allowed"] is False
    assert authorize_growth_action(
        policy_for(started), action,
        state_at(started + timedelta(days=30), clicks=20),
    )["allowed"] is True
```

Also test THB 100/day, THB 3,000/month, two-title, THB 50 initial-title, 20% reserve, 72-hour increase, and forbidden live metadata actions.

- [ ] **Step 2: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_growth_policy.py -q`

Expected: FAIL because `growth_policy` does not exist.

- [ ] **Step 3: Implement exact policy constants and fail-closed authorization**

```python
ORGANIC_DAYS = 30
DAILY_CAP_THB = 100
MONTHLY_CAP_THB = 3000
INITIAL_TITLE_CAP_THB = 50
MAX_AD_TITLES = 2
MAX_ACTIVE_TITLES = 8
MAX_ORGANIC_TESTS = 3
FORBIDDEN_LIVE_FIELDS = {
    "title", "subtitle", "author", "description", "keywords",
    "categories", "cover", "interior",
}
```

Return `{"allowed": False, "reason": <stable reason>}` for every unknown or missing input. Add `price_update`, `free_promo`, `countdown_deal`, and `amazon_ads` only to the new policy path; do not weaken legacy metadata/category gates.

- [ ] **Step 4: Run focused and existing policy tests**

Run: `cd /root/libra && pytest tests/test_growth_policy.py tests/test_profit_agent.py tests/test_kdp_action_executor.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add growth_policy.py profit_agent.py tests/test_growth_policy.py
git commit -m "feat: enforce growth phase and spend policy"
```

---

### Task 3: Deterministic Portfolio Scoring and Capacity

**Files:**
- Create: `portfolio_scorer.py`
- Create: `tests/test_portfolio_scorer.py`

**Interfaces:**
- Consumes: `score_portfolio(titles: list[dict], now: datetime)`.
- Produces: rows containing `slug`, `score`, `classification`, `components`, `reasons`, `evidence_fresh`.

- [ ] **Step 1: Write failing evidence-only scoring tests**

```python
def test_missing_and_estimated_signals_cannot_raise_score():
    title = {
        "slug": "book-a", "royalty_delta_usd": 0, "kenp_delta": 0,
        "tracked_clicks": 0, "verified_placements": 3,
        "estimated_market_demand": 100, "risk_active": False,
    }
    result = score_title(title)
    assert result["score"] == 0
    assert result["classification"] == "freeze"

def test_revenue_winner_is_scale_but_risky_title_is_blocked():
    winner = score_title({
        "slug": "book-a", "royalty_delta_usd": 5, "kenp_delta": 120,
        "tracked_clicks": 25, "conversion_signal": 1, "risk_active": False,
    })
    assert winner["classification"] == "scale"
    assert score_title({**winner_input(), "risk_active": True})["classification"] == "blocked"
```

- [ ] **Step 2: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_portfolio_scorer.py -q`

Expected: FAIL because `portfolio_scorer` does not exist.

- [ ] **Step 3: Implement the approved 30/25/20/15/10 scoring**

Normalize each component to `0..100`, apply weights, round to two decimals, and classify using explicit thresholds covered by tests. Risk is a penalty and hard-blocks content/account incidents. Freeze requires at least three verified placements with zero clicks; otherwise use `test` or `maintain`, not premature freeze.

- [ ] **Step 4: Run focused tests**

Run: `cd /root/libra && pytest tests/test_portfolio_scorer.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add portfolio_scorer.py tests/test_portfolio_scorer.py
git commit -m "feat: score and classify the KDP portfolio"
```

---

### Task 4: Idempotent Growth Planner

**Files:**
- Create: `growth_planner.py`
- Create: `tests/test_growth_planner.py`

**Interfaces:**
- Consumes: scored titles, active experiments, phase, policy state.
- Produces: `build_growth_plan(*, scored_titles: list[dict], active_experiments: list[dict], phase: str, now: datetime) -> dict` with stable `action_key`, `phase`, `portfolio`, and bounded `actions`.

- [ ] **Step 1: Write failing capacity and one-variable tests**

```python
def test_plan_caps_portfolio_and_never_overlaps_variables():
    titles = ranked_titles(12)
    experiments = [{"slug": "book-1", "variable": "channel"}]
    plan = build_growth_plan(
        scored_titles=titles,
        active_experiments=experiments,
        phase="organic_test", now=NOW,
    )
    assert len(plan["portfolio"]["active"]) <= 8
    assert len([a for a in plan["actions"] if a["kind"] == "organic_test"]) <= 3
    assert not any(
        a["slug"] == "book-1" and a["variable"] != "channel"
        for a in plan["actions"]
    )
    replay = build_growth_plan(
        scored_titles=titles,
        active_experiments=experiments,
        phase="organic_test", now=NOW,
    )
    assert replay["action_key"] == plan["action_key"]
```

- [ ] **Step 2: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_growth_planner.py -q`

Expected: FAIL because `build_growth_plan` is missing.

- [ ] **Step 3: Implement deterministic selection and stable hashing**

Sort by classification priority, score descending, then slug. Derive `action_key` from canonical phase, date, selected slugs, and variables. Output only bounded actions; never emit a metadata or republish action.

- [ ] **Step 4: Run focused tests**

Run: `cd /root/libra && pytest tests/test_growth_planner.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add growth_planner.py tests/test_growth_planner.py
git commit -m "feat: plan bounded Libra growth actions"
```

---

### Task 5: Content Hub and First-Party Tracking

**Files:**
- Create: `content_hub.py`
- Modify: `app.py`
- Create: `templates/hub_book.html`
- Create: `templates/hub_article.html`
- Create: `tests/test_content_hub.py`
- Create: `tests/test_growth_routes.py`

**Interfaces:**
- Produces: `make_tracking_token(slug, campaign, destination) -> str`, `resolve_tracking_token(token) -> dict`.
- Routes: `GET /growth/books/{slug}`, `GET /growth/articles/{article_id}`, `GET /growth/out/{token}`, `GET /api/growth/summary`.

- [ ] **Step 1: Write failing token, redirect, and privacy tests**

```python
def test_outbound_click_records_once_and_redirects(client, ledger):
    token = make_tracking_token("book-a", "organic-1", "https://www.amazon.com/dp/ASIN")
    response = client.get(f"/growth/out/{token}", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "https://www.amazon.com/dp/ASIN"
    assert count_events(ledger, event_kind="amazon_outbound") == 1
```

Reject destinations outside approved Amazon marketplace hosts. Do not store raw IP addresses, user agents, cookies, or email.

- [ ] **Step 2: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_content_hub.py tests/test_growth_routes.py -q`

Expected: FAIL because Hub functions and routes do not exist.

- [ ] **Step 3: Implement signed tokens, pages, and namespaced routes**

Use HMAC-SHA256 with `LIBRA_GROWTH_TRACKING_SECRET`. Store a random event key plus slug/campaign/timestamp only. Render useful book/article pages with one tracked Amazon CTA. Keep API paths inside the existing Libra app; do not edit nginx.

- [ ] **Step 4: Run focused route and app regressions**

Run: `cd /root/libra && pytest tests/test_content_hub.py tests/test_growth_routes.py tests/test_profit_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add content_hub.py app.py templates/hub_book.html templates/hub_article.html tests/test_content_hub.py tests/test_growth_routes.py
git commit -m "feat: add tracked Libra Content Hub"
```

---

### Task 6: Localized Content Quality and Verified Distribution

**Files:**
- Create: `growth_content.py`
- Create: `distribution_executor.py`
- Create: `tests/test_growth_content.py`
- Create: `tests/test_distribution_executor.py`

**Interfaces:**
- Produces: `build_content_request(listing, source_excerpt, campaign) -> dict`, `validate_growth_content(content, listing) -> list[str]`, `execute_distribution(action, adapter) -> dict`.
- Adapter result must contain `post_url` or `post_id` and readable after-state.

- [ ] **Step 1: Write failing language, unsupported-claim, and false-success tests**

```python
def test_distribution_requires_external_after_state():
    result = execute_distribution(
        {"action_key": "post:1", "slug": "book-a", "language": "es"},
        adapter=FakeAdapter({"clicked": True, "post_url": None}),
    )
    assert result["status"] == "manual_required"
    assert result["evidence"] == {}

def test_content_rejects_wrong_language_and_unverified_health_claim():
    errors = validate_growth_content(
        {"language": "en", "body": "This treatment cures ADHD."},
        {"language": "Spanish", "risk_domain": "health"},
    )
    assert "language_mismatch" in errors
    assert "unsupported_claim" in errors
```

- [ ] **Step 2: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_growth_content.py tests/test_distribution_executor.py -q`

Expected: FAIL because modules do not exist.

- [ ] **Step 3: Implement grounded request, gates, and executor contract**

Require listing language, source excerpt hash, target reader, allowed claims, and canonical CTA. Map authentication barriers to `manual_required`, policy rejection to `blocked`, and only stable after-state to `executed`. Record results through `record_growth_evidence`.

- [ ] **Step 4: Run focused tests**

Run: `cd /root/libra && pytest tests/test_growth_content.py tests/test_distribution_executor.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add growth_content.py distribution_executor.py tests/test_growth_content.py tests/test_distribution_executor.py
git commit -m "feat: gate localized growth distribution"
```

---

### Task 7: Safe KDP Promotion Controller

**Files:**
- Create: `kdp_promotion_controller.py`
- Create: `tests/test_kdp_promotion_controller.py`
- Modify: `scripts/kdp_action_executor.py`

**Interfaces:**
- Produces: `propose_promotion(slug, state, evidence) -> dict`, `reconcile_promotion(action, adapter) -> dict`.

- [ ] **Step 1: Write failing paired one-day promotion tests**

```python
def test_free_promo_requires_verified_distribution_and_one_day():
    blocked = propose_promotion("book-a", kdp_state(), evidence=[])
    assert blocked["status"] == "blocked"
    allowed = propose_promotion(
        "book-a", kdp_state(),
        evidence=[{"kind": "external_post", "post_url": "https://example.test/p/1"}],
    )
    assert allowed["days"] == 1

def test_zero_download_result_exhausts_cycle():
    assert evaluate_promotion({"downloads": 0, "verified": True})["allow_more_days"] is False
```

- [ ] **Step 2: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_kdp_promotion_controller.py -q`

Expected: FAIL because controller functions do not exist.

- [ ] **Step 3: Implement proposal, reconciliation, and permanent mutation refusal**

Reuse the existing browser/KDP adapter and audit result format. Require fresh before-state, external placement, no overlapping experiment, one calendar day, KDP confirmation after-state, and a seven-day evaluation window. Keep `validate_action` refusal for category/metadata changes on ASIN listings.

- [ ] **Step 4: Run promotion and executor regressions**

Run: `cd /root/libra && pytest tests/test_kdp_promotion_controller.py tests/test_kdp_action_executor.py tests/test_final_blocker_remediation.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add kdp_promotion_controller.py scripts/kdp_action_executor.py tests/test_kdp_promotion_controller.py
git commit -m "feat: control evidence-paired KDP promotions"
```

---

### Task 8: Amazon Ads Eligibility, Budget, and Profit Controller

**Files:**
- Create: `amazon_ads_controller.py`
- Create: `tests/test_amazon_ads_controller.py`

**Interfaces:**
- Produces: `ads_decision(title, campaign, portfolio, policy, now) -> dict`, `reconcile_ads_action(action, adapter) -> dict`.

- [ ] **Step 1: Write failing Growth Gate and loss-guard tests**

```python
def test_day_31_without_growth_remains_zero_spend():
    decision = ads_decision(
        title_metrics(clicks=19, kenp_delta=99, royalty_delta=0),
        campaign=None, portfolio=portfolio_state(), policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "growth_gate_closed"}

def test_profitable_budget_increase_is_bounded():
    decision = ads_decision(
        title_metrics(clicks=20, kenp_delta=100, royalty_delta=2),
        campaign_state(budget=50, contribution=1, last_increase_hours=80),
        portfolio=portfolio_state(daily_spend=40, monthly_spend=500),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision["budget_thb"] <= 57.5
```

Also test two-title capacity, reserve, stale data, no-order stop threshold, break-even ACOS, monthly rollover, and unreadable campaign after-state.

- [ ] **Step 2: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_amazon_ads_controller.py -q`

Expected: FAIL because `amazon_ads_controller` does not exist.

- [ ] **Step 3: Implement pure decision logic and adapter reconciliation**

Keep all money in integer satang internally. Calculate per-title break-even ACOS from verified net royalty and direct cost. Permit a maximum 15% increase every 72 hours. Stale data can only `hold`, `reduce`, or `stop`. Require campaign ID, budget, status, and observed after-state for `executed`.

- [ ] **Step 4: Run focused policy and Ads tests**

Run: `cd /root/libra && pytest tests/test_amazon_ads_controller.py tests/test_growth_policy.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add amazon_ads_controller.py tests/test_amazon_ads_controller.py
git commit -m "feat: add profit-gated Amazon Ads controller"
```

---

### Task 9: Single Growth Autopilot Controller and Emergency Stop

**Files:**
- Create: `growth_autopilot.py`
- Create: `scripts/libra_growth_autopilot.py`
- Create: `tests/test_growth_autopilot.py`
- Modify: `scripts/libra_profit_agent_daily.py`

**Interfaces:**
- Produces: `run_growth_controller(config, now, shadow=True) -> dict`.
- CLI: `python3 scripts/libra_growth_autopilot.py --shadow|--execute --send`.

- [ ] **Step 1: Write failing shadow, lock, and emergency-stop tests**

```python
def test_shadow_mode_writes_plan_but_executes_nothing(tmp_path):
    state = run_growth_controller(config(tmp_path), now=NOW, shadow=True)
    assert state["mode"] == "shadow"
    assert state["executed"] == []
    assert state["plan"]["actions"]

def test_account_incident_stops_mutation_but_keeps_collection(tmp_path):
    cfg = config(tmp_path, incidents=[{"severity": "critical", "scope": "account"}])
    state = run_growth_controller(cfg, now=NOW, shadow=False)
    assert state["readiness"]["mutation_allowed"] is False
    assert state["observations_collected"] > 0
    assert state["executed"] == []
```

- [ ] **Step 2: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_growth_autopilot.py -q`

Expected: FAIL because controller does not exist.

- [ ] **Step 3: Compose collection, score, plan, authorize, execute, reconcile**

Acquire a file lock before plan/action writes. Run collectors first, derive readiness, score titles, build and persist one plan, authorize every action immediately before execution, reconcile external state, and atomically write `data/growth-autopilot-state.json`. Keep the legacy daily Profit Agent read-only after authority transfer.

- [ ] **Step 4: Run controller and regression tests**

Run: `cd /root/libra && pytest tests/test_growth_autopilot.py tests/test_libra_profit_agent_daily.py tests/test_profit_agent.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add growth_autopilot.py scripts/libra_growth_autopilot.py scripts/libra_profit_agent_daily.py tests/test_growth_autopilot.py
git commit -m "feat: orchestrate Libra growth autopilot"
```

---

### Task 10: Growth Dashboard and Operating Reports

**Files:**
- Modify: `app.py`
- Create: `templates/growth.html`
- Modify: `templates/profit.html`
- Create: `tests/test_growth_dashboard.py`

**Interfaces:**
- Routes: `GET /growth`, `GET /api/growth/state`.
- Consumes: `data/growth-autopilot-state.json` and ledger read models.

- [ ] **Step 1: Write failing six-question dashboard tests**

```python
def test_growth_dashboard_separates_plans_from_verified_outcomes(client):
    response = client.get("/growth")
    assert response.status_code == 200
    for label in (
        "Verified revenue", "Portfolio state", "Traffic sources",
        "Verified actions", "Spend and contribution", "Blocked actions",
    ):
        assert label in response.text
    assert "Planned" in response.text
    assert "Executed with evidence" in response.text
```

- [ ] **Step 2: Run and confirm RED**

Run: `cd /root/libra && pytest tests/test_growth_dashboard.py -q`

Expected: FAIL because the route/template do not exist.

- [ ] **Step 3: Implement compact operational dashboard and digest**

Render current phase/day, caps, readiness, classification table, evidence funnel, experiments, verified actions, incidents, and paid contribution. Do not nest cards or expose control buttons that bypass the controller. Add a plain-language daily Telegram digest from the same state.

- [ ] **Step 4: Run dashboard and API regressions**

Run: `cd /root/libra && pytest tests/test_growth_dashboard.py tests/test_growth_routes.py tests/test_profit_api.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py templates/growth.html templates/profit.html tests/test_growth_dashboard.py
git commit -m "feat: show Libra growth operations"
```

---

### Task 11: Shadow Rollout, Cron Transfer, and Production Verification

**Files:**
- Modify only after verified shadow run: system crontab.
- Modify: `/root/memory.md`
- Update: `CLAUDE.md` only if a new permanent Libra rule is discovered.

**Interfaces:**
- Consumes all prior tasks.
- Produces a verified shadow state, organic-only production activation, and rollback evidence.

- [ ] **Step 1: Run full Libra tests from the project directory**

Run: `cd /root/libra && pytest tests/ -q`

Expected: all tests pass with no network/KDP/Ads mutations.

- [ ] **Step 2: Run schema and dry-run verification against a copied ledger**

```bash
cd /root/libra
cp data/libra-business.db /tmp/libra-growth-shadow.db
LIBRA_LEDGER=/tmp/libra-growth-shadow.db python3 scripts/libra_growth_autopilot.py --shadow
```

Expected: phase is organic, spend is zero, no external execution occurs, active/test capacity is within limits, and no forbidden action is planned.

- [ ] **Step 3: Run production shadow mode**

Run: `cd /root/libra && python3 scripts/libra_growth_autopilot.py --shadow --send`

Expected: a fresh state and Telegram digest, zero paid spend, no KDP mutation, no external post, and complete readiness/evidence reasons.

- [ ] **Step 4: Audit the shadow output before any cron change**

Run:

```bash
cd /root/libra
jq '{mode,phase,readiness,policy,portfolio,plan,executed,blocked}' data/growth-autopilot-state.json
```

Expected: `mode=shadow`, organic phase, paid allowed false, no forbidden action kinds, and deterministic bounded actions.

- [ ] **Step 5: Install ordered system cron in shadow mode**

Use the existing Python `crontab` subprocess pattern. Add:

```cron
30 9 * * * cd /root/libra && /usr/bin/python3 scripts/libra_growth_autopilot.py --collect >> logs/growth-autopilot.log 2>&1
0 10 * * * cd /root/libra && /usr/bin/python3 scripts/libra_growth_autopilot.py --shadow --send >> logs/growth-autopilot.log 2>&1
30 20 * * * cd /root/libra && /usr/bin/python3 scripts/libra_growth_autopilot.py --verify >> logs/growth-autopilot.log 2>&1
```

Do not remove or alter legacy commercial cron yet.

- [ ] **Step 6: Observe one complete shadow cycle and reconcile outputs**

Expected: KDP sync precedes planning, plans are idempotent, verification does not invent outcomes, and repeated runs do not duplicate actions.

- [ ] **Step 7: Transfer authority to organic execution**

After the shadow audit passes:

- change the 10:00 command from `--shadow` to `--execute`;
- keep paid policy hard-disabled by the persisted phase start;
- comment out only legacy cron lines that can mutate price, promotion, free posts, or experiments;
- retain KDP sales, Bookshelf, category health, and report collectors.

- [ ] **Step 8: Verify organic production activation**

Run: `cd /root/libra && python3 scripts/libra_growth_autopilot.py --execute --send`

Expected: only organic actions authorized by fresh evidence; paid spend remains exactly zero; any authentication barrier becomes `manual_required`.

- [ ] **Step 9: Update memory and commit activation files**

Record phase start, enabled cron, disabled legacy writers, test counts, readiness state, and remaining manual barriers in `/root/memory.md`. Commit only repository files changed by activation, then push the configured backup remote.

- [ ] **Step 10: Day-15 and Day-30 review gates**

At day 15, verify freeze/reallocation rules against actual evidence. At day 30, run:

```bash
cd /root/libra
python3 scripts/libra_growth_autopilot.py --growth-gate-report
```

Expected: Ads remain at zero unless at least one title has paid royalty growth, KENP +100, or 20 verified clicks. Only then may the existing `--execute` path create Ads within THB 100/day and THB 3,000/month.

---

## Final Verification Matrix

- Policy: day 1-30 zero spend; day 31 Growth Gate; exact daily/monthly/title caps.
- Safety: no live metadata/file mutation; incidents and stale state fail closed.
- Evidence: all external success has stable after-state; replay is idempotent.
- Portfolio: active <= 8, organic <= 3, paid <= 2, one variable per title/window.
- Organic: Hub tracking, localized content gates, paired one-day promotions.
- Paid: eligibility, reserve, ACOS, contribution, 15%/72-hour increase boundary.
- Operations: single writer, shadow rollout, ordered cron, dashboard, Telegram.
- Regression: full `/root/libra/tests/` passes from `/root/libra`.
