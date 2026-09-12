"""Unit tests for scripts/pinterest_evidence.py — what our own nginx log can and
cannot prove about Pinterest.

The distinction under test is the one that matters for honesty: a feed fetch
proves ingestion, a crawl plus an image fetch after publication proves Pinterest
built a Pin, a referral proves a person clicked one, and none of them yields the
Pin's URL."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import pinterest_evidence as evidence  # noqa: E402

PUBLISHED = datetime(2026, 9, 12, 6, 18, tzinfo=timezone.utc)
ARTICLE = "adhd-lista-dos-columnas-es"
SLUG = "adhd-adults-workbook-es"
CRAWLER = "Mozilla/5.0 (compatible; Pinterestbot/1.0; +http://www.pinterest.com/bot.html)"
IMAGE_FETCHER = "Pinterest/0.2 (+http://www.pinterest.com/)"
VERIFIER = "Domain Verifier"
BROWSER = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 "
           "(KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1")


def line(path, agent, *, when="12/Sep/2026:13:45:39 +0700", status=200, referrer="-"):
    return (f'203.0.113.7 - - [{when}] "GET {path} HTTP/1.1" {status} 1447 '
            f'"{referrer}" "{agent}"')


@pytest.fixture
def log(tmp_path):
    def write(lines):
        path = tmp_path / "access.log"
        path.write_text("\n".join(lines) + "\n")
        return (path,)
    return write


def articles():
    return {ARTICLE: {"slug": SLUG, "published_at": PUBLISHED}}


def test_a_rendered_pin_needs_both_the_crawl_and_the_image(log):
    logs = log([
        line(evidence.FEED_PATH, CRAWLER),
        line(f"{evidence.ARTICLE_PREFIX}{ARTICLE}", CRAWLER),
        line(f"{evidence.COVER_PREFIX}{SLUG}/cover", IMAGE_FETCHER,
             when="12/Sep/2026:13:45:50 +0700"),
    ])
    result = evidence.evidence_for(articles(), logs=logs)
    article = result["articles"][ARTICLE]
    assert article["rendered"] is True
    assert article["article_crawled_at"] and article["image_fetched_at"]
    assert result["feed_fetches"] == 1


def test_a_crawl_without_an_image_fetch_is_not_a_publication(log):
    logs = log([line(f"{evidence.ARTICLE_PREFIX}{ARTICLE}", CRAWLER)])
    assert evidence.evidence_for(articles(), logs=logs)["articles"][ARTICLE]["rendered"] is False


def test_an_image_fetch_without_a_crawl_is_not_a_publication(log):
    logs = log([line(f"{evidence.COVER_PREFIX}{SLUG}/cover", IMAGE_FETCHER)])
    assert evidence.evidence_for(articles(), logs=logs)["articles"][ARTICLE]["rendered"] is False


def test_requests_from_before_publication_never_count(log):
    logs = log([
        line(f"{evidence.ARTICLE_PREFIX}{ARTICLE}", CRAWLER, when="11/Sep/2026:09:00:00 +0700"),
        line(f"{evidence.COVER_PREFIX}{SLUG}/cover", IMAGE_FETCHER,
             when="11/Sep/2026:09:00:10 +0700"),
    ])
    assert evidence.evidence_for(articles(), logs=logs)["articles"][ARTICLE]["rendered"] is False


def test_a_failed_fetch_does_not_count_as_evidence(log):
    logs = log([
        line(f"{evidence.ARTICLE_PREFIX}{ARTICLE}", CRAWLER, status=404),
        line(f"{evidence.COVER_PREFIX}{SLUG}/cover", IMAGE_FETCHER, status=500),
    ])
    assert evidence.evidence_for(articles(), logs=logs)["articles"][ARTICLE]["rendered"] is False


def test_another_articles_evidence_is_not_borrowed(log):
    logs = log([
        line(f"{evidence.ARTICLE_PREFIX}some-other-article", CRAWLER),
        line(f"{evidence.COVER_PREFIX}another-book/cover", IMAGE_FETCHER),
    ])
    assert evidence.evidence_for(articles(), logs=logs)["articles"][ARTICLE]["rendered"] is False


def test_a_human_click_from_pinterest_is_recorded_as_a_referral(log):
    logs = log([line(f"{evidence.ARTICLE_PREFIX}{ARTICLE}", BROWSER,
                     referrer="https://www.pinterest.com/pin/12345/")])
    article = evidence.evidence_for(articles(), logs=logs)["articles"][ARTICLE]
    assert article["referrals"] == 1
    assert article["first_referral_at"]
    # A referral is downstream of publication; on its own it is not the render signal.
    assert article["rendered"] is False


def test_a_referral_from_somewhere_else_is_ignored(log):
    logs = log([line(f"{evidence.ARTICLE_PREFIX}{ARTICLE}", BROWSER,
                     referrer="https://www.notpinterest.com/x")])
    assert evidence.evidence_for(articles(), logs=logs)["articles"][ARTICLE]["referrals"] == 0


def test_the_domain_verifier_is_recognised(log):
    logs = log([line(evidence.CLAIM_PATH, VERIFIER)])
    assert evidence.evidence_for(articles(), logs=logs)["claim_verified_by_pinterest"] is True


def test_feed_ingestion_goes_stale_after_the_threshold(log):
    logs = log([line(evidence.FEED_PATH, CRAWLER, when="10/Sep/2026:13:45:39 +0700")])
    now = datetime(2026, 9, 13, 12, 0, tzinfo=timezone.utc)
    result = evidence.evidence_for(articles(), logs=logs, now=now)
    assert result["feed_ingestion_stale"] is True
    fresh = evidence.evidence_for(articles(), logs=logs, now=now,
                                  feed_stale_after_hours=24 * 30)
    assert fresh["feed_ingestion_stale"] is False


def test_no_feed_fetch_at_all_reads_as_stale(log):
    assert evidence.evidence_for(articles(), logs=log(["garbage line"]))["feed_ingestion_stale"]


def test_a_missing_log_file_degrades_instead_of_raising(tmp_path):
    result = evidence.evidence_for(articles(), logs=(tmp_path / "nope.log",))
    assert result["feed_fetches"] == 0
    assert result["articles"][ARTICLE]["rendered"] is False


def test_no_ip_address_survives_the_parser(log):
    logs = log([line(evidence.FEED_PATH, CRAWLER)])
    for event in evidence.read_events(logs):
        assert "203.0.113.7" not in str(event)
        assert set(event) == {"at", "path", "status", "referrer_host", "kind"}


def test_the_evidence_sentence_says_what_was_not_seen():
    text = evidence.publication_evidence_text(ARTICLE, {
        "article_crawled_at": "2026-09-12T13:45:39+07:00",
        "image_fetched_at": "2026-09-12T13:45:50+07:00"})
    assert "pin_url stays null" in text
    assert "no Pin page was opened" in text
