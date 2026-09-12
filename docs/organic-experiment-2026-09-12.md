# Libra organic experiment — prepared 2026-09-12

Status: **PREPARED ONLY.** Nothing is published. The measurement window starts at
the first *verified* publication — a live url we have opened and recorded — not at
a date, not at a prepared file, not at an installed cron. Libra posts nothing by
itself (owner decision 2026-08-30), and nothing on KDP may be touched (TOTAL KDP
FREEZE); both rules stay in force here.

## Why this shape

Every lever on the KDP side — price, keywords, categories, description, cover,
A+ content, free promos, new titles — is closed by owner order, and a price
change is what triggered the review that blocked the Spanish ADHD ebook. So the
only lane left for growing royalties from the existing catalogue is traffic from
outside Amazon to listings that are already live.

The machinery for that already exists and is now verified working: a hub page per
book on our own server with exactly one signed, tracked CTA to the Amazon
listing.

## The three books

Chosen from the eight books that earned anything between 2026-07-11 and
2026-09-12, then filtered by the read-only full-catalogue QA run
(`/root/downloads/libra-audit-2026-09-12/`):

| Book | ASIN | Baseline | Why it is in |
|---|---|---|---|
| adhd-adults-workbook-es | B0H6VB1SDX | Aug $1.44 / 274 KENP · Sep MTD $7.92 / 3 orders | Best-earning live book; 274 KENP means real readers finish pages, and the EPUB passes epubcheck with no unmet promise |
| ai-workflows-accountants-pt | B0H3WY9M22 | Jul $3.32 / 11 orders | Narrow professional buyer, clean EPUB, no image promise |
| bilingual-english-spanish-kids-vocab | B0H4V4DRLV | Aug $2.86 / 1 order / 70 KENP | Defined buyer (bilingual parents), clean EPUB |

Deliberately excluded, with reasons, in `data/organic_experiment.json`:
`senior-smartphone-french` (highest earner, but the listing promises interior
images and the EPUB has none — and the fix would need a frozen republish),
`beginner-watercolor-spanish` (visual how-to with zero interior images; its two
sibling titles are the two books Amazon pulled), and
`ai-augmented-productivity-toolkit` (its 25 "orders" are free-promo downloads).

## Links to post

Each link tags its own channel, so two channels posting the same book stay
countable apart. Only campaigns declared in `data/growth_campaigns.json` are
accepted; anything else records under `content-hub`.

```
https://newton-winai-klinprasom.incomeinclick.in.th/libra/growth/books/adhd-adults-workbook-es?c=organic-pinterest
https://newton-winai-klinprasom.incomeinclick.in.th/libra/growth/books/adhd-adults-workbook-es?c=organic-owner-post
https://newton-winai-klinprasom.incomeinclick.in.th/libra/growth/books/ai-workflows-accountants-pt?c=organic-owner-post
https://newton-winai-klinprasom.incomeinclick.in.th/libra/growth/books/bilingual-english-spanish-kids-vocab?c=organic-pinterest
https://newton-winai-klinprasom.incomeinclick.in.th/libra/growth/books/bilingual-english-spanish-kids-vocab?c=organic-owner-post
```

Allowed: posting where the buyer already is (ADHD and productivity communities,
accountant groups, bilingual-parent groups), and Pinterest pins to the hub page.
Not allowed, and not part of this: paid ads, unsolicited DMs, review trading,
vote or engagement manipulation, or link drops in unrelated threads.

## How it is measured

Each track is counted on its own; none is derived from another.

| Track | Source | Today |
|---|---|---|
| content published (verified) | a record in `publications` with a live url, the time we saw it, and what was checked | 0 |
| exposure (impressions, saves) | Pinterest analytics, which we do not read — owner reports it or it stays null | not measured |
| qualified outbound clicks | `hub_events`, crawlers filtered, registered synthetic rows subtracted | 0 |
| paid orders | KDP report, latest observation per month | see baseline |
| KENP | KDP report | see baseline |
| royalties | KDP report | see baseline |
| costs | no paid channel is authorized | $0 paid spend |
| human interventions | `interventions` in the experiment file | 0 |

Rules the report enforces in code:

- **The window starts at the first verified publication.** Not a typed date, not
  the first click, and never a prepared file or an installed cron. A record whose
  url is not `https://`, or which lacks an observation time or evidence, is not a
  publication.
- **25 qualified clicks is a provisional acquisition threshold** — evidence the
  channel reaches readers. It is not sales validation and the report says so on
  the same line.
- **Zero qualified clicks at day 14 triggers a distribution and tracking
  diagnosis**, not a book retirement: is the Pin live, does the link resolve, are
  events being recorded?
- **Shelf status drives a proportionate response.** `IN_REVIEW`, `BLOCKED`,
  `UNPUBLISHED` or `DRAFT` on any format pauses that one book's campaign.
  `Live - Updates in review` means the book is still selling, so it is watched,
  not paused. A book missing from the roster is held, never assumed Live.
- **No inference.** No purchase is attributed to a click; no read-through rate is
  derived from KENP.

## What the books are, exactly

Verified 2026-09-12 from `listing.json` plus the bookshelf roster, with file-level
QA (full record in `data/organic_experiment.json` → `qualification`):

| Book | ebook ASIN | Price | Other format | Select (file record) | Layout note |
|---|---|---|---|---|---|
| adhd-adults-workbook-es | B0H6VB1SDX | $2.99 | paperback B0H7PP17YX $9.99, Live | Enrolled to 2026-09-25 | 195 table rows, up to 8 columns — wide tables reflow badly on a phone |
| ai-workflows-accountants-pt | B0H3WY9M22 | $2.99 | none | Enrolled to 2026-09-29 | 25 table rows, max 4 columns |
| bilingual-english-spanish-kids-vocab | B0H4V4DRLV | $3.49 | none | Enrolled to 2026-09-29 | 163 table rows, max 3 columns |

All three: epubcheck clean, zero interior images but **no image promise** in the
listing, and no write-in blanks inside the ebook — their "templates" tell the
reader to copy the table into a notebook or print it, which is the right handling
for a reflowable Kindle book. The ADHD book's link must point at the ebook ASIN;
the paperback is a different product and may only be mentioned as an alternative.

Still unverified, and stated as such: semantic correctness of the Spanish,
Portuguese and English text (a marker scan found no wrong-language content, which
is not a native reading), and on-device Kindle rendering of the wide ADHD tables
(no Previewer run; a republish is frozen, so this is a caveat on the claim, not a
fix).

Books kept out of the campaign and why: `data/organic_experiment.json` →
`books_excluded_after_qa`. The 30 live books with no royalties are **not** called
unmarketable — no tracked traffic was ever sent to them, so nothing about them has
been tested. Blocked and removed titles are **not** called irrecoverable either:
no recovery route has been attempted or ruled out with evidence.

## Automation coverage, step by step

Denominator: the 14 steps of this workflow end to end.

| # | Step | Status |
|---|---|---|
| 1 | Select and qualify books from catalogue + QA data | AUTO_VERIFIED |
| 2 | Write the article | HUMAN_REQUIRED (draft prepared) |
| 3 | QA-approve the article | HUMAN_REQUIRED (checklist in the draft) |
| 4 | Serve the hub page with one tracked CTA | AUTO_VERIFIED |
| 5 | Build the RSS feed from approved articles | AUTO_VERIFIED (build + rules tested; serving gated) |
| 6 | Authorize the `pinterest-rss` channel | HUMAN_REQUIRED (one time) |
| 7 | Claim the subpath on Pinterest | HUMAN_REQUIRED (one time) |
| 8 | Connect the feed to a board | HUMAN_REQUIRED (one time) |
| 9 | Pins created from the feed | EXTERNAL (Pinterest, ≤24h, ≤200/day) |
| 10 | Count qualified reader clicks, exclude crawlers and synthetic rows | AUTO_VERIFIED (in production) |
| 11 | Record the verified publication | HUMAN_REQUIRED (owner sends the Pin url) |
| 12 | Daily status report | AUTO_VERIFIED (cron 09:55, silent while inactive) |
| 13 | Shelf watch: blocked / in review / unpublished / vanished | AUTO_VERIFIED (cron 08:45) |
| 14 | KDP notice watch (mailbox) | PREPARED_ONLY (needs the iCloud credential or forwarding) |

So: **6 of 14 steps are automated and verified**, 3 are one-time owner setup, 3
are recurring human steps, 1 belongs to Pinterest, and 1 waits on a credential.
No percentage is claimed beyond those counts.

## To start it

1. Follow `docs/pinterest-rss-workflow.md` steps 1–5 (business account, claim,
   QA the article, authorize the channel, connect the feed).
2. Send me the first live Pin or post url. I record it in `publications`, and the
   30-day window starts from that observation.
3. Read status any time: `python3 scripts/organic_experiment_report.py`
   (`--json` for the payload). The cron sends a Telegram summary once the
   experiment is active.

## Rollback

- Experiment: `"active": false` in `data/organic_experiment.json` → the report
  reads nothing and prints `inactive`.
- Channel: `"authorized": false` in `data/posting_authorization.json` → the feed
  404s at once.
- Report job: remove the 09:55 crontab line.
- Code: `git checkout` the changed files and restart `libra.service`.
- Nothing above writes to KDP, and the KDP freeze is independent of all of it.
