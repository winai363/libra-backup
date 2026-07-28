"""Pure-logic, idempotent growth planner for the Libra Growth Autopilot. No
I/O, no database access, no wall clock — every call is a deterministic
function of its arguments (the `now` parameter stands in for the clock) so
it stays fully unit-testable and safely replayable.

Consumes `portfolio_scorer.score_portfolio` rows (slug, score,
classification) and the caller's active experiment list, and produces one
bounded plan: which titles make the active portfolio and which of them get
an `organic_test` action this cycle.

Portfolio selection (approved spec): freeze and blocked titles are NEVER
selected into the active portfolio, no matter how high their raw score —
that block is deliberate (freeze means "tested and dead"; blocked means
risk_active). Everything else is ranked by classification priority
(scale > test > maintain), then score descending, then slug ascending as a
final, fully deterministic tiebreak, and the top MAX_ACTIVE_TITLES rows
become the active portfolio.

One-variable rule: `active_experiments` lists {slug, variable} pairs
already running. A title with an active experiment may only ever receive a
plan action on that SAME variable within the window — the planner never
proposes a second variable for a slug mid-experiment. A title with no
active experiment defaults to DEFAULT_ORGANIC_TEST_VARIABLE.

Action kinds: this planner only ever emits `organic_test` actions, for
"test"-classified titles in the active portfolio, bounded by
MAX_ORGANIC_TESTS and gated by phase (ORGANIC_TEST_PHASES). It never emits
a metadata/republish kind or variable (title/subtitle/author/description/
keywords/categories/cover/interior) — those stay forbidden everywhere in
this system, so any action that would touch one is dropped rather than
emitted.

action_key: a stable sha256 hex digest over a canonical JSON dump of
(phase, the calendar date of `now`, and the sorted (slug, variable) pairs
selected for action). Same inputs always produce the same key, so a
replayed plan for the same day is byte-identical and safely idempotent —
callers can use action_key to detect and skip a duplicate replay.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from growth_policy import FORBIDDEN_LIVE_FIELDS, MAX_ACTIVE_TITLES, MAX_ORGANIC_TESTS

# Classification priority for active-portfolio ranking. freeze and blocked
# are intentionally absent — they are excluded before ranking, not merely
# ranked last, so they can never fill a slot even when under-capacity.
_CLASSIFICATION_PRIORITY = {"scale": 0, "test": 1, "maintain": 2}

# Phases in which an organic_test action may be proposed. Any other phase
# (e.g. a consolidation phase) withholds organic_test actions entirely,
# regardless of portfolio composition.
ORGANIC_TEST_PHASES = {"organic_test"}

# Variable assigned to a "test" title with no active experiment yet.
DEFAULT_ORGANIC_TEST_VARIABLE = "price"

# Kinds/variables an organic_test action must never carry, even if a caller
# passed a forbidden value in via active_experiments — republishing
# metadata/cover/interior is never a growth lever (see module docstring).
_FORBIDDEN_ACTION_VALUES = FORBIDDEN_LIVE_FIELDS | {"metadata_update", "category_update"}


def _experiment_variable_by_slug(active_experiments: list[dict]) -> dict:
    variables = {}
    for experiment in active_experiments or []:
        if not isinstance(experiment, dict):
            continue
        slug = experiment.get("slug")
        variable = experiment.get("variable")
        if isinstance(slug, str) and isinstance(variable, str):
            variables[slug] = variable
    return variables


def _coerce_score(title: dict):
    """Numeric score for ranking, or None if the row's score is malformed
    (fails closed: the row is excluded from ranking entirely rather than
    raising or silently defaulting to 0, which would let a bad row rank
    as if it had the worst possible score)."""
    try:
        return float(title.get("score", 0) or 0)
    except (TypeError, ValueError):
        return None


def _rank_active_portfolio(scored_titles: list[dict]) -> list[dict]:
    eligible = []
    seen_slugs = set()
    for title in scored_titles or []:
        if not isinstance(title, dict):
            continue
        slug = title.get("slug")
        if not isinstance(slug, str):
            continue
        if title.get("classification") not in _CLASSIFICATION_PRIORITY:
            continue
        score = _coerce_score(title)
        if score is None:
            continue
        if slug in seen_slugs:
            # First occurrence wins — a later duplicate row for the same
            # slug is skipped so the active-portfolio cap counts unique
            # titles, not duplicate rows.
            continue
        seen_slugs.add(slug)
        eligible.append((title, score))

    eligible.sort(
        key=lambda pair: (
            _CLASSIFICATION_PRIORITY[pair[0]["classification"]],
            -pair[1],
            pair[0]["slug"],
        )
    )
    return [title for title, _score in eligible[:MAX_ACTIVE_TITLES]]


def _plan_organic_tests(
    active_portfolio: list[dict], experiment_variable_by_slug: dict, phase: str,
) -> list[dict]:
    if phase not in ORGANIC_TEST_PHASES:
        return []
    actions = []
    for title in active_portfolio:
        if len(actions) >= MAX_ORGANIC_TESTS:
            break
        if title["classification"] != "test":
            continue
        slug = title["slug"]
        variable = experiment_variable_by_slug.get(slug, DEFAULT_ORGANIC_TEST_VARIABLE)
        if variable in _FORBIDDEN_ACTION_VALUES:
            continue
        actions.append({"slug": slug, "kind": "organic_test", "variable": variable})
    return actions


def _stable_action_key(phase: str, now: datetime, actions: list[dict]) -> str:
    selected = sorted((action["slug"], action["variable"]) for action in actions)
    canonical = {
        "phase": phase,
        "date": now.date().isoformat(),
        "selected": selected,
    }
    payload = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def build_growth_plan(
    *, scored_titles: list[dict], active_experiments: list[dict], phase: str, now: datetime,
) -> dict:
    """Build one bounded, idempotent growth plan.

    Pure function: the same scored_titles/active_experiments/phase/now
    always produce the same plan, including the same action_key.
    """
    active_portfolio = _rank_active_portfolio(scored_titles)
    experiment_variable_by_slug = _experiment_variable_by_slug(active_experiments)
    actions = _plan_organic_tests(active_portfolio, experiment_variable_by_slug, phase)

    portfolio = {
        "active": [
            {
                "slug": title["slug"],
                "classification": title["classification"],
                "score": title.get("score", 0),
            }
            for title in active_portfolio
        ],
    }

    return {
        "action_key": _stable_action_key(phase, now, actions),
        "phase": phase,
        "portfolio": portfolio,
        "actions": actions,
    }
