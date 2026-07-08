import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import distribution_report as dr


def write_json(path: Path, data: dict | list):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_build_report_separates_real_royalties_from_free_downloads(tmp_path, monkeypatch):
    kdp = tmp_path / "kdp"
    strategy = tmp_path / "strategy.json"
    lovely = tmp_path / "lovelybooks"
    reddit = tmp_path / "reddit.json"

    write_json(kdp / "sales-sync-state.json", {
        "updated_at": "2026-07-08T09:15:10",
        "titles": {
            "B0AAA": {"orders": 17, "kenp": 0, "royalties": 1.98},
            "B0BBB": {"orders": 13, "kenp": 25, "royalties": 0.08},
        },
    })
    write_json(kdp / "hero-one" / "listing.json", {
        "title": "Hero One",
        "asin": "B0AAA",
        "status": "uploaded",
        "live_status": "LIVE",
        "kdp_select": {"status": "Enrolled"},
        "free_promo": {"status": "Scheduled", "start": "2026-07-15", "end": "2026-07-19"},
    })
    write_json(kdp / "hero-one" / "feedback-history.json", [
        {"date": "2026-07-15", "delta_units": 8, "delta_kenp": 3, "revenue_usd": 0.0},
        {"date": "2026-07-16", "delta_units": 9, "delta_kenp": 0, "revenue_usd": 1.98},
    ])
    write_json(kdp / "hero-two" / "listing.json", {
        "title": "Hero Two",
        "asin": "B0BBB",
        "status": "uploaded",
        "live_status": "LIVE",
        "free_promo": {"status": "Scheduled", "start": "2026-07-25", "end": "2026-07-26"},
    })
    write_json(kdp / "hero-two" / "feedback-history.json", [
        {"date": "2026-07-08", "delta_units": 13, "delta_kenp": 25, "revenue_usd": 0.08},
    ])
    write_json(strategy, {
        "checkpoint": "2026-07-31",
        "strategy_name": "Depth Loop",
        "hero_slugs": ["hero-one", "hero-two"],
        "promo_days_left": {},
        "events": [],
        "actions_bui": [],
    })
    write_json(reddit, {"posts": [{"date": "2026-07-16", "slug": "hero-one", "title": "Reddit title", "body": "Body"}]})
    (lovely / "book_indexed.flag").parent.mkdir(parents=True, exist_ok=True)
    (lovely / "book_indexed.flag").write_text("author page live", encoding="utf-8")
    (lovely / "leserunde_tool.flag").write_text("signal: Aktion starten", encoding="utf-8")
    (lovely / "shots").mkdir()
    (lovely / "shots" / "published.png").write_bytes(b"png")

    monkeypatch.setattr(dr, "KDP_DIR", kdp)
    monkeypatch.setattr(dr, "STRATEGY_FILE", strategy)
    monkeypatch.setattr(dr, "LOVELY_DIR", lovely)
    monkeypatch.setattr(dr, "REDDIT_SCHEDULE", reddit)

    report = dr.build_report(today=date(2026, 7, 16))

    assert report["money"]["mtd_royalties_usd"] == 2.06
    assert report["money"]["mtd_orders_all_types"] == 30
    assert report["money"]["warning"] == "orders include free downloads; royalties are the money source of truth"
    assert report["free_promos"]["total_downloads_in_promo_windows"] == 17
    assert report["free_promos"]["active"][0]["slug"] == "hero-one"
    assert report["hero_books"][0]["free_downloads"] == 17
    assert report["lovelybooks"]["status"] == "ready"
    assert any(item["channel"] == "Reddit" for item in report["today_actions"])


def test_telegram_message_contains_next_actions_and_money_warning():
    report = {
        "generated_at": "2026-07-08T22:30:00",
        "checkpoint": {"date": "2026-07-31", "days_left": 23},
        "money": {"mtd_royalties_usd": 2.06, "mtd_orders_all_types": 115},
        "free_promos": {"active": [], "upcoming": [{"slug": "x", "start": "2026-07-15", "end": "2026-07-19"}]},
        "lovelybooks": {"status": "ready"},
        "today_actions": [{"channel": "Pinterest", "action": "pin workbook-es"}],
    }

    msg = dr.telegram_message(report)

    assert "$2.06" in msg
    assert "115 orders/downloads" in msg
    assert "Pinterest" in msg
    assert "LovelyBooks: ready" in msg
