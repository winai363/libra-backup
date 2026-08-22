"""Visual-niche gate: an illustrated guide cannot pass without real,
provenance-backed instructional images.

Amazon's "disappointing customer experience" rejection (acuarela, 11 Jul 2026)
is what a text-only book in a visual niche looks like from the outside.
"""

import json
import sys
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from quality_gate import validate_book
from tests.test_quality_gate import _make_book, _stub_epubcheck  # noqa: F401 (fixture)

PROVENANCE_FIELDS = {
    "source_kind": "screenshot",
    "source": "test device capture",
    "captured_at": "2026-08-22T09:00:00+07:00",
    "device": "Pixel 6a",
    "os_version": "Android 14",
    "app_version": "WhatsApp 2.26.1",
    "license": "own-capture",
    "contains_personal_data": False,
}


def _images(book_dir: Path, count: int = 12) -> list:
    images = book_dir / "images"
    images.mkdir(exist_ok=True)
    names = []
    for index in range(count):
        name = f"step-{index:02}.png"
        Image.new("RGB", (40, 40), color="white").save(images / name)
        names.append(name)
    return names


def _provenance(book_dir: Path, names, **overrides) -> None:
    rows = []
    for name in names:
        row = {"file": f"images/{name}", "alt_text": f"Step {name}", **PROVENANCE_FIELDS}
        row.update(overrides)
        rows.append(row)
    (book_dir / "image-provenance.json").write_text(json.dumps({"images": rows}))


@pytest.fixture
def book(tmp_path, monkeypatch):
    book_dir, slug = _make_book(tmp_path, words=13000, sections=15, refs=10)
    monkeypatch.setattr("quality_gate._unreachable_urls", lambda urls: [])
    return book_dir, slug, tmp_path


def test_visual_pilot_fails_without_instructional_images(book):
    book_dir, slug, root = book
    report = validate_book(slug, root=root, require_visuals=True)
    assert any("instructional images" in error for error in report.errors)


def test_visual_pilot_requires_provenance_for_every_image(book):
    book_dir, slug, root = book
    _images(book_dir)
    (book_dir / "image-provenance.json").write_text(json.dumps({"images": []}))
    report = validate_book(slug, root=root, require_visuals=True)
    assert any("provenance" in error for error in report.errors)


def test_visual_pilot_passes_with_complete_provenance(book):
    book_dir, slug, root = book
    _provenance(book_dir, _images(book_dir))
    report = validate_book(slug, root=root, require_visuals=True)
    assert report.passed, report.errors
    assert report.metrics["instructional_images"] == 12


def test_personal_data_and_missing_alt_text_are_rejected(book):
    book_dir, slug, root = book
    names = _images(book_dir)
    _provenance(book_dir, names, contains_personal_data=True)
    report = validate_book(slug, root=root, require_visuals=True)
    assert any("personal data" in error for error in report.errors)

    _provenance(book_dir, names, alt_text="")
    report = validate_book(slug, root=root, require_visuals=True)
    assert any("alt text" in error for error in report.errors)


def test_file_set_must_match_provenance_exactly(book):
    book_dir, slug, root = book
    names = _images(book_dir)
    _provenance(book_dir, names[:-1])  # one image left undocumented
    report = validate_book(slug, root=root, require_visuals=True)
    assert any("provenance" in error for error in report.errors)


def test_provenance_row_outside_images_dir_is_rejected(book):
    book_dir, slug, root = book
    names = _images(book_dir)
    _provenance(book_dir, names)
    data = json.loads((book_dir / "image-provenance.json").read_text())
    data["images"][0]["file"] = "../../etc/passwd"
    (book_dir / "image-provenance.json").write_text(json.dumps(data))
    report = validate_book(slug, root=root, require_visuals=True)
    assert any("images/" in error for error in report.errors)


def test_corrupt_image_is_rejected(book):
    book_dir, slug, root = book
    names = _images(book_dir)
    (book_dir / "images" / names[0]).write_bytes(b"not a png")
    _provenance(book_dir, names)
    report = validate_book(slug, root=root, require_visuals=True)
    assert any("unreadable" in error for error in report.errors)


def test_visual_checks_are_off_by_default(book):
    book_dir, slug, root = book
    report = validate_book(slug, root=root)
    assert not any("instructional images" in error for error in report.errors)
