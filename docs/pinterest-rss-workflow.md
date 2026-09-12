# Pinterest native RSS → hub page → Amazon (prepared 2026-09-12)

Status: **PREPARED_ONLY.** The feed is built and tested locally, the channel is
**not authorized**, no Pinterest account is connected, and `/libra/growth/feed.xml`
returns 404 today. Nothing here touches KDP.

## What Pinterest actually requires

From the official help pages, read 2026-09-12:

- *Auto-publish Pins from your RSS feed*: "We support RSS 2.* and RSS 1.* (RDF)
  formats. We currently do not support Atom." Each item needs a `<title>` and
  `<description>`, a link on the claimed domain, and an image from `<image>`,
  `<enclosure>` or `<media:content>`. "When you update your RSS feed, your
  content will be added to your boards as Pins within 24 hours", "The oldest
  content on your RSS feed will be published first", and "Pins will be created
  with a limit of up to 200 Pins per day". Multiple feeds are allowed "as long as
  they match your claimed website".
- *Claim your website*: "To claim a website, you must own the domain, subdomain
  or subpath, and you need to be able to edit the source code." Four methods:
  Google Merchant Center, HTML tag, HTML file, or DNS TXT record. A business
  account is recommended, not strictly required; auto-publish is documented as a
  business feature, so treat the business account as required.

### What that means for us

| Requirement | Our position |
|---|---|
| Business account | **UNKNOWN** — only the owner can see it. HUMAN_REQUIRED |
| Claimed domain/subdomain/subpath | Claimable: we serve `/libra/growth/*` ourselves, so the HTML-tag or HTML-file method is feasible on that subpath. DNS TXT is not ours to set (platform DNS). **Not claimed yet** |
| RSS 2.0, no Atom | Feed is RSS 2.0 with a `media:` namespace. Verified by test |
| Link on claimed domain | Enforced in code: an off-site or non-`/libra/growth/` link is rejected, not silently dropped |
| Image in an accepted tag | Each item carries `<media:content>` and `<enclosure>`; the image is the book cover served from `/libra/api/books/<slug>/cover` — our own artwork |
| Oldest first, 200/day | Feed is capped (5 items by default, hard cap 20), so a first connection cannot fire a backlog |
| Image rights | Covers are ours (generated in this project). No third-party image enters the feed |
| Language / topic fit | Entries are per-article and language-tagged by the article. Spanish ADHD routines, Portuguese accounting workflows and bilingual-parent vocabulary are ordinary Pinterest topics; no medical claims allowed in an approved entry (QA checklist in the draft) |

## The workflow

```
article we wrote (original, QA-approved)
  → data/growth_articles/<id>.json            (served at /libra/growth/articles/<id>)
  → /libra/growth/feed.xml                    (RSS 2.0, gated on channel authorization)
  → Pinterest native auto-publish             (Pinterest creates the Pins, not us)
  → reader lands on our hub article
  → one tracked CTA → /growth/out/<token> → Amazon listing
  → hub_events records a qualified reader click (crawlers excluded)
```

No posting code exists and none is planned: Pinterest pulls the feed. That is
why this channel is compatible with the standing no-auto-posting rule — but it
is still gated, because a served feed is a public distribution channel.

## Guards in code

- `posting_authorization.channel_authorized("pinterest-rss")` — fail closed.
  `authorized: true` is not enough: `authorized_by` and `authorized_at` must also
  be filled, so a stray edit cannot open the channel. Unknown channel names are
  never authorized. While closed, the route answers 404.
- `growth_feed.validate_entry` rejects, loudly, any entry that is not
  `qa_approved`, is off-site, links outside `/libra/growth/`, has an image outside
  `/libra/api/books/` or `/libra/static/`, is future-dated, has a title outside
  10–140 characters or a description outside 30–500, or carries a key that would
  smuggle book content (`manuscript`, `ebook_md`, `sample_text`, `epub`, `pdf`,
  `chapter_text`). Rejections are logged with the reason.
- Feed size cap prevents a Pin flood; `guid` is stable per entry so a rebuild
  does not re-pin the same article.
- Drafts live in `data/growth_articles_drafts/`, which is **not** served and not
  read by the feed. One draft exists today
  (`adhd-tres-rutinas-es.json`, `qa_approved: false`) with its QA checklist.

## Owner steps to turn it on (one time, in this order)

1. Confirm (or create) a Pinterest **business** account.
2. Claim `https://newton-winai-klinprasom.incomeinclick.in.th/libra/growth` on
   that account — ask me for the HTML tag or file once Pinterest gives it to you;
   serving it from our path is a small change I can make.
3. QA the draft article, then move it into `data/growth_articles/` with
   `qa_approved: true` and a real `published_at`.
4. Authorize the channel in `data/posting_authorization.json`:
   `authorized: true`, `authorized_by`, `authorized_at`. The feed starts serving.
5. In Pinterest: Settings → Bulk create Pins → add the feed URL
   `https://newton-winai-klinprasom.incomeinclick.in.th/libra/growth/feed.xml`
   and pick the board.
6. When a Pin actually appears, give me its URL. I record it in
   `data/organic_experiment.json` → `publications` with the time and what was
   checked. **That record, not the feed file and not the cron, is what starts the
   experiment window.**

## Rollback

Set `authorized: false` (or delete `data/posting_authorization.json`) → the feed
404s immediately. Remove the feed in Pinterest's settings to stop new Pins;
existing Pins stay until deleted in Pinterest. Nothing else in Libra changes, and
KDP is untouched either way.
