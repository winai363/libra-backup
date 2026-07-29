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


def test_growth_phase_fails_closed_on_naive_now_with_aware_started_at():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    naive_now = datetime(2026, 9, 5)  # 35 days later, but naive
    assert growth_phase(started, naive_now) == "organic"


def test_growth_phase_fails_closed_on_naive_started_at_with_aware_now():
    naive_started = datetime(2026, 8, 1)
    aware_now = datetime(2026, 9, 5, tzinfo=timezone.utc)  # 35 days later, but aware
    assert growth_phase(naive_started, aware_now) == "organic"


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


def test_naive_now_with_aware_started_at_is_denied_not_crashed():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    naive_now = datetime(2026, 9, 5)  # would be day 35 if comparable
    result = authorize_growth_action(
        policy_for(started), {"kind": "price_update", "slug": "book-a"}, {"now": naive_now},
    )
    assert result == {"allowed": False, "reason": "missing_time_reference"}


def test_naive_started_at_with_aware_now_is_denied_not_crashed():
    naive_started = datetime(2026, 8, 1)
    aware_now = datetime(2026, 9, 5, tzinfo=timezone.utc)  # would be day 35 if comparable
    result = authorize_growth_action(
        {"started_at": naive_started}, {"kind": "price_update", "slug": "book-a"}, {"now": aware_now},
    )
    assert result == {"allowed": False, "reason": "missing_time_reference"}


def test_naive_last_budget_increase_reference_is_denied_not_crashed():
    """The same offset-naive/aware crash is reachable through
    last_budget_increase_at inside the amazon_ads budget-increase check."""
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = started + timedelta(days=ORGANIC_DAYS)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 55},
        state_at(
            now, clicks=20,
            advertised_title_slugs=["book-a"],
            title_daily_budget_thb=50,
            last_budget_increase_at=datetime(2026, 8, 29),  # naive, now is aware
        ),
    )
    assert result == {"allowed": False, "reason": "invalid_last_increase_reference"}


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


@pytest.mark.parametrize("bad_value", [True, 5, "book-a"])
def test_truthy_non_iterable_advertised_title_slugs_is_denied_not_crashed(bad_value):
    """set(True) / set(5) raise TypeError; a bare string would silently be
    treated as a set of characters. Both must fail closed, not crash or
    silently misbehave."""
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 50},
        state_at(_growth_window(started), clicks=20, advertised_title_slugs=bad_value),
    )
    assert result == {"allowed": False, "reason": "invalid_advertised_title_slugs"}


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


def test_budget_increase_with_nonzero_other_portfolio_spend_pins_exclusion_contract():
    """portfolio_daily_spend_thb/portfolio_monthly_spend_thb must EXCLUDE the
    target title's own current budget. Here book-a already spends 50/day and
    is increasing to 57.5; the other title (book-b) spends 40/day. If the
    caller correctly excluded book-a's own 50 from portfolio_daily_spend_thb,
    the total is 40 + 57.5 = 97.5, comfortably under the THB 100/day cap."""
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = _growth_window(started)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 57.5},
        state_at(
            now, clicks=20,
            advertised_title_slugs=["book-a", "book-b"],
            title_daily_budget_thb=50,
            portfolio_daily_spend_thb=40,  # book-b only — excludes book-a's own 50
            portfolio_monthly_spend_thb=1000,
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
# ads_intent="decrease" — the stop-loss escape hatch. A risk-REDUCING
# mutation (stop = budget 0, or a reduce below the current budget) must
# reach the adapter even though a bare daily_budget_thb <= 0 is normally
# invalid — that is the pipeline's only way to turn OFF a losing campaign.
# The Growth Gate eligibility check is intentionally left fully in force
# even for a verified decrease (see growth_policy.py's _authorize_ads
# docstring: amazon_ads_controller.ads_decision already requires
# eligibility before it will ever propose stop/reduce at all, using the
# same growth_policy.ads_eligibility this function calls, so a real caller
# following that wiring always has eligibility already holding by the time
# it gets here) — every test below therefore supplies an eligible signal
# (clicks=20) and isolates the ONE thing this fix actually changes: the
# bare daily_budget_thb <= 0 check. The label is independently re-verified
# against `state`, never trusted alone.
# ---------------------------------------------------------------------------


def test_ads_stop_at_zero_budget_is_authorized_when_decrease_is_verified():
    """The exact bug this closes: a "stop" decision (budget 0) on an
    already-advertised, currently-eligible title is authorized so a losing
    campaign can actually be turned off — the bare daily_budget_thb<=0
    check no longer unconditionally blocks it."""
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = _growth_window(started)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 0, "ads_intent": "decrease"},
        state_at(now, clicks=20, advertised_title_slugs=["book-a"], title_daily_budget_thb=50),
    )
    assert result == {"allowed": True, "reason": "growth_gate_open"}


def test_ads_reduce_to_a_lower_positive_budget_is_authorized():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = _growth_window(started)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 25, "ads_intent": "decrease"},
        state_at(now, clicks=20, advertised_title_slugs=["book-a"], title_daily_budget_thb=50),
    )
    assert result == {"allowed": True, "reason": "growth_gate_open"}


def test_ads_intent_decrease_absent_still_denies_zero_budget():
    """Absent-marker fail-closed check: WITHOUT ads_intent="decrease", a
    bare daily_budget_thb=0 stays denied "invalid_budget" exactly as
    before, even with an otherwise-eligible signal — the escape hatch
    never applies unless explicitly requested."""
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = _growth_window(started)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 0},
        state_at(now, clicks=20, advertised_title_slugs=["book-a"], title_daily_budget_thb=50),
    )
    assert result == {"allowed": False, "reason": "invalid_budget"}


def test_ads_intent_decrease_does_not_bypass_the_eligibility_check():
    """The eligibility check is deliberately NOT bypassed even for a
    verified decrease (see the module-level comment above) — with no
    growth signal at all, a "stop" proposal is still denied
    "growth_gate_closed", same as any other amazon_ads action."""
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = _growth_window(started)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 0, "ads_intent": "decrease"},
        state_at(now, clicks=0, advertised_title_slugs=["book-a"], title_daily_budget_thb=50),
    )
    assert result == {"allowed": False, "reason": "growth_gate_closed"}


def test_ads_intent_decrease_never_bypasses_start_caps_for_a_new_title():
    """A "decrease" label on a title that was never advertised is not a
    real decrease at all — is_new_title is always True, so the label is
    never honored (there's nothing to decrease from), and the normal
    new-title budget-positivity check still applies. Here daily_budget_thb
    is 0 on a title that was never advertised, so it must still be denied
    "invalid_budget", never silently allowed as a "start" at 0."""
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = _growth_window(started)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 0, "ads_intent": "decrease"},
        state_at(now, clicks=20),  # never advertised
    )
    assert result == {"allowed": False, "reason": "invalid_budget"}


def test_ads_intent_decrease_mislabeled_as_an_actual_increase_is_not_honored():
    """A mislabeled "decrease" that is really an INCREASE (proposed budget
    higher than the current one) must never skip the 15%/cooldown checks —
    verified_decrease only holds when the proposed budget is <= the
    current one. Here the "increase" exceeds the 15% cap, so it must still
    be denied on the ordinary cap path, never silently allowed."""
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = _growth_window(started)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 80, "ads_intent": "decrease"},
        state_at(
            now, clicks=20, advertised_title_slugs=["book-a"], title_daily_budget_thb=50,
            last_budget_increase_at=now - timedelta(hours=80),
        ),
    )
    assert result == {"allowed": False, "reason": "budget_increase_exceeds_15_percent_cap"}


def test_ads_intent_decrease_still_respects_negative_budget_guard():
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = _growth_window(started)
    result = authorize_growth_action(
        policy_for(started),
        {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": -5, "ads_intent": "decrease"},
        state_at(now, clicks=0, advertised_title_slugs=["book-a"], title_daily_budget_thb=50),
    )
    assert result == {"allowed": False, "reason": "invalid_budget"}


@pytest.mark.parametrize("kind", ["start", "increase"])
def test_start_and_increase_caps_are_completely_unchanged_by_the_decrease_escape_hatch(kind):
    """Regression guard: every existing start/increase test above already
    covers this, but pin it explicitly here too — a start/increase
    proposal (ads_intent absent, as every real start/increase decision
    sends it) must go through the exact same eligibility/cap/cooldown path
    as before this fix, with no behavior difference at all."""
    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    now = _growth_window(started)
    if kind == "start":
        action = {"kind": "amazon_ads", "slug": "book-new", "daily_budget_thb": 51}
        state = state_at(now, clicks=20)
    else:
        action = {"kind": "amazon_ads", "slug": "book-a", "daily_budget_thb": 58}
        state = state_at(
            now, clicks=20, advertised_title_slugs=["book-a"], title_daily_budget_thb=50,
            last_budget_increase_at=now - timedelta(hours=80),
        )
    result = authorize_growth_action(policy_for(started), action, state)
    assert result["allowed"] is False


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


def test_profit_agent_check_policy_growth_allow_still_enforces_legacy_cooldown():
    """A growth_policy allow must not bypass the legacy cooldown gate — the
    opt-in branch only short-circuits on denial; an allow falls through to
    the existing cooldown/one-variable checks (defense in depth)."""
    from profit_agent import check_policy

    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    allowed, reason = check_policy(
        {"kind": "free_promo", "slug": "book-a"},
        {
            "growth_policy": policy_for(started),
            "growth_state": state_at(started + timedelta(days=1)),
            "cooldown_slugs": ["book-a"],
        },
    )
    assert allowed is False
    assert reason == "title is already in cooldown"


def test_profit_agent_check_policy_growth_allow_passes_through_when_no_cooldown():
    """Positive control for the fall-through: when growth_policy allows and
    no legacy gate objects, the final result is still allowed."""
    from profit_agent import check_policy

    started = datetime(2026, 8, 1, tzinfo=timezone.utc)
    allowed, reason = check_policy(
        {"kind": "free_promo", "slug": "book-a"},
        {
            "growth_policy": policy_for(started),
            "growth_state": state_at(started + timedelta(days=1)),
        },
    )
    assert allowed is True
    assert reason == "allowed"
