"""Tests for quality_gate.py — deterministic checks only, no API calls."""
import json
import sys
import tempfile
import pytest
from pathlib import Path
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent.parent))
from quality_gate import validate_book, GateReport, MIN_WORDS, MIN_FICTION_WORDS

KDP_DIR = Path("/root/kdp")


@pytest.fixture(autouse=True)
def _stub_epubcheck(monkeypatch):
    """The synthetic fixture EPUB is a placeholder, not a real zip, so the
    external W3C epubcheck tool can't validate it and reports a fatal. These
    tests exercise validate_book's deterministic logic (word count, sections,
    references, URLs, cover), not the epubcheck integration — so stub it clean.
    Real books are still epubchecked by the production pipeline."""
    monkeypatch.setattr(
        "quality_gate._epubcheck",
        lambda book_dir: {"fatals": 0, "errors": 0, "warnings": 0},
    )


def _make_book(tmp_path, words=12000, sections=15, refs=10, fiction=False):
    """Create a minimal valid book for testing."""
    book_dir = tmp_path / "test-book"
    book_dir.mkdir()

    # Build manuscript
    body = ""
    for i in range(1, sections + 1):
        body += f"\n## Section {i}: Content Here\n\n"
        body += ("This is test content. " * 20) + "\n\n"

    # Pad to word count
    while len(body.split()) < words:
        body += "Additional content for padding purposes. " * 50 + "\n\n"

    # Add references
    refs_text = "\n## References\n\n"
    for i in range(1, refs + 1):
        refs_text += f"[{i}] Author (2025). *Title*. Publisher. https://example.com/ref{i}\n"

    (book_dir / "ebook.md").write_text(body + refs_text)

    # listing.json
    listing = {
        "title": "Test Book",
        "subtitle": "Test Subtitle",
        "language": "English",
        "keywords": ["keyword one", "keyword two", "keyword three",
                     "keyword four", "keyword five", "keyword six", "keyword seven"],
        "categories": ["Computers > AI", "Business > Technology"],
        "description": "x" * 150,
        "status": "uploaded",
    }
    (book_dir / "listing.json").write_text(json.dumps(listing))

    # metadata.yaml
    (book_dir / "metadata.yaml").write_text(
        f'---\ntitle: "Test Book"\nauthor: "Author"\nlang: en\ndate: "2025"\n---\n'
    )

    # Cover: valid 1600x2560 RGB JPEG required by quality_gate
    img = Image.new("RGB", (1600, 2560), color="white")
    img.save(str(book_dir / "cover.jpg"))

    # EPUB (minimal — just needs to be > 5000 bytes)
    (book_dir / "ebook.epub").write_bytes(b"PK" + b"\x00" * 6000)

    # Research files
    (book_dir / "market-research.md").write_text("# Market Research\n\nTest market research content.")
    (book_dir / "content-research.md").write_text("# Content Research\n\nTest content research.")

    return book_dir, "test-book"


def test_pass_nonfiction(tmp_path, monkeypatch):
    """A valid non-fiction book should pass all deterministic checks."""
    book_dir, slug = _make_book(tmp_path, words=13000, sections=15, refs=10)
    monkeypatch.setattr("quality_gate.KDP_DIR", tmp_path)
    # Disable URL check (no network in tests)
    monkeypatch.setattr("quality_gate._unreachable_urls", lambda urls: [])
    report = validate_book(slug)
    assert report.passed, f"Expected PASS, got errors: {report.errors}"


def test_fail_too_short(tmp_path, monkeypatch):
    """A book with < MIN_WORDS should fail."""
    book_dir, slug = _make_book(tmp_path, words=5000, sections=15, refs=10)
    monkeypatch.setattr("quality_gate.KDP_DIR", tmp_path)
    monkeypatch.setattr("quality_gate._unreachable_urls", lambda urls: [])
    report = validate_book(slug)
    assert not report.passed
    assert any("too short" in e.lower() for e in report.errors), f"Expected word-count error, got: {report.errors}"


def test_fail_too_few_sections(tmp_path, monkeypatch):
    """A book with < 12 sections should fail."""
    book_dir, slug = _make_book(tmp_path, words=12000, sections=5, refs=10)
    monkeypatch.setattr("quality_gate.KDP_DIR", tmp_path)
    monkeypatch.setattr("quality_gate._unreachable_urls", lambda urls: [])
    report = validate_book(slug)
    assert not report.passed
    assert any("section" in e.lower() for e in report.errors), f"Expected section error, got: {report.errors}"


def test_fail_keyword_too_long(tmp_path, monkeypatch):
    """A keyword > 50 chars should fail."""
    book_dir, slug = _make_book(tmp_path, words=12000, sections=15, refs=10)
    listing = json.loads((book_dir / "listing.json").read_text())
    listing["keywords"][0] = "a" * 51
    (book_dir / "listing.json").write_text(json.dumps(listing))
    monkeypatch.setattr("quality_gate.KDP_DIR", tmp_path)
    monkeypatch.setattr("quality_gate._unreachable_urls", lambda urls: [])
    report = validate_book(slug)
    assert not report.passed
    assert any("keyword" in e.lower() for e in report.errors)


def test_fail_too_few_keywords(tmp_path, monkeypatch):
    """Fewer than 7 keywords should fail."""
    book_dir, slug = _make_book(tmp_path, words=12000, sections=15, refs=10)
    listing = json.loads((book_dir / "listing.json").read_text())
    listing["keywords"] = listing["keywords"][:5]
    (book_dir / "listing.json").write_text(json.dumps(listing))
    monkeypatch.setattr("quality_gate.KDP_DIR", tmp_path)
    monkeypatch.setattr("quality_gate._unreachable_urls", lambda urls: [])
    report = validate_book(slug)
    assert not report.passed


def test_fiction_threshold(tmp_path, monkeypatch):
    """Fiction books require MIN_FICTION_WORDS = 20000."""
    book_dir, slug = _make_book(tmp_path, words=15000, sections=15, refs=10, fiction=True)
    listing = json.loads((book_dir / "listing.json").read_text())
    listing["categories"] = ["Fiction > Romance", "Fiction > Historical"]
    (book_dir / "listing.json").write_text(json.dumps(listing))
    monkeypatch.setattr("quality_gate.KDP_DIR", tmp_path)
    monkeypatch.setattr("quality_gate._unreachable_urls", lambda urls: [])
    report = validate_book(slug)
    assert not report.passed
    assert any("too short" in e.lower() for e in report.errors)


def test_all_ready_books_pass_quality_gate(monkeypatch):
    """All books with status=ready must pass the deterministic quality gate."""
    # Disable URL check for speed
    monkeypatch.setattr("quality_gate._unreachable_urls", lambda urls: [])
    failures = []
    for d in KDP_DIR.iterdir():
        if not d.is_dir():
            continue
        listing_path = d / "listing.json"
        if not listing_path.exists():
            continue
        listing = json.loads(listing_path.read_text())
        if listing.get("status") != "ready":
            continue
        report = validate_book(d.name)
        if not report.passed:
            failures.append(f"{d.name}: {report.errors[:2]}")
    assert not failures, f"Ready books failing quality gate:\n" + "\n".join(failures)


def test_url_network_error_is_warning(tmp_path, monkeypatch):
    """A transient connection failure must not block an otherwise valid book."""
    book_dir, slug = _make_book(tmp_path, words=13000, sections=15, refs=10)
    monkeypatch.setattr("quality_gate.KDP_DIR", tmp_path)
    monkeypatch.setattr(
        "quality_gate._check_urls",
        lambda urls: ([], ["https://example.com/ref1 (ConnectTimeout)"]),
    )

    report = validate_book(slug, check_urls=True)

    assert report.passed
    assert any("temporary network errors" in warning for warning in report.warnings)


def test_real_http_error_blocks_book(tmp_path, monkeypatch):
    """A confirmed HTTP error must still fail the gate."""
    book_dir, slug = _make_book(tmp_path, words=13000, sections=15, refs=10)
    monkeypatch.setattr("quality_gate.KDP_DIR", tmp_path)
    monkeypatch.setattr(
        "quality_gate._check_urls",
        lambda urls: (["https://example.com/ref1 (404)"], []),
    )

    report = validate_book(slug, check_urls=True)

    assert not report.passed
    assert any("Unreachable reference URLs" in error for error in report.errors)
