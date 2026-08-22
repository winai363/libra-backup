"""Turning a finished book into a Payhip product — with the guards that matter.

The single most dangerous mistake here is selling an EPUB that is enrolled in
KDP Select: that breaks Amazon's exclusivity term and puts the whole account at
risk again. Every other guard is about not shipping a half-made product.
"""

import json
import sys
import zipfile
from pathlib import Path

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from payhip_catalog import (
    CatalogError,
    build_bundle,
    build_product_spec,
    guard_book_for_payhip,
    record_product,
)


def _book(tmp_path, *, select_status=None, ai_images=True, manifest_status="staged_quality_passed"):
    book = tmp_path / "aquarelle-botanique-debutants-fr"
    book.mkdir()
    listing = {
        "title": "Aquarelle Botanique pour Débutants",
        "subtitle": "Peindre fleurs et feuillages étape par étape",
        "language": "French",
        "description": "Un guide pas à pas pour débutants. " * 10,
        "keywords": ["aquarelle", "botanique"],
        "ai_generated_images": ai_images,
        "ai_content_disclosure": {"text": "ai_assisted", "images": "ai_generated"},
    }
    if select_status:
        listing["kdp_select"] = {"status": select_status}
    (book / "listing.json").write_text(json.dumps(listing, ensure_ascii=False))
    import os
    (book / "ebook.epub").write_bytes(b"PK" + os.urandom(6000))
    (book / "Aquarelle-paperback.pdf").write_bytes(b"%PDF-1.4" + os.urandom(6000))
    Image.new("RGB", (1600, 2560), "white").save(book / "cover.jpg")
    (book / "staging-manifest.json").write_text(json.dumps({"status": manifest_status}))
    return book


def test_guard_passes_a_finished_non_exclusive_book(tmp_path):
    book = _book(tmp_path)
    guard_book_for_payhip(book)  # no raise


def test_guard_refuses_a_kdp_select_enrolled_ebook(tmp_path):
    book = _book(tmp_path, select_status="Enrolled")
    with pytest.raises(CatalogError, match="KDP Select"):
        guard_book_for_payhip(book)


def test_guard_refuses_a_book_that_did_not_pass_quality(tmp_path):
    book = _book(tmp_path, manifest_status="staged_quality_failed")
    with pytest.raises(CatalogError, match="quality"):
        guard_book_for_payhip(book)


def test_guard_refuses_missing_files(tmp_path):
    book = _book(tmp_path)
    (book / "ebook.epub").unlink()
    with pytest.raises(CatalogError, match="ebook.epub"):
        guard_book_for_payhip(book)


def test_bundle_contains_pdf_epub_and_a_readme_with_the_ai_disclosure(tmp_path):
    book = _book(tmp_path)
    out = tmp_path / "out"

    bundle = build_bundle(book, out)

    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
        readme = archive.read("LISEZ-MOI.txt").decode("utf-8")
    assert any(n.endswith(".pdf") for n in names)
    assert any(n.endswith(".epub") for n in names)
    assert "illustrations" in readme.lower() and "intelligence artificielle" in readme.lower()
    assert bundle.stat().st_size > 10_000


def test_bundle_never_includes_working_files(tmp_path):
    book = _book(tmp_path)
    (book / "cost-report.json").write_text("{}")
    (book / "image-provenance.json").write_text("{}")
    (book / "ebook.md").write_text("# draft")

    with zipfile.ZipFile(build_bundle(book, tmp_path / "out")) as archive:
        names = archive.namelist()
    for forbidden in ("cost-report.json", "image-provenance.json", "ebook.md", "listing.json"):
        assert forbidden not in names


def test_product_spec_is_priced_in_the_market_currency_as_integer_minor(tmp_path):
    book = _book(tmp_path)

    spec = build_product_spec(book, price_minor=1290, currency="EUR")

    assert spec["title"] == "Aquarelle Botanique pour Débutants"
    assert spec["price_minor"] == 1290
    assert spec["currency"] == "EUR"
    assert spec["price_display"] == "12.90"
    assert spec["slug"] == "aquarelle-botanique-debutants-fr"
    assert "intelligence artificielle" in spec["description"].lower()


def test_product_spec_refuses_a_non_integer_or_zero_price(tmp_path):
    book = _book(tmp_path)
    with pytest.raises(CatalogError):
        build_product_spec(book, price_minor=12.9, currency="EUR")
    with pytest.raises(CatalogError):
        build_product_spec(book, price_minor=0, currency="EUR")
    with pytest.raises(CatalogError):
        build_product_spec(book, price_minor=1290, currency="eur")


def test_record_product_is_idempotent_and_keeps_provider_id(tmp_path):
    db = tmp_path / "ledger.db"
    book = _book(tmp_path)
    spec = build_product_spec(book, price_minor=1290, currency="EUR")

    record_product(db, spec, provider_product_id="payhip-abc", status="live")
    record_product(db, spec, provider_product_id="payhip-abc", status="live")

    import sqlite3
    with sqlite3.connect(db) as connection:
        rows = connection.execute("SELECT slug, provider_product_id, status, price_minor FROM commerce_products").fetchall()
    assert rows == [("aquarelle-botanique-debutants-fr", "payhip-abc", "live", 1290)]


def test_moving_a_product_to_another_provider_updates_the_provider(tmp_path):
    """Switching storefronts must not leave the old provider name behind —
    the ledger would then attribute Lemon Squeezy sales to Payhip."""
    import sqlite3

    db = tmp_path / "ledger.db"
    book = _book(tmp_path)
    spec = build_product_spec(book, price_minor=1290, currency="EUR")
    record_product(db, spec, provider_product_id="https://payhip.com/b/OLD", status="live")

    moved = build_product_spec(book, price_minor=49000, currency="THB")
    record_product(db, moved, provider="lemonsqueezy",
                   provider_product_id="https://wkbui.lemonsqueezy.com/checkout/buy/NEW",
                   status="live")

    with sqlite3.connect(db) as connection:
        rows = connection.execute(
            "SELECT provider, provider_product_id, currency, price_minor FROM commerce_products"
        ).fetchall()
    assert rows == [("lemonsqueezy", "https://wkbui.lemonsqueezy.com/checkout/buy/NEW", "THB", 49000)]
