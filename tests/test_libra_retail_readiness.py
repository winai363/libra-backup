import json
import sqlite3
import zipfile
from pathlib import Path

from libra_retail_readiness import audit_candidate, audit_portfolio, verdict_for


REQUIRED = {
    name: {"result": "pass", "evidence": f"verified {name}"}
    for name in (
        "live", "marketplace", "price", "landing", "promise_match",
        "language", "claims", "rights", "policy_eligibility",
    )
}


def make_book(kdp_root: Path, slug: str, *, asin: str = "B000TEST01") -> Path:
    book = kdp_root / slug
    book.mkdir(parents=True)
    (book / "listing.json").write_text(json.dumps({
        "title": "Test title",
        "subtitle": "Test subtitle",
        "asin": asin,
        "language": "English",
    }))
    (book / "editorial-review.json").write_text(json.dumps({
        "passed": True,
        "critical_issues": [],
    }))
    (book / "cover.jpg").write_bytes(b"cover")
    (book / "aplus").mkdir()
    (book / "aplus" / "content.json").write_text("{}")
    with zipfile.ZipFile(book / "ebook.epub", "w") as archive:
        archive.writestr("EPUB/cover.jpg", b"cover")
        archive.writestr("EPUB/text.xhtml", "<p>text</p>")
    return book


def seed_ledger(path: Path, asin: str) -> None:
    with sqlite3.connect(path) as connection:
        connection.executescript("""
            CREATE TABLE kdp_snapshots (
                id INTEGER PRIMARY KEY, observed_at TEXT NOT NULL, month TEXT NOT NULL,
                royalties_usd REAL NOT NULL, orders_all_types INTEGER NOT NULL,
                kenp INTEGER NOT NULL, raw_json TEXT NOT NULL
            );
            CREATE TABLE kdp_title_attribution (
                snapshot_id INTEGER NOT NULL, asin TEXT NOT NULL,
                royalties_usd REAL NOT NULL, orders_count INTEGER NOT NULL,
                kenp INTEGER NOT NULL
            );
        """)
        connection.execute(
            "INSERT INTO kdp_snapshots VALUES (1, '2026-11-19T09:15:00+07:00', '2026-11', 1, 0, 10, '{}')"
        )
        connection.execute(
            "INSERT INTO kdp_title_attribution VALUES (1, ?, 0.70, 0, 127)", (asin,)
        )


def evidence_for(slug: str, controls=None, asin="B000TEST01") -> dict:
    return {
        "observed_at": "2026-11-19T10:00:00+07:00",
        "candidates": {
            slug: {
                "asin": asin,
                "controls": controls if controls is not None else REQUIRED,
            }
        },
    }


def test_missing_required_evidence_is_unknown(tmp_path):
    slug = "book-a"
    make_book(tmp_path, slug)

    result = audit_candidate(slug, evidence_for(slug, {}), tmp_path, tmp_path / "missing.db")

    assert result["verdict"] == "UNKNOWN"
    assert set(result["blockers"]) == set(REQUIRED)


def test_visual_promise_without_matching_interior_is_hold(tmp_path):
    slug = "book-a"
    make_book(tmp_path, slug)
    controls = dict(REQUIRED)
    controls["promise_match"] = {
        "result": "fail",
        "evidence": "listing promises illustrated steps; EPUB has zero interior images",
    }

    result = audit_candidate(
        slug, evidence_for(slug, controls), tmp_path, tmp_path / "missing.db",
    )

    assert result["verdict"] == "HOLD"
    assert result["local_artifacts"]["epub_interior_image_count"] == 0
    assert result["blockers"] == ["promise_match"]


def test_pass_requires_all_controls_and_keeps_monthly_history_separate(tmp_path):
    slug = "book-a"
    asin = "B000TEST01"
    make_book(tmp_path, slug, asin=asin)
    ledger = tmp_path / "ledger.db"
    seed_ledger(ledger, asin)

    result = audit_candidate(slug, evidence_for(slug, asin=asin), tmp_path, ledger)

    assert result["verdict"] == "PASS"
    assert result["blockers"] == []
    assert result["monthly_history"] == [{
        "month": "2026-11",
        "observed_at": "2026-11-19T09:15:00+07:00",
        "royalties_usd": 0.7,
        "orders": 0,
        "kenp": 127,
    }]


def test_failed_control_takes_precedence_over_unknown():
    controls = dict(REQUIRED)
    controls["rights"] = {"result": "unknown", "evidence": "missing provenance"}
    controls["promise_match"] = {"result": "fail", "evidence": "unmet promise"}

    assert verdict_for(controls) == "HOLD"


def test_portfolio_keeps_requested_order(tmp_path):
    make_book(tmp_path, "first", asin="B000TEST01")
    make_book(tmp_path, "second", asin="B000TEST02")
    evidence = {
        "observed_at": "2026-11-19T10:00:00+07:00",
        "candidates": {
            "first": {"asin": "B000TEST01", "controls": REQUIRED},
            "second": {"asin": "B000TEST02", "controls": REQUIRED},
        },
    }

    report = audit_portfolio(
        evidence, tmp_path, tmp_path / "missing.db", candidates=["second", "first"],
    )

    assert [row["slug"] for row in report["candidates"]] == ["second", "first"]
    assert report["summary"] == {"PASS": 2, "HOLD": 0, "UNKNOWN": 0}
