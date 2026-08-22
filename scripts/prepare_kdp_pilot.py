#!/usr/bin/env python3
"""Prepare the fixed French senior-smartphone pilot locally — preparation only.

TOTAL KDP FREEZE: this CLI cannot publish, queue, approve, or contact KDP. A
successful `--execute` run writes only under the staging root and ends at
`staged_quality_passed` with `publish_blocked: total_kdp_freeze`.

    python3 scripts/prepare_kdp_pilot.py --dry-run
    python3 scripts/prepare_kdp_pilot.py --execute   # only when explicitly asked
"""

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRA_DIR))

from editorial_review import review_book  # noqa: E402
from gpt_fallback_writer import write_book_from_topic  # noqa: E402
from kdp_freeze import freeze_state  # noqa: E402
from pdf_builder import build_paperback_pdf  # noqa: E402
from quality_gate import validate_book  # noqa: E402
from seo_optimizer import optimize as optimize_seo  # noqa: E402
from staging_pipeline import (  # noqa: E402
    StageDependencies,
    StageResult,
    load_pilot_spec,
    prepare_pilot,
    write_manifest,
)

logger = logging.getLogger("prepare_kdp_pilot")

PILOTS_DIR = LIBRA_DIR / "data" / "pilots"
DEFAULT_PILOT = "aquarelle-botanique-fr"


def spec_path(name: str) -> Path:
    """Resolve a pilot name to its checked-in spec. Names only — no paths."""
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", name):
        raise SystemExit(f"invalid pilot name: {name}")
    path = PILOTS_DIR / f"{name}.json"
    if not path.is_file():
        available = ", ".join(sorted(p.stem for p in PILOTS_DIR.glob("*.json")))
        raise SystemExit(f"unknown pilot '{name}'; available: {available}")
    return path


def production_dependencies(staging_root: Path) -> StageDependencies:
    """Bind every real step to the staging root — never the live KDP tree."""
    staging_root = Path(staging_root)

    def generate(spec, book_dir):
        topic = {
            "slug": spec["slug"],
            "title": spec["title"],
            "subtitle": spec["subtitle"],
            "language": spec["language"],
            "lang_code": spec["lang_code"],
            "audience": spec["audience"],
            "niche": spec["niche"],
            "description_en": spec["description_en"],
            "required_sections": spec["required_sections"],
            "visual_required": bool(spec.get("visual_required")),
            "minimum_instructional_images": spec.get("minimum_instructional_images", 12),
            "prohibited_claims": spec.get("prohibited_claims", []),
        }
        return write_book_from_topic(
            topic, output_root=staging_root, preparation_only=True
        )

    def build_pdf(slug, root):
        return build_paperback_pdf(slug, root=Path(root))

    def editorial(slug, root):
        root = Path(root)
        # SEO first: the editorial board scores seo_quality, so running the
        # optimiser afterwards (as the legacy writer did) means that score is
        # always judged on un-optimised keywords.
        try:
            optimize_seo(slug, root=root)
        except Exception as exc:
            logger.warning("SEO optimisation skipped for %s: %s", slug, exc)

        review = review_book(slug, root=root)
        # The quality gate reads editorial-review.json from disk; without this
        # bridge a passing review still fails the gate as "missing".
        (root / slug / "editorial-review.json").write_text(
            json.dumps(review, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return review

    def validate(slug, root):
        report = validate_book(
            slug,
            require_pdf=True,
            check_urls=True,
            require_editorial=True,
            require_visuals=True,
            root=Path(root),
        )
        return {
            "passed": getattr(report, "passed", False),
            "errors": list(getattr(report, "errors", [])),
        }

    return StageDependencies(
        generate=generate, build_pdf=build_pdf, editorial=editorial, validate=validate
    )


def finalize_pilot(*, spec_path: Path, staging_root: Path) -> StageResult:
    """Re-run PDF, SEO, editorial and quality on an already-staged book.

    Writing a book costs real API money; a book that failed only on a gate
    should be re-gated, never rewritten.
    """
    spec, spec_bytes = load_pilot_spec(Path(spec_path))
    staging_root = Path(staging_root)
    book_dir = staging_root / spec["slug"]
    if not (book_dir / "listing.json").exists():
        raise SystemExit(f"nothing staged at {book_dir} — run --execute first")

    dependencies = production_dependencies(staging_root)
    dependencies.build_pdf(spec["slug"], staging_root)
    editorial = dependencies.editorial(spec["slug"], staging_root)
    quality = dependencies.validate(spec["slug"], staging_root)
    status = (
        "staged_quality_passed"
        if editorial.get("passed") and quality.get("passed")
        else "staged_quality_failed"
    )
    manifest_path = write_manifest(
        book_dir, spec, spec_bytes, status=status, editorial=editorial, quality=quality
    )
    return StageResult(status, spec["publish_blocked"], book_dir, manifest_path)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Prepare a frozen KDP pilot book locally (no publishing)"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="check the boundary only")
    mode.add_argument("--execute", action="store_true", help="run the staging pipeline")
    mode.add_argument("--finalize", action="store_true",
                      help="re-run the gates on an already-staged book (no rewriting)")
    parser.add_argument("--pilot", default=DEFAULT_PILOT,
                        help=f"pilot spec name in data/pilots (default: {DEFAULT_PILOT})")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    state = freeze_state()
    spec = spec_path(args.pilot)

    if args.dry_run:
        print(f"PASS: {state['code']} active; staging only; no writes or external calls")
        print(f"PASS: pilot spec {spec.name} present: {spec.is_file()}")
        return 0

    staging_root = Path(os.getenv("KDP_STAGING_ROOT", "/root/kdp-staging"))
    live_root = Path(os.getenv("KDP_DIR", "/root/kdp"))

    if args.finalize:
        result = finalize_pilot(spec_path=spec, staging_root=staging_root)
        print(f"status: {result.status}")
        print(f"publish_blocked: {result.publish_blocked}")
        print(result.manifest_path)
        return 0 if result.status == "staged_quality_passed" else 2

    result = prepare_pilot(
        spec_path=spec,
        staging_root=staging_root,
        live_root=live_root,
        queue_path=LIBRA_DIR / "queue.txt",
        dependencies=production_dependencies(staging_root),
    )
    print(f"status: {result.status}")
    print(f"publish_blocked: {result.publish_blocked}")
    print(result.manifest_path)
    return 0 if result.status == "staged_quality_passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
