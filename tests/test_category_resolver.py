"""A wrong Amazon category is worse than a missing one.

Real defect this pins down (22 Aug 2026): a watercolour book resolved to
"Arts & Photography > Art > Techniques > Basketry" because Basketry, Beadwork,
Quillwork and Composition all scored identically — the leaf word itself was
never checked for relevance, so the resolver picked whichever came first.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from category_resolver import resolve_paths

ARTS_LEAVES = [
    "Arts & Photography > Art > Techniques > Basketry",
    "Arts & Photography > Art > Techniques > Beadwork",
    "Arts & Photography > Art > Techniques > Quillwork",
    "Arts & Photography > Art > Techniques > Composition",
    "Arts & Photography > Art > Painting > General",
    "Arts & Photography > Art > General",
]


def test_an_ambiguous_tie_keeps_the_proposed_path_instead_of_guessing():
    proposed = [
        "Arts & Photography > Art > Painting",
        "Arts & Photography > Art > Watercolor",
        "Arts & Photography > Art > Techniques",
    ]

    resolved = resolve_paths(proposed, ARTS_LEAVES)

    assert "Basketry" not in " ".join(resolved)
    assert "Beadwork" not in " ".join(resolved)
    assert "Arts & Photography > Art > Techniques" in resolved


def test_a_leaf_that_shares_the_subject_word_is_still_chosen():
    proposed = ["Arts & Photography > Art > Painting"]

    resolved = resolve_paths(proposed, ARTS_LEAVES)

    assert resolved == ["Arts & Photography > Art > Painting > General"]


def test_unique_best_match_is_unaffected_by_the_tie_rule():
    leaves = [
        "Health, Fitness & Dieting > Mental Health > Anxiety Disorders",
        "Arts & Photography > Art > Painting > General",
    ]

    resolved = resolve_paths(
        ["Health, Fitness & Dieting > Mental Health > Anxiety"], leaves
    )

    assert resolved == ["Health, Fitness & Dieting > Mental Health > Anxiety Disorders"]


def test_empty_tree_returns_the_proposed_paths_unchanged():
    proposed = ["Arts & Photography > Art > Painting"]
    assert resolve_paths(proposed, []) == proposed
