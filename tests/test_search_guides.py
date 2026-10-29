import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import app as libra_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("LIBRA_GROWTH_TRACKING_SECRET", "guide-test-secret")
    kdp_dir = tmp_path / "kdp"
    articles_dir = tmp_path / "articles"
    kdp_dir.mkdir()
    articles_dir.mkdir()
    monkeypatch.setattr(libra_app, "KDP_DIR", kdp_dir)
    monkeypatch.setattr(libra_app, "GROWTH_ARTICLES_DIR", articles_dir)
    portfolio = tmp_path / "search_portfolio.json"
    portfolio.write_text(json.dumps({
        "schema": "libra-search-portfolio/1",
        "campaign": "seo-search",
        "release": {"state": "ACTIVE"},
        "books": [
            {
                "slug": "book-a", "topic": "rutinas-tdah-adultos", "language": "es",
                "promotion_status": "APPROVED", "title": "Rutinas prácticas",
                "description": "Sistemas sencillos para el día a día.",
            },
            {
                "slug": "held-book", "topic": "smartphone-seniors-fr", "language": "fr",
                "promotion_status": "HOLD_PROMOTION", "title": "Guía visual para smartphone",
                "description": "Guide en attente.",
            },
        ],
    }))
    monkeypatch.setattr(libra_app, "SEARCH_PORTFOLIO_FILE", portfolio)
    campaigns = tmp_path / "growth_campaigns.json"
    campaigns.write_text(json.dumps({"campaigns": ["content-hub", "seo-search"]}))
    monkeypatch.setattr(libra_app, "GROWTH_CAMPAIGNS_FILE", campaigns)
    ledger = tmp_path / "libra-business.db"
    monkeypatch.setattr(libra_app, "PROFIT_LEDGER_FILE", ledger)
    write_listing(kdp_dir, "book-a")
    write_listing(kdp_dir, "held-book")
    write_article(articles_dir, "a1")
    write_article(articles_dir, "a2")
    write_article(articles_dir, "held-a1", target_slug="held-book")
    return TestClient(libra_app.app)


READER_UA = "Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 Safari/604.1"
GOOGLEBOT_UA = "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"


def event_count(ledger, event_kind):
    with sqlite3.connect(ledger) as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM hub_events WHERE event_kind=?", (event_kind,)
        ).fetchone()[0]


def write_listing(kdp_dir, slug):
    book_dir = kdp_dir / slug
    book_dir.mkdir()
    (book_dir / "listing.json").write_text(json.dumps({
        "title": "Cuaderno práctico",
        "description": "Ejercicios sencillos.",
        "asin": "B0TESTASIN1",
        "live_status": "LIVE",
    }))


def write_article(articles_dir, article_id, *, target_slug="book-a"):
    (articles_dir / f"{article_id}.json").write_text(json.dumps({
        "qa_approved": True,
        "title": f"Article {article_id}",
        "description": "A useful article.",
        "body": "First useful step.\n\nSecond useful step.",
        "target_slug": target_slug,
        "campaign": "seo-search",
        "language": "es",
        "book_line": "Ejercicios para practicar en papel.",
        "cta_label": "Ver el cuaderno en Amazon",
        "published_at": "2026-09-12T09:00:00+07:00",
    }))


def test_guides_index_lists_only_approved_live_topics(client):
    response = client.get("/guides/")

    assert response.status_code == 200
    assert "Rutinas prácticas" in response.text
    assert "Guía visual para smartphone" not in response.text
    assert "/libra/guides/rutinas-tdah-adultos" in response.text


def test_topic_page_links_to_articles_and_one_tracked_amazon_cta(client):
    response = client.get("/guides/rutinas-tdah-adultos")

    assert response.status_code == 200
    assert "/libra/growth/articles/a1" in response.text
    assert response.text.count("/libra/growth/out/") == 1
    assert "Cuaderno práctico" in response.text


def test_held_and_unknown_topic_are_404(client):
    assert client.get("/guides/smartphone-seniors-fr").status_code == 404
    assert client.get("/guides/unknown").status_code == 404


def test_eligible_article_has_topic_navigation_related_links_and_schema(client):
    response = client.get("/growth/articles/a1")

    assert response.status_code == 200
    assert "/libra/guides/rutinas-tdah-adultos" in response.text
    assert "/libra/growth/articles/a2" in response.text
    assert "application/ld+json" in response.text
    assert '"@type": "Article"' in response.text
    assert "Nota educativa práctica" in response.text


def test_held_book_article_is_not_added_to_search_navigation(client):
    response = client.get("/growth/articles/held-a1")

    assert response.status_code == 200
    assert "/libra/guides/" not in response.text


def test_sitemap_contains_only_approved_live_public_urls(client):
    response = client.get("/sitemap.xml")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/xml")
    assert "/libra/guides/rutinas-tdah-adultos" in response.text
    assert "/libra/growth/articles/a1" in response.text
    assert "held-a1" not in response.text


def test_reader_view_counts_but_googlebot_does_not(client):
    ledger = libra_app.PROFIT_LEDGER_FILE
    client.get("/guides/rutinas-tdah-adultos", headers={"User-Agent": READER_UA})
    client.get("/guides/rutinas-tdah-adultos", headers={"User-Agent": GOOGLEBOT_UA})

    assert event_count(ledger, "qualified_view") == 1


def test_article_view_counts_only_when_article_is_search_eligible(client):
    ledger = libra_app.PROFIT_LEDGER_FILE
    client.get("/growth/articles/a1", headers={"User-Agent": READER_UA})
    client.get("/growth/articles/held-a1", headers={"User-Agent": READER_UA})

    assert event_count(ledger, "qualified_view") == 1
