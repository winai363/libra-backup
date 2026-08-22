"""Boundary tests for the preparation-only KDP pilot CLI.

The CLI must never publish, queue, or reach KDP — and its dry run must not
write anything at all.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

LIBRA = Path(__file__).resolve().parent.parent
SCRIPT = LIBRA / "scripts" / "prepare_kdp_pilot.py"

sys.path.insert(0, str(LIBRA))
sys.path.insert(0, str(LIBRA / "scripts"))


def _run(*args, **env_overrides):
    env = {**os.environ, "PYTHONPATH": str(LIBRA), **env_overrides}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True
    )


def test_dry_run_is_offline_and_write_free(tmp_path):
    result = _run(
        "--dry-run",
        KDP_STAGING_ROOT=str(tmp_path / "staging"),
        KDP_DIR=str(tmp_path / "live"),
    )

    assert result.returncode == 0, result.stderr
    assert "total_kdp_freeze" in result.stdout
    assert not (tmp_path / "staging").exists()
    assert not (tmp_path / "live").exists()


def test_cli_has_no_publish_or_queue_option():
    result = _run("--help")

    assert result.returncode == 0
    for forbidden in ("--publish", "--queue", "--force", "--upload"):
        assert forbidden not in result.stdout


def test_cli_requires_an_explicit_mode():
    assert _run().returncode != 0


def test_production_dependencies_are_bound_to_the_staging_root(monkeypatch, tmp_path):
    """Every adapter must be handed the staging root, never the live KDP dir."""
    import prepare_kdp_pilot

    staging = tmp_path / "staging"
    seen = {}

    def fake_write(topic, *, output_root, preparation_only):
        seen["writer"] = (Path(output_root), preparation_only)
        book = Path(output_root) / topic["slug"]
        book.mkdir(parents=True)
        return book

    def fake_pdf(slug, root=None, **kwargs):
        seen["pdf"] = Path(root)
        return Path(root) / slug / "book.pdf"

    def fake_review(slug, root=None):
        seen["editorial"] = Path(root)
        return {"passed": True}

    def fake_validate(slug, **kwargs):
        seen["validate"] = kwargs
        return {"passed": True, "errors": []}

    monkeypatch.setattr(prepare_kdp_pilot, "write_book_from_topic", fake_write)
    monkeypatch.setattr(prepare_kdp_pilot, "build_paperback_pdf", fake_pdf)
    monkeypatch.setattr(prepare_kdp_pilot, "review_book", fake_review)
    monkeypatch.setattr(prepare_kdp_pilot, "validate_book", fake_validate)

    deps = prepare_kdp_pilot.production_dependencies(staging)
    spec = json.loads((LIBRA / "data" / "pilots" / "senior-smartphone-fr.json").read_text())
    deps.generate(spec, staging / spec["slug"])
    deps.build_pdf(spec["slug"], staging)
    deps.editorial(spec["slug"], staging)
    deps.validate(spec["slug"], staging)

    assert seen["writer"] == (staging, True)
    assert seen["pdf"] == staging
    assert seen["editorial"] == staging
    assert seen["validate"] == {
        "require_pdf": True,
        "check_urls": True,
        "require_editorial": True,
        "require_visuals": True,
        "root": staging,
    }


def test_cli_never_imports_publishing_machinery():
    source = SCRIPT.read_text(encoding="utf-8")
    for forbidden in ("kdp_upload", "kdp_finish_publish", "playwright"):
        assert forbidden not in source
    # queue.txt may appear only as the inert snapshot path handed to
    # prepare_pilot — never opened or written here.
    assert source.count("queue.txt") == 1
    assert 'queue_path=LIBRA_DIR / "queue.txt"' in source


def test_writer_refuses_live_output_root_in_preparation_mode(tmp_path):
    import gpt_fallback_writer
    from kdp_freeze import KDPFrozenError

    with pytest.raises(ValueError):
        gpt_fallback_writer.write_book_from_topic(
            {"slug": "x"}, output_root=tmp_path, preparation_only=False
        )
    with pytest.raises(KDPFrozenError):
        gpt_fallback_writer.write_book_from_topic(
            {"slug": "x"}, output_root=gpt_fallback_writer.KDP_DIR, preparation_only=True
        )
