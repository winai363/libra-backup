"""Unit tests for the bookshelf roster's takedown watch: compute_alerts and
maybe_alert. No KDP session and no browser involved — the bookshelf report is
built by hand, which is also the only way to exercise a status we hope never to
see on the real shelf.

Every recorded content block on this account passed through Amazon's review
first, so IN_REVIEW and UNPUBLISHED have to raise an alert on their own rather
than wait for the BLOCKED badge."""
import json

import pytest

import kdp_bookshelf_roster as roster


def entry(asin: str, status: str, *, slug: str = "book-a", fmt: str = "ebook",
          title: str = "A Title") -> dict:
    return {"book_id": f"ID-{asin}", "asin": asin, "status": status, "slug": slug,
            "format": fmt, "title_guess": title}


def report(entries, *, total_rows: int = 45, orphans=None) -> dict:
    return {"entries": entries, "orphans": orphans or [], "total_rows": total_rows}


@pytest.fixture(autouse=True)
def isolated_paths(tmp_path, monkeypatch):
    """Keep the ack file and the listing scan inside tmp_path so a test never
    reads or writes the real catalogue."""
    monkeypatch.setattr(roster, "ACK_FILE", tmp_path / "roster-acknowledged.json")
    monkeypatch.setattr(roster, "KDP_DIR", tmp_path / "kdp")
    (tmp_path / "kdp").mkdir()
    return tmp_path


@pytest.fixture
def telegram(monkeypatch):
    sent = []
    monkeypatch.setattr(roster, "_telegram", lambda msg: sent.append(msg))
    return sent


# ── compute_alerts ───────────────────────────────────────────────────────────

def test_in_review_row_is_reported():
    alerts = roster.compute_alerts(report([entry("B0H0000001", "IN_REVIEW")]))

    assert [e["asin"] for e in alerts["in_review"]] == ["B0H0000001"]
    assert alerts["blocked"] == []


def test_unpublished_row_is_reported():
    alerts = roster.compute_alerts(report([entry("B0H0000002", "UNPUBLISHED")]))

    assert [e["asin"] for e in alerts["unpublished"]] == ["B0H0000002"]


def test_live_rows_raise_nothing():
    alerts = roster.compute_alerts(report([
        entry("B0H0000003", "LIVE"),
        entry("B0H0000004", "LIVE", slug="book-b"),
    ]))

    assert alerts["in_review"] == []
    assert alerts["unpublished"] == []
    assert alerts["blocked"] == []
    assert alerts["live_duplicates"] == {}


def test_untracked_in_review_row_is_not_reported():
    """A row with no slug and no duplicate_of belongs to no book we track."""
    row = entry("B0H0000005", "IN_REVIEW")
    row["slug"] = None

    alerts = roster.compute_alerts(report([row]))

    assert alerts["in_review"] == []


def test_ebook_and_its_paperback_are_not_a_duplicate():
    alerts = roster.compute_alerts(report([
        entry("B0H0000006", "LIVE", fmt="ebook"),
        entry("B0H0000007", "LIVE", fmt="paperback"),
    ]))

    assert alerts["live_duplicates"] == {}


# ── maybe_alert ──────────────────────────────────────────────────────────────

def test_maybe_alert_sends_for_in_review_and_unpublished(telegram):
    roster.maybe_alert(report([
        entry("B0H0000008", "IN_REVIEW", title="Guide Senior"),
        entry("B0H0000009", "UNPUBLISHED", slug="book-b", title="Cuaderno TDAH"),
    ]))

    assert len(telegram) == 1
    message = telegram[0]
    assert "B0H0000008" in message and "IN REVIEW" in message
    assert "B0H0000009" in message and "UNPUBLISHED" in message


def test_maybe_alert_does_not_repeat_the_same_finding(telegram, isolated_paths):
    shelf = report([entry("B0H0000010", "IN_REVIEW")])

    roster.maybe_alert(shelf)
    roster.maybe_alert(shelf)

    assert len(telegram) == 1
    acked = json.loads((isolated_paths / "roster-acknowledged.json").read_text())
    assert acked["asins"] == ["B0H0000010"]


def test_maybe_alert_stays_quiet_on_a_healthy_shelf(telegram):
    roster.maybe_alert(report([entry("B0H0000011", "LIVE")]))

    assert telegram == []


def test_maybe_alert_reports_a_locally_live_book_missing_from_the_shelf(telegram, isolated_paths):
    book = isolated_paths / "kdp" / "book-gone"
    book.mkdir()
    (book / "listing.json").write_text(json.dumps(
        {"live_status": "LIVE", "asin": "B0H0000012", "title": "Vanished Book"}))

    roster.maybe_alert(report([entry("B0H0000013", "LIVE")]))

    assert len(telegram) == 1
    assert "book-gone" in telegram[0]


def test_partial_fetch_does_not_claim_books_vanished(telegram, isolated_paths):
    """Fewer than 20 rows means the scrape itself is suspect — an incomplete
    page must not be read as a shelf full of takedowns."""
    book = isolated_paths / "kdp" / "book-gone"
    book.mkdir()
    (book / "listing.json").write_text(json.dumps(
        {"live_status": "LIVE", "asin": "B0H0000014", "title": "Vanished Book"}))

    roster.maybe_alert(report([entry("B0H0000015", "LIVE")], total_rows=3))

    assert telegram == []
