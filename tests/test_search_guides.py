import json

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
        "release": {"state": "PRE_RELEASE"},
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
    write_listing(kdp_dir, "book-a")
    write_listing(kdp_dir, "held-book")
    write_article(articles_dir, "a1")
    write_article(articles_dir, "held-a1", target_slug="held-book")
    return TestClient(libra_app.app)


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
