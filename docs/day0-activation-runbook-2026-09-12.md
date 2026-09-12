# Day-0 activation runbook — Libra organic experiment (prepared 2026-09-12)

Status: **PREPARED, NOT EXECUTED.** The channel is closed, `/libra/growth/feed.xml`
returns 404 in production, no article is approved, `data/growth_articles` does not
exist, and KDP is untouched. Nothing in this document has been run.

The trigger is the owner saying, in chat:

```
PINTEREST VERIFIED — ACTIVATE LIBRA ORGANIC EXPERIMENT
```

That phrase is also the literal `--confirm` value the activation command requires,
so an accidental run without it changes nothing.

## One command

```bash
cd /root/libra
python3 scripts/activate_organic_experiment.py preflight          # read-only, run first
python3 scripts/activate_organic_experiment.py day0 \
    --owner Bui --confirm "PINTEREST VERIFIED — ACTIVATE LIBRA ORGANIC EXPERIMENT"
```

`day0` performs, in order, and stops at the first refusal:

1. **preflight** — 9 prepared articles present, each with a `SEMANTIC_QA_PASS`
   record, a declared campaign mapped to its own lane, a target book that is
   `LIVE` on the shelf with a matching ASIN, and a cover asset for the feed image.
2. **authorize** — `data/posting_authorization.json` → `pinterest-rss`:
   `authorized: true`, `authorized_by`, `authorized_at` (the run's own timestamp).
   Idempotent: an already-open channel is left exactly as it is.
3. **approve the first article of each lane** — `qa_approved: true`,
   `published_at` = now (never a future date), file moved from
   `data/growth_articles_drafts/` to `data/growth_articles/`.
   Pinterest lane: `adhd-lista-dos-columnas-es`. LinkedIn lane:
   `contabil-o-que-nunca-colar-na-ia`.
4. **feed-check** — fetches `http://127.0.0.1:8200/growth/feed.xml`, asserts RSS
   2.0, and prints the guids and links it holds. Reading our own feed publishes
   nothing and records no click event.

The experiment clock is deliberately **not** started by `day0`. It starts only at
step 7 below, from a publication we actually saw.

### The other commands

```bash
# days 1-5 (Pinterest lane) and days 7, 14 (LinkedIn lane) — one article per run
python3 scripts/activate_organic_experiment.py approve-next --lane pinterest-rss
python3 scripts/activate_organic_experiment.py approve-next --lane owner-post

# the Pin or post is live and I opened it — this is what starts the 30-day window
python3 scripts/activate_organic_experiment.py record-publication \
    --channel pinterest-rss --slug adhd-adults-workbook-es \
    --url https://www.pinterest.com/pin/XXXXXXXX \
    --evidence "opened the pin, it resolves to the hub article and the CTA reaches the ASIN"

python3 scripts/activate_organic_experiment.py status    # the existing read-only report
```

Refusals are deliberate and each one is tested: a wrong confirmation phrase, a
target book that is not Live, a second article of the same lane on the same day,
an article out of publication order, a lane already exhausted, a publication URL
that is not https, a publication with empty evidence, a book outside the
experiment, a feed that is not RSS 2.0.

## What the owner still has to do (Pinterest account actions — not mine)

| # | Step | Where |
|---|---|---|
| 1 | Business account (convert if personal — free) | pinterest.com → Settings → Account management |
| 2 | Claim the subpath `https://newton-winai-klinprasom.incomeinclick.in.th/libra/growth`, method **HTML tag** | Settings → Claimed accounts → Websites |
| 3 | Press Verify — the tag you sent on 2026-09-12 is already live on that page (`<meta name="p:domain_verify" content="b6ff3ac6…">`, verified serving today). Nothing to add, no code change | Pinterest |
| 4 | Create the boards (below) | Pinterest → profile |
| 5 | Say the activation phrase → I run `day0` | chat |
| 6 | Connect the feed: Bulk create Pins → Add RSS feed → `https://newton-winai-klinprasom.incomeinclick.in.th/libra/growth/feed.xml` → board A | Pinterest |
| 7 | Send me the first Pin URL when it appears (≤24h) → I record the verified publication; the clock starts there | chat → me |
| 8 | Post LinkedIn article 1 (`contabil-o-que-nunca-colar-na-ia`) from your own profile | LinkedIn |

I do not log into Pinterest, do not use the Pinterest API and run no browser
automation on the account.

## Board plan (owner creates; I create nothing)

Pinterest's native RSS auto-publish sends **one feed to one board**, and we have
one feed. Splitting into two feeds would need a code change, which the feature
freeze rules out until the Day-30 decision. So: two boards, one connected.

**Board A — connected to the feed (all Pins land here first)**
- Name: `Organización en casa · rutinas TDAH`
- Description: "Rutinas cortas y sistemas simples para organizar el día: listas,
  recordatorios y un sitio fijo para lo que siempre se pierde."
- Language: Spanish. Holds the three `pin-adhd-es` articles.

**Board B — the bilingual lane**
- Name: `Bilingual kids at home`
- Description: "Ten-minute bilingual routines for English/Spanish families that
  use what is already in the house — no printing, no apps."
- Language: English. Holds the three `pin-bilingual-kids` articles.

**The one manual step this costs:** when a `pin-bilingual-kids` Pin appears on
board A (days 1, 3 and 5 — three Pins in total), move it to board B from the Pin's
⋯ menu. Three moves over six days is cheaper than a code change, and it keeps each
board in one language and one topic, which is what Pinterest's distribution reads.

Do not create more boards than this for two pilot books: an empty or thin board is
a weaker signal than a board with three coherent Pins.

## The publication schedule

Day offsets are counted from the day the owner approves the batch. One article per
lane per day — the feed holds the 5 newest items, oldest published first, so a
batch dropped at once would pin everything in one burst and burn the whole lane in
a day.

| Offset | Lane | Article | Campaign | Book |
|---|---|---|---|---|
| 0 | Pinterest | `adhd-lista-dos-columnas-es` | pin-adhd-es | ADHD ES |
| 1 | Pinterest | `bilingual-kitchen-word-walk` | pin-bilingual-kids | Bilingual kids |
| 2 | Pinterest | `adhd-punto-de-aterrizaje-es` | pin-adhd-es | ADHD ES |
| 3 | Pinterest | `bilingual-child-refuses-to-speak` | pin-bilingual-kids | Bilingual kids |
| 4 | Pinterest | `adhd-nota-de-regreso-es` | pin-adhd-es | ADHD ES |
| 5 | Pinterest | `bilingual-same-story-two-languages` | pin-bilingual-kids | Bilingual kids |
| 0 | LinkedIn | `contabil-o-que-nunca-colar-na-ia` | li-contab-pt | Accountants PT |
| 7 | LinkedIn | `contabil-tres-roteiros-conciliacao` | li-contab-pt | Accountants PT |
| 14 | LinkedIn | `contabil-ia-inventa-base-legal` | li-contab-pt | Accountants PT |

After day 5 the feed settles on the five newest Pinterest articles; article 1 has
been fetched long before it rolls out of the window, which is why the feed must be
connected **before** the batch starts, not after.

## The prepared authorization change (not applied)

Production still reads `"authorized": false`. This is the exact change, byte for
byte, that `day0` makes — or that the owner can make by hand:

```diff
   "channels": {
     "pinterest-rss": {
-      "authorized": false,
-      "authorized_by": null,
-      "authorized_at": null,
+      "authorized": true,
+      "authorized_by": "Bui",
+      "authorized_at": "2026-09-DDTHH:MM:SS+07:00",
```

`authorized_at` is the real timestamp of the authorization. `channel_authorized()`
fails closed: `authorized: true` without both `authorized_by` and `authorized_at`
stays a 404, and so does any channel name other than `pinterest-rss`.

## Reporting after Day 0 — already running, no new cron

`55 9 * * *` runs `scripts/organic_experiment_report.py --send`. It is silent
while `data/organic_experiment.json` has `active: false`, and starts reporting the
moment the first verified publication sets it. The report derives the checkpoints
itself from the window start:

- **Day 7** — health and tracking check only: feed reachable, Pins exist, clicks
  recording, books still Live. No content changes.
- **Day 14** — zero qualified clicks reads as `INCONCLUSIVE` with a distribution
  and tracking diagnosis, never as a reason to change a book.
- **Day 30** — `window_closed` flips and the verdict becomes `CONTINUE` (≥25
  qualified clicks), `ITERATE` (some reach, below threshold) or `STOP-CHANNEL`
  (zero clicks after a verified publication). No verdict retires a book.

Our own verification clicks are registered in `/root/shared/synthetic_events.json`
and subtracted by the report; the raw rows stay.

## What this runbook will never do

Touch KDP in any way · post to Pinterest, LinkedIn or anywhere else · log into an
account · add a channel, a book, a dashboard or an agent · start the clock from a
typed date · approve an article whose book is not Live · publish an article with a
future `published_at` · attribute a sale to a click.
