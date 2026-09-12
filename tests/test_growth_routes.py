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
        "qa_approved": True,
        "title": "Test Article Title",
        "body": "First paragraph.\n\nSecond paragraph.",
        "target_slug": "book-a",
        "campaign": "article-1",
        "language": "es",
        "description": "A one-line summary of the test article.",
        "book_line": "A short honest line about the book.",
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


def test_crawler_click_redirects_without_recording(client, ledger):
    """A crawler or link-preview fetcher must still reach Amazon, but must not
    inflate the click count that the organic experiment is measured on."""
    init_ledger(ledger)
    token = make_tracking_token("book-a", "organic-1", "https://www.amazon.com/dp/ASIN")

    response = client.get(
        f"/growth/out/{token}",
        follow_redirects=False,
        headers={"User-Agent": "facebookexternalhit/1.1 (+http://www.facebook.com/externalhit_uatext.php)"},
    )

    assert response.status_code == 307
    assert response.headers["location"] == "https://www.amazon.com/dp/ASIN"
    assert count_events(ledger, event_kind="amazon_outbound") == 0


def test_click_without_user_agent_is_not_recorded(client, ledger):
    init_ledger(ledger)
    token = make_tracking_token("book-a", "organic-1", "https://www.amazon.com/dp/ASIN")

    response = client.get(
        f"/growth/out/{token}", follow_redirects=False, headers={"User-Agent": ""},
    )

    assert response.status_code == 307
    assert count_events(ledger, event_kind="amazon_outbound") == 0


def test_in_app_browser_click_is_recorded(client, ledger):
    """Pinterest/Facebook in-app browsers are readers, not fetchers."""
    token = make_tracking_token("book-a", "organic-1", "https://www.amazon.com/dp/ASIN")

    client.get(
        f"/growth/out/{token}",
        follow_redirects=False,
        headers={"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 [Pinterest/iOS]"},
    )

    assert count_events(ledger, event_kind="amazon_outbound") == 1


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

def test_article_hub_page_renders_with_one_tracked_cta(client, ledger, campaigns_file):
    campaigns_file.write_text(json.dumps({"campaigns": ["article-1"]}))
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


# ── declared campaigns on the book hub page ─────────────────────────────────

@pytest.fixture
def campaigns_file(tmp_path, monkeypatch):
    path = tmp_path / "growth_campaigns.json"
    monkeypatch.setattr(libra_app, "GROWTH_CAMPAIGNS_FILE", path)
    return path


def _campaign_of_cta(body: str) -> str:
    start = body.index('href="/growth/out/') + len('href="/growth/out/')
    token = body[start:body.index('"', start)]
    return resolve_tracking_token(token)["campaign"]


def test_declared_campaign_tags_the_click(client, ledger, campaigns_file):
    campaigns_file.write_text(json.dumps({"campaigns": ["organic-pinterest"]}))
    _write_listing(libra_app.KDP_DIR, "book-a")

    response = client.get("/growth/books/book-a?c=organic-pinterest")

    assert response.status_code == 200
    assert _campaign_of_cta(response.text) == "organic-pinterest"


def test_undeclared_campaign_falls_back_to_the_default(client, ledger, campaigns_file):
    campaigns_file.write_text(json.dumps({"campaigns": ["organic-pinterest"]}))
    _write_listing(libra_app.KDP_DIR, "book-a")

    response = client.get("/growth/books/book-a?c=whatever-a-visitor-typed")

    assert _campaign_of_cta(response.text) == libra_app.GROWTH_HUB_CAMPAIGN


def test_missing_campaigns_file_still_serves_the_default(client, ledger, campaigns_file):
    _write_listing(libra_app.KDP_DIR, "book-a")

    response = client.get("/growth/books/book-a?c=organic-pinterest")

    assert response.status_code == 200
    assert _campaign_of_cta(response.text) == libra_app.GROWTH_HUB_CAMPAIGN


def test_campaign_click_is_recorded_under_its_own_campaign(client, ledger, campaigns_file):
    campaigns_file.write_text(json.dumps({"campaigns": ["organic-pinterest"]}))
    _write_listing(libra_app.KDP_DIR, "book-a")
    body = client.get("/growth/books/book-a?c=organic-pinterest").text
    start = body.index('href="/growth/out/') + len('href="')
    cta = body[start:body.index('"', start)]

    client.get(cta, follow_redirects=False,
               headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"})

    with sqlite3.connect(ledger) as connection:
        rows = connection.execute("SELECT slug, campaign FROM hub_events").fetchall()
    assert rows == [("book-a", "organic-pinterest")]


# ── gated RSS feed ──────────────────────────────────────────────────────────

@pytest.fixture
def authorization_file(tmp_path, monkeypatch):
    path = tmp_path / "posting_authorization.json"
    monkeypatch.setattr(libra_app.posting_authorization, "AUTHORIZATION_FILE", path)
    return path


def _authorize(path, channel="pinterest-rss"):
    path.write_text(json.dumps({"channels": {channel: {
        "authorized": True, "authorized_by": "Bui",
        "authorized_at": "2026-09-12T18:00:00+07:00"}}}))


def _declare_feed_campaign(campaigns_file, campaign="pin-adhd-es", channel="pinterest-rss"):
    campaigns_file.write_text(json.dumps({
        "campaigns": [campaign], "channels": {campaign: channel}}))


def _approved_article(articles_dir):
    (articles_dir / "adhd-routines-es.json").write_text(json.dumps({
        "qa_approved": True,
        "campaign": "pin-adhd-es",
        "title": "Tres rutinas cortas para el TDAH adulto",
        "description": "Una rutina de cinco minutos, una lista de dos columnas y un recordatorio "
                       "visible para empezar el dia sin perder el hilo.",
        "link": "/libra/growth/articles/adhd-routines-es",
        "image_url": "/libra/api/books/adhd-adults-workbook-es/cover",
        "published_at": "2026-09-10T08:00:00+00:00",
    }))


def test_feed_is_404_while_the_channel_is_not_authorized(client, authorization_file, campaigns_file):
    _declare_feed_campaign(campaigns_file)
    _approved_article(libra_app.GROWTH_ARTICLES_DIR)

    response = client.get("/growth/feed.xml")

    assert response.status_code == 404


def test_feed_serves_rss_once_the_owner_authorized_the_channel(client, authorization_file,
                                                               campaigns_file):
    _declare_feed_campaign(campaigns_file)
    _approved_article(libra_app.GROWTH_ARTICLES_DIR)
    _authorize(authorization_file)

    response = client.get("/growth/feed.xml")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/rss+xml")
    assert "<rss version=\"2.0\"" in response.text
    assert "/libra/growth/articles/adhd-routines-es" in response.text


def test_authorized_feed_omits_articles_that_are_not_qa_approved(client, authorization_file,
                                                                 campaigns_file):
    _declare_feed_campaign(campaigns_file)
    (libra_app.GROWTH_ARTICLES_DIR / "draft.json").write_text(json.dumps({
        "qa_approved": False, "campaign": "pin-adhd-es",
        "title": "A draft that must not be pinned",
        "description": "This description is long enough to pass the length rule on its own.",
        "link": "/libra/growth/articles/draft",
        "image_url": "/libra/api/books/adhd-adults-workbook-es/cover",
        "published_at": "2026-09-10T08:00:00+00:00",
    }))
    _authorize(authorization_file)

    response = client.get("/growth/feed.xml")

    assert response.status_code == 200
    assert "<item>" not in response.text


def test_feed_for_an_unknown_channel_name_stays_closed(client, authorization_file, campaigns_file):
    _declare_feed_campaign(campaigns_file)
    _approved_article(libra_app.GROWTH_ARTICLES_DIR)
    _authorize(authorization_file, channel="pinterest")

    response = client.get("/growth/feed.xml")

    assert response.status_code == 404


# ── article page conversion path ────────────────────────────────────────────

def test_article_without_qa_approval_is_404(client, ledger):
    _write_listing(libra_app.KDP_DIR, "book-a")
    _write_article(libra_app.GROWTH_ARTICLES_DIR, "draft-1", qa_approved=False)

    assert client.get("/growth/articles/draft-1").status_code == 404


def test_article_page_declares_its_own_language(client, ledger):
    _write_listing(libra_app.KDP_DIR, "book-a")
    _write_article(libra_app.GROWTH_ARTICLES_DIR, "article-1", language="pt")

    body = client.get("/growth/articles/article-1").text

    assert '<html lang="pt">' in body


def test_article_page_shows_what_the_button_leads_to(client, ledger):
    _write_listing(libra_app.KDP_DIR, "book-a", title="Cuaderno de Test")
    _write_article(libra_app.GROWTH_ARTICLES_DIR, "article-1")

    body = client.get("/growth/articles/article-1").text

    assert "Cuaderno de Test" in body
    assert "/libra/api/books/book-a/cover" in body
    assert "A short honest line about the book." in body
    # Still exactly one tracked link: the cover is an image, not a second CTA.
    assert body.count("/growth/out/") == 1


def test_article_page_carries_canonical_and_og_tags(client, ledger):
    _write_listing(libra_app.KDP_DIR, "book-a")
    _write_article(libra_app.GROWTH_ARTICLES_DIR, "article-1")

    body = client.get("/growth/articles/article-1").text

    assert f'rel="canonical" href="{libra_app.GROWTH_SITE_BASE}/libra/growth/articles/article-1"' in body
    assert 'property="og:image"' in body and "/cover" in body


def test_article_campaign_must_be_declared(client, ledger, campaigns_file):
    """An article file may not invent a campaign label either."""
    campaigns_file.write_text(json.dumps({"campaigns": ["pin-book-a"]}))
    _write_listing(libra_app.KDP_DIR, "book-a")
    _write_article(libra_app.GROWTH_ARTICLES_DIR, "article-1", campaign="made-up-label")

    body = client.get("/growth/articles/article-1").text
    start = body.index('href="/growth/out/') + len('href="/growth/out/')
    token = body[start:body.index('"', start)]

    assert resolve_tracking_token(token)["campaign"] == libra_app.GROWTH_HUB_CAMPAIGN


def test_declared_article_campaign_is_used(client, ledger, campaigns_file):
    campaigns_file.write_text(json.dumps({"campaigns": ["pin-book-a"]}))
    _write_listing(libra_app.KDP_DIR, "book-a")
    _write_article(libra_app.GROWTH_ARTICLES_DIR, "article-1", campaign="pin-book-a")

    body = client.get("/growth/articles/article-1").text
    start = body.index('href="/growth/out/') + len('href="/growth/out/')
    token = body[start:body.index('"', start)]

    assert resolve_tracking_token(token)["campaign"] == "pin-book-a"


def test_format_line_comes_from_the_roster_and_is_omitted_when_unknown(client, ledger, tmp_path):
    _write_listing(libra_app.KDP_DIR, "book-a")
    _write_article(libra_app.GROWTH_ARTICLES_DIR, "article-1")
    (libra_app.KDP_DIR / "bookshelf-roster.json").write_text(json.dumps({"entries": [
        {"slug": "book-a", "format": "ebook", "status": "LIVE", "price": 2.99, "currency": "USD"},
    ]}))

    assert "Kindle ebook · 2.99 USD" in client.get("/growth/articles/article-1").text

    (libra_app.KDP_DIR / "bookshelf-roster.json").write_text(json.dumps({"entries": []}))

    assert "Kindle ebook" not in client.get("/growth/articles/article-1").text


def test_feed_carries_only_its_own_channels_articles(client, authorization_file, campaigns_file):
    """An approved LinkedIn article must never turn into a Pin."""
    campaigns_file.write_text(json.dumps({
        "campaigns": ["pin-adhd-es", "li-contab-pt"],
        "channels": {"pin-adhd-es": "pinterest-rss", "li-contab-pt": "owner-post"}}))
    _approved_article(libra_app.GROWTH_ARTICLES_DIR)
    (libra_app.GROWTH_ARTICLES_DIR / "contabil.json").write_text(json.dumps({
        "qa_approved": True, "campaign": "li-contab-pt",
        "title": "Tres roteiros de IA para o escritorio contabil",
        "description": "Um texto em portugues que pertence ao canal do LinkedIn e nao ao Pinterest.",
        "link": "/libra/growth/articles/contabil",
        "image_url": "/libra/api/books/ai-workflows-accountants-pt/cover",
        "published_at": "2026-09-10T08:00:00+00:00",
    }))
    _authorize(authorization_file)

    body = client.get("/growth/feed.xml").text

    assert "adhd-routines-es" in body
    assert "contabil" not in body
