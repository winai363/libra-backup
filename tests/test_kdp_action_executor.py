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


def test_category_update_refused_on_published_book():
    # Republishing re-triggers Amazon content review; a published asset must
    # never be risked for a category tweak (acuarela incident 2026-07-11).
    action = {"kind": "category_update", "cost_usd": 0,
              "proposed_value": "Crafts, Hobbies & Home > Crafts & Hobbies > Painting",
              "replaces": LISTING["categories"][2]}
    for published in ({"live_status": "LIVE"},          # normal live book
                      {"asin": "B0TEST12345"},          # stale/missing live_status
                      {"asin": "B0TEST12345", "live_status": "BLOCKED"}):
        ok, reason, _ = validate_action(action, {**LISTING, **published}, LEAVES)
        assert ok is False
        assert "republishing a published book" in reason


def test_category_update_allowed_on_true_draft():
    # No ASIN = never been through publish; the only safe lane for experiments.
    action = {"kind": "category_update", "cost_usd": 0,
              "proposed_value": "Crafts, Hobbies & Home > Crafts & Hobbies > Painting",
              "replaces": LISTING["categories"][2]}
    ok, reason, extra = validate_action(action, LISTING, LEAVES)
    assert ok is True
    assert extra["targets"]


def _price_action(value="2.99"):
    return {"kind": "price_update", "cost_usd": 0, "proposed_value": value}


LIVE_PRICED = {**LISTING, "live_status": "LIVE", "price": 5.99}


def test_price_update_gates():
    # outside the 70% band — both ends
    for bad in ("2.98", "10.00", "0", "abc", None):
        ok, reason, _ = validate_action(_price_action(bad), LIVE_PRICED, LEAVES)
        assert ok is False, bad
    # LIVE books only (BLOCKED/None/UNKNOWN all refused)
    ok, reason, _ = validate_action(_price_action(), {**LIVE_PRICED, "live_status": "BLOCKED"}, LEAVES)
    assert ok is False and "LIVE" in reason
    # no-op change refused
    ok, reason, _ = validate_action(_price_action("5.99"), LIVE_PRICED, LEAVES)
    assert ok is False and "already" in reason
    # free promo covering today → royalty binding locked → refused
    from datetime import date, timedelta
    today = date.today()
    promo = {"status": "Scheduled", "start": (today - timedelta(days=1)).isoformat(),
             "end": (today + timedelta(days=1)).isoformat()}
    ok, reason, _ = validate_action(_price_action(), {**LIVE_PRICED, "free_promo": promo}, LEAVES)
    assert ok is False and "promo" in reason


def test_price_update_allowed_within_band_on_live_book():
    ok, reason, extra = validate_action(_price_action("2.99"), LIVE_PRICED, LEAVES)
    assert ok is True
    assert extra == {"price": "2.99"}
    # a future (non-overlapping) promo does not block a price change
    from datetime import date, timedelta
    future = {"status": "Scheduled",
              "start": (date.today() + timedelta(days=7)).isoformat(),
              "end": (date.today() + timedelta(days=9)).isoformat()}
    ok, _, extra = validate_action(_price_action("4.49"), {**LIVE_PRICED, "free_promo": future}, LEAVES)
    assert ok is True and extra == {"price": "4.49"}


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
    ok, reason, _ = validate_action({"kind": "cover_update", "cost_usd": 0}, LISTING, LEAVES)
    assert ok is False
    assert "unsupported" in reason


def _pair_slug(tmp_path, monkeypatch, slug):
    import json

    import scripts.kdp_action_executor as executor_module

    pairings = tmp_path / "promo_pairings.json"
    pairings.write_text(json.dumps({"pairings": {slug: [{"channel": "reddit"}]}}))
    monkeypatch.setattr(executor_module, "PAIRINGS_FILE", pairings)
    monkeypatch.setattr(executor_module, "REDDIT_SCHEDULE_FILE", tmp_path / "reddit_promo_schedule.json")


def test_free_promo_days_parsed_and_bounded(tmp_path, monkeypatch):
    _pair_slug(tmp_path, monkeypatch, "acuarela")
    ok, _, extra = validate_action(
        {"kind": "free_promo", "slug": "acuarela", "cost_usd": 0,
         "proposed_value": "2-day KDP Select free promotion"},
        LISTING, LEAVES)
    assert ok is True and extra == {"days": 2}
    ok, reason, _ = validate_action(
        {"kind": "free_promo", "slug": "acuarela", "cost_usd": 0,
         "proposed_value": "9-day free promotion"},
        LISTING, LEAVES)
    assert ok is False and "range" in reason


def test_free_promo_without_distribution_pairing_is_refused(tmp_path, monkeypatch):
    # 13 of 17 naked promos measured 0 downloads — a free promo without a
    # paired external channel burns the 5-free-days/term quota for nothing.
    _pair_slug(tmp_path, monkeypatch, "some-other-slug")
    ok, reason, _ = validate_action(
        {"kind": "free_promo", "slug": "unpaired-book", "cost_usd": 0,
         "proposed_value": "2-day KDP Select free promotion"},
        LISTING, LEAVES)
    assert ok is False and "paired distribution channel" in reason


def test_free_promo_pairing_accepts_scheduled_reddit_post(tmp_path, monkeypatch):
    import json

    import scripts.kdp_action_executor as executor_module

    monkeypatch.setattr(executor_module, "PAIRINGS_FILE", tmp_path / "missing.json")
    schedule = tmp_path / "reddit_promo_schedule.json"
    schedule.write_text(json.dumps({"posts": [{"date": "2026-07-16", "slug": "reddit-book"}]}))
    monkeypatch.setattr(executor_module, "REDDIT_SCHEDULE_FILE", schedule)
    ok, _, extra = validate_action(
        {"kind": "free_promo", "slug": "reddit-book", "cost_usd": 0,
         "proposed_value": "2-day KDP Select free promotion"},
        LISTING, LEAVES)
    assert ok is True and extra == {"days": 2}


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
