# Libra KDP Pilot Automation Design

**Date:** 2026-08-21

## Goal

Automate preparation of one evidence-backed French senior-smartphone book from fixed topic through research, manuscript, visual assets, EPUB, paperback PDF, editorial review, and quality gates while making every KDP mutation structurally impossible under the active TOTAL KDP FREEZE.

## Definition Of Automation

The system runs unattended from an approved topic contract to `staged_quality_passed`. It may use network research and model APIs, but it may not open a KDP mutation page, append to the upload queue, set an upload-ready state, or call an uploader. OTP, CAPTCHA, KDP review, and publishing are outside this preparation pipeline.

Publishing remains a separate exceptional operation. Before any future publish attempt, Libra must show the account-closure risk again and obtain a new explicit confirmation for that exact slug. The active freeze cannot be disabled by a request parameter, environment default, listing field, or UI action.

## Product Contract

- Working title: `Le Smartphone Sans Stress`
- Language: French
- Audience: French-speaking seniors and family caregivers
- Format: reflowable eBook plus large-print paperback
- Scope: Android basics, WhatsApp, photos/video calls, online administration, privacy, scams, and emergency setup
- Exclusions: medical advice, financial advice, credential storage, device-specific promises without a tested version, invented testimonials, and copyrighted screenshots without documented permission
- This book must not advertise Payhip or link to another eBook store.

## Architecture

### Executable Freeze Policy

A new pure policy module is the single source of truth for KDP mutation authority. Its default is `total_freeze`. It distinguishes preparation actions from external mutations and returns stable refusal reasons.

Every mutation entry point must call the same policy before any file state implies upload readiness and before any browser or subprocess starts:

- the dashboard approval endpoint;
- the queue processor;
- `kdp_upload.py`;
- `kdp_finish_publish.py` and equivalent finish/update scripts;
- pricing, promotion, metadata, category, cover, and interior mutation adapters.

The policy may permit read-only Bookshelf, royalty, roster, and session-health observations. A blocked mutation creates an auditable incident without touching KDP.

### Isolated Pilot Orchestrator

A dedicated orchestrator consumes a checked-in fixed topic JSON. It does not reuse `auto-generate.sh` because that script can append `queue.txt`. It calls the reusable Python preparation modules directly and writes artifacts under `/root/kdp-staging/<slug>/` until every gate passes.

The orchestrator uses a stable run key and file lock. A replay either resumes the same stage or becomes a no-op. Conflicting inputs fail closed and create an incident.

### Preparation Stages

1. Validate the fixed topic and market evidence freshness.
2. Research current Android/WhatsApp behavior from authoritative sources.
3. Generate a cited manuscript and a claim-provenance ledger.
4. Acquire or create licensed instructional visuals with source, version, date, and alt text.
5. Build cover, EPUB, and large-print paperback PDF.
6. Run deterministic quality gates and full editorial review.
7. Run French-language review over the complete manuscript, not a sample.
8. Copy the immutable release candidate to `/root/kdp/<slug>/` only as `staged_quality_passed` with `publish_blocked: total_kdp_freeze`.

The orchestrator never sets `ready`, `approval_pending`, `kdp_uploading`, or `uploaded`, and never writes `queue.txt`.

## Quality Gates

- Market score is GO and evidence is no older than 30 days.
- The manuscript meets the existing non-fiction length, section, citation, and reference thresholds.
- Every operational claim has a source and observation date.
- Every screenshot or illustration has provenance and a tested software/device scope.
- A visual how-to book fails when instructional visuals are absent.
- EPUBCheck passes; PDF fonts are embedded; no clipped text or blank instructional pages exist.
- French review covers language, terminology, clarity for seniors, and misleading claims.
- Similarity checks against the existing Libra catalog remain below the approved threshold.
- KDP content disclosure metadata is complete and accurate.

## State Model

`topic_validated -> researched -> manuscript_ready -> visuals_ready -> formats_ready -> editorial_passed -> staged_quality_passed`

Any gate can move the run to `quality_failed`, `insufficient_evidence`, or `manual_required`. No preparation state is an upload state.

## Observability

Each run records stage start/end, input hashes, artifact hashes, model/API cost, gate results, and failure reason. Daily monitoring reports progress, but a report or process exit code is never evidence of a KDP state change.

## Verification

Tests must prove:

- every known uploader, queue, API, pricing, promotion, and metadata entry point refuses under freeze;
- a successful pilot run produces staged artifacts without queue writes, browser calls, or KDP mutations;
- replays are idempotent and conflicting topic inputs fail closed;
- a visual-niche manuscript without instructional visuals fails;
- staged status cannot be interpreted as ready/uploading/uploaded;
- only the fixed French smartphone topic is accepted by the pilot command;
- mutation-capable cron detection is accurate while read-only Libra cron remains allowed.

## Deployment Boundary

Deployment may activate preparation and monitoring only. No cron may invoke upload or publish. A future request to publish requires a separate design change, explicit per-slug confirmation after the risk warning, fresh tests, and live before/after evidence.

