from pathlib import Path

from business_ledger import (
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
