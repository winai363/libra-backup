"""Unit tests for scripts/organic_experiment_report.py — a read-only report on
the organic experiment. The report must never invent a figure: a month KDP did
not attribute stays unknown, a click count of zero reads as INCONCLUSIVE rather
than failure, and the ledger is opened read-only."""
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import organic_experiment_report as report_module  # noqa: E402

from business_ledger import init_ledger, record_hub_event  # noqa: E402
from content_hub import build_outbound_event  # noqa: E402


@pytest.fixture
def ledger(tmp_path):
    path = tmp_path / "libra-business.db"
    init_ledger(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO kdp_snapshots(id, observed_at, month, royalties_usd, "
            "orders_all_types, kenp, raw_json, source_key, content_hash) "
            "VALUES (1, '2026-09-12T09:15:10+07:00', '2026-09', 13.81, 2, 205, '{}', 'k1', 'h1')"
        )
        connection.execute(
            "INSERT INTO kdp_snapshots(id, observed_at, month, royalties_usd, "
            "orders_all_types, kenp, raw_json, source_key, content_hash) "
            "VALUES (2, '2026-09-01T09:15:10+07:00', '2026-09', 9.00, 1, 100, '{}', 'k0', 'h0')"
        )
        connection.executemany(
            "INSERT INTO kdp_title_attribution(snapshot_id, asin, royalties_usd, orders_count, kenp) "
            "VALUES (?, ?, ?, ?, ?)",
            [(1, "B0BOOKONE01", 7.92, 3, 100), (2, "B0BOOKONE01", 4.00, 1, 40)],
        )
    return path


@pytest.fixture
def kdp_dir(tmp_path):
    path = tmp_path / "kdp"
    for slug, listing in {
        "book-one": {"asin": "B0BOOKONE01", "live_status": "LIVE"},
        "book-two": {"live_status": "LIVE"},  # never got an ASIN back from KDP
    }.items():
        (path / slug).mkdir(parents=True)
        (path / slug / "listing.json").write_text(json.dumps(listing))
    return path


def write_experiment(tmp_path, **overrides) -> Path:
    experiment = {
        "name": "organic-test",
        "active": True,
        "books": ["book-one", "book-two"],
        "planned_window_days": 30,
        "publications": [{
            "channel": "owner-post", "slug": "book-one",
            "url": "https://example.test/post/1",
            "observed_at": "2026-09-01T09:00:00+00:00",
            "evidence": "opened the post url; it resolves to the hub page",
        }],
        "primary_metric": "qualified reader clicks per book per campaign",
        "acquisition_threshold_clicks": 25,
        "baseline": {"books": {"book-one": {"2026-08": {"royalties_usd": 1.44}}}},
    }
    experiment.update(overrides)
    path = tmp_path / "organic_experiment.json"
    path.write_text(json.dumps(experiment))
    return path


def write_roster(tmp_path, rows) -> Path:
    path = tmp_path / "bookshelf-roster.json"
    path.write_text(json.dumps({"fetched_at": "2026-09-12T08:45:18", "entries": rows}))
    return path


def build(tmp_path, ledger, kdp_dir, *, today=date(2026, 9, 12), rows=None, **overrides) -> dict:
    roster = write_roster(tmp_path, rows if rows is not None else [
        {"slug": "book-one", "format": "ebook", "status": "LIVE", "asin": "B0BOOKONE01"},
    ])
    return report_module.build_report(
        experiment_file=write_experiment(tmp_path, **overrides),
        ledger=ledger, kdp_dir=kdp_dir, roster_file=roster, today=today,
    )


def test_inactive_experiment_reports_inactive(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir, active=False)

    assert result["state"] == "inactive"
    assert "books" not in result


def test_missing_experiment_file_reports_inactive(tmp_path, ledger, kdp_dir):
    result = report_module.build_report(
        experiment_file=tmp_path / "absent.json", ledger=ledger, kdp_dir=kdp_dir)

    assert result["state"] == "inactive"


def test_missing_ledger_is_blocked_not_zero(tmp_path, kdp_dir):
    result = report_module.build_report(
        experiment_file=write_experiment(tmp_path), ledger=tmp_path / "absent.db",
        kdp_dir=kdp_dir)

    assert result["state"] == "blocked"
    assert "ledger_not_found" in result["reason"]


def test_royalties_use_the_latest_observation_per_month(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir)
    book_one = next(b for b in result["books"] if b["slug"] == "book-one")

    # 7.92 is the later observation; 4.00 + 7.92 would be double counting.
    assert book_one["royalties_by_month"] == {
        "2026-09": {"royalties_usd": 7.92, "orders": 3, "kenp": 100}
    }
    assert result["account_royalties_by_month"]["2026-09"]["royalties_usd"] == 13.81


def test_book_without_asin_reports_unknown_royalties(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir)
    book_two = next(b for b in result["books"] if b["slug"] == "book-two")

    assert book_two["asin"] is None
    assert book_two["royalties_by_month"] is None


def test_no_clicks_is_inconclusive(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir)

    assert result["tracks"]["qualified_outbound_clicks"] == 0
    assert result["verdict"] == "INCONCLUSIVE"


def test_clicks_are_grouped_by_campaign_and_counted(tmp_path, ledger, kdp_dir):
    record_hub_event(ledger, build_outbound_event("book-one", "organic-pinterest"))
    record_hub_event(ledger, build_outbound_event("book-one", "organic-pinterest"))
    record_hub_event(ledger, build_outbound_event("book-one", "organic-owner-post"))

    result = build(tmp_path, ledger, kdp_dir)
    book_one = next(b for b in result["books"] if b["slug"] == "book-one")

    assert book_one["clicks_by_campaign"] == {
        "organic-pinterest": {"amazon_outbound": 2},
        "organic-owner-post": {"amazon_outbound": 1},
    }
    assert result["tracks"]["qualified_outbound_clicks"] == 3
    assert result["verdict"] == "INCONCLUSIVE"
    assert "3/25 qualified clicks" in result["action"]


def test_clicks_before_the_window_are_excluded(tmp_path, ledger, kdp_dir):
    from datetime import datetime, timezone
    old = datetime(2026, 8, 1, tzinfo=timezone.utc)
    record_hub_event(ledger, build_outbound_event("book-one", "organic-pinterest", now=old))

    result = build(tmp_path, ledger, kdp_dir)

    assert result["tracks"]["qualified_outbound_clicks"] == 0


def test_window_closes_on_the_end_date(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir, today=date(2026, 10, 1))

    assert result["window_closed"] is True
    assert result["days_elapsed"] == 30


def test_purchases_are_never_attributed_to_a_click(tmp_path, ledger, kdp_dir):
    record_hub_event(ledger, build_outbound_event("book-one", "organic-pinterest"))

    result = build(tmp_path, ledger, kdp_dir)

    assert all(b["purchase_attribution"] == "not_attributable" for b in result["books"])
    assert "no purchase may be attributed" in result["attribution_note"]


def test_report_never_writes_to_the_ledger(tmp_path, ledger, kdp_dir):
    with sqlite3.connect(ledger) as connection:
        before = connection.execute(
            "SELECT COUNT(*) FROM sqlite_master").fetchone()[0]

    build(tmp_path, ledger, kdp_dir)

    with sqlite3.connect(ledger) as connection:
        after = connection.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0]
        events = connection.execute("SELECT COUNT(*) FROM hub_events").fetchone()[0]
    assert (before, events) == (after, 0)


def test_format_lines_renders_an_inactive_report(tmp_path, ledger, kdp_dir):
    text = report_module.format_lines(build(tmp_path, ledger, kdp_dir, active=False))

    assert "inactive" in text


def test_format_lines_renders_an_active_report(tmp_path, ledger, kdp_dir):
    record_hub_event(ledger, build_outbound_event("book-one", "organic-pinterest"))

    text = report_module.format_lines(build(tmp_path, ledger, kdp_dir))

    assert "organic-test" in text
    assert "book-one" in text
    assert "not_attributable" in text


# ── publication-verified window ─────────────────────────────────────────────

def test_window_starts_at_the_first_verified_publication(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir, publications=[
        {"channel": "owner-post", "slug": "book-one", "url": "https://example.test/b",
         "observed_at": "2026-09-05T10:00:00+00:00", "evidence": "url opens"},
        {"channel": "pinterest-rss", "slug": "book-one", "url": "https://example.test/a",
         "observed_at": "2026-09-02T10:00:00+00:00", "evidence": "pin visible"},
    ])

    assert result["started_on"] == "2026-09-02"
    assert result["started_from"] == "first verified publication"
    assert result["ends_on"] == "2026-10-02"
    assert result["tracks"]["content_published"] == 2


def test_no_publication_means_not_started(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir, publications=[])

    assert result["state"] == "not_started"
    assert result["started_on"] is None
    assert "no verified publication" in result["action"]
    assert result["verdict"] == "INCONCLUSIVE"


@pytest.mark.parametrize("record", [
    {"url": "https://example.test/a", "observed_at": "2026-09-02T10:00:00+00:00"},  # no evidence
    {"url": "https://example.test/a", "evidence": "seen"},                          # no time
    {"observed_at": "2026-09-02T10:00:00+00:00", "evidence": "seen"},               # no url
    {"url": "file:///root/libra/data/feed.xml", "observed_at": "2026-09-02T10:00:00+00:00",
     "evidence": "the rss file exists"},                                            # a file is not a publication
    {"url": "scheduled", "observed_at": "2026-09-02T10:00:00+00:00", "evidence": "cron installed"},
])
def test_a_prepared_or_scheduled_thing_is_not_a_publication(tmp_path, ledger, kdp_dir, record):
    result = build(tmp_path, ledger, kdp_dir, publications=[record])

    assert result["state"] == "not_started"
    assert result["tracks"]["content_published"] == 0


def test_threshold_met_is_reported_as_reach_not_sales(tmp_path, ledger, kdp_dir):
    for _ in range(25):
        record_hub_event(ledger, build_outbound_event("book-one", "organic-pinterest"))

    result = build(tmp_path, ledger, kdp_dir)

    assert result["verdict"] == "ACQUISITION_THRESHOLD_MET"
    assert "not sales validation" in result["action"]
    assert result["threshold_meaning"].startswith("provisional acquisition signal")


def test_zero_clicks_at_day_14_asks_for_a_diagnosis_not_a_retirement(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir, today=date(2026, 9, 15))

    assert result["days_elapsed"] == 14
    assert "diagnose distribution and tracking" in result["action"]
    assert "do not retire" in result["action"]


# ── proportionate response to shelf status ──────────────────────────────────

@pytest.mark.parametrize("status,action", [
    ("LIVE", "run"),
    ("LIVE_UPDATES_IN_REVIEW", "watch"),
    ("IN_REVIEW", "pause"),
    ("BLOCKED", "pause"),
    ("UNPUBLISHED", "pause"),
])
def test_shelf_status_drives_a_proportionate_campaign_action(tmp_path, ledger, kdp_dir,
                                                             status, action):
    result = build(tmp_path, ledger, kdp_dir, rows=[
        {"slug": "book-one", "format": "ebook", "status": status, "asin": "B0BOOKONE01"},
    ])
    book_one = next(b for b in result["books"] if b["slug"] == "book-one")

    assert book_one["campaign"]["action"] == action
    assert (book_one["slug"] in result["paused_books"]) is (action == "pause")


def test_a_book_missing_from_the_roster_is_held_not_assumed_live(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir, rows=[])
    book_one = next(b for b in result["books"] if b["slug"] == "book-one")

    assert book_one["campaign"]["action"] == "hold"
    assert "unknown" in book_one["campaign"]["reason"]


def test_paperback_in_review_pauses_the_book_even_when_the_ebook_is_live(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir, rows=[
        {"slug": "book-one", "format": "ebook", "status": "LIVE", "asin": "B0BOOKONE01"},
        {"slug": "book-one", "format": "paperback", "status": "IN_REVIEW", "asin": "B0PAPER0001"},
    ])
    book_one = next(b for b in result["books"] if b["slug"] == "book-one")

    assert book_one["campaign"]["action"] == "pause"


# ── synthetic verification clicks ───────────────────────────────────────────

def test_registered_synthetic_clicks_are_excluded_from_the_metric(tmp_path, ledger, kdp_dir,
                                                                  monkeypatch):
    synthetic = build_outbound_event("book-one", "synthetic-verification")
    record_hub_event(ledger, synthetic)
    record_hub_event(ledger, build_outbound_event("book-one", "organic-pinterest"))
    registry = tmp_path / "synthetic.json"
    registry.write_text(json.dumps({"version": 1, "incidents": [{
        "id": "libra_hub_click_verification",
        "store_rows": {str(ledger): {"table": "hub_events", "column": "event_key",
                                     "ids": [synthetic["event_key"]]}},
    }]}))
    monkeypatch.setenv("ENGPEAK_SYNTHETIC_EVENTS", str(registry))
    monkeypatch.setattr(report_module.synthetic_events, "REGISTRY_PATH", str(registry))
    monkeypatch.setattr(report_module.synthetic_events, "_cache", {"mtime": None, "data": {}})

    result = build(tmp_path, ledger, kdp_dir)

    assert result["tracks"]["qualified_outbound_clicks"] == 1
    assert result["tracks"]["synthetic_clicks_excluded"] is True


def test_a_missing_registry_reports_more_clicks_never_fewer(tmp_path, ledger, kdp_dir, monkeypatch):
    record_hub_event(ledger, build_outbound_event("book-one", "organic-pinterest"))
    monkeypatch.setattr(report_module, "synthetic_events", None)

    result = build(tmp_path, ledger, kdp_dir)

    assert result["tracks"]["qualified_outbound_clicks"] == 1
    assert result["tracks"]["synthetic_clicks_excluded"] is False


# ── separate metric tracks ──────────────────────────────────────────────────

def test_tracks_are_reported_separately_and_costs_are_zero(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir)
    tracks = result["tracks"]

    assert set(tracks) == {"content_published", "exposure", "qualified_outbound_clicks",
                           "paid_orders", "kenp", "royalties_usd", "costs_usd",
                           "human_interventions", "synthetic_clicks_excluded"}
    assert tracks["paid_orders"] == {"2026-09": 3}
    assert tracks["kenp"] == {"2026-09": 100}
    assert tracks["royalties_usd"] == {"2026-09": 7.92}
    assert tracks["costs_usd"]["paid_spend"] == 0
    assert tracks["exposure"] is None


def test_human_interventions_are_counted(tmp_path, ledger, kdp_dir):
    result = build(tmp_path, ledger, kdp_dir, interventions=[
        {"at": "2026-09-02", "what": "posted the pin by hand"},
        {"at": "2026-09-09", "what": "re-posted after a broken link"},
    ])

    assert result["tracks"]["human_interventions"] == 2
