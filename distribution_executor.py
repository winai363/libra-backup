"""Verified-distribution gate for the Libra Growth Autopilot.

The project's hardest-won rule (CLAUDE.md, "Autonomous Management"): an
external action counts as `executed` ONLY with verifiable after-state
evidence. Reminders, browser clicks, and exit codes are NEVER success. This
module is the single enforcement point for that rule on the distribution
path (posting content to an external channel — Reddit, a forum, etc).

Adapter contract: `adapter.publish(action) -> dict`. The returned dict is
the adapter's reported after-state — never trusted as a success signal by
itself, and never trusted to be well-behaved: `adapter.publish` may raise,
return garbage, or report stale data, and every one of those must still
fail closed rather than propagate or invent success. Adapters MUST flag
staleness explicitly — set a truthy top-level `"stale"` key, or a truthy
`"stale"` key inside `after_state`, whenever the reported after-state was
not read fresh at execution time (e.g. a cached page, a queued/pending read).
`execute_distribution` classifies the outcome into exactly one of:

- `executed`   — the response contains a real `post_url` or `post_id` AND a
                 non-empty `after_state` readback (the adapter re-read the
                 destination and confirmed the post is actually there), AND
                 neither is flagged `stale`, AND the action carries an
                 identity (`action_key` or `slug`) to record against. A bare
                 id/url with no readback is NOT enough — that is exactly the
                 "clicked=True, post_url=None" false-success trap this rule
                 exists to close, generalized to "id present but nothing
                 confirms it resolves."
- `manual_required` — the response signals an authentication barrier (OTP,
                 CAPTCHA, login, expired session); the adapter itself raised
                 (`reason="adapter_error"`); the evidence is flagged stale
                 (`reason="stale_data"`); the action has no identity to
                 record against (`reason="missing_action_identity"`); or
                 anything else ambiguous — no barrier, no policy rejection,
                 but also no verifiable after-state. This is the fail-closed
                 default.
- `blocked`    — the adapter reports the platform rejected the action on
                 policy grounds (spam/rules violation). Retrying without
                 changing the content is pointless.

Every result also carries a `reason` string documenting exactly which gate
produced it, for logging/debugging — never used to decide anything by the
caller, just a stable diagnostic.

Only `executed` results are recorded, via `business_ledger.record_growth_evidence`,
because only `executed` produces actual evidence of a growth action having
happened — a `manual_required`/`blocked` outcome is not evidence of
anything. Recording is opt-in: pass `ledger_path` to record; omit it (the
default) to run without touching the ledger, e.g. in tests.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from business_ledger import growth_evidence, record_growth_evidence

# How long a distribution-evidence row stays "fresh" for downstream growth
# signals (growth_policy.ads_eligibility, etc) before it needs refreshing.
EVIDENCE_FRESH_DAYS = 30

_AUTH_BARRIER_SIGNALS = ("otp_required", "captcha_required", "login_required", "session_expired")

# Values a flaky/lazy adapter might put in post_id/post_url that look like an
# identifier but are not — same placeholder list as the existing convention
# in scripts/kdp_action_executor.py's valid_distribution_proof.
_PROOF_PLACEHOLDERS = {"planned", "pending", "reminded", "scheduled", "todo", "true", "false", "none", "null"}


def _result(status: str, evidence: dict, reason: str) -> dict:
    return {"status": status, "evidence": evidence, "reason": reason}


def _external_proof(response: dict) -> dict:
    """Return {"post_url": ...} or {"post_id": ...} if the response contains
    a real, externally-readable identifier — otherwise {}. A numeric
    post_id (some adapters return the platform's native integer id) is
    coerced to str before validation; bool is excluded since Python's bool
    is a subclass of int and True/False is never a real id."""
    url = response.get("post_url")
    if isinstance(url, str):
        value = url.strip()
        parsed = urlparse(value)
        if parsed.scheme in {"http", "https"} and parsed.hostname and parsed.path not in {"", "/"}:
            return {"post_url": value}
    post_id = response.get("post_id")
    if isinstance(post_id, int) and not isinstance(post_id, bool):
        post_id = str(post_id)
    if isinstance(post_id, str):
        value = post_id.strip()
        if value and value.lower() not in _PROOF_PLACEHOLDERS and re.fullmatch(r"[A-Za-z0-9_-]{3,}", value):
            return {"post_id": value}
    return {}


def _raw_after_state(response: dict) -> dict:
    state = response.get("after_state")
    return state if isinstance(state, dict) else {}


def _has_auth_barrier(response: dict) -> bool:
    return any(response.get(signal) for signal in _AUTH_BARRIER_SIGNALS)


def _is_policy_rejected(response: dict) -> bool:
    return bool(response.get("policy_rejected") or response.get("blocked"))


def _is_stale(response: dict, after_state: dict) -> bool:
    """True if the adapter flagged its own report as stale — a truthy
    top-level "stale" key, or a truthy "stale" key inside after_state.
    Fires even when post_url/post_id and a non-empty after_state are
    otherwise present: a stale readback is not verification, no matter how
    complete it looks."""
    return bool(response.get("stale")) or bool(after_state.get("stale"))


def _has_action_identity(action: dict) -> bool:
    return bool(action.get("action_key") or action.get("slug"))


def _record_executed(ledger_path: Path, action: dict, evidence: dict) -> None:
    """Record executed evidence, keyed by the action's own action_key (or
    slug, if no action_key) so a replay of the SAME action (e.g. a retried
    cron run) is a no-op rather than a ledger conflict — matches the
    action_key-replay convention already used by growth_planner. Caller
    guarantees the action has an identity (see _has_action_identity) before
    calling this.

    record_growth_evidence's content hash bakes in `observed_at`, which is
    wall-clock and differs on every call — so a byte-identical replay of the
    same action still looks like a content change to it and raises. The
    try/except below works around that: on a ValueError, re-check whether
    the ALREADY-STORED row for this source_key has the same evidence
    payload we just tried to write. If so, this is just a harmless replay
    at a different wall-clock time — swallow it. If the stored evidence
    differs, this is a genuine conflict (e.g. a second, different post
    somehow reusing the same action_key) and must still raise, same as
    every other business_ledger record_* function."""
    source_key = f"distribution:{action.get('action_key') or action.get('slug')}"
    now = datetime.now(timezone.utc)
    try:
        record_growth_evidence(ledger_path, {
            "source_key": source_key,
            "kind": "distribution_published",
            "slug": action.get("slug"),
            "observed_at": now.isoformat(),
            "fresh_until": (now + timedelta(days=EVIDENCE_FRESH_DAYS)).isoformat(),
            "confidence": 1.0,
            "payload": evidence,
        })
    except ValueError:
        existing = next(
            (row for row in growth_evidence(ledger_path, slug=action.get("slug"))
             if row["source_key"] == source_key),
            None,
        )
        if existing is None or existing["payload"] != evidence:
            raise


def execute_distribution(action: dict, adapter, *, ledger_path: Path | None = None) -> dict:
    """Execute one distribution action through `adapter` and classify the
    result. Never raises on an ambiguous/ill-formed adapter response, and
    never propagates an exception the adapter itself raises — every failure
    mode fails closed to `manual_required` instead."""
    if not isinstance(action, dict):
        return _result("manual_required", {}, "invalid_action")

    try:
        response = adapter.publish(action)
    except Exception:
        return _result("manual_required", {}, "adapter_error")

    if not isinstance(response, dict):
        return _result("manual_required", {}, "invalid_adapter_response")

    if _has_auth_barrier(response):
        return _result("manual_required", {}, "auth_barrier")

    if _is_policy_rejected(response):
        return _result("blocked", {}, "policy_rejected")

    after_state = _raw_after_state(response)
    if _is_stale(response, after_state):
        return _result("manual_required", {}, "stale_data")

    proof = _external_proof(response)
    if proof and after_state:
        if not _has_action_identity(action):
            return _result("manual_required", {}, "missing_action_identity")
        evidence = {**proof, "after_state": after_state}
        if ledger_path is not None:
            _record_executed(ledger_path, action, evidence)
        return _result("executed", evidence, "verified_after_state")

    # No barrier, no policy rejection, no staleness flag, but also no
    # verifiable after-state (e.g. only a client-side "clicked" flag, or a
    # URL with nothing confirming it resolves) — fail closed rather than
    # invent success.
    return _result("manual_required", {}, "unverified_after_state")
