#!/usr/bin/env python3
"""pinterest_evidence.py — what our own web server can prove about Pinterest,
without logging into Pinterest, calling any API, or fetching anything from them.

Everything here is read out of nginx's access log, which the server already
writes. No account, no token, no scraping, no browser. Three signals, weakest to
strongest:

  1. `feed_fetch`    — Pinterestbot fetched /libra/growth/feed.xml. Proves
                       ingestion of the feed, nothing about a Pin.
  2. `render`        — Pinterestbot fetched one article page AND Pinterest's
                       image fetcher (`Pinterest/0.2`) fetched that article's
                       cover image, both after the article was published.
                       Pinterest does this when it materialises a Pin from a feed
                       item, so this is our publication signal.
  3. `referral`      — a real browser (not a fetcher) arrived at that article
                       page with a pinterest.* referrer. Proves a person saw a
                       Pin and clicked it. It is downstream of publication and
                       therefore late, so it confirms rather than starts.

What none of these give us is the Pin's own URL: Pinterest does not tell a
publisher where it put the Pin, and the only supported way to read that is the
Pinterest API with the owner's OAuth token. So a publication recorded from these
signals carries `pin_url: null` and says plainly that the evidence is server-side
rendering, not a Pin we opened. That distinction must never be smoothed over.

Privacy: IP addresses are parsed and discarded. Nothing here stores an IP, a full
user agent string, or a cookie.
"""
from __future__ import annotations

import gzip
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

ACCESS_LOGS = (Path("/var/log/nginx/access.log"), Path("/var/log/nginx/access.log.1"))
# Pinterest's own crawler and image fetcher. Both are documented, public strings.
CRAWLER_MARKER = "pinterestbot"
IMAGE_FETCHER_MARKER = "pinterest/"
DOMAIN_VERIFIER_MARKER = "domain verifier"
PINTEREST_REFERRER_HOSTS = ("pinterest.com", "pinterest.co.uk", "pinterest.es", "pin.it")
FEED_PATH = "/libra/growth/feed.xml"
ARTICLE_PREFIX = "/libra/growth/articles/"
COVER_PREFIX = "/libra/api/books/"
CLAIM_PATH = "/libra/growth"

LINE = re.compile(
    r'^\S+ \S+ \S+ \[(?P<time>[^\]]+)\] "(?P<method>[A-Z]+) (?P<path>[^" ]*) [^"]*" '
    r'(?P<status>\d{3}) \S+ "(?P<referrer>[^"]*)" "(?P<agent>[^"]*)"'
)


def _parse_time(raw: str) -> datetime | None:
    try:
        return datetime.strptime(raw, "%d/%b/%Y:%H:%M:%S %z")
    except ValueError:
        return None


def _open(path: Path):
    if str(path).endswith(".gz"):
        return gzip.open(path, "rt", encoding="utf-8", errors="replace")
    return open(path, "r", encoding="utf-8", errors="replace")


def _referrer_host(referrer: str) -> str:
    if not referrer or referrer == "-":
        return ""
    try:
        return (urlsplit(referrer).hostname or "").lower()
    except ValueError:
        return ""


def _is_pinterest_referrer(host: str) -> bool:
    return any(host == h or host.endswith("." + h) for h in PINTEREST_REFERRER_HOSTS)


def read_events(logs=ACCESS_LOGS, *, since: datetime | None = None) -> list:
    """Every Pinterest-relevant request in the logs, as plain records. An IP is
    never carried out of this function."""
    events = []
    for path in logs:
        path = Path(path)
        if not path.exists():
            continue
        try:
            with _open(path) as handle:
                for raw in handle:
                    match = LINE.match(raw)
                    if not match:
                        continue
                    when = _parse_time(match.group("time"))
                    if when is None or (since and when < since):
                        continue
                    agent = match.group("agent").lower()
                    request_path = match.group("path")
                    referrer_host = _referrer_host(match.group("referrer"))
                    is_crawler = CRAWLER_MARKER in agent
                    is_image_fetcher = IMAGE_FETCHER_MARKER in agent and not is_crawler
                    is_verifier = DOMAIN_VERIFIER_MARKER in agent
                    is_referral = _is_pinterest_referrer(referrer_host) and not (
                        is_crawler or is_image_fetcher or is_verifier)
                    if not (is_crawler or is_image_fetcher or is_verifier or is_referral):
                        continue
                    events.append({
                        "at": when,
                        "path": request_path.split("?", 1)[0],
                        "status": int(match.group("status")),
                        "referrer_host": referrer_host,
                        "kind": ("crawler" if is_crawler else
                                 "image_fetcher" if is_image_fetcher else
                                 "verifier" if is_verifier else "referral"),
                    })
        except OSError:
            # An unreadable log degrades the evidence; it never breaks the run.
            continue
    events.sort(key=lambda event: event["at"])
    return events


def evidence_for(articles: dict, *, logs=ACCESS_LOGS, now: datetime | None = None,
                 feed_stale_after_hours: int = 48) -> dict:
    """`articles` maps article id → {"slug": book slug, "published_at": datetime}.

    Returns per-article evidence plus feed-level ingestion health. Only requests
    that happened after an article was published count for it: a crawler visit
    from before publication says nothing about this Pin."""
    now = now or datetime.now(timezone.utc)
    events = read_events(logs)

    feed_fetches = [e for e in events
                    if e["path"] == FEED_PATH and e["kind"] == "crawler" and e["status"] == 200]
    claim_checks = [e for e in events if e["path"].rstrip("/") == CLAIM_PATH
                    and e["kind"] == "verifier"]

    per_article = {}
    for article_id, meta in articles.items():
        published_at = meta["published_at"]
        article_path = ARTICLE_PREFIX + article_id
        cover_path = f"{COVER_PREFIX}{meta['slug']}/cover"
        after = [e for e in events if e["at"] >= published_at]
        crawls = [e for e in after if e["path"] == article_path
                  and e["kind"] == "crawler" and e["status"] == 200]
        images = [e for e in after if e["path"] == cover_path
                  and e["kind"] == "image_fetcher" and e["status"] == 200]
        referrals = [e for e in after if e["path"] == article_path and e["kind"] == "referral"]
        per_article[article_id] = {
            "slug": meta["slug"],
            "article_crawled_at": crawls[0]["at"].isoformat() if crawls else None,
            "image_fetched_at": images[0]["at"].isoformat() if images else None,
            "first_referral_at": referrals[0]["at"].isoformat() if referrals else None,
            "referrals": len(referrals),
            # Pinterest crawled the destination page and pulled the Pin image
            # after we published it: it built a Pin from the feed item.
            "rendered": bool(crawls and images),
        }

    last_feed_fetch = feed_fetches[-1]["at"] if feed_fetches else None
    return {
        "checked_at": now.isoformat(),
        "feed_fetches": len(feed_fetches),
        "last_feed_fetch": last_feed_fetch.isoformat() if last_feed_fetch else None,
        "feed_ingestion_stale": bool(
            last_feed_fetch is None or now - last_feed_fetch > timedelta(hours=feed_stale_after_hours)),
        "claim_verified_by_pinterest": bool(claim_checks),
        "articles": per_article,
    }


def publication_evidence_text(article_id: str, evidence: dict) -> str:
    """The sentence stored with a publication record. It says what we saw and,
    just as importantly, what we did not see."""
    return (
        f"server-side evidence only: Pinterest crawled "
        f"{ARTICLE_PREFIX}{article_id} at {evidence['article_crawled_at']} and its image "
        f"fetcher pulled the article's cover at {evidence['image_fetched_at']}, both after "
        f"publication — Pinterest materialised a Pin from the feed item. The Pin's own URL "
        f"is not observable to a publisher without the Pinterest API, so no Pin page was "
        f"opened and pin_url stays null."
    )
