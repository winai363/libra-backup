# Libra Risk-Aware Recovery Agent Design

## Goal

Prevent false-green category health after a KDP category-removal notice and make a verified revenue stall visible to the daily profit agent without mutating any live KDP listing.

## Evidence And Constraints

- KDP removed three unrelated categories on 2026-07-22 while keeping the books for sale.
- The affected ASIN/category pairs are recorded as immutable local incidents.
- Live metadata changes are forbidden because they trigger content review.
- Paid spend remains disabled during the organic 90-day mode.
- The agent may diagnose, prioritize, and notify. It may not infer successful external actions without evidence.

## Design

`data/kdp_metadata_incidents.json` is the source of truth for KDP notices. `category_health_manager.py` joins incidents to local listings, raises `metadata_risk`, and treats a removed category that remains in local metadata as a blocker. Historical removed categories are never suggested again.

`profit_pace.py` derives a three-day revenue-stall signal from cumulative KDP snapshots. `scripts/libra_profit_agent_daily.py` includes the signal plus the category health state in its output and Telegram digest. Metadata risk closes the metadata safety gate but does not block observation, free distribution, or evaluation.

## Success Criteria

- The three notified ASIN/category pairs appear in the category health report.
- Category health cannot report `ok` while an unresolved KDP notice exists.
- The profit agent reports a stall after three consecutive daily observations with less than $0.25 total royalty growth.
- No code path submits or edits KDP metadata.
- Focused and regression tests pass.
