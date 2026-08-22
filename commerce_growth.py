"""Zero-budget growth decisions for the direct-sales lane.

A pure function over measured metrics. It proposes; it never publishes, and it
never spends — `paid_spend_minor` is hard-coded to 0 on every path.

Order matters: an open money incident (a mismatch, a dispute, a conflicting
replay) outranks every growth rule. Scaling distribution while the books do not
add up is how a small problem becomes an expensive one.
"""

from __future__ import annotations

from pathlib import Path

LIBRA_DIR = Path(__file__).resolve().parent

COMMERCE_MODULE_PATHS = (
    LIBRA_DIR / "commerce_growth.py",
    LIBRA_DIR / "commerce_ledger.py",
    LIBRA_DIR / "commerce_reconciliation.py",
    LIBRA_DIR / "commerce_reporting.py",
    LIBRA_DIR / "stripe_webhook.py",
    LIBRA_DIR / "payhip_webhook.py",
    LIBRA_DIR / "settings.py",
)

MIN_VISITS_TO_JUDGE = 100
PLACEMENTS_BEFORE_FREEZE = 3
MAX_ORGANIC_ASSETS_PER_WEEK = 2


def _proposal(kind: str, detail: str) -> dict:
    return {
        "kind": kind,
        "detail": detail,
        "state": "proposed",
        "paid_spend_minor": 0,
        # Nothing counts as published until an adapter returns a real external
        # post id or URL that can be checked afterwards.
        "requires_external_evidence": True,
    }


def commerce_growth_decision(metrics: dict) -> dict:
    visits = int(metrics.get("verified_visits") or 0)
    clicks = int(metrics.get("product_clicks") or 0)
    sales = int(metrics.get("verified_sales") or 0)
    placements = int(metrics.get("verified_placements") or 0)
    published_recently = int(metrics.get("assets_published_last_7d") or 0)
    incidents = list(metrics.get("open_incidents") or [])

    decision = {
        "paid_spend_minor": 0,
        "actions": [],
        "throttle": None,
        "measured": {
            "verified_visits": visits,
            "product_clicks": clicks,
            "verified_sales": sales,
        },
        # Payhip-observed sales are never promoted to verified, and no campaign
        # can be credited until a click id is proven to survive checkout.
        "attribution": {"status": "unknown", "verified_sales": sales},
    }

    if incidents:
        decision["status"] = "manual_required"
        decision["reason"] = "open money incident: " + ",".join(
            sorted({str(i.get("error_code")) for i in incidents})
        )
        return decision

    if placements >= PLACEMENTS_BEFORE_FREEZE and clicks == 0:
        decision["status"] = "freeze_angle"
        decision["reason"] = (
            f"{placements} verified placements produced no product clicks — "
            "the angle, not the volume, is the problem"
        )
        decision["actions"] = [
            _proposal("rewrite_angle", "test a different promise on the same audience")
        ]
        return decision

    if visits < MIN_VISITS_TO_JUDGE:
        decision["status"] = "collecting_distribution"
        decision["reason"] = f"{visits} verified visits; judge nothing below {MIN_VISITS_TO_JUDGE}"
        decision["actions"] = [
            _proposal("publish_organic_asset", "keep placing until the sample is readable")
        ]
    elif clicks == 0:
        decision["status"] = "fix_offer"
        decision["reason"] = "people arrive and do not click the product — the offer is not landing"
        decision["actions"] = [_proposal("rewrite_offer", "reframe the promise above the fold")]
    elif sales == 0:
        decision["status"] = "fix_checkout_or_value"
        decision["reason"] = "clicks without sales — price, checkout, or perceived value"
        decision["actions"] = [
            _proposal("review_checkout", "walk the buyer path and look for the drop-off")
        ]
    else:
        decision["status"] = "eligible_for_next_organic_experiment"
        decision["reason"] = f"{sales} verified sales — repeat what produced them"
        decision["actions"] = [
            _proposal("publish_organic_asset", "repeat the channel that produced verified sales")
        ]

    if published_recently >= MAX_ORGANIC_ASSETS_PER_WEEK:
        decision["actions"] = []
        decision["throttle"] = "organic_asset_cap_reached"
    return decision
