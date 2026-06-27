#!/usr/bin/env python3
"""Find KDP listings whose category paths are not English taxonomy.

KDP's category picker is English-only. A path like
"Business & Money > Management & Leadership" can be selected; localized paths
like "Negócios e Economia > Contabilidade" cannot. This scanner checks every
segment in every category path, not only the top-level segment.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

KDP = Path("/root/kdp")

LOCALIZED_MARKERS = {
    # German
    "bucher", "karriere", "zeitmanagement", "gesundheit", "korper", "eltern",
    "familie", "erziehung", "kochen", "essen", "geniessen", "getranke",
    "liebesromane", "historisch",
    # Spanish / Portuguese
    "negocios", "negocios y dinero", "dinero", "economia", "contabilidade",
    "contabilidad", "computacao", "tecnologia", "inteligencia artificial",
    "autonomos", "empresas", "emprendimiento", "impuestos",
    # Italian
    "business e finanza", "settore immobiliare", "informatica e internet",
    "intelligenza artificiale", "famiglia e relazioni", "maternita",
    "salute mentale", "guide pratiche", "sviluppo personale",
    # French / Dutch
    "entreprise et bourse", "developpement personnel", "bien-etre",
    "gezondheid", "geest", "lichaam", "zelfhulp", "gezonde levensstijl",
}

LOCALIZED_CHARS = re.compile(r"[À-ÿ]")


def normalize(value: str) -> str:
    value = value.lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9À-ÿ]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def suspicious_segment(segment: str) -> str | None:
    raw = segment.strip()
    norm = normalize(raw)
    if not norm:
        return None
    if LOCALIZED_CHARS.search(raw):
        return "accented/localized characters"
    for marker in LOCALIZED_MARKERS:
        if marker in norm:
            return f"localized marker: {marker}"
    return None


def _queued_slugs() -> set[str]:
    """Slugs waiting in the KDP upload queue — these are about to be submitted and
    must be screened even though they have no live_status yet."""
    qf = Path("/root/libra/queue.txt")
    if not qf.exists():
        return set()
    return {ln.strip() for ln in qf.read_text(encoding="utf-8").splitlines() if ln.strip()}


def scan(include_review: bool = False, include_unpublished: bool = False) -> list[dict]:
    findings: list[dict] = []
    allowed_status = {"LIVE"}
    if include_review:
        allowed_status.add("IN_REVIEW")
    if include_unpublished:
        allowed_status.add("UNPUBLISHED")
    queued = _queued_slugs()
    for listing_file in sorted(KDP.glob("*/listing.json")):
        try:
            listing = json.loads(listing_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        live_status = listing.get("live_status")
        # Always screen books queued for upload (no live_status yet) — that's where
        # localized categories jam the pipeline before a book ever goes live.
        if live_status not in allowed_status and listing_file.parent.name not in queued:
            continue
        categories = listing.get("categories") or []
        bad = []
        for path in categories:
            for segment in str(path).split(">"):
                reason = suspicious_segment(segment)
                if reason:
                    bad.append({"path": path, "segment": segment.strip(), "reason": reason})
        if bad:
            findings.append({
                "slug": listing_file.parent.name,
                "title": listing.get("title", ""),
                "asin": listing.get("asin", ""),
                "live_status": live_status,
                "issues": bad,
            })
    return findings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-review", action="store_true")
    parser.add_argument("--include-unpublished", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--fail-on-findings", action="store_true")
    args = parser.parse_args()
    findings = scan(
        include_review=args.include_review,
        include_unpublished=args.include_unpublished,
    )
    if args.json:
        print(json.dumps(findings, indent=2, ensure_ascii=False))
    else:
        print(f"Category language scan: {len(findings)} listing(s) need English taxonomy")
        for finding in findings:
            print(f"\n{finding['slug']} [{finding['live_status']}] {finding['asin']}")
            for issue in finding["issues"]:
                print(f"  - {issue['segment']} ({issue['reason']})")
                print(f"    {issue['path']}")
    return 1 if findings and args.fail_on_findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
