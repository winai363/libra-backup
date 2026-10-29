"""Public search portfolio policy and privacy-safe organic measurements."""
from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from business_ledger import init_ledger


SCHEMA = "libra-search-portfolio/1"
SEARCH_CAMPAIGN = "seo-search"


def load_search_portfolio(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != SCHEMA:
        raise ValueError("unsupported search portfolio schema")
    if not isinstance(payload.get("books"), list):
        raise ValueError("search portfolio books must be a list")
    return payload


def _listing_for(kdp_dir: Path, slug: str) -> dict | None:
    try:
        listing = json.loads((kdp_dir / slug / "listing.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if listing.get("live_status") != "LIVE" or not listing.get("asin"):
        return None

    roster_path = kdp_dir / "bookshelf-roster.json"
    if roster_path.exists():
        try:
            roster = json.loads(roster_path.read_text(encoding="utf-8"))
            live = any(
                entry.get("slug") == slug
                and entry.get("format") == "ebook"
                and entry.get("status") == "LIVE"
                for entry in roster.get("entries", [])
            )
        except (OSError, json.JSONDecodeError, AttributeError):
            return None
        if not live:
            return None
    return listing


def _approved_articles(articles_dir: Path, slug: str) -> list[dict]:
    articles = []
    for path in sorted(articles_dir.glob("*.json")):
        try:
            article = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if article.get("qa_approved") is not True or article.get("target_slug") != slug:
            continue
        row = dict(article)
        row["id"] = path.stem
        articles.append(row)
    return sorted(articles, key=lambda row: (row.get("published_at") or "", row["id"]))


def eligible_topics(config: dict, kdp_dir: Path, articles_dir: Path) -> list[dict]:
    topics = []
    books = config.get("books") if isinstance(config, dict) else None
    if not isinstance(books, list):
        return topics
    for book in books:
        if not isinstance(book, dict) or book.get("promotion_status") != "APPROVED":
            continue
        slug = book.get("slug")
        topic = book.get("topic")
        if not isinstance(slug, str) or not isinstance(topic, str):
            continue
        listing = _listing_for(kdp_dir, slug)
        articles = _approved_articles(articles_dir, slug)
        if listing is None or not articles:
            continue
        row = dict(book)
        row["listing"] = listing
        row["articles"] = articles
        topics.append(row)
    return sorted(topics, key=lambda row: row["topic"])


def topic_for_article(topics: list[dict], article_id: str) -> dict | None:
    for topic in topics:
        if any(article.get("id") == article_id for article in topic.get("articles", [])):
            return topic
    return None


def related_articles(topic: dict | None, article_id: str, *, limit: int = 3) -> list[dict]:
    if topic is None:
        return []
    return [
        article for article in topic.get("articles", [])
        if article.get("id") != article_id
    ][:limit]


def public_search_urls(topics: list[dict], site_base: str, public_mount: str) -> list[str]:
    base = site_base.rstrip("/")
    mount = "/" + public_mount.strip("/")
    urls = [f"{base}{mount}/guides/"]
    for topic in topics:
        urls.append(f"{base}{mount}/guides/{topic['topic']}")
        urls.extend(
            f"{base}{mount}/growth/articles/{article['id']}"
            for article in topic.get("articles", [])
        )
    return urls


def build_view_event(slug: str, campaign: str, page_id: str, *, now=None) -> dict:
    return {
        "event_key": secrets.token_hex(16),
        "occurred_at": (now or datetime.now(timezone.utc)).isoformat(),
        "slug": slug,
        "campaign": campaign,
        "event_kind": "qualified_view",
        "payload": {"page_id": page_id},
    }


def build_scorecard(ledger_path: Path, config: dict, *, now=None) -> dict:
    init_ledger(ledger_path)
    with sqlite3.connect(ledger_path) as connection:
        snapshots = connection.execute(
            "SELECT s.month, s.observed_at, s.royalties_usd, s.orders_all_types, s.kenp "
            "FROM kdp_snapshots s JOIN ("
            " SELECT month, MAX(observed_at) observed_at FROM kdp_snapshots GROUP BY month"
            ") latest ON latest.month=s.month AND latest.observed_at=s.observed_at "
            "ORDER BY s.month"
        ).fetchall()
        counts = dict(connection.execute(
            "SELECT event_kind, COUNT(*) FROM hub_events "
            "WHERE campaign=? GROUP BY event_kind", (SEARCH_CAMPAIGN,)
        ).fetchall())
    months = {
        month: {
            "observed_at": observed_at,
            "royalties_usd": royalties,
            "orders_all_types": orders,
            "kenp": kenp,
        }
        for month, observed_at, royalties, orders, kenp in snapshots
    }
    return {
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(),
        "release": config.get("release", {}),
        "kdp_months": months,
        "traffic": {
            "qualified_views": counts.get("qualified_view", 0),
            "amazon_outbound": counts.get("amazon_outbound", 0),
        },
        "search_console": {"status": "UNKNOWN", "reason": "not_connected"},
        "purchase_attribution": "UNKNOWN",
    }
