"""Read-only catalogue repair inventory. Never writes book files or publishes."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from editorial_review import editorial_source_hashes, validate_editorial_report
from quality_gate import (
    KDP_DIR, _epub_visual_evidence, _is_fiction, _promises_illustrations,
    validate_book,
)


def audit_catalogue(root: Path = KDP_DIR) -> dict:
    books = []
    for path in sorted(root.glob("*/listing.json")):
        book_dir = path.parent
        row = {"slug": book_dir.name, "source_dir": str(book_dir),
               "sources": {}, "local_status": "UNKNOWN",
               "publish_blocked": "total_kdp_freeze",
               "semantic_review": "not_performed"}
        try:
            row["sources"] = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                              for p in book_dir.iterdir()
                              if p.is_file() and p.suffix in {
                                  ".json", ".md", ".epub", ".yaml", ".jpg", ".pdf"}}
            listing = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(listing, dict):
                raise ValueError("listing must be an object")
        except (OSError, ValueError) as exc:
            row.update(structural_status="unreadable", errors=[str(exc)])
            books.append(row)
            continue
        row["local_status"] = str(listing.get("live_status") or "UNKNOWN").upper()
        row["title"] = listing.get("title")
        try:
            gate = validate_book(book_dir.name, root=root, require_editorial=True)
        except Exception as exc:
            # A malformed book or failed local validator must not hide other titles.
            row.update(structural_status="unreadable",
                       errors=[f"Validation failed: {type(exc).__name__}: {exc}"])
            books.append(row)
            continue
        epub = _epub_visual_evidence(book_dir)
        editorial_path = book_dir / "editorial-review.json"
        try:
            editorial = json.loads(editorial_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            editorial = None
        try:
            expected_sources = editorial_source_hashes(book_dir)
        except OSError:
            expected_sources = {}
        evidence = validate_editorial_report(
            editorial, fiction=_is_fiction(listing), expected_sources=expected_sources)
        errors = list(dict.fromkeys(gate.errors + epub["errors"]))
        row.update(
            structural_status="needs_repair" if errors else "structural_checks_passed",
            errors=errors, warnings=gate.warnings, metrics=gate.metrics,
            illustration_promise_detected=_promises_illustrations(listing),
            epub=epub, editorial=evidence,
        )
        books.append(row)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "available" if books else "unavailable",
        "status_source": "local_files_not_amazon_or_delivered_copy_verification",
        "summary": dict(Counter(row["structural_status"] for row in books)),
        "local_status_counts": dict(Counter(row["local_status"] for row in books)),
        "limitations": [
            "No semantic, language, copyright, source truth or reader-task review performed.",
            "Literal illustration and numbered demonstration detection is not exhaustive.",
            "Valid source URLs are syntax checks, not proof the source supports a claim.",
            "No manuscripts changed. Internal checks do not authorize publication.",
        ],
        "books": books,
    }


def markdown_report(report: dict) -> str:
    lines = ["# Libra Catalogue Repair Inventory", "", report["generated_at"], "",
             "Local evidence only. KDP publishing remains frozen.", "",
             "## Summary", "", json.dumps(report["summary"], sort_keys=True), "",
             *["- " + item for item in report["limitations"]], ""]
    for row in report["books"]:
        lines.extend(["## " + row["slug"], "",
                      f"Local status: {row['local_status']}; checks: {row['structural_status']}",
                      "", "Source: " + row["source_dir"], ""])
        lines.extend("- " + error for error in row["errors"])
        if "epub" in row:
            lines.extend(["", f"Used interior images: {row['epub']['image_count']}"])
            for demo in row["epub"]["demonstrations"]:
                lines.append(f"- {demo['heading']}: {demo['image_count']} images ({demo['page']})")
        lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=KDP_DIR)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source = args.root.resolve()
    output = args.output_dir.resolve()
    if output == source or source in output.parents:
        parser.error("Output must be outside the source catalogue.")
    report = audit_catalogue(source)
    output.mkdir(parents=True, exist_ok=True)
    (output / "libra-catalogue-quality.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (output / "libra-catalogue-quality.md").write_text(markdown_report(report), encoding="utf-8")
    print(json.dumps({"status": report["status"], "summary": report["summary"],
                      "books": len(report["books"]), "output_dir": str(output)}))
    return 0 if report["status"] == "available" else 1


if __name__ == "__main__":
    raise SystemExit(main())
