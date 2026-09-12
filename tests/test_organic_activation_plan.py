"""The activation plan itself, checked against the real prepared drafts and the
real catalogue. These tests answer one question: if the owner authorizes the
channel tomorrow, does the batch that would be published hold together — right
book, right ASIN, right image, right campaign, stable guid — and does the feed
that Pinterest would fetch on each day of the schedule obey every feed rule?

Nothing here writes into data/: the drafts are copied into tmp_path and the
approval transform is applied to the copies."""
import json
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import pytest

LIBRA_DIR = Path(__file__).resolve().parent.parent
KDP_DIR = LIBRA_DIR.parent / "kdp"
sys.path.insert(0, str(LIBRA_DIR / "scripts"))

from growth_feed import DEFAULT_MAX_ITEMS, build_rss, load_entries  # noqa: E402
import activate_organic_experiment as activation  # noqa: E402

SITE = "https://newton-winai-klinprasom.incomeinclick.in.th"
CAMPAIGNS = json.loads((LIBRA_DIR / "data" / "growth_campaigns.json").read_text())
PINTEREST_CAMPAIGNS = {name for name, channel in CAMPAIGNS["channels"].items()
                       if channel == "pinterest-rss"}
APPROVED_ON = datetime(2026, 9, 15, 9, 0, tzinfo=timezone.utc)  # a stand-in Day 0


@pytest.fixture(scope="module")
def plan():
    """The whole prepared batch — the drafts still waiting plus the ones already
    approved and served. Reading both keeps these checks true before activation
    and after it: approving an article moves its file, it does not leave the
    batch."""
    paths = activation.Paths(LIBRA_DIR, KDP_DIR)
    rows = activation.prepared_articles(paths)
    served = paths.served
    for file in sorted(served.glob("*.json")) if served.is_dir() else []:
        data = json.loads(file.read_text())
        rows.append({"file": file, "id": data["id"], "lane": data["channel"],
                     "order": data["publication_order"], "campaign": data["campaign"],
                     "slug": data["target_slug"], "title": data["title"],
                     "data": data, "approved": True})
    for row in rows:
        row.setdefault("approved", False)
    rows.sort(key=lambda row: (activation.LANES.index(row["lane"]), row["order"]))
    return rows


@pytest.fixture(scope="module")
def lanes(plan):
    return ({row["id"]: row for row in plan if row["lane"] == "pinterest-rss"},
            {row["id"]: row for row in plan if row["lane"] == "owner-post"})


def _approved(row, at):
    entry = dict(row["data"])
    entry["qa_approved"] = True
    entry["published_at"] = at.isoformat()
    return entry


def _served_tree(tmp_path, plan, *, up_to_day):
    """The served directory as it would look `up_to_day` days after approval,
    plus an unapproved backlog draft as a control."""
    served = tmp_path / "growth_articles"
    served.mkdir(parents=True, exist_ok=True)
    backlog = LIBRA_DIR / "data" / "growth_articles_drafts" / "adhd-tres-rutinas-es.json"
    if backlog.exists():
        (served / backlog.name).write_text(backlog.read_text())
    for row in plan:
        offset = row["data"]["recommended_offset_days"]
        if offset <= up_to_day:
            entry = _approved(row, APPROVED_ON + timedelta(days=offset))
            (served / f"{row['id']}.json").write_text(json.dumps(entry, ensure_ascii=False))
    return served


def test_nine_prepared_articles_split_six_and_three(lanes):
    pinterest, owner_post = lanes
    assert len(pinterest) == 6
    assert len(owner_post) == 3


def test_publication_order_is_one_per_day_then_weekly(plan):
    pinterest = [row["data"]["recommended_offset_days"] for row in plan
                 if row["lane"] == "pinterest-rss"]
    owner_post = [row["data"]["recommended_offset_days"] for row in plan
                  if row["lane"] == "owner-post"]
    assert pinterest == [0, 1, 2, 3, 4, 5]
    assert owner_post == [0, 7, 14]


def test_every_article_points_at_a_live_book_and_its_own_asin(plan):
    for row in plan:
        listing = json.loads((KDP_DIR / row["slug"] / "listing.json").read_text())
        assert listing["live_status"] == "LIVE", row["id"]
        assert row["data"]["destination"] == f"https://www.amazon.com/dp/{listing['asin']}", row["id"]


def test_every_article_uses_its_own_book_cover_as_the_feed_image(plan):
    for row in plan:
        assert row["data"]["image_url"] == f"/libra/api/books/{row['slug']}/cover", row["id"]
        assert (KDP_DIR / row["slug"] / "cover.jpg").exists(), row["id"]


def test_guids_are_stable_unique_and_derived_from_the_id(plan):
    guids = [row["data"]["guid"] for row in plan]
    assert guids == [f"libra-{row['id']}" for row in plan]
    assert len(set(guids)) == len(guids)


def test_campaigns_are_declared_and_mapped_to_the_articles_own_lane(plan):
    for row in plan:
        assert row["campaign"] in CAMPAIGNS["campaigns"], row["id"]
        assert CAMPAIGNS["channels"][row["campaign"]] == row["lane"], row["id"]


def test_an_article_is_either_an_unapproved_draft_or_a_published_one(plan):
    """No half state: a draft carries no published_at, and anything approved
    carries a real one that is not in the future."""
    for row in plan:
        assert row["data"]["flagged_uncertainty"] == [], row["id"]
        if row["approved"]:
            assert row["data"]["qa_approved"] is True, row["id"]
            published = datetime.fromisoformat(row["data"]["published_at"])
            assert published <= datetime.now(timezone.utc), row["id"]
        else:
            assert row["data"]["qa_approved"] is False, row["id"]
            assert row["data"]["published_at"] is None, row["id"]


@pytest.mark.parametrize("day", list(range(0, 15)))
def test_the_feed_obeys_every_rule_on_each_day_of_the_schedule(tmp_path, plan, day):
    served = _served_tree(tmp_path / str(day), plan, up_to_day=day)
    now = APPROVED_ON + timedelta(days=day, hours=6)
    xml, report = build_rss(load_entries(served), site_base=SITE, now=now,
                            allowed_campaigns=PINTEREST_CAMPAIGNS)
    root = ET.fromstring(xml)
    assert root.tag == "rss" and root.get("version") == "2.0"
    items = root.find("channel").findall("item")
    assert len(items) <= DEFAULT_MAX_ITEMS
    assert report["items"] == len(items)

    guids, published = [], []
    for item in items:
        guid = item.findtext("guid")
        row = next(r for r in plan if r["id"] == guid.removeprefix("libra-"))
        assert row["lane"] == "pinterest-rss"
        assert row["campaign"] in PINTEREST_CAMPAIGNS
        assert item.findtext("title") == row["data"]["title"]
        assert item.findtext("description") == " ".join(row["data"]["description"].split())
        assert item.findtext("link") == SITE + row["data"]["link"]
        image = item.find("{http://search.yahoo.com/mrss/}content").get("url")
        assert image == SITE + row["data"]["image_url"]
        assert item.find("enclosure").get("url") == image
        published.append(parsedate_to_datetime(item.findtext("pubDate")))
        guids.append(guid)
    assert len(set(guids)) == len(guids)
    assert published == sorted(published)
    assert all(moment <= now for moment in published)


def test_an_unapproved_draft_in_the_served_directory_never_reaches_the_feed(tmp_path, plan):
    served = _served_tree(tmp_path, plan, up_to_day=14)
    _, report = build_rss(load_entries(served), site_base=SITE,
                          now=APPROVED_ON + timedelta(days=20),
                          allowed_campaigns=PINTEREST_CAMPAIGNS)
    rejected = {row["id"]: row["reason"] for row in report["rejected"]}
    assert "not QA-approved" in rejected["adhd-tres-rutinas-es"]


def test_the_linkedin_lane_is_never_pinned(tmp_path, plan, lanes):
    _, owner_post = lanes
    served = _served_tree(tmp_path, plan, up_to_day=14)
    _, report = build_rss(load_entries(served), site_base=SITE,
                          now=APPROVED_ON + timedelta(days=20),
                          allowed_campaigns=PINTEREST_CAMPAIGNS)
    rejected = {row["id"] for row in report["rejected"]}
    assert set(owner_post) <= rejected


def test_the_feed_window_settles_on_the_five_newest_pinterest_articles(tmp_path, plan, lanes):
    pinterest, _ = lanes
    served = _served_tree(tmp_path, plan, up_to_day=14)
    xml, _ = build_rss(load_entries(served), site_base=SITE,
                       now=APPROVED_ON + timedelta(days=20),
                       allowed_campaigns=PINTEREST_CAMPAIGNS)
    served_ids = [item.findtext("guid").removeprefix("libra-")
                  for item in ET.fromstring(xml).find("channel").findall("item")]
    newest = [row["id"] for row in sorted(pinterest.values(), key=lambda r: r["order"])]
    assert served_ids == newest[-DEFAULT_MAX_ITEMS:]


def test_anything_already_served_is_approved_and_belongs_to_a_declared_campaign():
    """Holds before activation (the directory does not exist) and after it (every
    file in it was approved through the runbook)."""
    served = LIBRA_DIR / "data" / "growth_articles"
    for file in served.glob("*.json") if served.is_dir() else []:
        article = json.loads(file.read_text())
        assert article.get("qa_approved") is True, file.name
        assert article.get("campaign") in CAMPAIGNS["campaigns"], file.name
        assert str(article.get("published_at") or "") <= datetime.now(timezone.utc).isoformat(), file.name
