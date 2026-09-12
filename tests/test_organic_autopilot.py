"""Unit tests for scripts/organic_autopilot.py — the unattended half of the
organic experiment.

What must hold: Day 0 starts from evidence and only once, a risky book pauses
only itself, a checkpoint fires once, the day-30 verdict may close a channel but
may never touch a book, a duplicate run cannot double-record, a broken step does
not take the others down, and the owner is told only about the things worth a
message."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import activate_organic_experiment as activation  # noqa: E402
import organic_autopilot as autopilot  # noqa: E402
import organic_experiment_report as report_module  # noqa: E402

PUBLISHED_AT = datetime(2026, 9, 12, 6, 18, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def alerts(monkeypatch):
    """No test may send a real Telegram message."""
    sent = []

    def fake_send(message):
        sent.append(message)
        return True

    monkeypatch.setattr(autopilot, "send_telegram", fake_send)
    return sent


@pytest.fixture
def env(tmp_path, monkeypatch):
    root = tmp_path / "libra"
    kdp = tmp_path / "kdp"
    (root / "data" / "growth_articles").mkdir(parents=True)
    (root / "data" / "growth_articles_drafts").mkdir(parents=True)
    (kdp / "book-a").mkdir(parents=True)
    (kdp / "book-a" / "listing.json").write_text(
        json.dumps({"asin": "B0BOOKAAA1", "live_status": "LIVE"}))
    (kdp / "book-a" / "cover.jpg").write_bytes(b"\xff\xd8jpeg")
    (root / "data" / "growth_articles" / "pin-one.json").write_text(json.dumps({
        "id": "pin-one", "channel": "pinterest-rss", "campaign": "pin-adhd-es",
        "target_slug": "book-a", "qa_approved": True,
        "published_at": PUBLISHED_AT.isoformat(), "title": "t", "publication_order": 1}))
    (root / "data" / "organic_experiment.json").write_text(json.dumps({
        "name": "test", "active": False, "books": ["book-a"], "publications": [],
        "planned_window_days": 30}))
    (root / "data" / "posting_authorization.json").write_text(json.dumps({
        "channels": {"pinterest-rss": {"authorized": True, "authorized_by": "Bui",
                                       "authorized_at": "2026-09-12T06:18:12+00:00"}}}))
    (root / "data" / "growth_campaigns.json").write_text(json.dumps({
        "campaigns": ["pin-adhd-es"], "channels": {"pin-adhd-es": "pinterest-rss"}}))

    # The mailbox step is exercised by its own tests; in a full run_all it must be
    # silent, so point it at a configured file rather than the real machine's.
    configured_mailbox = root / "icloud.env"
    configured_mailbox.write_text("IMAP_USER=test\n")
    monkeypatch.setattr(autopilot, "ICLOUD_ENV", configured_mailbox)

    paths = activation.Paths(root, kdp)
    monkeypatch.setattr(autopilot, "_paths", lambda: paths)
    monkeypatch.setattr(autopilot, "STATE_FILE", root / "data" / "state.json")
    monkeypatch.setattr(autopilot, "PAUSES_FILE", root / "data" / "organic_pauses.json")
    monkeypatch.setattr(autopilot, "LOCK_FILE", root / "data" / ".lock")
    monkeypatch.setattr(activation, "KDP_DIR", kdp)
    return paths


def rendered_evidence(*, rendered=True, stale=False):
    return {
        "checked_at": "2026-09-12T07:00:00+00:00",
        "feed_fetches": 3, "last_feed_fetch": "2026-09-12T06:45:45+00:00",
        "feed_ingestion_stale": stale, "claim_verified_by_pinterest": True,
        "articles": {"pin-one": {
            "slug": "book-a",
            "article_crawled_at": "2026-09-12T06:45:39+00:00" if rendered else None,
            "image_fetched_at": "2026-09-12T06:45:50+00:00" if rendered else None,
            "first_referral_at": None, "referrals": 0, "rendered": rendered}},
    }


def experiment(paths):
    return json.loads(paths.experiment.read_text())


def roster(paths, status):
    path = paths.root / "roster.json"
    path.write_text(json.dumps({"fetched_at": "2026-09-12T01:45:00+07:00",
                                "entries": [{"slug": "book-a", "format": "ebook",
                                             "status": status}]}))
    return path


# ── Day 0 ───────────────────────────────────────────────────────────────────

def test_day0_starts_from_render_evidence_without_any_owner_message(env, alerts):
    state = autopilot.load_state()
    result = autopilot.verify_publications(state, paths=env, evidence=rendered_evidence())
    assert result["state"] == "recorded"
    data = experiment(env)
    assert data["active"] is True
    record = data["publications"][0]
    assert record["channel"] == "pinterest-rss"
    assert record["slug"] == "book-a"
    assert record["pin_url"] is None
    assert record["evidence_type"] == "pinterest_render_logs"
    assert record["observed_at"] == "2026-09-12T06:45:50+00:00"
    assert len(alerts) == 1 and "Day 0 started automatically" in alerts[0]


def test_without_render_evidence_nothing_starts(env, alerts):
    state = autopilot.load_state()
    result = autopilot.verify_publications(state, paths=env,
                                           evidence=rendered_evidence(rendered=False))
    assert result["state"] == "nothing_new"
    assert experiment(env)["active"] is False
    assert alerts == []


def test_a_second_run_does_not_record_the_same_article_twice(env, alerts):
    state = autopilot.load_state()
    autopilot.verify_publications(state, paths=env, evidence=rendered_evidence())
    autopilot.verify_publications(state, paths=env, evidence=rendered_evidence())
    assert len(experiment(env)["publications"]) == 1
    assert len(alerts) == 1


def test_the_day0_alert_is_sent_once_even_across_state_reloads(env, alerts):
    state = autopilot.load_state()
    autopilot.verify_publications(state, paths=env, evidence=rendered_evidence())
    autopilot._write_json(autopilot.STATE_FILE, state)
    autopilot.verify_publications(autopilot.load_state(), paths=env,
                                  evidence=rendered_evidence())
    assert len(alerts) == 1


def test_an_unapproved_article_is_never_verified(env, alerts):
    article = env.served / "pin-one.json"
    data = json.loads(article.read_text())
    data["qa_approved"] = False
    article.write_text(json.dumps(data))
    state = autopilot.load_state()
    assert autopilot.verify_publications(state, paths=env,
                                         evidence=rendered_evidence())["state"] == \
        "no_published_articles"
    assert experiment(env)["active"] is False


# ── safety ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("status", ["IN_REVIEW", "BLOCKED", "UNPUBLISHED", "DRAFT"])
def test_a_risky_shelf_row_pauses_that_book(env, alerts, status):
    state = autopilot.load_state()
    result = autopilot.safety_scan(state, paths=env, roster_file=roster(env, status))
    assert result["paused"] == ["book-a"]
    assert activation.paused_slugs(env) != {}
    assert len(alerts) == 1 and "paused automatically" in alerts[0]
    assert "NOT modified" in alerts[0]


def test_a_live_book_is_not_paused(env, alerts):
    state = autopilot.load_state()
    assert autopilot.safety_scan(state, paths=env, roster_file=roster(env, "LIVE"))["paused"] == []
    assert alerts == []


def test_a_book_that_is_live_with_updates_in_review_keeps_running(env, alerts):
    state = autopilot.load_state()
    result = autopilot.safety_scan(state, paths=env,
                                   roster_file=roster(env, "LIVE_UPDATES_IN_REVIEW"))
    assert result["paused"] == []
    assert alerts == []


def test_a_pause_blocks_the_next_scheduled_approval(env):
    (env.drafts / "pin-two.json").write_text(json.dumps({
        "id": "pin-two", "channel": "pinterest-rss", "campaign": "pin-adhd-es",
        "target_slug": "book-a", "qa_approved": False, "published_at": None,
        "publication_order": 2, "title": "t",
        "semantic_qa": {"status": "SEMANTIC_QA_PASS"}}))
    autopilot.safety_scan(autopilot.load_state(), paths=env,
                          roster_file=roster(env, "BLOCKED"))
    with pytest.raises(activation.Refused, match="is paused"):
        activation.approve_next(env, lane="pinterest-rss")


def test_a_recovered_book_is_released_again(env, alerts):
    state = autopilot.load_state()
    autopilot.safety_scan(state, paths=env, roster_file=roster(env, "BLOCKED"))
    result = autopilot.safety_scan(state, paths=env, roster_file=roster(env, "LIVE"))
    assert result["released"] == ["book-a"]
    assert activation.paused_slugs(env) == {}


def test_safety_never_writes_to_the_kdp_listing(env):
    listing = env.kdp / "book-a" / "listing.json"
    before = listing.read_text()
    autopilot.safety_scan(autopilot.load_state(), paths=env, roster_file=roster(env, "BLOCKED"))
    assert listing.read_text() == before


# ── feed health ─────────────────────────────────────────────────────────────

def test_a_stale_feed_alerts_once(env, alerts):
    state = autopilot.load_state()
    autopilot.feed_health(state, evidence=rendered_evidence(stale=True))
    autopilot.feed_health(state, evidence=rendered_evidence(stale=True))
    assert len(alerts) == 1 and "has not fetched the RSS feed" in alerts[0]


def test_a_recovered_feed_clears_the_alert_so_it_can_fire_again(env, alerts):
    state = autopilot.load_state()
    autopilot.feed_health(state, evidence=rendered_evidence(stale=True))
    autopilot.feed_health(state, evidence=rendered_evidence(stale=False))
    autopilot.feed_health(state, evidence=rendered_evidence(stale=True))
    assert len(alerts) == 2


# ── checkpoints ─────────────────────────────────────────────────────────────

def _report(days_elapsed, verdict="INCONCLUSIVE", clicks=0):
    return {
        "state": "active", "days_elapsed": days_elapsed, "verdict": verdict,
        "action": "an action sentence", "acquisition_threshold_clicks": 25,
        "paused_books": [],
        "tracks": {"content_published": 1, "qualified_outbound_clicks": clicks,
                   "paid_orders": {}, "kenp": {}, "royalties_usd": {},
                   "human_interventions": 0},
    }


@pytest.mark.parametrize("elapsed,expected", [(6, []), (7, [7]), (14, [7, 14]), (31, [7, 14, 30])])
def test_checkpoints_fire_when_the_window_reaches_them(env, alerts, elapsed, expected):
    state = autopilot.load_state()
    result = autopilot.run_checkpoints(state, report=_report(elapsed), paths=env)
    assert result["fired"] == expected
    assert len(alerts) == len(expected)


def test_a_checkpoint_never_fires_twice(env, alerts):
    state = autopilot.load_state()
    autopilot.run_checkpoints(state, report=_report(7), paths=env)
    autopilot.run_checkpoints(state, report=_report(9), paths=env)
    assert len(alerts) == 1


def test_a_missed_checkpoint_still_fires_on_a_later_run(env, alerts):
    state = autopilot.load_state()
    result = autopilot.run_checkpoints(state, report=_report(20), paths=env)
    assert result["fired"] == [7, 14]


def test_checkpoints_do_nothing_before_the_clock_starts(env, alerts):
    state = autopilot.load_state()
    assert autopilot.run_checkpoints(state, report={"state": "inactive"}, paths=env)["state"] == \
        "inactive"
    assert alerts == []


def test_the_checkpoint_message_never_claims_purchase_attribution(env, alerts):
    autopilot.run_checkpoints(autopilot.load_state(), report=_report(7, clicks=9), paths=env)
    assert "no purchase is attributed" in alerts[0]


# ── day 30 ──────────────────────────────────────────────────────────────────

def test_day30_stop_channel_closes_the_channel_and_nothing_else(env, alerts):
    listing = (env.kdp / "book-a" / "listing.json").read_text()
    autopilot.run_checkpoints(autopilot.load_state(),
                              report=_report(30, verdict="STOP-CHANNEL"), paths=env)
    authorization = json.loads(env.authorization.read_text())["channels"]["pinterest-rss"]
    assert authorization["authorized"] is False
    assert authorization["closed_by"].startswith("organic_autopilot")
    assert (env.kdp / "book-a" / "listing.json").read_text() == listing
    assert experiment(env)["books"] == ["book-a"]
    assert experiment(env)["decisions"][0]["verdict"] == "STOP-CHANNEL"


@pytest.mark.parametrize("verdict", ["CONTINUE", "ITERATE", "INCONCLUSIVE"])
def test_any_other_day30_verdict_leaves_the_channel_open(env, alerts, verdict):
    autopilot.run_checkpoints(autopilot.load_state(), report=_report(30, verdict=verdict),
                              paths=env)
    assert json.loads(env.authorization.read_text())["channels"]["pinterest-rss"]["authorized"] \
        is True
    assert experiment(env)["decisions"][0]["applied"] != "channel paused"


# ── the hourly run ──────────────────────────────────────────────────────────

def test_a_failing_step_does_not_stop_the_others(env, alerts, monkeypatch):
    monkeypatch.setattr(autopilot.evidence_module, "evidence_for",
                        lambda *a, **k: rendered_evidence())

    def explode(*args, **kwargs):
        raise RuntimeError("simulated")

    monkeypatch.setattr(autopilot, "safety_scan", explode)
    result = autopilot.run_all()
    assert result["steps"]["safety"]["state"] == "failed"
    assert result["steps"]["verify"]["state"] == "recorded"
    assert experiment(env)["active"] is True


def test_a_step_alerts_only_after_repeated_failure(env, alerts, monkeypatch):
    monkeypatch.setattr(autopilot.evidence_module, "evidence_for",
                        lambda *a, **k: rendered_evidence(rendered=False))

    def explode(*args, **kwargs):
        raise RuntimeError("simulated")

    monkeypatch.setattr(autopilot, "safety_scan", explode)
    for _ in range(autopilot.ALERT_AFTER_FAILURES - 1):
        autopilot.run_all()
    assert alerts == []
    autopilot.run_all()
    assert len(alerts) == 1 and "failed" in alerts[0]


def test_a_concurrent_run_is_refused(env, monkeypatch):
    import fcntl

    autopilot.LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(autopilot.LOCK_FILE, "a+", encoding="utf-8") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert autopilot.run_all()["state"] == "locked"


def test_state_survives_a_restart(env, alerts, monkeypatch):
    monkeypatch.setattr(autopilot.evidence_module, "evidence_for",
                        lambda *a, **k: rendered_evidence())
    autopilot.run_all()
    # A "restart" is simply the next process reading the same state file.
    autopilot.run_all()
    assert len(experiment(env)["publications"]) == 1
    assert len(alerts) == 1
    assert json.loads(autopilot.STATE_FILE.read_text())["last_run_at"]


def test_status_reports_no_routine_owner_action_once_running(env, monkeypatch):
    monkeypatch.setattr(autopilot.evidence_module, "evidence_for",
                        lambda *a, **k: rendered_evidence())
    autopilot.run_all()
    view = autopilot.status(paths=env)
    assert view["experiment_active"] is True
    assert view["publications"] == 1
    assert view["routine_owner_actions_required"] == []


# ── KDP notice mailbox ──────────────────────────────────────────────────────

def test_a_missing_icloud_credential_asks_once(env, alerts, tmp_path):
    state = autopilot.load_state()
    missing = tmp_path / "icloud.env"
    assert autopilot.mailbox_check(state, icloud_env=missing)["state"] == "not_configured"
    autopilot.mailbox_check(state, icloud_env=missing)
    assert len(alerts) == 1
    assert "one-time setup" in alerts[0].lower()


def test_a_configured_mailbox_is_silent(env, alerts, tmp_path):
    configured = tmp_path / "icloud.env"
    configured.write_text("IMAP_USER=someone\n")
    assert autopilot.mailbox_check(autopilot.load_state(),
                                   icloud_env=configured)["state"] == "configured"
    assert alerts == []


# ── lanes on hold and the day-30 branches ───────────────────────────────────

def test_a_held_lane_approves_nothing_and_keeps_its_content(env):
    data = json.loads(env.experiment.read_text())
    data["lanes_on_hold"] = {"owner-post": {"since": "2026-09-12", "reason": "held"}}
    env.experiment.write_text(json.dumps(data))
    (env.drafts / "li-one.json").write_text(json.dumps({
        "id": "li-one", "channel": "owner-post", "campaign": "li-contab-pt",
        "target_slug": "book-a", "qa_approved": False, "published_at": None,
        "publication_order": 1, "title": "t", "semantic_qa": {"status": "SEMANTIC_QA_PASS"}}))
    with pytest.raises(activation.Refused, match="on hold"):
        activation.approve_next(env, lane="owner-post")
    assert (env.drafts / "li-one.json").exists()


def test_day30_continue_unlocks_phase_two_as_a_flag_only(env, alerts):
    autopilot.run_checkpoints(autopilot.load_state(), report=_report(30, verdict="CONTINUE",
                                                                    clicks=40), paths=env)
    data = experiment(env)
    assert data["phase2_unlocked"]["by"].endswith("CONTINUE")
    assert "no new book" in data["phase2_unlocked"]["scope"]
    assert json.loads(env.authorization.read_text())["channels"]["pinterest-rss"]["authorized"]


def test_day30_iterate_writes_a_local_plan_without_scaling(env, alerts):
    report = _report(30, verdict="ITERATE", clicks=9)
    report["books"] = [{"slug": "book-a", "clicks_by_campaign": {"pin-adhd-es": {"amazon": 9}}},
                       {"slug": "book-b", "clicks_by_campaign": {}}]
    autopilot.run_checkpoints(autopilot.load_state(), report=report, paths=env)
    plan = experiment(env)["iteration_plan"]
    assert plan["keep"] == ["book-a"]
    assert plan["rework"] == ["book-b"]
    assert "do not add a platform" in plan["rule"]


def test_day30_inconclusive_scales_nothing(env, alerts):
    autopilot.run_checkpoints(autopilot.load_state(), report=_report(30, verdict="INCONCLUSIVE"),
                              paths=env)
    data = experiment(env)
    assert "phase2_unlocked" not in data
    assert data["decisions"][0]["applied"].startswith("bounded observation")


def test_status_counts_every_routine_step_as_automated(env, monkeypatch):
    view = autopilot.status(paths=env)
    assert view["routine_autonomy"]["automated"] == view["routine_autonomy"]["steps"]
    assert view["routine_autonomy"]["owner_executed"] == []
    assert view["routine_owner_actions_required"] == []
    assert view["exception_gates"]
