from datetime import datetime, timezone

from amazon_ads_pilot import APPROVED_CAPS, apply_pilot, build_pilot_gate


AS_OF = datetime(2026, 11, 19, 4, 0, tzinfo=timezone.utc)


def retail(verdict="PASS"):
    return {
        "candidates": [{
            "slug": "adhd-adults-workbook-es",
            "asin": "B0H6VB1SDX",
            "verdict": verdict,
            "blockers": [] if verdict == "PASS" else ["promise_match"],
        }]
    }


def complete_inputs():
    return {
        "account_profile": {
            "account_id": "ads-account-1",
            "entity_id": "entity-1",
            "marketplace": "Amazon.com",
            "country": "United States",
            "currency": "USD",
            "timezone": "America/Los_Angeles",
            "authority": "Admin",
            "observed_at": "2026-11-19T10:30:00+07:00",
        },
        "account_snapshot": {
            "fingerprint": "snapshot-1",
            "observed_at": "2026-11-19T10:30:00+07:00",
            "campaigns": 0,
            "spend_usd": 0.0,
        },
        "capability_manifest": {
            "create_sponsored_products": "live-write-verified",
            "pause_campaign": "live-write-verified",
            "read_campaign": "live-read-verified",
            "spend_watcher": "live-read-verified",
        },
        "mutation": {
            "asin": "B0H6VB1SDX",
            "campaign_name": "LIBRA-TDAH-ES-US-20261119-PILOT",
            "daily_budget_usd": 2.0,
            "total_loss_cap_usd": 10.0,
            "duration_days": 14,
            "bid_usd": 0.25,
            "bidding_strategy": "dynamic_bids_down_only",
            "targeting": [
                {"keyword": "cuaderno tdah adultos", "match_type": "EXACT"},
            ],
            "owner_approved_exact_plan": True,
            "idempotency_key": "libra-tdah-us-20261119-v1",
            "audit_destination": "data/amazon_ads_pilot_audit.jsonl",
            "rollback": "pause_campaign",
        },
    }


def test_missing_account_and_capability_blocks_with_evidence():
    result = build_pilot_gate({}, retail(), AS_OF)

    assert result["status"] == "BLOCKED_WITH_EVIDENCE"
    assert "account_profile" in result["missing_inputs"]
    assert "account_snapshot" in result["missing_inputs"]
    assert "capability:create_sponsored_products" in result["missing_inputs"]


def test_non_pass_retail_candidate_blocks_apply():
    result = build_pilot_gate(complete_inputs(), retail("HOLD"), AS_OF)

    assert result["status"] == "BLOCKED_WITH_EVIDENCE"
    assert result["missing_inputs"] == ["retail_readiness:adhd-adults-workbook-es"]


def test_ready_plan_never_exceeds_approved_caps():
    result = build_pilot_gate(complete_inputs(), retail(), AS_OF)

    assert result["status"] == "APPLY_READY"
    assert result["plan"]["total_loss_cap_usd"] == APPROVED_CAPS["total_loss_cap_usd"]
    assert result["plan"]["daily_budget_usd"] <= APPROVED_CAPS["daily_budget_usd"]
    assert result["plan"]["duration_days"] <= APPROVED_CAPS["duration_days"]
    assert result["plan"]["asin"] == "B0H6VB1SDX"
    assert result["plan"]["rollback"] == "pause_campaign"


def test_unverified_exact_operation_blocks_even_with_complete_plan():
    inputs = complete_inputs()
    inputs["capability_manifest"]["create_sponsored_products"] = "discovered"

    result = build_pilot_gate(inputs, retail(), AS_OF)

    assert result["status"] == "BLOCKED_WITH_EVIDENCE"
    assert "capability:create_sponsored_products" in result["missing_inputs"]


def test_stale_remote_snapshot_blocks_apply_ready_plan():
    inputs = complete_inputs()
    inputs["account_snapshot"]["observed_at"] = "2026-11-17T10:30:00+07:00"

    result = build_pilot_gate(inputs, retail(), AS_OF)

    assert result["status"] == "BLOCKED_WITH_EVIDENCE"
    assert "account_snapshot:stale" in result["missing_inputs"]


def test_missing_bid_spend_watcher_and_exact_approval_are_named():
    inputs = complete_inputs()
    inputs["mutation"]["bid_usd"] = None
    inputs["mutation"]["owner_approved_exact_plan"] = False
    inputs["capability_manifest"]["spend_watcher"] = "disabled"

    result = build_pilot_gate(inputs, retail(), AS_OF)

    assert result["status"] == "BLOCKED_WITH_EVIDENCE"
    assert "mutation:bid_usd" in result["missing_inputs"]
    assert "mutation:owner_approved_exact_plan" in result["missing_inputs"]
    assert "capability:spend_watcher" in result["missing_inputs"]


class UnverifiedAdapter:
    def capability_manifest(self):
        return {
            "create_sponsored_products": "discovered",
            "pause_campaign": "disabled",
            "read_campaign": "live-read-verified",
        }


def test_apply_refuses_unverified_exact_operation():
    gate = build_pilot_gate(complete_inputs(), retail(), AS_OF)

    result = apply_pilot(gate, UnverifiedAdapter())

    assert result == {
        "status": "BLOCKED",
        "reason": "exact_operation_not_live_write_verified",
    }


class VerifiedAdapter:
    def __init__(self):
        self.created = []

    def capability_manifest(self):
        return {
            "create_sponsored_products": "live-write-verified",
            "pause_campaign": "live-write-verified",
            "read_campaign": "live-read-verified",
        }

    def snapshot(self):
        return {"fingerprint": "snapshot-1"}

    def create_sponsored_products(self, plan):
        self.created.append(plan)
        return {"campaign_id": "campaign-1", "status": "ENABLED"}

    def read_campaign(self, campaign_id):
        return {
            "campaign_id": campaign_id,
            "status": "ENABLED",
            "daily_budget_usd": 2.0,
            "asin": "B0H6VB1SDX",
        }


def test_apply_requires_fresh_snapshot_and_verified_readback():
    gate = build_pilot_gate(complete_inputs(), retail(), AS_OF)
    adapter = VerifiedAdapter()

    result = apply_pilot(gate, adapter)

    assert result["status"] == "APPLIED_VERIFIED"
    assert result["after"]["campaign_id"] == "campaign-1"
    assert result["rollback"] == "pause_campaign"
    assert len(adapter.created) == 1
