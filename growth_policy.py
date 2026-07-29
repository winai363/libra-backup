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


def _tz_consistent(a: datetime, b: datetime) -> bool:
    """True when both datetimes are naive or both are aware — the only
    combinations Python can subtract without raising."""
    return (a.tzinfo is None) == (b.tzinfo is None)


def growth_phase(started_at, now) -> str:
    """"organic" for the first ORGANIC_DAYS complete days, "growth" once the
    Growth Gate window opens (day 31+). Invalid input — wrong type, or one
    of started_at/now naive while the other is timezone-aware — fails
    closed to the more restrictive "organic" phase rather than raising."""
    if not isinstance(started_at, datetime) or not isinstance(now, datetime):
        return "organic"
    if not _tz_consistent(started_at, now):
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
    is refused with a stable reason; nothing here ever mutates state.

    Caller-side budget contract for ``amazon_ads``: ``state["portfolio_daily_spend_thb"]``
    and ``state["portfolio_monthly_spend_thb"]`` must be the portfolio totals
    with the target title's OWN current budget (``state["title_daily_budget_thb"]``)
    already EXCLUDED. This function adds the proposed ``daily_budget_thb`` on
    top of those totals to check the THB 100/day and THB 3,000/month caps —
    if a caller included the title's existing spend in those totals, its
    current budget would be double-counted against the cap.

    ``action["ads_intent"] == "decrease"`` marks a proposed budget as a
    stop/reduce mutation on an already-advertised title (see
    ``_authorize_ads``) — the ONLY thing it ever widens is whether a
    non-increasing budget can proceed despite a bare ``daily_budget_thb <= 0``
    or a currently-closed growth-signal check; it is independently
    re-verified against ``state`` and can never raise a budget or skip any
    cap/cooldown check below. Omit it (or any other value) for every
    start/increase proposal — the field defaults to the original strict
    behavior.
    """
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
    if not _tz_consistent(started_at, now):
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

    slug = action["slug"]
    try:
        daily_budget = float(action.get("daily_budget_thb"))
    except (TypeError, ValueError):
        return _deny("invalid_budget")
    if daily_budget < 0:
        return _deny("invalid_budget")

    raw_advertised = state.get("advertised_title_slugs")
    if raw_advertised is None:
        advertised_slugs = set()
    elif isinstance(raw_advertised, (list, tuple, set, frozenset)):
        advertised_slugs = set(raw_advertised)
    else:
        return _deny("invalid_advertised_title_slugs")
    is_new_title = slug not in advertised_slugs

    # A stop/decrease mutation (budget going to 0, or down from whatever is
    # currently running) is RISK-REDUCING and must be able to reach the
    # adapter even though a bare daily_budget_thb <= 0 is normally invalid
    # — that is the pipeline's only way to turn OFF a losing campaign (see
    # amazon_ads_controller._decide_existing_title's no-order-stop and
    # break-even paths, both of which propose daily_budget_thb == 0/lower).
    # The caller opts in via action["ads_intent"] == "decrease"; the field
    # being absent (or anything else) keeps the exact original strict path
    # unchanged. This label is never trusted on its own — it is only
    # honored when INDEPENDENTLY VERIFIED against `state`: the title must
    # already be advertised (a "decrease" on a title that was never
    # started makes no sense) and the proposed budget must not actually be
    # higher than its current one. A mislabeled "decrease" that is really
    # an increase gets verified_decrease=False and falls through to the
    # exact same check as before.
    #
    # The growth-signal eligibility check is deliberately NOT bypassed
    # here, even for a verified decrease: amazon_ads_controller.ads_decision
    # itself already requires ads_eligibility(title) before it will ever
    # dispatch into _decide_existing_title's stop/reduce logic at all (see
    # its own module docstring/source), using the exact same
    # growth_policy.ads_eligibility this function calls — so eligibility is
    # structurally guaranteed to already hold whenever a caller correctly
    # wires ads_decision's output through to this authorize call. Bypassing
    # it here too would be an unused, unnecessarily wider gate.
    verified_decrease = False
    if action.get("ads_intent") == "decrease" and not is_new_title:
        try:
            current_for_decrease_check = float(state.get("title_daily_budget_thb"))
        except (TypeError, ValueError):
            current_for_decrease_check = None
        if current_for_decrease_check is not None and daily_budget <= current_for_decrease_check:
            verified_decrease = True

    if not ads_eligibility(state)["eligible"]:
        return _deny("growth_gate_closed")
    if daily_budget <= 0 and not verified_decrease:
        return _deny("invalid_budget")

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
            if isinstance(last_increase, datetime):
                if not _tz_consistent(last_increase, now):
                    return _deny("invalid_last_increase_reference")
                if now - last_increase < timedelta(hours=BUDGET_INCREASE_MIN_HOURS):
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
