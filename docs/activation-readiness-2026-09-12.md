# Activation readiness — organic experiment (2026-09-12)

Status: **READY**, pending owner authorization. Nothing is published, the feed
answers 404, and KDP is untouched.

## Owner preview — approve as one batch

Nine articles. Nothing in them needs sentence-level review: the semantic QA pass
is recorded inside each draft (`semantic_qa`), and no draft is left with material
uncertainty. What the owner is approving is the batch and the channel, not the
prose.

| # | Article | Book | Channel | Buyer intent | CTA |
|---|---|---|---|---|---|
| 1 | La lista de dos columnas: planificar la mañana con TDAH en cinco minutos | ADHD ES | Pinterest RSS | organising the morning without an app | Ver el cuaderno en Amazon |
| 2 | The ten-minute kitchen word walk: twelve bilingual words, no printer | Bilingual kids | Pinterest RSS | a bilingual activity with no printer | See the book on Amazon |
| 3 | El punto de aterrizaje: dejar de perder las llaves sin recordar nada | ADHD ES | Pinterest RSS | losing keys and wallet every week | Ver el cuaderno en Amazon |
| 4 | When your child answers in the other language: four things to say instead of pushing | Bilingual kids | Pinterest RSS | child understands but will not speak it | See the book on Amazon |
| 5 | La nota de regreso: cómo retomar una tarea después de una interrupción | ADHD ES | Pinterest RSS | losing the thread after interruptions | Ver el cuaderno en Amazon |
| 6 | One story, two languages: the bedtime routine that does not add a minute | Bilingual kids | Pinterest RSS | already reads at bedtime, wants it to count | See the book on Amazon |
| 7 | O que nunca colar em uma IA — e o que colar no lugar | Accountants PT | Owner LinkedIn | client data in a general AI tool | Ver o e-book na Amazon |
| 8 | Três roteiros de IA para olhar um conjunto de lançamentos sem expor o cliente | Accountants PT | Owner LinkedIn | using AI on entries without a project | Ver o e-book na Amazon |
| 9 | Quando a IA inventa a base legal: como conferir antes de usar | Accountants PT | Owner LinkedIn | has seen AI cite a non-existent rule | Ver o e-book na Amazon |

Remaining material uncertainty: **none.** Every flagged item from the first pass
was resolved by rewriting into plainer, region-neutral wording — full list inside
each draft's `semantic_qa.resolved`.

What this is not: none of the nine has been read by a native Spanish, Portuguese
or bilingual human. The drafts are marked `SEMANTIC_QA_PASS`, never "native
verified". If the owner ever wants a native read, it is a nice-to-have, not a
blocker, and it is not a recurring task.

## 30-day calendar

Day 0 is the **first verified publication** — a live URL we opened and recorded.
Offsets below are days from the owner's approval.

| Day | Action | Who |
|---|---|---|
| −1 | Claim the subpath on Pinterest, authorize `pinterest-rss`, connect the feed **before** any article is approved, so each day's new item is fetched as it appears | owner |
| 0 | Publish article 1 (ADHD ES). Send me the Pin URL once it appears → recorded as the verified publication, Day 0 | owner → me |
| 0 | Publish LinkedIn post 7 (PT) | owner |
| 1–5 | Publish articles 2–6, one per day, in the order above | owner (approval) |
| 7 | LinkedIn post 8 (PT) | owner |
| 7 | **Health check only**: feed reachable, Pins exist, clicks recording, books Live. No content changes | me |
| 14 | LinkedIn post 9 (PT) | owner |
| 14 | **Distribution diagnosis** if qualified clicks are still zero: is the Pin live, does the link resolve, are events recorded? No book changes | me |
| 30 | **Decision**: CONTINUE / ITERATE / INCONCLUSIVE / STOP-CHANNEL | me → owner |

Between checkpoints I intervene only for: publication failure, tracking failure,
a KDP status change on a pilot book, a compliance problem, or clearly broken
content. Nothing else. A book is never modified because traffic was weak.

One ordering detail: the feed holds the five newest approved articles. Connecting
it before publishing means each article is fetched on the day it appears; connect
it after all six and the oldest one may never be pinned.

## Pinterest — exact owner steps

1. **Business account.** pinterest.com → log in → Settings → Account management.
   If it says Personal, use "Convert to business account". Free.
2. **Claim the subpath.** Settings → Claimed accounts → Websites → Claim → enter
   `https://newton-winai-klinprasom.incomeinclick.in.th/libra/growth`.
   Pinterest offers three methods; pick **HTML tag** (simplest for us).
3. **Send me the tag.** It looks like
   `<meta name="p:domain_verify" content="…"/>`. Paste it to me in chat — it is a
   public verification string, not a secret. I add it to the hub pages' `<head>`
   and tell you when it is live; you then press "Verify" in Pinterest.
4. **Authorize the channel.** Tell me to open `pinterest-rss`, or edit
   `data/posting_authorization.json` yourself: `authorized: true`,
   `authorized_by: "Bui"`, `authorized_at: <today, ISO-8601>`. The feed starts
   serving immediately; until then it is a 404.
5. **Connect the feed** (desktop only — mobile cannot do this). ▾ top right →
   Settings → Create Pins in bulk → under Auto-publish → Connect RSS feed → URL
   `https://newton-winai-klinprasom.incomeinclick.in.th/libra/growth/feed.xml`
   → choose the board (one board is enough; "Rutinas y organización" or similar).
6. **Send me the first Pin URL** when it appears (within 24 hours). That is Day 0.

I do not log in, do not use the Pinterest API, and run no browser automation on
the account. Steps 1, 2, 4 (if you prefer), 5 are yours alone.

## iCloud KDP notices — exact owner steps

Pick **one**. B is faster; A gives the watcher direct access.

**A. App-specific password (5 minutes)**
1. appleid.apple.com → Sign-In and Security → App-Specific Passwords → "+".
2. Name it `newton mail watch`. Apple shows a 16-character password **once**.
3. Do **not** paste it in chat. Put it in the file yourself:
   `nano /root/.config/mail-watch/icloud.env` — copy the template first with
   `cp /root/.config/mail-watch/icloud.env.example /root/.config/mail-watch/icloud.env`,
   fill `IMAP_USER` (your Apple ID address) and `IMAP_APP_PASSWORD`, save,
   then `chmod 600 /root/.config/mail-watch/icloud.env`.
4. Tell me it is in place. I run
   `python3 /root/libra/scripts/mail_watch.py --check --account icloud`
   and confirm `{"state": "ok"}`. The existing 10-minute cron picks it up — no new
   job, no code change.

**B. Selective forwarding (3 minutes, no password anywhere)**
1. icloud.com/mail → Settings (gear) → Rules → Add a Rule.
2. Condition: "From" contains `kdp` → Action: "Forward to"
   `winai363@gmail.com`. Save.
3. Add a second rule for "From" contains `amazon` if you want account notices too.
4. Tell me when it is on; the Gmail watcher already matches those senders, and a
   duplicate of the same notice alerts only once (Message-ID dedup).

Secrets stay out of chat and out of git either way: `.env` files under
`/root/.config/mail-watch/` are not in the repository.

## Feature freeze

From this pass until the Day-30 decision, the organic experiment is **frozen**:
no new channels, books, content formats, dashboards, agents, attribution systems
or architecture changes. Exceptions are production failures only — publication,
tracking, KDP status, compliance, or broken content. Recorded in `CLAUDE.md` and
in `data/organic_experiment.json` → `feature_freeze`.

## Activation checklist (not executed)

```
# 1. owner: Pinterest business account + claim /libra/growth  (manual)
# 2. owner: send me the p:domain_verify tag                    (manual)
# 3. open the channel (owner edit, or ask me):
#    data/posting_authorization.json → authorized true + authorized_by + authorized_at
# 4. approve the batch — ask me, or do it yourself:
#    for each draft, in publication_order: set qa_approved true, set published_at,
#    move data/growth_articles_drafts/<id>.json → data/growth_articles/<id>.json
# 5. verify the feed serves what you expect (read-only, no publishing):
curl -s https://newton-winai-klinprasom.incomeinclick.in.th/libra/growth/feed.xml | head -40
# 6. owner: connect that URL in Pinterest → Settings → Create Pins in bulk → Auto-publish
# 7. owner: send me the first Pin URL → I record the verified publication (Day 0)
# 8. status any time (read-only):
python3 /root/libra/scripts/organic_experiment_report.py
```
