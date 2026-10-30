import json
import sqlite3

import pytest
from bs4 import BeautifulSoup
from fastapi.testclient import TestClient

import app as libra_app
from content_hub import resolve_tracking_token


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
        "campaign": "search-surface",
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
    campaigns.write_text(json.dumps({"campaigns": [
        "content-hub", "search-surface", "unattributed", "pin-adhd-es",
    ]}))
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
        "campaign": "pin-adhd-es",
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
    assert '<link rel="canonical" href="https://newton-winai-klinprasom.incomeinclick.in.th/libra/guides/">' in response.text
    assert "artículos revisados individualmente" in response.text
    assert "El contenido es educativo" in response.text


def test_topic_page_links_to_articles_and_one_tracked_amazon_cta(client):
    response = client.get("/guides/rutinas-tdah-adultos")

    assert response.status_code == 200
    assert "/libra/growth/articles/a1" in response.text
    assert response.text.count("/libra/growth/out/") == 1
    assert "Cuaderno práctico" in response.text
    assert "?c=search-surface" in response.text
    assert "Cómo se prepara esta página" in response.text
    assert "no se pueden vincular a una compra" in response.text
    soup = BeautifulSoup(response.text, "html.parser")
    cta = soup.select_one(".book-dock .cta")
    assert set(cta["rel"]) == {"nofollow", "sponsored"}
    payload = resolve_tracking_token(cta["href"].rsplit("/", 1)[1])
    assert {key: payload[key] for key in ("slug", "campaign", "destination")} == {
        "slug": "book-a", "campaign": "search-surface",
        "destination": "https://www.amazon.com/dp/B0TESTASIN1",
    }


def test_spanish_topic_uses_localized_controls_and_meaningful_cover_alt(client):
    soup = BeautifulSoup(client.get("/guides/rutinas-tdah-adultos").text, "html.parser")

    assert soup.html["lang"] == "es"
    assert soup.select_one(".site-bar a").get_text(" ", strip=True) == "← Todas las guías"
    assert soup.select_one(".site-meta").get_text(" ", strip=True) == "2 notas"
    assert soup.select_one("#notes-title").get_text(strip=True) == "Notas prácticas"
    assert soup.select_one(".dock-label").get_text(strip=True) == "Cuaderno relacionado"
    assert soup.select_one(".book-dock img")["alt"] == "Portada de Cuaderno práctico"
    assert soup.select_one(".book-dock .cta").get_text(" ", strip=True) == "Ver el libro en Amazon"


def test_topic_note_cards_are_single_links_with_descriptions(client):
    soup = BeautifulSoup(client.get("/guides/rutinas-tdah-adultos").text, "html.parser")
    cards = soup.select(".note-card")

    assert len(cards) == 2
    assert len(soup.select(".note-grid > li")) == 2
    for card in cards:
        assert card.name == "a"
        assert len(card.parent.select("a[href]")) == 1
        assert card.select_one(".note-card__summary").get_text(strip=True) == "A useful article."
        assert card["href"].endswith("?c=search-surface")


@pytest.mark.parametrize("language", ["en", "fr"])
def test_topic_uses_english_fallback_for_non_spanish_inventory(client, language):
    portfolio = libra_app.SEARCH_PORTFOLIO_FILE
    data = json.loads(portfolio.read_text())
    data["books"][0]["language"] = language
    portfolio.write_text(json.dumps(data))
    soup = BeautifulSoup(client.get("/guides/rutinas-tdah-adultos").text, "html.parser")

    assert soup.html["lang"] == language
    assert soup.select_one(".skip-link").get_text(strip=True) == "Skip to content"
    assert soup.select_one(".site-bar nav")["aria-label"] == "Primary navigation"
    assert soup.select_one(".site-bar a").get_text(" ", strip=True) == "← All guides"
    assert soup.select_one(".site-meta").get_text(strip=True) == "2 notes"
    assert soup.select_one("#notes-title").get_text(strip=True) == "Practical notes"
    assert soup.select_one(".dock-label").get_text(strip=True) == "Related workbook"
    assert soup.select_one(".book-dock img")["alt"] == "Cover of Cuaderno práctico"
    assert soup.select_one(".cta").get_text(strip=True) == "View the book on Amazon"
    assert soup.select_one("#method-title").get_text(strip=True) == "How this page is prepared"
    assert "cannot be tied to a purchase" in soup.select_one(".editorial-note").get_text()


def test_held_and_unknown_topic_are_404(client):
    assert client.get("/guides/smartphone-seniors-fr").status_code == 404
    assert client.get("/guides/unknown").status_code == 404


def test_eligible_article_has_topic_navigation_related_links_and_schema(client):
    response = client.get("/growth/articles/a1?c=search-surface")

    assert response.status_code == 200
    assert "/libra/guides/rutinas-tdah-adultos" in response.text
    assert "/libra/growth/articles/a2" in response.text
    assert "/libra/growth/articles/a2?c=search-surface" in response.text
    assert "application/ld+json" in response.text
    assert '"@type": "Article"' in response.text
    assert "Nota educativa práctica" in response.text
    assert "Prepared by Libra" in response.text
    start = response.text.index('href="/libra/growth/out/') + len('href="/libra/growth/out/')
    token = response.text[start:response.text.index('"', start)]
    assert resolve_tracking_token(token)["campaign"] == "search-surface"


def test_article_has_skip_link_reading_landmark_and_localized_related_heading(client):
    soup = BeautifulSoup(client.get("/growth/articles/a1?c=search-surface").text, "html.parser")

    assert soup.select_one('a.skip-link[href="#article-content"]').get_text(strip=True) == "Saltar al artículo"
    assert soup.select_one("main#article-content.reading-surface")
    assert soup.find("h2", string="Sigue leyendo")
    assert soup.select_one(".book-dock img")["alt"] == "Portada de Cuaderno práctico"
    assert soup.select_one(".article-header .eyebrow").get_text(strip=True) == "Nota práctica"
    assert soup.select_one(".article-summary").get_text(strip=True) == "A useful article."
    assert soup.select_one(".dock-label").get_text(strip=True) == "Cuaderno relacionado"
    assert soup.select_one(".disclosure")["aria-label"] == "Aviso educativo"
    assert soup.select_one(".disclosure p").get_text(strip=True) == (
        "Nota educativa práctica. No sustituye asesoramiento médico ni promete resultados."
    )
    nav = soup.select_one(".site-bar nav")
    assert nav["aria-label"] == "Navegación principal"
    assert nav.select_one('a[href="/libra/guides/"]').get_text(" ", strip=True) == "Todas las guías"
    assert nav.select_one('a[href="/libra/guides/rutinas-tdah-adultos"]')


def test_article_related_cards_keep_one_tagged_link_each(client):
    soup = BeautifulSoup(client.get("/growth/articles/a1?c=search-surface").text, "html.parser")
    cards = soup.select(".related-grid > .related-card")

    assert len(cards) == 1
    assert len(cards[0].select("a[href]")) == 1
    assert cards[0].a["href"].endswith("/growth/articles/a2?c=search-surface")
    assert soup.select_one('.related[aria-labelledby="related-title"] h2#related-title')


def test_direct_article_related_cards_do_not_acquire_search_attribution(client):
    soup = BeautifulSoup(client.get("/growth/articles/a1").text, "html.parser")
    assert soup.select_one(".related-card a")["href"] == "/libra/growth/articles/a2"


def test_article_english_controls_for_non_spanish_inventory(client):
    article = libra_app.GROWTH_ARTICLES_DIR / "a1.json"
    payload = json.loads(article.read_text())
    payload["language"] = "en"
    article.write_text(json.dumps(payload))
    soup = BeautifulSoup(client.get("/growth/articles/a1?c=search-surface").text, "html.parser")

    assert soup.select_one(".skip-link").get_text(strip=True) == "Skip to article"
    assert soup.select_one(".article-header .eyebrow").get_text(strip=True) == "Practical note"
    assert soup.select_one(".disclosure")["aria-label"] == "Educational notice"
    assert soup.select_one(".dock-label").get_text(strip=True) == "Related workbook"
    assert soup.select_one(".book-dock img")["alt"] == "Cover of Cuaderno práctico"
    assert soup.select_one(".related h2").get_text(strip=True) == "Read next"


def test_tagged_read_next_keeps_search_surface_for_next_cta(client):
    first = client.get("/growth/articles/a1?c=search-surface")
    assert "/libra/growth/articles/a2?c=search-surface" in first.text

    second = client.get("/growth/articles/a2?c=search-surface")
    start = second.text.index('href="/libra/growth/out/') + len('href="/libra/growth/out/')
    token = second.text[start:second.text.index('"', start)]
    assert resolve_tracking_token(token)["campaign"] == "search-surface"


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


def test_synthetic_owner_and_test_requests_do_not_count(client):
    ledger = libra_app.PROFIT_LEDGER_FILE
    for marker in ("synthetic", "owner", "test"):
        client.get(
            "/guides/rutinas-tdah-adultos",
            headers={"User-Agent": READER_UA, "X-Libra-Measurement": marker},
        )

    assert not ledger.exists()


def test_direct_article_request_is_not_labeled_as_search(client):
    response = client.get("/growth/articles/a1", headers={"User-Agent": READER_UA})
    start = response.text.index('href="/libra/growth/out/') + len('href="/libra/growth/out/')
    token = response.text[start:response.text.index('"', start)]

    assert resolve_tracking_token(token)["campaign"] == "unattributed"
    with sqlite3.connect(libra_app.PROFIT_LEDGER_FILE) as connection:
        campaign = connection.execute(
            "SELECT campaign FROM hub_events WHERE event_kind='qualified_view'"
        ).fetchone()[0]
    assert campaign == "unattributed"


def test_json_ld_neutralizes_mixed_case_script_end_tag(client):
    article = libra_app.GROWTH_ARTICLES_DIR / "a1.json"
    payload = json.loads(article.read_text())
    payload["title"] = "Safe </SCRIPT><script>alert(1)</script> title"
    article.write_text(json.dumps(payload))

    response = client.get("/growth/articles/a1?c=search-surface")

    assert "</SCRIPT>" not in response.text
    assert "\\u003c/SCRIPT>" in response.text


def test_article_view_counts_only_when_article_is_search_eligible(client):
    ledger = libra_app.PROFIT_LEDGER_FILE
    client.get("/growth/articles/a1", headers={"User-Agent": READER_UA})
    client.get("/growth/articles/held-a1", headers={"User-Agent": READER_UA})

    assert event_count(ledger, "qualified_view") == 1


def test_guides_language_uses_the_only_public_language():
    assert libra_app._guides_language([{"language": "es"}, {"language": "es"}]) == "es"
    assert libra_app._guides_language([{"language": "es"}, {"language": "en"}]) == "en"
    assert libra_app._guides_language([]) == "en"


def test_topic_feature_has_one_non_overlapping_link():
    html = libra_app._topic_cards([{
        "slug": "book-a", "topic": "focus", "language": "es",
        "title": "Rutinas prácticas", "description": "Pasos claros.",
        "articles": [{"id": "a1"}, {"id": "a2"}],
    }])
    soup = BeautifulSoup(html, "html.parser")
    card = soup.select_one(".topic-card")
    assert len(card.select("a[href]")) == 1
    assert card.select_one("a[href]").get("aria-label") == "Explorar 2 notas: Rutinas prácticas"


def test_topic_article_card_has_one_tagged_link():
    html = libra_app._article_cards({"articles": [{
        "id": "a1", "title": "Una nota", "description": "Descripción",
    }]})
    soup = BeautifulSoup(html, "html.parser")
    first = soup.select_one(".note-card")
    assert first.name == "a"
    assert len(first.parent.select("a[href]")) == 1
    assert first["href"].endswith("/growth/articles/a1?c=search-surface")


@pytest.mark.parametrize("campaign,suffix", [
    ("search-surface", "?c=search-surface"), ("unattributed", ""),
])
def test_related_cards_preserve_campaign_attribution(campaign, suffix):
    soup = BeautifulSoup(libra_app._related_cards([
        {"id": "a2", "title": "Otra nota"},
    ], campaign), "html.parser")
    card = soup.select_one(".related-card")
    assert len(card.select("a[href]")) == 1
    assert card.a["href"] == f"/libra/growth/articles/a2{suffix}"
    assert card.a.get_text(strip=True) == "Otra nota→"


def test_public_guides_theme_reads_utf8_file(tmp_path, monkeypatch):
    theme = tmp_path / "public_guides_theme.css"
    theme.write_text("/* Guías */\n:root { color-scheme: light; }", encoding="utf-8")
    monkeypatch.setattr(libra_app, "PUBLIC_GUIDES_THEME_FILE", theme)
    assert libra_app._public_guides_theme() == theme.read_text(encoding="utf-8")


def test_index_copy_supports_spanish_and_english_fallback():
    assert libra_app._index_copy("es")["skip"] == "Saltar al contenido"
    assert libra_app._index_copy("es")["meta_title"] == "Guías prácticas de Libra"
    assert libra_app._index_copy("fr") == libra_app._index_copy("en")


def test_card_renderers_escape_dynamic_content():
    unsafe = '\"><script>alert("x")</script>'
    article = {"id": unsafe, "title": unsafe, "description": unsafe, "language": unsafe}
    topic = {
        "slug": unsafe, "topic": unsafe, "language": unsafe,
        "title": unsafe, "description": unsafe, "articles": [article],
    }
    for html in (
        libra_app._topic_cards([topic]), libra_app._article_cards(topic),
        libra_app._related_cards([article], "search-surface"),
    ):
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert not BeautifulSoup(html, "html.parser").select("script")
