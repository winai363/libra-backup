from datetime import datetime, timedelta, timezone

import pytest

from growth_policy import (
    DAILY_CAP_THB,
    FORBIDDEN_LIVE_FIELDS,
    INITIAL_TITLE_CAP_THB,
    MAX_AD_TITLES,
    MONTHLY_CAP_THB,
    ORGANIC_DAYS,
    ads_eligibility,
    authorize_growth_action,
    growth_phase,
)


def policy_for(started: datetime) -> dict:
    return {"started_at": started}


def state_at(now: datetime, *, clicks: int = 0, **extra) -> dict:
    return {"now": now, "tracked_clicks": clicks, **extra}


# ---------------------------------------------------------------------------
# Verbatim boundary test from the task brief.
# ---------------------------------------------------------------------------


def test_ads_stay_closed_until_day_31_and_growth_signal():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    action = {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 50}
    assert authorize_growth_action(
        policy_for(started), action,
        state_at(started + timedelta(days=29), clicks=100),
    )["allowed"] is False
    assert authorize_growth_action(
        policy_for(started), action,
        state_at(started + timedelta(days=30), clicks=19),
    )["allowed"] is False
    assert authorize_growth_action(
        policy_for(started), action,
        state_at(started + timedelta(days=30), clicks=20),
    )["allowed"] is True


# ---------------------------------------------------------------------------
# growth_phase
# ---------------------------------------------------------------------------


def test_growth_phase_is_organic_before_30_complete_days():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert growth_phase(started, started + timedelta(days=29, hours=23)) == "organic"


def test_growth_phase_opens_on_day_31():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert growth_phase(started, started + timedelta(days=ORGANIC_DAYS)) == "growth"


def test_growth_phase_fails_closed_on_bad_input():
    assert growth_phase("not-a-date", datetime.now(timezone.utc)) == "organic"
    assert growth_phase(datetime.now(timezone.utc), None) == "organic"


# ---------------------------------------------------------------------------
# ads_eligibility
# ---------------------------------------------------------------------------


def test_ads_eligibility_accepts_paid_royalty_growth():
    result = ads_eligibility({"royalty_growth_usd": 0.5, "kenp_delta": 0, "tracked_clicks": 0})
    assert result == {"eligible": True, "reason": "paid_royalty_growth"}


def test_ads_eligibility_accepts_kenp_threshold():
    result = ads_eligibility({"royalty_growth_usd": 0, "kenp_delta": 100, "tracked_clicks": 0})
    assert result == {"eligible": True, "reason": "incremental_kenp_threshold"}


def test_ads_eligibility_rejects_kenp_just_under_threshold():
    result = ads_eligibility({"royalty_growth_usd": 0, "kenp_delta": 99, "tracked_clicks": 0})
    assert result == {"eligible": False, "reason": "no_growth_signal"}


def test_ads_eligibility_accepts_click_threshold():
    result = ads_eligibility({"royalty_growth_usd": 0, "kenp_delta": 0, "tracked_clicks": 20})
    assert result == {"eligible": True, "reason": "verified_tracked_clicks_threshold"}


def test_ads_eligibility_fails_closed_on_non_dict():
    assert ads_eligibility(None) == {"eligible": False, "reason": "missing_metrics"}


def test_ads_eligibility_fails_closed_on_malformed_values():
    assert ads_eligibility({"kenp_delta": "lots"}) == {"eligible": False, "reason": "invalid_metrics"}


# ---------------------------------------------------------------------------
# authorize_growth_action — fail-closed on unknown/missing input
# ---------------------------------------------------------------------------


def test_unknown_action_kind_is_denied():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = authorize_growth_action(
        policy_for(started), {"kind": "category_update", "slug": "book-a"},
        state_at(started + timedelta(days=1)),
    )
    assert result == {"allowed": False, "reason": "unsupported_action_kind"}


@pytest.mark.parametrize("policy,action,state", [
    (None, {"kind": "price_update", "slug": "book-a"}, {"now": datetime.now(timezone.utc)}),
    ({"started_at": datetime.now(timezone.utc)}, None, {"now": datetime.now(timezone.utc)}),
    ({"started_at": datetime.now(timezone.utc)}, {"kind": "price_update", "slug": "book-a"}, None),
])
def test_missing_input_is_denied(policy, action, state):
    result = authorize_growth_action(policy, action, state)
    assert result["allowed"] is False
    assert isinstance(result["reason"], str) and result["reason"]


def test_missing_time_reference_is_denied():
    result = authorize_growth_action(
        {"started_at": "not-a-date"}, {"kind": "price_update", "slug": "book-a"}, {"now": "also-not-a-date"},
    )
    assert result == {"allowed": False, "reason": "missing_time_reference"}


# ---------------------------------------------------------------------------
# Forbidden live fields — never allowed regardless of kind or phase
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field", sorted(FORBIDDEN_LIVE_FIELDS))
def test_forbidden_field_action_is_always_refused(field):
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "price_update", "slug": "book-a", "field": field},
        state_at(started + timedelta(days=1)),
    )
    assert result == {"allowed": False, "reason": "forbidden_live_field"}


def test_action_carrying_forbidden_key_directly_is_refused():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 50, "cover": "new.jpg"},
        state_at(
            started + timedelta(days=ORGANIC_DAYS),
            clicks=20,
        ),
    )
    assert result == {"allowed": False, "reason": "forbidden_live_field"}


# ---------------------------------------------------------------------------
# Organic zero-cost growth levers (price_update, free_promo, countdown_deal)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("kind", ["price_update", "free_promo", "countdown_deal"])
def test_organic_growth_actions_are_permitted_any_time(kind):
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = authorize_growth_action(
        policy_for(started), {"kind": kind, "slug": "book-a"},
        state_at(started + timedelta(days=1)),
    )
    assert result["allowed"] is True


@pytest.mark.parametrize("kind", ["price_update", "free_promo", "countdown_deal"])
def test_organic_growth_action_without_slug_is_denied(kind):
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = authorize_growth_action(
        policy_for(started), {"kind": kind},
        state_at(started + timedelta(days=1)),
    )
    assert result == {"allowed": False, "reason": "missing_slug"}


# ---------------------------------------------------------------------------
# THB 100/day and THB 3,000/month totals (with the 20% monthly reserve)
# ---------------------------------------------------------------------------


def _growth_window(started):
    return started + timedelta(days=ORGANIC_DAYS)


def test_daily_total_cap_is_enforced_across_the_portfolio():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 50},
        state_at(
            _growth_window(started), clicks=20,
            advertised_title_slugs=["book-a"],
            title_daily_budget_thb=50,
            portfolio_daily_spend_thb=60,
        ),
    )
    assert result == {"allowed": False, "reason": "daily_cap_exceeded"}
    assert DAILY_CAP_THB == 100


def test_daily_total_cap_allows_exactly_the_cap():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 50},
        state_at(
            _growth_window(started), clicks=20,
            advertised_title_slugs=["book-a"],
            title_daily_budget_thb=50,
            portfolio_daily_spend_thb=50,
        ),
    )
    assert result["allowed"] is True


def test_monthly_total_cap_reserves_20_percent():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 50},
        state_at(
            _growth_window(started), clicks=20,
            advertised_title_slugs=["book-a"],
            title_daily_budget_thb=50,
            portfolio_monthly_spend_thb=2360,
        ),
    )
    assert result == {"allowed": False, "reason": "monthly_cap_exceeded"}
    assert MONTHLY_CAP_THB == 3000


def test_monthly_total_cap_allows_within_the_80_percent_reserve_boundary():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 50},
        state_at(
            _growth_window(started), clicks=20,
            advertised_title_slugs=["book-a"],
            title_daily_budget_thb=50,
            portfolio_monthly_spend_thb=2350,
        ),
    )
    assert result["allowed"] is True


# ---------------------------------------------------------------------------
# Two-title advertised cap + THB 50/day initial per-title cap
# ---------------------------------------------------------------------------


def test_third_advertised_title_is_blocked():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-c", "daily_budget_thb": 50},
        state_at(
            _growth_window(started), clicks=20,
            advertised_title_slugs=["book-a", "book-b"],
        ),
    )
    assert result == {"allowed": False, "reason": "max_advertised_titles_reached"}
    assert MAX_AD_TITLES == 2


def test_initial_title_budget_above_50_is_blocked():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 51},
        state_at(_growth_window(started), clicks=20),
    )
    assert result == {"allowed": False, "reason": "initial_title_cap_exceeded"}
    assert INITIAL_TITLE_CAP_THB == 50


def test_initial_title_budget_at_exactly_50_is_allowed():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 50},
        state_at(_growth_window(started), clicks=20),
    )
    assert result["allowed"] is True


# ---------------------------------------------------------------------------
# Budget increase: max 15% per 72 hours
# ---------------------------------------------------------------------------


def test_budget_increase_beyond_15_percent_is_blocked():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = _growth_window(started)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 58},
        state_at(
            now, clicks=20,
            advertised_title_slugs=["book-a"],
            title_daily_budget_thb=50,
            last_budget_increase_at=now - timedelta(hours=80),
        ),
    )
    assert result == {"allowed": False, "reason": "budget_increase_exceeds_15_percent_cap"}


def test_budget_increase_within_15_percent_after_72_hours_is_allowed():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = _growth_window(started)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 57.5},
        state_at(
            now, clicks=20,
            advertised_title_slugs=["book-a"],
            title_daily_budget_thb=50,
            last_budget_increase_at=now - timedelta(hours=80),
        ),
    )
    assert result["allowed"] is True


def test_budget_increase_within_15_percent_before_72_hours_is_blocked():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = _growth_window(started)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 55},
        state_at(
            now, clicks=20,
            advertised_title_slugs=["book-a"],
            title_daily_budget_thb=50,
            last_budget_increase_at=now - timedelta(hours=10),
        ),
    )
    assert result == {"allowed": False, "reason": "budget_increase_too_soon"}


def test_budget_decrease_for_existing_title_is_unrestricted_by_increase_rule():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = _growth_window(started)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 40},
        state_at(
            now, clicks=20,
            advertised_title_slugs=["book-a"],
            title_daily_budget_thb=50,
            last_budget_increase_at=now - timedelta(hours=1),
        ),
    )
    assert result["allowed"] is True


def test_malformed_budget_is_denied():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": "lots"},
        state_at(_growth_window(started), clicks=20),
    )
    assert result == {"allowed": False, "reason": "invalid_budget"}


# ---------------------------------------------------------------------------
# profit_agent.check_policy delegates the four growth kinds to growth_policy
# ---------------------------------------------------------------------------


def test_profit_agent_check_policy_delegates_growth_actions():
    from profit_agent import check_policy

    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    allowed, reason = check_policy(
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 50},
        {
            "growth_policy": policy_for(started),
            "growth_state": state_at(started + timedelta(days=1), clicks=100),
        },
    )
    assert allowed is False
    assert reason == "growth_gate_closed"


def test_profit_agent_check_policy_ignores_growth_path_when_not_opted_in():
    """Existing callers that never pass growth_policy/growth_state keep the
    legacy 90-day organic behaviour untouched."""
    from profit_agent import check_policy

    allowed, reason = check_policy({"kind": "amazon_ads", "cost_usd": 1}, {"no_spend": True})
    assert allowed is False
    assert reason == "paid actions disabled during 90-day organic mode"
