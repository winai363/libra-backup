import json

from scripts.experiment_proposer import (
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


def test_free_promo_only_for_enrolled_never_promoted():
    enrolled = {"kdp_select": {"status": "Enrolled"}}
    assert free_promo_candidate("s", enrolled) is not None
    assert free_promo_candidate("s", {**enrolled, "free_promo": {"status": "Done"}}) is None
    assert free_promo_candidate("s", {}) is None


def test_gather_skips_active_slugs_and_validates(tmp_path):
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
    create_experiment(db, slug="busy-book", asin="A1", variable="promotion",
                      action={"kind": "free_promo", "cost_usd": 0, "proposed_value": "2-day KDP Select free promotion"},
                      now=datetime(2026, 7, 11, tzinfo=timezone.utc))

    proposals = gather_proposals(kdp, db, LEAVES)

    assert [p["slug"] for p in proposals] == ["free-candidate"]
    assert proposals[0]["action"]["kind"] == "free_promo"
