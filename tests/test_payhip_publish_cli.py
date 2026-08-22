"""The publish CLI refuses before it acts, and a dry run never opens a browser."""

import json
import os
import subprocess
import sys
from pathlib import Path

from PIL import Image

LIBRA = Path(__file__).resolve().parent.parent
SCRIPT = LIBRA / "scripts" / "payhip_publish.py"


def _book(root: Path, slug: str, *, enrolled=False) -> Path:
    book = root / slug
    book.mkdir(parents=True)
    listing = {
        "title": "Titre Test", "subtitle": "Sous-titre", "language": "French",
        "description": "desc " * 30, "ai_generated_images": True,
        "ai_content_disclosure": {"text": "ai_assisted", "images": "ai_generated"},
    }
    if enrolled:
        listing["kdp_select"] = {"status": "Enrolled"}
    (book / "listing.json").write_text(json.dumps(listing))
    (book / "ebook.epub").write_bytes(b"PK" + os.urandom(5000))
    (book / "x-paperback.pdf").write_bytes(b"%PDF" + os.urandom(5000))
    Image.new("RGB", (1600, 2560), "white").save(book / "cover.jpg")
    (book / "staging-manifest.json").write_text(json.dumps({"status": "staged_quality_passed"}))
    return book


def _run(tmp_path, *args):
    env = {**os.environ, "PYTHONPATH": str(LIBRA),
           "KDP_DIR": str(tmp_path / "kdp"), "KDP_STAGING_ROOT": str(tmp_path / "staging")}
    return subprocess.run([sys.executable, str(SCRIPT), *args], env=env,
                          capture_output=True, text=True)


def test_dry_run_builds_the_bundle_and_plans_without_a_browser(tmp_path):
    _book(tmp_path / "kdp", "titre-test")

    result = _run(tmp_path, "--slug", "titre-test", "--price-minor", "990", "--dry-run")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["mode"] == "dry_run"
    assert payload["price"] == "9.90 EUR"
    assert payload["external_calls"] == 0
    assert "click_save" in payload["browser_steps"]
    assert Path(payload["bundle"]).exists()


def test_kdp_select_enrolled_book_is_refused_before_anything_is_built(tmp_path):
    _book(tmp_path / "kdp", "exclusive-book", enrolled=True)

    result = _run(tmp_path, "--slug", "exclusive-book", "--price-minor", "990", "--dry-run")

    assert result.returncode == 2
    assert "KDP Select" in result.stderr


def test_execute_without_credentials_fails_closed(tmp_path, monkeypatch):
    _book(tmp_path / "kdp", "titre-test")
    env = {**os.environ, "PYTHONPATH": str(LIBRA), "KDP_DIR": str(tmp_path / "kdp"),
           "KDP_STAGING_ROOT": str(tmp_path / "staging")}
    env.pop("PAYHIP_EMAIL", None)
    env.pop("PAYHIP_PASSWORD", None)

    # Point the CLI at an empty .env so real server credentials never leak into the test.
    result = subprocess.run(
        [sys.executable, "-c",
         "import sys; sys.argv=['x','--slug','titre-test','--price-minor','990','--execute'];"
         "sys.path.insert(0, %r); sys.path.insert(0, %r);"
         "import payhip_publish, settings; payhip_publish.load_env_file = lambda p: {};"
         "raise SystemExit(payhip_publish.main())" % (str(LIBRA / "scripts"), str(LIBRA))],
        env=env, capture_output=True, text=True,
    )

    assert result.returncode != 0
    assert "credentials_missing" in (result.stderr + result.stdout)


def test_cli_never_imports_kdp_mutators():
    import ast

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    assert imported.isdisjoint({"kdp_upload", "kdp_finish_publish", "kdp_action_executor", "set_price"})
