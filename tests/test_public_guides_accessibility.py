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
