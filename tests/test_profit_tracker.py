import json
from datetime import date

import profit_tracker
from business_ledger import record_kdp_snapshot


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_portfolio_flags_books_without_sales_data(tmp_path, monkeypatch):
    monkeypatch.setattr(profit_tracker, "KDP_DIR", tmp_path)
    write_json(
        tmp_path / "book-one" / "listing.json",
        {
            "title": "Book One",
            "status": "uploaded",
            "kdp_book_id": "A123",
            "uploaded_at": "2026-05-01",
        },
    )

    portfolio = profit_tracker.build_portfolio(today=date(2026, 6, 7))

    assert portfolio["book_count"] == 1
    assert portfolio["summary"]["books_with_data"] == 0
    assert portfolio["books"][0]["action"] == "needs_data"


def test_free_units_never_create_revenue_or_winner(tmp_path, monkeypatch):
    monkeypatch.setattr(profit_tracker, "KDP_DIR", tmp_path)
    write_json(
        tmp_path / "free-book" / "listing.json",
        {
            "title": "Free Book",
            "status": "uploaded",
            "uploaded_at": "2026-07-01",
        },
    )
    write_json(
        tmp_path / "free-book" / "feedback-history.json",
        [
            {
                "date": "2026-07-10",
                "units_7d": 17,
                "kenp_7d": 0,
                "revenue_usd": 0.0,
            }
        ],
    )

    book = profit_tracker.build_portfolio(today=date(2026, 7, 11))["books"][0]

    assert book["totals_30d"]["verified_revenue_usd"] == 0.0
    assert book["action"] != "winner"


def test_portfolio_uses_ledger_for_verified_profit_and_reconciliation(tmp_path, monkeypatch):
    kdp_dir = tmp_path / "kdp"
    ledger = tmp_path / "ledger.db"
    monkeypatch.setattr(profit_tracker, "KDP_DIR", kdp_dir)
    monkeypatch.setattr(profit_tracker, "LEDGER_FILE", ledger)
    record_kdp_snapshot(ledger, {
        "observed_at": "2026-07-11T09:15:09+07:00",
        "month": "2026-07",
        "overview": {"royalties_usd": 7.63, "orders_all_types": 252, "kenp": 361},
        "titles": [{"asin": "A", "royalties_usd": 6.84, "orders": 60, "kenp": 173}],
    })

    portfolio = profit_tracker.build_portfolio(today=date(2026, 7, 11))

    assert portfolio["verified_royalties_mtd_usd"] == 7.63
    assert portfolio["contribution_profit_usd"] == 7.63
    assert portfolio["fully_loaded_net_profit_usd"] is None
    assert portfolio["overhead_complete"] is False
    assert portfolio["reconciliation"] == {
        "attributed_royalties_usd": 6.84,
        "unattributed_royalties_usd": 0.79,
        "snapshot_count": 1,
    }
