"""Demand must come from measured sales, never from a model's opinion."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from business_ledger import init_ledger
from demand_analysis import (
    demand_report,
    evidence_strength,
    title_performance,
)


def _seed(tmp_path, snapshots):
    """snapshots: list of (observed_at, month, [(asin, royalties, orders, kenp)])"""
    db = tmp_path / "ledger.db"
    init_ledger(db)
    with sqlite3.connect(db) as connection:
        for observed_at, month, titles in snapshots:
            overview = {
                "royalties_usd": sum(t[1] for t in titles),
                "orders_all_types": sum(t[2] for t in titles),
                "kenp": sum(t[3] for t in titles),
            }
            cursor = connection.execute(
                "INSERT INTO kdp_snapshots(observed_at, month, royalties_usd,"
                " orders_all_types, kenp, raw_json) VALUES (?,?,?,?,?,?)",
                (observed_at, month, overview["royalties_usd"], overview["orders_all_types"],
                 overview["kenp"], json.dumps({"overview": overview})),
            )
            for asin, royalties, orders, kenp in titles:
                connection.execute(
                    "INSERT INTO kdp_title_attribution(snapshot_id, asin, royalties_usd,"
                    " orders_count, kenp) VALUES (?,?,?,?,?)",
                    (cursor.lastrowid, asin, royalties, orders, kenp),
                )
    return db


def _books(tmp_path, books):
    """books: list of (slug, asin, language, category, live_status, published)"""
    root = tmp_path / "kdp"
    for slug, asin, language, category, live_status, published in books:
        folder = root / slug
        folder.mkdir(parents=True)
        (folder / "listing.json").write_text(json.dumps({
            "asin": asin,
            "language": language,
            "categories": [category],
            "live_status": live_status,
            "publish_submission_confirmed_at": published,
        }))
    return root


def test_cumulative_snapshots_are_not_double_counted(tmp_path):
    """KDP snapshots are month-to-date totals — summing rows would inflate revenue."""
    db = _seed(tmp_path, [
        ("2026-07-01T09:00:00+07:00", "2026-07", [("A1", 1.00, 1, 50)]),
        ("2026-07-15T09:00:00+07:00", "2026-07", [("A1", 3.00, 2, 120)]),
        ("2026-08-02T09:00:00+07:00", "2026-08", [("A1", 2.00, 1, 40)]),
    ])
    books = _books(tmp_path, [
        ("book-a", "A1", "Spanish", "Crafts > Painting", "LIVE", "2026-06-01"),
    ])

    performance = title_performance(db, books, today="2026-08-22")

    assert len(performance) == 1
    row = performance[0]
    assert row["royalties_usd"] == pytest.approx(5.00)  # 3.00 (Jul max) + 2.00 (Aug max)
    assert row["kenp"] == 160
    assert row["months"]["2026-07"]["royalties_usd"] == pytest.approx(3.00)


def test_titles_with_no_sales_are_reported_not_dropped(tmp_path):
    db = _seed(tmp_path, [("2026-08-02T09:00:00+07:00", "2026-08", [("A1", 2.00, 1, 40)])])
    books = _books(tmp_path, [
        ("book-a", "A1", "Spanish", "Crafts > Painting", "LIVE", "2026-06-01"),
        ("book-b", "A2", "German", "Computers > AI", "LIVE", "2026-05-01"),
    ])

    performance = title_performance(db, books, today="2026-08-22")

    slugs = {row["slug"]: row for row in performance}
    assert slugs["book-b"]["royalties_usd"] == 0
    assert slugs["book-b"]["kenp"] == 0
    assert slugs["book-b"]["evidence"] == "no_signal"


def test_evidence_strength_is_deterministic_and_never_claims_strong():
    assert evidence_strength(royalties=0, kenp=0) == "no_signal"
    assert evidence_strength(royalties=0, kenp=40) == "weak"
    assert evidence_strength(royalties=0.5, kenp=0) == "weak"
    assert evidence_strength(royalties=1.5, kenp=0) == "moderate"
    assert evidence_strength(royalties=0, kenp=150) == "moderate"
    # Our whole catalogue earns tens of dollars — no tier may read as proven.
    assert "strong" not in {
        evidence_strength(royalties=r, kenp=k)
        for r in (0, 1, 10, 1000) for k in (0, 10, 10000)
    }


def test_clusters_flag_repeated_failure_at_scale(tmp_path):
    """Three or more titles in one niche with zero signal is our strongest datum."""
    db = _seed(tmp_path, [("2026-08-02T09:00:00+07:00", "2026-08", [("A9", 2.00, 1, 40)])])
    books = _books(tmp_path, [
        ("ai-one", "A1", "German", "Computers > AI", "LIVE", "2026-05-01"),
        ("ai-two", "A2", "Dutch", "Computers > AI", "LIVE", "2026-05-01"),
        ("ai-three", "A3", "French", "Computers > AI", "LIVE", "2026-05-01"),
        ("acuarela-one", "A9", "Spanish", "Crafts > Painting", "LIVE", "2026-06-01"),
    ])

    report = demand_report(db, books, today="2026-08-22")

    clusters = {c["theme"]: c for c in report["clusters"]}
    assert clusters["ai_productivity"]["verdict"] == "no_signal_at_scale"
    assert clusters["ai_productivity"]["titles"] == 3
    assert clusters["art_craft"]["verdict"] == "signal_present"


def test_language_breakdown_is_kept_inside_each_cluster(tmp_path):
    db = _seed(tmp_path, [("2026-08-02T09:00:00+07:00", "2026-08", [("A1", 2.00, 1, 40)])])
    books = _books(tmp_path, [
        ("ai-de", "A1", "German", "Computers > AI", "LIVE", "2026-05-01"),
        ("ai-nl", "A2", "Dutch", "Computers > AI", "LIVE", "2026-05-01"),
    ])

    cluster = demand_report(db, books, today="2026-08-22")["clusters"][0]

    assert cluster["languages"]["German"]["royalties_usd"] == pytest.approx(2.00)
    assert cluster["languages"]["Dutch"]["royalties_usd"] == 0


def test_single_title_niche_is_insufficient_data_not_a_winner(tmp_path):
    db = _seed(tmp_path, [("2026-08-02T09:00:00+07:00", "2026-08", [("A1", 5.00, 1, 0)])])
    books = _books(tmp_path, [
        ("senior-smartphone-french", "A1", "French", "Computers > Mobile Devices", "LIVE", "2026-07-01"),
    ])

    report = demand_report(db, books, today="2026-08-22")

    cluster = report["clusters"][0]
    assert cluster["verdict"] == "signal_present"
    assert cluster["confidence"] == "low"
    assert cluster["sample_size"] == 1


def test_report_states_the_traffic_blind_spot_explicitly(tmp_path):
    db = _seed(tmp_path, [("2026-08-02T09:00:00+07:00", "2026-08", [("A1", 2.00, 1, 40)])])
    books = _books(tmp_path, [("book-a", "A1", "Spanish", "Crafts > Painting", "LIVE", "2026-06-01")])

    report = demand_report(db, books, today="2026-08-22")

    assert report["caveats"]["traffic_data"] == "absent"
    assert "zero revenue does not prove absent demand" in report["caveats"]["interpretation"]
    assert report["totals"]["royalties_usd"] == pytest.approx(2.00)
    assert report["totals"]["titles_with_any_signal"] == 1


def test_blocked_titles_are_excluded_from_recommendations_but_kept_as_evidence(tmp_path):
    db = _seed(tmp_path, [("2026-08-02T09:00:00+07:00", "2026-08", [("A1", 4.00, 2, 300)])])
    books = _books(tmp_path, [
        ("adhd-blocked-book", "A1", "Spanish", "Health > ADHD", "BLOCKED", "2026-06-01"),
    ])

    report = demand_report(db, books, today="2026-08-22")

    cluster = report["clusters"][0]
    assert cluster["blocked_titles"] == 1
    assert cluster["verdict"] == "signal_present"
    assert cluster["safe_to_reuse"] is False
    assert cluster["blocked_reason"] == "only_evidence_is_a_blocked_title"


def test_theme_classification_records_the_matched_keyword():
    from demand_analysis import theme_for_slug

    assert theme_for_slug("senior-smartphone-french") == ("senior_tech", "senior")
    assert theme_for_slug("ai-productivity-spanish") == ("ai_productivity", "ai-")
    assert theme_for_slug("guia-completa-iva-autonomos-espana-2026")[0] == "tax_accounting_spain"
    assert theme_for_slug("mystery-title-xyz") == ("unclassified", None)


def test_report_is_pure_data_with_no_model_call():
    source = (Path(__file__).resolve().parent.parent / "demand_analysis.py").read_text()
    for forbidden in ("openai", "OpenAI", "anthropic", "requests", "httpx", "call_gpt"):
        assert forbidden not in source


# ── product opportunities ────────────────────────────────────────────────────

def test_opportunities_rank_by_revenue_per_live_title_not_raw_revenue(tmp_path):
    """20 titles earning $5 is a worse bet than 1 title earning $5."""
    from demand_analysis import product_opportunities

    db = _seed(tmp_path, [("2026-08-02T09:00:00+07:00", "2026-08", [
        ("A1", 5.00, 1, 0), ("A2", 2.00, 1, 0), ("A3", 2.00, 1, 0), ("A4", 2.00, 1, 0),
    ])])
    books = _books(tmp_path, [
        ("senior-smartphone-french", "A1", "French", "Computers > Mobile", "LIVE", "2026-07-01"),
        ("ai-one", "A2", "German", "Computers > AI", "LIVE", "2026-05-01"),
        ("ai-two", "A3", "Dutch", "Computers > AI", "LIVE", "2026-05-01"),
        ("ai-three", "A4", "French", "Computers > AI", "LIVE", "2026-05-01"),
        ("ai-four", "A5", "Italian", "Computers > AI", "LIVE", "2026-05-01"),
    ])

    report = demand_report(db, books, today="2026-08-22")
    opportunities = product_opportunities(report)

    assert opportunities[0]["theme"] == "senior_tech"          # $5.00 / 1 live title
    assert opportunities[0]["royalties_per_live_title"] == pytest.approx(5.00)
    ai = [o for o in opportunities if o["theme"] == "ai_productivity"][0]
    assert ai["royalties_per_live_title"] == pytest.approx(1.50)  # $6.00 / 4 live


def test_opportunity_carries_provenance_back_to_real_titles(tmp_path):
    from demand_analysis import product_opportunities

    db = _seed(tmp_path, [("2026-08-02T09:00:00+07:00", "2026-08", [("A1", 5.00, 1, 0)])])
    books = _books(tmp_path, [
        ("senior-smartphone-french", "A1", "French", "Computers > Mobile", "LIVE", "2026-07-01"),
    ])

    opportunity = product_opportunities(demand_report(db, books, today="2026-08-22"))[0]

    assert opportunity["evidence"]["titles"] == [
        {"slug": "senior-smartphone-french", "asin": "A1", "royalties_usd": 5.00,
         "kenp": 0, "live_status": "LIVE"}
    ]
    assert opportunity["evidence"]["months_observed"] == ["2026-08"]
    assert opportunity["confidence"] == "low"


def test_visual_themes_require_instructional_images(tmp_path):
    from demand_analysis import product_opportunities

    db = _seed(tmp_path, [("2026-08-02T09:00:00+07:00", "2026-08", [("A1", 5.00, 1, 0)])])
    books = _books(tmp_path, [
        ("acuarela-basics", "A1", "Spanish", "Crafts > Painting", "LIVE", "2026-07-01"),
    ])

    opportunity = product_opportunities(demand_report(db, books, today="2026-08-22"))[0]

    assert opportunity["visual_required"] is True
    assert "instructional images" in opportunity["must_have"][0]


def test_zero_signal_themes_become_explicit_avoid_list(tmp_path):
    from demand_analysis import product_opportunities, themes_to_avoid

    db = _seed(tmp_path, [("2026-08-02T09:00:00+07:00", "2026-08", [("A9", 1.00, 1, 0)])])
    books = _books(tmp_path, [
        ("anxiety-one", "A1", "English", "Self-Help > Anxiety", "LIVE", "2026-05-01"),
        ("anxiety-two", "A2", "French", "Self-Help > Anxiety", "LIVE", "2026-05-01"),
        ("anxiety-three", "A3", "German", "Self-Help > Anxiety", "LIVE", "2026-05-01"),
        ("acuarela-basics", "A9", "Spanish", "Crafts > Painting", "LIVE", "2026-07-01"),
    ])

    report = demand_report(db, books, today="2026-08-22")

    assert [t["theme"] for t in product_opportunities(report)] == ["art_craft"]
    avoid = themes_to_avoid(report)
    assert avoid[0]["theme"] == "anxiety_mental_health"
    assert avoid[0]["titles_spent"] == 3
    assert avoid[0]["reason"] == "no_signal_at_scale"


def test_blocked_only_niche_never_becomes_an_opportunity(tmp_path):
    from demand_analysis import product_opportunities

    db = _seed(tmp_path, [("2026-08-02T09:00:00+07:00", "2026-08", [("A1", 9.00, 2, 300)])])
    books = _books(tmp_path, [
        ("adhd-blocked", "A1", "Spanish", "Health > ADHD", "BLOCKED", "2026-06-01"),
    ])

    assert product_opportunities(demand_report(db, books, today="2026-08-22")) == []


def test_permanent_no_go_niches_are_never_proposed(tmp_path):
    """diet/meal-plan cost us an account block — no amount of revenue reopens it."""
    from demand_analysis import product_opportunities

    db = _seed(tmp_path, [("2026-08-02T09:00:00+07:00", "2026-08", [("A1", 99.00, 20, 900)])])
    books = _books(tmp_path, [
        ("high-protein-meal-plan-french", "A1", "French", "Health > Diets", "LIVE", "2026-06-01"),
    ])

    assert product_opportunities(demand_report(db, books, today="2026-08-22")) == []


def test_latest_correction_replaces_month_maximum(tmp_path):
    db = _seed(tmp_path, [
        ("2026-08-02T09:00:00+07:00", "2026-08", [("A1", 9, 4, 200)]),
        ("2026-08-03T09:00:00+07:00", "2026-08", [("A1", 2, 1, 40)]),
        ("2026-08-04T09:00:00+07:00", "2026-08", [("A1", 7, 3, 90)]),
    ])
    books = _books(tmp_path, [("ai-one", "A1", "English", "AI", "LIVE", "2026-07-01")])
    row = title_performance(db, books, today="2026-08-03")[0]
    assert (row["royalties_usd"], row["orders"], row["kenp"]) == (2, 1, 40)
    assert row["months"]["2026-08"]["observed_at"] == "2026-08-03T09:00:00+07:00"


def test_mixed_status_revenue_cannot_boost_live_opportunity(tmp_path):
    from demand_analysis import product_opportunities
    db = _seed(tmp_path, [("2026-08-02T09:00:00+07:00", "2026-08", [
        ("A1", 2, 1, 10), ("A2", 90, 30, 900), ("A3", 80, 20, 800),
    ])])
    books = _books(tmp_path, [
        ("ai-live", "A1", "English", "AI", "LIVE", "2026-07-01"),
        ("ai-blocked", "A2", "French", "AI", "BLOCKED", "2026-07-01"),
        ("ai-unknown", "A3", "German", "AI", "UNKNOWN", "2026-07-01"),
    ])
    report = demand_report(db, books, today="2026-08-03")
    result = product_opportunities(report)[0]
    assert result["royalties_per_live_title"] == 2
    assert result["royalties_usd"] == 2
    assert result["kenp"] == 10
    assert result["languages_with_signal"] == ["English"]
    assert [r["asin"] for r in result["evidence"]["titles"]] == ["A1"]
    assert report["totals"]["royalties_usd"] == 172


def test_unknown_status_is_not_a_live_opportunity(tmp_path):
    from demand_analysis import product_opportunities
    db = _seed(tmp_path, [("2026-08-02T09:00:00+07:00", "2026-08", [("A1", 9, 2, 300)])])
    books = _books(tmp_path, [("ai-unknown", "A1", "English", "AI", "UNKNOWN", "2026-07-01")])
    assert product_opportunities(demand_report(db, books, today="2026-08-03")) == []


def test_evidence_reconciles_overview_and_uses_equal_period(tmp_path):
    db = _seed(tmp_path, [
        ("2026-08-05T09:00:00+07:00", "2026-08", [("A1", 2, 1, 10)]),
        ("2026-08-31T09:00:00+07:00", "2026-08", [("A1", 90, 30, 900)]),
        ("2026-09-05T09:00:00+07:00", "2026-09", [("A1", 3, 1, 20)]),
    ])
    with sqlite3.connect(db) as c:
        c.execute("UPDATE kdp_snapshots SET royalties_usd=4 WHERE month='2026-09'")
    books = _books(tmp_path, [("ai-live", "A1", "English", "AI", "LIVE", "2026-07-01")])
    before = db.read_bytes()
    evidence = demand_report(db, books, today="2026-09-06")["sales_evidence"]
    assert db.read_bytes() == before
    current = evidence["months"][-1]
    assert current["attribution_gap_usd"] == 1
    assert current["paid_orders"] is None
    assert current["profit_usd"] is None
    assert evidence["age_days"] == 1
    assert evidence["comparison"]["previous"]["royalties_usd"] == 2
    assert evidence["comparison"]["change_pct"] == 100


def test_no_matching_comparison_date_means_unknown(tmp_path):
    db = _seed(tmp_path, [
        ("2026-08-04T09:00:00+07:00", "2026-08", [("A1", 2, 1, 10)]),
        ("2026-09-05T09:00:00+07:00", "2026-09", [("A1", 3, 1, 20)]),
    ])
    books = _books(tmp_path, [("ai-live", "A1", "English", "AI", "LIVE", "2026-07-01")])
    evidence = demand_report(db, books, today="2026-09-06")["sales_evidence"]
    assert evidence["comparison"]["status"] == "unavailable"
    assert evidence["comparison"]["change_pct"] is None


def test_absent_ledger_is_unknown_and_not_created(tmp_path):
    db = tmp_path / "missing.db"
    report = demand_report(db, tmp_path / "books", today="2026-09-06")
    assert report["sales_evidence"]["status"] == "unavailable"
    assert report["sales_evidence"]["estimated_royalties_usd"] is None
    assert not db.exists()


def test_blocked_register_preserves_notes_without_claiming_cause(tmp_path):
    db = _seed(tmp_path, [])
    books = _books(tmp_path, [("ai-blocked", "A1", "English", "AI", "BLOCKED", "2026-07-01")])
    path = books / "ai-blocked" / "listing.json"
    listing = json.loads(path.read_text())
    listing["blocked"] = {"date": "2026-08-22", "reason": "Old note blames AI disclosure"}
    path.write_text(json.dumps(listing))
    report = demand_report(db, books, today="2026-09-06")
    case = report["rejections"]["cases"][0]
    assert case["local_note"] == listing["blocked"]["reason"]
    assert case["confirmed_cause"] is None
    assert case["source"] == str(path)
    assert report["rejections"]["status_source"] == "local_listings_not_live_verification"


def test_missing_title_keeps_last_known_observation_explicit(tmp_path):
    db = _seed(tmp_path, [
        ("2026-08-02T09:00:00+07:00", "2026-08", [("A1", 2, 1, 10)]),
        ("2026-08-03T09:00:00+07:00", "2026-08", [("A2", 3, 1, 20)]),
    ])
    books = _books(tmp_path, [("ai-live", "A1", "English", "AI", "LIVE", "2026-07-01")])
    row = title_performance(db, books, today="2026-08-03")[0]
    assert row["royalties_usd"] == 2
    assert row["months"]["2026-08"]["observed_at"] == "2026-08-02T09:00:00+07:00"


def test_zero_baseline_and_stale_data_do_not_imply_percent_growth(tmp_path):
    db = _seed(tmp_path, [
        ("2026-08-05T09:00:00+07:00", "2026-08", [("A1", 0, 0, 0)]),
        ("2026-09-05T09:00:00+07:00", "2026-09", [("A1", 3, 1, 20)]),
    ])
    books = _books(tmp_path, [("ai-live", "A1", "English", "AI", "LIVE", "2026-07-01")])
    evidence = demand_report(db, books, today="2026-09-09")["sales_evidence"]
    assert evidence["stale"] is True
    assert evidence["comparison"]["change_pct"] is None
    assert evidence["comparison"]["reason"] == "nonpositive_baseline"


def test_month_end_without_equivalent_day_does_not_compare_full_month(tmp_path):
    db = _seed(tmp_path, [
        ("2026-02-28T09:00:00+07:00", "2026-02", [("A1", 10, 3, 0)]),
        ("2026-03-31T09:00:00+07:00", "2026-03", [("A1", 20, 5, 0)]),
    ])
    books = _books(tmp_path, [("ai-live", "A1", "English", "AI", "LIVE", "2026-01-01")])
    comparison = demand_report(db, books, today="2026-03-31")["sales_evidence"]["comparison"]
    assert comparison["status"] == "unavailable"


def test_unreadable_listing_is_visible_and_does_not_abort_report(tmp_path):
    db = _seed(tmp_path, [])
    books = _books(tmp_path, [("ai-bad", "A1", "English", "AI", "LIVE", "2026-01-01")])
    path = books / "ai-bad" / "listing.json"
    path.write_text("[]")
    report = demand_report(db, books, today="2026-09-06")
    assert report["rejections"]["unreadable_listings"] == [str(path)]


def test_historical_import_is_not_compared_as_current_month(tmp_path):
    db = _seed(tmp_path, [
        ("2026-08-05T09:00:00+07:00", "2026-08", [("A1", 2, 1, 10)]),
        ("2026-09-05T09:00:00+07:00", "2026-07", [("A1", 3, 1, 20)]),
    ])
    books = _books(tmp_path, [("ai-live", "A1", "English", "AI", "LIVE", "2026-07-01")])
    comparison = demand_report(db, books, today="2026-09-06")["sales_evidence"]["comparison"]
    assert comparison["status"] == "unavailable"
