"""Instructional illustrations: planned against real sections, then proven."""

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import illustrations

MANUSCRIPT = """## Préface

Bienvenue dans ce guide.

# Partie 1 : Les Bases

## Chapitre 1 : Le lavis humide sur humide

Texte du chapitre un.

## Chapitre 2 : Les dégradés

Texte du chapitre deux.

## Références

1. Source
"""


def _brief(index, heading):
    return {
        "heading": heading,
        "filename": f"step-{index:02}.png",
        "alt_text": f"Démonstration {index}",
        "prompt": f"watercolour demonstration {index}",
    }


def _fake_renderer(calls):
    def render(prompt, destination):
        calls.append((prompt, destination.name))
        Image.new("RGB", (64, 64), color="white").save(destination)
        return {"model": "fake-image-1"}
    return render


def test_briefs_must_reference_headings_that_exist():
    good = [_brief(0, "Chapitre 1 : Le lavis humide sur humide")]
    bad = [_brief(0, "Chapitre 9 : Ce chapitre n'existe pas")]

    illustrations.validate_briefs(good, MANUSCRIPT)
    with pytest.raises(illustrations.IllustrationError, match="heading"):
        illustrations.validate_briefs(bad, MANUSCRIPT)


def test_briefs_reject_duplicate_filenames_and_unsafe_names():
    with pytest.raises(illustrations.IllustrationError, match="duplicate"):
        illustrations.validate_briefs(
            [_brief(0, "Chapitre 1 : Le lavis humide sur humide"),
             _brief(0, "Chapitre 2 : Les dégradés")],
            MANUSCRIPT,
        )
    escaping = _brief(0, "Chapitre 1 : Le lavis humide sur humide")
    escaping["filename"] = "../evil.png"
    with pytest.raises(illustrations.IllustrationError, match="filename"):
        illustrations.validate_briefs([escaping], MANUSCRIPT)


def test_images_are_inserted_inside_their_own_section(tmp_path):
    briefs = [
        _brief(0, "Chapitre 1 : Le lavis humide sur humide"),
        _brief(1, "Chapitre 2 : Les dégradés"),
    ]

    updated = illustrations.insert_into_manuscript(MANUSCRIPT, briefs)

    chapter_one = updated.split("## Chapitre 2")[0]
    assert "![Démonstration 0](images/step-00.png)" in chapter_one
    assert "![Démonstration 1](images/step-01.png)" not in chapter_one
    # never before the heading it belongs to
    assert updated.index("## Chapitre 1") < updated.index("images/step-00.png")


def test_render_writes_files_and_provenance_rows(tmp_path):
    book = tmp_path / "book"
    book.mkdir()
    briefs = [_brief(0, "Chapitre 1 : Le lavis humide sur humide")]
    calls = []

    rows = illustrations.render_illustrations(
        book, briefs, renderer=_fake_renderer(calls), now="2026-08-22T10:00:00+07:00"
    )

    assert (book / "images" / "step-00.png").exists()
    assert calls[0][1] == "step-00.png"
    row = rows[0]
    assert row["file"] == "images/step-00.png"
    assert row["source_kind"] == "ai_generated"
    assert row["model"] == "fake-image-1"
    assert row["prompt"] == "watercolour demonstration 0"
    assert row["generated_at"] == "2026-08-22T10:00:00+07:00"
    assert row["contains_personal_data"] is False
    assert row["alt_text"] == "Démonstration 0"


def test_a_failed_image_aborts_instead_of_shipping_a_gap(tmp_path):
    """Silently dropping a failed image is how a visual book ends up text-only."""
    book = tmp_path / "book"
    book.mkdir()

    def broken(prompt, destination):
        raise RuntimeError("image API refused")

    with pytest.raises(illustrations.IllustrationError, match="image API refused"):
        illustrations.render_illustrations(
            book, [_brief(0, "Chapitre 1 : Le lavis humide sur humide")],
            renderer=broken, now="2026-08-22T10:00:00+07:00",
        )


def test_provenance_file_round_trips(tmp_path):
    book = tmp_path / "book"
    book.mkdir()
    rows = illustrations.render_illustrations(
        book, [_brief(0, "Chapitre 1 : Le lavis humide sur humide")],
        renderer=_fake_renderer([]), now="2026-08-22T10:00:00+07:00",
    )

    illustrations.write_provenance(book, rows)

    stored = json.loads((book / "image-provenance.json").read_text())
    assert stored["images"] == rows
    assert stored["disclosure"]["ai_generated_images"] is True


def test_prompts_forbid_text_in_the_image():
    prompt = illustrations.build_image_prompt(
        "Trois étapes d'un lavis humide sur humide",
        niche="aquarelle botanique pour débutants",
    )
    lowered = prompt.lower()
    assert "no text" in lowered and "no letters" in lowered
    assert "aquarelle botanique" in lowered
    # instructional, not decorative
    assert "step" in lowered or "demonstration" in lowered


def test_briefs_never_target_front_or_back_matter():
    brief = _brief(0, "Références")
    with pytest.raises(illustrations.IllustrationError, match="back matter"):
        illustrations.validate_briefs([brief], MANUSCRIPT)
