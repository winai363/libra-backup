#!/usr/bin/env python3
"""Prepare the fixed French senior-smartphone pilot locally — preparation only.

TOTAL KDP FREEZE: this CLI cannot publish, queue, approve, or contact KDP. A
successful `--execute` run writes only under the staging root and ends at
`staged_quality_passed` with `publish_blocked: total_kdp_freeze`.

    python3 scripts/prepare_kdp_pilot.py --dry-run
    python3 scripts/prepare_kdp_pilot.py --execute   # only when explicitly asked
"""

import argparse
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
from staging_pipeline import StageDependencies, prepare_pilot  # noqa: E402

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
        return review_book(slug, root=Path(root))

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


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Prepare a frozen KDP pilot book locally (no publishing)"
    )
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="check the boundary only")
    mode.add_argument("--execute", action="store_true", help="run the staging pipeline")
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
