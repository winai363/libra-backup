# Libra organic experiment — prepared 2026-09-12

Status: **PREPARED ONLY.** Nothing is running. The experiment starts on the first
day Bui posts a link, because nothing in Libra posts by itself (owner decision
2026-08-30) and nothing on KDP may be touched (TOTAL KDP FREEZE).

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

- **Primary metric:** tracked reader clicks per book per campaign. Crawlers and
  link-preview fetchers are redirected but not counted, so the number is
  readers, not bots.
- **Secondary:** royalties per book per month, latest observation per month.
- **Attribution:** Amazon reports no referrer. A click and a royalty are two
  separate observations, never multiplied into a conversion rate. No purchase is
  ever attributed to a click.
- **Continue if:** ≥25 tracked clicks across the three books in 30 days and no
  book leaves LIVE.
- **Stop if:** zero tracked clicks by day 14, or any tracked book shows up as
  IN_REVIEW / BLOCKED / UNPUBLISHED on the bookshelf.
- **Insufficient evidence is reported as INCONCLUSIVE**, never as success.

## To start it

1. `data/organic_experiment.json`: set `"active": true`, `"started_on"` to the
   first posting day, `"ends_on"` to 30 days later.
2. Post the links above. The experiment exists only once a link is posted.
3. Read status any time: `python3 scripts/organic_experiment_report.py`
   (`--json` for the payload, `--send` to push the summary to Telegram).

No cron is installed for this. Libra's decision agents were cancelled on
2026-08-30 and nothing was re-enabled here; the report runs when it is asked to,
unless Bui asks for a schedule.

## Rollback

- Experiment: set `"active": false` (or delete `data/organic_experiment.json`).
  The report then reads nothing and prints `inactive`.
- Declared campaigns: delete `data/growth_campaigns.json` — hub links fall back
  to the `content-hub` campaign, pages keep working.
- Click counting and takedown alerts: `git checkout` the three changed files
  (`app.py`, `content_hub.py`, `kdp_bookshelf_roster.py`) and restart
  `libra.service`. Nothing in this change writes to KDP or to any provider.
