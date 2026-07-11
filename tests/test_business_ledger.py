from pathlib import Path

from business_ledger import (
    direct_costs_for_slug,
    portfolio_financials,
    record_direct_cost,
    record_kdp_snapshot,
)


def test_same_observation_is_idempotent(tmp_path: Path):
    db = tmp_path / "ledger.db"
    snap = {
        "observed_at": "2026-07-11T09:15:09+07:00",
        "month": "2026-07",
        "overview": {"royalties_usd": 7.63, "orders_all_types": 252, "kenp": 361},
        "titles": [{"asin": "A", "royalties_usd": 6.84, "orders": 60, "kenp": 173}],
    }
    first = record_kdp_snapshot(db, snap)
    second = record_kdp_snapshot(db, snap)
    result = portfolio_financials(db, "2026-07")
    assert first == second
    assert result["verified_royalties_usd"] == 7.63
    assert result["attributed_royalties_usd"] == 6.84
    assert result["unattributed_royalties_usd"] == 0.79
    assert result["snapshot_count"] == 1


def test_profit_a_and_b_keep_unknown_overhead_incomplete(tmp_path):
    db = tmp_path / "ledger.db"
    record_kdp_snapshot(db, {
        "observed_at": "2026-07-11T09:15:09+07:00", "month": "2026-07",
        "overview": {"royalties_usd": 10.0, "orders_all_types": 4, "kenp": 20},
        "titles": [],
    })
    result = portfolio_financials(db, "2026-07")
    assert result["contribution_profit_usd"] == 10.0
    assert result["fully_loaded_net_profit_usd"] is None
    assert result["overhead_complete"] is False


def test_direct_costs_and_complete_overhead_are_deducted(tmp_path: Path):
    db = tmp_path / "ledger.db"
    record_kdp_snapshot(db, {
        "observed_at": "2026-07-11T09:15:09+07:00", "month": "2026-07",
        "overview": {"royalties_usd": 10.0, "orders_all_types": 4, "kenp": 20},
        "titles": [],
    })
    first = record_direct_cost(
        db,
        incurred_at="2026-07-05T12:00:00+07:00",
        slug="book-a",
        category="cover",
        amount_usd=1.25,
        source_key="cover:book-a:2026-07",
    )
    second = record_direct_cost(
        db,
        incurred_at="2026-07-05T12:00:00+07:00",
        slug="book-a",
        category="cover",
        amount_usd=1.25,
        source_key="cover:book-a:2026-07",
    )

    result = portfolio_financials(db, "2026-07", overhead={
        "newton_server_usd": 2.0,
        "ai_subscription_usd": 3.0,
        "other_usd": 0.5,
    })

    assert first == second
    assert result["direct_costs_usd"] == 1.25
    assert result["contribution_profit_usd"] == 8.75
    assert result["fully_loaded_net_profit_usd"] == 3.25
    assert result["overhead_complete"] is True


def test_direct_costs_for_slug_only_sums_attributed_book_costs(tmp_path: Path):
    db = tmp_path / "ledger.db"
    for slug, amount, key in [
        ("book-a", 1.25, "a:cover"),
        ("book-a", 0.75, "a:edit"),
        ("book-b", 9.0, "b:cover"),
        (None, 12.0, "overhead"),
    ]:
        record_direct_cost(
            db, incurred_at="2026-07-05T12:00:00+07:00", slug=slug,
            category="test", amount_usd=amount, source_key=key,
        )

    assert direct_costs_for_slug(db, "book-a") == 2.0


def test_cost_estimate_counts_as_cost_but_never_verified(tmp_path):
    import json

    from business_ledger import ingest_uploaded_title_costs, title_contribution

    books = tmp_path / "kdp"
    folder = books / "legacy-book"
    folder.mkdir(parents=True)
    (folder / "listing.json").write_text(json.dumps({"status": "uploaded"}))
    (folder / "cost-estimate.json").write_text(json.dumps({"total_usd": 0.5, "estimated": True}))
    db = tmp_path / "ledger.db"

    result = ingest_uploaded_title_costs(db, books, checked_at="2026-07-11T12:00:00+07:00")

    assert result["verified_titles"] == 0
    assert result["estimated_slugs"] == ["legacy-book"]
    assert result["missing_slugs"] == []
    assert result["complete"] is False

    contribution = title_contribution(db, "legacy-book", 2.0)
    assert contribution["direct_costs_usd"] == 0.5
    assert contribution["contribution_profit_usd"] == 1.5
    assert contribution["cost_complete"] is False
    assert contribution["positive_contribution_proven"] is False

    financials = portfolio_financials(db, "2026-07")
    assert financials["direct_costs_usd"] == 0.5
    assert financials["cost_complete"] is False
    assert financials["positive_contribution_proven"] is False


def test_cost_report_wins_over_estimate_and_upgrades_status(tmp_path):
    import json
    import sqlite3

    from business_ledger import ingest_uploaded_title_costs

    books = tmp_path / "kdp"
    folder = books / "legacy-book"
    folder.mkdir(parents=True)
    (folder / "listing.json").write_text(json.dumps({"status": "uploaded"}))
    (folder / "cost-estimate.json").write_text(json.dumps({"total_usd": 0.5}))
    db = tmp_path / "ledger.db"
    ingest_uploaded_title_costs(db, books, checked_at="2026-07-11T12:00:00+07:00")

    (folder / "cost-report.json").write_text(json.dumps({"total_usd": 0.3}))
    result = ingest_uploaded_title_costs(db, books, checked_at="2026-07-11T13:00:00+07:00")

    assert result["verified_titles"] == 1
    assert result["estimated_slugs"] == []
    assert result["complete"] is True
    with sqlite3.connect(db) as connection:
        status, source_key = connection.execute(
            "SELECT status, source_key FROM cost_inventory WHERE slug='legacy-book'"
        ).fetchone()
    assert status == "verified"
    assert source_key == "cost-report:legacy-book"
    financials = portfolio_financials(db, "2026-07")
    assert financials["direct_costs_usd"] == 0.3  # estimate superseded, not double-counted
    assert financials["cost_complete"] is True
