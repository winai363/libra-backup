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


def test_build_report_includes_manual_progress_from_evidence(tmp_path, monkeypatch):
    kdp = tmp_path / "kdp"
    strategy = tmp_path / "strategy.json"
    lovely = tmp_path / "lovelybooks"
    reddit = tmp_path / "reddit.json"
    manual = tmp_path / "manual-task-state.json"

    write_json(kdp / "sales-sync-state.json", {"updated_at": "2026-07-09T09:15:10", "titles": {}})
    write_json(kdp / "hero-one" / "listing.json", {
        "title": "Cuaderno",
        "asin": "B0AAA",
        "live_status": "LIVE",
        "kdp_select": {"status": "Enrolled"},
        "free_promo": {"status": "Scheduled", "start": "2026-07-15", "end": "2026-07-19"},
    })
    write_json(kdp / "hero-two" / "listing.json", {
        "title": "Enfoque",
        "asin": "B0BBB",
        "live_status": "LIVE",
        "kdp_select": {"status": "Enrolled"},
        "free_promo": {"status": "Scheduled", "start": "2026-07-22", "end": "2026-07-26"},
    })
    write_json(kdp / "hero-three" / "listing.json", {
        "title": "ADHS German",
        "asin": "B0CCC",
        "live_status": "LIVE",
        "kdp_select": {"status": "Enrolled"},
        "free_promo": {"status": "Scheduled", "start": "2026-07-25", "end": "2026-07-26"},
    })
    write_json(kdp / "hero-four" / "listing.json", {
        "title": "Easy Taxes",
        "asin": "B0DDD",
        "live_status": "LIVE",
        "kdp_select": {"status": "Enrolled"},
    })
    write_json(strategy, {
        "checkpoint": "2026-07-31",
        "strategy_name": "Depth Loop",
        "hero_slugs": ["hero-one", "hero-two", "hero-three", "hero-four"],
        "promo_days_left": {},
        "events": [],
        "actions_bui": [],
    })
    write_json(reddit, {"posts": []})
    write_json(manual, {
        "pinterest": {
            "target_slugs": ["hero-one", "hero-two", "hero-three"],
            "completed_slugs": ["hero-one", "hero-two"]
        }
    })

    monkeypatch.setattr(dr, "KDP_DIR", kdp)
    monkeypatch.setattr(dr, "STRATEGY_FILE", strategy)
    monkeypatch.setattr(dr, "LOVELY_DIR", lovely)
    monkeypatch.setattr(dr, "REDDIT_SCHEDULE", reddit)
    monkeypatch.setattr(dr, "MANUAL_STATE_FILE", manual)

    report = dr.build_report(today=date(2026, 7, 9))

    assert report["manual_progress"]["pinterest"]["completed_slugs"] == ["hero-one", "hero-two"]
    assert report["manual_progress"]["pinterest"]["completed_count"] == 2
    assert report["manual_progress"]["pinterest"]["target_slugs"] == ["hero-one", "hero-two", "hero-three"]
    assert report["manual_progress"]["pinterest"]["remaining_slugs"] == ["hero-three"]


def test_build_monitor_summarizes_on_track_distribution_plan():
    report = {
        "generated_at": "2026-07-10T09:50:01",
        "checkpoint": {"date": "2026-07-31", "days_left": 21},
        "money": {
            "last_sync": "2026-07-10T09:15:09",
            "mtd_orders_all_types": 60,
            "mtd_kenp": 159,
            "mtd_royalties_usd": 6.77,
        },
        "free_promos": {
            "total_downloads_in_promo_windows": 2,
            "active": [],
            "upcoming": [
                {"slug": "workbook-es", "start": "2026-07-15", "end": "2026-07-19"},
                {"slug": "focus-es", "start": "2026-07-22", "end": "2026-07-26"},
            ],
        },
        "lovelybooks": {"status": "ready"},
        "manual_progress": {
            "pinterest": {
                "completed_count": 2,
                "remaining_count": 2,
                "remaining_slugs": ["german", "taxes"],
            }
        },
        "hero_books": [
            {"slug": "lead", "live_status": "LIVE", "select": True, "aplus": "submitted", "free_promo": {"status": "Done"}},
            {"slug": "workbook-es", "live_status": "LIVE", "select": True, "aplus": "submitted", "free_promo": {"status": "Scheduled"}},
            {"slug": "focus-es", "live_status": "LIVE", "select": True, "aplus": "submitted", "free_promo": {"status": "Scheduled"}},
            {"slug": "german", "live_status": "LIVE", "select": True, "aplus": "submitted", "free_promo": {"status": "Scheduled"}},
            {"slug": "taxes", "live_status": "LIVE", "select": True, "aplus": "submitted", "free_promo": None},
        ],
        "today_actions": [{"channel": "Monitor", "action": "ดูรายงานวันนี้"}],
    }
    overview = {
        "counts": {"queued_for_kdp": 0, "quality_failed": 17, "tracked_asins": 42},
        "queue_blocker": None,
        "sales": {"last_sync": "2026-07-10T09:15:09"},
    }
    category_health = {"signature": {"status": "ok", "blocker_count": 0, "warning_count": 26}}

    monitor = dr.build_monitor(report, overview=overview, category_health=category_health)

    assert monitor["overall"]["status"] == "on_track"
    assert monitor["overall"]["score"] >= 75
    assert monitor["timeline"]["label"] == "On track"
    assert monitor["setup"]["hero_live"] == "5/5"
    assert monitor["setup"]["hero_select"] == "5/5"
    assert monitor["setup"]["hero_aplus"] == "5/5"
    assert monitor["manual"]["pinterest"]["label"] == "2/4 done"
    assert monitor["blockers"]["count"] == 0
    assert monitor["decision"]["recommendation"] == "ห้าม paid promo ตลอดโหมด organic 90 วัน"
    assert monitor["actual_vs_plan"]["metrics"][0]["name"] == "Revenue"
    assert monitor["actual_vs_plan"]["metrics"][0]["actual"] == 6.77
    assert monitor["actual_vs_plan"]["metrics"][0]["plan"] == 25.0
    assert monitor["actual_vs_plan"]["metrics"][0]["percent"] == 27
    assert monitor["actual_vs_plan"]["roles"]["CFO"]["status"] == "early"
    assert monitor["actual_vs_plan"]["roles"]["COO"]["status"] == "on_plan"
    assert monitor["actual_vs_plan"]["roles"]["CMO"]["status"] == "behind"
    assert monitor["kdp_agent"]["mode"] == "auto_advisor"
    assert "ห้ามซื้อ paid promo" in monitor["kdp_agent"]["next_actions"][0]
    assert monitor["kdp_agent"]["action_queue"][0]["owner"] == "CMO"
    assert monitor["kdp_agent"]["action_queue"][0]["status"] == "due_now"
    assert monitor["kdp_agent"]["decision_gates"][0]["name"] == "Paid promo gate"
    assert monitor["kdp_agent"]["decision_gates"][0]["status"] == "closed"
    assert monitor["kdp_agent"]["free_growth_engine"]["mode"] == "auto_free_actions"
    assert monitor["kdp_agent"]["free_growth_engine"]["decisions"][0]["action"] == "free_post"
    assert monitor["kdp_agent"]["free_growth_engine"]["decisions"][0]["execute"] is True


def test_build_monitor_separates_operational_and_commercial_verdicts():
    report = {
        "generated_at": "2026-07-11T09:50:01",
        "checkpoint": {"date": "2026-07-31", "days_left": 20},
        "money": {
            "last_sync": "2026-07-11T09:15:09",
            "mtd_orders_all_types": 60,
            "mtd_kenp": 159,
            "mtd_royalties_usd": 7.63,
        },
        "free_promos": {"total_downloads_in_promo_windows": 100, "active": [], "upcoming": []},
        "manual_progress": {"pinterest": {"completed_count": 4, "remaining_count": 0}},
        "hero_books": [{
            "slug": "hero", "live_status": "LIVE", "select": True,
            "aplus": "submitted", "free_promo": {"status": "Done"},
        }],
        "today_actions": [],
    }
    overview = {"counts": {"queued_for_kdp": 0}, "queue_blocker": None}
    health = {"signature": {"status": "ok", "blocker_count": 0}}

    monitor = dr.build_monitor(
        report,
        overview=overview,
        category_health=health,
        financials={"verified_royalties_usd": 7.63, "contribution_profit_usd": -1.81},
    )

    assert monitor["operations"] == {"score": 100, "status": "ready"}
    assert monitor["commercial"] == {
        "status": "behind",
        "verified_royalties_usd": 7.63,
        "contribution_profit_usd": -1.81,
    }


def test_render_monitor_html_contains_status_and_next_actions():
    monitor = {
        "generated_at": "2026-07-10T09:50:01",
        "overall": {"status": "on_track", "label": "On track", "score": 82},
        "money": {"royalties": 6.77, "orders": 60, "kenp": 159},
        "timeline": {"label": "On track", "days_left": 21, "checkpoint": "2026-07-31"},
        "setup": {"hero_live": "5/5", "hero_select": "5/5", "hero_aplus": "5/5"},
        "manual": {"pinterest": {"label": "2/4 done", "remaining": ["german", "taxes"]}},
        "promo": {"active": [], "upcoming": [{"slug": "workbook-es", "start": "2026-07-15", "end": "2026-07-19"}]},
        "health": {"kdp_queue": "0", "category": "ok", "sales_sync": "fresh"},
        "blockers": {"count": 0, "items": []},
        "today_actions": [{"channel": "Monitor", "action": "ดูรายงานวันนี้"}],
        "decision": {"recommendation": "รอ checkpoint ก่อนซื้อ paid promo"},
        "actual_vs_plan": {
            "metrics": [
                {"name": "Revenue", "actual_label": "$6.77", "plan_label": "$25.00", "percent": 27, "status": "early"},
                {"name": "Pinterest", "actual_label": "2", "plan_label": "4", "percent": 50, "status": "behind"},
            ],
            "roles": {
                "CFO": {"status": "early", "verdict": "Revenue proof ยังไม่พอ"},
                "COO": {"status": "on_plan", "verdict": "ระบบพร้อม"},
                "CMO": {"status": "behind", "verdict": "Pinterest ยังไม่ครบ"},
                "KDP Strategist": {"status": "watch", "verdict": "รอ promo windows"},
            },
        },
        "kdp_agent": {
            "mode": "auto_advisor",
            "next_actions": ["อย่าเพิ่งซื้อ paid promo", "เร่ง Pinterest ที่เหลือ"],
            "action_queue": [
                {"owner": "CMO", "task": "เร่ง Pinterest ที่เหลือ", "due": "ก่อน 2026-07-15", "status": "due_now"}
            ],
            "decision_gates": [
                {"name": "Paid promo gate", "status": "closed", "rule": "รอ proof หลัง promo windows"}
            ],
            "free_growth_engine": {
                "mode": "auto_free_actions",
                "decisions": [
                    {"action": "free_post", "channel": "Pinterest/Reddit", "reason": "promo approaching", "execute": True}
                ],
            },
        },
    }

    html = dr.render_monitor_html(monitor)

    assert "Libra Monitor" in html
    assert "Actual vs Plan" in html
    assert "bar-fill" in html
    assert "$25.00" in html
    assert "CFO" in html
    assert "KDP Strategist" in html
    assert "อย่าเพิ่งซื้อ paid promo" in html
    assert "Action Queue" in html
    assert "Decision Gates" in html
    assert "Free Growth Engine" in html
    assert "free_post" in html
    assert "On track" in html
    assert "$6.77" in html
    assert "2/4 done" in html
    assert "รอ checkpoint ก่อนซื้อ paid promo" in html


def test_kdp_agent_digest_summarizes_roles_queue_and_gates():
    state = {
        "overall": {"status": "on_track", "score": 92},
        "roles": {
            "CFO": {"status": "early"},
            "COO": {"status": "on_plan"},
            "CMO": {"status": "behind"},
            "KDP Strategist": {"status": "watch"},
        },
        "agent": {
            "next_actions": ["อย่าเพิ่งซื้อ paid promo", "เร่ง Pinterest ที่เหลือ"],
            "action_queue": [
                {"owner": "CMO", "task": "เร่ง Pinterest ที่เหลือ", "due": "ก่อน 2026-07-15", "status": "due_now"}
            ],
            "decision_gates": [
                {"name": "Paid promo gate", "status": "closed", "rule": "รอ proof หลัง promo windows"}
            ],
            "free_growth_engine": {
                "decisions": [
                    {"action": "free_post", "channel": "Pinterest/Reddit", "reason": "promo approaching", "execute": True}
                ],
            },
        },
        "actual_vs_plan": [
            {"name": "Revenue", "actual_label": "$6.77", "plan_label": "$25.00", "percent": 27, "status": "early"}
        ],
        "blockers": {"count": 0},
    }

    msg = dr.kdp_agent_digest(state)

    assert "Libra KDP Auto Manager" in msg
    assert "Score: 92" in msg
    assert "CFO=early" in msg
    assert "CMO=behind" in msg
    assert "เร่ง Pinterest ที่เหลือ" in msg
    assert "Paid promo gate: closed" in msg
    assert "free_post" in msg


def test_free_growth_engine_can_open_free_promo_when_no_near_promo_and_metrics_behind():
    report = {
        "checkpoint": {"days_left": 12},
        "money": {"mtd_royalties_usd": 1.0, "mtd_orders_all_types": 5, "mtd_kenp": 0},
        "free_promos": {"total_downloads_in_promo_windows": 0, "active": [], "upcoming": []},
        "manual_progress": {"pinterest": {"completed_count": 4, "remaining_count": 0, "remaining_slugs": []}},
        "hero_books": [
            {"slug": "taxes", "live_status": "LIVE", "select": True, "aplus": "submitted", "free_promo": None},
        ],
    }
    actual_vs_plan = {
        "metrics": [
            {"name": "Revenue", "percent": 4, "status": "behind"},
            {"name": "Free downloads", "percent": 0, "status": "behind"},
        ],
        "roles": {"CMO": {"status": "on_plan"}},
    }

    engine = dr.build_free_growth_engine(report, actual_vs_plan, [])

    assert engine["decisions"][0]["action"] == "free_promo"
    assert engine["decisions"][0]["slug"] == "taxes"
    assert engine["decisions"][0]["execute"] is True
