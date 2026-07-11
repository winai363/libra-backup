import json
from datetime import date

import winner_signals


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_zero_royalty_units_are_not_proven_winner(tmp_path, monkeypatch):
    monkeypatch.setattr(winner_signals, "KDP_DIR", tmp_path)
    write_json(tmp_path / "free-book" / "listing.json", {"title": "Free Book"})
    write_json(tmp_path / "free-book" / "feedback-history.json", [{
        "date": "2026-07-10", "units_7d": 20, "kenp_7d": 0,
        "revenue_usd": 0.0,
    }])

    assert winner_signals.get_winners(today=date(2026, 7, 11)) == []


def test_prompt_calls_units_orders_downloads_when_royalty_is_verified():
    prompt = winner_signals.format_for_prompt([{
        "slug": "paid-book", "title": "Paid Book", "niche": "tax",
        "language": "English", "marketplace": "US", "units": 3,
        "kenp": 0, "revenue_usd": 4.25,
    }])

    assert "3 orders/downloads" in prompt
    assert "sale(s)" not in prompt
    assert "$4.25 royalty" in prompt


def test_known_cost_must_leave_positive_contribution_for_winner(tmp_path, monkeypatch):
    monkeypatch.setattr(winner_signals, "KDP_DIR", tmp_path)
    write_json(tmp_path / "loss-book" / "listing.json", {"title": "Loss Book"})
    write_json(tmp_path / "loss-book" / "cost-report.json", {"total_usd": 5.0})
    write_json(tmp_path / "loss-book" / "feedback-history.json", [{
        "date": "2026-07-10", "units_7d": 2, "kenp_7d": 0,
        "revenue_usd": 4.25,
    }])

    assert winner_signals.get_winners(today=date(2026, 7, 11)) == []
