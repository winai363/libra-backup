"""Zero-budget growth decisions — deterministic, and always spending nothing."""

import ast
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commerce_growth import COMMERCE_MODULE_PATHS, commerce_growth_decision


@pytest.mark.parametrize(("metrics", "expected"), [
    ({"verified_visits": 99, "product_clicks": 20, "verified_sales": 0}, "collecting_distribution"),
    ({"verified_visits": 100, "product_clicks": 0, "verified_sales": 0}, "fix_offer"),
    ({"verified_visits": 100, "product_clicks": 10, "verified_sales": 0}, "fix_checkout_or_value"),
    ({"verified_visits": 100, "product_clicks": 10, "verified_sales": 3},
     "eligible_for_next_organic_experiment"),
    ({"verified_placements": 3, "verified_visits": 100, "product_clicks": 0,
      "verified_sales": 0}, "freeze_angle"),
])
def test_commerce_growth_decision_table(metrics, expected):
    decision = commerce_growth_decision(metrics)

    assert decision["status"] == expected
    assert decision["paid_spend_minor"] == 0


def test_every_possible_decision_spends_nothing():
    for visits in (0, 99, 100, 5000):
        for clicks in (0, 1, 10):
            for sales in (0, 1, 9):
                decision = commerce_growth_decision({
                    "verified_visits": visits, "product_clicks": clicks, "verified_sales": sales,
                })
                assert decision["paid_spend_minor"] == 0
                assert all(a.get("paid_spend_minor", 0) == 0 for a in decision["actions"])


def test_incident_stops_scaling():
    decision = commerce_growth_decision({
        "verified_visits": 100, "product_clicks": 10, "verified_sales": 3,
        "open_incidents": [{"error_code": "payout_mismatch"}],
    })

    assert decision["status"] == "manual_required"
    assert decision["actions"] == []


@pytest.mark.parametrize("error_code", [
    "amount_mismatch", "event_content_conflict", "dispute_opened", "refund_exceeds_gross",
])
def test_any_money_incident_outranks_every_growth_rule(error_code):
    decision = commerce_growth_decision({
        "verified_visits": 1000, "product_clicks": 500, "verified_sales": 50,
        "open_incidents": [{"error_code": error_code}],
    })

    assert decision["status"] == "manual_required"


def test_payhip_observed_sales_do_not_count_as_verified(tmp_path):
    decision = commerce_growth_decision({
        "verified_visits": 100, "product_clicks": 10,
        "verified_sales": 0, "payhip_observed_sales": 7,
    })

    assert decision["status"] == "fix_checkout_or_value"
    assert decision["attribution"]["status"] == "unknown"


def test_at_most_two_organic_assets_per_seven_day_window():
    decision = commerce_growth_decision({
        "verified_visits": 100, "product_clicks": 10, "verified_sales": 3,
        "assets_published_last_7d": 2,
    })

    assert decision["status"] == "eligible_for_next_organic_experiment"
    assert decision["actions"] == []
    assert decision["throttle"] == "organic_asset_cap_reached"


def test_actions_are_proposals_not_claims_of_publication():
    decision = commerce_growth_decision({
        "verified_visits": 100, "product_clicks": 10, "verified_sales": 3,
    })

    assert decision["actions"]
    for action in decision["actions"]:
        assert action["state"] == "proposed"
        assert action["requires_external_evidence"] is True


def test_commerce_modules_do_not_import_kdp_mutators():
    forbidden = {
        "kdp_upload", "kdp_finish_publish", "kdp_fix_publish",
        "kdp_live_replace", "reupload_metadata", "set_price",
        "free_promo_auto", "kdp_action_executor",
    }
    for path in COMMERCE_MODULE_PATHS:
        tree = ast.parse(Path(path).read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports |= {a.name.split(".")[-1] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[-1])
        assert imports.isdisjoint(forbidden), (path, imports & forbidden)
