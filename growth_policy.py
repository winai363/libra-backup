"""Pure-logic phase, authority, and budget policy for the Libra Growth
Autopilot. No I/O, no database access — every function is a deterministic
gate over its inputs so it stays fully unit-testable.

Growth Gate (approved spec): Amazon Ads stay at zero spend until 30 complete
organic days have elapsed (day 31+), AND at least one growth signal is
present: paid royalty growth, incremental KENP >= 100, or verified tracked
outbound clicks >= 20.

Fail closed: any unknown action kind, missing state, or malformed budget is
refused with a stable reason string rather than raising or guessing.
"""
from datetime import datetime, timedelta

ORGANIC_DAYS = 30
DAILY_CAP_THB = 100
MONTHLY_CAP_THB = 3000
INITIAL_TITLE_CAP_THB = 50
MAX_AD_TITLES = 2
MAX_ACTIVE_TITLES = 8
MAX_ORGANIC_TESTS = 3
FORBIDDEN_LIVE_FIELDS = {
    "title", "subtitle", "author", "description", "keywords",
    "categories", "cover", "interior",
}

# Only these four action kinds are recognized on the growth policy path.
# metadata_update / category_update stay governed exclusively by the legacy
# gates in profit_agent.check_policy and scripts/kdp_action_executor.py.
GROWTH_ACTION_KINDS = {"price_update", "free_promo", "countdown_deal", "amazon_ads"}

GROWTH_SIGNAL_KENP_DELTA = 100
GROWTH_SIGNAL_CLICKS = 20
MONTHLY_RESERVE_FRACTION = 0.20
BUDGET_INCREASE_MAX_FRACTION = 0.15
BUDGET_INCREASE_MIN_HOURS = 72


def _deny(reason: str) -> dict:
    return {"allowed": False, "reason": reason}


def growth_phase(started_at, now) -> str:
    """"organic" for the first ORGANIC_DAYS complete days, "growth" once the
    Growth Gate window opens (day 31+). Invalid input fails closed to the
    more restrictive "organic" phase."""
    if not isinstance(started_at, datetime) or not isinstance(now, datetime):
        return "organic"
    if now - started_at < timedelta(days=ORGANIC_DAYS):
        return "organic"
    return "growth"


def ads_eligibility(metrics) -> dict:
    """Evaluate the Growth Gate signal from evidence totals. Fails closed to
    ineligible on missing or malformed metrics."""
    if not isinstance(metrics, dict):
        return {"eligible": False, "reason": "missing_metrics"}
    try:
        royalty_growth = float(metrics.get("royalty_growth_usd", 0) or 0)
        kenp_delta = float(metrics.get("kenp_delta", 0) or 0)
        tracked_clicks = float(metrics.get("tracked_clicks", 0) or 0)
    except (TypeError, ValueError):
        return {"eligible": False, "reason": "invalid_metrics"}
    if royalty_growth > 0:
        return {"eligible": True, "reason": "paid_royalty_growth"}
    if kenp_delta >= GROWTH_SIGNAL_KENP_DELTA:
        return {"eligible": True, "reason": "incremental_kenp_threshold"}
    if tracked_clicks >= GROWTH_SIGNAL_CLICKS:
        return {"eligible": True, "reason": "verified_tracked_clicks_threshold"}
    return {"eligible": False, "reason": "no_growth_signal"}


def authorize_growth_action(policy, action, state) -> dict:
    """Authorize one proposed growth action. Every unknown or missing input
    is refused with a stable reason; nothing here ever mutates state."""
    if not isinstance(policy, dict):
        return _deny("missing_policy")
    if not isinstance(action, dict):
        return _deny("missing_action")
    if not isinstance(state, dict):
        return _deny("missing_state")

    kind = action.get("kind")
    if kind not in GROWTH_ACTION_KINDS:
        return _deny("unsupported_action_kind")

    # Refuse anything touching a live-book field, regardless of kind or
    # phase — republishing metadata/cover/interior is never a growth lever.
    if action.get("field") in FORBIDDEN_LIVE_FIELDS:
        return _deny("forbidden_live_field")
    if FORBIDDEN_LIVE_FIELDS & set(action.keys()):
        return _deny("forbidden_live_field")

    started_at = policy.get("started_at")
    now = state.get("now")
    if not isinstance(started_at, datetime) or not isinstance(now, datetime):
        return _deny("missing_time_reference")

    slug = action.get("slug")
    if not slug or not isinstance(slug, str):
        return _deny("missing_slug")

    if kind != "amazon_ads":
        # price_update / free_promo / countdown_deal are zero paid-spend
        # organic levers — permitted any time once basic shape checks pass.
        return {"allowed": True, "reason": "organic_growth_action_permitted"}

    return _authorize_ads(started_at, action, state, now)


def _authorize_ads(started_at, action, state, now) -> dict:
    if growth_phase(started_at, now) != "growth":
        return _deny("growth_gate_closed")

    if not ads_eligibility(state)["eligible"]:
        return _deny("growth_gate_closed")

    slug = action["slug"]
    try:
        daily_budget = float(action.get("daily_budget_thb"))
    except (TypeError, ValueError):
        return _deny("invalid_budget")
    if daily_budget <= 0:
        return _deny("invalid_budget")

    advertised_slugs = set(state.get("advertised_title_slugs") or [])
    is_new_title = slug not in advertised_slugs

    if is_new_title:
        if len(advertised_slugs) >= MAX_AD_TITLES:
            return _deny("max_advertised_titles_reached")
        if daily_budget > INITIAL_TITLE_CAP_THB:
            return _deny("initial_title_cap_exceeded")
    else:
        try:
            current_budget = float(state.get("title_daily_budget_thb"))
        except (TypeError, ValueError):
            return _deny("missing_current_budget")
        if daily_budget > current_budget:
            max_allowed = round(current_budget * (1 + BUDGET_INCREASE_MAX_FRACTION), 2)
            if daily_budget > max_allowed:
                return _deny("budget_increase_exceeds_15_percent_cap")
            last_increase = state.get("last_budget_increase_at")
            if isinstance(last_increase, datetime) and now - last_increase < timedelta(hours=BUDGET_INCREASE_MIN_HOURS):
                return _deny("budget_increase_too_soon")

    try:
        portfolio_daily_spend = float(state.get("portfolio_daily_spend_thb", 0) or 0)
        portfolio_monthly_spend = float(state.get("portfolio_monthly_spend_thb", 0) or 0)
    except (TypeError, ValueError):
        return _deny("invalid_portfolio_state")

    if portfolio_daily_spend + daily_budget > DAILY_CAP_THB:
        return _deny("daily_cap_exceeded")

    effective_monthly_cap = MONTHLY_CAP_THB * (1 - MONTHLY_RESERVE_FRACTION)
    if portfolio_monthly_spend + daily_budget > effective_monthly_cap:
        return _deny("monthly_cap_exceeded")

    return {"allowed": True, "reason": "growth_gate_open"}
