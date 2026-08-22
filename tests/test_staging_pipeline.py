"""Offline coverage for the frozen KDP staging state machine.

Nothing here may touch /root/kdp, queue.txt, Playwright, or the network.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from staging_pipeline import (
    StageDependencies,
    StagingBoundaryError,
    prepare_pilot,
)

SPEC_PATH = Path(__file__).resolve().parent.parent / "data" / "pilots" / "senior-smartphone-fr.json"


def _generate(spec, book_dir):
    book_dir.mkdir(parents=True)
    (book_dir / "listing.json").write_text(json.dumps({"title": spec["title"]}))
    return book_dir


def _deps(**overrides):
    base = dict(
        generate=_generate,
        build_pdf=lambda slug, root: root / slug / "paperback.pdf",
        editorial=lambda slug, root: {"passed": True},
        validate=lambda slug, root: {"passed": True, "errors": []},
    )
    base.update(overrides)
    return StageDependencies(**base)


def _world(tmp_path):
    live = tmp_path / "kdp"
    staging = tmp_path / "staging"
    queue = tmp_path / "queue.txt"
    live.mkdir()
    (live / "existing-book").mkdir()
    queue.write_text("existing-book\n")
    return live, staging, queue


def _run(tmp_path, **overrides):
    live, staging, queue = _world(tmp_path)
    result = prepare_pilot(
        spec_path=SPEC_PATH,
        staging_root=staging,
        live_root=live,
        queue_path=queue,
        dependencies=_deps(**overrides),
    )
    return result, live, staging, queue


def test_prepare_pilot_isolated_and_never_queues(tmp_path):
    live, staging, queue = _world(tmp_path)
    before = queue.read_bytes()
    live_before = sorted(p.name for p in live.iterdir())

    result = prepare_pilot(
        spec_path=SPEC_PATH,
        staging_root=staging,
        live_root=live,
        queue_path=queue,
        dependencies=_deps(),
    )

    assert result.status == "staged_quality_passed"
    assert result.publish_blocked == "total_kdp_freeze"
    assert queue.read_bytes() == before
    assert sorted(p.name for p in live.iterdir()) == live_before
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["pilot_id"] == "senior-smartphone-fr-v1"
    assert manifest["publish_blocked"] == "total_kdp_freeze"
    assert manifest["slug"] == "smartphone-sans-stress-seniors-fr"
    assert len(manifest["spec_sha256"]) == 64


def test_output_slug_equals_checked_in_slug(tmp_path):
    result, _, staging, _ = _run(tmp_path)
    assert result.book_dir == staging / "smartphone-sans-stress-seniors-fr"


def test_invalid_spec_fails_before_creating_any_directory(tmp_path):
    live, staging, queue = _world(tmp_path)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"slug": "x", "publish_blocked": "none"}))

    with pytest.raises(ValueError):
        prepare_pilot(
            spec_path=bad,
            staging_root=staging,
            live_root=live,
            queue_path=queue,
            dependencies=_deps(),
        )
    assert not staging.exists()


def test_existing_staging_directory_aborts_without_overwrite(tmp_path):
    live, staging, queue = _world(tmp_path)
    book = staging / "smartphone-sans-stress-seniors-fr"
    book.mkdir(parents=True)
    (book / "keep.txt").write_text("previous run")

    with pytest.raises(StagingBoundaryError):
        prepare_pilot(
            spec_path=SPEC_PATH,
            staging_root=staging,
            live_root=live,
            queue_path=queue,
            dependencies=_deps(),
        )
    assert (book / "keep.txt").read_text() == "previous run"


@pytest.mark.parametrize(
    "overrides",
    [
        {"editorial": lambda slug, root: {"passed": False, "errors": ["tone"]}},
        {"validate": lambda slug, root: {"passed": False, "errors": ["no images"]}},
    ],
)
def test_failed_gate_produces_failure_manifest_and_no_queue_change(tmp_path, overrides):
    result, live, _, queue = _run(tmp_path, **overrides)

    assert result.status == "staged_quality_failed"
    assert queue.read_bytes() == b"existing-book\n"
    manifest = json.loads(result.manifest_path.read_text())
    assert manifest["status"] == "staged_quality_failed"
    assert manifest["publish_blocked"] == "total_kdp_freeze"


def test_generator_escaping_staging_root_raises(tmp_path):
    live, staging, queue = _world(tmp_path)

    def escaping(spec, book_dir):
        outside = live / spec["slug"]
        outside.mkdir(parents=True)
        return outside

    with pytest.raises(StagingBoundaryError):
        prepare_pilot(
            spec_path=SPEC_PATH,
            staging_root=staging,
            live_root=live,
            queue_path=queue,
            dependencies=_deps(generate=escaping),
        )


def test_dependency_failure_preserves_live_and_queue_snapshot(tmp_path):
    live, staging, queue = _world(tmp_path)
    before = queue.read_bytes()

    def boom(slug, root):
        raise RuntimeError("pdf build crashed")

    with pytest.raises(RuntimeError):
        prepare_pilot(
            spec_path=SPEC_PATH,
            staging_root=staging,
            live_root=live,
            queue_path=queue,
            dependencies=_deps(build_pdf=boom),
        )
    assert queue.read_bytes() == before
    assert sorted(p.name for p in live.iterdir()) == ["existing-book"]
    manifest = json.loads(
        (staging / "smartphone-sans-stress-seniors-fr" / "staging-manifest.json").read_text()
    )
    assert manifest["status"] == "staged_pipeline_failed"
    assert "pdf build crashed" in manifest["quality"]["errors"][0]


def test_live_mutation_during_staging_is_detected(tmp_path):
    live, staging, queue = _world(tmp_path)

    def sneaky(slug, root):
        queue.write_text("existing-book\nsmuggled\n")
        return {"passed": True, "errors": []}

    with pytest.raises(StagingBoundaryError):
        prepare_pilot(
            spec_path=SPEC_PATH,
            staging_root=staging,
            live_root=live,
            queue_path=queue,
            dependencies=_deps(validate=sneaky),
        )


def test_module_never_imports_publishing_machinery():
    source = (Path(__file__).resolve().parent.parent / "staging_pipeline.py").read_text()
    for forbidden in ("kdp_upload", "kdp_finish_publish", "playwright", "requests", "httpx"):
        assert forbidden not in source
