# Libra organic experiment — autonomy map (2026-09-12)

Day 0 is **2026-09-12**, started automatically. Window closes 2026-10-12.

## What runs without the owner

| Job | When | What it does |
|---|---|---|
| `scripts/scheduled_pinterest_approval.py` | `0 9 13-17 9 *` (09:00 +07) | Approves one prepared Pinterest article per day, in order, through the same gates as a manual approval. No catch-up, no duplicates, stops when the inventory is exhausted. |
| `scripts/organic_autopilot.py --all` | `7 * * * *` (hourly) | Verification + Day 0, book safety pauses, feed ingestion health, KDP-mailbox nag (once), day 7/14/30 checkpoints. |
| `scripts/organic_experiment_report.py --send` | `55 9 * * *` | The daily read-only report (pre-existing). |
| `kdp_bookshelf_roster.py --alert` | `45 8 * * *` | The daily shelf scrape the safety step reads (pre-existing). |
| `scripts/mail_watch.py` | `*/10 * * * *` | Gmail KDP-notice watcher (pre-existing). |

## How Day 0 started without a Pin URL

Pinterest does not tell a publisher where it put a Pin. The supported way to read
that is the Pinterest API with the owner's OAuth token, which we do not have and
will not fake. What we do have is our own nginx access log, and Pinterest's
behaviour in it is unambiguous:

```
"GET /libra/growth"                       "… Domain Verifier"       200   ← claim verified
"GET /libra/growth/feed.xml"              "… Pinterestbot/1.0 …"    200   ← feed ingested (×3)
"GET /libra/growth/articles/adhd-…-es"    "… Pinterestbot/1.0 …"    200   ← destination crawled
"GET /libra/api/books/adhd-…-es/cover"    "Pinterest/0.2 (+…)"      200   ← Pin image pulled
```

The crawl of the destination page **and** the image fetch, both after the article
was published, is what Pinterest does when it materialises a Pin from a feed item.
`scripts/pinterest_evidence.py` reads exactly that, and only that: no login, no
API, no scraping of Pinterest, no cookies, no stealth. IP addresses are parsed and
discarded.

The recorded publication says what it is:

```json
{"channel": "pinterest-rss", "slug": "adhd-adults-workbook-es",
 "url": "https://…/libra/growth/articles/adhd-lista-dos-columnas-es",
 "observed_at": "2026-09-12T13:45:50+07:00",
 "pin_url": null, "evidence_type": "pinterest_render_logs",
 "url_kind": "destination article on our own site, not the Pin"}
```

`pin_url` stays null and the evidence sentence states that no Pin page was opened.
This is an inference from server-side behaviour, one step weaker than opening the
Pin, and it is labelled that way everywhere it is stored. A later Pinterest
referral (a real browser arriving at the article with a pinterest referrer) is
recorded as confirming evidence; it is never needed to start the clock, because
it depends on someone clicking.

## Safety

- A pilot book whose shelf row turns `IN_REVIEW` / `BLOCKED` / `UNPUBLISHED` /
  `DRAFT` is paused **on our side only**: its campaigns stop taking new slots
  (`data/organic_pauses.json`, enforced inside `approve_next`). The KDP listing is
  never edited, resubmitted, appealed, repriced or republished. One alert per
  incident; the pause releases itself when the shelf recovers.
- `Live - Updates in review` is watched, not paused: the book is still selling.
- Day 30 may close the Pinterest channel (`STOP-CHANNEL`) by writing
  `authorized: false`. That is the strongest automatic action in the system, and
  the owner reverses it by editing one file. No verdict touches a book.

## Telegram policy

Sends only: Day 0 started · a book paused · feed ingestion stale · a step failed
three runs in a row · day 7 · day 14 · day 30 · the one-time iCloud setup ask.
Everything else is silent — a successful hourly run says nothing.

## Self-healing

Each step is independent and idempotent. A failing step is counted, retried on the
next hourly run, and alerts only at three consecutive failures. An exclusive lock
means a second run (or an overlapping cron) refuses instead of queueing, so
nothing can double-publish or double-record. Alerts are keyed, so a persistent
condition produces one message, not one per hour. Recovery is "the next run does
the right thing", never a burst.

## Routine owner actions: none (2026-09-12, second pass)

Two recurring dependencies were removed by owner decision rather than by writing
more automation:

- **LinkedIn is out of the active experiment.** The lane is `HOLD /
  FUTURE_CHANNEL` in `data/organic_experiment.json`; `approve_next` refuses a held
  lane. The prepared PT content is preserved untouched, and no substitute channel
  is introduced during the experiment.
- **No manual Pin movement.** Every Pin lands on the connected board. Board
  organisation is cosmetic; a second feed/board may be *proposed* after Day 30.

`ONE_TIME_SETUP_PENDING`: the iCloud credential or forward rule for KDP notices —
asked once, never repeated. Until it exists the daily shelf scrape is the KDP
safety source, and the KDP freeze is not weakened because email is missing.

`EXCEPTION_GATES` (never automated, always fail closed, and not counted against
routine autonomy): MFA · CAPTCHA · forced reauthentication · platform ownership
verification · account restriction · legal or policy dispute · irreversible KDP
action · initial secret provisioning.

## Deliberately not built yet

Bounded automatic content generation and new-book research are **not**
implemented. Day 30 only sets a flag: CONTINUE writes `phase2_unlocked`, ITERATE
writes a local `iteration_plan` (which books got reach, which did not — no model
call, no new platform), INCONCLUSIVE scales nothing, STOP-CHANNEL closes the
channel. Both are post-Day-30 by the owner's own
condition ("only if CONTINUE is supported by evidence") and by the feature freeze
recorded in `CLAUDE.md` and `data/organic_experiment.json → feature_freeze`, which
forbids new content formats, agents and architecture until the Day-30 decision.
Building a generator now would also make the 30-day measurement dishonest: the
inventory under test would change mid-window.

The design is agreed and unchanged: buyer-intent evidence → brief → draft →
semantic QA → claims/compliance QA → duplicate check → campaign mapping →
publication, with a daily cap, a token budget, a saturation check, a quality
threshold, and a refusal to publish below confidence. It gets built when the Day-30
verdict is CONTINUE and the owner says so.
