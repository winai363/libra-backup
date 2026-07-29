from datetime import datetime, timedelta, timezone

import pytest

from amazon_ads_controller import (
    BREAK_EVEN_ACOS_PCT,
    DAILY_CAP_THB,
    INITIAL_TITLE_CAP_THB,
    MAX_AD_TITLES,
    MONTHLY_CAP_THB,
    NO_ORDER_STOP_SPEND_THB,
    ads_decision,
    reconcile_ads_action,
)


# ---------------------------------------------------------------------------
# Test-local fixtures/helpers (field names are this test file's own choice
# -- ads_decision's real input shapes are documented in the module
# docstring; these helpers just build dicts matching that shape).
# ---------------------------------------------------------------------------

STARTED = datetime(2026, 8, 1, tzinfo=timezone.utc)
DAY_31 = STARTED + timedelta(days=30)  # growth_policy.ORGANIC_DAYS == 30


def day_31_policy():
    return {"started_at": STARTED}


def title_metrics(*, clicks=0, kenp_delta=0, royalty_delta=0, stale=False, **extra):
    return {
        "royalty_growth_usd": royalty_delta,
        "kenp_delta": kenp_delta,
        "tracked_clicks": clicks,
        "stale": stale,
        **extra,
    }


def campaign_state(
    *, budget, contribution=0, direct_cost=0, orders=1,
    last_increase_hours=None, campaign_id="camp-1", stale=False, now=DAY_31, **extra,
):
    last_increase_at = None
    if last_increase_hours is not None:
        last_increase_at = now - timedelta(hours=last_increase_hours)
    return {
        "campaign_id": campaign_id,
        "daily_budget_thb": budget,
        "net_royalty_thb": contribution,
        "direct_cost_thb": direct_cost,
        "orders": orders,
        "last_increase_at": last_increase_at,
        "stale": stale,
        **extra,
    }


def portfolio_state(*, daily_spend=0, monthly_spend=0, month=None, advertised_slugs=None, **extra):
    return {
        "daily_spend_thb": daily_spend,
        "monthly_spend_thb": monthly_spend,
        "month": month,
        "advertised_slugs": advertised_slugs if advertised_slugs is not None else [],
        **extra,
    }


PROFITABLE_TITLE = title_metrics(clicks=20, kenp_delta=100, royalty_delta=1)


# ---------------------------------------------------------------------------
# Verbatim brief tests
# ---------------------------------------------------------------------------


def test_day_31_without_growth_remains_zero_spend():
    decision = ads_decision(
        title_metrics(clicks=19, kenp_delta=99, royalty_delta=0),
        campaign=None, portfolio=portfolio_state(), policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "growth_gate_closed"}


def test_profitable_budget_increase_is_bounded():
    decision = ads_decision(
        title_metrics(clicks=20, kenp_delta=100, royalty_delta=2),
        campaign_state(budget=50, contribution=1, last_increase_hours=80),
        portfolio=portfolio_state(daily_spend=40, monthly_spend=500),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision["budget_thb"] <= 57.5


# ---------------------------------------------------------------------------
# Growth Gate closed -- always forces zero, even with an existing campaign
# ---------------------------------------------------------------------------


def test_growth_gate_closed_before_day_31_regardless_of_signals():
    decision = ads_decision(
        title_metrics(clicks=100, kenp_delta=1000, royalty_delta=10),
        campaign=None, portfolio=portfolio_state(),
        policy=day_31_policy(), now=STARTED + timedelta(days=10),
    )
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "growth_gate_closed"}


def test_growth_gate_closed_forces_zero_even_with_existing_profitable_campaign():
    decision = ads_decision(
        title_metrics(clicks=0, kenp_delta=0, royalty_delta=0),
        campaign_state(budget=50, contribution=100, last_increase_hours=80, now=STARTED + timedelta(days=10)),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=STARTED + timedelta(days=10),
    )
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "growth_gate_closed"}


# ---------------------------------------------------------------------------
# New-title start: two-title capacity + THB 50/day initial cap + daily/
# monthly headroom
# ---------------------------------------------------------------------------


def test_third_advertised_title_is_blocked():
    decision = ads_decision(
        PROFITABLE_TITLE, campaign=None,
        portfolio=portfolio_state(advertised_slugs=["book-a", "book-b"]),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "max_advertised_titles_reached"}
    assert MAX_AD_TITLES == 2


@pytest.mark.parametrize("bad_value", [True, 5, "book-a"])
def test_truthy_non_iterable_advertised_slugs_is_denied_not_crashed(bad_value):
    decision = ads_decision(
        PROFITABLE_TITLE, campaign=None,
        portfolio=portfolio_state(advertised_slugs=bad_value),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "invalid_advertised_slugs"}


def test_new_title_starts_at_initial_cap_when_portfolio_has_headroom():
    decision = ads_decision(
        PROFITABLE_TITLE, campaign=None, portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "start", "budget_thb": 50.0, "reason": "growth_gate_open_new_title"}
    assert INITIAL_TITLE_CAP_THB == 50


def test_new_title_start_is_capped_by_daily_headroom_below_the_initial_cap():
    decision = ads_decision(
        PROFITABLE_TITLE, campaign=None,
        portfolio=portfolio_state(daily_spend=60),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "start", "budget_thb": 40.0, "reason": "growth_gate_open_new_title"}
    assert DAILY_CAP_THB == 100


def test_new_title_blocked_when_daily_cap_already_exhausted():
    decision = ads_decision(
        PROFITABLE_TITLE, campaign=None,
        portfolio=portfolio_state(daily_spend=100),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "daily_cap_exceeded"}


# ---------------------------------------------------------------------------
# 20% monthly reserve. Unlike growth_policy.authorize_growth_action (which
# tests a caller-PROPOSED fixed budget against the cap and allows/denies
# outright), ads_decision picks the budget itself, so a partial monthly
# headroom degrades gracefully to whatever room is left (same behaviour as
# the daily-headroom test above) rather than a hard all-or-nothing block;
# a hard "monthly_cap_exceeded" block only fires when there is zero
# headroom left at all.
# ---------------------------------------------------------------------------


def test_monthly_reserve_degrades_the_new_title_budget_to_remaining_headroom():
    # Effective cap is THB 2,400 (80% of 3,000); 2,360 spent leaves only
    # THB 40 of headroom -- below the THB 50 initial cap.
    decision = ads_decision(
        PROFITABLE_TITLE, campaign=None,
        portfolio=portfolio_state(monthly_spend=2360, month="2026-08"),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "start", "budget_thb": 40.0, "reason": "growth_gate_open_new_title"}
    assert MONTHLY_CAP_THB == 3000


def test_monthly_reserve_allows_within_the_80_percent_boundary():
    decision = ads_decision(
        PROFITABLE_TITLE, campaign=None,
        portfolio=portfolio_state(monthly_spend=2350, month="2026-08"),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "start", "budget_thb": 50.0, "reason": "growth_gate_open_new_title"}


def test_monthly_reserve_hard_blocks_when_headroom_is_fully_exhausted():
    decision = ads_decision(
        PROFITABLE_TITLE, campaign=None,
        portfolio=portfolio_state(monthly_spend=2400, month="2026-08"),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "monthly_cap_exceeded"}


# ---------------------------------------------------------------------------
# Monthly rollover: a stale month's spend figure must not block this
# month's cap
# ---------------------------------------------------------------------------


def test_monthly_rollover_resets_a_stale_months_spend_to_zero():
    # 2900 is well over the effective THB 2,400 reserve cap, but it's
    # tagged "2026-06" while `now` (DAY_31) is in "2026-08" -- last
    # month's spend must not block this month's budget.
    decision = ads_decision(
        PROFITABLE_TITLE, campaign=None,
        portfolio=portfolio_state(monthly_spend=2900, month="2026-06"),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "start", "budget_thb": 50.0, "reason": "growth_gate_open_new_title"}


def test_missing_month_field_uses_monthly_spend_as_is_fail_closed():
    decision = ads_decision(
        PROFITABLE_TITLE, campaign=None,
        portfolio=portfolio_state(monthly_spend=2900, month=None),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "monthly_cap_exceeded"}


def test_month_matching_now_uses_the_spend_figure_as_is():
    decision = ads_decision(
        PROFITABLE_TITLE, campaign=None,
        portfolio=portfolio_state(monthly_spend=2900, month="2026-08"),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "monthly_cap_exceeded"}


# ---------------------------------------------------------------------------
# Stale data: can only hold, reduce, or stop -- never start or increase
# ---------------------------------------------------------------------------


def test_stale_title_metrics_block_a_new_start():
    decision = ads_decision(
        title_metrics(clicks=20, kenp_delta=100, royalty_delta=1, stale=True),
        campaign=None, portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "stale_data"}


def test_stale_campaign_data_blocks_an_increase_but_keeps_current_budget():
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=50, contribution=1, last_increase_hours=80, stale=True),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 50.0, "reason": "stale_data"}


def test_stale_data_does_not_block_a_stop_or_reduce():
    # A stop/reduce decision is still allowed under stale data -- only
    # start/increase are blocked.
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=50, orders=0, direct_cost=50, stale=True),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "stop", "budget_thb": 0, "reason": "no_order_stop_threshold"}


# ---------------------------------------------------------------------------
# No-order stop threshold
# ---------------------------------------------------------------------------


def test_no_order_stop_threshold_reached():
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=50, orders=0, direct_cost=50, last_increase_hours=80),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "stop", "budget_thb": 0, "reason": "no_order_stop_threshold"}
    assert NO_ORDER_STOP_SPEND_THB == 50


def test_zero_orders_below_the_stop_threshold_does_not_force_a_stop():
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=50, orders=0, direct_cost=10, contribution=100, last_increase_hours=80),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision["action"] != "stop"
    assert decision["reason"] != "no_order_stop_threshold"


# ---------------------------------------------------------------------------
# Break-even ACOS
# ---------------------------------------------------------------------------


def test_break_even_acos_exceeded_reduces_the_budget():
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=50, orders=3, direct_cost=60, contribution=40, last_increase_hours=80),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "reduce", "budget_thb": 25.0, "reason": "break_even_acos_exceeded"}
    assert BREAK_EVEN_ACOS_PCT == 100.0


def test_break_even_loss_stops_when_reduction_would_be_negligible():
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=0.01, orders=1, direct_cost=10, contribution=1, last_increase_hours=80),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "stop", "budget_thb": 0, "reason": "break_even_acos_exceeded"}


def test_missing_financial_data_holds_at_current_budget_instead_of_guessing():
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=50, contribution=None, direct_cost=None, last_increase_hours=80),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 50.0, "reason": "insufficient_financial_data"}


# ---------------------------------------------------------------------------
# Bounded increase: 15% / 72h cooldown, daily/monthly caps
# ---------------------------------------------------------------------------


def test_budget_increase_too_soon_holds_at_current_budget():
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=50, contribution=100, last_increase_hours=10),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 50.0, "reason": "budget_increase_too_soon"}


def test_no_prior_increase_is_not_subject_to_the_cooldown():
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=50, contribution=100, last_increase_hours=None),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision["action"] == "increase"
    assert decision["budget_thb"] == 57.5


def test_naive_last_increase_at_with_aware_now_is_denied_not_crashed():
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(
            budget=50, contribution=100,
            last_increase_at=datetime(2026, 8, 28),  # naive, now is aware
        ),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 50.0, "reason": "invalid_last_increase_reference"}


def test_budget_increase_capped_by_remaining_daily_headroom():
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=90, contribution=1000, last_increase_hours=80),
        portfolio=portfolio_state(daily_spend=10),  # 100 - 10 - 90(current) == 0 headroom left
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 90.0, "reason": "daily_cap_exceeded"}


# ---------------------------------------------------------------------------
# 15% increase-ceiling rounding must agree with growth_policy's own
# rounding (THB, 2dp) -- NOT a separate satang-domain rounding, which
# disagrees at some budget levels (Python round-half-to-even).
# ---------------------------------------------------------------------------


def test_increase_ceiling_pinned_at_a_previously_divergent_value():
    # THB 1.30 * 1.15 = 1.4949999999999999 in float -> round(.,2) = 1.49
    # (growth_policy's own rounding domain). A satang-domain rounding
    # instead computes round(130 * 1.15) = round(149.5) = 150 (banker's
    # rounding on the exact half), i.e. THB 1.50 -- a whole satang off.
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=1.30, contribution=1000, last_increase_hours=80),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "increase", "budget_thb": 1.49, "reason": "profitable_budget_increase"}


def test_increase_ceiling_agrees_with_growth_policys_rounding_across_a_range_of_budgets():
    from growth_policy import BUDGET_INCREASE_MAX_FRACTION

    # Kept under ~87 THB so the 15%-growth ceiling (current * 1.15) never
    # crosses the fixed THB 100/day portfolio cap and becomes the binding
    # constraint instead of the thing under test.
    for satang in range(100, 8600, 7):  # a range of odd/uneven current budgets
        current_thb = satang / 100
        expected_ceiling_thb = round(current_thb * (1 + BUDGET_INCREASE_MAX_FRACTION), 2)
        decision = ads_decision(
            PROFITABLE_TITLE,
            campaign_state(budget=current_thb, contribution=1_000_000, last_increase_hours=80),
            portfolio=portfolio_state(),
            policy=day_31_policy(), now=DAY_31,
        )
        if expected_ceiling_thb <= current_thb:
            # No real increase possible at this budget (rounds down to
            # itself or below) -- ads_decision must not report "increase".
            assert decision["action"] != "increase", (current_thb, decision)
        else:
            assert decision == {
                "action": "increase", "budget_thb": expected_ceiling_thb,
                "reason": "profitable_budget_increase",
            }, (current_thb, decision)


# ---------------------------------------------------------------------------
# Negative money is malformed data, not a valid low value
# ---------------------------------------------------------------------------


def test_negative_current_budget_is_denied_not_silently_accepted():
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=-50, contribution=100, last_increase_hours=80),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "invalid_current_budget"}


def test_negative_direct_cost_is_denied_not_silently_accepted():
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=50, direct_cost=-10, contribution=100, last_increase_hours=80),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 50.0, "reason": "insufficient_financial_data"}


def test_negative_portfolio_daily_spend_is_denied_not_silently_accepted():
    decision = ads_decision(
        PROFITABLE_TITLE, campaign=None,
        portfolio=portfolio_state(daily_spend=-10),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "invalid_portfolio_state"}


# ---------------------------------------------------------------------------
# Malformed vs. absent `orders` -- absent keeps the pre-existing semantics
# (no verified order count -- skip the no-order-stop check); a present but
# malformed value is a distinct, explicit fail-closed hold.
# ---------------------------------------------------------------------------


def test_absent_orders_field_skips_the_no_order_stop_check():
    campaign = campaign_state(budget=50, contribution=100, last_increase_hours=80)
    del campaign["orders"]
    decision = ads_decision(PROFITABLE_TITLE, campaign, portfolio_state(), day_31_policy(), DAY_31)
    assert decision["action"] == "increase"


@pytest.mark.parametrize("bad_orders", [None, "three", True, False])
def test_malformed_orders_present_holds_with_insufficient_order_data(bad_orders):
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=50, contribution=100, orders=bad_orders, last_increase_hours=80),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 50.0, "reason": "insufficient_order_data"}


# ---------------------------------------------------------------------------
# reconcile_ads_action -- adapter boundary
# ---------------------------------------------------------------------------


class FakeAdapter:
    def __init__(self, response):
        self.response = response

    def publish(self, action):
        return self.response


class RaisingAdapter:
    def publish(self, action):
        raise RuntimeError("connection reset")


def test_reconcile_executed_requires_campaign_id_budget_status_and_after_state():
    result = reconcile_ads_action(
        {"kind": "amazon_ads", "slug": "book-a"},
        adapter=FakeAdapter({
            "campaign_id": "amzn-camp-42",
            "budget_thb": 50,
            "status": "active",
            "after_state": {"daily_budget": 50, "status": "active"},
        }),
    )
    assert result["status"] == "executed"
    assert result["evidence"] == {
        "campaign_id": "amzn-camp-42", "budget_thb": 50.0, "status": "active",
        "after_state": {"daily_budget": 50, "status": "active"},
    }


def test_reconcile_unreadable_after_state_is_not_executed():
    result = reconcile_ads_action(
        {"kind": "amazon_ads", "slug": "book-a"},
        adapter=FakeAdapter({"campaign_id": "amzn-camp-42", "budget_thb": 50, "status": "active"}),
    )
    assert result == {"status": "manual_required", "evidence": {}, "reason": "unreadable_after_state"}


def test_reconcile_empty_after_state_is_not_executed():
    result = reconcile_ads_action(
        {"kind": "amazon_ads", "slug": "book-a"},
        adapter=FakeAdapter({
            "campaign_id": "amzn-camp-42", "budget_thb": 50, "status": "active", "after_state": {},
        }),
    )
    assert result == {"status": "manual_required", "evidence": {}, "reason": "unreadable_after_state"}


def test_reconcile_missing_campaign_id_is_manual_required():
    result = reconcile_ads_action(
        {"kind": "amazon_ads", "slug": "book-a"},
        adapter=FakeAdapter({"budget_thb": 50, "status": "active", "after_state": {"status": "active"}}),
    )
    assert result == {"status": "manual_required", "evidence": {}, "reason": "missing_campaign_id"}


def test_reconcile_missing_budget_is_manual_required():
    result = reconcile_ads_action(
        {"kind": "amazon_ads", "slug": "book-a"},
        adapter=FakeAdapter({"campaign_id": "amzn-camp-42", "status": "active", "after_state": {"status": "active"}}),
    )
    assert result == {"status": "manual_required", "evidence": {}, "reason": "missing_budget"}


def test_reconcile_malformed_budget_is_manual_required():
    result = reconcile_ads_action(
        {"kind": "amazon_ads", "slug": "book-a"},
        adapter=FakeAdapter({
            "campaign_id": "amzn-camp-42", "budget_thb": "lots", "status": "active",
            "after_state": {"status": "active"},
        }),
    )
    assert result == {"status": "manual_required", "evidence": {}, "reason": "invalid_budget"}


@pytest.mark.parametrize("bad_value", [float("inf"), float("-inf"), float("nan")])
def test_reconcile_non_finite_budget_is_manual_required_not_invented_success(bad_value):
    result = reconcile_ads_action(
        {"kind": "amazon_ads", "slug": "book-a"},
        adapter=FakeAdapter({
            "campaign_id": "amzn-camp-42", "budget_thb": bad_value, "status": "active",
            "after_state": {"status": "active"},
        }),
    )
    assert result == {"status": "manual_required", "evidence": {}, "reason": "invalid_budget"}


def test_reconcile_missing_status_is_manual_required():
    result = reconcile_ads_action(
        {"kind": "amazon_ads", "slug": "book-a"},
        adapter=FakeAdapter({"campaign_id": "amzn-camp-42", "budget_thb": 50, "after_state": {"x": 1}}),
    )
    assert result == {"status": "manual_required", "evidence": {}, "reason": "missing_status"}


@pytest.mark.parametrize("signal", ["otp_required", "captcha_required", "login_required", "session_expired"])
def test_reconcile_auth_barrier_is_manual_required(signal):
    result = reconcile_ads_action(
        {"kind": "amazon_ads", "slug": "book-a"},
        adapter=FakeAdapter({signal: True}),
    )
    assert result == {"status": "manual_required", "evidence": {}, "reason": "auth_barrier"}


def test_reconcile_policy_rejection_is_blocked():
    result = reconcile_ads_action(
        {"kind": "amazon_ads", "slug": "book-a"},
        adapter=FakeAdapter({"policy_rejected": True}),
    )
    assert result == {"status": "blocked", "evidence": {}, "reason": "policy_rejected"}


def test_reconcile_adapter_exception_is_manual_required():
    result = reconcile_ads_action({"kind": "amazon_ads", "slug": "book-a"}, adapter=RaisingAdapter())
    assert result == {"status": "manual_required", "evidence": {}, "reason": "adapter_error"}


def test_reconcile_top_level_stale_flag_is_manual_required():
    result = reconcile_ads_action(
        {"kind": "amazon_ads", "slug": "book-a"},
        adapter=FakeAdapter({
            "campaign_id": "amzn-camp-42", "budget_thb": 50, "status": "active",
            "after_state": {"status": "active"}, "stale": True,
        }),
    )
    assert result == {"status": "manual_required", "evidence": {}, "reason": "stale_data"}


def test_reconcile_after_state_stale_flag_is_manual_required():
    result = reconcile_ads_action(
        {"kind": "amazon_ads", "slug": "book-a"},
        adapter=FakeAdapter({
            "campaign_id": "amzn-camp-42", "budget_thb": 50, "status": "active",
            "after_state": {"status": "active", "stale": True},
        }),
    )
    assert result == {"status": "manual_required", "evidence": {}, "reason": "stale_data"}


def test_reconcile_invalid_action_is_manual_required():
    result = reconcile_ads_action(None, adapter=FakeAdapter({}))
    assert result == {"status": "manual_required", "evidence": {}, "reason": "invalid_action"}


def test_reconcile_invalid_adapter_response_is_manual_required():
    result = reconcile_ads_action({"kind": "amazon_ads"}, adapter=FakeAdapter("not-a-dict"))
    assert result == {"status": "manual_required", "evidence": {}, "reason": "invalid_adapter_response"}


def test_reconcile_never_invents_success_from_a_bare_click_flag():
    result = reconcile_ads_action(
        {"kind": "amazon_ads", "slug": "book-a"},
        adapter=FakeAdapter({"clicked": True}),
    )
    assert result["status"] == "manual_required"
    assert result["evidence"] == {}


# ---------------------------------------------------------------------------
# ads_decision -- fail-closed on unknown/missing/malformed input
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("title,campaign,portfolio,policy,now", [
    (None, None, portfolio_state(), day_31_policy(), DAY_31),
    (PROFITABLE_TITLE, "not-a-dict-or-none", portfolio_state(), day_31_policy(), DAY_31),
    (PROFITABLE_TITLE, None, None, day_31_policy(), DAY_31),
    (PROFITABLE_TITLE, None, portfolio_state(), None, DAY_31),
    (PROFITABLE_TITLE, None, portfolio_state(), day_31_policy(), "not-a-date"),
    (PROFITABLE_TITLE, None, portfolio_state(), {"started_at": "not-a-date"}, DAY_31),
])
def test_malformed_input_is_denied_not_crashed(title, campaign, portfolio, policy, now):
    decision = ads_decision(title, campaign, portfolio, policy, now)
    assert decision["action"] == "hold"
    assert isinstance(decision["reason"], str) and decision["reason"]


def test_naive_now_with_aware_started_at_is_denied_not_crashed():
    naive_now = datetime(2026, 9, 5)  # would be well past day 31 if comparable
    decision = ads_decision(PROFITABLE_TITLE, None, portfolio_state(), day_31_policy(), naive_now)
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "missing_time_reference"}


def test_malformed_current_budget_on_existing_campaign_is_denied_not_crashed():
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget="lots", contribution=100, last_increase_hours=80),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "invalid_current_budget"}


def test_malformed_portfolio_daily_spend_on_existing_campaign_is_denied_not_crashed():
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=50, contribution=100, last_increase_hours=80),
        portfolio=portfolio_state(daily_spend="lots"),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 50.0, "reason": "invalid_portfolio_state"}


# ---------------------------------------------------------------------------
# Non-finite floats (inf/-inf/nan) must fail closed, not raise -- round()
# raises OverflowError (not ValueError) on inf, which is easy to miss when
# only guarding TypeError/ValueError.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [float("inf"), float("-inf"), float("nan")])
def test_infinite_or_nan_current_budget_is_denied_not_crashed(bad_value):
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=bad_value, contribution=100, last_increase_hours=80),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "invalid_current_budget"}


@pytest.mark.parametrize("bad_value", [float("inf"), float("-inf"), float("nan")])
def test_infinite_or_nan_portfolio_daily_spend_is_denied_not_crashed(bad_value):
    decision = ads_decision(
        PROFITABLE_TITLE, campaign=None,
        portfolio=portfolio_state(daily_spend=bad_value),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 0, "reason": "invalid_portfolio_state"}


@pytest.mark.parametrize("bad_value", [float("inf"), float("-inf"), float("nan")])
def test_infinite_or_nan_direct_cost_is_denied_not_crashed(bad_value):
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=50, direct_cost=bad_value, contribution=100, last_increase_hours=80),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "hold", "budget_thb": 50.0, "reason": "insufficient_financial_data"}


def test_break_even_at_exactly_the_boundary_is_treated_as_not_profitable():
    # cost == royalty -- ACOS exactly at BREAK_EVEN_ACOS_PCT (100%), zero
    # net profit. Documented as NOT profitable (strict "<" required), not
    # a coin-flip.
    decision = ads_decision(
        PROFITABLE_TITLE,
        campaign_state(budget=50, orders=3, direct_cost=40, contribution=40, last_increase_hours=80),
        portfolio=portfolio_state(),
        policy=day_31_policy(), now=DAY_31,
    )
    assert decision == {"action": "reduce", "budget_thb": 25.0, "reason": "break_even_acos_exceeded"}
