import json
from datetime import date

import winner_signals
from business_ledger import record_direct_cost


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


def test_ledger_attributed_cost_prevents_proven_winner(tmp_path, monkeypatch):
    kdp_dir = tmp_path / "kdp"
    ledger = tmp_path / "ledger.db"
    monkeypatch.setattr(winner_signals, "KDP_DIR", kdp_dir)
    monkeypatch.setattr(winner_signals, "LEDGER_FILE", ledger)
    write_json(kdp_dir / "loss-book" / "listing.json", {"title": "Loss Book"})
    write_json(kdp_dir / "loss-book" / "feedback-history.json", [{
        "date": "2026-07-10", "units_7d": 2, "kenp_7d": 0,
        "revenue_usd": 4.25,
    }])
    record_direct_cost(
        ledger, incurred_at="2026-07-05T12:00:00+07:00", slug="loss-book",
        category="cover", amount_usd=5.0, source_key="cover:loss-book",
    )

    assert winner_signals.get_winners(today=date(2026, 7, 11)) == []


def test_prompt_describes_kenp_royalty_without_claiming_paid_sale():
    prompt = winner_signals.format_for_prompt([{
        "slug": "read-book", "title": "Read Book", "niche": "tax",
        "language": "English", "marketplace": "US", "units": 4,
        "kenp": 120, "revenue_usd": 4.25,
    }])

    lowered = prompt.lower()
    assert "verified royalty" in lowered
    assert "reader audience" in lowered
    assert "already sold" not in lowered
    assert "buyers paid" not in lowered
    assert "recorded a sale" not in lowered
    assert "proven buyers" not in lowered
    assert "buyers" not in lowered
    assert "will also pay" not in lowered
