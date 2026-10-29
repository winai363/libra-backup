import json
import sqlite3
from datetime import datetime, timezone

from business_ledger import init_ledger, record_hub_event
from organic_search import (
    build_scorecard,
    build_view_event,
    eligible_topics,
    load_search_portfolio,
)


def write_listing(kdp_dir, slug, *, live_status="LIVE", asin="B0TESTASIN"):
    book_dir = kdp_dir / slug
    book_dir.mkdir(parents=True, exist_ok=True)
    (book_dir / "listing.json").write_text(json.dumps({
        "title": f"Title {slug}",
        "description": f"Description {slug}",
        "live_status": live_status,
        "asin": asin,
    }))


def write_article(articles_dir, article_id, **overrides):
    articles_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "qa_approved": True,
        "title": f"Article {article_id}",
        "description": "Useful summary",
        "target_slug": "approved",
        "language": "es",
    }
    payload.update(overrides)
    (articles_dir / f"{article_id}.json").write_text(json.dumps(payload))


def test_load_search_portfolio_requires_supported_schema(tmp_path):
    path = tmp_path / "portfolio.json"
    path.write_text(json.dumps({"schema": "libra-search-portfolio/1", "books": []}))
    assert load_search_portfolio(path)["books"] == []


def test_eligible_topics_fail_closed_on_hold_or_non_live(tmp_path):
    config = {"books": [
        {"slug": "approved", "promotion_status": "APPROVED", "topic": "focus", "language": "es"},
        {"slug": "held", "promotion_status": "HOLD_PROMOTION", "topic": "phone", "language": "fr"},
        {"slug": "not-live", "promotion_status": "APPROVED", "topic": "paint", "language": "es"},
    ]}
    write_listing(tmp_path / "kdp", "approved")
    write_listing(tmp_path / "kdp", "held")
    write_listing(tmp_path / "kdp", "not-live", live_status="BLOCKED")
    write_article(tmp_path / "articles", "a1", target_slug="approved")
    write_article(tmp_path / "articles", "held-a1", target_slug="held")
    write_article(tmp_path / "articles", "blocked-a1", target_slug="not-live")

    rows = eligible_topics(config, tmp_path / "kdp", tmp_path / "articles")

    assert [row["slug"] for row in rows] == ["approved"]
    assert [article["id"] for article in rows[0]["articles"]] == ["a1"]


def test_eligible_topics_excludes_unapproved_article(tmp_path):
    config = {"books": [
        {"slug": "approved", "promotion_status": "APPROVED", "topic": "focus", "language": "es"},
    ]}
    write_listing(tmp_path / "kdp", "approved")
    write_article(tmp_path / "articles", "draft", qa_approved=False)

    assert eligible_topics(config, tmp_path / "kdp", tmp_path / "articles") == []


def test_build_view_event_keeps_only_privacy_safe_fields():
    event = build_view_event(
        "book-a", "seo-search", "article:a1",
        now=datetime(2026, 10, 30, tzinfo=timezone.utc),
    )

    assert event["event_kind"] == "qualified_view"
    assert event["payload"] == {"page_id": "article:a1"}
    assert set(event) == {"event_key", "occurred_at", "slug", "campaign", "event_kind", "payload"}


def seed_snapshot(ledger, observed_at, month, royalties):
    init_ledger(ledger)
    with sqlite3.connect(ledger) as connection:
        connection.execute(
            "INSERT INTO kdp_snapshots "
            "(observed_at, month, royalties_usd, orders_all_types, kenp, raw_json, source_key, content_hash) "
            "VALUES (?, ?, ?, 0, 0, '{}', ?, ?)",
            (observed_at, month, royalties, f"source:{observed_at}", observed_at),
        )


def test_scorecard_uses_latest_snapshot_and_keeps_search_console_unknown(tmp_path):
    ledger = tmp_path / "ledger.db"
    seed_snapshot(ledger, "2026-11-20T09:00:00+07:00", "2026-11", 19.0)
    seed_snapshot(ledger, "2026-11-30T09:00:00+07:00", "2026-11", 21.0)
    record_hub_event(ledger, build_view_event("book-a", "seo-search", "article:a1"))

    result = build_scorecard(ledger, {"release": {"state": "PRE_RELEASE"}})

    assert result["kdp_months"]["2026-11"]["royalties_usd"] == 21.0
    assert result["traffic"] == {"qualified_views": 1, "amazon_outbound": 0}
    assert result["search_console"] == {"status": "UNKNOWN", "reason": "not_connected"}
    assert result["purchase_attribution"] == "UNKNOWN"
