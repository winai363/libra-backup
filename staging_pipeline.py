"""Frozen KDP staging — preparation only.

Prepares a checked-in pilot book inside an isolated staging root. It can never
append the publish queue, mark a title ready/uploaded/live, start a browser, or
contact KDP: a successful run ends at ``staged_quality_passed`` with
``publish_blocked = total_kdp_freeze``.
"""

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Callable

from kdp_freeze import FREEZE_CODE

REQUIRED_SPEC_FIELDS = {
    "pilot_id",
    "slug",
    "title",
    "language",
    "lang_code",
    "visual_required",
    "publish_blocked",
}


class StagingBoundaryError(RuntimeError):
    """Raised when staging would touch live state or escape its root."""


@dataclass(frozen=True)
class StageDependencies:
    generate: Callable[[dict, Path], Path]
    build_pdf: Callable[[str, Path], Path]
    editorial: Callable[[str, Path], dict]
    validate: Callable[[str, Path], dict]


@dataclass(frozen=True)
class StageResult:
    status: str
    publish_blocked: str
    book_dir: Path
    manifest_path: Path


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _snapshot(root: Path) -> list:
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def load_pilot_spec(spec_path: Path) -> tuple[dict, bytes]:
    spec_bytes = spec_path.read_bytes()
    spec = json.loads(spec_bytes)
    if REQUIRED_SPEC_FIELDS - spec.keys() or spec["publish_blocked"] != FREEZE_CODE:
        raise ValueError("invalid frozen-stage pilot specification")
    return spec, spec_bytes


def prepare_pilot(
    *,
    spec_path: Path,
    staging_root: Path,
    live_root: Path,
    queue_path: Path,
    dependencies: StageDependencies,
) -> StageResult:
    spec, spec_bytes = load_pilot_spec(spec_path)

    book_dir = staging_root / spec["slug"]
    if not _inside(book_dir, staging_root):
        raise StagingBoundaryError("staging destination escapes staging root")
    if book_dir.exists():
        raise StagingBoundaryError(f"staging destination already exists: {book_dir}")

    queue_before = queue_path.read_bytes() if queue_path.exists() else None
    live_before = _snapshot(live_root) if live_root.exists() else None

    def assert_live_untouched() -> None:
        queue_after = queue_path.read_bytes() if queue_path.exists() else None
        live_after = _snapshot(live_root) if live_root.exists() else None
        if queue_after != queue_before or live_after != live_before:
            raise StagingBoundaryError("live KDP state changed during staging")

    try:
        produced = dependencies.generate(spec, book_dir)
        if not _inside(Path(produced), staging_root):
            raise StagingBoundaryError("generator escaped staging root")
        dependencies.build_pdf(spec["slug"], staging_root)
        editorial = dependencies.editorial(spec["slug"], staging_root)
        quality = dependencies.validate(spec["slug"], staging_root)
    except Exception as exc:
        # A crashed stage still leaves an auditable record — never a silent
        # half-staged directory that could be mistaken for a finished book.
        _write_manifest(
            book_dir,
            spec,
            spec_bytes,
            status="staged_pipeline_failed",
            editorial={},
            quality={"passed": False, "errors": [f"{type(exc).__name__}: {exc}"]},
        )
        raise
    finally:
        assert_live_untouched()

    passed = bool(editorial.get("passed")) and bool(quality.get("passed"))
    status = "staged_quality_passed" if passed else "staged_quality_failed"
    manifest_path = _write_manifest(
        book_dir, spec, spec_bytes, status=status, editorial=editorial, quality=quality
    )
    assert_live_untouched()
    return StageResult(status, FREEZE_CODE, book_dir, manifest_path)


def _write_manifest(
    book_dir: Path, spec: dict, spec_bytes: bytes, *, status: str, editorial: dict, quality: dict
) -> Path | None:
    if not book_dir.exists():
        return None
    manifest = {
        "schema_version": 1,
        "pilot_id": spec["pilot_id"],
        "slug": spec["slug"],
        "status": status,
        "publish_blocked": FREEZE_CODE,
        "spec_sha256": hashlib.sha256(spec_bytes).hexdigest(),
        "editorial": editorial,
        "quality": quality,
    }
    manifest_path = book_dir / "staging-manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest_path
