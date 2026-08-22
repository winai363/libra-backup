"""The human-handoff path: a pack the human can upload, and proof before recording."""

import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

LIBRA = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRA))
sys.path.insert(0, str(LIBRA / "scripts"))


def _book(root: Path, slug: str) -> Path:
    book = root / slug
    book.mkdir(parents=True)
    (book / "listing.json").write_text(json.dumps({
        "title": "Titre Test Unique", "subtitle": "S", "language": "French",
        "description": "desc " * 20, "ai_generated_images": True,
        "ai_content_disclosure": {"images": "ai_generated"},
    }))
    (book / "ebook.epub").write_bytes(b"PK" + os.urandom(3000))
    (book / "x-paperback.pdf").write_bytes(b"%PDF" + os.urandom(3000))
    Image.new("RGB", (1600, 2560), "white").save(book / "cover.jpg")
    (book / "staging-manifest.json").write_text(json.dumps({"status": "staged_quality_passed"}))
    return book


def test_upload_pack_contains_everything_the_human_needs(tmp_path):
    _book(tmp_path / "kdp", "titre-test")
    env = {**os.environ, "PYTHONPATH": str(LIBRA), "KDP_DIR": str(tmp_path / "kdp"),
           "KDP_STAGING_ROOT": str(tmp_path / "staging"), "LIBRA_DOWNLOADS": str(tmp_path / "dl")}

    result = subprocess.run(
        [sys.executable, str(LIBRA / "scripts" / "payhip_upload_pack.py"),
         "--slug", "titre-test", "--price-minor", "990"],
        env=env, capture_output=True, text=True,
    )

    assert result.returncode == 0, result.stderr
    pack = tmp_path / "dl" / "payhip-titre-test"
    names = sorted(p.name for p in pack.iterdir())
    assert "CHECKLIST-TH.txt" in names and "cover.jpg" in names and "product-text.txt" in names
    assert any(n.endswith(".zip") for n in names)
    text = (pack / "product-text.txt").read_text(encoding="utf-8")
    assert "Titre Test Unique" in text and "9.90 EUR" in text
    checklist = (pack / "CHECKLIST-TH.txt").read_text(encoding="utf-8")
    assert "payhip_record_product.py --slug titre-test" in checklist


def test_record_product_refuses_a_page_that_does_not_show_the_title(tmp_path, monkeypatch):
    import payhip_record_product as recorder

    _book(tmp_path / "kdp", "titre-test")
    monkeypatch.setattr(recorder, "KDP_DIR", tmp_path / "kdp")
    monkeypatch.setattr(recorder, "LEDGER", tmp_path / "ledger.db")

    evidence = recorder.verify_public_page(
        "https://payhip.com/b/abc12", "Titre Test Unique", fetch=lambda u: "<html>Something else</html>"
    )
    assert evidence["title_found"] is False

    evidence = recorder.verify_public_page(
        "https://payhip.com/b/abc12", "Titre Test Unique",
        fetch=lambda u: "<html><h1>Titre Test Unique</h1></html>",
    )
    assert evidence["title_found"] is True


def test_record_product_rejects_non_payhip_urls():
    import pytest

    import payhip_record_product as recorder

    with pytest.raises(SystemExit):
        recorder.verify_public_page("https://evil.example/b/abc", "T", fetch=lambda u: "T")
    with pytest.raises(SystemExit):
        recorder.verify_public_page("http://payhip.com/b/abc", "T", fetch=lambda u: "T")


def test_captcha_is_reported_as_manual_required_not_worked_around():
    source = (LIBRA / "payhip_admin.py").read_text(encoding="utf-8")
    assert "captcha_manual_required" in source
    for forbidden in ("2captcha", "anticaptcha", "capsolver", "solve_captcha"):
        assert forbidden not in source.lower()
