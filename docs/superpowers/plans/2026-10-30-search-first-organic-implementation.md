# Libra Search-first Organic Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a public, measurable search path for QA-approved existing Libra books and measure progress toward KDP royalties above USD 20 for two complete consecutive calendar months within 90 days.

**Architecture:** A small `organic_search.py` policy module owns eligibility, public guide models, sitemap URLs, privacy-safe view events, and scorecard calculations. FastAPI routes render customer-facing guide pages and enhanced existing articles from those models; the existing signed Amazon redirect remains the only outbound path. A read-only CLI reports traffic and the latest KDP monthly estimate without claiming purchase attribution.

**Tech Stack:** Python 3.12, FastAPI, Jinja-style static HTML placeholders, SQLite, pytest, JSON configuration, system cron.

## Global Constraints

- Keep TOTAL KDP FREEZE: no KDP metadata, price, cover, manuscript, upload, appeal, unpublish, promotion, or new book action.
- Keep Pinterest RSS authorization closed and do not activate LinkedIn, paid ads, email, or cancelled decision agents.
- Public pages include only `promotion_status: APPROVED`, `qa_approved: true`, and currently LIVE listings.
- Store no persistent IP address, cookie, email, or user-agent value; crawler and empty user-agent requests do not count as qualified views.
- Treat KDP snapshots as cumulative monthly observations and use only the latest observation per month.
- Report search traffic and KDP royalties side by side; never calculate Amazon purchase conversion or claim SEO attribution.
- Use `UNKNOWN`, never zero, when Search Console data or another source is unavailable.
- Preserve existing dirty files; stage named files only and push completed commits to `backup main`.

---

### Task 1: Search portfolio policy and view events

**Files:**
- Create: `organic_search.py`
- Create: `data/search_portfolio.json`
- Test: `tests/test_organic_search.py`

**Interfaces:**
- Consumes: KDP listing directory, approved article directory, `data/search_portfolio.json`, and `content_hub.is_bot_user_agent`.
- Produces: `load_search_portfolio(path: Path) -> dict`, `eligible_topics(config, kdp_dir, articles_dir) -> list[dict]`, `build_view_event(slug, campaign, page_id, now=None) -> dict`, and `build_scorecard(ledger_path, config, now=None) -> dict`.

- [ ] **Step 1: Write failing policy tests**

```python
def test_eligible_topics_fail_closed_on_hold_or_non_live(tmp_path):
    config = {"books": [
        {"slug": "approved", "promotion_status": "APPROVED", "topic": "focus", "language": "es"},
        {"slug": "held", "promotion_status": "HOLD_PROMOTION", "topic": "phone", "language": "fr"},
    ]}
    write_listing(tmp_path / "kdp", "approved", live_status="LIVE")
    write_listing(tmp_path / "kdp", "held", live_status="LIVE")
    write_article(tmp_path / "articles", "a1", target_slug="approved", qa_approved=True)
    assert [row["slug"] for row in eligible_topics(config, tmp_path / "kdp", tmp_path / "articles")] == ["approved"]

def test_build_view_event_keeps_only_privacy_safe_fields():
    event = build_view_event("book-a", "seo-search", "article:a1")
    assert event["event_kind"] == "qualified_view"
    assert event["payload"] == {"page_id": "article:a1"}
    assert set(event) == {"event_key", "occurred_at", "slug", "campaign", "event_kind", "payload"}
```

- [ ] **Step 2: Run the focused tests and confirm missing module failure**

Run: `python3 -m pytest tests/test_organic_search.py -q`

Expected: collection fails with `ModuleNotFoundError: No module named 'organic_search'`.

- [ ] **Step 3: Implement the minimal policy module and configuration**

Create `data/search_portfolio.json` with `seo-search` as the campaign, a release state of `PRE_RELEASE`, a 90-day goal, TDAH as `APPROVED`, bilingual and watercolor as `QA_REQUIRED`, and senior smartphone as `HOLD_PROMOTION`. Each public book entry contains a public topic title and description; no unverified expertise or result promise is included. Promotion status changes require a separate recorded product/content review.

Implement strict JSON validation, LIVE listing checks, approved-article filtering, and view events with this stable payload:

```python
def build_view_event(slug, campaign, page_id, *, now=None):
    return {
        "event_key": secrets.token_hex(16),
        "occurred_at": (now or datetime.now(timezone.utc)).isoformat(),
        "slug": slug,
        "campaign": campaign,
        "event_kind": "qualified_view",
        "payload": {"page_id": page_id},
    }
```

- [ ] **Step 4: Add scorecard tests for latest monthly observations and UNKNOWN Search Console**

```python
def test_scorecard_uses_latest_snapshot_and_keeps_search_console_unknown(tmp_path):
    ledger = tmp_path / "ledger.db"
    seed_snapshot(ledger, "2026-11-20T09:00:00+07:00", "2026-11", 19.0)
    seed_snapshot(ledger, "2026-11-30T09:00:00+07:00", "2026-11", 21.0)
    result = build_scorecard(ledger, {"release": {"state": "PRE_RELEASE"}})
    assert result["kdp_months"]["2026-11"]["royalties_usd"] == 21.0
    assert result["search_console"] == {"status": "UNKNOWN", "reason": "not_connected"}
    assert result["purchase_attribution"] == "UNKNOWN"
```

- [ ] **Step 5: Run the focused tests and commit**

Run: `python3 -m pytest tests/test_organic_search.py -q`

Expected: all tests pass.

Commit only `organic_search.py`, `data/search_portfolio.json`, and `tests/test_organic_search.py` with message `feat: add organic search portfolio policy`.

### Task 2: Public guide index and topic pages

**Files:**
- Create: `templates/guides_index.html`
- Create: `templates/guide_topic.html`
- Modify: `app.py`
- Test: `tests/test_search_guides.py`

**Interfaces:**
- Consumes: `eligible_topics()` and the existing `_public_path`, `_live_book_asin`, and signed outbound helper.
- Produces: anonymous routes `GET /guides/` and `GET /guides/{topic}`.

- [ ] **Step 1: Write failing route tests**

```python
def test_guides_index_lists_only_approved_live_topics(client, portfolio):
    response = client.get("/guides/")
    assert response.status_code == 200
    assert "Rutinas prácticas" in response.text
    assert "Guía visual para smartphone" not in response.text

def test_topic_page_links_to_articles_and_one_tracked_amazon_cta(client):
    response = client.get("/guides/rutinas-tdah-adultos")
    assert response.status_code == 200
    assert '/libra/growth/articles/a1' in response.text
    assert response.text.count('/libra/growth/out/') == 1
```

- [ ] **Step 2: Run route tests and confirm 404 failures**

Run: `python3 -m pytest tests/test_search_guides.py -q`

Expected: `/guides/` and `/guides/{topic}` return 404.

- [ ] **Step 3: Implement public templates and routes**

Render semantic HTML with one H1, language, canonical link, topic description, crawlable article links, book cover, honest format line, and one signed Amazon CTA tagged `seo-search`. The index has no operational state, internal revenue, or administration links. Return 404 for unknown, held, QA-required, blocked, or unpublished topics.

- [ ] **Step 4: Verify routes and existing hub behavior**

Run: `python3 -m pytest tests/test_search_guides.py tests/test_growth_routes.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit the public guide surface**

Commit `app.py`, both guide templates, and `tests/test_search_guides.py` with message `feat: publish customer guide topics`.

### Task 3: Enhanced article navigation and structured data

**Files:**
- Modify: `templates/hub_article.html`
- Modify: `app.py`
- Modify: `organic_search.py`
- Test: `tests/test_search_guides.py`
- Test: `tests/test_growth_routes.py`

**Interfaces:**
- Consumes: eligible topic model and current article JSON.
- Produces: existing `/growth/articles/{article_id}` pages with guide navigation, related links, disclosure, and visible-content `Article` JSON-LD.

- [ ] **Step 1: Write failing article enhancement tests**

```python
def test_eligible_article_has_topic_navigation_related_links_and_schema(client):
    response = client.get("/growth/articles/a1")
    assert response.status_code == 200
    assert '/libra/guides/rutinas-tdah-adultos' in response.text
    assert '/libra/growth/articles/a2' in response.text
    assert 'application/ld+json' in response.text
    assert '"@type": "Article"' in response.text

def test_held_book_article_is_not_added_to_search_navigation(client):
    response = client.get("/growth/articles/held-a1")
    assert response.status_code == 200
    assert '/libra/guides/' not in response.text
```

- [ ] **Step 2: Run the tests and confirm missing enhancement failures**

Run: `python3 -m pytest tests/test_search_guides.py -q`

Expected: assertions for guide navigation, related links, and JSON-LD fail.

- [ ] **Step 3: Implement model helpers and template placeholders**

Add `topic_for_article()` and `related_articles()` helpers. Generate JSON-LD with `json.dumps` from the visible title, description, language, canonical URL, and publication date; escape `</` as `<\/` before insertion. Add a concise disclosure stating the page provides practical educational information and links to the relevant book on Amazon. Do not add ratings, reviews, medical credentials, or outcome claims.

- [ ] **Step 4: Run article and security regressions**

Run: `python3 -m pytest tests/test_search_guides.py tests/test_growth_routes.py tests/test_content_hub.py -q`

Expected: all tests pass, including HTML escaping and signed-link checks.

- [ ] **Step 5: Commit the article enhancements**

Commit the three modified source/template files and the two named test files with message `feat: connect search guides and articles`.

### Task 4: Sitemap and qualified view measurement

**Files:**
- Modify: `app.py`
- Modify: `organic_search.py`
- Test: `tests/test_search_guides.py`

**Interfaces:**
- Consumes: eligible guide/article models, `is_bot_user_agent()`, and `record_hub_event()`.
- Produces: `GET /sitemap.xml`, qualified view rows for guide/article requests, and no view rows for bots or missing user agents.

- [ ] **Step 1: Write failing sitemap and measurement tests**

```python
def test_sitemap_contains_only_approved_live_public_urls(client):
    body = client.get("/sitemap.xml").text
    assert "/libra/guides/rutinas-tdah-adultos" in body
    assert "/libra/growth/articles/a1" in body
    assert "held-a1" not in body

def test_reader_view_counts_but_googlebot_does_not(client, ledger):
    client.get("/guides/rutinas-tdah-adultos", headers={"User-Agent": READER_UA})
    client.get("/guides/rutinas-tdah-adultos", headers={"User-Agent": GOOGLEBOT_UA})
    assert event_count(ledger, "qualified_view") == 1
```

- [ ] **Step 2: Run tests and confirm sitemap 404 and zero view events**

Run: `python3 -m pytest tests/test_search_guides.py -q`

Expected: sitemap route and event assertions fail.

- [ ] **Step 3: Implement XML sitemap and in-memory user-agent filtering**

Build the XML with `xml.etree.ElementTree`; include `/libra/guides/`, eligible topic pages, and eligible approved articles. Record `qualified_view` only after successful public rendering and only when `is_bot_user_agent()` returns false. Do not persist request headers or addresses.

- [ ] **Step 4: Run focused and existing ledger tests**

Run: `python3 -m pytest tests/test_search_guides.py tests/test_growth_routes.py tests/test_business_ledger.py -q`

Expected: all tests pass.

- [ ] **Step 5: Commit sitemap and measurement**

Commit `app.py`, `organic_search.py`, and `tests/test_search_guides.py` with message `feat: measure qualified organic guide views`.

### Task 5: Read-only 90-day scorecard

**Files:**
- Create: `scripts/search_organic_report.py`
- Test: `tests/test_search_organic_report.py`
- Modify: `data/search_portfolio.json` only at release to set D0.

**Interfaces:**
- Consumes: `build_scorecard()`, ledger snapshots/events, and portfolio release state.
- Produces: human text and `--json` report with source timestamps, month totals, USD 20 gap, traffic totals, attribution status, and TARGET_MET/TARGET_NOT_MET/IN_PROGRESS/UNKNOWN.

- [ ] **Step 1: Write failing CLI tests**

```python
def test_json_report_keeps_traffic_and_revenue_separate(runner):
    payload = json.loads(runner("--json"))
    assert payload["purchase_attribution"] == "UNKNOWN"
    assert "qualified_views" in payload["traffic"]
    assert "amazon_outbound" in payload["traffic"]
    assert "conversion_rate" not in json.dumps(payload)

def test_two_complete_months_above_target_are_target_met():
    assert evaluate_goal({"2026-11": 20.01, "2026-12": 24.50}, as_of=date(2027, 1, 1)) == "TARGET_MET"
```

- [ ] **Step 2: Run tests and confirm missing CLI failure**

Run: `python3 -m pytest tests/test_search_organic_report.py -q`

Expected: import or file-not-found failure.

- [ ] **Step 3: Implement CLI with explicit source health**

Support `--json`, `--ledger`, `--portfolio`, and `--as-of`. The human report prints observed timestamps, current month estimate, gap to USD 20, completed-month goal status, qualified views, outbound clicks, and `Search Console: UNKNOWN (not connected)`.

- [ ] **Step 4: Run tests and a production read-only report**

Run: `python3 -m pytest tests/test_search_organic_report.py tests/test_organic_search.py -q`

Run: `python3 scripts/search_organic_report.py --json`

Expected: tests pass and JSON parses without writing the ledger or contacting KDP/Amazon.

- [ ] **Step 5: Commit the scorecard**

Commit `scripts/search_organic_report.py` and `tests/test_search_organic_report.py` with message `feat: report organic search revenue goal`.

### Task 6: Release, weekly schedule, and production verification

**Files:**
- Modify: `data/search_portfolio.json`
- Modify: `/root/memory.md`
- Modify: `/root/telos.md`
- Shared control surface: root crontab through `/root/scripts/safe_crontab.py`

**Interfaces:**
- Consumes: all previous tasks and Libra service/nginx mount.
- Produces: D0 release record, live pages, weekly read-only report log, and rollback evidence.

- [ ] **Step 1: Run the complete relevant test set**

Run: `python3 -m pytest tests/test_organic_search.py tests/test_search_guides.py tests/test_search_organic_report.py tests/test_growth_routes.py tests/test_content_hub.py tests/test_business_ledger.py tests/test_kdp_freeze.py tests/test_kdp_freeze_entrypoints.py -q`

Expected: all selected tests pass.

- [ ] **Step 2: Snapshot shared/runtime state and set release D0**

Save named snapshots under `/root/backups/libra-search-first-20261030/` for the live service unit, relevant crontab lines, current public response headers, and the pre-release config. Change only `release.state` to `ACTIVE` and set `released_at` to the actual Asia/Bangkok timestamp after tests pass.

- [ ] **Step 3: Restart Libra and verify public behavior**

Run `systemctl restart libra` and verify service active plus HTTP 200 for `/libra/guides/`, one eligible topic, one eligible article, and `/libra/sitemap.xml`; verify held topic is 404 and Pinterest feed remains 404.

- [ ] **Step 4: Inspect mobile and desktop renderings**

Capture Playwright screenshots at 390x844 and 1440x1000 for the guide index, one topic, and one article. Check no horizontal overflow, readable CTA, visible language/topic navigation, and no internal operations or false claims.

- [ ] **Step 5: Install one weekly read-only report using the safe crontab helper**

Add Sunday 10:05 Asia/Bangkok command `cd /root/libra && /usr/bin/python3 scripts/search_organic_report.py >> logs/search-organic-report.log 2>&1` with owner tag `libra-search-organic-weekly-20261030`. Re-read and diff crontab; only this tagged line may change.

- [ ] **Step 6: Commit release state and push backup**

Stage only `data/search_portfolio.json`, commit with message `chore: activate Libra organic search measurement`, and run `git push backup main`. Report any push failure explicitly.

- [ ] **Step 7: Update business memory and release ownership**

Update `/root/memory.md` with code, tests, deploy, D0, public URLs, cron, limitations, and commit IDs. Update `/root/telos.md` with ACTIVE status and the two target calendar months. Run `ai-work finish <token> <summary>` only after the updates and live checks pass.
