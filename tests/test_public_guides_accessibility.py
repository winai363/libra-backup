import json
from pathlib import Path
import re

from bs4 import BeautifulSoup
import pytest

import app as libra_app
from test_search_guides import client


THEME = Path(__file__).parents[1] / "templates" / "public_guides_theme.css"


def inherited_language(node):
    while node is not None:
        if node.get("lang"):
            return node["lang"]
        node = node.parent
    return None


def test_french_index_uses_english_ui_and_french_content(client):
    portfolio = libra_app.SEARCH_PORTFOLIO_FILE
    data = json.loads(portfolio.read_text())
    data["books"][0]["language"] = "fr"
    portfolio.write_text(json.dumps(data))
    soup = BeautifulSoup(client.get("/guides/").text, "html.parser")

    for selector in ("title", ".skip-link", ".site-bar nav", "h1", ".editorial-note"):
        assert inherited_language(soup.select_one(selector)) == "en", selector
    card = soup.select_one(".topic-card")
    assert inherited_language(card.h2) == "fr"
    assert inherited_language(card.select_one(".topic-card__copy > h2 + p")) == "fr"
    for selector in (".topic-card__count", ".topic-card__action"):
        assert inherited_language(card.select_one(selector)) == "en", selector


def test_french_card_accessible_name_preserves_each_parts_language(client):
    portfolio = libra_app.SEARCH_PORTFOLIO_FILE
    data = json.loads(portfolio.read_text())
    data["books"][1]["promotion_status"] = "APPROVED"
    portfolio.write_text(json.dumps(data))
    soup = BeautifulSoup(client.get("/guides/").text, "html.parser")
    card = soup.select_one('.topic-card[lang="fr"]')
    action = card.select_one(".topic-card__action")
    assert not action.has_attr("aria-label")
    labels = [soup.find(id=label_id) for label_id in action["aria-labelledby"].split()]
    assert [inherited_language(label) for label in labels] == ["en", "fr"]
    assert [label.get_text(" ", strip=True) for label in labels] == [
        "Explore 1 notes", "Guía visual para smartphone",
    ]
    assert inherited_language(card.select_one(".topic-card__count")) == "en"


@pytest.mark.parametrize("language, count, expected", [
    ("es", 1, "1 tema"), ("es", 2, "2 temas"),
    ("en", 1, "1 topic"), ("en", 2, "2 topics"),
    ("en", 0, "0 topics"),
])
def test_index_topic_count_uses_singular_only_for_one(client, language, count, expected):
    portfolio = libra_app.SEARCH_PORTFOLIO_FILE
    data = json.loads(portfolio.read_text())
    for book in data["books"]:
        book["language"] = language
        book["promotion_status"] = "APPROVED" if count else "HOLD_PROMOTION"
    if count == 1:
        data["books"] = data["books"][:1]
    portfolio.write_text(json.dumps(data))
    soup = BeautifulSoup(client.get("/guides/").text, "html.parser")
    assert soup.select_one(".site-meta").get_text(" ", strip=True) == expected


def test_french_topic_marks_fallback_ui_and_preserves_content_language(client):
    portfolio = libra_app.SEARCH_PORTFOLIO_FILE
    data = json.loads(portfolio.read_text())
    data["books"][0]["language"] = "fr"
    portfolio.write_text(json.dumps(data))
    soup = BeautifulSoup(client.get("/guides/rutinas-tdah-adultos").text, "html.parser")
    assert soup.html["lang"] == "fr"
    for selector in (
        ".skip-link", ".site-bar nav", ".site-meta", "#notes-title",
        ".dock-label", ".book-dock img", ".cta", ".editorial-note",
    ):
        assert inherited_language(soup.select_one(selector)) == "en", selector
    for selector in ("h1", ".topic-hero > h1 + p", "#book-title"):
        assert inherited_language(soup.select_one(selector)) == "fr", selector
    assert inherited_language(soup.select_one(".note-card")) == "es"


@pytest.mark.parametrize("language, article_id", [("es", "a1"), ("fr", "a1"), ("es", "held-a1")])
def test_article_marks_ui_and_editorial_without_overriding_content(client, language, article_id):
    path = libra_app.GROWTH_ARTICLES_DIR / f"{article_id}.json"
    article = json.loads(path.read_text())
    article["language"] = language
    path.write_text(json.dumps(article))
    soup = BeautifulSoup(client.get(f"/growth/articles/{article_id}").text, "html.parser")
    ui_language = "es" if language == "es" and article_id == "a1" else "en"
    assert soup.html["lang"] == language
    for selector in (
        ".skip-link", ".article-header .eyebrow", ".disclosure", ".disclosure strong",
        ".dock-label", ".book-dock img",
    ):
        assert inherited_language(soup.select_one(selector)) == ui_language, selector
    if article_id == "a1":
        assert inherited_language(soup.select_one(".site-bar nav")) == ui_language
        assert inherited_language(soup.select_one(".site-bar nav a:last-child")) == "es"
        assert inherited_language(soup.select_one(".related h2")) == ui_language
    for selector in ("h1", ".article-summary", ".article-body", ".disclosure p", ".cta"):
        assert inherited_language(soup.select_one(selector)) == language, selector
    editorial = soup.select_one(".editorial")
    assert inherited_language(editorial) == "en"
    assert editorial.get_text(strip=True).startswith("Prepared by Libra.")


def test_spanish_index_marks_fixed_english_brand_word(client):
    soup = BeautifulSoup(client.get("/guides/").text, "html.parser")
    assert inherited_language(soup.select_one(".brand span")) == "en"


def test_portuguese_article_marks_english_disclaimer_fallback(client):
    path = libra_app.GROWTH_ARTICLES_DIR / "a1.json"
    article = json.loads(path.read_text())
    article.update({"language": "pt", "title": "Uma rotina simples", "body": "Um passo por vez."})
    path.write_text(json.dumps(article))
    soup = BeautifulSoup(client.get("/growth/articles/a1").text, "html.parser")

    assert soup.html["lang"] == "pt"
    assert inherited_language(soup.h1) == "pt"
    assert inherited_language(soup.select_one(".article-body")) == "pt"
    disclosure = soup.select_one(".disclosure p")
    assert disclosure.get_text(strip=True) == (
        "Practical educational note. It does not replace professional advice or promise results."
    )
    assert inherited_language(disclosure) == "en"


def test_index_has_skip_link_landmarks_and_one_h1(client):
    soup = BeautifulSoup(client.get("/guides/").text, "html.parser")
    assert soup.select_one('a.skip-link[href="#main-content"]')
    assert soup.select_one("header.site-bar nav[aria-label]")
    assert soup.select_one("main#main-content")
    assert len(soup.find_all("h1")) == 1


def test_index_is_spanish_when_every_public_topic_is_spanish(client):
    soup = BeautifulSoup(client.get("/guides/").text, "html.parser")
    assert soup.html["lang"] == "es"
    assert soup.h1.get_text(" ", strip=True) == "Pequeños sistemas. Días más claros."
    assert soup.title.string == "Guías prácticas de Libra"
    assert soup.select_one(".site-meta").get_text(" ", strip=True) == "1 tema"
    assert soup.select_one(".topic-card")["lang"] == "es"


def test_index_uses_english_shell_for_mixed_public_languages(client):
    portfolio = libra_app.SEARCH_PORTFOLIO_FILE
    data = json.loads(portfolio.read_text())
    data["books"][1]["promotion_status"] = "APPROVED"
    portfolio.write_text(json.dumps(data))
    soup = BeautifulSoup(client.get("/guides/").text, "html.parser")
    assert soup.html["lang"] == "en"
    assert soup.h1.get_text(" ", strip=True) == "Small systems. Clearer days."
    assert {card["lang"] for card in soup.select(".topic-card")} == {"es", "fr"}


def test_index_injects_shared_theme_without_unresolved_placeholders(client, monkeypatch):
    css = "/* Shared guide theme */ body { color: #101828; }"
    monkeypatch.setattr(libra_app, "_public_guides_theme", lambda: css)
    response = client.get("/guides/")
    soup = BeautifulSoup(response.text, "html.parser")
    assert soup.style.string == css
    assert "{{" not in response.text
    assert soup.select_one(".topic-card__action")["href"] == "/libra/guides/rutinas-tdah-adultos"


def test_topic_has_localized_skip_link_named_landmarks_and_one_h1(client):
    soup = BeautifulSoup(client.get("/guides/rutinas-tdah-adultos").text, "html.parser")

    assert soup.select_one('a.skip-link[href="#main-content"]').get_text(strip=True) == "Saltar al contenido"
    assert soup.select_one("header.site-bar nav")["aria-label"] == "Navegación principal"
    main = soup.select_one("main#main-content.topic-shell")
    assert main
    assert len(soup.find_all("h1")) == 1
    assert main.select_one('section[aria-labelledby="notes-title"] h2#notes-title')
    assert main.select_one('aside.book-dock[aria-labelledby="book-title"] h2#book-title')
    assert main.select_one('aside.editorial-note[aria-labelledby="method-title"] h2#method-title')
    assert [child.name for child in soup.select_one(".topic-layout").find_all(recursive=False)] == ["section", "aside"]


def test_topic_injects_shared_theme_without_unresolved_placeholders(client, monkeypatch):
    css = "/* Shared topic theme */ body { color: #101828; }"
    monkeypatch.setattr(libra_app, "_public_guides_theme", lambda: css)
    response = client.get("/guides/rutinas-tdah-adultos")
    soup = BeautifulSoup(response.text, "html.parser")

    assert soup.style.string == css
    assert "{{" not in response.text
    assert soup.select_one(".site-bar a")["href"] == "/libra/guides/"


def test_theme_has_accessibility_preferences_and_target_floor():
    css = THEME.read_text(encoding="utf-8")
    for contract in (
        "min-height: 44px", "min-width: 44px", ":focus-visible",
        "prefers-reduced-motion: reduce", "prefers-contrast: more",
        "prefers-reduced-transparency: reduce",
    ):
        assert contract in css


def test_theme_keeps_content_opaque_and_glass_progressive():
    css = THEME.read_text(encoding="utf-8")
    for selector in (".reading-surface", ".topic-card", ".note-card"):
        rule = re.search(r"^" + re.escape(selector) + r"\s*\{([^}]+)\}", css, re.M)
        assert rule, selector
        assert "background: var(--white)" in rule.group(1)
        assert "backdrop-filter" not in rule.group(1)
    assert css.index(".glass-control {") < css.index("@supports (backdrop-filter:")
    assert ".focus-lens" in css
    assert "max-width: 68ch" in css
    assert "text-decoration: underline" in css
    assert "max-width: 760px" in css
    assert "max-width: 420px" in css
    assert "--r-pill: 999px" in css


def test_article_has_named_landmarks_and_one_h1(client):
    soup = BeautifulSoup(client.get("/growth/articles/a1?c=search-surface").text, "html.parser")

    assert soup.body["class"] == ["article-page"]
    assert soup.select_one('a.skip-link[href="#article-content"]')
    main = soup.select_one("main#article-content.reading-surface")
    assert main
    assert len(soup.find_all("main")) == 1
    assert len(soup.find_all("h1")) == 1
    assert main.select_one("header.article-header h1")
    assert main.select_one(".article-body p")
    assert main.select_one('aside.disclosure[aria-label] strong')
    assert main.select_one('aside.book-dock[aria-labelledby="article-book-title"] h2#article-book-title')
    assert main.select_one('aside.related[aria-labelledby="related-title"] h2#related-title')


def test_article_injects_shared_theme_without_unresolved_placeholders(client, monkeypatch):
    css = "/* Shared article theme */ body { color: #101828; }"
    monkeypatch.setattr(libra_app, "_public_guides_theme", lambda: css)
    response = client.get("/growth/articles/a1?c=search-surface")
    soup = BeautifulSoup(response.text, "html.parser")

    assert soup.style.string == css
    assert "{{" not in response.text


def test_synthetic_browser_audit_requests_do_not_write_business_ledger(client, monkeypatch):
    def unexpected_event(*args, **kwargs):
        raise AssertionError("Synthetic accessibility audit must not record business events")

    monkeypatch.setattr(libra_app, "record_hub_event", unexpected_event)
    for path in (
        "/guides/", "/guides/rutinas-tdah-adultos",
        "/growth/articles/a1?c=search-surface",
    ):
        response = client.get(path, headers={
            "User-Agent": "Mozilla/5.0 Chrome/140.0.0.0 Safari/537.36",
            "X-Libra-Measurement": "synthetic",
        })
        assert response.status_code == 200
    assert not libra_app.PROFIT_LEDGER_FILE.exists()
