"""Deterministic, read-only retail-readiness audit for Libra KDP titles."""
from __future__ import annotations

import json
import sqlite3
import zipfile
from pathlib import Path


REQUIRED_CONTROLS = (
    "live",
    "marketplace",
    "price",
    "landing",
    "promise_match",
    "language",
    "claims",
    "rights",
    "policy_eligibility",
)

DEFAULT_CANDIDATES = (
    "adhd-adults-workbook-es",
    "beginner-watercolor-spanish",
    "ai-productivity-workflows-professionals",
    "senior-smartphone-french",
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp"}


def _load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _epub_images(path: Path) -> tuple[int | None, int | None]:
    try:
        with zipfile.ZipFile(path) as archive:
            images = [
                name for name in archive.namelist()
                if Path(name).suffix.lower() in IMAGE_SUFFIXES
            ]
    except (OSError, zipfile.BadZipFile):
        return None, None
    interior = [name for name in images if "cover" not in Path(name).name.lower()]
    return len(images), len(interior)


def _monthly_history(ledger: Path, asin: str | None) -> list[dict]:
    if not asin or not ledger.exists():
        return []
    try:
        with sqlite3.connect(ledger) as connection:
            rows = connection.execute(
                """
                SELECT s.month, s.observed_at, a.royalties_usd, a.orders_count, a.kenp
                FROM kdp_title_attribution AS a
                JOIN kdp_snapshots AS s ON s.id = a.snapshot_id
                WHERE a.asin = ?
                ORDER BY s.observed_at DESC
                """,
                (asin,),
            ).fetchall()
    except sqlite3.Error:
        return []
    latest = {}
    for month, observed_at, royalties, orders, kenp in rows:
        if month in latest:
            continue
        latest[month] = {
            "month": month,
            "observed_at": observed_at,
            "royalties_usd": float(royalties),
            "orders": int(orders),
            "kenp": int(kenp),
        }
    return [latest[month] for month in sorted(latest)]


def _normalize_controls(raw: object) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    controls = {}
    for name in REQUIRED_CONTROLS:
        row = raw.get(name)
        if not isinstance(row, dict) or row.get("result") not in {"pass", "fail", "unknown"}:
            controls[name] = {
                "result": "unknown",
                "evidence": "required evidence missing or malformed",
            }
        else:
            controls[name] = dict(row)
    return controls


def verdict_for(controls: dict) -> str:
    if any((controls.get(name) or {}).get("result") == "fail" for name in REQUIRED_CONTROLS):
        return "HOLD"
    if any((controls.get(name) or {}).get("result") != "pass" for name in REQUIRED_CONTROLS):
        return "UNKNOWN"
    return "PASS"


def audit_candidate(slug: str, evidence: dict, kdp_root: Path, ledger: Path) -> dict:
    candidate = (evidence.get("candidates") or {}).get(slug)
    candidate = candidate if isinstance(candidate, dict) else {}
    book = kdp_root / slug
    listing = _load_json(book / "listing.json")
    editorial = _load_json(book / "editorial-review.json")
    total_images, interior_images = _epub_images(book / "ebook.epub")
    controls = _normalize_controls(candidate.get("controls"))
    verdict = verdict_for(controls)
    blockers = [
        name for name in REQUIRED_CONTROLS
        if controls[name]["result"] != "pass"
    ]
    asin = candidate.get("asin") or listing.get("asin")
    return {
        "slug": slug,
        "asin": asin,
        "verdict": verdict,
        "blockers": blockers,
        "controls": controls,
        "local_artifacts": {
            "book_directory_present": book.is_dir(),
            "listing_present": (book / "listing.json").is_file(),
            "epub_present": (book / "ebook.epub").is_file(),
            "paperback_present": any(book.glob("*paperback*.pdf")),
            "cover_present": (book / "cover.jpg").is_file(),
            "aplus_present": (book / "aplus" / "content.json").is_file(),
            "epub_image_count": total_images,
            "epub_interior_image_count": interior_images,
            "editorial_passed": editorial.get("passed"),
            "editorial_critical_issues": editorial.get("critical_issues"),
        },
        "listing": {
            "title": listing.get("title"),
            "subtitle": listing.get("subtitle"),
            "language": listing.get("language"),
        },
        "retail_observation": {
            key: candidate.get(key)
            for key in ("marketplace", "price", "currency", "landing_url", "review_evidence")
            if key in candidate
        },
        "monthly_history": _monthly_history(ledger, asin),
        "selection_basis": candidate.get("selection_basis"),
    }


def audit_portfolio(
    evidence: dict,
    kdp_root: Path,
    ledger: Path,
    *,
    candidates=DEFAULT_CANDIDATES,
) -> dict:
    rows = [audit_candidate(slug, evidence, kdp_root, ledger) for slug in candidates]
    summary = {name: sum(row["verdict"] == name for row in rows) for name in ("PASS", "HOLD", "UNKNOWN")}
    return {
        "schema": "libra-retail-readiness/1",
        "observed_at": evidence.get("observed_at"),
        "mode": "read_only",
        "summary": summary,
        "candidates": rows,
    }
