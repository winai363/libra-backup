# Libra editorial review and operating model

Local-file inspection on 2026-09-06. No Amazon actions or manuscript edits.
Local LIVE status does not establish present Amazon availability, nor that the
local EPUB is identical to the last submitted/customer-delivered version.

## Concrete findings

1. senior-smartphone-french: listing.json lines 3/6 and ebook.md line 7 promise
   an illustrated guide. Local ebook.epub has a cover and zero interior images.
   Both editorial-review.json and quality-report.json pass. New EPUB image gate
   returns zero used interior assets. This is a promise/file mismatch.
2. aquarelle-botanique-debutants-fr: listing.json lines 3/6 promise 12 illustrated
   demonstrations. All 12 PNGs are embedded, but demonstration sections 1 and 9
   alone contain images (ebook.md lines 295/468). Other pictures are elsewhere.
   Image-count checks cannot establish coverage of the promised demonstrations.
3. Same watercolor book: ebook.md lines 276/280 describe a single ivy leaf;
   inspected images/step-08.png shows a branch of elongated, unlobed leaves.
   This warrants correction in a future manuscript draft, not a claim about
   Amazon's rejection cause. Only step-08 and step-09 were visually inspected.
4. Both editorial-review.json files put descriptive prose into source_url fields
   (watercolor lines 16/21/31; senior lines 16/21/36). Passing scores do not
   demonstrate retrievable fact-check evidence. Senior ebook.md line 1349 has
   a bibliography item ending in Disponible sur: without its link.
5. Watercolor listing.json line 27 labels text ai_assisted, while its block note
   line 41 records submitted disclosure as entire work. staging-manifest.json
   retains staged_quality_passed while listing records BLOCKED. These are distinct
   dimensions: origin, submitted disclosure, internal checks and platform status.

No external references were verified in this review. No EPUBCheck or paperback
readability review was performed. The findings invalidate the earlier inference
that internal passing scores establish clean content; they do not reveal Amazon's
actual rejection cause.

## New internal workflow

1. Reader brief: one audience, one practical outcome, explicit sales promises.
   Define measurable revenue/profit target with the owner; target currently unknown.
2. Evidence before writing: sources for claims, rights/provenance for assets,
   existing comparable title performance with observation dates and status.
3. Manuscript and file review: map each sales promise to a section and delivered
   artifact. For illustrated demonstrations, review each promised demonstration,
   not total picture count. Verify image subject and steps against the text.
4. Independent editorial check: readable source links and relevant excerpts,
   language review, reader task completion, repetition, scope and Kindle rendering.
   Model-generated scores alone cannot close these checks.
5. Local evidence report: run demand_analysis.py. Account estimates, attribution
   gaps, unknown paid-order/profit values, local rejection register and LIVE-only
   cohort signals are separate. Source dates stay visible.
6. Publishing remains frozen. No autonomous upload, retry, appeal, pricing or
   metadata action is authorized by passing any internal test. Direct-sales
   distribution requires current Select-rights and storefront checks first.

## Implemented in this change

- demand_analysis.py: latest per-title/month observations rather than maxima,
  as-of date cutoff and observation dates; LIVE-only revenue and language signals.
- kdp_evidence.py: read-only monthly account estimates, reconciliation gaps,
  same-day/time comparison, missing/stale evidence and sourced rejection register.
- quality_gate.py require_visuals: actual EPUB manifest/spine image references,
  excluding declared cover images. Assets merely present on disk/in ZIP cannot pass.

This is the internal reporting and structural quality foundation. It does not
rewrite the manuscripts, prove market demand, activate sales, or promise revenue.

## Commands

```bash
cd /root/libra
python3 demand_analysis.py
python3 demand_analysis.py --json
python3 -m pytest tests/test_demand_analysis.py tests/test_quality_gate_visual.py tests/test_quality_gate.py tests/test_prepare_kdp_pilot_cli.py tests/test_business_ledger.py tests/test_kdp_freeze.py tests/test_kdp_freeze_entrypoints.py -q
```
