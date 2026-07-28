from datetime import datetime, timedelta

from portfolio_scorer import (
    EVIDENCE_FRESHNESS_DAYS,
    FREEZE_MIN_VERIFIED_PLACEMENTS,
    MAINTAIN_SCORE_THRESHOLD,
    SCALE_SCORE_THRESHOLD,
    WEIGHT_CLICKS,
    WEIGHT_CONVERSION,
    WEIGHT_KENP,
    WEIGHT_PLACEMENTS,
    WEIGHT_ROYALTY,
    score_portfolio,
    score_title,
)


def winner_input() -> dict:
    return {
        "slug": "book-a", "royalty_delta_usd": 5, "kenp_delta": 120,
        "tracked_clicks": 25, "conversion_signal": 1, "risk_active": False,
    }


# ---------------------------------------------------------------------------
# Verbatim tests from the task brief.
# ---------------------------------------------------------------------------


def test_missing_and_estimated_signals_cannot_raise_score():
    title = {
        "slug": "book-a", "royalty_delta_usd": 0, "kenp_delta": 0,
        "tracked_clicks": 0, "verified_placements": 3,
        "estimated_market_demand": 100, "risk_active": False,
    }
    result = score_title(title)
    assert result["score"] == 0
    assert result["classification"] == "freeze"


def test_revenue_winner_is_scale_but_risky_title_is_blocked():
    winner = score_title({
        "slug": "book-a", "royalty_delta_usd": 5, "kenp_delta": 120,
        "tracked_clicks": 25, "conversion_signal": 1, "risk_active": False,
    })
    assert winner["classification"] == "scale"
    assert score_title({**winner_input(), "risk_active": True})["classification"] == "blocked"


# ---------------------------------------------------------------------------
# Evidence-only principle
# ---------------------------------------------------------------------------


def test_estimated_fields_are_always_ignored_even_when_no_verified_fields_present():
    title = {
        "slug": "book-c",
        "estimated_royalty_delta_usd": 999,
        "estimated_kenp_delta": 999,
        "estimated_market_demand": 999,
        "risk_active": False,
    }
    result = score_title(title)
    assert result["score"] == 0
    assert result["classification"] == "test"


def test_zero_signal_title_with_fewer_than_three_placements_is_test_not_freeze():
    title = {
        "slug": "book-b", "royalty_delta_usd": 0, "kenp_delta": 0,
        "tracked_clicks": 0, "verified_placements": 2, "risk_active": False,
    }
    result = score_title(title)
    assert result["score"] == 0
    assert result["classification"] == "test"


def test_freeze_requires_zero_clicks_not_just_low_score():
    # 3+ placements but clicks are still flowing -> not tested-and-dead.
    title = {
        "slug": "book-d", "royalty_delta_usd": 0, "kenp_delta": 0,
        "tracked_clicks": 1, "verified_placements": 5, "risk_active": False,
    }
    result = score_title(title)
    assert result["classification"] != "freeze"


# ---------------------------------------------------------------------------
# Classification thresholds
# ---------------------------------------------------------------------------


def test_maintain_tier_sits_between_thresholds():
    title = {
        "slug": "book-e", "royalty_delta_usd": 3, "kenp_delta": 60,
        "tracked_clicks": 12, "conversion_signal": 0.5,
        "verified_placements": 2, "risk_active": False,
    }
    result = score_title(title)
    assert MAINTAIN_SCORE_THRESHOLD <= result["score"] < SCALE_SCORE_THRESHOLD
    assert result["classification"] == "maintain"


def test_weights_are_the_approved_30_25_20_15_10_split():
    assert (WEIGHT_ROYALTY, WEIGHT_KENP, WEIGHT_CLICKS, WEIGHT_CONVERSION, WEIGHT_PLACEMENTS) == (
        30, 25, 20, 15, 10,
    )
    assert WEIGHT_ROYALTY + WEIGHT_KENP + WEIGHT_CLICKS + WEIGHT_CONVERSION + WEIGHT_PLACEMENTS == 100


def test_score_is_rounded_to_two_decimals():
    title = {
        "slug": "book-f", "royalty_delta_usd": 0.1, "kenp_delta": 1,
        "tracked_clicks": 1, "risk_active": False,
    }
    result = score_title(title)
    assert result["score"] == 1.85


# ---------------------------------------------------------------------------
# Components and reasons
# ---------------------------------------------------------------------------


def test_components_breakdown_is_present_for_every_weighted_signal():
    result = score_title(winner_input())
    assert set(result["components"]) == {
        "royalty_growth", "kenp_growth", "tracked_clicks",
        "conversion_signal", "verified_placements",
    }


def test_reasons_are_non_empty_and_explain_freeze_and_blocked():
    freeze_reasons = score_title({
        "slug": "book-a", "royalty_delta_usd": 0, "kenp_delta": 0,
        "tracked_clicks": 0, "verified_placements": 3, "risk_active": False,
    })["reasons"]
    assert freeze_reasons
    assert any("placements" in r and "0" in r for r in freeze_reasons)

    blocked_reasons = score_title({**winner_input(), "risk_active": True})["reasons"]
    assert blocked_reasons
    assert any("risk" in r.lower() for r in blocked_reasons)


# ---------------------------------------------------------------------------
# Evidence freshness (score_portfolio passes `now` through)
# ---------------------------------------------------------------------------


def test_stale_evidence_does_not_inflate_score_and_is_flagged():
    now = datetime(2026, 7, 28)
    stale_title = {
        **winner_input(),
        "evidence_as_of": now - timedelta(days=EVIDENCE_FRESHNESS_DAYS + 1),
    }
    [row] = score_portfolio([stale_title], now)
    assert row["evidence_fresh"] is False
    assert row["score"] == 0
    assert row["classification"] != "scale"


def test_fresh_evidence_within_window_scores_normally():
    now = datetime(2026, 7, 28)
    fresh_title = {
        **winner_input(),
        "evidence_as_of": now - timedelta(days=EVIDENCE_FRESHNESS_DAYS - 1),
    }
    [row] = score_portfolio([fresh_title], now)
    assert row["evidence_fresh"] is True
    assert row["classification"] == "scale"


def test_missing_evidence_timestamp_defaults_to_fresh():
    now = datetime(2026, 7, 28)
    [row] = score_portfolio([winner_input()], now)
    assert row["evidence_fresh"] is True
    assert row["classification"] == "scale"


# ---------------------------------------------------------------------------
# score_portfolio built on score_title
# ---------------------------------------------------------------------------


def test_score_portfolio_returns_one_row_per_title_keyed_by_slug():
    now = datetime(2026, 7, 28)
    titles = [
        winner_input(),
        {
            "slug": "book-a-freeze", "royalty_delta_usd": 0, "kenp_delta": 0,
            "tracked_clicks": 0, "verified_placements": 3, "risk_active": False,
        },
    ]
    rows = score_portfolio(titles, now)
    assert [row["slug"] for row in rows] == ["book-a", "book-a-freeze"]
    assert rows[0]["classification"] == "scale"
    assert rows[1]["classification"] == "freeze"
    for row in rows:
        assert set(row) == {
            "slug", "score", "classification", "components", "reasons", "evidence_fresh",
        }


def test_deterministic_same_input_yields_same_output():
    title = winner_input()
    assert score_title(dict(title)) == score_title(dict(title))
    now = datetime(2026, 7, 28)
    assert score_portfolio([title], now) == score_portfolio([title], now)
