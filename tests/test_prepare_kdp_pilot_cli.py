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


def test_visual_topic_illustrates_before_writing_files(monkeypatch, tmp_path):
    """The manuscript that becomes the EPUB must already contain the images."""
    import gpt_fallback_writer as writer
    from PIL import Image

    manuscript = (
        "## Préface\n\nBienvenue.\n\n"
        "# Partie 1\n\n## Chapitre 1 : Le lavis\n\nTexte un.\n\n"
        "## Chapitre 2 : Les dégradés\n\nTexte deux.\n\n"
        "## Références\n\n1. Source\n"
    )
    captured = {}

    def fake_plan(topic, content, count):
        assert count == 2
        return [
            {"heading": "Chapitre 1 : Le lavis", "filename": "step-00.png",
             "alt_text": "Lavis", "prompt": "p0"},
            {"heading": "Chapitre 2 : Les dégradés", "filename": "step-01.png",
             "alt_text": "Dégradé", "prompt": "p1"},
        ]

    def fake_render(prompt, destination):
        Image.new("RGB", (32, 32), color="white").save(destination)
        return {"model": "test-image-1"}

    def fake_create_files(topic, content, listing, market="", research="", *, output_root):
        captured["content"] = content
        captured["listing"] = listing
        book = Path(output_root) / topic["slug"]
        book.mkdir(parents=True, exist_ok=True)
        return book

    monkeypatch.setattr(writer, "plan_illustration_briefs", fake_plan)
    monkeypatch.setattr(writer, "openai_image_renderer", fake_render)
    monkeypatch.setattr(writer, "_validate_topic", lambda topic: topic)
    monkeypatch.setattr(writer, "_market_research_md", lambda topic, score: "research")
    monkeypatch.setattr(writer, "step1b_content_research", lambda topic: "sources")
    monkeypatch.setattr(writer, "step2_write_book", lambda topic, research: manuscript)
    monkeypatch.setattr(writer, "content_units", lambda content, lang: 12000)
    monkeypatch.setattr(writer, "continuation_threshold", lambda lang: 1)
    monkeypatch.setattr(writer, "abort_threshold", lambda lang: 1)
    monkeypatch.setattr(writer, "max_threshold", lambda lang: 99999)
    monkeypatch.setattr(writer, "step3_write_listing", lambda topic: {"title": topic["title"]})
    monkeypatch.setattr(writer, "step4_create_files", fake_create_files)
    monkeypatch.setattr(writer, "step5_generate_epub", lambda book_dir: True)

    topic = {
        "slug": "aquarelle-test", "title": "T", "subtitle": "S", "language": "French",
        "lang_code": "fr", "niche": "aquarelle", "visual_required": True,
        "minimum_instructional_images": 2,
    }
    book_dir = writer.write_book_from_topic(
        topic, output_root=tmp_path / "staging", preparation_only=True
    )

    assert "![Lavis](images/step-00.png)" in captured["content"]
    assert "![Dégradé](images/step-01.png)" in captured["content"]
    assert captured["listing"]["ai_generated_images"] is True
    assert captured["listing"]["publish_blocked"] == "total_kdp_freeze"
    assert (book_dir / "images" / "step-00.png").exists()
    assert (book_dir / "image-provenance.json").exists()


def test_cli_accepts_a_named_pilot_and_defaults_to_the_current_one():
    import prepare_kdp_pilot

    assert prepare_kdp_pilot.parse_args(["--dry-run"]).pilot == prepare_kdp_pilot.DEFAULT_PILOT
    args = prepare_kdp_pilot.parse_args(["--dry-run", "--pilot", "aquarelle-botanique-fr"])
    assert args.pilot == "aquarelle-botanique-fr"


def test_unknown_pilot_name_fails_loudly():
    result = _run("--dry-run", "--pilot", "no-such-pilot")
    assert result.returncode != 0
    assert "no-such-pilot" in (result.stderr + result.stdout)


def test_every_checked_in_pilot_spec_is_valid():
    from staging_pipeline import load_pilot_spec

    specs = sorted((LIBRA / "data" / "pilots").glob("*.json"))
    assert specs, "no pilot specs checked in"
    for spec_path in specs:
        spec, _ = load_pilot_spec(spec_path)
        assert spec["publish_blocked"] == "total_kdp_freeze"
        if spec.get("visual_required"):
            assert spec["minimum_instructional_images"] >= 12


def test_the_watercolour_pilot_records_why_it_was_chosen():
    """A product choice with no evidence trail is a guess wearing a spec file."""
    spec = json.loads((LIBRA / "data" / "pilots" / "aquarelle-botanique-fr.json").read_text())

    evidence = spec["evidence"]
    assert evidence["theme"] == "art_craft"
    assert evidence["supporting_titles"][0]["asin"] == "B0H4FMMP8P"
    assert evidence["confidence"] == "low"
    assert "duplicate" in evidence["duplicate_check"]


def test_editorial_adapter_writes_the_report_the_gate_reads(monkeypatch, tmp_path):
    """review_book returns a dict; the quality gate reads a file. Bridge them."""
    import prepare_kdp_pilot

    book = tmp_path / "aquarelle-botanique-debutants-fr"
    book.mkdir(parents=True)
    monkeypatch.setattr(prepare_kdp_pilot, "review_book",
                        lambda slug, root=None: {"passed": True, "scores": {"seo_quality": 8}})
    monkeypatch.setattr(prepare_kdp_pilot, "optimize_seo", lambda slug, root: {"seo_score_after": 9})

    deps = prepare_kdp_pilot.production_dependencies(tmp_path)
    result = deps.editorial("aquarelle-botanique-debutants-fr", tmp_path)

    assert result["passed"] is True
    stored = json.loads((book / "editorial-review.json").read_text())
    assert stored["passed"] is True


def test_seo_runs_before_the_editorial_review(monkeypatch, tmp_path):
    """SEO score was being judged before the optimiser had ever run."""
    import prepare_kdp_pilot

    book = tmp_path / "aquarelle-botanique-debutants-fr"
    book.mkdir(parents=True)
    order = []
    monkeypatch.setattr(prepare_kdp_pilot, "optimize_seo",
                        lambda slug, root: order.append("seo") or {})
    monkeypatch.setattr(prepare_kdp_pilot, "review_book",
                        lambda slug, root=None: order.append("editorial") or {"passed": True})

    deps = prepare_kdp_pilot.production_dependencies(tmp_path)
    deps.editorial("aquarelle-botanique-debutants-fr", tmp_path)

    assert order == ["seo", "editorial"]


def test_a_failing_seo_pass_never_blocks_the_editorial_review(monkeypatch, tmp_path):
    import prepare_kdp_pilot

    book = tmp_path / "aquarelle-botanique-debutants-fr"
    book.mkdir(parents=True)

    def broken(slug, root):
        raise RuntimeError("seo API down")

    monkeypatch.setattr(prepare_kdp_pilot, "optimize_seo", broken)
    monkeypatch.setattr(prepare_kdp_pilot, "review_book",
                        lambda slug, root=None: {"passed": True})

    deps = prepare_kdp_pilot.production_dependencies(tmp_path)

    assert deps.editorial("aquarelle-botanique-debutants-fr", tmp_path)["passed"] is True


def test_finalize_mode_reuses_the_staged_manuscript(monkeypatch, tmp_path):
    """Re-running the gates must not re-pay for writing the book."""
    import prepare_kdp_pilot

    book = tmp_path / "aquarelle-botanique-debutants-fr"
    book.mkdir(parents=True)
    (book / "listing.json").write_text(json.dumps({"title": "T"}))
    monkeypatch.setattr(prepare_kdp_pilot, "write_book_from_topic",
                        lambda *a, **k: pytest.fail("writer must not run in finalize mode"))
    monkeypatch.setattr(prepare_kdp_pilot, "build_paperback_pdf", lambda slug, root=None: book / "b.pdf")
    monkeypatch.setattr(prepare_kdp_pilot, "optimize_seo", lambda slug, root: {})
    monkeypatch.setattr(prepare_kdp_pilot, "review_book", lambda slug, root=None: {"passed": True})
    monkeypatch.setattr(prepare_kdp_pilot, "validate_book",
                        lambda slug, **kwargs: type("R", (), {"passed": True, "errors": []})())

    result = prepare_kdp_pilot.finalize_pilot(
        spec_path=LIBRA / "data" / "pilots" / "aquarelle-botanique-fr.json",
        staging_root=tmp_path,
    )

    assert result.status == "staged_quality_passed"
    assert json.loads(result.manifest_path.read_text())["status"] == "staged_quality_passed"
