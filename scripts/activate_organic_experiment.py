#!/usr/bin/env python3
"""activate_organic_experiment.py — the one operator entry point for the
server-side half of the organic experiment activation.

It does exactly four kinds of write, each one the owner has authorized in
advance, and nothing else:

  1. open the `pinterest-rss` channel in data/posting_authorization.json;
  2. approve one prepared article (qa_approved + published_at, draft → served);
  3. record a verified publication (a live url we opened, with evidence);
  4. start the experiment clock, but only once a publication is recorded.

What it refuses to do: touch KDP, post anything to any network, log into
Pinterest, approve an article out of order, approve two articles of the same lane
on the same day, publish an article whose target book is not Live, set a
published_at in the future, or start the clock on a typed date.

Everything else about the experiment (the report, the day 7/14/30 cadence) is
already running from cron and needs no help from here.

    python3 scripts/activate_organic_experiment.py preflight
    python3 scripts/activate_organic_experiment.py day0 --owner Bui --confirm "<phrase>"
    python3 scripts/activate_organic_experiment.py approve-next --lane pinterest-rss
    python3 scripts/activate_organic_experiment.py record-publication \
        --channel pinterest-rss --slug adhd-adults-workbook-es \
        --url https://www.pinterest.com/pin/... --evidence "opened it, resolves to the hub page"
    python3 scripts/activate_organic_experiment.py status
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parent.parent
KDP_DIR = LIBRA_DIR.parent / "kdp"
CONFIRM_PHRASE = "PINTEREST VERIFIED — ACTIVATE LIBRA ORGANIC EXPERIMENT"
LOCAL_FEED_URL = "http://127.0.0.1:8200/growth/feed.xml"
CHANNEL = "pinterest-rss"
LANES = ("pinterest-rss", "owner-post")


class Refused(RuntimeError):
    """A precondition the owner set is not met. Nothing is written."""


class Paths:
    def __init__(self, root: Path, kdp: Path):
        self.root = Path(root)
        self.kdp = Path(kdp)
        self.data = self.root / "data"
        self.drafts = self.data / "growth_articles_drafts"
        self.served = self.data / "growth_articles"
        self.authorization = self.data / "posting_authorization.json"
        self.experiment = self.data / "organic_experiment.json"
        self.campaigns = self.data / "growth_campaigns.json"


def _read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: Path, data) -> None:
    """Write through a temporary file so a reader never sees half a file."""
    path = Path(path)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def prepared_articles(paths: Paths) -> list:
    """The prepared drafts, per lane, in publication order. A draft without a
    SEMANTIC_QA_PASS record is not prepared and is never offered for approval."""
    rows = []
    for file in sorted(paths.drafts.glob("*.json")):
        try:
            data = _read_json(file)
        except (OSError, json.JSONDecodeError):
            continue
        if (data.get("semantic_qa") or {}).get("status") != "SEMANTIC_QA_PASS":
            continue
        rows.append({"file": file, "id": data.get("id") or file.stem,
                     "lane": data.get("channel"), "order": data.get("publication_order"),
                     "campaign": data.get("campaign"), "slug": data.get("target_slug"),
                     "title": data.get("title"), "data": data})
    rows.sort(key=lambda row: (LANES.index(row["lane"]) if row["lane"] in LANES else len(LANES),
                               row["order"] or 0))
    return rows


def _served_ids(paths: Paths) -> dict:
    """id → {lane, day} for everything already approved and served. Read from the
    served file itself: once approved, an article no longer has a draft to
    consult."""
    published = {}
    if not paths.served.is_dir():
        return published
    for file in sorted(paths.served.glob("*.json")):
        try:
            data = _read_json(file)
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("qa_approved") is True:
            published[data.get("id") or file.stem] = {
                "lane": data.get("channel"),
                "day": str(data.get("published_at") or "")[:10],
            }
    return published


def lanes_on_hold(paths: Paths) -> dict:
    """Lanes withdrawn from the active experiment, lane → record. A held lane
    publishes nothing: its prepared content stays exactly as it is, and no owner
    action is expected for it either."""
    try:
        experiment = _read_json(paths.experiment)
    except (OSError, json.JSONDecodeError):
        return {}
    holds = experiment.get("lanes_on_hold") if isinstance(experiment, dict) else None
    return holds if isinstance(holds, dict) else {}


def paused_slugs(paths: Paths) -> dict:
    """Books whose campaigns the safety watcher has paused, slug → reason. Written
    by scripts/organic_autopilot.py when a shelf row turns IN_REVIEW / BLOCKED /
    UNPUBLISHED / DRAFT; read here so a paused book can never take its next slot."""
    path = paths.data / "organic_pauses.json"
    try:
        data = _read_json(path)
    except (OSError, json.JSONDecodeError):
        return {}
    paused = data.get("paused") if isinstance(data, dict) else None
    return paused if isinstance(paused, dict) else {}


def _book_is_live(paths: Paths, slug) -> bool:
    try:
        listing = _read_json(paths.kdp / str(slug) / "listing.json")
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    return bool(listing.get("asin")) and listing.get("live_status") == "LIVE"


def preflight(paths: Paths) -> dict:
    """Read-only. Every line is a fact about the current state, so the owner can
    see what activation would act on before anything is written."""
    articles = prepared_articles(paths)
    served = _served_ids(paths)
    authorization = _read_json(paths.authorization) if paths.authorization.exists() else {}
    record = ((authorization.get("channels") or {}).get(CHANNEL) or {})
    experiment = _read_json(paths.experiment) if paths.experiment.exists() else {}
    campaigns = _read_json(paths.campaigns) if paths.campaigns.exists() else {}
    declared = set(campaigns.get("campaigns") or [])
    mapping = campaigns.get("channels") or {}

    problems = []
    if len(articles) != 9:
        problems.append(f"expected 9 prepared articles, found {len(articles)}")
    for row in articles:
        if row["lane"] not in LANES:
            problems.append(f"{row['id']}: unknown lane {row['lane']!r}")
        if row["campaign"] not in declared:
            problems.append(f"{row['id']}: campaign {row['campaign']!r} is not declared")
        elif mapping.get(row["campaign"]) != row["lane"]:
            problems.append(f"{row['id']}: campaign/lane mapping disagrees")
        if not _book_is_live(paths, row["slug"]):
            problems.append(f"{row['id']}: target book {row['slug']!r} is not Live on the shelf")
        if not (paths.kdp / str(row["slug"]) / "cover.jpg").exists():
            problems.append(f"{row['id']}: no cover asset for the feed image")
    for lane in LANES:
        orders = [row["order"] for row in articles if row["lane"] == lane]
        if sorted(orders) != list(range(1, len(orders) + 1)):
            problems.append(f"{lane}: publication_order is not 1..{len(orders)} ({orders})")

    return {
        "ok": not problems,
        "problems": problems,
        "prepared": [{"lane": r["lane"], "order": r["order"], "id": r["id"],
                      "campaign": r["campaign"], "approved": r["id"] in served}
                     for r in articles],
        "approved_count": len(served),
        "channel_authorized": bool(record.get("authorized") is True
                                   and record.get("authorized_by") and record.get("authorized_at")),
        "experiment_active": bool(experiment.get("active")),
        "publications": len(experiment.get("publications") or []),
    }


def authorize(paths: Paths, *, owner: str, at: datetime | None = None) -> dict:
    """Open the one channel. Idempotent: an already-open channel is left as it is,
    so re-running the activation never rewrites who authorized it or when."""
    authorization = _read_json(paths.authorization)
    record = (authorization.get("channels") or {}).get(CHANNEL)
    if not isinstance(record, dict):
        raise Refused(f"{CHANNEL} is not a known channel in {paths.authorization}")
    if record.get("authorized") is True and record.get("authorized_by") and record.get("authorized_at"):
        return {"changed": False, "record": record}
    record.update({"authorized": True, "authorized_by": owner,
                   "authorized_at": (at or _now()).isoformat()})
    _write_json(paths.authorization, authorization)
    return {"changed": True, "record": record}


def approve_next(paths: Paths, *, lane: str, at: datetime | None = None) -> dict:
    """Approve the next article of one lane: qa_approved true, published_at now,
    draft moved into the served directory. One per lane per day."""
    if lane not in LANES:
        raise Refused(f"unknown lane {lane!r}")
    held = lanes_on_hold(paths).get(lane)
    if held:
        raise Refused(f"{lane}: lane is on hold since {held.get('since', 'an unrecorded date')} "
                      f"({held.get('reason', 'no reason recorded')}) — nothing is approved for it")
    at = at or _now()
    served = _served_ids(paths)
    lane_rows = [row for row in prepared_articles(paths) if row["lane"] == lane]
    pending = [row for row in lane_rows if row["id"] not in served]
    if not pending:
        raise Refused(f"{lane}: every prepared article is already approved")
    row = pending[0]
    # Book-level safety first: if the book must not be promoted at all, that is
    # the reason worth reporting, not the cadence rule.
    if not _book_is_live(paths, row["slug"]):
        raise Refused(f"{row['id']}: target book {row['slug']!r} is not Live — not approving")
    paused = paused_slugs(paths)
    if row["slug"] in paused:
        raise Refused(f"{row['id']}: {row['slug']!r} is paused "
                      f"({paused[row['slug']].get('reason', 'no reason recorded')}) — not approving")
    pin_prefix = "/libra/growth/pins/"
    image_url = str(row["data"].get("image_url") or "")
    if image_url.startswith(pin_prefix) and not (
            paths.data / "growth_pins" / image_url.removeprefix(pin_prefix)).exists():
        raise Refused(f"{row['id']}: its Pin image {image_url} has not been generated — not approving")
    already_today = [i for i, record in served.items()
                     if record["day"] == str(at.date()) and record["lane"] == lane]
    if already_today:
        raise Refused(f"{lane}: {already_today[0]} was already approved today — "
                      "one article per lane per day keeps Pinterest from pinning a batch")

    entry = dict(row["data"])
    entry["qa_approved"] = True
    entry["published_at"] = at.isoformat()
    entry["approved_by"] = "owner batch approval (activation runbook)"
    paths.served.mkdir(parents=True, exist_ok=True)
    _write_json(paths.served / f"{row['id']}.json", entry)
    row["file"].unlink()
    return {"id": row["id"], "lane": lane, "order": row["order"],
            "campaign": row["campaign"], "published_at": entry["published_at"],
            "url": f"/libra/growth/articles/{row['id']}"}


def feed_check(url: str = LOCAL_FEED_URL) -> dict:
    """Read the feed the way Pinterest will and report what it holds. Read-only:
    fetching our own feed publishes nothing and records no event."""
    try:
        with urllib.request.urlopen(url, timeout=15) as response:
            status, body = response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return {"status": error.code, "items": 0,
                "note": "404 means the channel is still closed" if error.code == 404 else "error"}
    except (urllib.error.URLError, TimeoutError) as error:
        raise Refused(f"cannot reach the feed at {url}: {error}") from error
    root = ET.fromstring(body)
    if root.tag != "rss" or root.get("version") != "2.0":
        raise Refused("the served feed is not RSS 2.0")
    items = root.find("channel").findall("item")
    return {"status": status, "items": len(items),
            "guids": [item.findtext("guid") for item in items],
            "links": [item.findtext("link") for item in items]}


def record_publication(paths: Paths, *, channel: str, slug: str, url: str, evidence: str,
                       observed_at: str | None = None, extra: dict | None = None) -> dict:
    """Record a publication we actually saw, and start the clock on the first one.
    The window starts here and nowhere else — never on a typed date, never on a
    prepared file, never on the first click."""
    if channel not in LANES:
        raise Refused(f"unknown channel {channel!r}")
    if not url.startswith("https://"):
        raise Refused("a verified publication needs the live https url we opened")
    if not evidence.strip():
        raise Refused("a verified publication needs evidence of what was checked")
    experiment = _read_json(paths.experiment)
    if slug not in (experiment.get("books") or []):
        raise Refused(f"{slug!r} is not a book of this experiment")
    publications = list(experiment.get("publications") or [])
    if any(record.get("url") == url for record in publications):
        return {"changed": False, "publications": len(publications),
                "active": bool(experiment.get("active"))}
    record = {"channel": channel, "slug": slug, "url": url,
              "observed_at": observed_at or _now().isoformat(),
              "evidence": evidence.strip()}
    # Extra fields say how the publication was established (a Pin we opened, or
    # server-side rendering evidence). They never overwrite the five that make a
    # record valid.
    for key, value in (extra or {}).items():
        record.setdefault(key, value)
    publications.append(record)
    experiment["publications"] = publications
    started = False
    if not experiment.get("active"):
        experiment["active"] = True
        experiment["activated_at"] = _now().isoformat()
        started = True
    _write_json(paths.experiment, experiment)
    return {"changed": True, "clock_started": started, "publications": len(publications),
            "window_start": publications[0]["observed_at"]}


def status(paths: Paths) -> int:
    """The existing read-only report, unchanged — this command only calls it."""
    return subprocess.call([sys.executable, str(paths.root / "scripts" / "organic_experiment_report.py")],
                           cwd=str(paths.root))


def day0(paths: Paths, *, owner: str, confirm: str) -> dict:
    """Everything the server side of Day 0 consists of, in one run: authorize the
    channel, approve the first article of each lane, read back the feed."""
    if confirm != CONFIRM_PHRASE:
        raise Refused("the confirmation phrase does not match — nothing was changed")
    checks = preflight(paths)
    if not checks["ok"]:
        raise Refused("preflight failed: " + "; ".join(checks["problems"]))
    result = {"authorization": authorize(paths, owner=owner), "approved": []}
    for lane in LANES:
        try:
            result["approved"].append(approve_next(paths, lane=lane))
        except Refused as error:
            result.setdefault("skipped", []).append(str(error))
    result["feed"] = feed_check()
    return result


def _print(payload) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", default=str(LIBRA_DIR))
    parser.add_argument("--kdp-dir", default=str(KDP_DIR))
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("preflight")
    authorize_parser = sub.add_parser("authorize")
    authorize_parser.add_argument("--owner", required=True)
    authorize_parser.add_argument("--confirm", required=True)
    approve_parser = sub.add_parser("approve-next")
    approve_parser.add_argument("--lane", default="pinterest-rss", choices=list(LANES))
    sub.add_parser("feed-check").add_argument("--url", default=LOCAL_FEED_URL)
    publication = sub.add_parser("record-publication")
    publication.add_argument("--channel", required=True, choices=list(LANES))
    publication.add_argument("--slug", required=True)
    publication.add_argument("--url", required=True)
    publication.add_argument("--evidence", required=True)
    publication.add_argument("--observed-at")
    sub.add_parser("status")
    day0_parser = sub.add_parser("day0")
    day0_parser.add_argument("--owner", required=True)
    day0_parser.add_argument("--confirm", required=True)

    args = parser.parse_args(argv)
    paths = Paths(Path(args.root), Path(args.kdp_dir))
    try:
        if args.command == "preflight":
            checks = preflight(paths)
            _print(checks)
            return 0 if checks["ok"] else 1
        if args.command == "authorize":
            if args.confirm != CONFIRM_PHRASE:
                raise Refused("the confirmation phrase does not match — nothing was changed")
            _print(authorize(paths, owner=args.owner))
        elif args.command == "approve-next":
            _print(approve_next(paths, lane=args.lane))
        elif args.command == "feed-check":
            _print(feed_check(args.url))
        elif args.command == "record-publication":
            _print(record_publication(paths, channel=args.channel, slug=args.slug,
                                      url=args.url, evidence=args.evidence,
                                      observed_at=args.observed_at))
        elif args.command == "status":
            return status(paths)
        elif args.command == "day0":
            _print(day0(paths, owner=args.owner, confirm=args.confirm))
        return 0
    except Refused as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
