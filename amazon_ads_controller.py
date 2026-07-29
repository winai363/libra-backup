"""Amazon Ads eligibility, budget, and profit controller for the Libra
Growth Autopilot.

Two functions, same split as the rest of the growth-autopilot modules
(growth_policy.py's pure gates, distribution_executor.py's adapter
boundary):

- `ads_decision(title, campaign, portfolio, policy, now) -> dict` — pure
  decision logic, no I/O. Decides whether/how much to spend on Amazon Ads
  for ONE title today, given the Growth Gate, the per-title profit
  picture, and portfolio-wide budget caps.
- `reconcile_ads_action(action, adapter) -> dict` — adapter-boundary
  reconciliation, same fail-closed philosophy as
  `distribution_executor.execute_distribution`: an ads mutation counts as
  `executed` only with a verified after-state readback; every
  ambiguous/exceptional adapter response fails closed to `manual_required`
  (or `blocked` for a policy rejection) rather than inventing success.

Money handling: every internal comparison happens in integer satang
(1 THB = 100 satang) so the daily-cap / 15%-increase-cap arithmetic can't
drift on float rounding across repeated calls. `ads_decision`'s public
`budget_thb` output is converted back to a float, rounded to 2 dp, only at
the return boundary.

The Growth Gate and portfolio budget constants (day 31+ organic window,
THB 100/day and THB 3,000/month caps with a 20% monthly reserve, 2-title
cap, THB 50/day initial per-title cap, 15%-per-72h increase cap) are
imported from `growth_policy` — the single source of truth for those
numbers. Nothing here re-derives or restates them.

Input shapes (none of these are existing repo types, so they're documented
here):

`title` — this title's Growth Gate evidence, using the SAME key names
`growth_policy.ads_eligibility` already expects, so `ads_decision` can pass
`title` straight through to it:
    {"royalty_growth_usd": float, "kenp_delta": float, "tracked_clicks": float,
     "stale": bool}   # stale: this title's growth metrics are not fresh

`campaign` — `None` if this title has no running Ads campaign yet, else:
    {"campaign_id": str,
     "daily_budget_thb": float,   # current live daily budget
     "net_royalty_thb": float,    # verified net royalty (margin, already
                                   # net of production/distribution cost)
                                   # attributable to this campaign this period
     "direct_cost_thb": float,    # verified ad spend this period
     "orders": int,               # verified order count this period
     "last_increase_at": datetime | None,
     "stale": bool}               # this campaign's reported figures
                                   # (spend/orders/royalty) are not fresh

Break-even ACOS is computed from `net_royalty_thb`/`direct_cost_thb` on a
MARGIN basis: since `net_royalty_thb` is already profit before ad spend,
spend exactly equal to it is the break-even point (ACOS = 100%); spend
above it is a real loss. See `BREAK_EVEN_ACOS_PCT`.

`portfolio` — portfolio-wide totals EXCLUDING this title's own current
budget (same caller contract as `growth_policy.authorize_growth_action`):
    {"daily_spend_thb": float, "monthly_spend_thb": float,
     "month": str | None,        # "YYYY-MM" the monthly_spend_thb figure
                                  # covers. See MONTHLY ROLLOVER below.
     "advertised_slugs": list | tuple | set}

`policy` — {"started_at": datetime}, same as growth_policy.

MONTHLY ROLLOVER: `portfolio["monthly_spend_thb"]` is only meaningful for
the calendar month it was measured in. If `portfolio["month"]` is a string
that does NOT match `now`'s "YYYY-MM", the monthly total is treated as 0
(last month's spend doesn't block this month's cap) rather than blocking a
new month's spend with stale data. Omitting `month` (None) is "no rollover
information available" and the monthly_spend_thb figure is used exactly
as given — fail-closed toward the CAP being enforced, not toward
permissiveness.

Fail-closed: every unknown/missing/malformed input degrades to `hold` at
the safest budget — 0 for a title with no running campaign, the CURRENT
budget for one that's already running — with a stable reason string.
Nothing here raises on bad caller input. The one deliberate exception is
the Growth Gate itself: while it is closed the budget is always forced to
0 even for an existing campaign, because growth_policy's own contract for
the gate is "stay at zero spend" — not "freeze at whatever it already is."
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta

from growth_policy import (
    BUDGET_INCREASE_MAX_FRACTION,
    BUDGET_INCREASE_MIN_HOURS,
    DAILY_CAP_THB,
    INITIAL_TITLE_CAP_THB,
    MAX_AD_TITLES,
    MONTHLY_CAP_THB,
    MONTHLY_RESERVE_FRACTION,
    ads_eligibility,
    growth_phase,
)

# No-order stop: once a campaign has spent at least one full initial daily
# budget (reusing INITIAL_TITLE_CAP_THB rather than inventing a fresh
# number) with a VERIFIED zero orders, continuing to spend is a guaranteed
# loss with no offsetting signal at all -- stop rather than keep testing.
NO_ORDER_STOP_SPEND_THB = INITIAL_TITLE_CAP_THB

# Break-even ACOS, in percent, on a margin basis -- see module docstring.
BREAK_EVEN_ACOS_PCT = 100.0


def _decision(action: str, budget_satang: int, reason: str) -> dict:
    return {"action": action, "budget_thb": _from_satang(max(int(budget_satang), 0)), "reason": reason}


def _to_satang(thb_value) -> int | None:
    """Coerce a THB amount to an integer satang count. Fails closed to
    None on anything that isn't a genuine finite number -- bool is
    explicitly excluded since Python's bool is an int subclass and
    True/False are never real money amounts, and inf/-inf/nan are
    excluded via math.isfinite since round(inf) raises OverflowError
    (not ValueError -- float("nan") raises ValueError on its own, but
    float("inf") converts cleanly and only blows up inside round())."""
    if isinstance(thb_value, bool):
        return None
    try:
        value = float(thb_value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value):
        return None
    return round(value * 100)


def _from_satang(satang: int) -> float:
    return round(satang / 100, 2)


def _tz_consistent(a: datetime, b: datetime) -> bool:
    """True when both datetimes are naive or both are aware -- the only
    combinations Python can subtract without raising."""
    return (a.tzinfo is None) == (b.tzinfo is None)


def _current_budget_satang(campaign) -> int:
    if not isinstance(campaign, dict):
        return 0
    satang = _to_satang(campaign.get("daily_budget_thb"))
    return satang if satang is not None else 0


def _advertised_slugs(portfolio):
    """Returns a set, or None if `advertised_slugs` is present but not a
    genuine collection (mirrors growth_policy.authorize_growth_action's
    guard against set(True)/set(5) raising, and a bare string silently
    being treated as a set of characters)."""
    raw = portfolio.get("advertised_slugs")
    if raw is None:
        return set()
    if isinstance(raw, (list, tuple, set, frozenset)):
        return set(raw)
    return None


def _portfolio_spend_satang(portfolio, now):
    """Returns (daily_spend_satang, monthly_spend_satang), applying the
    monthly-rollover reset when `portfolio["month"]` doesn't match `now`'s
    month. Returns None if either figure is malformed."""
    daily = _to_satang(portfolio.get("daily_spend_thb", 0) or 0)
    if daily is None:
        return None
    month = portfolio.get("month")
    monthly_raw = portfolio.get("monthly_spend_thb", 0) or 0
    if isinstance(month, str) and month != now.strftime("%Y-%m"):
        monthly_raw = 0
    monthly = _to_satang(monthly_raw)
    if monthly is None:
        return None
    return daily, monthly


def _caps_satang():
    daily_cap = _to_satang(DAILY_CAP_THB)
    monthly_cap = _to_satang(MONTHLY_CAP_THB * (1 - MONTHLY_RESERVE_FRACTION))
    return daily_cap, monthly_cap


def _decide_new_title(portfolio, now) -> dict:
    # NOTE: unlike growth_policy.authorize_growth_action (which tests a
    # caller-PROPOSED fixed budget against the caps and allows/denies
    # outright), this function picks the budget itself -- so a partial
    # cap shortfall degrades gracefully to whatever headroom remains
    # (min(...) below) rather than an all-or-nothing block. A hard
    # "*_cap_exceeded" block only fires when headroom is fully exhausted
    # (<= 0).
    slugs = _advertised_slugs(portfolio)
    if slugs is None:
        return _decision("hold", 0, "invalid_advertised_slugs")
    if len(slugs) >= MAX_AD_TITLES:
        return _decision("hold", 0, "max_advertised_titles_reached")

    spend = _portfolio_spend_satang(portfolio, now)
    if spend is None:
        return _decision("hold", 0, "invalid_portfolio_state")
    daily_spend, monthly_spend = spend
    daily_cap, monthly_cap = _caps_satang()

    daily_headroom = daily_cap - daily_spend
    if daily_headroom <= 0:
        return _decision("hold", 0, "daily_cap_exceeded")
    monthly_headroom = monthly_cap - monthly_spend
    if monthly_headroom <= 0:
        return _decision("hold", 0, "monthly_cap_exceeded")

    budget = min(_to_satang(INITIAL_TITLE_CAP_THB), daily_headroom, monthly_headroom)
    if budget <= 0:
        return _decision("hold", 0, "daily_cap_exceeded")
    return _decision("start", budget, "growth_gate_open_new_title")


def _decide_existing_title(campaign, portfolio, now) -> dict:
    current_satang = _to_satang(campaign.get("daily_budget_thb"))
    if current_satang is None:
        return _decision("hold", 0, "invalid_current_budget")

    orders = campaign.get("orders")
    orders_valid = isinstance(orders, (int, float)) and not isinstance(orders, bool)
    cost_satang = _to_satang(campaign.get("direct_cost_thb"))

    # No-order stop -- checked first, highest priority: verified zero
    # orders at or above the stop-spend threshold is a guaranteed loss
    # with no offsetting signal, regardless of what break-even ACOS says.
    if (
        orders_valid and orders == 0
        and cost_satang is not None and cost_satang >= _to_satang(NO_ORDER_STOP_SPEND_THB)
    ):
        return _decision("stop", 0, "no_order_stop_threshold")

    royalty_satang = _to_satang(campaign.get("net_royalty_thb"))
    if cost_satang is None or royalty_satang is None:
        return _decision("hold", current_satang, "insufficient_financial_data")

    # Strict "<": spend exactly equal to net royalty (ACOS exactly at the
    # BREAK_EVEN_ACOS_PCT boundary, zero net profit) is treated as NOT
    # profitable, not as a coin-flip -- a title earning literally nothing
    # from its ad spend doesn't get to increase that spend.
    profitable = royalty_satang > 0 and cost_satang < royalty_satang
    if not profitable:
        reduced = current_satang // 2
        if reduced <= 0 or reduced >= current_satang:
            return _decision("stop", 0, "break_even_acos_exceeded")
        return _decision("reduce", reduced, "break_even_acos_exceeded")

    # Profitable -- consider a bounded increase.
    last_increase = campaign.get("last_increase_at")
    if last_increase is not None:
        if not isinstance(last_increase, datetime) or not _tz_consistent(last_increase, now):
            return _decision("hold", current_satang, "invalid_last_increase_reference")
        if now - last_increase < timedelta(hours=BUDGET_INCREASE_MIN_HOURS):
            return _decision("hold", current_satang, "budget_increase_too_soon")

    spend = _portfolio_spend_satang(portfolio, now)
    if spend is None:
        return _decision("hold", current_satang, "invalid_portfolio_state")
    daily_spend, monthly_spend = spend
    daily_cap, monthly_cap = _caps_satang()

    max_by_growth = round(current_satang * (1 + BUDGET_INCREASE_MAX_FRACTION))
    max_by_daily = daily_cap - daily_spend
    max_by_monthly = monthly_cap - monthly_spend
    target = min(max_by_growth, max_by_daily, max_by_monthly)

    if target <= current_satang:
        if max_by_daily <= current_satang:
            reason = "daily_cap_exceeded"
        elif max_by_monthly <= current_satang:
            reason = "monthly_cap_exceeded"
        else:
            reason = "at_budget_cap"
        return _decision("hold", current_satang, reason)

    return _decision("increase", target, "profitable_budget_increase")


def ads_decision(title, campaign, portfolio, policy, now) -> dict:
    if not isinstance(title, dict):
        return _decision("hold", 0, "missing_title")
    if campaign is not None and not isinstance(campaign, dict):
        return _decision("hold", 0, "invalid_campaign")
    if not isinstance(portfolio, dict):
        return _decision("hold", _current_budget_satang(campaign), "missing_portfolio")
    if not isinstance(policy, dict):
        return _decision("hold", _current_budget_satang(campaign), "missing_policy")
    if not isinstance(now, datetime):
        return _decision("hold", _current_budget_satang(campaign), "missing_time_reference")

    started_at = policy.get("started_at")
    if not isinstance(started_at, datetime) or not _tz_consistent(started_at, now):
        return _decision("hold", _current_budget_satang(campaign), "missing_time_reference")

    # Growth Gate: "stay at zero spend" is unconditional while the gate is
    # closed, whether or not a campaign already exists.
    if growth_phase(started_at, now) != "growth":
        return _decision("hold", 0, "growth_gate_closed")
    if not ads_eligibility(title)["eligible"]:
        return _decision("hold", 0, "growth_gate_closed")

    stale = bool(title.get("stale")) or (campaign is not None and bool(campaign.get("stale")))

    if campaign is None:
        result = _decide_new_title(portfolio, now)
    else:
        result = _decide_existing_title(campaign, portfolio, now)

    if stale and result["action"] in ("start", "increase"):
        # Stale data can only hold, reduce, or stop -- never start or
        # increase spend on unverified numbers.
        return _decision("hold", _current_budget_satang(campaign), "stale_data")

    return result


# ---------------------------------------------------------------------------
# Adapter-boundary reconciliation
# ---------------------------------------------------------------------------

_AUTH_BARRIER_SIGNALS = ("otp_required", "captcha_required", "login_required", "session_expired")


def _result(status: str, evidence: dict, reason: str) -> dict:
    return {"status": status, "evidence": evidence, "reason": reason}


def _has_auth_barrier(response: dict) -> bool:
    return any(response.get(signal) for signal in _AUTH_BARRIER_SIGNALS)


def _is_policy_rejected(response: dict) -> bool:
    return bool(response.get("policy_rejected") or response.get("blocked"))


def _is_stale(response: dict, after_state: dict) -> bool:
    return bool(response.get("stale")) or bool(after_state.get("stale"))


def reconcile_ads_action(action, adapter) -> dict:
    """Execute one Amazon Ads mutation (start/increase/reduce/stop) through
    `adapter` and classify the result. Never raises on an
    ambiguous/ill-formed adapter response, and never propagates an
    exception `adapter.publish` itself raises -- every failure mode fails
    closed to `manual_required` instead.

    Adapter contract: `adapter.publish(action) -> dict`, same method name
    and philosophy as `distribution_executor.execute_distribution`. The
    response is classified `executed` only when it carries a real
    `campaign_id`, a numeric `budget_thb`, a `status` string, AND a
    non-empty `after_state` readback (the adapter re-read the campaign and
    confirmed the mutation actually took) -- an unreadable/missing/empty
    campaign after-state is never treated as success, matching the
    project's "never invent success" rule.
    """
    if not isinstance(action, dict):
        return _result("manual_required", {}, "invalid_action")

    try:
        response = adapter.publish(action)
    except Exception:
        return _result("manual_required", {}, "adapter_error")

    if not isinstance(response, dict):
        return _result("manual_required", {}, "invalid_adapter_response")

    if _has_auth_barrier(response):
        return _result("manual_required", {}, "auth_barrier")

    if _is_policy_rejected(response):
        return _result("blocked", {}, "policy_rejected")

    after_state = response.get("after_state")
    after_state = after_state if isinstance(after_state, dict) else {}

    if _is_stale(response, after_state):
        return _result("manual_required", {}, "stale_data")

    campaign_id = response.get("campaign_id")
    if not campaign_id or not isinstance(campaign_id, str):
        return _result("manual_required", {}, "missing_campaign_id")

    budget_thb = response.get("budget_thb")
    if isinstance(budget_thb, bool) or budget_thb is None:
        return _result("manual_required", {}, "missing_budget")
    try:
        budget_thb = float(budget_thb)
    except (TypeError, ValueError):
        return _result("manual_required", {}, "invalid_budget")
    if not math.isfinite(budget_thb):
        return _result("manual_required", {}, "invalid_budget")

    status = response.get("status")
    if not status or not isinstance(status, str):
        return _result("manual_required", {}, "missing_status")

    if not after_state:
        # Campaign ID, budget, and status all present, but no readable
        # after-state confirming the mutation actually landed -- the exact
        # "clicked=True, no readback" false-success trap this rule exists
        # to close.
        return _result("manual_required", {}, "unreadable_after_state")

    evidence = {
        "campaign_id": campaign_id,
        "budget_thb": round(budget_thb, 2),
        "status": status,
        "after_state": after_state,
    }
    return _result("executed", evidence, "verified_after_state")
