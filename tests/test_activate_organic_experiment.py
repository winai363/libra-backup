"""Unit tests for scripts/activate_organic_experiment.py — the operator entry
point for activation. The behaviour under test is mostly refusal: the script may
open one channel, approve one prepared article at a time, record a publication it
was given evidence for, and nothing else. Every test runs against a temporary
tree, so no test can approve a real article or open the real channel."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import activate_organic_experiment as activation  # noqa: E402

NOW = datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc)


def _draft(article_id, *, lane, order, campaign, slug, semantic_pass=True):
    draft = {
        "id": article_id,
        "title": f"title of {article_id}",
        "description": "a description long enough to pass the feed's own limits",
        "channel": lane,
        "campaign": campaign,
        "publication_order": order,
        "target_slug": slug,
        "link": f"/libra/growth/articles/{article_id}",
        "image_url": f"/libra/api/books/{slug}/cover",
        "guid": f"libra-{article_id}",
        "qa_approved": False,
        "published_at": None,
    }
    if semantic_pass:
        draft["semantic_qa"] = {"status": "SEMANTIC_QA_PASS"}
    return draft


@pytest.fixture
def paths(tmp_path):
    root = tmp_path / "libra"
    kdp = tmp_path / "kdp"
    (root / "data" / "growth_articles_drafts").mkdir(parents=True)
    lanes = [
        ("pin-one", "pinterest-rss", 1, "pin-adhd-es", "book-a"),
        ("pin-two", "pinterest-rss", 2, "pin-adhd-es", "book-a"),
        ("li-one", "owner-post", 1, "li-contab-pt", "book-b"),
    ]
    for article_id, lane, order, campaign, slug in lanes:
        (root / "data" / "growth_articles_drafts" / f"{article_id}.json").write_text(
            json.dumps(_draft(article_id, lane=lane, order=order, campaign=campaign, slug=slug)))
    for slug, asin in (("book-a", "B0BOOKAAA1"), ("book-b", "B0BOOKBBB2")):
        (kdp / slug).mkdir(parents=True)
        (kdp / slug / "listing.json").write_text(
            json.dumps({"asin": asin, "live_status": "LIVE", "title": slug}))
        (kdp / slug / "cover.jpg").write_bytes(b"\xff\xd8jpeg")
    (root / "data" / "posting_authorization.json").write_text(json.dumps({
        "channels": {"pinterest-rss": {"authorized": False, "authorized_by": None,
                                       "authorized_at": None}}}))
    (root / "data" / "organic_experiment.json").write_text(json.dumps({
        "name": "test-experiment", "active": False, "books": ["book-a", "book-b"],
        "publications": []}))
    (root / "data" / "growth_campaigns.json").write_text(json.dumps({
        "campaigns": ["pin-adhd-es", "li-contab-pt"],
        "channels": {"pin-adhd-es": "pinterest-rss", "li-contab-pt": "owner-post"}}))
    (root / "scripts").mkdir()
    return activation.Paths(root, kdp)


def _authorization(paths):
    return json.loads(paths.authorization.read_text())["channels"]["pinterest-rss"]


def _experiment(paths):
    return json.loads(paths.experiment.read_text())


# --- preflight ---------------------------------------------------------------

def test_preflight_reports_prepared_articles_without_writing(paths):
    before = paths.authorization.read_text()
    checks = activation.preflight(paths)
    assert [row["id"] for row in checks["prepared"]] == ["pin-one", "pin-two", "li-one"]
    assert checks["channel_authorized"] is False
    assert checks["experiment_active"] is False
    assert paths.authorization.read_text() == before
    assert not paths.served.exists()


def test_preflight_flags_a_book_that_is_not_live(paths):
    listing = paths.kdp / "book-a" / "listing.json"
    listing.write_text(json.dumps({"asin": "B0BOOKAAA1", "live_status": "BLOCKED"}))
    checks = activation.preflight(paths)
    assert checks["ok"] is False
    assert any("not Live" in problem for problem in checks["problems"])


def test_preflight_flags_an_undeclared_campaign(paths):
    campaigns = json.loads(paths.campaigns.read_text())
    campaigns["campaigns"].remove("pin-adhd-es")
    paths.campaigns.write_text(json.dumps(campaigns))
    checks = activation.preflight(paths)
    assert checks["ok"] is False
    assert any("not declared" in problem for problem in checks["problems"])


def test_preflight_ignores_a_draft_without_a_semantic_qa_pass(paths):
    (paths.drafts / "backlog.json").write_text(json.dumps(
        _draft("backlog", lane="pinterest-rss", order=9, campaign="pin-adhd-es",
               slug="book-a", semantic_pass=False)))
    assert [row["id"] for row in activation.preflight(paths)["prepared"]] == \
        ["pin-one", "pin-two", "li-one"]


# --- authorization -----------------------------------------------------------

def test_authorize_records_who_and_when(paths):
    result = activation.authorize(paths, owner="Bui", at=NOW)
    assert result["changed"] is True
    record = _authorization(paths)
    assert record["authorized"] is True
    assert record["authorized_by"] == "Bui"
    assert record["authorized_at"] == NOW.isoformat()


def test_authorize_is_idempotent(paths):
    activation.authorize(paths, owner="Bui", at=NOW)
    again = activation.authorize(paths, owner="Someone else", at=NOW + timedelta(days=1))
    assert again["changed"] is False
    assert _authorization(paths)["authorized_by"] == "Bui"


def test_authorize_refuses_an_unknown_channel_file(paths):
    paths.authorization.write_text(json.dumps({"channels": {}}))
    with pytest.raises(activation.Refused):
        activation.authorize(paths, owner="Bui", at=NOW)


def test_cli_authorize_refuses_a_wrong_confirmation_phrase(paths, capsys):
    exit_code = activation.main(["--root", str(paths.root), "--kdp-dir", str(paths.kdp),
                                 "authorize", "--owner", "Bui", "--confirm", "yes do it"])
    assert exit_code == 2
    assert _authorization(paths)["authorized"] is False


# --- approval ----------------------------------------------------------------

def test_approve_next_publishes_one_article_in_order(paths):
    first = activation.approve_next(paths, lane="pinterest-rss", at=NOW)
    assert first["id"] == "pin-one"
    served = json.loads((paths.served / "pin-one.json").read_text())
    assert served["qa_approved"] is True
    assert served["published_at"] == NOW.isoformat()
    assert not (paths.drafts / "pin-one.json").exists()
    assert (paths.drafts / "pin-two.json").exists()

    second = activation.approve_next(paths, lane="pinterest-rss", at=NOW + timedelta(days=1))
    assert second["id"] == "pin-two"


def test_approve_next_refuses_two_of_one_lane_on_the_same_day(paths):
    activation.approve_next(paths, lane="pinterest-rss", at=NOW)
    with pytest.raises(activation.Refused, match="one article per lane per day"):
        activation.approve_next(paths, lane="pinterest-rss", at=NOW + timedelta(hours=3))
    assert not (paths.served / "pin-two.json").exists()


def test_approve_next_keeps_the_lanes_independent(paths):
    activation.approve_next(paths, lane="pinterest-rss", at=NOW)
    owner_post = activation.approve_next(paths, lane="owner-post", at=NOW)
    assert owner_post["id"] == "li-one"
    assert owner_post["campaign"] == "li-contab-pt"


def test_approve_next_refuses_when_the_target_book_is_not_live(paths):
    (paths.kdp / "book-a" / "listing.json").write_text(
        json.dumps({"asin": "B0BOOKAAA1", "live_status": "IN_REVIEW"}))
    with pytest.raises(activation.Refused, match="not Live"):
        activation.approve_next(paths, lane="pinterest-rss", at=NOW)
    assert not paths.served.exists()


def test_approve_next_refuses_when_the_lane_is_exhausted(paths):
    activation.approve_next(paths, lane="owner-post", at=NOW)
    with pytest.raises(activation.Refused, match="already approved"):
        activation.approve_next(paths, lane="owner-post", at=NOW + timedelta(days=1))


def test_approved_published_at_is_never_in_the_future(paths):
    result = activation.approve_next(paths, lane="pinterest-rss")
    assert datetime.fromisoformat(result["published_at"]) <= datetime.now(timezone.utc)


# --- verified publication and the clock --------------------------------------

def test_record_publication_starts_the_clock_once(paths):
    first = activation.record_publication(
        paths, channel="pinterest-rss", slug="book-a",
        url="https://www.pinterest.com/pin/1", evidence="opened it, resolves to the hub page")
    assert first["clock_started"] is True
    assert _experiment(paths)["active"] is True
    second = activation.record_publication(
        paths, channel="owner-post", slug="book-b",
        url="https://www.linkedin.com/posts/2", evidence="opened it")
    assert second["clock_started"] is False
    assert len(_experiment(paths)["publications"]) == 2


def test_record_publication_is_idempotent_on_the_same_url(paths):
    activation.record_publication(paths, channel="pinterest-rss", slug="book-a",
                                  url="https://www.pinterest.com/pin/1", evidence="opened it")
    again = activation.record_publication(paths, channel="pinterest-rss", slug="book-a",
                                          url="https://www.pinterest.com/pin/1", evidence="opened it")
    assert again["changed"] is False
    assert len(_experiment(paths)["publications"]) == 1


@pytest.mark.parametrize("url,evidence", [
    ("http://www.pinterest.com/pin/1", "opened it"),
    ("pinterest.com/pin/1", "opened it"),
    ("https://www.pinterest.com/pin/1", "   "),
])
def test_record_publication_refuses_weak_evidence(paths, url, evidence):
    with pytest.raises(activation.Refused):
        activation.record_publication(paths, channel="pinterest-rss", slug="book-a",
                                      url=url, evidence=evidence)
    assert _experiment(paths)["active"] is False


def test_record_publication_refuses_a_book_outside_the_experiment(paths):
    with pytest.raises(activation.Refused, match="not a book of this experiment"):
        activation.record_publication(paths, channel="pinterest-rss", slug="book-z",
                                      url="https://www.pinterest.com/pin/1", evidence="opened it")


# --- the whole Day-0 run -----------------------------------------------------

def test_day0_refuses_without_the_exact_phrase(paths):
    with pytest.raises(activation.Refused):
        activation.day0(paths, owner="Bui", confirm="activate please")
    assert _authorization(paths)["authorized"] is False
    assert not paths.served.exists()


def test_day0_refuses_when_preflight_fails(paths, monkeypatch):
    (paths.kdp / "book-a" / "cover.jpg").unlink()
    with pytest.raises(activation.Refused, match="preflight failed"):
        activation.day0(paths, owner="Bui", confirm=activation.CONFIRM_PHRASE)
    assert _authorization(paths)["authorized"] is False


def test_day0_authorizes_and_approves_one_article_per_lane(paths, monkeypatch):
    monkeypatch.setattr(activation, "preflight", lambda _paths: {"ok": True, "problems": []})
    monkeypatch.setattr(activation, "feed_check", lambda *a, **k: {"status": 200, "items": 1})
    result = activation.day0(paths, owner="Bui", confirm=activation.CONFIRM_PHRASE)
    assert _authorization(paths)["authorized"] is True
    assert [row["id"] for row in result["approved"]] == ["pin-one", "li-one"]
    assert result["feed"]["items"] == 1
    # The clock stays stopped: only a verified publication starts it.
    assert _experiment(paths)["active"] is False


def test_day0_never_touches_kdp_or_the_experiment_clock(paths, monkeypatch):
    monkeypatch.setattr(activation, "preflight", lambda _paths: {"ok": True, "problems": []})
    monkeypatch.setattr(activation, "feed_check", lambda *a, **k: {"status": 200, "items": 1})
    listing_before = (paths.kdp / "book-a" / "listing.json").read_text()
    activation.day0(paths, owner="Bui", confirm=activation.CONFIRM_PHRASE)
    assert (paths.kdp / "book-a" / "listing.json").read_text() == listing_before
    assert _experiment(paths)["publications"] == []


# --- feed check --------------------------------------------------------------

def test_feed_check_reads_a_404_as_a_closed_channel(monkeypatch):
    import urllib.error

    def raise_404(*args, **kwargs):
        raise urllib.error.HTTPError("url", 404, "Not Found", {}, None)

    monkeypatch.setattr(activation.urllib.request, "urlopen", raise_404)
    assert activation.feed_check()["status"] == 404


def test_feed_check_refuses_a_feed_that_is_not_rss_2(monkeypatch):
    class Response:
        status = 200

        def read(self):
            return b'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"></feed>'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    monkeypatch.setattr(activation.urllib.request, "urlopen", lambda *a, **k: Response())
    with pytest.raises(activation.Refused, match="not RSS 2.0"):
        activation.feed_check()
