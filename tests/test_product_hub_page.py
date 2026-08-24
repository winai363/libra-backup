"""The product page: one tracked Payhip link, attribution stated as unknown."""

import json
import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as libra_app
from payhip_catalog import record_product

ENV = {
    "LIBRA_COMMERCE_MODE": "test",
    "STRIPE_WEBHOOK_SECRET_TEST": "whsec_test_fixture",
    "STRIPE_EXPECTED_ACCOUNT_TEST": "acct_test_fixture",
    "PAYHIP_WEBHOOK_TOKEN_TEST": "p" * 48,
    "PAYHIP_ALLOWED_HOSTS": "payhip.com,www.payhip.com",
    "PAYHIP_PRODUCT_IDS_TEST": "kit-fr-test",
}


@pytest.fixture
def world(tmp_path, monkeypatch):
    ledger = tmp_path / "ledger.db"
    kdp = tmp_path / "kdp"
    book = kdp / "aquarelle-botanique-debutants-fr"
    book.mkdir(parents=True)
    (book / "listing.json").write_text(json.dumps({
        "title": "Aquarelle Botanique pour Débutants",
        "subtitle": "Peindre fleurs et feuillages",
        "description": "Un guide pas à pas.",
        "language": "French",
    }, ensure_ascii=False))
    monkeypatch.setenv("LIBRA_GROWTH_TRACKING_SECRET", "test-secret")
    monkeypatch.setattr(libra_app, "PROFIT_LEDGER_FILE", ledger)
    monkeypatch.setattr(libra_app, "KDP_DIR", kdp)
    monkeypatch.setattr(libra_app, "ENV", {**libra_app.ENV, **ENV})
    record_product(ledger, {
        "slug": "aquarelle-botanique-debutants-fr", "currency": "EUR", "price_minor": 1290,
    }, provider_product_id="https://payhip.com/b/abc12", status="live")
    return ledger


def test_product_page_renders_one_tracked_payhip_cta(world):
    client = TestClient(libra_app.app)

    response = client.get("/growth/products/aquarelle-botanique-debutants-fr")

    assert response.status_code == 200
    body = response.text
    assert "Aquarelle Botanique pour Débutants" in body
    assert body.count("/growth/out/") == 1
    assert "12.90" in body and "EUR" in body
    assert "payhip.com/b/abc12" not in body  # destination stays behind the tracked link


def test_clicking_the_cta_redirects_to_payhip_and_records_a_payhip_click(world):
    client = TestClient(libra_app.app)
    page = client.get("/growth/products/aquarelle-botanique-debutants-fr").text
    start = page.index('href="/growth/out/') + len('href="')
    cta = page[start:page.index('"', start)]

    outbound = client.get(cta, follow_redirects=False)

    assert outbound.status_code == 307
    assert outbound.headers["location"] == "https://payhip.com/b/abc12"
    with sqlite3.connect(world) as connection:
        row = connection.execute(
            "SELECT event_kind, payload_json FROM hub_events ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row[0] == "payhip_outbound"
    payload = json.loads(row[1])
    assert len(payload["click_id"]) >= 32
    assert payload["attribution_status"] == "unknown"


def test_unknown_or_unlisted_product_is_404(world):
    client = TestClient(libra_app.app)
    assert client.get("/growth/products/not-a-product").status_code == 404


def test_product_page_needs_no_login(world):
    client = TestClient(libra_app.app)
    assert client.get("/growth/products/aquarelle-botanique-debutants-fr").status_code == 200


def _write_sample(kdp_dir: Path) -> bytes:
    pdf = b"%PDF-1.4 fake sample"
    (kdp_dir / "aquarelle-botanique-debutants-fr" / "sample.pdf").write_bytes(pdf)
    return pdf


def test_sample_pdf_is_public_when_the_book_has_one(world):
    pdf = _write_sample(libra_app.KDP_DIR)
    client = TestClient(libra_app.app)

    response = client.get("/growth/products/aquarelle-botanique-debutants-fr/sample.pdf")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content == pdf


def test_missing_sample_file_is_404(world):
    client = TestClient(libra_app.app)
    assert client.get(
        "/growth/products/aquarelle-botanique-debutants-fr/sample.pdf"
    ).status_code == 404


def test_sample_of_an_unlisted_product_is_404(world):
    # A book directory that is not a live product must not be readable through
    # the public sample route.
    other = libra_app.KDP_DIR / "not-a-product"
    other.mkdir()
    (other / "sample.pdf").write_bytes(b"%PDF-1.4 private")
    client = TestClient(libra_app.app)

    assert client.get("/growth/products/not-a-product/sample.pdf").status_code == 404


def test_product_page_links_the_sample_only_when_it_exists(world):
    client = TestClient(libra_app.app)
    assert "sample.pdf" not in client.get(
        "/growth/products/aquarelle-botanique-debutants-fr"
    ).text

    _write_sample(libra_app.KDP_DIR)

    page = client.get("/growth/products/aquarelle-botanique-debutants-fr").text
    assert "/growth/products/aquarelle-botanique-debutants-fr/sample.pdf" in page
