# Claude Code Handoff: Libra 100% Auto KDP Publisher

Updated: 2026-06-07 00:45 Asia/Bangkok

## User Goal

Build Libra into a reliable 100% automated KDP publishing business system:

1. Market research: buying power, growth, demand, low competition, marketplace/language fit.
2. Book production: professional international layout, legal/policy compliance, factual quality,
   useful content, 40+ real paperback pages, correct AI disclosure.
3. KDP publishing and SEO: localized title/subtitle/description, 7 backend keyword phrases,
   categories, pricing, tags, visibility strategy, publish verification.
4. Design any remaining components needed for an end-to-end feedback loop.
5. Do not report ready or reactivate automation until tests prove the complete system works.

## Critical Current State

- ALL root cron jobs are disabled. Do not restore until the complete Libra test suite passes.
- Crontab backup: `/root/backups/root-crontab-before-libra-fix-20260607-001410.txt`
- No KDP upload should be triggered during development/testing.
- `libra.service` is active on localhost:8200 and deployed behind `/libra/`.
- The user explicitly wants full automation, not mandatory human approval.
- Use machine quality approval; fail closed and notify on failure.

## Work Completed

### Security

- Telegram tokens were redacted from 28 log occurrences; rescan found zero.
- `.env`, `kdp_session.json`, queue files, and KDP logs changed to mode 600.
- Disabled `httpx` INFO logging in app/upload code.
- Login now has 5-attempt/15-minute IP rate limiting.
- Cookie now has `HttpOnly`, `Secure`, `SameSite=Strict`, one-day expiry, `/libra` path.
- Added CSP, HSTS, X-Frame-Options, nosniff, Referrer-Policy, Permissions-Policy.
- `/api/pipeline-status`, `/status`, `/audit`, preview/review/approval pages now require auth.
- Added slug validation to prevent path traversal.
- Preview Markdown is rendered as text, not unsafe HTML.
- Dashboard escapes AI/listing values.
- Dashboard can no longer manually mark a book uploaded.

### Publishing and Queue

- Added `/root/libra/quality_gate.py`.
- Added `/root/libra/editorial_review.py`.
- Added `/root/libra/upgrade_existing_books.py`.
- Queue removes an item only after success; failures remain queued.
- Corrected audit path mismatch: `audit/corrected/corrected.epub`.
- Existing KDP IDs use `kdp_upload.py --update`; new books use normal flow.
- Upload blocks before browser launch unless quality gate passes.
- Unconfirmed publish no longer becomes `uploaded`.
- Success clears stale `kdp_error`.
- Update mode now requires confirmed save and republish.
- Background API upload logs to file instead of `PIPE` deadlock risk.
- AI disclosure corrected:
  - Text: `Entire work`, tool `GPT-4.1`
  - Images: `Some images`, tool `gpt-image-1`
  - Translations: `None`
- Important: KDP official guidelines state AI-generated cover art counts as AI-generated images.

### Quality and Book Production

- New non-fiction minimum: 10,000 words and actual PDF >= 40 pages.
- Fiction minimum: 20,000 words and actual PDF >= 40 pages.
- Required: 12+ sections, 8 references/URLs for non-fiction, citations matched, 7 unique SEO
  keyword phrases, 2 categories, description length, cover 1600x2560 RGB, EPUB, research files.
- Editorial AI requires all seven scores >= 8:
  language, reader value, structure, facts, citations, SEO, originality.
- Editorial review uses web search and must fail on critical issues/contradicted claims.
- New books default to text-first interior with no AI interior images.
- EPUB now embeds `cover.jpg` and `epub.css`.
- New auto-generate target reduced from two books/day to one quality-gated book/day.
- Writer rejects fiction for future automatic topic selection.
- Dashboard has `QA Failed` tab.
- `requirements.txt` expanded for actual runtime dependencies.

## Verification Already Passed

- Python compileall: pass.
- All shell scripts `bash -n`: pass.
- Dashboard JavaScript `node --check`: pass.
- `git diff --check`: pass.
- Synthetic quality gate pass and fail cases: pass.
- Public `/api/pipeline-status`: HTTP 401.
- Authenticated pipeline: HTTP 200, 11 books.
- HTTPS security headers present.
- Login cookie confirmed `HttpOnly; Secure; SameSite=strict`.
- `libra.service` restarted and active.
- No current cron jobs.

## Existing Books

All 11 old books are currently blocked by the new gate. Do not upload them as-is.
They are below the word target and/or lack editorial approval. Several already have 40+ PDF pages,
but word quality and editorial requirements still fail.

### Pilot Upgrade Result

Book: `anxiety-workbook-young-women-de`

- Original: 2,678 words, 20 PDF pages.
- Upgraded manuscript reached about 11,069 words and 76 PDF pages.
- Latest editorial scores reached 8-9 in every category.
- A review returned `recommended_action: pass` but `passed: false` because code requires at least
  five fact checks; the model returned only three. This is the immediate bug to fix.
- Earlier review correctly blocked broken URLs and one uncertain claim.
- Upgrade process was manually interrupted to preserve credits while it attempted an unnecessary
  rewrite after this false failure.
- Backups exist under:
  `/root/kdp/anxiety-workbook-young-women-de/backups/before-quality-upgrade-*`
- Inspect current `listing.json`, `editorial-review.json`, `quality-report.json`, EPUB and PDF before
  resuming. Do not discard current 11k/76-page manuscript.

## Immediate Bugs / Next Work

1. Fix editorial fact-check count:
   - Current code requires 5 fact checks for non-fiction.
   - Prompt says at least five, but model may return three.
   - Either make JSON schema enforcement/retry request for missing checks, or accept 3 strong checks.
   - Do not rewrite a good manuscript solely because the reviewer response format was incomplete.
2. Separate infrastructure/reviewer failure from editorial failure:
   - Rate limit/API failure must retry with backoff and not trigger manuscript rewrite.
   - `editorial_review.py` now retries 429 every 65 seconds, but upgrade orchestration must classify
     failure types explicitly.
3. Resume pilot using repair mode:
   `python3 /root/libra/upgrade_existing_books.py --repair anxiety-workbook-young-women-de`
   Do not use normal mode unless current manuscript is demonstrably bad.
4. Validate broken URLs after repair. Quality gate ignores 401/403/429 but fails real 4xx/5xx.
5. When pilot passes, run full regression and manually inspect PDF/EPUB.
6. Upgrade remaining books one at a time, not all in parallel due 30k TPM API limit.
7. The Romantasy book is a product mismatch:
   listing says novel, manuscript is an instructional essay. Rewrite as a real 20k+ novella or archive.
8. Build market intelligence scoring before reactivating generation:
   demand evidence, buyer intent, trend, competition proxy, differentiation, legal risk,
   language saturation, expected price/royalty, go/no-go score.
9. Add post-publication feedback loop:
   impressions/rank/sales/reads/reviews, keyword/category experiments, 7/30-day review,
   no metadata churn without sufficient evidence.
10. Add tests for market scoring, editorial retry, queue idempotency, publish confirmation,
    status transitions, and KDP selector failure.

## Design Requirements Still Missing

- Market research currently uses generic web search and qualitative topic choice. It needs a saved
  structured `market-score.json` and hard go/no-go thresholds.
- No plagiarism/similarity detector across Libra books yet.
- Language purity is only AI-reviewed; add a deterministic heuristic where practical.
- Legal/high-risk topic classifier is missing. Medical, financial, legal, children, copyrighted
  companion works, trademarks, and public-domain claims need stricter routing/blocking.
- No automatic pricing strategy by marketplace/category/length.
- No Amazon performance ingestion or SEO learning loop.
- No full integration test with a non-publishing KDP dry-run boundary.
- `requirements.txt` is not version-pinned.
- Git repository remains dirty with many important untracked files. Do not lose user changes.

## Git / Backup State

- Repo: `/root/libra`
- HEAD: `62fc448 feat: handle Amazon AI Questionnaire, bypass re-auth, add EPUB justified CSS`
- `origin/main` is one commit behind local.
- Many implementation files remain untracked.
- A GitHub push previously failed 403. Do not claim full backup exists.
- Pre-fix code backup:
  `/root/backups/libra-audit-fix-20260607-001300`
- Root crontab backup:
  `/root/backups/root-crontab-before-libra-fix-20260607-001410.txt`

## Safe Resume Commands

```bash
cd /root/libra
cat CLAUDE_HANDOFF_LIBRA_AUTOPUBLISH.md
crontab -l  # must say no crontab for root
systemctl status libra.service --no-pager
ps auxww | rg -i '[k]dp_upload|[u]pgrade_existing|[e]ditorial_review'
python3 -m compileall -q .
for f in *.sh scripts/*.sh; do bash -n "$f" || exit 1; done
```

## Definition of Done Before Restoring Cron

- New-book pipeline passes market go/no-go, deterministic QA, editorial QA, real 40+ page PDF,
  EPUB inspection, SEO metadata QA, legal/policy QA, and correct AI disclosure.
- Existing-book update pipeline changes the intended KDP ID and verifies republish success.
- Failures are retried safely without duplicate books, lost queue items, or false success.
- Full tests pass twice from clean test fixtures.
- At least one staged end-to-end run reaches the final KDP confirmation boundary without publishing,
  followed by one explicitly authorized real publish/update.
- All 11 existing books are upgraded or intentionally archived.
- Root cron restored from backup only after all above checks pass.
- Update `memory.md` and `telos.md`, commit tracked code, and clearly report any GitHub push limitation.
