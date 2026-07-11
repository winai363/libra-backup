import asyncio

import kdp_sales_sync
from kdp_sales_sync import ledger_snapshot_from_kdp, merge_title_baselines


def test_partial_top_titles_preserves_missing_baseline():
    previous = {"A": {"orders": 5}, "B": {"orders": 7}}

    merged = merge_title_baselines(previous, [{"asin": "A", "orders": 6}])

    assert merged["A"]["orders"] == 6
    assert merged["B"]["orders"] == 7


def test_title_reentry_replaces_its_preserved_baseline():
    previous = {"A": {"orders": 5}, "B": {"orders": 7}}
    partial = merge_title_baselines(previous, [{"asin": "A", "orders": 6}])

    merged = merge_title_baselines(partial, [{"asin": "B", "orders": 8}])

    assert merged["A"]["orders"] == 6
    assert merged["B"]["orders"] == 8


def test_overview_snapshot_keeps_total_separate_from_attribution():
    snap = ledger_snapshot_from_kdp(
        {
            "overview": {
                "digitalOrders": 252,
                "kenpRead": 361,
                "totalRoyalties": 7.63,
                "currency": "USD",
            },
            "titles": [
                {
                    "asin": "A",
                    "orders": 60,
                    "pagesRead": 173,
                    "royalties": 6.84,
                    "currency": "USD",
                }
            ],
        },
        "2026-07-11T09:15:09+07:00",
    )

    assert snap["observed_at"] == "2026-07-11T09:15:09+07:00"
    assert snap["month"] == "2026-07"
    assert snap["overview"] == {
        "royalties_usd": 7.63,
        "orders_all_types": 252,
        "kenp": 361,
    }
    assert snap["titles"][0] == {
        "asin": "A",
        "orders": 60,
        "kenp": 173,
        "royalties_usd": 6.84,
    }


def test_dry_run_builds_reconciliation_input_without_writing_ledger(
    tmp_path, monkeypatch
):
    data = {
        "overview": {
            "digitalOrders": 1,
            "kenpRead": 2,
            "totalRoyalties": 3.0,
            "currency": "USD",
        },
        "titles": [],
    }
    messages = []
    writes = []
    monkeypatch.setattr(kdp_sales_sync, "KDP_DIR", tmp_path)
    monkeypatch.setattr(kdp_sales_sync, "STATE_FILE", tmp_path / "state.json")
    monkeypatch.setattr(kdp_sales_sync, "fetch_kdp", lambda: asyncio.sleep(0, data))
    monkeypatch.setattr(kdp_sales_sync, "_log", messages.append)
    monkeypatch.setattr(
        kdp_sales_sync, "record_kdp_snapshot", lambda *args: writes.append(args)
    )

    kdp_sales_sync.sync(dry_run=True)

    assert writes == []
    assert any("reconciliation input" in message for message in messages)
