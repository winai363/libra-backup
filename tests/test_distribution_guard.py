"""Unit tests for scripts/distribution_guard.py.

What must hold: Pinterestbot activity is publication evidence and never traffic;
owner, test and crawler requests are never counted; missing log coverage is never
a zero; the lane waits for the scheduled inventory and a 72-hour window; it alerts
once, only on EARLY_DISTRIBUTION_FAILURE, with a read-only diagnosis; and it
writes nothing outside the state dict it is handed."""
import hashlib
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import distribution_guard as guard  # noqa: E402

SITE = guard.SITE_BASE
FIRST = datetime(2026, 9, 12, 6, 18, tzinfo=timezone.utc)
LAST = datetime(2026, 9, 17, 2, 0, tzinfo=timezone.utc)  # 09:00 +07 on the last scheduled day
WINDOW_END = LAST + timedelta(hours=72)
BROWSER = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 "
           "(KHTML, like Gecko) Mobile/15E148 Pinterest/iOS")
DESKTOP = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36"
PINBOT = "Mozilla/5.0 (compatible; Pinterestbot/1.0; +http://www.pinterest.com/bot.html)"
PIN_IMAGE = "Pinterest/0.2 (+http://www.pinterest.com/)"
SELF = "5.5.5.5"
OWNER = "7.7.7.7"


def article(published_at, slug="book-a", campaign="pin-adhd-es"):
    return {"slug": slug, "published_at": published_at, "campaign": campaign, "language": "es",
            "title": "La lista de dos columnas", "description": "Una hoja partida en dos para "
            "planificar la mañana con calma."}


def line(ip, when, path, status=200, referrer="-", agent=DESKTOP, method="GET"):
    stamp = when.strftime("%d/%b/%Y:%H:%M:%S %z")
    return f'{ip} - - [{stamp}] "{method} {path} HTTP/2.0" {status} 100 "{referrer}" "{agent}"\n'


def write_log(tmp_path, lines, start=FIRST - timedelta(hours=1)):
    log = tmp_path / "access.log"
    log.write_text(line("9.9.9.9", start, "/") + "".join(lines))
    return [log]


def ingestion(article_id, when, slug="book-a"):
    return [line("54.236.1.1", when, f"/libra/growth/articles/{article_id}", agent=PINBOT),
            line("54.236.1.2", when + timedelta(seconds=5), f"/libra/api/books/{slug}/cover",
                 agent=PIN_IMAGE)]


def visit(ip, when, article_id, agent=BROWSER, referrer="https://www.pinterest.com/pin/123456/"):
    return line(ip, when, f"/libra/growth/articles/{article_id}", referrer=referrer, agent=agent)


def snap(articles, lines, tmp_path, now):
    return guard.snapshot(articles, now=now, logs=write_log(tmp_path, lines),
                          self_addresses={SELF})


# ── per-article measurement ─────────────────────────────────────────────────

def test_pinterestbot_is_publication_evidence_not_traffic(tmp_path):
    result = snap({"a1": article(FIRST)}, ingestion("a1", FIRST + timedelta(hours=7)), tmp_path,
                  FIRST + timedelta(hours=80))
    facts = result["articles"]["a1"]
    assert facts["pinterestbot_ingestion_confirmed"] is True
    assert facts["qualified_human_visits"] == 0
    assert facts["excluded"]["crawler"] == 1
    assert facts["classification"] == "PUBLISHED_NOT_DISTRIBUTED"
    assert "not measured" in result["impressions"]


def test_young_article_without_visits_is_insufficient_data(tmp_path):
    result = snap({"a1": article(FIRST)}, ingestion("a1", FIRST + timedelta(hours=7)), tmp_path,
                  FIRST + timedelta(hours=10))
    assert result["articles"]["a1"]["classification"] == "INSUFFICIENT_DATA"


def test_owner_test_and_crawler_visits_are_not_counted(tmp_path):
    when = FIRST + timedelta(hours=20)
    lines = [line(OWNER, when, "/ws", status=101),
             visit(OWNER, when, "a1"),
             visit(SELF, when, "a1", agent=DESKTOP, referrer="-"),
             visit("8.8.8.8", when, "a1", agent="curl/8.5.0", referrer="-"),
             visit("8.8.4.4", when, "a1", agent="Mozilla/5.0 (compatible)", referrer="-")]
    facts = snap({"a1": article(FIRST)}, lines, tmp_path, FIRST + timedelta(hours=80))["articles"]["a1"]
    assert facts["qualified_human_visits"] == 0
    assert facts["excluded"] == {"crawler": 2, "owner": 1, "test": 1}
    assert facts["pin_ids_in_referrers"] == ["123456"]


def test_human_traffic_classifies_early_qualified_and_conversion(tmp_path):
    when = FIRST + timedelta(hours=20)
    one = snap({"a1": article(FIRST)}, [visit("1.1.1.1", when, "a1"), visit("1.1.1.1", when, "a1")],
               tmp_path, FIRST + timedelta(hours=80))["articles"]["a1"]
    assert (one["qualified_human_visits"], one["pinterest_referral_visits"]) == (1, 1)
    assert one["classification"] == "EARLY_DISTRIBUTION"

    two = snap({"a1": article(FIRST)}, [visit("1.1.1.1", when, "a1"),
                                        visit("2.2.2.2", when, "a1", agent=DESKTOP, referrer="-")],
               tmp_path, FIRST + timedelta(hours=80))["articles"]["a1"]
    assert (two["qualified_human_visits"], two["pinterest_referral_visits"]) == (2, 1)
    assert two["classification"] == "QUALIFIED_TRAFFIC"

    click = line("1.1.1.1", when, "/libra/growth/out/tok", status=307, agent=BROWSER,
                 referrer=f"{SITE}/libra/growth/articles/a1")
    broken = line("3.3.3.3", when, "/growth/out/tok", status=404, agent=BROWSER,
                  referrer=f"{SITE}/libra/growth/articles/a1")
    three = snap({"a1": article(FIRST)}, [visit("1.1.1.1", when, "a1"), click, broken],
                 tmp_path, FIRST + timedelta(hours=80))["articles"]["a1"]
    assert (three["amazon_cta_clicks"], three["cta_click_errors"]) == (1, 1)
    assert three["classification"] == "CONVERSION_TRAFFIC"


def test_missing_log_coverage_is_never_zero(tmp_path):
    late_log = tmp_path / "access.log"
    late_log.write_text(line("1.1.1.1", FIRST + timedelta(days=2), "/"))
    result = guard.snapshot({"a1": article(FIRST)}, now=FIRST + timedelta(days=3),
                            logs=[late_log], self_addresses=set())
    assert result["log_coverage_complete"] is False
    assert result["articles"]["a1"]["qualified_human_visits"] is None
    assert result["totals"]["amazon_cta_clicks"] is None
    assert result["articles"]["a1"]["classification"] == "INSUFFICIENT_DATA"


def test_output_never_carries_an_address(tmp_path):
    when = FIRST + timedelta(hours=20)
    result = snap({"a1": article(FIRST)}, [line(OWNER, when, "/ws", status=101),
                                           visit("1.1.1.1", when, "a1"), visit(OWNER, when, "a1")],
                  tmp_path, FIRST + timedelta(hours=80))
    text = json.dumps(result)
    assert "1.1.1.1" not in text and OWNER not in text


# ── lane gate ───────────────────────────────────────────────────────────────

def site(cta_href="/libra/growth/out/tok", robots_status=404):
    calls = []

    def fetch(url):
        calls.append(url)
        path = urlsplit(url).path
        if path == "/libra/growth/feed.xml":
            return 200, {}, f"<link>{SITE}/libra/growth/articles/a1</link>".encode()
        if path == "/libra/growth":
            return 200, {}, b"<html></html>"
        if path.startswith("/libra/growth/articles/"):
            article_id = path.rsplit("/", 1)[1]
            url_ = f"{SITE}/libra/growth/articles/{article_id}"
            return 200, {}, (
                f'<html lang="es"><head><meta name="description" content="Una hoja partida.">'
                f'<link rel="canonical" href="{url_}"><meta property="og:title" content="T">'
                f'<meta property="og:description" content="D">'
                f'<meta property="og:image" content="{SITE}/libra/api/books/book-a/cover">'
                f'<meta property="og:url" content="{url_}"></head>'
                f'<body><a class="cta" href="{cta_href}" rel="nofollow">View</a></body></html>'
            ).encode()
        if path == "/libra/growth/out/tok":
            return 307, {"location": "https://www.amazon.com/dp/B0BOOKAAA1"}, b""
        if path.startswith("/libra/api/books/"):
            return 200, {"content-type": "image/jpeg"}, b"\xff\xd8"
        if path == "/robots.txt":
            return robots_status, {}, b"User-agent: *\nDisallow: /libra/growth/\n"
        return 404, {}, b""

    fetch.calls = calls
    return fetch


@pytest.fixture
def lane(tmp_path):
    kdp = tmp_path / "kdp" / "book-a"
    kdp.mkdir(parents=True)
    (kdp / "listing.json").write_text(json.dumps({"asin": "B0BOOKAAA1"}))
    campaigns = tmp_path / "growth_campaigns.json"
    campaigns.write_text(json.dumps({"campaigns": ["pin-adhd-es"],
                                     "channels": {"pin-adhd-es": "pinterest-rss"}}))
    articles = {"a1": article(FIRST), "a2": article(LAST)}
    lines = ingestion("a1", FIRST + timedelta(hours=7)) + ingestion("a2", LAST + timedelta(hours=6))
    lines.append(line("54.236.1.1", WINDOW_END - timedelta(hours=2), "/libra/growth/feed.xml",
                      agent=PINBOT))
    return {"articles": articles, "lines": lines, "kdp": tmp_path / "kdp", "campaigns": campaigns,
            "tmp": tmp_path}


def run_guard(lane, state, now, alerts, *, lines=None, fetch=None, send_ok=True):
    def alert(key, message):
        alerts.append((key, message))
        return send_ok
    return guard.run(state, alert=alert, articles=lane["articles"], now=now,
                     logs=write_log(lane["tmp"], lane["lines"] if lines is None else lines),
                     self_addresses={SELF}, fetch=fetch or site(), kdp_dir=lane["kdp"],
                     campaigns_file=lane["campaigns"])


def test_waits_for_inventory_then_for_the_discovery_window(lane):
    state, alerts = {}, []
    early = {"a1": article(FIRST)}
    lane_early = {**lane, "articles": early}
    assert run_guard(lane_early, state, FIRST + timedelta(days=2), alerts)["state"] == \
        "inventory_publishing"
    assert run_guard(lane, state, LAST + timedelta(hours=30), alerts)["state"] == "discovery_window"
    assert alerts == [] and "evaluated_at" not in state["distribution_guard"]


def test_failure_after_window_alerts_once_with_diagnosis(lane):
    state, alerts = {}, []
    one_human = lane["lines"] + [visit("1.1.1.1", LAST + timedelta(hours=10), "a2")]
    result = run_guard(lane, state, WINDOW_END + timedelta(minutes=5), alerts, lines=one_human)
    assert result["state"] == "EARLY_DISTRIBUTION_FAILURE"
    assert len(alerts) == 1 and alerts[0][0] == guard.ALERT_KEY
    message = alerts[0][1]
    assert "not a book failure" in message.lower() and "Impressions not measured" in message
    names = {check["check"] for check in state["distribution_guard"]["diagnosis"]}
    assert names == {"rss_ingestion_timing", "article_index_accessibility",
                     "pin_destination_correctness", "image_availability",
                     "title_description_metadata", "campaign_mapping", "robots_indexability",
                     "pinterest_referral_evidence"}

    again = run_guard(lane, state, WINDOW_END + timedelta(hours=1), alerts, lines=one_human)
    assert again["state"] == "evaluated" and len(alerts) == 1


def test_observed_traffic_after_window_is_silent(lane):
    state, alerts = {}, []
    two_humans = lane["lines"] + [visit("1.1.1.1", LAST + timedelta(hours=10), "a2"),
                                  visit("2.2.2.2", LAST + timedelta(hours=11), "a1")]
    result = run_guard(lane, state, WINDOW_END + timedelta(minutes=5), alerts, lines=two_humans)
    assert result["state"] == "DISTRIBUTION_OBSERVED" and alerts == []


def test_a_single_cta_click_is_not_a_distribution_failure(lane):
    state, alerts = {}, []
    click = [visit("1.1.1.1", LAST + timedelta(hours=10), "a2"),
             line("1.1.1.1", LAST + timedelta(hours=10), "/libra/growth/out/tok", status=307,
                  agent=BROWSER, referrer=f"{SITE}/libra/growth/articles/a2")]
    result = run_guard(lane, state, WINDOW_END + timedelta(minutes=5), alerts,
                       lines=lane["lines"] + click)
    assert result["state"] == "DISTRIBUTION_OBSERVED" and alerts == []


def test_owner_visits_cannot_rescue_the_lane(lane):
    state, alerts = {}, []
    owner = [line(OWNER, LAST, "/api/tabs"), visit(OWNER, LAST + timedelta(hours=1), "a1"),
             visit(OWNER, LAST + timedelta(hours=2), "a2")]
    result = run_guard(lane, state, WINDOW_END + timedelta(minutes=5), alerts,
                       lines=lane["lines"] + owner)
    assert result["state"] == "EARLY_DISTRIBUTION_FAILURE"


def test_a_failed_send_is_retried_next_run(lane):
    state, alerts = {}, []
    now = WINDOW_END + timedelta(minutes=5)
    assert run_guard(lane, state, now, alerts, send_ok=False)["alert_sent"] is False
    assert "evaluated_at" not in state["distribution_guard"]
    assert run_guard(lane, state, now, alerts)["state"] == "EARLY_DISTRIBUTION_FAILURE"
    assert state["distribution_guard"]["result"] == "EARLY_DISTRIBUTION_FAILURE"


def test_incomplete_logs_give_no_verdict_and_no_alert(lane):
    state, alerts = {}, []
    late = lane["tmp"] / "late.log"
    late.write_text(line("1.1.1.1", LAST, "/"))
    result = guard.run(state, alert=lambda k, m: alerts.append(k) or True, articles=lane["articles"],
                       now=WINDOW_END + timedelta(hours=1), logs=[late], self_addresses=set(),
                       fetch=site(), kdp_dir=lane["kdp"], campaigns_file=lane["campaigns"])
    assert result["state"] == "INSUFFICIENT_DATA" and alerts == []


# ── diagnosis ───────────────────────────────────────────────────────────────

def _diagnosis(lane, fetch):
    snapshot = guard.snapshot(lane["articles"], now=WINDOW_END + timedelta(hours=1),
                              logs=write_log(lane["tmp"], lane["lines"]), self_addresses=set())
    checks = guard.diagnose(lane["articles"], snapshot, fetch=fetch, campaigns_file=lane["campaigns"],
                            kdp_dir=lane["kdp"])
    return {check["check"]: check for check in checks}


def test_diagnosis_passes_a_healthy_article_path(lane):
    checks = _diagnosis(lane, site())
    for name in ("rss_ingestion_timing", "article_index_accessibility",
                 "pin_destination_correctness", "image_availability",
                 "title_description_metadata", "campaign_mapping", "robots_indexability"):
        assert checks[name]["ok"] is True, checks[name]
    assert checks["pinterest_referral_evidence"]["ok"] is False


def test_diagnosis_flags_a_cta_that_does_not_resolve(lane):
    checks = _diagnosis(lane, site(cta_href="/growth/out/tok"))
    assert checks["pin_destination_correctness"]["ok"] is False
    assert "HTTP 404" in checks["pin_destination_correctness"]["detail"]


def test_diagnosis_flags_robots_blocking_articles(lane):
    checks = _diagnosis(lane, site(robots_status=200))
    assert checks["robots_indexability"]["ok"] is False


def test_diagnosis_only_issues_get_requests_to_our_own_site(lane):
    fetch = site()
    _diagnosis(lane, fetch)
    assert fetch.calls and all(url.startswith(SITE) for url in fetch.calls)


# ── read-only ───────────────────────────────────────────────────────────────

def test_guard_writes_nothing_but_the_state_it_is_given(lane, tmp_path):
    served = tmp_path / "served"
    served.mkdir()
    for article_id, published in (("a1", FIRST), ("a2", LAST)):
        (served / f"{article_id}.json").write_text(json.dumps({
            "id": article_id, "channel": "pinterest-rss", "qa_approved": True,
            "target_slug": "book-a", "campaign": "pin-adhd-es", "language": "es",
            "title": "La lista de dos columnas", "description": "Una hoja partida en dos.",
            "published_at": published.isoformat()}))
    (served / "linkedin.json").write_text(json.dumps({
        "id": "li", "channel": "owner-post", "qa_approved": True,
        "published_at": FIRST.isoformat()}))

    def digest():
        return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(tmp_path.rglob("*")) if p.is_file() and p.suffix != ".log"}

    before = digest()
    assert set(guard.load_pinterest_articles(served)) == {"a1", "a2"}
    state = {}
    guard.run(state, alert=lambda k, m: True, served_dir=served, now=WINDOW_END + timedelta(hours=1),
              logs=write_log(tmp_path, lane["lines"]), self_addresses=set(), fetch=site(),
              kdp_dir=lane["kdp"], campaigns_file=lane["campaigns"])
    after = digest()
    after.pop("access.log", None)
    assert after == before
    assert state["distribution_guard"]["result"] == "EARLY_DISTRIBUTION_FAILURE"
