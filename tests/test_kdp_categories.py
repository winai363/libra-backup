"""Category matching — the pure parts, testable without a browser.

Real failure this pins down (22 Aug 2026): the watercolour book could not be
uploaded at all. Its three targets each ended in a leaf whose own name is a
stop-word ("General"), so the leaf was tokenised to nothing, dropped from the
path, and every placement checkbox scored 0 — including the checkbox literally
labelled "General" sitting right there.

Stop-words are right for the middle of a path and wrong for its end: "General"
in "Art > Painting > General" IS the category name.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kdp_categories import _leaf_tokens, _path_segments, _score, _tokens


def test_a_leaf_named_general_survives_the_path():
    """The bug: "General" vanished, leaving a path that pointed nowhere."""
    segs = _path_segments("Arts & Photography > Art > Painting > General")

    assert segs == ["Arts & Photography", "Art", "Painting", "General"]


def test_store_root_prefixes_are_still_dropped():
    assert _path_segments("Kindle eBooks > Arts & Photography > Art > Painting") == [
        "Arts & Photography", "Art", "Painting",
    ]
    assert _path_segments("Books > Kindle Store > Self-Help") == ["Self-Help"]


@pytest.mark.parametrize("leaf", ["General", "Books", "Kindle", "Store"])
def test_leaf_tokens_keep_words_that_are_stopwords_elsewhere(leaf):
    assert _leaf_tokens(leaf) == {leaf.lower()}


def test_a_general_target_matches_the_general_checkbox():
    """This exact comparison returned 0.0 and cancelled the whole upload."""
    assert _score(_leaf_tokens("General"), "General", leaf=True) == 1.0


def test_a_general_target_does_not_match_an_unrelated_leaf():
    for wrong in ("Basketry", "Criticism", "Leatherwork & Hidework", "Blogging"):
        assert _score(_leaf_tokens("General"), wrong, leaf=True) == 0.0


def test_middle_segments_still_ignore_stopwords():
    """Mid-path matching must not regress: "Books" there is noise, not a name."""
    assert _tokens("Kindle eBooks") == set()
    assert _tokens("Arts & Photography") == {"arts", "photography"}


def test_the_three_targets_of_the_watercolour_book_all_resolve_to_a_leaf():
    targets = [
        "Arts & Photography > Art > Painting > General",
        "Arts & Photography > Art > General",
        "Arts & Photography > Art > Techniques",
    ]
    for path in targets:
        segs = _path_segments(path)
        assert segs, path
        assert _leaf_tokens(segs[-1]), f"{path} has no usable leaf"


def test_scoring_prefers_the_exact_leaf_over_a_partial_overlap():
    candidates = ["General", "General Aviation", "Art History"]
    scored = sorted(candidates,
                    key=lambda c: _score(_leaf_tokens("General"), c, leaf=True), reverse=True)
    assert scored[0] == "General"


def test_an_empty_target_never_matches_anything():
    assert _score(set(), "General", leaf=True) == 0.0
    assert _score(_leaf_tokens(""), "General", leaf=True) == 0.0


def test_every_category_on_a_book_we_are_about_to_upload_is_a_real_leaf():
    """A branch like "Art > Techniques" has no checkbox — targeting one wastes
    a slot and, when all three miss, cancels the whole upload."""
    import json

    root = Path(__file__).resolve().parent.parent
    tree = root / "data" / "kdp_category_tree.json"
    if not tree.exists():
        pytest.skip("category tree snapshot not available")
    leaves = set(json.loads(tree.read_text(encoding="utf-8")).get("leaves", []))
    if not leaves:
        pytest.skip("empty category tree snapshot")

    from kdp_freeze import APPROVED_UPLOADS

    for slug in APPROVED_UPLOADS:
        listing_file = Path("/root/kdp") / slug / "listing.json"
        if not listing_file.exists():
            continue
        categories = json.loads(listing_file.read_text(encoding="utf-8")).get("categories", [])
        assert categories, f"{slug} has no categories"
        for category in categories:
            assert category in leaves, f"{slug}: {category!r} is not a real KDP leaf"
