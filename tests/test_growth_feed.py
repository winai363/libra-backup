"""Tests for growth_feed.py and posting_authorization.py.

Two things must hold no matter what is in the data directory: nothing is served
unless the owner authorized that one channel, and no entry reaches the feed
unless it is QA-approved, on our own site, and free of book content.

Building XML is not publishing. These tests connect to nothing.
"""
import json
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree

import pytest

import growth_feed
import posting_authorization as auth
from growth_feed import FeedEntryRejected, build_rss, load_entries, select_entries, validate_entry

SITE = "https://example-host.test"
NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def entry(**overrides) -> dict:
    base = {
        "id": "adhd-routines-es",
        "qa_approved": True,
        "title": "Tres rutinas cortas para el TDAH adulto",
        "description": "Una rutina de cinco minutos, una lista de dos columnas y un recordatorio "
                       "visible: tres formas de empezar el dia sin perder el hilo.",
        "link": "/libra/growth/articles/adhd-routines-es",
        "image_url": "/libra/api/books/adhd-adults-workbook-es/cover",
        "published_at": "2026-09-10T08:00:00+00:00",
        "campaign": "pin-adhd-es",
    }
    base.update(overrides)
    return base


# ── entry validation ────────────────────────────────────────────────────────

def test_valid_entry_is_normalised_to_absolute_urls():
    result = validate_entry(entry(), site_base=SITE, now=NOW)

    assert result["link"] == f"{SITE}/libra/growth/articles/adhd-routines-es"
    assert result["image_url"] == f"{SITE}/libra/api/books/adhd-adults-workbook-es/cover"
    assert result["published_at"] == datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("overrides,reason", [
    ({"qa_approved": False}, "not QA-approved"),
    ({"qa_approved": None}, "not QA-approved"),
    ({"title": "short"}, "title length"),
    ({"description": "too short"}, "description length"),
    ({"description": "x" * 501}, "description length"),
    ({"link": "https://evil.test/libra/growth/articles/x"}, "off-site"),
    ({"link": "/admin/secret"}, "path not allowed"),
    ({"image_url": "https://evil.test/libra/api/books/x/cover"}, "off-site"),
    ({"image_url": "/root/kdp/x/cover.jpg"}, "path not allowed"),
    ({"published_at": "not-a-date"}, "ISO-8601"),
    ({"id": ""}, "stable id"),
])
def test_invalid_entries_are_rejected(overrides, reason):
    with pytest.raises(FeedEntryRejected) as error:
        validate_entry(entry(**overrides), site_base=SITE, now=NOW)

    assert reason in str(error.value)


def test_future_dated_entry_is_rejected():
    future = (NOW + timedelta(days=1)).isoformat()

    with pytest.raises(FeedEntryRejected):
        validate_entry(entry(published_at=future), site_base=SITE, now=NOW)


@pytest.mark.parametrize("key", ["manuscript", "ebook_md", "sample_text", "epub", "pdf"])
def test_entry_carrying_book_content_is_rejected(key):
    with pytest.raises(FeedEntryRejected) as error:
        validate_entry(entry(**{key: "chapter one..."}), site_base=SITE, now=NOW)

    assert "book content" in str(error.value)


def test_http_link_is_rejected():
    with pytest.raises(FeedEntryRejected):
        validate_entry(entry(link="http://example-host.test/libra/growth/articles/x"),
                       site_base=SITE, now=NOW)


# ── selection and flood control ─────────────────────────────────────────────

def test_only_the_newest_entries_are_kept_and_ordered_oldest_first():
    entries = [entry(id=f"a{n}", published_at=f"2026-09-0{n}T08:00:00+00:00") for n in range(1, 8)]

    kept, rejected = select_entries(entries, site_base=SITE, now=NOW, max_items=3)

    assert [e["id"] for e in kept] == ["a5", "a6", "a7"]
    assert rejected == []


def test_rejected_entries_are_reported_not_hidden():
    kept, rejected = select_entries([entry(), entry(id="bad", qa_approved=False)],
                                    site_base=SITE, now=NOW)

    assert [e["id"] for e in kept] == ["adhd-routines-es"]
    assert rejected[0]["id"] == "bad"
    assert "not QA-approved" in rejected[0]["reason"]


def test_max_items_above_the_hard_cap_is_refused():
    with pytest.raises(ValueError):
        select_entries([], site_base=SITE, now=NOW, max_items=growth_feed.MAX_ITEMS_HARD + 1)


def test_load_entries_on_a_missing_directory_is_empty(tmp_path):
    assert load_entries(tmp_path / "absent") == []


def test_load_entries_skips_broken_json(tmp_path):
    (tmp_path / "good.json").write_text(json.dumps(entry()))
    (tmp_path / "broken.json").write_text("{not json")

    assert [e["id"] for e in load_entries(tmp_path)] == ["adhd-routines-es"]


def test_load_entries_defaults_the_id_to_the_filename(tmp_path):
    payload = entry()
    payload.pop("id")
    (tmp_path / "from-filename.json").write_text(json.dumps(payload))

    assert load_entries(tmp_path)[0]["id"] == "from-filename"


# ── RSS output ──────────────────────────────────────────────────────────────

def test_feed_is_parseable_rss_2_with_the_fields_pinterest_requires():
    xml, report = build_rss([entry()], site_base=SITE, now=NOW)
    root = ElementTree.fromstring(xml)

    assert root.tag == "rss" and root.attrib["version"] == "2.0"
    item = root.find("./channel/item")
    assert item.findtext("title")
    assert item.findtext("description")
    assert item.findtext("link") == f"{SITE}/libra/growth/articles/adhd-routines-es"
    assert item.findtext("pubDate")
    media = item.find("{http://search.yahoo.com/mrss/}content")
    assert media.attrib["url"].endswith("/cover")
    assert item.find("enclosure").attrib["type"] == "image/jpeg"
    assert report["items"] == 1


def test_guid_is_stable_across_builds():
    first, _ = build_rss([entry()], site_base=SITE, now=NOW)
    second, _ = build_rss([entry()], site_base=SITE, now=NOW + timedelta(hours=3))

    def guid(xml):
        return ElementTree.fromstring(xml).findtext("./channel/item/guid")

    assert guid(first) == guid(second) == "libra-adhd-routines-es"


def test_feed_with_no_approved_entries_is_still_valid_and_empty():
    xml, report = build_rss([entry(qa_approved=False)], site_base=SITE, now=NOW)
    root = ElementTree.fromstring(xml)

    assert root.findall("./channel/item") == []
    assert report["items"] == 0 and len(report["rejected"]) == 1


def test_titles_are_escaped_not_injected():
    xml, _ = build_rss([entry(title="Rutinas & <TDAH> para adultos")], site_base=SITE, now=NOW)
    root = ElementTree.fromstring(xml)

    assert root.findtext("./channel/item/title") == "Rutinas & <TDAH> para adultos"


# ── authorization gate ──────────────────────────────────────────────────────

def authorization(tmp_path, **record) -> "Path":
    path = tmp_path / "posting_authorization.json"
    path.write_text(json.dumps({"channels": {"pinterest-rss": record}}))
    return path


def test_missing_authorization_file_denies(tmp_path):
    assert auth.channel_authorized("pinterest-rss", path=tmp_path / "absent.json") is False


def test_broken_authorization_file_denies(tmp_path):
    path = tmp_path / "posting_authorization.json"
    path.write_text("{not json")

    assert auth.channel_authorized("pinterest-rss", path=path) is False


def test_authorized_flag_without_owner_and_date_denies(tmp_path):
    path = authorization(tmp_path, authorized=True)

    assert auth.channel_authorized("pinterest-rss", path=path) is False


def test_fully_recorded_authorization_allows(tmp_path):
    path = authorization(tmp_path, authorized=True, authorized_by="Bui",
                         authorized_at="2026-09-12T18:00:00+07:00")

    assert auth.channel_authorized("pinterest-rss", path=path) is True


def test_unknown_channel_is_never_authorized(tmp_path):
    path = tmp_path / "posting_authorization.json"
    path.write_text(json.dumps({"channels": {"facebook-groups": {
        "authorized": True, "authorized_by": "Bui", "authorized_at": "2026-09-12"}}}))

    assert auth.channel_authorized("facebook-groups", path=path) is False


def test_the_shipped_authorization_file_never_opens_a_channel_anonymously():
    """The channel was closed until the owner opened it on 2026-09-12, so this can
    no longer assert `false`. What must hold in both states is the fail-closed
    rule: an open channel always says who opened it and when, and a channel is
    open only if the owner's own record says so."""
    record = auth.channel_record("pinterest-rss")
    if record.get("authorized") is True:
        assert str(record.get("authorized_by") or "").strip()
        assert str(record.get("authorized_at") or "").strip()
    assert auth.channel_authorized("pinterest-rss") is (record.get("authorized") is True
                                                        and bool(record.get("authorized_by"))
                                                        and bool(record.get("authorized_at")))


# ── one channel's feed carries only that channel's articles ─────────────────

def test_a_foreign_campaign_is_kept_out_of_the_feed():
    kept, rejected = select_entries(
        [entry(campaign="pin-adhd-es"), entry(id="pt-one", campaign="li-contab-pt")],
        site_base=SITE, now=NOW, allowed_campaigns={"pin-adhd-es"})

    assert [e["id"] for e in kept] == ["adhd-routines-es"]
    assert rejected[0]["id"] == "pt-one"
    assert "does not belong to this feed's channel" in rejected[0]["reason"]


def test_an_entry_without_a_campaign_is_kept_out_when_scoping_is_on():
    payload = entry()
    payload.pop("campaign", None)

    kept, rejected = select_entries([payload], site_base=SITE, now=NOW,
                                    allowed_campaigns={"pin-adhd-es"})

    assert kept == []
    assert "None" in rejected[0]["reason"]


def test_no_scoping_keeps_previous_behaviour():
    kept, _ = select_entries([entry(campaign="anything")], site_base=SITE, now=NOW)

    assert [e["id"] for e in kept] == ["adhd-routines-es"]
