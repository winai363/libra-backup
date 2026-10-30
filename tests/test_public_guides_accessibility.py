import json
from pathlib import Path
import re

from bs4 import BeautifulSoup

import app as libra_app
from test_search_guides import client


THEME = Path(__file__).parents[1] / "templates" / "public_guides_theme.css"


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
