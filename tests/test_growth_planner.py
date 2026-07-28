from datetime import datetime, timezone

from growth_planner import build_growth_plan
from growth_policy import MAX_ACTIVE_TITLES, MAX_ORGANIC_TESTS

NOW = datetime(2026, 7, 28, 9, 0, tzinfo=timezone.utc)


def _title(slug, classification, score):
    return {
        "slug": slug,
        "score": score,
        "classification": classification,
        "components": {},
        "reasons": [],
        "evidence_fresh": True,
    }


def ranked_titles(n):
    """12 scored_titles rows spanning every classification, shaped so the
    portfolio cap and freeze/blocked exclusion are both exercised:
    4 scale + 4 test (incl. book-1, which carries an active experiment)
    fill all 8 active slots, cutting both maintain titles even though they
    outrank nothing else. book-freeze-11 scores higher than every scale
    title on purpose, to prove freeze is never "rescued" by raw score.
    """
    assert n == 12
    titles = [
        _title("book-scale-1", "scale", 90),
        _title("book-scale-2", "scale", 85),
        _title("book-scale-3", "scale", 80),
        _title("book-scale-4", "scale", 75),
        _title("book-1", "test", 60),
        _title("book-test-6", "test", 55),
        _title("book-test-7", "test", 50),
        _title("book-test-8", "test", 45),
        _title("book-maintain-9", "maintain", 65),
        _title("book-maintain-10", "maintain", 62),
        _title("book-freeze-11", "freeze", 95),
        _title("book-blocked-12", "blocked", 92),
    ]
    assert len(titles) == n
    return titles


# ---------------------------------------------------------------------------
# Verbatim test from the task brief.
# ---------------------------------------------------------------------------


def test_plan_caps_portfolio_and_never_overlaps_variables():
    titles = ranked_titles(12)
    experiments = [{"slug": "book-1", "variable": "channel"}]
    plan = build_growth_plan(
        scored_titles=titles,
        active_experiments=experiments,
        phase="organic_test", now=NOW,
    )
    assert len(plan["portfolio"]["active"]) <= 8
    assert len([a for a in plan["actions"] if a["kind"] == "organic_test"]) <= 3
    assert not any(
        a["slug"] == "book-1" and a["variable"] != "channel"
        for a in plan["actions"]
    )
    replay = build_growth_plan(
        scored_titles=titles,
        active_experiments=experiments,
        phase="organic_test", now=NOW,
    )
    assert replay["action_key"] == plan["action_key"]


# ---------------------------------------------------------------------------
# Portfolio selection: caps, priority order, freeze/blocked exclusion.
# ---------------------------------------------------------------------------


def test_portfolio_never_admits_freeze_or_blocked_regardless_of_score():
    titles = ranked_titles(12)
    plan = build_growth_plan(
        scored_titles=titles, active_experiments=[], phase="organic_test", now=NOW,
    )
    active_slugs = {row["slug"] for row in plan["portfolio"]["active"]}
    assert "book-freeze-11" not in active_slugs
    assert "book-blocked-12" not in active_slugs


def test_portfolio_respects_max_active_titles_cap():
    titles = ranked_titles(12)
    plan = build_growth_plan(
        scored_titles=titles, active_experiments=[], phase="organic_test", now=NOW,
    )
    assert len(plan["portfolio"]["active"]) == MAX_ACTIVE_TITLES


def test_portfolio_priority_is_scale_then_test_then_maintain():
    titles = ranked_titles(12)
    plan = build_growth_plan(
        scored_titles=titles, active_experiments=[], phase="organic_test", now=NOW,
    )
    # 4 scale + 4 test already fill all 8 slots, so neither maintain title
    # (which outscores several test titles) makes the cut.
    active_slugs = [row["slug"] for row in plan["portfolio"]["active"]]
    assert "book-maintain-9" not in active_slugs
    assert "book-maintain-10" not in active_slugs
    assert all(row["classification"] in ("scale", "test") for row in plan["portfolio"]["active"])


# ---------------------------------------------------------------------------
# Bounds and phase gating on organic_test actions.
# ---------------------------------------------------------------------------


def test_organic_test_actions_capped_at_max_organic_tests():
    titles = ranked_titles(12)
    plan = build_growth_plan(
        scored_titles=titles, active_experiments=[], phase="organic_test", now=NOW,
    )
    organic_tests = [a for a in plan["actions"] if a["kind"] == "organic_test"]
    assert len(organic_tests) == MAX_ORGANIC_TESTS


def test_organic_tests_withheld_outside_allowed_phase():
    titles = ranked_titles(12)
    plan = build_growth_plan(
        scored_titles=titles, active_experiments=[], phase="consolidate", now=NOW,
    )
    assert plan["actions"] == []


def test_never_emits_forbidden_metadata_or_republish_kinds():
    titles = ranked_titles(12)
    experiments = [{"slug": "book-1", "variable": "cover"}]
    plan = build_growth_plan(
        scored_titles=titles, active_experiments=experiments,
        phase="organic_test", now=NOW,
    )
    forbidden = {
        "title", "subtitle", "description", "keywords",
        "categories", "cover", "interior", "metadata_update", "category_update",
    }
    for action in plan["actions"]:
        assert action["kind"] not in forbidden
        assert action.get("variable") not in forbidden


# ---------------------------------------------------------------------------
# action_key stability / idempotent replay.
# ---------------------------------------------------------------------------


def test_action_key_changes_when_phase_or_day_changes():
    titles = ranked_titles(12)
    base = build_growth_plan(
        scored_titles=titles, active_experiments=[], phase="organic_test", now=NOW,
    )
    other_phase = build_growth_plan(
        scored_titles=titles, active_experiments=[], phase="consolidate", now=NOW,
    )
    next_day = build_growth_plan(
        scored_titles=titles, active_experiments=[], phase="organic_test",
        now=NOW.replace(day=NOW.day + 1),
    )
    assert other_phase["action_key"] != base["action_key"]
    assert next_day["action_key"] != base["action_key"]
