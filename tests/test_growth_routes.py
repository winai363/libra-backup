"""HTTP-level tests for the Libra Content Hub routes: /growth/books/{slug},
/growth/articles/{article_id}, /growth/out/{token}, and
/api/growth/summary. Uses a tmp ledger DB and tmp KDP/articles directories
monkeypatched onto the app module, following the same pattern as
tests/test_profit_api.py."""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app as libra_app
from business_ledger import init_ledger
from content_hub import make_tracking_token, resolve_tracking_token


def count_events(ledger, *, event_kind):
    with sqlite3.connect(ledger) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM hub_events WHERE event_kind = ?", (event_kind,)
        ).fetchone()[0]


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    ledger_path = tmp_path / "libra-business.db"
    monkeypatch.setattr(libra_app, "PROFIT_LEDGER_FILE", ledger_path)
    return ledger_path


@pytest.fixture
def client(tmp_path, monkeypatch, ledger):
    monkeypatch.setenv("LIBRA_GROWTH_TRACKING_SECRET", "route-test-secret")
    kdp_dir = tmp_path / "kdp"
    kdp_dir.mkdir()
    monkeypatch.setattr(libra_app, "KDP_DIR", kdp_dir)
    articles_dir = tmp_path / "growth_articles"
    articles_dir.mkdir()
    monkeypatch.setattr(libra_app, "GROWTH_ARTICLES_DIR", articles_dir)
    return TestClient(libra_app.app)


def _write_listing(kdp_dir, slug, **overrides):
    book_dir = kdp_dir / slug
    book_dir.mkdir(parents=True, exist_ok=True)
    listing = {
        "title": "Test Book Title",
        "description": "A useful description of the test book.",
        "status": "uploaded",
        "asin": "B0TESTASIN1",
        "live_status": "LIVE",
    }
    listing.update(overrides)
    (book_dir / "listing.json").write_text(json.dumps(listing))
    return listing


def _write_article(articles_dir, article_id, **overrides):
    article = {
        "title": "Test Article Title",
        "body": "First paragraph.\n\nSecond paragraph.",
        "target_slug": "book-a",
        "campaign": "article-1",
    }
    article.update(overrides)
    (articles_dir / f"{article_id}.json").write_text(json.dumps(article))
    return article


# ── Verbatim test from the task brief ──────────────────────────────────────

def test_outbound_click_records_once_and_redirects(client, ledger):
    token = make_tracking_token("book-a", "organic-1", "https://www.amazon.com/dp/ASIN")
    response = client.get(f"/growth/out/{token}", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"] == "https://www.amazon.com/dp/ASIN"
    assert count_events(ledger, event_kind="amazon_outbound") == 1


# ── Outbound click route ────────────────────────────────────────────────────

def test_repeated_clicks_are_recorded_as_separate_events(client, ledger):
    token = make_tracking_token("book-a", "organic-1", "https://www.amazon.com/dp/ASIN")

    client.get(f"/growth/out/{token}", follow_redirects=False)
    client.get(f"/growth/out/{token}", follow_redirects=False)

    assert count_events(ledger, event_kind="amazon_outbound") == 2
    with sqlite3.connect(ledger) as connection:
        keys = [row[0] for row in connection.execute("SELECT event_key FROM hub_events")]
    assert len(keys) == len(set(keys))


def test_outbound_click_rejects_forged_token(client, ledger):
    init_ledger(ledger)

    response = client.get("/growth/out/not-a-real-token", follow_redirects=False)

    assert response.status_code == 404
    assert count_events(ledger, event_kind="amazon_outbound") == 0


def test_outbound_click_never_stores_ip_or_user_agent(client, ledger):
    token = make_tracking_token("book-a", "organic-1", "https://www.amazon.com/dp/ASIN")

    client.get(
        f"/growth/out/{token}",
        follow_redirects=False,
        headers={"User-Agent": "SecretBrowser/1.0 (tracking-me)"},
    )

    with sqlite3.connect(ledger) as connection:
        row = connection.execute(
            "SELECT slug, campaign, event_kind, payload_json FROM hub_events"
        ).fetchone()
    assert row == ("book-a", "organic-1", "amazon_outbound", "{}")
    dumped = json.dumps(row)
    assert "SecretBrowser" not in dumped
    assert "127.0.0.1" not in dumped
    assert "testclient" not in dumped.lower()


# ── /growth/books/{slug} ─────────────────────────────────────────────────────

def test_book_hub_page_renders_with_one_tracked_cta(client, ledger):
    _write_listing(libra_app.KDP_DIR, "book-a")

    response = client.get("/growth/books/book-a")

    assert response.status_code == 200
    body = response.text
    assert "Test Book Title" in body
    assert "A useful description of the test book." in body
    assert body.count("/growth/out/") == 1

    start = body.index('href="/growth/out/') + len('href="')
    end = body.index('"', start)
    cta_path = body[start:end]
    token = cta_path.rsplit("/", 1)[-1]
    payload = resolve_tracking_token(token)
    assert payload["slug"] == "book-a"
    assert payload["campaign"] == "content-hub"
    assert payload["destination"] == "https://www.amazon.com/dp/B0TESTASIN1"
    assert payload["destination_kind"] == "amazon"


def test_book_hub_page_unknown_slug_is_404(client):
    response = client.get("/growth/books/does-not-exist")

    assert response.status_code == 404


def test_book_hub_page_without_asin_is_404(client):
    _write_listing(libra_app.KDP_DIR, "book-a", asin=None)

    response = client.get("/growth/books/book-a")

    assert response.status_code == 404


def test_book_hub_page_with_live_status_live_is_200(client):
    """asin + live_status LIVE renders — the positive case matching the
    negative BLOCKED case below."""
    _write_listing(libra_app.KDP_DIR, "book-a", live_status="LIVE")

    response = client.get("/growth/books/book-a")

    assert response.status_code == 200


def test_book_hub_page_blocked_book_is_404(client):
    """A book that was pulled from Amazon (live_status BLOCKED) must never
    get a tracked CTA sending traffic to a dead ASIN — real example:
    acuarela-para-principiantes-guia-paso-a-paso, blocked 11 Jul 2026."""
    _write_listing(libra_app.KDP_DIR, "book-a", live_status="BLOCKED")

    response = client.get("/growth/books/book-a")

    assert response.status_code == 404


def test_book_hub_page_missing_live_status_is_404(client):
    """Fail closed: no live_status field at all must not be treated as live."""
    _write_listing(libra_app.KDP_DIR, "book-a", live_status=None)

    response = client.get("/growth/books/book-a")

    assert response.status_code == 404


def test_book_hub_page_escapes_html_in_listing_data(client):
    _write_listing(libra_app.KDP_DIR, "book-a", title="<script>alert(1)</script>")

    response = client.get("/growth/books/book-a")

    assert "<script>alert(1)</script>" not in response.text
    assert "&lt;script&gt;" in response.text


# ── /growth/articles/{article_id} ───────────────────────────────────────────

def test_article_hub_page_renders_with_one_tracked_cta(client, ledger):
    _write_listing(libra_app.KDP_DIR, "book-a")
    _write_article(libra_app.GROWTH_ARTICLES_DIR, "article-1")

    response = client.get("/growth/articles/article-1")

    assert response.status_code == 200
    body = response.text
    assert "Test Article Title" in body
    assert "First paragraph." in body
    assert "Second paragraph." in body
    assert body.count("/growth/out/") == 1

    start = body.index('href="/growth/out/') + len('href="')
    end = body.index('"', start)
    token = body[start:end].rsplit("/", 1)[-1]
    payload = resolve_tracking_token(token)
    assert payload["slug"] == "book-a"
    assert payload["campaign"] == "article-1"
    assert payload["destination"] == "https://www.amazon.com/dp/B0TESTASIN1"
    assert payload["destination_kind"] == "amazon"


def test_article_hub_page_unknown_id_is_404(client):
    response = client.get("/growth/articles/does-not-exist")

    assert response.status_code == 404


def test_article_hub_page_missing_target_book_is_404(client):
    _write_article(libra_app.GROWTH_ARTICLES_DIR, "article-1", target_slug="no-such-book")

    response = client.get("/growth/articles/article-1")

    assert response.status_code == 404


def test_article_hub_page_blocked_target_book_is_404(client):
    """Reuses _live_book_asin, so the BLOCKED gate must apply here too."""
    _write_listing(libra_app.KDP_DIR, "book-a", live_status="BLOCKED")
    _write_article(libra_app.GROWTH_ARTICLES_DIR, "article-1")

    response = client.get("/growth/articles/article-1")

    assert response.status_code == 404


# ── Missing tracking secret (fail closed, but as a clean 503) ──────────────

def test_book_hub_page_returns_503_when_tracking_secret_unset(client, monkeypatch):
    _write_listing(libra_app.KDP_DIR, "book-a")
    monkeypatch.delenv("LIBRA_GROWTH_TRACKING_SECRET", raising=False)

    response = client.get("/growth/books/book-a")

    assert response.status_code == 503


def test_outbound_click_returns_503_when_tracking_secret_unset(client, monkeypatch):
    token = make_tracking_token("book-a", "organic-1", "https://www.amazon.com/dp/ASIN")
    monkeypatch.delenv("LIBRA_GROWTH_TRACKING_SECRET", raising=False)

    response = client.get(f"/growth/out/{token}", follow_redirects=False)

    assert response.status_code == 503


# ── /api/growth/summary ─────────────────────────────────────────────────────

def test_growth_summary_api_reflects_recorded_clicks(client, ledger):
    token_a = make_tracking_token("book-a", "organic-1", "https://www.amazon.com/dp/ASIN")
    token_b = make_tracking_token("book-b", "reddit-1", "https://www.amazon.co.uk/dp/ASIN2")
    client.get(f"/growth/out/{token_a}", follow_redirects=False)
    client.get(f"/growth/out/{token_a}", follow_redirects=False)
    client.get(f"/growth/out/{token_b}", follow_redirects=False)

    summary = client.get("/api/growth/summary").json()

    assert summary["total_events"] == 3
    assert summary["by_event_kind"] == {"amazon_outbound": 3}
    assert summary["by_slug"]["book-a"]["total"] == 2
    assert summary["by_slug"]["book-b"]["total"] == 1


def test_growth_summary_api_empty(client):
    summary = client.get("/api/growth/summary").json()

    assert summary == {"total_events": 0, "by_event_kind": {}, "by_slug": {}}
