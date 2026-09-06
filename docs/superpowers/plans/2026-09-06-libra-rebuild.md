# Libra Internal Rebuild Implementation Plan

Goal: replace misleading sales decisions with traceable evidence and a local
rejection review register. Keep all KDP publishing guards intact.
Architecture: extend existing demand_analysis CLI; one read-only evidence module
reads existing ledger and local listings. No new database, service or cron.
Tech: Python standard library, SQLite, existing pytest fixtures.

1. Add regression tests in tests/test_demand_analysis.py for corrected monthly
   observations, mixed LIVE/BLOCKED cohorts, UNKNOWN titles, missing observations,
   equal-period comparison, explicit attribution gaps and local rejection evidence.
2. Fix demand_analysis.py to use last observed title values, preserve observation
   dates, and restrict opportunity revenue and supporting languages to LIVE titles.
3. Add kdp_evidence.py: readonly snapshot loading, latest monthly overview and
   reconciliation, same-calendar-day prior-month comparison, freshness and unknown
   paid-order/profit fields. Rejection register preserves local notes as notes,
   with unknown cause and source path. Expose through demand_report and CLI.
4. Run focused demand, ledger and freeze tests. Run the CLI against local real
   data; verify inputs unchanged and no network required. Review output before
   committing. Document limits and correct misleading causal claims in rules.
5. Save human-readable findings under downloads, update memory, commit and push
   only task files. No production restart or publishing authorization implied.

Expanded after concrete file audit: quality_gate.py now checks actual EPUB spine
references and validates embedded raster bytes under require_visuals, excluding
cover/unreferenced/repeated assets. tests/test_quality_gate_visual.py uses real
synthetic EPUB ZIP fixtures. Semantic promise-versus-chapter coverage remains an
explicit editorial task, documented with two local title examples.

Acceptance: royalties corrections retained; zero cannot replace unknown paid
sales/profit; blocked revenue cannot lift live cohort ranking; comparison absent
when matching date missing; each blocked record has source and missing evidence;
freeze entrypoint checks pass. User's monetary target remains unspecified.
