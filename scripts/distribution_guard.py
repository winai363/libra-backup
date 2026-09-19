#!/usr/bin/env python3
"""distribution_guard.py — keeps "Pinterest ingested our feed" apart from "people
saw a Pin and came". Read-only.

Why it exists: Pinterestbot crawling an article and Pinterest's image fetcher
pulling its cover prove that a Pin was built (publication). They say nothing about
whether anyone was shown that Pin. Without this guard, a healthy ingestion log
could be read as a healthy channel.

For every published Pinterest-lane article it reports, from our own nginx log:
  - article age
  - Pinterestbot ingestion confirmed (publication evidence, never traffic)
  - confirmed Pinterest referral visits (a real browser, pinterest referrer or
    Pinterest in-app browser)
  - qualified human visits (unique visitor per article; crawlers, the owner and
    our own test traffic removed)
  - Amazon CTA clicks (a human request to the tracked out-link that redirected)

Per-article classification, each independent:
  CONVERSION_TRAFFIC        at least one Amazon CTA click
  QUALIFIED_TRAFFIC         at least QUALIFIED_TRAFFIC_MIN_VISITS qualified visits
  EARLY_DISTRIBUTION        exactly one qualified visit
  PUBLISHED_NOT_DISTRIBUTED ingestion confirmed, 72h old, no qualified visit
  INSUFFICIENT_DATA         too young, not ingested yet, or the log does not
                            cover the article's whole life (missing data is
                            never reported as zero)

Lane gate: once the scheduled inventory has finished publishing (the last day of
scheduled_pinterest_approval's window), a 72-hour discovery window runs from the
last article's publication. At its end, if qualified human visits across all
Pinterest articles are <= 1 and Amazon CTA clicks are 0, the lane is
EARLY_DISTRIBUTION_FAILURE: not a book failure, not a conversion failure. Only
then the guard runs a read-only diagnosis and sends one Telegram alert. Any other
outcome is recorded silently and the Day-7/14/30 checkpoints carry on unchanged.

Who is excluded, and how:
  crawler  content_hub.is_bot_user_agent (Pinterestbot, Pinterest/0.2, curl, …)
           plus bare "Mozilla/5.0 (compatible)" probes with no browser engine
  test     requests from this server's own addresses
  owner    an address that, inside the same log window, holds an authenticated
           Chat UI session (/ws upgrade, /api/tabs, /api/sessions/). An owner
           visit from an address that never touched the Chat UI in the window
           cannot be recognised — a known limit, stated rather than guessed at.
  synthetic hub_events rows are not read here; nginx sees our TestClient-free
           verification requests as test or crawler traffic.

What it never does: estimate impressions, write an article, the feed, the
experiment file, a book, KDP, Pinterest, a schedule or a campaign; change
strategy; call any API other than plain GETs to our own public pages. The GET to
a tracked out-link uses a bot user agent, which the app redirects without
recording a click. IP addresses are used in memory to classify and are never
stored or printed.

    python3 scripts/distribution_guard.py --status     # read-only snapshot + gate
    python3 scripts/distribution_guard.py --diagnose   # read-only diagnosis, no alert
"""
from __future__ import annotations

import argparse
import html as html_module
import json
import re
import socket
import sys
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin, urlsplit
from zoneinfo import ZoneInfo

LIBRA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRA_DIR))
sys.path.insert(0, str(LIBRA_DIR / "scripts"))

import pinterest_evidence as evidence_module  # noqa: E402
from content_hub import is_bot_user_agent  # noqa: E402
from growth_feed import DESCRIPTION_LIMITS, TITLE_LIMITS  # noqa: E402

SITE_BASE = "https://newton-winai-klinprasom.incomeinclick.in.th"
# The early-distribution gate judges the first scheduled inventory (13-17 Sep
# 2026), once. On 19 Sep the owner extended the publication schedule to add Pin
# volume; articles published after this day must not slide that check forward.
WINDOW_LAST_DAY = date(2026, 9, 17)
SERVED_DIR = LIBRA_DIR / "data" / "growth_articles"
CAMPAIGNS_FILE = LIBRA_DIR / "data" / "growth_campaigns.json"
KDP_DIR = LIBRA_DIR.parent / "kdp"
LOG_DIR = Path("/var/log/nginx")
TIMEZONE = ZoneInfo("Asia/Bangkok")
PINTEREST_LANE = "pinterest-rss"

DISCOVERY_WINDOW = timedelta(hours=72)
QUALIFIED_TRAFFIC_MIN_VISITS = 2
FAILURE_MAX_VISITS = 1
ALERT_KEY = "early_distribution_failure"
USER_AGENT = "LibraDistributionGuard-bot/1.0"

ARTICLE_PREFIX = evidence_module.ARTICLE_PREFIX
CTA_PREFIXES = ("/libra/growth/out/", "/growth/out/")
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
OWNER_SESSION_PATHS = ("/api/tabs", "/api/sessions/")
COUNTED = ("qualified_human_visits", "pinterest_referral_visits", "amazon_cta_clicks",
           "cta_click_errors")

LINE = re.compile(
    r'^(?P<ip>\S+) \S+ \S+ \[(?P<time>[^\]]+)\] "(?P<method>[A-Z]+) (?P<path>[^" ]*) [^"]*" '
    r'(?P<status>\d{3}) \S+ "(?P<referrer>[^"]*)" "(?P<agent>[^"]*)"'
)
PIN_ID = re.compile(r"/pin/(\d+)")


# ── published articles ──────────────────────────────────────────────────────

def load_pinterest_articles(served_dir: Path = SERVED_DIR) -> dict:
    """QA-approved Pinterest-lane articles that are served: id → facts."""
    articles = {}
    for file in sorted(Path(served_dir).glob("*.json")):
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("qa_approved") is not True or data.get("channel") != PINTEREST_LANE:
            continue
        try:
            published = datetime.fromisoformat(str(data.get("published_at")))
        except ValueError:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        articles[data.get("id") or file.stem] = {
            "slug": data.get("target_slug"), "published_at": published,
            "campaign": data.get("campaign"), "language": data.get("language"),
            "title": data.get("title") or "", "description": data.get("description") or "",
        }
    return articles


# ── traffic from our own access log ─────────────────────────────────────────

def default_logs(since: datetime | None = None) -> list:
    """Current and rotated nginx logs, skipping rotations older than `since`."""
    rotated = sorted(LOG_DIR.glob("access.log.*.gz"), key=lambda p: int(p.name.split(".")[2]))
    logs = [LOG_DIR / "access.log", LOG_DIR / "access.log.1"] + rotated
    if since is None:
        return logs
    cutoff = (since - timedelta(days=1)).timestamp()
    return [path for path in logs if path.exists() and path.stat().st_mtime >= cutoff]


def local_addresses() -> set:
    """This server's own addresses. A UDP connect sends no packet; it only asks
    the kernel which source address it would use."""
    addresses = {"127.0.0.1", "::1"}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 9))
            addresses.add(probe.getsockname()[0])
    except OSError:
        pass
    return addresses


def _is_crawler(agent: str) -> bool:
    if is_bot_user_agent(agent):
        return True
    lowered = agent.lower()
    # "Mozilla/5.0 (compatible)" with no engine token is a probe, not a browser.
    return "compatible" in lowered and not any(
        engine in lowered for engine in ("applewebkit", "gecko", "trident", "presto"))


def read_traffic(logs, *, since: datetime, self_addresses: set) -> dict:
    """Relevant requests since `since`, each tagged crawler/test/owner/human.
    Addresses stay inside this function except as an opaque visitor key."""
    owner_addresses, raw_requests, earliest = set(), [], None
    for path in logs:
        path = Path(path)
        if not path.exists():
            continue
        try:
            with evidence_module._open(path) as handle:
                first = True
                for raw in handle:
                    if not first and not ("/growth/" in raw or " /ws " in raw or " /api/" in raw):
                        continue
                    match = LINE.match(raw)
                    if not match:
                        continue
                    when = evidence_module._parse_time(match.group("time"))
                    if when is None:
                        continue
                    if first:
                        earliest = when if earliest is None else min(earliest, when)
                        first = False
                    if when < since:
                        continue
                    request_path = match.group("path").split("?", 1)[0]
                    status = int(match.group("status"))
                    address = match.group("ip")
                    if (request_path == "/ws" and status == 101) or (
                            request_path.startswith(OWNER_SESSION_PATHS) and status == 200):
                        owner_addresses.add(address)
                        continue
                    if not request_path.startswith((ARTICLE_PREFIX,) + CTA_PREFIXES):
                        continue
                    raw_requests.append({
                        "at": when, "method": match.group("method"), "path": request_path,
                        "status": status, "referrer": match.group("referrer"),
                        "agent": match.group("agent"), "address": address,
                    })
        except OSError:
            continue

    requests = []
    for request in raw_requests:
        address = request.pop("address")
        if _is_crawler(request["agent"]):
            source = "crawler"
        elif address in self_addresses:
            source = "test"
        elif address in owner_addresses:
            source = "owner"
        else:
            source = "human"
        request["source"] = source
        request["visitor"] = hash((address, request["agent"]))
        requests.append(request)
    return {"requests": requests, "earliest": earliest}


def _from_pinterest(request: dict) -> bool:
    host = evidence_module._referrer_host(request["referrer"])
    agent = request["agent"].lower()
    return evidence_module._is_pinterest_referrer(host) or ("pinterest" in agent and "mozilla" in agent)


def article_traffic(article_id: str, published_at: datetime, requests: list) -> dict:
    article_path = ARTICLE_PREFIX + article_id
    visitors, referral_visitors = set(), set()
    clicks = errors = 0
    excluded = {"crawler": 0, "owner": 0, "test": 0}
    pins = set()
    for request in requests:
        if request["at"] < published_at:
            continue
        on_article = request["path"] == article_path and request["method"] == "GET"
        cta_from_article = (request["path"].startswith(CTA_PREFIXES)
                            and urlsplit(request["referrer"]).path == article_path)
        if not (on_article or cta_from_article):
            continue
        pins.update(PIN_ID.findall(request["referrer"]))
        if request["source"] != "human":
            excluded[request["source"]] += 1
            continue
        if on_article:
            if 200 <= request["status"] < 400:
                visitors.add(request["visitor"])
                if _from_pinterest(request):
                    referral_visitors.add(request["visitor"])
        elif request["status"] in REDIRECT_STATUSES:
            clicks += 1
        else:
            errors += 1
    return {"qualified_human_visits": len(visitors),
            "pinterest_referral_visits": len(referral_visitors),
            "amazon_cta_clicks": clicks, "cta_click_errors": errors,
            "excluded": excluded, "pin_ids_in_referrers": sorted(pins)}


def classify(*, age_hours: float, ingested: bool, visits, clicks, coverage_complete: bool) -> str:
    if not coverage_complete or visits is None:
        return "INSUFFICIENT_DATA"
    if clicks >= 1:
        return "CONVERSION_TRAFFIC"
    if visits >= QUALIFIED_TRAFFIC_MIN_VISITS:
        return "QUALIFIED_TRAFFIC"
    if visits == 1:
        return "EARLY_DISTRIBUTION"
    if ingested and age_hours >= DISCOVERY_WINDOW.total_seconds() / 3600:
        return "PUBLISHED_NOT_DISTRIBUTED"
    return "INSUFFICIENT_DATA"


def snapshot(articles: dict, *, now: datetime | None = None, logs=None,
             self_addresses: set | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if not articles:
        return {"state": "no_published_articles", "articles": {}}
    since = min(meta["published_at"] for meta in articles.values())
    logs = list(logs) if logs is not None else default_logs(since)
    self_addresses = local_addresses() if self_addresses is None else self_addresses
    traffic = read_traffic(logs, since=since, self_addresses=self_addresses)
    evidence = evidence_module.evidence_for(
        {key: {"slug": meta["slug"], "published_at": meta["published_at"]}
         for key, meta in articles.items()}, logs=logs, now=now)
    coverage_complete = traffic["earliest"] is not None and traffic["earliest"] <= since

    per_article = {}
    for article_id, meta in sorted(articles.items()):
        stats = article_traffic(article_id, meta["published_at"], traffic["requests"])
        article_evidence = evidence["articles"][article_id]
        ingested = article_evidence["article_crawled_at"] is not None
        age_hours = round((now - meta["published_at"]).total_seconds() / 3600, 1)
        if not coverage_complete:
            # A log that does not reach back to publication cannot prove a zero.
            stats.update({key: None for key in COUNTED})
            stats["excluded"] = None
        per_article[article_id] = {
            "slug": meta["slug"], "published_at": meta["published_at"].isoformat(),
            "age_hours": age_hours,
            "pinterestbot_ingestion_confirmed": ingested,
            "pinterestbot_crawled_at": article_evidence["article_crawled_at"],
            "pin_image_fetched_at": article_evidence["image_fetched_at"],
            **stats,
            "classification": classify(age_hours=age_hours, ingested=ingested,
                                       visits=stats["qualified_human_visits"],
                                       clicks=stats["amazon_cta_clicks"],
                                       coverage_complete=coverage_complete),
        }

    totals = {key: (sum(a[key] for a in per_article.values()) if coverage_complete else None)
              for key in COUNTED}
    totals["ingested_articles"] = sum(a["pinterestbot_ingestion_confirmed"]
                                      for a in per_article.values())
    totals["excluded"] = ({kind: sum(a["excluded"][kind] for a in per_article.values())
                           for kind in ("crawler", "owner", "test")}
                          if coverage_complete else None)
    return {
        "state": "ok", "checked_at": now.isoformat(), "log_coverage_complete": coverage_complete,
        "log_earliest": traffic["earliest"].isoformat() if traffic["earliest"] else None,
        "impressions": "not measured (Pinterest analytics are not read)",
        "feed": {key: evidence[key] for key in ("feed_fetches", "last_feed_fetch",
                                                 "feed_ingestion_stale",
                                                 "claim_verified_by_pinterest")},
        "articles": per_article, "totals": totals,
    }


# ── lane gate ───────────────────────────────────────────────────────────────

def discovery_window_end(articles: dict, now: datetime) -> datetime | None:
    """None while the scheduled inventory is still publishing; otherwise 72h after
    the last article's publication."""
    published = [meta["published_at"] for meta in articles.values()]
    first_inventory = [moment for moment in published
                       if moment.astimezone(TIMEZONE).date() <= WINDOW_LAST_DAY]
    latest = max(first_inventory or published)
    finished = (now.astimezone(TIMEZONE).date() > WINDOW_LAST_DAY
                or latest.astimezone(TIMEZONE).date() >= WINDOW_LAST_DAY)
    return latest + DISCOVERY_WINDOW if finished else None


def run(state: dict, *, alert, served_dir: Path = SERVED_DIR, campaigns_file: Path = CAMPAIGNS_FILE,
        kdp_dir: Path = KDP_DIR, articles: dict | None = None, now: datetime | None = None,
        logs=None, self_addresses: set | None = None, fetch=None) -> dict:
    """One hourly pass. Silent until the discovery window closes, evaluates once,
    alerts only on EARLY_DISTRIBUTION_FAILURE. Only `state` is written (by the caller)."""
    now = now or datetime.now(timezone.utc)
    guard = state.setdefault("distribution_guard", {})
    articles = load_pinterest_articles(served_dir) if articles is None else articles
    if not articles:
        return {"state": "no_published_articles"}
    window_end = discovery_window_end(articles, now)
    if window_end is None:
        return {"state": "inventory_publishing", "last_scheduled_day": str(WINDOW_LAST_DAY)}
    if now < window_end:
        return {"state": "discovery_window", "ends_at": window_end.isoformat()}
    if guard.get("evaluated_at"):
        return {"state": "evaluated", "result": guard.get("result")}

    snap = snapshot(articles, now=now, logs=logs, self_addresses=self_addresses)
    if not snap["log_coverage_complete"]:
        # Unknown is not zero: no verdict, no alert, try again next run.
        return {"state": "INSUFFICIENT_DATA", "reason": "access log does not cover the lane"}

    totals = snap["totals"]
    failed = (totals["qualified_human_visits"] <= FAILURE_MAX_VISITS
              and totals["amazon_cta_clicks"] == 0)
    record = {
        "evaluated_at": now.isoformat(), "window_end": window_end.isoformat(),
        "result": "EARLY_DISTRIBUTION_FAILURE" if failed else "DISTRIBUTION_OBSERVED",
        "totals": totals,
        "articles": {key: {k: value[k] for k in ("classification", "age_hours",
                                                   "pinterestbot_ingestion_confirmed", *COUNTED)}
                     for key, value in snap["articles"].items()},
    }
    if failed:
        diagnosis = diagnose(articles, snap, fetch=fetch, campaigns_file=campaigns_file,
                             kdp_dir=kdp_dir)
        record["diagnosis"] = diagnosis
        sent = alert(ALERT_KEY, format_alert(snap, diagnosis, window_end)) or \
            bool(state.get("alerts_sent", {}).get(ALERT_KEY))
        if not sent:
            return {"state": record["result"], "alert_sent": False}
    guard.update(record)
    return {"state": record["result"], "totals": totals}


# ── read-only diagnosis ─────────────────────────────────────────────────────

class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_get(url: str, limit: int = 600_000):
    """GET without following redirects: (status | None, lowercase headers, body)."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=15) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, \
                response.read(limit)
    except urllib.error.HTTPError as error:
        return error.code, {k.lower(): v for k, v in (error.headers or {}).items()}, b""
    except (urllib.error.URLError, OSError) as error:
        return None, {"error": str(error)}, b""


def _attr(page: str, pattern: str):
    match = re.search(pattern, page, re.IGNORECASE)
    return html_module.unescape(match.group(1)) if match else None


def _asin(kdp_dir: Path, slug) -> str | None:
    try:
        return json.loads((Path(kdp_dir) / str(slug) / "listing.json").read_text()).get("asin")
    except (OSError, json.JSONDecodeError):
        return None


def _robots_blocks(text: str, path: str) -> bool:
    agents, blocked, in_rules = [], False, False
    for line in text.splitlines():
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        key = key.lower()
        if key == "user-agent":
            if in_rules:
                agents, in_rules = [], False
            agents.append(value.lower())
        elif key == "disallow":
            in_rules = True
            if value and path.startswith(value) and any(
                    agent in ("*", "pinterest", "pinterestbot") for agent in agents):
                blocked = True
    return blocked


def _check(name: str, ok, detail: str) -> dict:
    return {"check": name, "ok": ok, "detail": detail}


def diagnose(articles: dict, snap: dict, *, fetch=None, site_base: str = SITE_BASE,
             campaigns_file: Path = CAMPAIGNS_FILE, kdp_dir: Path = KDP_DIR) -> list:
    fetch = fetch or http_get
    checks = []
    per = snap["articles"]

    lags = {}
    for article_id, facts in per.items():
        crawled = facts["pinterestbot_crawled_at"]
        lags[article_id] = (round((datetime.fromisoformat(crawled)
                                   - datetime.fromisoformat(facts["published_at"])
                                   ).total_seconds() / 3600, 1) if crawled else None)
    missing = [key for key, lag in lags.items() if lag is None]
    feed = snap["feed"]
    checks.append(_check(
        "rss_ingestion_timing", not missing and not feed["feed_ingestion_stale"],
        f"publish→crawl hours {sorted(lag for lag in lags.values() if lag is not None)}; "
        f"not crawled {missing or 'none'}; feed fetches {feed['feed_fetches']}, "
        f"last {feed['last_feed_fetch']}"))

    feed_status, _, feed_body = fetch(site_base + evidence_module.FEED_PATH)
    feed_links = set(re.findall(r"<link>([^<]+)</link>", feed_body.decode("utf-8", "replace")))
    index_status = fetch(site_base + "/libra/growth")[0]
    pages = {}
    for article_id in articles:
        url = site_base + ARTICLE_PREFIX + article_id
        status, headers, body = fetch(url)
        pages[article_id] = (url, status, headers, body.decode("utf-8", "replace"))
    not_ok = [key for key, page in pages.items() if page[1] != 200]
    checks.append(_check(
        "article_index_accessibility", feed_status == 200 and index_status == 200 and not not_ok,
        f"feed HTTP {feed_status}, index HTTP {index_status}, articles not 200: {not_ok or 'none'}"))

    destination_problems, image_problems, metadata_problems, noindex = [], [], [], []
    for article_id, (url, status, headers, page) in pages.items():
        meta = articles[article_id]
        if url not in feed_links and not per[article_id]["pinterestbot_ingestion_confirmed"]:
            destination_problems.append(f"{article_id}: not in feed and never crawled")
        if _attr(page, r'<link rel="canonical" href="([^"]*)"') != url or \
                _attr(page, r'<meta property="og:url" content="([^"]*)"') != url:
            destination_problems.append(f"{article_id}: canonical/og:url is not the article URL")
        href = _attr(page, r'<a class="cta" href="([^"]*)"')
        if not href:
            destination_problems.append(f"{article_id}: no CTA link")
        else:
            cta_status, cta_headers, _ = fetch(urljoin(url, href))
            expected = f"https://www.amazon.com/dp/{_asin(kdp_dir, meta['slug'])}"
            if cta_status not in REDIRECT_STATUSES or cta_headers.get("location") != expected:
                destination_problems.append(
                    f"{article_id}: CTA {urlsplit(urljoin(url, href)).path[:22]}… → HTTP "
                    f"{cta_status} (expected redirect to {expected})")

        image = _attr(page, r'<meta property="og:image" content="([^"]*)"')
        image_status, image_headers, image_body = fetch(image) if image else (None, {}, b"")
        if not (image_status == 200 and image_headers.get("content-type", "").startswith("image/")
                and image_body):
            image_problems.append(f"{article_id}: og:image HTTP {image_status}")
        if not per[article_id]["pin_image_fetched_at"]:
            image_problems.append(f"{article_id}: Pinterest image fetch not seen")

        if not TITLE_LIMITS[0] <= len(meta["title"]) <= TITLE_LIMITS[1]:
            metadata_problems.append(f"{article_id}: title length {len(meta['title'])}")
        if not DESCRIPTION_LIMITS[0] <= len(" ".join(meta["description"].split())) <= DESCRIPTION_LIMITS[1]:
            metadata_problems.append(f"{article_id}: description length")
        for label, pattern in (("og:title", r'<meta property="og:title" content="([^"]*)"'),
                               ("og:description", r'<meta property="og:description" content="([^"]*)"'),
                               ("meta description", r'<meta name="description" content="([^"]*)"')):
            if not (_attr(page, pattern) or "").strip():
                metadata_problems.append(f"{article_id}: empty {label}")
        lang = _attr(page, r'<html[^>]*\blang="([^"]*)"')
        if meta.get("language") and lang != meta["language"]:
            metadata_problems.append(f"{article_id}: html lang {lang} ≠ {meta['language']}")

        robots_meta = _attr(page, r'<meta name="robots" content="([^"]*)"') or ""
        if "noindex" in robots_meta.lower() or "noindex" in headers.get("x-robots-tag", "").lower():
            noindex.append(article_id)

    checks.append(_check("pin_destination_correctness", not destination_problems,
                         "; ".join(destination_problems) or
                         "feed link, canonical, og:url and CTA redirect all match"))
    checks.append(_check("image_availability", not image_problems,
                         "; ".join(image_problems) or "og:image 200 image/* and fetched by Pinterest"))
    checks.append(_check("title_description_metadata", not metadata_problems,
                         "; ".join(metadata_problems) or "titles, descriptions, og tags and lang present"))

    try:
        campaigns = json.loads(Path(campaigns_file).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        campaigns = {}
    declared = set(campaigns.get("campaigns") or [])
    mapping = campaigns.get("channels") or {}
    unmapped = [key for key, meta in articles.items()
                if meta.get("campaign") not in declared or mapping.get(meta.get("campaign")) != PINTEREST_LANE]
    checks.append(_check("campaign_mapping", not unmapped,
                         f"not declared/mapped to {PINTEREST_LANE}: {unmapped or 'none'}"))

    robots_status, _, robots_body = fetch(site_base + "/robots.txt")
    robots_block = robots_status == 200 and _robots_blocks(
        robots_body.decode("utf-8", "replace"), ARTICLE_PREFIX)
    checks.append(_check(
        "robots_indexability", not robots_block and not noindex,
        f"robots.txt HTTP {robots_status}"
        f"{' blocks articles' if robots_block else ' (nothing disallowed)'}; "
        f"noindex pages: {noindex or 'none'}"))

    totals = snap["totals"]
    pins = sorted({pin for facts in per.values() for pin in (facts.get("pin_ids_in_referrers") or [])})
    checks.append(_check(
        "pinterest_referral_evidence", bool(totals["pinterest_referral_visits"]),
        f"qualified Pinterest referral visits {totals['pinterest_referral_visits']}; "
        f"Pin ids seen in referrers (any source) {len(pins)}; excluded {totals['excluded']}"))
    return checks


def format_alert(snap: dict, diagnosis: list, window_end: datetime) -> str:
    totals = snap["totals"]
    passed = sum(1 for check in diagnosis if check["ok"])
    lines = [
        "🟠 Libra Pinterest: EARLY_DISTRIBUTION_FAILURE",
        f"72h discovery window ended {window_end.astimezone(TIMEZONE):%d %b %H:%M} (+07).",
        f"{len(snap['articles'])} articles · qualified human visits "
        f"{totals['qualified_human_visits']} · Pinterest referrals "
        f"{totals['pinterest_referral_visits']} · Amazon CTA clicks {totals['amazon_cta_clicks']}",
        f"Pinterestbot ingested {totals['ingested_articles']}/{len(snap['articles'])} "
        "(publication evidence, not audience). Impressions not measured.",
        "Not a book failure, not a conversion failure. Nothing was changed.",
        f"Read-only diagnosis — {passed}/{len(diagnosis)} OK:",
    ]
    for check in diagnosis:
        mark = "✅" if check["ok"] else "❌"
        lines.append(f"{mark} {check['check']}: {check['detail'][:180]}")
    lines.append("Day-7/14/30 checkpoints continue unchanged.")
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--status", action="store_true", help="read-only snapshot and gate state")
    group.add_argument("--diagnose", action="store_true", help="read-only diagnosis, no alert")
    args = parser.parse_args(argv)
    now = datetime.now(timezone.utc)
    articles = load_pinterest_articles()
    snap = snapshot(articles, now=now)
    if args.status:
        window_end = discovery_window_end(articles, now) if articles else None
        snap["gate"] = {"last_scheduled_day": str(WINDOW_LAST_DAY),
                        "discovery_window_ends_at": window_end.isoformat() if window_end else None}
        print(json.dumps(snap, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(diagnose(articles, snap), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
