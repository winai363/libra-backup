"""Fail-closed gate and adapter boundary for the approved Libra Ads pilot."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta


APPROVED_CAPS = {
    "total_loss_cap_usd": 10.0,
    "daily_budget_usd": 2.0,
    "duration_days": 14,
    "max_asins": 1,
}

EXACT_WRITE_OPERATIONS = (
    "create_sponsored_products",
    "pause_campaign",
)

EXACT_READ_OPERATIONS = ("read_campaign",)

PROFILE_FIELDS = (
    "account_id",
    "entity_id",
    "marketplace",
    "country",
    "currency",
    "timezone",
    "authority",
    "observed_at",
)

MUTATION_FIELDS = (
    "asin",
    "campaign_name",
    "daily_budget_usd",
    "total_loss_cap_usd",
    "duration_days",
    "bid_usd",
    "bidding_strategy",
    "targeting",
    "owner_approved_exact_plan",
    "idempotency_key",
    "audit_destination",
    "rollback",
)


def _candidate(retail: dict, slug: str) -> dict | None:
    rows = retail.get("candidates") if isinstance(retail, dict) else None
    if not isinstance(rows, list):
        return None
    return next((row for row in rows if isinstance(row, dict) and row.get("slug") == slug), None)


def _valid_targeting(value) -> bool:
    if not isinstance(value, list) or not value:
        return False
    for row in value:
        if not isinstance(row, dict) or not str(row.get("keyword") or "").strip():
            return False
        if row.get("match_type") not in {"EXACT", "PHRASE"}:
            return False
    return True


def _positive_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0


def build_pilot_gate(inputs: dict, retail: dict, as_of: datetime) -> dict:
    inputs = inputs if isinstance(inputs, dict) else {}
    missing = []
    slug = str(inputs.get("candidate_slug") or "adhd-adults-workbook-es")
    candidate = _candidate(retail, slug)
    if not candidate or candidate.get("verdict") != "PASS":
        missing.append(f"retail_readiness:{slug}")

    profile = inputs.get("account_profile")
    if not isinstance(profile, dict):
        missing.append("account_profile")
        profile = {}
    else:
        for field in PROFILE_FIELDS:
            if profile.get(field) in {None, ""}:
                missing.append(f"account_profile:{field}")

    snapshot = inputs.get("account_snapshot")
    if not isinstance(snapshot, dict):
        missing.append("account_snapshot")
        snapshot = {}
    else:
        for field in ("fingerprint", "observed_at", "campaigns", "spend_usd"):
            if snapshot.get(field) is None:
                missing.append(f"account_snapshot:{field}")
        if snapshot.get("observed_at"):
            try:
                snapshot_observed_at = datetime.fromisoformat(str(snapshot["observed_at"]))
                if (
                    snapshot_observed_at.tzinfo is None
                    or as_of.tzinfo is None
                    or as_of - snapshot_observed_at > timedelta(hours=24)
                    or snapshot_observed_at - as_of > timedelta(minutes=5)
                ):
                    missing.append("account_snapshot:stale")
            except ValueError:
                missing.append("account_snapshot:observed_at_invalid")

    capabilities = inputs.get("capability_manifest")
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    for operation in EXACT_WRITE_OPERATIONS:
        if capabilities.get(operation) != "live-write-verified":
            missing.append(f"capability:{operation}")
    for operation in EXACT_READ_OPERATIONS:
        if capabilities.get(operation) != "live-read-verified":
            missing.append(f"capability:{operation}")
    if capabilities.get("spend_watcher") != "live-read-verified":
        missing.append("capability:spend_watcher")

    mutation = inputs.get("mutation")
    mutation = mutation if isinstance(mutation, dict) else {}
    for field in MUTATION_FIELDS:
        value = mutation.get(field)
        valid = value not in {None, ""} if not isinstance(value, (list, dict)) else bool(value)
        if field == "owner_approved_exact_plan":
            valid = value is True
        elif field == "targeting":
            valid = _valid_targeting(value)
        elif field in {"daily_budget_usd", "total_loss_cap_usd", "duration_days", "bid_usd"}:
            valid = _positive_number(value)
        if not valid:
            missing.append(f"mutation:{field}")

    if _positive_number(mutation.get("daily_budget_usd")) and mutation["daily_budget_usd"] > APPROVED_CAPS["daily_budget_usd"]:
        missing.append("mutation:daily_budget_usd_exceeds_cap")
    if _positive_number(mutation.get("total_loss_cap_usd")) and mutation["total_loss_cap_usd"] > APPROVED_CAPS["total_loss_cap_usd"]:
        missing.append("mutation:total_loss_cap_usd_exceeds_cap")
    if _positive_number(mutation.get("duration_days")) and mutation["duration_days"] > APPROVED_CAPS["duration_days"]:
        missing.append("mutation:duration_days_exceeds_cap")
    if mutation.get("asin") and candidate and mutation.get("asin") != candidate.get("asin"):
        missing.append("mutation:asin_does_not_match_retail_pass")
    if mutation.get("bidding_strategy") not in {None, "dynamic_bids_down_only"}:
        missing.append("mutation:bidding_strategy_not_approved")
    if mutation.get("rollback") not in {None, "pause_campaign"}:
        missing.append("mutation:rollback_not_reversible")

    plan = {
        "schema": "libra-amazon-ads-mutation-plan/1",
        "generated_at": as_of.isoformat(),
        "objective": "attributed paid book order and contribution after ad spend",
        "conversion": "Amazon Ads attributed order, reconciled side-by-side with KDP royalties and KENP",
        "account_id": profile.get("account_id"),
        "entity_id": profile.get("entity_id"),
        "marketplace": profile.get("marketplace"),
        "country": profile.get("country"),
        "currency": profile.get("currency"),
        "timezone": profile.get("timezone"),
        "remote_snapshot_fingerprint": snapshot.get("fingerprint"),
        "asin": mutation.get("asin"),
        "campaign_name": mutation.get("campaign_name"),
        "daily_budget_usd": mutation.get("daily_budget_usd"),
        "total_loss_cap_usd": mutation.get("total_loss_cap_usd"),
        "duration_days": mutation.get("duration_days"),
        "bid_usd": mutation.get("bid_usd"),
        "bidding_strategy": mutation.get("bidding_strategy"),
        "targeting": deepcopy(mutation.get("targeting")),
        "negative_targeting": [],
        "idempotency_key": mutation.get("idempotency_key"),
        "audit_destination": mutation.get("audit_destination"),
        "rollback": mutation.get("rollback"),
        "blast_radius": "one United States Sponsored Products campaign and one ASIN",
        "learning_impact": "new campaign with no prior Amazon Ads history",
        "verification_window": "read back immediately; spend freshness must remain under 24 hours",
        "stop_rules": [
            "verified total spend reaches USD 10",
            "retail eligibility, listing availability, policy or measurement anomaly",
            "remote state cannot be read back or spend freshness exceeds 24 hours",
            "spend occurs while conversion evidence cannot be reconciled",
            "owner stop",
        ],
    }
    return {
        "schema": "libra-amazon-ads-pilot-gate/1",
        "as_of": as_of.isoformat(),
        "status": "APPLY_READY" if not missing else "BLOCKED_WITH_EVIDENCE",
        "candidate": {
            "slug": slug,
            "asin": candidate.get("asin") if candidate else None,
            "retail_verdict": candidate.get("verdict") if candidate else None,
        },
        "missing_inputs": missing,
        "capability_manifest": deepcopy(capabilities),
        "account_snapshot": deepcopy(snapshot),
        "plan": plan,
    }


def apply_pilot(gate: dict, adapter) -> dict:
    if gate.get("status") != "APPLY_READY":
        return {"status": "BLOCKED", "reason": "pilot_gate_not_apply_ready"}
    try:
        capabilities = adapter.capability_manifest()
    except Exception:
        return {"status": "BLOCKED", "reason": "capability_manifest_unavailable"}
    if any(capabilities.get(operation) != "live-write-verified" for operation in EXACT_WRITE_OPERATIONS):
        return {"status": "BLOCKED", "reason": "exact_operation_not_live_write_verified"}
    if any(capabilities.get(operation) != "live-read-verified" for operation in EXACT_READ_OPERATIONS):
        return {"status": "BLOCKED", "reason": "exact_readback_not_live_read_verified"}
    plan = gate["plan"]
    try:
        before = adapter.snapshot()
    except Exception:
        return {"status": "BLOCKED", "reason": "remote_snapshot_unavailable"}
    if before.get("fingerprint") != plan.get("remote_snapshot_fingerprint"):
        return {"status": "BLOCKED", "reason": "remote_state_changed"}
    try:
        created = adapter.create_sponsored_products(plan)
    except Exception:
        return {"status": "DELIVERY_UNCERTAIN", "reason": "create_operation_error"}
    campaign_id = created.get("campaign_id") if isinstance(created, dict) else None
    if not campaign_id:
        return {"status": "DELIVERY_UNCERTAIN", "reason": "campaign_id_missing"}
    try:
        after = adapter.read_campaign(campaign_id)
    except Exception:
        return {"status": "DELIVERY_UNCERTAIN", "reason": "readback_error", "campaign_id": campaign_id}
    if not isinstance(after, dict) or (
        after.get("campaign_id") != campaign_id
        or after.get("status") != "ENABLED"
        or after.get("asin") != plan.get("asin")
        or after.get("daily_budget_usd") != plan.get("daily_budget_usd")
    ):
        return {"status": "DELIVERY_UNCERTAIN", "reason": "readback_mismatch", "campaign_id": campaign_id}
    return {
        "status": "APPLIED_VERIFIED",
        "before": before,
        "after": after,
        "idempotency_key": plan.get("idempotency_key"),
        "audit_destination": plan.get("audit_destination"),
        "rollback": plan.get("rollback"),
    }
