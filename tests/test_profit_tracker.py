import json
from datetime import date

import profit_tracker


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


def test_portfolio_identifies_winner_and_estimates_revenue(tmp_path, monkeypatch):
    monkeypatch.setattr(profit_tracker, "KDP_DIR", tmp_path)
    write_json(
        tmp_path / "winner-book" / "listing.json",
        {
            "title": "Winner Book",
            "status": "uploaded",
            "kdp_book_id": "A123",
            "uploaded_at": "2026-06-01",
            "ebook_price": "2.99",
        },
    )
    write_json(
        tmp_path / "winner-book" / "feedback-history.json",
        [
            {
                "date": "2026-06-06",
                "units_7d": 5,
                "kenp_7d": 1000,
                "impressions_7d": 500,
                "bsr": 120000,
                "reviews_count": 1,
                "avg_rating": 5,
            }
        ],
    )

    portfolio = profit_tracker.build_portfolio(today=date(2026, 6, 7))
    book = portfolio["books"][0]

    assert book["action"] == "winner"
    assert portfolio["summary"]["books_with_data"] == 1
    assert portfolio["summary"]["units_30d"] == 5
    assert portfolio["summary"]["kenp_30d"] == 1000
    assert portfolio["summary"]["estimated_revenue_30d_usd"] == 14.96
