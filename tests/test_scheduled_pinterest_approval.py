"""Unit tests for scripts/scheduled_pinterest_approval.py — the 5-day Pinterest
publication schedule the owner authorized on 2026-09-12.

The behaviour that matters is what the wrapper refuses to do: fire outside its
window, approve two articles in one run, approve anything while another run holds
the lock, touch the LinkedIn lane, or make up for a failed day afterwards."""
import json
import sys
from datetime import date, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import activate_organic_experiment as activation  # noqa: E402
import scheduled_pinterest_approval as schedule  # noqa: E402


def _draft(article_id, *, lane, order, campaign, slug):
    return {
        "id": article_id, "title": f"title of {article_id}",
        "description": "a description long enough for the feed's own limits",
        "channel": lane, "campaign": campaign, "publication_order": order,
        "target_slug": slug, "link": f"/libra/growth/articles/{article_id}",
        "image_url": f"/libra/api/books/{slug}/cover", "guid": f"libra-{article_id}",
        "qa_approved": False, "published_at": None,
        "semantic_qa": {"status": "SEMANTIC_QA_PASS"},
    }


@pytest.fixture(autouse=True)
def no_real_alerts(monkeypatch):
    """A refused run alerts over Telegram. A test must never send that message to
    the owner's real chat, so the sender is replaced for every test here."""
    sent = []
    monkeypatch.setattr(schedule, "send_telegram", lambda message: sent.append(message))
    return sent


@pytest.fixture
def paths(tmp_path, monkeypatch):
    root = tmp_path / "libra"
    kdp = tmp_path / "kdp"
    (root / "data" / "growth_articles_drafts").mkdir(parents=True)
    for article_id, lane, order, campaign in (
        ("pin-one", "pinterest-rss", 1, "pin-adhd-es"),
        ("pin-two", "pinterest-rss", 2, "pin-adhd-es"),
        ("li-one", "owner-post", 1, "li-contab-pt"),
    ):
        (root / "data" / "growth_articles_drafts" / f"{article_id}.json").write_text(
            json.dumps(_draft(article_id, lane=lane, order=order, campaign=campaign,
                              slug="book-a")))
    (kdp / "book-a").mkdir(parents=True)
    (kdp / "book-a" / "listing.json").write_text(
        json.dumps({"asin": "B0BOOKAAA1", "live_status": "LIVE"}))
    (kdp / "book-a" / "cover.jpg").write_bytes(b"\xff\xd8jpeg")

    built = activation.Paths(root, kdp)
    monkeypatch.setattr(schedule, "_paths", lambda: built)
    monkeypatch.setattr(schedule, "LOCK_FILE", root / "data" / ".lock")
    monkeypatch.setattr(activation, "KDP_DIR", kdp)
    return built


def _served(paths):
    return sorted(f.stem for f in paths.served.glob("*.json")) if paths.served.is_dir() else []


@pytest.mark.parametrize("day", [datetime(2026, 9, 12, 9, 0), datetime(2026, 9, 18, 9, 0),
                                 datetime(2027, 9, 14, 9, 0)])
def test_outside_the_window_is_a_no_op(paths, day):
    result = schedule.run(now=day.replace(tzinfo=schedule.TIMEZONE))
    assert result["state"] == "outside_window"
    assert _served(paths) == []


@pytest.mark.parametrize("day", [datetime(2026, 9, 13, 9, 0), datetime(2026, 9, 15, 9, 0),
                                 datetime(2026, 9, 17, 9, 0)])
def test_inside_the_window_approves_exactly_one_pinterest_article(paths, day):
    result = schedule.run(now=day.replace(tzinfo=schedule.TIMEZONE))
    assert result["state"] == "approved"
    assert result["id"] == "pin-one"
    assert result["lane"] == "pinterest-rss"
    assert _served(paths) == ["pin-one"]


def test_a_second_run_on_the_same_day_approves_nothing_more(paths):
    schedule.run(now=datetime(2026, 9, 13, 9, 0, tzinfo=schedule.TIMEZONE))
    second = schedule.run(now=datetime(2026, 9, 13, 9, 0, tzinfo=schedule.TIMEZONE))
    assert second["state"] == "refused"
    assert "one article per lane per day" in second["reason"]
    assert _served(paths) == ["pin-one"]


def test_consecutive_days_preserve_publication_order(paths):
    assert schedule.run(now=datetime(2026, 9, 13, 9, 0, tzinfo=schedule.TIMEZONE))["id"] == "pin-one"
    assert schedule.run(now=datetime(2026, 9, 14, 9, 0, tzinfo=schedule.TIMEZONE))["id"] == "pin-two"
    assert _served(paths) == ["pin-one", "pin-two"]


def test_a_failed_day_is_never_compensated_later(paths, monkeypatch):
    def refuse(*args, **kwargs):
        raise activation.Refused("simulated failure")

    monkeypatch.setattr(activation, "approve_next", refuse)
    alerts = []
    monkeypatch.setattr(schedule, "send_telegram", lambda message: alerts.append(message))
    failed = schedule.run(now=datetime(2026, 9, 13, 9, 0, tzinfo=schedule.TIMEZONE))
    assert failed["state"] == "refused"
    assert len(alerts) == 1
    assert _served(paths) == []

    monkeypatch.undo()
    monkeypatch.setattr(schedule, "_paths", lambda: paths)
    monkeypatch.setattr(schedule, "LOCK_FILE", paths.root / "data" / ".lock")
    # The next day approves one article, not the missed one as well.
    assert schedule.run(now=datetime(2026, 9, 14, 9, 0, tzinfo=schedule.TIMEZONE))["id"] == "pin-one"
    assert _served(paths) == ["pin-one"]


def test_a_concurrent_run_is_refused_rather_than_queued(paths):
    import fcntl

    schedule.LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(schedule.LOCK_FILE, "a+", encoding="utf-8") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = schedule.run(now=datetime(2026, 9, 13, 9, 0, tzinfo=schedule.TIMEZONE))
    assert result["state"] == "locked"
    assert _served(paths) == []


def test_check_mode_writes_nothing(paths):
    result = schedule.run(now=datetime(2026, 9, 13, 9, 0, tzinfo=schedule.TIMEZONE), check_only=True)
    assert result["state"] == "check"
    assert result["next_eligible"] == "pin-one"
    assert _served(paths) == []


def test_the_linkedin_lane_is_never_touched(paths):
    for day in (13, 14, 15):
        schedule.run(now=datetime(2026, 9, day, 9, 0, tzinfo=schedule.TIMEZONE))
    assert "li-one" not in _served(paths)
    assert (paths.drafts / "li-one.json").exists()


def test_the_lane_runs_dry_without_error_once_every_article_is_approved(paths):
    schedule.run(now=datetime(2026, 9, 13, 9, 0, tzinfo=schedule.TIMEZONE))
    schedule.run(now=datetime(2026, 9, 14, 9, 0, tzinfo=schedule.TIMEZONE))
    exhausted = schedule.run(now=datetime(2026, 9, 15, 9, 0, tzinfo=schedule.TIMEZONE))
    assert exhausted["state"] == "refused"
    assert "already approved" in exhausted["reason"]


def test_the_window_is_exactly_the_five_authorized_days():
    assert schedule.WINDOW_FIRST_DAY == date(2026, 9, 13)
    assert schedule.WINDOW_LAST_DAY == date(2026, 9, 17)
    assert (schedule.WINDOW_LAST_DAY - schedule.WINDOW_FIRST_DAY).days + 1 == 5
