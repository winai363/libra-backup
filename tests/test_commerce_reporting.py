"""Reporting must never make an unknown look like a zero."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from commerce_reporting import commerce_summary
from tests.test_commerce_reconciliation import (
    _ingest,
    _seed_verified_order,
    payhip_paid,
    stripe_payment,
    stripe_payout,
    stripe_refund,
)


def test_summary_separates_currency_and_never_counts_payout_as_revenue(tmp_path):
    db = tmp_path / "ledger.db"
    _seed_verified_order(db, gross_minor=1290, payment_id="pi_eur", currency="EUR")
    _ingest(db, payhip_paid(sale_id="sale_usd", payment_id="pi_usd", gross_minor=900, currency="USD"))
    _ingest(db, stripe_payment(event_id="evt_usd", payment_id="pi_usd", amount_minor=900,
                               currency="USD", order_id="sale_usd"))
    _ingest(db, stripe_payout("po_eur", 1240))

    result = commerce_summary(db)

    assert result["by_currency"]["EUR"]["verified_gross_minor"] == 1290
    assert result["by_currency"]["USD"]["verified_gross_minor"] == 900
    assert result["by_currency"]["EUR"]["payout_minor"] == 1240
    assert "converted_total" not in result
    assert "total" not in result


def test_missing_fees_are_unknown_not_zero(tmp_path):
    db = tmp_path / "ledger.db"
    _seed_verified_order(db, gross_minor=1290)

    eur = commerce_summary(db)["by_currency"]["EUR"]

    assert eur["payhip_fee_minor"] is None
    assert eur["stripe_fee_minor"] is None
    assert eur["payhip_fee_complete"] is False
    assert eur["stripe_fee_complete"] is False
    # Contribution needs both fee sides; with either missing it stays unknown.
    assert eur["contribution_minor"] is None


def test_refunds_reduce_net_sales_but_not_gross(tmp_path):
    db = tmp_path / "ledger.db"
    _seed_verified_order(db, gross_minor=1290)
    _ingest(db, stripe_refund("re_1", "pi_test_1", 400, status="succeeded"))

    eur = commerce_summary(db)["by_currency"]["EUR"]

    assert eur["verified_gross_minor"] == 1290
    assert eur["refunded_minor"] == 400
    assert eur["verified_net_sales_minor"] == 890


def test_an_empty_ledger_reports_nothing_rather_than_zeroes(tmp_path):
    result = commerce_summary(tmp_path / "empty.db")

    assert result["by_currency"] == {}
    assert result["open_incidents"] == []
    assert result["attribution"]["status"] == "unknown"


def test_open_incidents_are_surfaced_with_the_summary(tmp_path):
    db = tmp_path / "ledger.db"
    _ingest(db, payhip_paid(gross_minor=1290))
    _ingest(db, stripe_payment(amount_minor=990))  # mismatch → incident

    result = commerce_summary(db)

    assert any(i["error_code"] == "amount_mismatch" for i in result["open_incidents"])
    assert result["by_currency"]["EUR"]["verified_gross_minor"] == 0
    assert result["by_currency"]["EUR"]["unverified_orders"] == 1


def test_date_bounds_limit_what_is_counted(tmp_path):
    db = tmp_path / "ledger.db"
    _seed_verified_order(db, gross_minor=1290)

    inside = commerce_summary(db, start="2026-08-01", end="2026-08-31")
    outside = commerce_summary(db, start="2026-09-01", end="2026-09-30")

    assert inside["by_currency"]["EUR"]["verified_gross_minor"] == 1290
    assert outside["by_currency"] == {}


def test_attribution_stays_unknown_until_a_click_id_round_trip_is_proven(tmp_path):
    db = tmp_path / "ledger.db"
    _seed_verified_order(db, gross_minor=1290)

    attribution = commerce_summary(db)["attribution"]

    assert attribution["status"] == "unknown"
    assert attribution["verified_sales"] == 0
    assert "not attributed" not in str(attribution).lower()
