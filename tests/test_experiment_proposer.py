import json

from scripts.experiment_proposer import (
    distribution_evidence,
    free_promo_candidate,
    gather_proposals,
    leaf_drivable,
    pick_category_fix,
    strip_store_prefix,
)

LEAVES = {
    "Business & Money > Taxation > Small Business",
    "Business & Money > Taxation > Personal",
    "Computers & Technology > Computer Science > Artificial Intelligence > Machine Learning",
    "Computers & Technology > Computer Science > Artificial Intelligence > Natural Language Processing",
    "Health, Fitness & Dieting > Personal Health > Men's Health > General",
}


def test_strip_store_prefix_drops_kindle_root_only():
    assert strip_store_prefix("Kindle eBooks > Business & Money > Taxation > Small Business") == \
        "Business & Money > Taxation > Small Business"
    assert strip_store_prefix("Business & Money > Taxation > Personal") == \
        "Business & Money > Taxation > Personal"


def test_leaf_drivable_rejects_stopword_leaves():
    assert leaf_drivable("Health, Fitness & Dieting > Personal Health > Men's Health > General") is False
    assert leaf_drivable("Business & Money > Taxation > Small Business") is True


def test_store_prefixed_valid_paths_are_not_experiment_material():
    listing = {
        "title": "Easy Taxes",
        "keywords": ["small business taxes"],
        "categories": ["Kindle eBooks > Business & Money > Taxation > Small Business",
                       "Kindle eBooks > Business & Money > Taxation > Personal"],
    }
    assert pick_category_fix(listing, LEAVES) is None


def test_broken_parent_path_needs_two_nongeneric_overlaps():
    base = {
        "categories": ["Computers & Technology > Computer Science > Artificial Intelligence",
                       "Business & Money > Taxation > Small Business"],
    }
    spanish = {**base, "title": "Ingeniería de Prompts",
               "keywords": ["automatización de tareas con IA"]}
    assert pick_category_fix(spanish, LEAVES) is None  # unsure → skip, never guess

    english = {**base, "title": "Machine Learning Workflows",
               "keywords": ["machine learning for professionals"]}
    fix = pick_category_fix(english, LEAVES)
    assert fix == (
        "Computers & Technology > Computer Science > Artificial Intelligence",
        "Computers & Technology > Computer Science > Artificial Intelligence > Machine Learning",
    )


def test_undrivable_sibling_categories_block_the_book():
    listing = {
        "title": "Machine Learning Workflows",
        "keywords": ["machine learning"],
        "categories": ["Computers & Technology > Computer Science > Artificial Intelligence",
                       "Health, Fitness & Dieting > Personal Health > Men's Health > General"],
    }
    # The General leaf can't be re-driven by the executor — abort risk → skip.
    assert pick_category_fix(listing, LEAVES) is None


def test_price_candidate_needs_ku_reading_but_no_royalties(tmp_path):
    from scripts.experiment_proposer import price_candidate

    def _book(slug, listing, history=None, rec_price=None):
        folder = tmp_path / slug
        folder.mkdir(exist_ok=True)
        if history is not None:
            (folder / "feedback-history.json").write_text(json.dumps(history))
        if rec_price is not None:
            (folder / "pricing-recommendation.json").write_text(
                json.dumps({"recommended_price_usd": rec_price}))
        return price_candidate(slug, listing, listing.pop("_royalties", 0.0), tmp_path)

    live = {"live_status": "LIVE", "price": 5.99}
    # KU reads AND real traffic (orders incl. free) — a measurable conversion test
    read_history = [{"mtd_orders": 12, "mtd_kenp": 133}, {"mtd_orders": 14, "mtd_kenp": 147}]

    good = _book("good", dict(live), read_history)
    assert good is not None and good["proposed_value"] == "2.99" and good["variable"] == "price"

    assert _book("blocked", {**live, "live_status": "BLOCKED"}, read_history) is None
    assert _book("cheap", {**live, "price": 2.99}, read_history) is None
    assert _book("no-history", dict(live), None) is None            # ไม่มั่นใจ = ข้าม
    assert _book("unread", dict(live), [{"mtd_kenp": 3}]) is None   # stray pages ≠ read
    # distribution-starved: passes the old KENP≥50 gate but traffic is far below
    # the noise floor → visibility problem, not a price problem → skip the slot
    assert _book("starved", dict(live), [{"mtd_orders": 1, "mtd_kenp": 60}]) is None
    assert _book("earning", {**live, "_royalties": 5.0}, read_history) is None

    # price never recorded on listing → fall back to the upload-time
    # recommended price; no record at all → skip, never guess
    unpriced = {"live_status": "LIVE"}
    assert _book("rec-price", dict(unpriced), read_history, rec_price=6.99) is not None
    assert _book("rec-cheap", dict(unpriced), read_history, rec_price=2.99) is None
    assert _book("no-price-record", dict(unpriced), read_history) is None

    # a promo inside the 14-day evaluation window pollutes the price signal
    from datetime import date, timedelta
    soon = {"status": "Scheduled",
            "start": (date.today() + timedelta(days=10)).isoformat(),
            "end": (date.today() + timedelta(days=11)).isoformat()}
    passed = {"status": "Done",
              "start": (date.today() - timedelta(days=20)).isoformat(),
              "end": (date.today() - timedelta(days=18)).isoformat()}
    assert _book("promo-soon", {**live, "free_promo": soon}, read_history) is None
    assert _book("promo-past", {**live, "free_promo": passed}, read_history) is not None


def test_free_promo_only_for_enrolled_never_promoted():
    enrolled = {"kdp_select": {"status": "Enrolled"}}
    assert free_promo_candidate("s", enrolled) is not None
    assert free_promo_candidate("s", {**enrolled, "free_promo": {"status": "Done"}}) is None
    assert free_promo_candidate("s", {}) is None


def _pair_slugs(tmp_path, monkeypatch, slugs):
    # experiment_proposer imports validate_action from the flat
    # "kdp_action_executor" module (scripts/ on sys.path), not the
    # scripts.kdp_action_executor instance the executor tests use.
    import kdp_action_executor as executor_module

    pairings = tmp_path / "promo_pairings.json"
    pairings.write_text(json.dumps({"pairings": {slug: [{"channel": "reddit"}] for slug in slugs}}))
    schedule = tmp_path / "reddit_promo_schedule.json"
    schedule.write_text(json.dumps({"posts": [
        {"slug": slug, "post_url": f"https://example.com/{slug}"} for slug in slugs
    ]}))
    monkeypatch.setattr(executor_module, "PAIRINGS_FILE", pairings)
    monkeypatch.setattr(executor_module, "REDDIT_SCHEDULE_FILE", schedule)


def test_gather_skips_active_slugs_and_validates(tmp_path, monkeypatch):
    import sqlite3

    from business_ledger import init_ledger
    from profit_agent import _init_schema, create_experiment
    from datetime import datetime, timezone

    db = tmp_path / "ledger.db"
    init_ledger(db)
    _init_schema(db)
    kdp = tmp_path / "kdp"
    for slug, listing in {
        "busy-book": {"status": "uploaded", "asin": "A1", "kdp_select": {"status": "Enrolled"}},
        "free-candidate": {"status": "uploaded", "asin": "A2", "kdp_select": {"status": "Enrolled"}},
    }.items():
        folder = kdp / slug
        folder.mkdir(parents=True)
        (folder / "listing.json").write_text(json.dumps(listing))
    _pair_slugs(tmp_path, monkeypatch, ["busy-book", "free-candidate"])
    create_experiment(db, slug="busy-book", asin="A1", variable="promotion",
                      action={"kind": "free_promo", "cost_usd": 0, "proposed_value": "2-day KDP Select free promotion"},
                      now=datetime(2026, 7, 11, tzinfo=timezone.utc))

    proposals = gather_proposals(kdp, db, LEAVES)

    assert [p["slug"] for p in proposals] == ["free-candidate"]
    assert proposals[0]["action"]["kind"] == "free_promo"


def test_gather_never_proposes_an_unpaired_free_promo(tmp_path, monkeypatch):
    from business_ledger import init_ledger
    from profit_agent import _init_schema

    db = tmp_path / "ledger.db"
    init_ledger(db)
    _init_schema(db)
    kdp = tmp_path / "kdp"
    folder = kdp / "naked-candidate"
    folder.mkdir(parents=True)
    (folder / "listing.json").write_text(json.dumps(
        {"status": "uploaded", "asin": "A9", "kdp_select": {"status": "Enrolled"}}))
    _pair_slugs(tmp_path, monkeypatch, [])  # no pairing declared for any slug

    proposals = gather_proposals(kdp, db, LEAVES)

    assert proposals == []


def test_distribution_evidence_does_not_treat_reminder_as_publication():
    evidence = distribution_evidence(
        "book",
        {"pairings": {"book": [{"channel": "reddit"}]}},
        {"posts": [{"slug": "book", "reminded_at": "2026-07-17T20:00:00"}]},
    )

    assert evidence["status"] == "reminded"
    assert evidence["usable_for_promo"] is False


def test_distribution_evidence_requires_external_proof():
    verified = distribution_evidence(
        "book", {}, {"posts": [{"slug": "book", "post_url": "https://example.com/post/1"}]}
    )
    capable = distribution_evidence(
        "book",
        {"pairings": {"book": [{"channel": "reddit", "publisher_returns_evidence": True}]}},
        {},
    )

    assert verified == {
        "status": "verified", "usable_for_promo": True,
        "channel": "reddit", "proof": "https://example.com/post/1",
    }
    assert capable["status"] == "planned"
    assert capable["usable_for_promo"] is False
