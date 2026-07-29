"""Safe KDP Promotion Controller for the Libra Growth Autopilot.

Enforces the project's hardest-won free-promo rule (CLAUDE.md, memory.md
14 Jul 2026): a KDP Select free promotion measured 0 downloads in 13 of 17
cases when it ran without a paired external traffic channel, and KDP Select
only allows 5 free days per title per 90-day term — every unpaired day is
quota burned for nothing.

IMPORTANT — this module is a gate layered ON TOP of the existing production
gate, not a replacement for it. `scripts/kdp_action_executor.py::validate_action`
already refuses an ungated `free_promo` action via `has_distribution_pairing`
(the file-based check against `data/promo_pairings.json` /
`data/reddit_promo_schedule.json`), and that check still runs in production
— see the Task-9 wiring contract below. This module adds a SECOND,
independent layer in front of it: a caller-supplied `evidence` item proving
the specific channel for THIS proposal (checked against the slug), a fresh
before-state read, and a no-overlapping-experiment check — none of which
`validate_action` covers.

Task-9 wiring contract (how the three functions compose in production):
  1. `propose_promotion(slug, state, evidence)` — pure gate, no I/O. Refuses
     unless a fresh before-state, verified slug-matched evidence, and no
     overlapping experiment all hold.
  2. If allowed, the caller builds an `action` from the proposal and calls
     `reconcile_promotion(action, KdpPromotionAdapter())`.
  3. `KdpPromotionAdapter.publish` (scripts/kdp_action_executor.py) is the
     THIRD gate: it calls `validate_action` (the SAME production function
     and pairing check the legacy `free_promo` action kind uses) before
     touching the browser at all, and only proceeds to `_execute_free_promo`
     if that also passes. A `validate_action` refusal is reported back as a
     policy rejection, which `reconcile_promotion` classifies `"blocked"`.

`propose_promotion(slug, state, evidence) -> dict` refuses unless ALL hold:
  - `state` is a fresh before-state (an `observed_at` timestamp read
    recently, not in the future — see AGENTS.md "before-state" rule: never
    trust listing.json over the real page).
  - no overlapping experiment is already running on the slug (`CLAUDE.md`
    "one variable per window" rule — a promo is itself an experiment here).
  - `evidence` contains at least one item with `kind == "external_post"`
    carrying a real, externally-readable `post_url` or `post_id` — the same
    proof contract Task 6's `distribution_executor` uses, reused here via
    `_external_proof` rather than re-implemented — AND, if the item carries
    a `slug` field, it matches the promotion's own slug (evidence proving a
    channel for book A must never authorize a promo for book B).
When allowed, the proposal is always exactly ONE calendar day — this
controller never proposes the old 1-5 day range; a fresh evidence item and
a fresh evaluation is required before another day is ever proposed again.

`evaluate_promotion(result) -> dict` closes the loop after a promotion
ends (evaluated over the project's 7-day evaluation window): a verified
zero-download result exhausts the cycle — matches the measured pattern
(unpaired promos = 0 downloads) — rather than being retried blindly.

`reconcile_promotion(action, adapter) -> dict` follows the exact same
fail-closed philosophy as Task 6's `execute_distribution`: the adapter's
response is never trusted as success by itself.
  - `executed`        — `returncode == 0` AND a `verified_state_change`
                         with both a `before` and an `after` (the real
                         browser audit shape already used by
                         `scripts/kdp_action_executor.py`'s free_promo lane).
  - `manual_required`  — an auth barrier (`otp_required`/`captcha_required`/
                         `login_required`/`session_expired`), the adapter
                         raised, or anything ambiguous (no barrier, no
                         policy rejection, but no verified state change
                         either).
  - `blocked`          — the adapter reports a policy rejection (including
                         `KdpPromotionAdapter`'s own `validate_action`
                         refusal — see the wiring contract above).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from distribution_executor import _external_proof

# How stale a before-state read is allowed to be before it can no longer be
# trusted to decide whether to propose a promotion (AGENTS.md: read the real
# page before acting, never trust a cached/old read).
STATE_FRESHNESS_MAX_AGE = timedelta(minutes=30)

_AUTH_BARRIER_SIGNALS = ("otp_required", "captcha_required", "login_required", "session_expired")


def _is_fresh(state: dict) -> bool:
    observed_at = state.get("observed_at")
    if not isinstance(observed_at, str):
        return False
    try:
        observed = datetime.fromisoformat(observed_at)
    except ValueError:
        return False
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - observed
    # A future-dated observed_at is not "fresh" — it's bad data (clock skew,
    # a fabricated timestamp) and must fail closed exactly like a stale one.
    return timedelta(0) <= delta <= STATE_FRESHNESS_MAX_AGE


def _has_verified_placement(slug: str, evidence: list) -> bool:
    for item in evidence or []:
        if not isinstance(item, dict) or item.get("kind") != "external_post":
            continue
        item_slug = item.get("slug")
        if item_slug is not None and item_slug != slug:
            continue  # evidence for a different book must not authorize this promo
        if _external_proof(item):
            return True
    return False


def _blocked(slug: str, reason: str) -> dict:
    return {"status": "blocked", "slug": slug, "reason": reason}


def propose_promotion(slug: str, state: dict, evidence: list) -> dict:
    if not isinstance(state, dict) or not _is_fresh(state):
        return _blocked(slug, "missing_fresh_before_state")
    if state.get("active_experiment"):
        return _blocked(slug, "overlapping_experiment_on_slug")
    if not _has_verified_placement(slug, evidence):
        return _blocked(slug, "missing_verified_distribution_evidence")
    return {
        "status": "allowed",
        "slug": slug,
        "kind": "kdp_promotion",
        "days": 1,
        "reason": "paired_one_day_promotion_ready",
    }


def evaluate_promotion(result: dict) -> dict:
    if not isinstance(result, dict) or not result.get("verified"):
        return {"allow_more_days": False, "reason": "unverified_result"}
    downloads = result.get("downloads")
    if not isinstance(downloads, (int, float)) or isinstance(downloads, bool) or downloads <= 0:
        return {"allow_more_days": False, "reason": "zero_downloads_exhausts_cycle"}
    return {"allow_more_days": True, "reason": "downloads_recorded"}


def reconcile_promotion(action: dict, adapter) -> dict:
    if not isinstance(action, dict):
        return {"status": "manual_required", "reason": "invalid_action"}

    try:
        response = adapter.publish(action)
    except Exception:
        return {"status": "manual_required", "reason": "adapter_error"}

    if not isinstance(response, dict):
        return {"status": "manual_required", "reason": "invalid_adapter_response"}

    if any(response.get(signal) for signal in _AUTH_BARRIER_SIGNALS):
        return {"status": "manual_required", "reason": "auth_barrier"}

    if response.get("policy_rejected") or response.get("blocked"):
        return {"status": "blocked", "reason": "policy_rejected"}

    change = response.get("verified_state_change")
    if (response.get("returncode") == 0 and isinstance(change, dict)
            and change.get("before") is not None and change.get("after")):
        return {
            "status": "executed",
            "reason": "verified_after_state",
            "confirmation_id": response.get("confirmation_id"),
            "external_url": response.get("external_url"),
            "verified_state_change": change,
        }

    return {"status": "manual_required", "reason": response.get("error") or "unverified_after_state"}
