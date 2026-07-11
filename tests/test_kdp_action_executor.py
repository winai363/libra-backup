from scripts.kdp_action_executor import (
    validate_action,
    validate_category_action,
    verify_chips,
)

LEAVES = {
    "Crafts, Hobbies & Home > Crafts & Hobbies > Painting",
    "Arts & Photography > Art > Painting > General",
}
LISTING = {
    "title": "Acuarela",
    "kdp_book_id": "X1",
    "categories": [
        "Arts & Photography > Art > Painting > General",
        "Arts & Photography > Art > Instruction & Reference > Study & Teaching",
        "Arts & Photography > Art > Instruction & Reference > Color",
    ],
}


def test_category_gate_rejects_paths_missing_from_kdp_tree():
    action = {"kind": "category_update", "proposed_value": "Watercolor Painting",
              "replaces": LISTING["categories"][2]}
    ok, reason, _ = validate_category_action(action, LISTING, LEAVES)
    assert ok is False
    assert "not in audited KDP tree" in reason


def test_category_gate_swaps_only_the_replaced_path():
    action = {"kind": "category_update",
              "proposed_value": "Crafts, Hobbies & Home > Crafts & Hobbies > Painting",
              "replaces": LISTING["categories"][2]}
    ok, reason, targets = validate_category_action(action, LISTING, LEAVES)
    assert ok is True
    assert targets == [
        LISTING["categories"][0],
        LISTING["categories"][1],
        "Crafts, Hobbies & Home > Crafts & Hobbies > Painting",
    ]


def test_category_gate_rejects_replacing_a_path_not_on_listing():
    action = {"kind": "category_update",
              "proposed_value": "Crafts, Hobbies & Home > Crafts & Hobbies > Painting",
              "replaces": "Some > Other > Path"}
    ok, reason, _ = validate_category_action(action, LISTING, LEAVES)
    assert ok is False
    assert "not on listing" in reason


def test_title_changes_are_permanently_refused():
    action = {"kind": "metadata_update", "field": "title",
              "proposed_value": "New Title", "cost_usd": 0}
    ok, reason, _ = validate_action(action, LISTING, LEAVES)
    assert ok is False
    assert "refused" in reason


def test_paid_actions_are_refused():
    action = {"kind": "category_update", "cost_usd": 5,
              "proposed_value": "Crafts, Hobbies & Home > Crafts & Hobbies > Painting"}
    ok, reason, _ = validate_action(action, LISTING, LEAVES)
    assert ok is False
    assert "zero-cost" in reason


def test_unknown_kinds_stay_manual():
    ok, reason, _ = validate_action({"kind": "price_update", "cost_usd": 0}, LISTING, LEAVES)
    assert ok is False
    assert "unsupported" in reason


def test_free_promo_days_parsed_and_bounded():
    ok, _, extra = validate_action(
        {"kind": "free_promo", "cost_usd": 0, "proposed_value": "2-day KDP Select free promotion"},
        LISTING, LEAVES)
    assert ok is True and extra == {"days": 2}
    ok, reason, _ = validate_action(
        {"kind": "free_promo", "cost_usd": 0, "proposed_value": "9-day free promotion"},
        LISTING, LEAVES)
    assert ok is False and "range" in reason


def test_verify_chips_matches_parent_paths_without_leaf():
    # KDP chips omit the placement leaf — verify on the parent path.
    chips = [
        "Kindle Books › Arts & Photography › Art › Instruction & Reference",
        "Kindle Books › Crafts, Hobbies & Home › Crafts & Hobbies",
    ]
    ok, missing = verify_chips(
        ["Arts & Photography > Art > Instruction & Reference > Study & Teaching",
         "Crafts, Hobbies & Home > Crafts & Hobbies > Painting"], chips)
    assert ok is True and missing is None


def test_verify_chips_allows_shared_parent_row():
    # KDP renders placements under the same parent in ONE accordion row —
    # leaf-level coverage is checked via the placements counter instead.
    chips = ["Kindle Books › Arts & Photography › Art › Instruction & Reference"]
    ok, missing = verify_chips(
        ["Arts & Photography > Art > Instruction & Reference > Study & Teaching",
         "Arts & Photography > Art > Instruction & Reference > Color"], chips)
    assert ok is True and missing is None


def test_verify_chips_rejects_wrong_subtree():
    chips = ["Kindle Books › Crafts, Hobbies & Home › Crafts & Hobbies"]
    ok, missing = verify_chips(
        ["Arts & Photography > Art > Painting > Portraits"], chips)
    assert ok is False and missing == "Arts & Photography > Art > Painting > Portraits"
