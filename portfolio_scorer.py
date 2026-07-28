"""Deterministic portfolio scoring and capacity for the Libra Growth
Autopilot. No I/O, no database access — every function is a pure
computation over its inputs so it stays fully unit-testable.

Evidence-only principle: only verified, measured signals can raise a
score. Any `estimated_*` field (e.g. estimated_market_demand) is never
read. A title with all-zero verified signals scores 0.

Scoring (approved spec): five components normalized to 0..100 each, then
weighted 30/25/20/15/10 (royalty growth / KENP growth / tracked clicks /
conversion signal / verified placements) and rounded to two decimals.

Risk is both a penalty and a hard block: risk_active subtracts
RISK_PENALTY from the weighted score (floored at 0.0) AND forces
classification to "blocked" regardless of the resulting score — so a
downstream consumer that sorts by raw score never ranks a risky title
above a clean one, even before classification is consulted.

Classification priority: risk_active hard-blocks to "blocked" (checked
first, beating every other rule). Otherwise, >= FREEZE_MIN_VERIFIED_PLACEMENTS
verified placements that produced zero tracked clicks is evidence of
"tested and dead" and freezes the title even at score 0 — but a
zero-signal title with fewer placements than that is merely untested
("test"), not frozen. Above the freeze check, score alone picks
"scale" / "maintain" / "test" (both boundaries are inclusive: score ==
SCALE_SCORE_THRESHOLD is "scale", score == MAINTAIN_SCORE_THRESHOLD is
"maintain").

Note: "scale" here means the Growth Autopilot should keep leaning on
this title organically — it says nothing about paid spend. Amazon Ads
eligibility is gated independently and more strictly by growth_policy.py
(30 organic days + its own growth-signal check); a "scale" title is not
automatically ads-eligible.

Evidence freshness: an optional `evidence_as_of` timestamp on a title is
compared against `now` (passed by score_portfolio, or explicitly to
score_title). Evidence older than EVIDENCE_FRESHNESS_DAYS is stale and
must not inflate the score — the title is scored as if every signal were
absent (score 0) and `evidence_fresh` is reported as False. A title with
no `evidence_as_of` field has nothing to flag as stale and is treated as
fresh.
"""
from __future__ import annotations

from datetime import datetime, timedelta

# Component weights (approved spec) — sum to 100.
WEIGHT_ROYALTY = 30
WEIGHT_KENP = 25
WEIGHT_CLICKS = 20
WEIGHT_CONVERSION = 15
WEIGHT_PLACEMENTS = 10

# Normalization caps: the verified value that maps to a full 100 for that
# component. Values above the cap are clamped to 100; negative deltas are
# clamped to 0 (a decline is not a bonus, but it does not go negative).
#
# KENP_DELTA_CAP and TRACKED_CLICKS_CAP are deliberately set equal to the
# approved day-31 Growth Gate thresholds in growth_policy.py (KENP delta
# >= 100, tracked clicks >= 20) — a title that maxes either component here
# is exactly one that already clears the Gate, so the two modules read as
# one consistent bar rather than two independently-guessed ones.
# ROYALTY_DELTA_CAP_USD approximates the real portfolio's best-title
# royalty delta scale (attributed winners run roughly $2-6 per measurement
# window) rather than an arbitrary round number.
# PLACEMENTS_CAP tracks one verified placement per weekly promo/A+ cycle
# target, so a title fully saturating its placement cadence hits 100.
ROYALTY_DELTA_CAP_USD = 5.0
KENP_DELTA_CAP = 100.0
TRACKED_CLICKS_CAP = 20.0
PLACEMENTS_CAP = 5.0
# conversion_signal is expected as a 0..1 fraction; CONVERSION_SIGNAL_CAP
# is 1.0 (already the natural ceiling of a fraction) but named rather than
# inlined so every component's cap is a visible, adjustable constant.
CONVERSION_SIGNAL_CAP = 1.0

# Classification thresholds.
SCALE_SCORE_THRESHOLD = 70.0
MAINTAIN_SCORE_THRESHOLD = 40.0
FREEZE_MIN_VERIFIED_PLACEMENTS = 3

# Risk penalty: subtracted from the weighted score when risk_active is
# truthy, on top of the hard classification block (see module docstring).
RISK_PENALTY = 40.0

# Evidence freshness window.
EVIDENCE_FRESHNESS_DAYS = 14


def _normalize(value: float, cap: float) -> float:
    if cap <= 0:
        return 0.0
    return min(max(value, 0.0), cap) / cap * 100.0


def _evidence_fresh(title: dict, now: datetime | None) -> bool:
    """True unless `now` and a title's `evidence_as_of` are both given and
    the gap exceeds the freshness window. Missing `now` or missing
    `evidence_as_of` means there is nothing to flag as stale, so freshness
    defaults to True."""
    if now is None:
        return True
    evidence_as_of = title.get("evidence_as_of")
    if evidence_as_of is None:
        return True
    return (now - evidence_as_of) <= timedelta(days=EVIDENCE_FRESHNESS_DAYS)


def _component_scores(title: dict) -> dict:
    royalty_delta = title.get("royalty_delta_usd", 0) or 0
    kenp_delta = title.get("kenp_delta", 0) or 0
    tracked_clicks = title.get("tracked_clicks", 0) or 0
    conversion_signal = title.get("conversion_signal", 0) or 0
    verified_placements = title.get("verified_placements", 0) or 0

    # A placement with zero resulting clicks is not positive evidence of
    # value on its own — it only counts once there is click evidence to
    # attach it to. This keeps "placements" a measure of validated reach,
    # not a bare count that could be gamed by listing everywhere.
    placements_score = (
        _normalize(verified_placements, PLACEMENTS_CAP) if tracked_clicks > 0 else 0.0
    )

    return {
        "royalty_growth": _normalize(royalty_delta, ROYALTY_DELTA_CAP_USD),
        "kenp_growth": _normalize(kenp_delta, KENP_DELTA_CAP),
        "tracked_clicks": _normalize(tracked_clicks, TRACKED_CLICKS_CAP),
        "conversion_signal": _normalize(conversion_signal, CONVERSION_SIGNAL_CAP),
        "verified_placements": placements_score,
    }


def _weighted_score(components: dict) -> float:
    total = (
        components["royalty_growth"] * WEIGHT_ROYALTY
        + components["kenp_growth"] * WEIGHT_KENP
        + components["tracked_clicks"] * WEIGHT_CLICKS
        + components["conversion_signal"] * WEIGHT_CONVERSION
        + components["verified_placements"] * WEIGHT_PLACEMENTS
    ) / 100.0
    return round(total, 2)


def _classify(score: float, verified_placements: float, tracked_clicks: float, risk_active: bool) -> str:
    if risk_active:
        return "blocked"
    if verified_placements >= FREEZE_MIN_VERIFIED_PLACEMENTS and tracked_clicks == 0:
        return "freeze"
    if score >= SCALE_SCORE_THRESHOLD:
        return "scale"
    if score >= MAINTAIN_SCORE_THRESHOLD:
        return "maintain"
    return "test"


def _reasons(
    classification: str, score: float, verified_placements: float, tracked_clicks: float,
) -> list[str]:
    reasons = []
    if classification == "blocked":
        reasons.append(
            f"risk_active flag hard-blocks regardless of score "
            f"(score reduced by {RISK_PENALTY:g}-point penalty, floored at 0)"
        )
    elif classification == "freeze":
        reasons.append(
            f"{verified_placements:g} verified placements produced 0 tracked clicks "
            "— tested and dead"
        )
    elif classification == "scale":
        reasons.append(f"score {score:g} >= {SCALE_SCORE_THRESHOLD:g} on verified signals")
    elif classification == "maintain":
        reasons.append(
            f"score {score:g} between {MAINTAIN_SCORE_THRESHOLD:g} and "
            f"{SCALE_SCORE_THRESHOLD:g} on verified signals"
        )
    else:
        reasons.append(f"score {score:g} below {MAINTAIN_SCORE_THRESHOLD:g} — not enough evidence yet")
    return reasons


def score_title(title: dict, now: datetime | None = None) -> dict:
    """Score a single title from its verified signals only.

    `now` is optional and only used to evaluate evidence freshness
    (see module docstring); score_portfolio supplies it for every title.
    """
    risk_active = bool(title.get("risk_active", False))
    verified_placements = title.get("verified_placements", 0) or 0
    tracked_clicks = title.get("tracked_clicks", 0) or 0

    fresh = _evidence_fresh(title, now)
    if fresh:
        components = _component_scores(title)
    else:
        # Stale evidence must not inflate the score: score as if no
        # verified signal were present.
        components = {
            "royalty_growth": 0.0, "kenp_growth": 0.0, "tracked_clicks": 0.0,
            "conversion_signal": 0.0, "verified_placements": 0.0,
        }
    score = _weighted_score(components)
    if risk_active:
        # Risk is a penalty, not just a classification override: a risky
        # title must never rank above a clean one on raw score alone.
        score = round(max(score - RISK_PENALTY, 0.0), 2)

    classification = _classify(score, verified_placements, tracked_clicks, risk_active)
    reasons = _reasons(classification, score, verified_placements, tracked_clicks)

    return {
        "slug": title.get("slug"),
        "score": score,
        "classification": classification,
        "components": components,
        "reasons": reasons,
        "evidence_fresh": fresh,
    }


def score_portfolio(titles: list[dict], now: datetime) -> list[dict]:
    """Score every title in the portfolio with the same `now` reference
    for evidence freshness. Built directly on score_title."""
    return [score_title(title, now=now) for title in titles]
