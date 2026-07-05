import json
from datetime import date

import feedback_loop


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def test_analyze_handles_na_bsr_and_blank_metrics(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback_loop, "KDP_DIR", tmp_path)
    write_json(
        tmp_path / "book" / "feedback-history.json",
        [
            {
                "date": date.today().isoformat(),
                "bsr": "n/a",
                "units_7d": "",
                "kenp_7d": None,
                "impressions_7d": "12",
                "reviews_count": "0",
                "avg_rating": "n/a",
            }
        ],
    )

    analysis = feedback_loop.analyze("book")

    assert analysis["totals_30d"]["units"] == 0
    assert analysis["totals_30d"]["kenp"] == 0
    assert analysis["totals_30d"]["impressions"] == 12


def test_record_snapshot_delta_handles_previous_na_values(tmp_path, monkeypatch):
    monkeypatch.setattr(feedback_loop, "KDP_DIR", tmp_path)
    write_json(
        tmp_path / "book" / "feedback-history.json",
        [{"date": "2026-07-01", "bsr": "n/a", "units_7d": "1", "kenp_7d": None}],
    )

    snapshot = feedback_loop.record_snapshot(
        "book", {"date": "2026-07-02", "bsr": 900000, "units_7d": 3, "kenp_7d": 20}
    )

    assert snapshot["delta_bsr"] == 900000
    assert snapshot["delta_units"] == 2
    assert snapshot["delta_kenp"] == 20
