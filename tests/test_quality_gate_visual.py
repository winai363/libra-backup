"""Visual-niche gate: an illustrated guide cannot pass without real,
provenance-backed instructional images.

Amazon's "disappointing customer experience" rejection (acuarela, 11 Jul 2026)
is what a text-only book in a visual niche looks like from the outside.
"""

import json
import sys
import zipfile
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
    _epub(book_dir, names)


def _epub(book_dir, names, referenced=None, corrupt=None, cover=None):
    referenced = names if referenced is None else referenced
    manifest = ''.join(f'<item id="i{n}" href="images/{name}" media-type="image/png"'
                       + (' properties="cover-image"' if name == cover else '') + '/>'
                       for n, name in enumerate(names))
    body = ''.join(f'<img src="images/{name}" alt="step"/>' for name in referenced)
    with zipfile.ZipFile(book_dir / "ebook.epub", "w") as archive:
        archive.writestr("META-INF/container.xml", '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="book.opf"/></rootfiles></container>')
        archive.writestr("book.opf", '<package xmlns="http://www.idpf.org/2007/opf"><manifest>' + manifest + '<item id="text" href="body.xhtml" media-type="application/xhtml+xml"/></manifest><spine><itemref idref="text"/></spine></package>')
        archive.writestr("body.xhtml", '<html xmlns="http://www.w3.org/1999/xhtml"><body>' + body + '<p>' + 'content ' * 800 + '</p></body></html>')
        for name in names:
            if name == corrupt:
                archive.writestr("images/" + name, b"not an image")
            else:
                archive.write(book_dir / "images" / name, "images/" + name)


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


# ── AI-generated illustrations ───────────────────────────────────────────────
# A screenshot and a generated illustration need different proof. Demanding
# "device"/"os_version" from a painting is theatre; demanding the model and the
# prompt is the real audit trail — and KDP requires the AI disclosure anyway.

AI_FIELDS = {
    "source_kind": "ai_generated",
    "source": "gpt-image-1",
    "model": "gpt-image-1",
    "prompt": "watercolour wet-on-wet wash demonstration, three stages",
    "generated_at": "2026-08-22T10:00:00+07:00",
    "license": "generated-for-this-title",
    "contains_personal_data": False,
}


def _ai_provenance(book_dir: Path, names, **overrides) -> None:
    rows = []
    for name in names:
        row = {"file": f"images/{name}", "alt_text": f"Step {name}", **AI_FIELDS}
        row.update(overrides)
        rows.append(row)
    (book_dir / "image-provenance.json").write_text(json.dumps({"images": rows}))
    _epub(book_dir, names)


def test_ai_generated_images_pass_with_model_and_prompt(book):
    book_dir, slug, root = book
    _ai_provenance(book_dir, _images(book_dir))

    report = validate_book(slug, root=root, require_visuals=True)

    assert report.passed, report.errors


def test_ai_generated_row_without_model_or_prompt_is_rejected(book):
    book_dir, slug, root = book
    names = _images(book_dir)

    for dropped in ("model", "prompt", "generated_at"):
        rows = json.loads(json.dumps({"images": []}))
        _ai_provenance(book_dir, names)
        data = json.loads((book_dir / "image-provenance.json").read_text())
        for row in data["images"]:
            del row[dropped]
        (book_dir / "image-provenance.json").write_text(json.dumps(data))

        report = validate_book(slug, root=root, require_visuals=True)
        assert any(dropped in error for error in report.errors), dropped


def test_screenshot_row_still_needs_device_details(book):
    book_dir, slug, root = book
    names = _images(book_dir)
    _provenance(book_dir, names)
    data = json.loads((book_dir / "image-provenance.json").read_text())
    for row in data["images"]:
        del row["device"]
    (book_dir / "image-provenance.json").write_text(json.dumps(data))

    report = validate_book(slug, root=root, require_visuals=True)

    assert any("device" in error for error in report.errors)


def test_unknown_source_kind_is_refused(book):
    book_dir, slug, root = book
    _ai_provenance(book_dir, _images(book_dir), source_kind="vibes")

    report = validate_book(slug, root=root, require_visuals=True)

    assert any("source_kind" in error for error in report.errors)


def test_image_files_on_disk_do_not_prove_images_shipped_in_epub(book):
    book_dir, slug, root = book
    _provenance(book_dir, _images(book_dir))
    (book_dir / "ebook.epub").write_bytes(b"PK" + b"\x00" * 6000)
    report = validate_book(slug, root=root, require_visuals=True)
    assert any("EPUB instructional" in error for error in report.errors)


def test_unreferenced_images_in_epub_do_not_count(book):
    book_dir, slug, root = book
    names = _images(book_dir)
    _provenance(book_dir, names)
    _epub(book_dir, names, referenced=names[:2])
    report = validate_book(slug, root=root, require_visuals=True)
    assert report.metrics["epub_instructional_images"] == 2
    assert any("EPUB instructional" in error for error in report.errors)


def test_corrupt_embedded_copy_fails_even_when_original_is_valid(book):
    book_dir, slug, root = book
    names = _images(book_dir)
    _provenance(book_dir, names)
    _epub(book_dir, names, corrupt=names[0])
    report = validate_book(slug, root=root, require_visuals=True)
    assert any("EPUB instructional" in error for error in report.errors)


def test_cover_and_repeated_references_do_not_inflate_interior_count(book):
    from quality_gate import _epub_instructional_images
    book_dir, _, _ = book
    names = _images(book_dir)
    _epub(book_dir, names, referenced=[names[0]] * 12)
    assert _epub_instructional_images(book_dir)[1] == 1
    _epub(book_dir, names, cover=names[0])
    assert _epub_instructional_images(book_dir)[1] == 11
