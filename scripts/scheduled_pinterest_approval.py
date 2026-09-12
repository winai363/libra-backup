#!/usr/bin/env python3
"""scheduled_pinterest_approval.py — the owner-authorized 5-day publication
schedule for the Pinterest lane of the organic experiment (13-17 Sep 2026,
09:00 Asia/Bangkok, one article per run).

It is a thin, heavily fenced wrapper around
`activate_organic_experiment.approve_next(lane="pinterest-rss")`, which keeps
every existing gate: QA approval, the book still Live on the shelf, the declared
campaign, publication order, one article per lane per day, and a published_at
that is never in the future.

What this wrapper adds, and nothing more:
  - a hard date window. Outside 13-17 Sep 2026 it is a no-op, so the crontab line
    can never fire again next September. The guard lives here rather than in a
    cron line that deletes itself, because a self-deleting one-shot cron has
    already failed once on this server (2026-09-12 cleanup).
  - one run at a time (an exclusive lock), so an overlapping or duplicated
    invocation cannot approve a second article.
  - exactly one approval attempt per run. A failed day is reported and then
    left alone: the next day approves one article, never two. No catch-up.

What it never does: generate content, post to LinkedIn or any network, touch KDP,
record a publication, start the experiment clock, or approve more than one
article in a single run.

    python3 scripts/scheduled_pinterest_approval.py --check   # read-only
    python3 scripts/scheduled_pinterest_approval.py           # the scheduled run
"""
from __future__ import annotations

import argparse
import fcntl
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

LIBRA_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(LIBRA_DIR / "scripts"))

import activate_organic_experiment as activation  # noqa: E402
from organic_experiment_report import send_telegram  # noqa: E402

LANE = "pinterest-rss"
TIMEZONE = ZoneInfo("Asia/Bangkok")
WINDOW_FIRST_DAY = date(2026, 9, 13)
WINDOW_LAST_DAY = date(2026, 9, 17)
LOCK_FILE = LIBRA_DIR / "data" / ".pinterest-approval.lock"


def now_in_bangkok() -> datetime:
    return datetime.now(TIMEZONE)


def _paths() -> activation.Paths:
    return activation.Paths(LIBRA_DIR, activation.KDP_DIR)


def next_eligible(paths: activation.Paths | None = None):
    """The article the next run would approve, read-only."""
    paths = paths or _paths()
    served = activation._served_ids(paths)
    pending = [row for row in activation.prepared_articles(paths)
               if row["lane"] == LANE and row["id"] not in served]
    return pending[0] if pending else None


def run(*, now: datetime | None = None, check_only: bool = False) -> dict:
    """One scheduled run. `now` is the moment the run happens — it decides both
    whether we are inside the window and the article's published_at, so the two
    can never disagree."""
    now = now or now_in_bangkok()
    today = now.date()
    if not WINDOW_FIRST_DAY <= today <= WINDOW_LAST_DAY:
        return {"state": "outside_window", "today": str(today),
                "window": [str(WINDOW_FIRST_DAY), str(WINDOW_LAST_DAY)]}

    candidate = next_eligible()
    if check_only:
        return {"state": "check", "today": str(today), "lane": LANE,
                "next_eligible": candidate["id"] if candidate else None,
                "would_approve": bool(candidate)}

    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOCK_FILE, "a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            # Another run holds the lock. Never queue behind it: waiting would
            # approve a second article the moment the first finished.
            return {"state": "locked", "today": str(today)}
        try:
            approved = activation.approve_next(_paths(), lane=LANE, at=now)
        except activation.Refused as error:
            send_telegram(f"⚠️ Libra Pinterest schedule {today}: no article approved — {error}")
            return {"state": "refused", "today": str(today), "reason": str(error)}
        return {"state": "approved", "today": str(today), **approved}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--check", action="store_true",
                        help="report what the next run would do; writes nothing")
    args = parser.parse_args(argv)
    result = run(check_only=args.check)
    print(json.dumps(result, ensure_ascii=False))
    return 1 if result["state"] == "refused" else 0


if __name__ == "__main__":
    raise SystemExit(main())
