import hashlib
import json

from tests.test_quality_gate import _make_book, _stub_epubcheck  # noqa: F401
from tests.test_quality_gate_visual import _epub


def test_catalogue_audit_records_defects_without_changing_inputs(tmp_path):
    from catalogue_quality import audit_catalogue

    book_dir, slug = _make_book(tmp_path)
    listing = json.loads((book_dir / "listing.json").read_text())
    listing.update(subtitle="An illustrated guide", live_status="LIVE")
    (book_dir / "listing.json").write_text(json.dumps(listing))
    (book_dir / "editorial-review.json").write_text('{"passed": true}')
    _epub(book_dir, [])
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in book_dir.rglob("*") if p.is_file()}
    report = audit_catalogue(tmp_path)
    row = report["books"][0]
    assert row["slug"] == slug
    assert row["local_status"] == "LIVE"
    assert row["structural_status"] == "needs_repair"
    assert row["semantic_review"] == "not_performed"
    assert row["publish_blocked"] == "total_kdp_freeze"
    assert row["epub"]["image_count"] == 0
    assert row["editorial"]["passed"] is False
    assert any("EPUB instructional" in error for error in row["errors"])
    assert row["sources"]["ebook.epub"] == before[str(book_dir / "ebook.epub")]
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in book_dir.rglob("*") if p.is_file()}
    assert after == before


def test_invalid_listing_is_reported_without_losing_other_titles(tmp_path):
    from catalogue_quality import audit_catalogue

    _make_book(tmp_path)
    broken = tmp_path / "broken-book"
    broken.mkdir()
    (broken / "listing.json").write_text("[]")
    report = audit_catalogue(tmp_path)
    assert len(report["books"]) == 2
    assert report["books"][0]["structural_status"] == "unreadable"
    assert report["books"][0]["local_status"] == "UNKNOWN"


def test_missing_catalogue_does_not_look_like_clean_catalogue(tmp_path):
    from catalogue_quality import audit_catalogue

    report = audit_catalogue(tmp_path / "missing")
    assert report["status"] == "unavailable"
    assert report["books"] == []


def test_malformed_fields_cannot_abort_catalogue(tmp_path):
    from catalogue_quality import audit_catalogue

    book_dir, _ = _make_book(tmp_path)
    listing = json.loads((book_dir / "listing.json").read_text())
    listing["categories"] = None
    (book_dir / "listing.json").write_text(json.dumps(listing))
    report = audit_catalogue(tmp_path)
    assert report["books"][0]["structural_status"] == "unreadable"
    assert report["books"][0]["errors"]
