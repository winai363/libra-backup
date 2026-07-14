import asyncio
import json

import kdp_sales_sync
from kdp_sales_sync import ledger_snapshot_from_kdp, merge_title_baselines, merged_title_rows


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


def test_merged_title_rows_keep_titles_kdp_dropped_from_the_widget():
    # acuarela earned $0.68 MTD, then fell out of KDP's top-N widget on 13 ก.ค.
    # — its money still counts in the overview total, so the ledger snapshot
    # must keep its last-seen row instead of faking an attribution gap.
    merged = merge_title_baselines(
        {"OLD": {"orders": 20, "pagesRead": 147, "royalties": 0.68, "currency": "USD"}},
        [{"asin": "NEW", "orders": 6, "pagesRead": 0, "royalties": 2.33, "currency": "USD"}],
    )

    rows = merged_title_rows(
        merged,
        [{"asin": "NEW", "orders": 6, "pagesRead": 0, "royalties": 2.33, "currency": "USD"}],
        "USD",
    )

    assert [row["asin"] for row in rows] == ["NEW", "OLD"]
    dropped = next(row for row in rows if row["asin"] == "OLD")
    assert dropped["royalties"] == 0.68 and dropped["currency"] == "USD"


def test_merged_title_rows_legacy_state_without_currency_uses_overview_currency():
    rows = merged_title_rows({"OLD": {"orders": 1, "pagesRead": 0, "royalties": 0.5}}, [], "USD")

    assert rows[0]["currency"] == "USD"


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
    monkeypatch.setattr(
        kdp_sales_sync, "_log", lambda message, **kwargs: messages.append(message)
    )
    monkeypatch.setattr(
        kdp_sales_sync, "record_kdp_snapshot", lambda *args: writes.append(args)
    )

    kdp_sales_sync.sync(dry_run=True)

    assert writes == []
    assert any("reconciliation input" in message for message in messages)


def test_dry_run_does_not_mutate_any_files_or_call_writers(tmp_path, monkeypatch):
    kdp_dir = tmp_path / "kdp"
    book_dir = kdp_dir / "book-one"
    book_dir.mkdir(parents=True)
    listing = book_dir / "listing.json"
    listing.write_text(json.dumps({"title": "Exact Match Book"}), encoding="utf-8")
    feedback = book_dir / "feedback-history.json"
    feedback.write_text("[]", encoding="utf-8")
    state = kdp_dir / "sales-sync-state.json"
    state.write_text(json.dumps({"month": "2026-07", "titles": {}}), encoding="utf-8")
    log = tmp_path / "logs" / "sales-sync.log"
    log.parent.mkdir()
    log.write_text("existing log\n", encoding="utf-8")
    ledger = tmp_path / "data" / "libra-business.db"
    ledger.parent.mkdir()
    ledger.write_bytes(b"existing ledger")
    watched = [listing, feedback, state, log, ledger]
    before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in watched}
    data = {
        "overview": {
            "digitalOrders": 1,
            "kenpRead": 0,
            "totalRoyalties": 1.0,
            "currency": "XYZ",
        },
        "titles": [{
            "asin": "NEW-ASIN",
            "title": "Exact Match Book",
            "orders": 1,
            "pagesRead": 0,
            "royalties": 1.0,
            "currency": "XYZ",
        }],
    }
    writer_calls = []
    monkeypatch.setattr(kdp_sales_sync, "KDP_DIR", kdp_dir)
    monkeypatch.setattr(kdp_sales_sync, "STATE_FILE", state)
    monkeypatch.setattr(kdp_sales_sync, "LOG_FILE", log)
    monkeypatch.setattr(kdp_sales_sync, "LEDGER_FILE", ledger)
    monkeypatch.setattr(kdp_sales_sync, "fetch_kdp", lambda: asyncio.sleep(0, data))
    monkeypatch.setattr(
        kdp_sales_sync, "record_kdp_snapshot", lambda *args: writer_calls.append(args)
    )
    monkeypatch.setattr(
        kdp_sales_sync, "upsert_today_snapshot", lambda *args: writer_calls.append(args)
    )

    kdp_sales_sync.sync(dry_run=True)

    assert writer_calls == []
    assert {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in watched} == before
