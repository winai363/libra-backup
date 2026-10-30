# Libra Calm Liquid Reader Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign all three public Libra guide surfaces as a distinctive iOS 27-inspired Calm Liquid Reader while meeting WCAG 2.1 AA and preserving the existing organic-search, attribution, KDP-freeze, and Pinterest-stop contracts.

**Architecture:** Keep FastAPI and the existing string-template renderer. Add one shared CSS source injected into the three public templates, and make the existing route helpers render escaped, language-aware view fragments with one link per card. Add deterministic HTML/accessibility contract tests plus a read-only Playwright/axe audit command for rendered pages.

**Tech Stack:** Python 3, FastAPI, existing `render_hub_page`, HTML/CSS, pytest, BeautifulSoup 4 (test-only, already installed), Playwright, axe-core 4.10.2 installed only into a temporary verification directory.

## Global Constraints

- Scope is exactly `/libra/guides/`, `/libra/guides/{topic}`, and eligible `/libra/growth/articles/{article}` pages.
- Liquid Glass is limited to navigation and primary controls; no glass-on-glass and no translucent reading surface.
- Native system font stack only; no CDN, remote font, JavaScript framework, analytics library, or runtime dependency.
- Normal text contrast is at least 4.5:1; large text, focus indicators, and UI boundaries are at least 3:1.
- Every interactive target is at least 44×44 CSS px with a visible `:focus-visible` indicator.
- Support `prefers-reduced-motion`, `prefers-contrast`, and `prefers-reduced-transparency` with an opaque base before `backdrop-filter`.
- Preserve canonical URLs, sitemap membership, Article JSON-LD, signed Amazon redirect, `search-surface`/`unattributed` classification, and synthetic/bot filtering.
- Preserve TOTAL KDP FREEZE, held/QA-required 404 behavior, and Pinterest feed 404 behavior.
- Do not stage existing runtime article moves, state files, locks, or `HANDOFF.md` changes.
- Use `X-Libra-Measurement: synthetic` for every browser or curl verification request.

---

## File map

- Create `templates/public_guides_theme.css`: all visual tokens, layout, focus, responsive, and accessibility preference rules shared by the three pages.
- Modify `app.py`: load the shared theme, calculate page language/copy, and render safe single-link topic/article/related fragments.
- Modify `templates/guides_index.html`: Spanish-first feature layout and Focus Lens.
- Modify `templates/guide_topic.html`: localized topic header, article-card list, and book dock.
- Modify `templates/hub_article.html`: reading surface, localized breadcrumb/disclosures, book dock, and related cards.
- Modify `tests/test_search_guides.py`: semantic, language, attribution, and copy regression coverage.
- Create `tests/test_public_guides_accessibility.py`: HTML structure, contrast-token, and CSS accessibility contract tests.
- Create `scripts/audit_public_guides.py`: read-only Playwright audit using a caller-supplied local `axe.min.js`.
- Create `tests/test_audit_public_guides.py`: unit tests for audit-result severity handling.

---

### Task 1: Shared presentation contract and safe card renderers

**Files:**
- Modify: `app.py:60-70`
- Modify: `app.py:1463-1675`
- Modify: `tests/test_search_guides.py`

**Interfaces:**
- Produces: `PUBLIC_GUIDES_THEME_FILE: Path`
- Produces: `_public_guides_theme() -> str`
- Produces: `_guides_language(topics: list[dict]) -> str`
- Produces: `_index_copy(language: str) -> dict[str, str]`
- Produces: `_topic_cards(topics: list[dict]) -> str` with exactly one anchor per topic card
- Produces: `_article_cards(topic: dict) -> str` with tagged `search-surface` URLs
- Produces: `_related_cards(rows: list[dict], campaign: str) -> str`
- Consumes: `_public_path`, `escape_text`, `SEARCH_CAMPAIGN`, and existing eligible topic/article dictionaries

- [ ] **Step 1: Add failing helper and single-link tests**

Append focused tests using BeautifulSoup:

```python
from bs4 import BeautifulSoup


def test_guides_language_uses_the_only_public_language():
    assert libra_app._guides_language([{"language": "es"}, {"language": "es"}]) == "es"
    assert libra_app._guides_language([{"language": "es"}, {"language": "en"}]) == "en"


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
    assert len(first.select("a[href]")) == 1
    assert first.a["href"].endswith("/growth/articles/a1?c=search-surface")
```

- [ ] **Step 2: Run tests and confirm the old structure fails**

Run:

```bash
python3 -m pytest \
  tests/test_search_guides.py::test_guides_language_uses_the_only_public_language \
  tests/test_search_guides.py::test_topic_feature_has_one_non_overlapping_link \
  tests/test_search_guides.py::test_topic_article_card_has_one_tagged_link -q
```

Expected: three failures because the language helper and article-card helper do not exist and the current topic card contains two links.

- [ ] **Step 3: Add shared-theme and renderer helpers**

Add the constant and helpers, keeping all dynamic values escaped:

```python
PUBLIC_GUIDES_THEME_FILE = Path(__file__).parent / "templates" / "public_guides_theme.css"


def _public_guides_theme() -> str:
    return PUBLIC_GUIDES_THEME_FILE.read_text(encoding="utf-8")


def _guides_language(topics: list[dict]) -> str:
    languages = {row.get("language") for row in topics if row.get("language")}
    return next(iter(languages)) if len(languages) == 1 else "en"


def _index_copy(language: str) -> dict[str, str]:
    if language == "es":
        return {
            "skip": "Saltar al contenido", "nav": "Navegación principal",
            "eyebrow": "Guías para días reales", "title_1": "Pequeños sistemas.",
            "title_2": "Días más claros.",
            "description": "Ideas prácticas que puedes probar hoy, con cuadernos completos cuando quieras profundizar.",
            "editorial_title": "Cómo preparamos estas guías",
            "editorial_text": "Libra publica únicamente artículos revisados individualmente y comprueba que el libro relacionado siga disponible. El contenido es educativo y no sustituye asesoramiento profesional.",
            "meta_title": "Guías prácticas de Libra",
            "meta_description": "Notas prácticas para organizar días reales y encontrar el cuaderno completo cuando lo necesites.",
            "topic_label": "tema",
        }
    return {
        "skip": "Skip to content", "nav": "Primary navigation",
        "eyebrow": "Guides for real days", "title_1": "Small systems.",
        "title_2": "Clearer days.",
        "description": "Practical ideas to try today, with complete workbooks when you want to go deeper.",
        "editorial_title": "How these guides are prepared",
        "editorial_text": "Libra publishes individually reviewed articles and checks that each related book remains available. The material is educational and does not replace professional advice.",
        "meta_title": "Libra practical guides",
        "meta_description": "Practical notes for real days, with complete workbooks when you want to go deeper.",
        "topic_label": "topic",
    }


def _topic_cards(topics: list[dict]) -> str:
    cards = []
    for topic in topics:
        href = escape_text(_public_path(f"/guides/{topic['topic']}"))
        language = escape_text(topic.get("language", "en"))
        title = escape_text(topic.get("title", ""))
        description = escape_text(topic.get("description", ""))
        count = len(topic.get("articles", []))
        action = "Explorar" if language == "es" else "Explore"
        note_word = "notas" if language == "es" else "notes"
        slug = topic["slug"]
        cover = escape_text(f"{GROWTH_SITE_BASE}{_public_path(f'/api/books/{slug}/cover')}")
        cards.append(
            f'<article class="topic-card" lang="{language}">'
            f'<div class="topic-card__copy"><p class="language-pill">{language}</p>'
            f'<p class="topic-card__count">{count} {note_word}</p>'
            f'<h2>{title}</h2><p>{description}</p>'
            f'<a class="topic-card__action" href="{href}" '
            f'aria-label="{action} {count} {note_word}: {title}">'
            f'{action} {count} {note_word} <span aria-hidden="true">→</span></a></div>'
            f'<div class="topic-card__visual" aria-hidden="true">'
            f'<span class="focus-lens"></span><img src="{cover}" alt=""></div>'
            '</article>'
        )
    return "\n".join(cards)


def _article_cards(topic: dict) -> str:
    return "\n".join(
        '<li><a class="note-card" lang="{}" href="{}?c={}"><span class="note-card__title">{}</span>'
        '<span class="note-card__summary">{}</span><span class="note-card__arrow" '
        'aria-hidden="true">→</span></a></li>'.format(
            escape_text(article.get("language", topic.get("language", "en"))),
            escape_text(_public_path(f"/growth/articles/{article['id']}")),
            SEARCH_CAMPAIGN,
            escape_text(article.get("title", article["id"])),
            escape_text(article.get("description", "")),
        )
        for article in topic["articles"]
    )
```

Replace inline article rendering with `_article_cards(topic)`. Inject `THEME_CSS`, `PAGE_LANG`, `TOPIC_COUNT`, `ARTICLE_COUNT`, and escaped `_index_copy()` values in the relevant route contexts. Keep CTA generation and event recording unchanged.

- [ ] **Step 4: Run focused route tests**

Run: `python3 -m pytest tests/test_search_guides.py -q`

Expected: all existing route tests plus the three new helper tests pass; no template has changed and no attribution assertion changes.

- [ ] **Step 5: Commit only Task 1 files**

```bash
git add app.py tests/test_search_guides.py
git commit -m "refactor: prepare accessible guide view models"
```

---

### Task 2: Shared Calm Liquid Reader theme and index page

**Files:**
- Create: `templates/public_guides_theme.css`
- Modify: `templates/guides_index.html`
- Create: `tests/test_public_guides_accessibility.py`
- Test: `tests/test_search_guides.py`

**Interfaces:**
- Consumes: `THEME_CSS`, `PAGE_LANG`, `TOPICS`, `TOPIC_COUNT`, and `CANONICAL`
- Produces: shared CSS classes used unchanged by topic/article templates

- [ ] **Step 1: Write failing structural and CSS contract tests**

Create tests that read both rendered HTML and the CSS file:

```python
from pathlib import Path
from bs4 import BeautifulSoup

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


def test_theme_has_accessibility_preferences_and_target_floor():
    css = THEME.read_text(encoding="utf-8")
    assert "min-height: 44px" in css
    assert "min-width: 44px" in css
    assert "prefers-reduced-motion: reduce" in css
    assert "prefers-contrast: more" in css
    assert "prefers-reduced-transparency: reduce" in css
    assert ":focus-visible" in css
```

- [ ] **Step 2: Run the new tests and confirm failure**

Run: `python3 -m pytest tests/test_public_guides_accessibility.py -q`

Expected: failure because the shared CSS and semantic shell do not exist.

- [ ] **Step 3: Build the shared token/fallback system**

Create CSS with opaque defaults first, then progressive glass enhancement:

```css
:root {
  color-scheme: light;
  --midnight: #101828;
  --slate: #344054;
  --cloud: #f7f8fc;
  --white: #ffffff;
  --cobalt: #155eef;
  --indigo: #3538cd;
  --aqua: #0e7067;
  --lavender: #e4e7ff;
  --ice: #dff7f4;
  --border: rgba(16, 24, 40, .16);
  --focus: #101828;
  --r-xl: 32px;
  --r-lg: 24px;
  --r-md: 16px;
}
* { box-sizing: border-box; }
html { background: var(--cloud); scroll-behavior: smooth; }
body { margin: 0; color: var(--midnight); background: var(--cloud); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.65; }
a, button { min-width: 44px; min-height: 44px; }
:focus-visible { outline: 3px solid var(--focus); outline-offset: 4px; }
.glass-control { background: rgba(255,255,255,.94); border: 1px solid var(--border); }
@supports (backdrop-filter: blur(20px)) {
  .glass-control { background: rgba(255,255,255,.72); backdrop-filter: saturate(140%) blur(20px); }
}
@media (prefers-reduced-motion: reduce) { *, *::before, *::after { scroll-behavior: auto !important; transition-duration: .01ms !important; animation-duration: .01ms !important; } }
@media (prefers-contrast: more), (prefers-reduced-transparency: reduce) { .glass-control { background: #fff; border-color: #101828; backdrop-filter: none; } }
```

Complete the file with the approved 32/24/16/999px radius family, Focus Lens, responsive hero/card, 68ch reading measure, solid note surfaces, underlined body links, and mobile rules at 760px and 420px. Do not add animation loops or glass to `.reading-surface`, `.topic-card`, or `.note-card`.

- [ ] **Step 4: Replace the index template**

Use a semantic Spanish-first shell:

```html
<!doctype html>
<html lang="{{PAGE_LANG}}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{META_TITLE}}</title>
  <meta name="description" content="{{META_DESCRIPTION}}">
  <link rel="canonical" href="{{CANONICAL}}">
  <style>{{THEME_CSS}}</style>
</head>
<body class="guides-index">
  <a class="skip-link" href="#main-content">{{SKIP_LABEL}}</a>
  <header class="site-bar glass-control">
    <nav aria-label="{{NAV_LABEL}}"><a class="brand" href="/libra/guides/">Libra <span>Guides</span></a><span class="site-meta">{{TOPIC_COUNT}} {{TOPIC_LABEL}}</span></nav>
  </header>
  <main id="main-content">
    <section class="index-hero" aria-labelledby="page-title">
      <div class="hero-copy"><p class="eyebrow">{{HERO_EYEBROW}}</p><h1 id="page-title">{{HERO_TITLE_1}}<br>{{HERO_TITLE_2}}</h1><p>{{HERO_DESCRIPTION}}</p></div>
      <div class="topic-stage">{{TOPICS}}</div>
    </section>
    <aside class="editorial-note" aria-labelledby="editorial-title"><h2 id="editorial-title">{{EDITORIAL_TITLE}}</h2><p>{{EDITORIAL_TEXT}}</p></aside>
  </main>
</body>
</html>
```

- [ ] **Step 5: Run index and accessibility contract tests**

Run:

```bash
python3 -m pytest \
  tests/test_search_guides.py \
  tests/test_public_guides_accessibility.py -q
```

Expected: all current index assertions and theme-contract assertions pass.

- [ ] **Step 6: Commit the index/theme unit**

```bash
git add templates/public_guides_theme.css templates/guides_index.html \
  tests/test_public_guides_accessibility.py tests/test_search_guides.py
git commit -m "feat: redesign Libra guides index"
```

---

### Task 3: Accessible topic library and book dock

**Files:**
- Modify: `templates/guide_topic.html`
- Modify: `app.py:1493-1530`
- Modify: `tests/test_search_guides.py`
- Modify: `tests/test_public_guides_accessibility.py`

**Interfaces:**
- Consumes: `THEME_CSS`, `LANG`, `TITLE`, `DESCRIPTION`, `ARTICLE_COUNT`, `ARTICLES`, cover/book/CTA fields
- Preserves: exactly one signed Amazon CTA and tagged article URLs

- [ ] **Step 1: Add failing localization, alt, and card tests**

```python
def test_spanish_topic_uses_localized_controls_and_meaningful_cover_alt(client):
    soup = BeautifulSoup(client.get("/guides/rutinas-tdah-adultos").text, "html.parser")
    assert soup.html["lang"] == "es"
    assert soup.select_one(".site-bar").get_text(" ", strip=True).startswith("Todas las guías")
    assert soup.find("h2", string=lambda value: value and "Notas prácticas" in value)
    assert soup.select_one(".book-dock img")["alt"] == "Portada de Cuaderno práctico"
    assert soup.select_one(".book-dock .cta").get_text(" ", strip=True) == "Ver el libro en Amazon"


def test_topic_note_cards_are_single_links_with_descriptions(client):
    soup = BeautifulSoup(client.get("/guides/rutinas-tdah-adultos").text, "html.parser")
    cards = soup.select(".note-card")
    assert len(cards) == 2
    assert all(card.name == "a" and card.select_one(".note-card__summary") for card in cards)
```

- [ ] **Step 2: Run tests and verify the English/empty-alt failures**

Run: `python3 -m pytest tests/test_search_guides.py -k 'spanish_topic or topic_note_cards' -q`

Expected: failures on English controls, empty cover alt, and old list markup.

- [ ] **Step 3: Implement localized route context**

For Spanish topics, supply neutral-Spanish labels; keep an English fallback dictionary for future mixed inventory:

```python
copy = {
    "es": {
        "all_guides": "Todas las guías",
        "notes": "Notas prácticas",
        "book": "Cuaderno relacionado",
        "amazon": "Ver el libro en Amazon",
        "method_title": "Cómo se prepara esta página",
    },
}.get(topic.get("language"), {
    "all_guides": "All guides", "notes": "Practical notes",
    "book": "Related workbook", "amazon": "View the book on Amazon",
    "method_title": "How this page is prepared",
})
```

Pass these escaped values into named placeholders. Pass `BOOK_COVER_ALT` as `Portada de {title}` for Spanish and `Cover of {title}` otherwise. Use `_article_cards(topic)` and keep `_hub_cta_path` untouched.

- [ ] **Step 4: Replace topic HTML with the approved layout**

The template must contain:

```html
<a class="skip-link" href="#main-content">{{SKIP_LABEL}}</a>
<header class="site-bar glass-control"><nav aria-label="{{NAV_LABEL}}"><a href="/libra/guides/">← {{ALL_GUIDES_LABEL}}</a><span>{{ARTICLE_COUNT}} notas</span></nav></header>
<main id="main-content" class="topic-shell">
  <header class="topic-hero"><p class="language-pill">{{LANG}}</p><h1>{{TITLE}}</h1><p>{{DESCRIPTION}}</p></header>
  <div class="topic-layout">
    <section aria-labelledby="notes-title"><h2 id="notes-title">{{NOTES_LABEL}}</h2><ul class="note-grid">{{ARTICLES}}</ul></section>
    <aside class="book-dock glass-control" aria-labelledby="book-title"><img src="{{BOOK_COVER}}" alt="{{BOOK_COVER_ALT}}" width="128"><div><p class="dock-label">{{BOOK_LABEL}}</p><h2 id="book-title">{{BOOK_TITLE}}</h2><p>{{BOOK_FORMAT}}</p><a class="cta" href="{{CTA_URL}}" rel="nofollow sponsored">{{CTA_LABEL}}</a></div></aside>
  </div>
  <aside class="editorial-note" aria-labelledby="method-title"><h2 id="method-title">{{METHOD_TITLE}}</h2><p>{{METHOD_TEXT}}</p></aside>
</main>
```

On desktop the book dock is sticky but remains in normal DOM order; at ≤760px it becomes static and never overlays the article list.

- [ ] **Step 5: Run topic, attribution, and held-route tests**

Run:

```bash
python3 -m pytest tests/test_search_guides.py \
  -k 'topic or held or tracked_amazon or reader_view' -q
```

Expected: pass; one tracked CTA, held topic 404, and synthetic filtering unchanged.

- [ ] **Step 6: Commit the topic unit**

```bash
git add app.py templates/guide_topic.html tests/test_search_guides.py \
  tests/test_public_guides_accessibility.py
git commit -m "feat: redesign Libra topic guides"
```

---

### Task 4: Liquid reading surface and attribution-safe related cards

**Files:**
- Modify: `templates/hub_article.html`
- Modify: `app.py:1574-1675`
- Modify: `tests/test_search_guides.py`
- Modify: `tests/test_public_guides_accessibility.py`

**Interfaces:**
- Consumes: shared theme, existing JSON-LD, signed CTA, disclosure/editorial copy, and related article data
- Produces: Spanish article chrome for eligible ES search topics; legacy held/non-search article behavior remains public but without guide navigation

- [ ] **Step 1: Add failing article-shell tests**

```python
def test_article_has_skip_link_reading_landmark_and_localized_related_heading(client):
    soup = BeautifulSoup(client.get("/growth/articles/a1?c=search-surface").text, "html.parser")
    assert soup.select_one('a.skip-link[href="#article-content"]')
    assert soup.select_one("main#article-content.reading-surface")
    assert soup.find("h2", string="Sigue leyendo")
    assert soup.select_one(".book-dock img")["alt"] == "Portada de Cuaderno práctico"


def test_article_related_cards_keep_one_tagged_link_each(client):
    soup = BeautifulSoup(client.get("/growth/articles/a1?c=search-surface").text, "html.parser")
    cards = soup.select(".related-card")
    assert len(cards) == 1
    assert len(cards[0].select("a[href]")) == 1
    assert cards[0].a["href"].endswith("/growth/articles/a2?c=search-surface")
```

- [ ] **Step 2: Run tests and confirm missing shell/localization**

Run: `python3 -m pytest tests/test_search_guides.py -k 'article_has_skip or article_related_cards' -q`

Expected: failures because the old article shell has no skip link/reading landmark and says “Read next”.

- [ ] **Step 3: Render localized navigation and related cards**

Replace the current list-string builder with one anchor per related card:

```python
def _related_cards(rows: list[dict], campaign: str) -> str:
    suffix = f"?c={SEARCH_CAMPAIGN}" if campaign == SEARCH_CAMPAIGN else ""
    return "".join(
        '<li class="related-card"><a href="{}{}"><span>{}</span>'
        '<span aria-hidden="true">→</span></a></li>'.format(
            escape_text(_public_path(f"/growth/articles/{row['id']}")),
            suffix,
            escape_text(row.get("title", row["id"])),
        )
        for row in rows
    )
```

For eligible Spanish articles, use “Todas las guías”, “Nota práctica”, “Aviso educativo”, “Cuaderno relacionado”, and “Sigue leyendo”. Preserve the existing neutral medical disclaimer text exactly. Keep non-search articles free of `/libra/guides/` navigation.

- [ ] **Step 4: Replace article template with semantic reader structure**

Use this hierarchy:

```html
<body class="article-page">
  <a class="skip-link" href="#article-content">Saltar al artículo</a>
  {{GUIDE_NAV}}
  <main id="article-content" class="reading-surface">
    <header class="article-header"><p class="eyebrow">{{ARTICLE_LABEL}}</p><h1>{{TITLE}}</h1><p class="article-summary">{{SUMMARY}}</p></header>
    <div class="article-body">{{BODY}}</div>
    <aside class="disclosure" aria-label="{{DISCLOSURE_LABEL}}"><strong>{{DISCLOSURE_LABEL}}</strong><p>{{DISCLOSURE}}</p></aside>
    <p class="editorial">{{EDITORIAL_NOTE}}</p>
    <aside class="book-dock glass-control" aria-labelledby="article-book-title">…existing escaped book fields and signed CTA…</aside>
    {{RELATED}}
  </main>
</body>
```

Keep the JSON-LD block byte-safe with `<` encoded as `\u003c`. Ensure `.article-body a` is underlined and `.reading-surface` is opaque.

- [ ] **Step 5: Run article security and attribution regressions**

Run:

```bash
python3 -m pytest tests/test_search_guides.py \
  -k 'article or json_ld or tagged_read_next or direct_article' -q
```

Expected: pass; tagged chains remain `search-surface`, direct article requests remain `unattributed`, and mixed-case script end tags remain neutralized.

- [ ] **Step 6: Commit the article unit**

```bash
git add app.py templates/hub_article.html tests/test_search_guides.py \
  tests/test_public_guides_accessibility.py
git commit -m "feat: redesign Libra article reader"
```

---

### Task 5: Reproducible browser accessibility audit

**Files:**
- Create: `scripts/audit_public_guides.py`
- Create: `tests/test_audit_public_guides.py`
- Modify: `tests/test_public_guides_accessibility.py`

**Interfaces:**
- CLI: `python3 scripts/audit_public_guides.py --base-url URL --axe-script PATH --output PATH`
- Produces: JSON containing per-page status, axe violations, overflow, touch-target failures, and console errors
- Exit 0 only when every page is HTTP 200, has no Critical/Serious axe violation, no horizontal overflow, no interactive target below 44×44, and no console error

- [ ] **Step 1: Write failing severity-policy tests**

```python
from scripts.audit_public_guides import audit_failed


def test_audit_failed_for_serious_violation_or_small_target():
    assert audit_failed({"violations": [{"impact": "serious"}], "small_targets": [], "overflow": False, "console_errors": []})
    assert audit_failed({"violations": [], "small_targets": [{"text": "CTA", "width": 40, "height": 44}], "overflow": False, "console_errors": []})


def test_audit_accepts_clean_page():
    assert not audit_failed({"violations": [], "small_targets": [], "overflow": False, "console_errors": []})
```

- [ ] **Step 2: Run tests and confirm missing module**

Run: `python3 -m pytest tests/test_audit_public_guides.py -q`

Expected: collection error because `scripts.audit_public_guides` does not exist.

- [ ] **Step 3: Implement the read-only Playwright runner**

The script must:

```python
PAGES = (
    "/guides/",
    "/guides/rutinas-tdah-adultos",
    "/growth/articles/adhd-lista-dos-columnas-es?c=search-surface",
)


def audit_failed(row: dict) -> bool:
    severe = any(item.get("impact") in {"critical", "serious"} for item in row["violations"])
    return severe or bool(row["small_targets"] or row["overflow"] or row["console_errors"])
```

For every page, create a fresh Playwright context at 390×844 with `X-Libra-Measurement: synthetic`, navigate with `networkidle`, inject the caller-supplied `axe.min.js`, execute `axe.run(document, {runOnly: {type: 'tag', values: ['wcag2a','wcag2aa','wcag21a','wcag21aa']}})`, inspect visible `a[href], button, input, select, textarea` bounding boxes, and write the complete JSON atomically to `--output`. Do not click the Amazon CTA or write the business ledger.

- [ ] **Step 4: Run unit and HTML-contract tests**

Run:

```bash
python3 -m pytest tests/test_audit_public_guides.py \
  tests/test_public_guides_accessibility.py -q
```

Expected: pass.

- [ ] **Step 5: Commit the audit unit**

```bash
git add scripts/audit_public_guides.py tests/test_audit_public_guides.py \
  tests/test_public_guides_accessibility.py
git commit -m "test: audit Libra public guide accessibility"
```

---

### Task 6: Full verification, production release, and evidence

**Files:**
- Snapshot only: files changed by Tasks 1–5
- Modify after verified release: `/root/memory.md`
- Modify after verified release: `/root/telos.md`

**Interfaces:**
- Production service: `libra.service` on `127.0.0.1:8200`, public mount `/libra`
- Rollback boundary: redesign commits only; preserve Search-first release commit `df32e48` and all runtime state

- [ ] **Step 1: Run the full relevant regression suite**

Run:

```bash
python3 -m pytest \
  tests/test_growth_routes.py \
  tests/test_content_hub.py \
  tests/test_business_ledger.py \
  tests/test_kdp_freeze.py \
  tests/test_kdp_freeze_entrypoints.py \
  tests/test_organic_search.py \
  tests/test_search_guides.py \
  tests/test_search_organic_report.py \
  tests/test_public_guides_accessibility.py \
  tests/test_audit_public_guides.py -q
```

Expected: zero failures. Also run `git diff --check` and require exit 0.

- [ ] **Step 2: Snapshot exact production inputs**

Create `/root/backups/libra-calm-liquid-reader-20261030/` and copy `app.py`, the four template/theme files, relevant tests, and `libra.service`. Record `git status --short`, `git rev-parse HEAD`, and the pre-release public screenshots. Do not snapshot secrets or the business ledger.

- [ ] **Step 3: Run a local axe audit before restart**

Start the candidate app on an isolated local port, then use a temporary dependency directory:

```bash
audit_tmp=$(mktemp -d)
npm install --prefix "$audit_tmp" axe-core@4.10.2
uvicorn app:app --host 127.0.0.1 --port 8211 >/tmp/libra-calm-reader-uvicorn.log 2>&1 &
audit_pid=$!
python3 scripts/audit_public_guides.py \
  --base-url http://127.0.0.1:8211 \
  --axe-script "$audit_tmp/node_modules/axe-core/axe.min.js" \
  --output /root/backups/libra-calm-liquid-reader-20261030/audit-local.json
kill "$audit_pid"
```

Expected: exit 0 and three clean page rows. Remove only the explicit temporary directory after the audit.

- [ ] **Step 4: Restart and verify live policy boundaries**

Run:

```bash
systemctl restart libra
systemctl is-active libra
curl -sS -o /dev/null -w '%{http_code}\n' -H 'X-Libra-Measurement: synthetic' \
  https://newton-winai-klinprasom.incomeinclick.in.th/libra/guides/
curl -sS -o /dev/null -w '%{http_code}\n' -H 'X-Libra-Measurement: synthetic' \
  https://newton-winai-klinprasom.incomeinclick.in.th/libra/guides/smartphone-seniors-fr
curl -sS -o /dev/null -w '%{http_code}\n' -H 'X-Libra-Measurement: synthetic' \
  https://newton-winai-klinprasom.incomeinclick.in.th/libra/growth/feed.xml
```

Expected: service `active`; public guides 200; held senior topic 404; Pinterest feed 404.

- [ ] **Step 5: Run live visual/accessibility evidence**

Run the audit command against the public base URL and save `audit-live.json`. Capture full-page screenshots for index, topic, and article at 390×844 and 1440×1000, plus index at 320px and 200% zoom. Every context must carry the synthetic header. Verify computed `scrollWidth <= innerWidth`, no console errors, focus order, and visible focus rings. Inspect all screenshots with the local image viewer before declaring success.

- [ ] **Step 6: Confirm measurement was not polluted**

Run:

```bash
python3 scripts/search_organic_report.py --json --as-of 2026-10-30 \
  | jq '{traffic,goal,search_console}'
```

Compare traffic counts with the pre-release snapshot; synthetic verification must not increase `search-surface` qualified views.

- [ ] **Step 7: Confirm named commits and push backup**

Confirm Tasks 1–5 committed only their named files, then push:

```bash
git status --short
git log --oneline -6
git push backup main
```

If the push fails, report it explicitly and keep the local commits; never stage unrelated dirty files.

- [ ] **Step 8: Update business memory and release ownership**

Update `/root/memory.md` with the visual/accessibility outcome, test count, live URLs, audit evidence, commit, and remaining limitation that semantic inspection is not a physical VoiceOver-device certification. Update the Libra entry in `/root/telos.md`. Then run:

```bash
ai-work finish Lfyz0G2-6QdVOTpM5BewUUvb6zvIfKOz \
  "Libra Calm Liquid Reader deployed and verified across index, topic, and article surfaces"
```

Expected: ownership released only after tests, live checks, backup push, memory, and telos are complete.

