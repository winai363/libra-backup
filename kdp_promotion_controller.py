"""Safe KDP Promotion Controller for the Libra Growth Autopilot.

Enforces the project's hardest-won free-promo rule (CLAUDE.md, memory.md
14 Jul 2026): a KDP Select free promotion measured 0 downloads in 13 of 17
cases when it ran without a paired external traffic channel, and KDP Select
only allows 5 free days per title per 90-day term — every unpaired day is
quota burned for nothing. This module is the single gate that decides
whether a promotion may be proposed at all, and the single point that turns
an approved proposal into a real KDP mutation without ever inventing
success.

`propose_promotion(slug, state, evidence) -> dict` refuses unless ALL hold:
  - `state` is a fresh before-state (an `observed_at` timestamp read
    recently — see AGENTS.md "before-state" rule: never trust listing.json
    over the real page).
  - no overlapping experiment is already running on the slug (`CLAUDE.md`
    "one variable per window" rule — a promo is itself an experiment here).
  - `evidence` contains at least one item with `kind == "external_post"`
    carrying a real, externally-readable `post_url` or `post_id` — the same
    proof contract Task 6's `distribution_executor` uses, reused here via
    `_external_proof` rather than re-implemented.
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
  - `blocked`          — the adapter reports a policy rejection.
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
    return datetime.now(timezone.utc) - observed <= STATE_FRESHNESS_MAX_AGE


def _has_verified_placement(evidence: list) -> bool:
    for item in evidence or []:
        if isinstance(item, dict) and item.get("kind") == "external_post" and _external_proof(item):
            return True
    return False


def _blocked(slug: str, reason: str) -> dict:
    return {"status": "blocked", "slug": slug, "reason": reason}


def propose_promotion(slug: str, state: dict, evidence: list) -> dict:
    if not isinstance(state, dict) or not _is_fresh(state):
        return _blocked(slug, "missing_fresh_before_state")
    if state.get("active_experiment"):
        return _blocked(slug, "overlapping_experiment_on_slug")
    if not _has_verified_placement(evidence):
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
