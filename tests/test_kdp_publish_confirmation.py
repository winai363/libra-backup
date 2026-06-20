"""Regression tests for fail-closed KDP update confirmation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import json

import kdp_upload
from kdp_upload import is_kdp_publish_confirmed, is_title_creation_limit


def test_bookshelf_url_confirms_publish():
    assert is_kdp_publish_confirmed(
        "https://kdp.amazon.com/en_US/bookshelf",
        "",
    )


def test_explicit_submission_message_confirms_publish():
    assert is_kdp_publish_confirmed(
        "https://kdp.amazon.com/en_US/title-setup/kindle/BOOK/pricing",
        "Your changes have been submitted for review.",
    )


def test_content_save_success_does_not_confirm_publish():
    assert not is_kdp_publish_confirmed(
        "https://kdp.amazon.com/en_US/title-setup/kindle/BOOK/content",
        "Your manuscript was uploaded successfully.",
    )


def test_pricing_page_without_submission_does_not_confirm_publish():
    assert not is_kdp_publish_confirmed(
        "https://kdp.amazon.com/en_US/title-setup/kindle/BOOK/pricing",
        "Save and Publish your Kindle eBook",
    )


def test_title_creation_limit_detection():
    assert is_title_creation_limit(
        "Title creation limit exceeded. The number of books that can be "
        "submitted for publishing has been exceeded by this account."
    )
    assert not is_title_creation_limit("Your book details were saved")


def test_update_preflight_is_local_and_read_only(tmp_path, monkeypatch):
    slug = "test-book"
    book_dir = tmp_path / slug
    book_dir.mkdir()
    (book_dir / "listing.json").write_text(json.dumps({"kdp_book_id": "BOOK123"}))
    (book_dir / "ebook.epub").write_bytes(b"PK" + b"\x00" * 6000)
    session = tmp_path / "session.json"
    session.write_text("{}")

    monkeypatch.setattr(kdp_upload, "KDP_DIR", tmp_path)
    monkeypatch.setattr(kdp_upload, "SESSION_FILE", session)
    monkeypatch.setattr(kdp_upload, "require_quality_gate", lambda value: value == slug)

    assert kdp_upload.preflight_update(slug)


def test_update_preflight_rejects_missing_book_id(tmp_path, monkeypatch):
    slug = "test-book"
    book_dir = tmp_path / slug
    book_dir.mkdir()
    (book_dir / "listing.json").write_text("{}")
    (book_dir / "ebook.epub").write_bytes(b"PK" + b"\x00" * 6000)
    session = tmp_path / "session.json"
    session.write_text("{}")

    monkeypatch.setattr(kdp_upload, "KDP_DIR", tmp_path)
    monkeypatch.setattr(kdp_upload, "SESSION_FILE", session)
    monkeypatch.setattr(kdp_upload, "require_quality_gate", lambda value: True)

    assert not kdp_upload.preflight_update(slug)
