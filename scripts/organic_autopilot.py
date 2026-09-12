#!/usr/bin/env python3
"""organic_autopilot.py — the unattended half of the organic experiment.

One cron entry runs `--all` every hour. Between them, these steps need no owner:

  verify      first Pinterest publication, from our own server logs → Day 0
  safety      a pilot book's shelf row turns risky → pause that book only
  health      Pinterest stopped fetching the feed → alert once past a threshold
  checkpoints day 7, 14 and 30 run themselves and report their own result

What it will not do, by design and by the owner's standing rules: touch KDP,
post anything anywhere, log into Pinterest, call a Pinterest API, invent a Pin
URL, attribute a sale to a click, or change the experiment's design. Day 30
classifies the channel and may pause it; it never retires a book.

Failure policy: every step is independent and idempotent. A step that fails is
counted, retried on the next hourly run, and only alerts once it has failed
`ALERT_AFTER_FAILURES` times in a row. No step ever catches up by doing two
things at once.

    python3 scripts/organic_autopilot.py --all      # the hourly cron run
    python3 scripts/organic_autopilot.py --status   # read-only autonomy view
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRA_DIR / "scripts"))

import activate_organic_experiment as activation  # noqa: E402
import pinterest_evidence as evidence_module  # noqa: E402
import organic_experiment_report as report_module  # noqa: E402
from organic_experiment_report import send_telegram  # noqa: E402

STATE_FILE = LIBRA_DIR / "data" / "organic_autopilot_state.json"
PAUSES_FILE = LIBRA_DIR / "data" / "organic_pauses.json"
LOCK_FILE = LIBRA_DIR / "data" / ".organic-autopilot.lock"
PINTEREST_LANE = "pinterest-rss"
SITE_BASE = "https://newton-winai-klinprasom.incomeinclick.in.th"
ALERT_AFTER_FAILURES = 3
CHECKPOINTS = (7, 14, 30)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _read_json(path: Path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _write_json(path: Path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_state() -> dict:
    state = _read_json(STATE_FILE, {})
    state.setdefault("alerts_sent", {})
    state.setdefault("checkpoints_done", {})
    state.setdefault("failures", {})
    return state


def _alert_once(state: dict, key: str, message: str) -> bool:
    """Send one Telegram message per distinct event. A repeated condition with the
    same key stays silent — the owner's inbox is not a log file."""
    if state["alerts_sent"].get(key):
        return False
    if send_telegram(message):
        state["alerts_sent"][key] = _now().isoformat()
        return True
    # A failed send is not recorded, so the next run tries again.
    return False


def _paths() -> activation.Paths:
    return activation.Paths(LIBRA_DIR, activation.KDP_DIR)


def _published_pinterest_articles(paths: activation.Paths) -> dict:
    """Approved articles of the Pinterest lane: id → slug + published_at."""
    articles = {}
    if not paths.served.is_dir():
        return articles
    for file in sorted(paths.served.glob("*.json")):
        data = _read_json(file, {})
        if data.get("qa_approved") is not True or data.get("channel") != PINTEREST_LANE:
            continue
        try:
            published = datetime.fromisoformat(str(data.get("published_at")))
        except ValueError:
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        articles[data.get("id") or file.stem] = {"slug": data.get("target_slug"),
                                                 "published_at": published}
    return articles


# ── verification and Day 0 ──────────────────────────────────────────────────

def verify_publications(state: dict, *, paths=None, evidence=None) -> dict:
    """Record every Pinterest article we can prove Pinterest rendered, and let the
    first record start the clock. The evidence is our own access log, so this
    needs no Pinterest account and no owner message."""
    paths = paths or _paths()
    articles = _published_pinterest_articles(paths)
    if not articles:
        return {"state": "no_published_articles"}
    evidence = evidence or evidence_module.evidence_for(articles)
    experiment = _read_json(paths.experiment, {})
    already = {record.get("slug_article") for record in experiment.get("publications", []) or []}

    recorded = []
    for article_id, article_evidence in sorted(evidence["articles"].items()):
        if not article_evidence["rendered"] or article_id in already:
            continue
        url = f"{SITE_BASE}{evidence_module.ARTICLE_PREFIX}{article_id}"
        result = activation.record_publication(
            paths, channel=PINTEREST_LANE, slug=article_evidence["slug"], url=url,
            evidence=evidence_module.publication_evidence_text(article_id, article_evidence),
            observed_at=article_evidence["image_fetched_at"],
            extra={"slug_article": article_id, "pin_url": None,
                   "evidence_type": "pinterest_render_logs",
                   "url_kind": "destination article on our own site, not the Pin"})
        recorded.append({"article": article_id, **result})
        if result.get("clock_started"):
            _alert_once(state, "day0",
                        "🟢 Libra organic: Day 0 started automatically.\n"
                        f"Pinterest rendered {article_id} "
                        f"(crawled {article_evidence['article_crawled_at']}, image "
                        f"{article_evidence['image_fetched_at']}).\n"
                        "Evidence is our own server log, not a Pin page — pin_url is null.\n"
                        "30-day window is running; day 7/14/30 report themselves.")
    return {"state": "recorded" if recorded else "nothing_new", "recorded": recorded,
            "feed_ingestion_stale": evidence["feed_ingestion_stale"]}


# ── safety: a risky book pauses itself ──────────────────────────────────────

def safety_scan(state: dict, *, paths=None, roster_file=None) -> dict:
    """Pause the campaigns of any pilot book whose shelf row is out of sale, and
    nothing else. The KDP title is never touched, never resubmitted, never
    edited: pausing is our side only."""
    paths = paths or _paths()
    roster_file = roster_file or report_module.ROSTER_FILE
    experiment = _read_json(paths.experiment, {})
    pauses = _read_json(PAUSES_FILE, {"paused": {}})
    pauses.setdefault("paused", {})
    changed, newly_paused, released = False, [], []

    for slug in experiment.get("books", []) or []:
        statuses = report_module.shelf_status(slug, roster_file)
        disposition = report_module.campaign_disposition(statuses)
        if disposition["action"] == "pause":
            if slug not in pauses["paused"]:
                pauses["paused"][slug] = {
                    "reason": disposition["reason"], "paused_at": _now().isoformat(),
                    "shelf": statuses.get("formats", {}),
                    "note": "campaigns paused on our side only — the KDP listing was not touched",
                }
                changed = True
                newly_paused.append(slug)
        elif disposition["action"] == "run" and slug in pauses["paused"]:
            # The shelf recovered on its own. Releasing is safe: it only lets the
            # book take its next scheduled slot again.
            pauses["paused"].pop(slug)
            changed = True
            released.append(slug)

    if changed:
        pauses["updated_at"] = _now().isoformat()
        _write_json(PAUSES_FILE, pauses)
    for slug in newly_paused:
        _alert_once(state, f"paused:{slug}:{pauses['paused'][slug]['paused_at'][:10]}",
                    f"⛔ Libra organic: {slug} paused automatically.\n"
                    f"{pauses['paused'][slug]['reason']}.\n"
                    "Its campaigns stop taking new slots. The KDP listing was NOT modified, "
                    "resubmitted or appealed — that stays an owner decision.")
    return {"state": "ok", "paused": sorted(pauses["paused"]), "newly_paused": newly_paused,
            "released": released}


# ── feed ingestion health ───────────────────────────────────────────────────

def feed_health(state: dict, *, evidence: dict) -> dict:
    """Pinterest fetches a connected feed regularly. A long silence means the
    connection broke on their side — that is worth one alert, not a daily one."""
    if not evidence["feed_ingestion_stale"]:
        state["alerts_sent"].pop("feed_stale", None)
        return {"state": "ok", "last_feed_fetch": evidence["last_feed_fetch"]}
    _alert_once(state, "feed_stale",
                "⚠️ Libra organic: Pinterest has not fetched the RSS feed recently "
                f"(last fetch: {evidence['last_feed_fetch'] or 'never'}).\n"
                "The feed itself is still served by us. Check the feed is still connected in "
                "Pinterest → Settings → Create Pins in bulk.")
    return {"state": "stale", "last_feed_fetch": evidence["last_feed_fetch"]}


# ── KDP notice mailbox ──────────────────────────────────────────────────────

ICLOUD_ENV = Path("/root/.config/mail-watch/icloud.env")


def mailbox_check(state: dict, *, icloud_env: Path | None = None) -> dict:
    """KDP notices go to the owner's iCloud address, which needs one credential we
    cannot provision for him. Until it exists, the daily bookshelf scrape is the
    only way we learn about a status change — so say that once and never again."""
    path = Path(icloud_env or ICLOUD_ENV)
    if path.exists():
        state["alerts_sent"].pop("icloud_setup", None)
        return {"state": "configured"}
    _alert_once(state, "icloud_setup",
                "📬 Libra: KDP notices go to your iCloud address, which the watcher cannot read "
                "yet.\nOne-time setup (either one):\n"
                "· forward rule in icloud.com/mail → Rules → from contains 'kdp' → "
                "forward to the Gmail the watcher already reads, or\n"
                "· an app-specific password in /root/.config/mail-watch/icloud.env.\n"
                "Until then the daily 08:45 bookshelf scrape is the only KDP status signal — "
                "it already runs and already pauses a risky book by itself.")
    return {"state": "not_configured"}


# ── checkpoints ─────────────────────────────────────────────────────────────

def _checkpoint_message(day: int, report: dict) -> str:
    tracks = report["tracks"]
    head = {7: "🔎 Day 7 — health and tracking check",
            14: "🩺 Day 14 — distribution diagnosis",
            30: "🏁 Day 30 — decision"}[day]
    lines = [
        f"{head} · Libra organic",
        f"verified publications: {tracks['content_published']} · "
        f"qualified clicks: {tracks['qualified_outbound_clicks']}",
        f"orders: {tracks['paid_orders']} · KENP: {tracks['kenp']} · "
        f"royalties: {tracks['royalties_usd']} · paid spend: 0",
        f"human interventions: {tracks['human_interventions']}",
        f"verdict: {report['verdict']} — {report['action']}",
    ]
    if report.get("paused_books"):
        lines.append(f"paused books: {', '.join(report['paused_books'])}")
    lines.append("Clicks and royalties are separate observations; no purchase is attributed "
                 "to a click.")
    return "\n".join(lines)


def run_checkpoints(state: dict, *, report: dict | None = None, paths=None) -> dict:
    """Fire each checkpoint once, when the window has reached it. A checkpoint that
    has already run never runs again, and a missed one fires on the next run
    rather than being skipped."""
    paths = paths or _paths()
    report = report or report_module.build_report()
    if report.get("state") in ("inactive", "blocked"):
        return {"state": report.get("state")}
    elapsed = report.get("days_elapsed")
    if elapsed is None:
        return {"state": "not_started"}

    fired = []
    for day in CHECKPOINTS:
        key = str(day)
        if elapsed < day or state["checkpoints_done"].get(key):
            continue
        if send_telegram(_checkpoint_message(day, report)):
            state["checkpoints_done"][key] = {"fired_at": _now().isoformat(),
                                              "days_elapsed": elapsed,
                                              "verdict": report["verdict"]}
            fired.append(day)
            if day == 30:
                _apply_day30_decision(paths, report)
    return {"state": "ok", "days_elapsed": elapsed, "fired": fired,
            "done": sorted(state["checkpoints_done"])}


def _apply_day30_decision(paths: activation.Paths, report: dict) -> dict:
    """Record the verdict and take only the action that verdict allows. The two
    channel-level actions are 'keep going' and 'stop this channel'. Nothing here
    touches a book, a listing, a price or a manuscript."""
    experiment = _read_json(paths.experiment, {})
    decision = {"decided_at": _now().isoformat(), "verdict": report["verdict"],
                "action": report["action"],
                "qualified_clicks": report["tracks"]["qualified_outbound_clicks"],
                "threshold": report["acquisition_threshold_clicks"],
                "applied": "channel paused" if report["verdict"] == "STOP-CHANNEL" else "none"}
    experiment.setdefault("decisions", []).append(decision)
    if report["verdict"] == "STOP-CHANNEL":
        # Closing the channel is the strongest automatic action available, and it
        # is reversible by the owner in one file.
        authorization = _read_json(paths.authorization, {})
        record = (authorization.get("channels") or {}).get(PINTEREST_LANE)
        if isinstance(record, dict):
            record.update({"authorized": False, "authorized_by": None, "authorized_at": None,
                           "closed_by": "organic_autopilot day-30 STOP-CHANNEL",
                           "closed_at": _now().isoformat()})
            _write_json(paths.authorization, authorization)
    _write_json(paths.experiment, experiment)
    return decision


# ── autonomy view ───────────────────────────────────────────────────────────

def status(*, paths=None) -> dict:
    paths = paths or _paths()
    state = load_state()
    experiment = _read_json(paths.experiment, {})
    drafts = [row for row in activation.prepared_articles(paths)]
    return {
        "checked_at": _now().isoformat(),
        "experiment_active": bool(experiment.get("active")),
        "publications": len(experiment.get("publications") or []),
        "pinterest_articles_pending": len([r for r in drafts if r["lane"] == PINTEREST_LANE]),
        "owner_post_articles_pending": len([r for r in drafts if r["lane"] == "owner-post"]),
        "paused_books": sorted(_read_json(PAUSES_FILE, {}).get("paused", {})),
        "checkpoints_done": sorted(state["checkpoints_done"]),
        "consecutive_failures": state["failures"],
        "routine_owner_actions_required": [] if experiment.get("active") else
        ["none — Day 0 starts from server-side evidence, no owner message needed"],
    }


# ── the hourly run ──────────────────────────────────────────────────────────

def run_all() -> dict:
    """Every step, each one independent. One failing step never stops the others,
    and a step only alerts after it has failed ALERT_AFTER_FAILURES times."""
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return {"state": "locked"}

        state = load_state()
        paths = _paths()
        results = {}
        articles = _published_pinterest_articles(paths)
        evidence = evidence_module.evidence_for(articles) if articles else {
            "articles": {}, "feed_ingestion_stale": False, "last_feed_fetch": None}

        steps = (
            ("verify", lambda: verify_publications(state, paths=paths, evidence=evidence)),
            ("safety", lambda: safety_scan(state, paths=paths)),
            ("health", lambda: feed_health(state, evidence=evidence)),
            ("mailbox", lambda: mailbox_check(state)),
            ("checkpoints", lambda: run_checkpoints(state, paths=paths)),
        )
        for name, step in steps:
            try:
                results[name] = step()
                state["failures"].pop(name, None)
            except Exception as error:  # a broken step must not take the others down
                count = state["failures"].get(name, 0) + 1
                state["failures"][name] = count
                results[name] = {"state": "failed", "error": str(error), "consecutive": count}
                if count >= ALERT_AFTER_FAILURES:
                    _alert_once(state, f"step_failed:{name}:{count}",
                                f"⚠️ Libra organic autopilot: step {name!r} has failed "
                                f"{count} runs in a row.\nLast error: {error}")
        state["last_run_at"] = _now().isoformat()
        _write_json(STATE_FILE, state)
        return {"state": "ok", "steps": results}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--all", action="store_true", help="the hourly unattended run")
    group.add_argument("--status", action="store_true", help="read-only autonomy view")
    group.add_argument("--evidence", action="store_true",
                       help="read-only: what the access log proves about Pinterest")
    args = parser.parse_args(argv)
    if args.status:
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    elif args.evidence:
        print(json.dumps(evidence_module.evidence_for(_published_pinterest_articles(_paths())),
                         ensure_ascii=False, indent=2))
    else:
        print(json.dumps(run_all(), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
