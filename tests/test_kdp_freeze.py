"""Unit coverage for the fail-closed TOTAL KDP FREEZE policy.

The freeze permits exactly one thing: publishing a NEW book whose slug Bui has
personally authorised. Every existing listing stays untouchable — the last two
account blocks came from a republish and a price change, not from new titles.
"""

from dataclasses import asdict

import pytest

import kdp_freeze

# Normally empty (nothing is authorised), so the "approved" cases run against a
# temporary entry rather than requiring a real live authorisation to exist.
APPROVED = "a-book-bui-authorised"


@pytest.fixture
def approved(monkeypatch):
    monkeypatch.setattr(kdp_freeze, "APPROVED_UPLOADS", {APPROVED: "test fixture"})
    return APPROVED


def test_nothing_is_authorised_by_default():
    """The steady state is a closed freeze — an entry here is the exception."""
    assert kdp_freeze.APPROVED_UPLOADS == {}


def test_freeze_state_is_machine_readable_and_active():
    state = kdp_freeze.freeze_state()
    assert state["active"] is True
    assert state["code"] == "total_kdp_freeze"
    assert state["reason"] == kdp_freeze.FREEZE_REASON
    assert state["allowed"] == ["local_staging", "read_only_reporting"]
    assert state["approved_uploads"] == sorted(kdp_freeze.APPROVED_UPLOADS)


@pytest.mark.parametrize("action", ["new_title", "republish", "price", "metadata", "cover", "publish"])
def test_every_kdp_mutation_fails_closed_without_an_approved_slug(action):
    with pytest.raises(kdp_freeze.KDPFrozenError) as exc:
        kdp_freeze.assert_kdp_mutation_allowed(action)
    assert exc.value.code == "total_kdp_freeze"
    assert exc.value.action == action
    assert asdict(exc.value.decision)["allowed"] is False


@pytest.mark.parametrize("action", ["republish", "price", "metadata", "cover", "update_ebook_content"])
def test_an_approved_slug_still_cannot_touch_an_existing_listing(action, approved):
    """This is the whole point: approval publishes a new book, nothing else."""
    with pytest.raises(kdp_freeze.KDPFrozenError):
        kdp_freeze.assert_kdp_mutation_allowed(action, APPROVED)


@pytest.mark.parametrize("action", sorted(kdp_freeze.NEW_TITLE_ACTIONS))
def test_approved_slug_may_publish_a_new_title(action, approved):
    kdp_freeze.assert_kdp_mutation_allowed(action, APPROVED)


def test_an_unapproved_slug_is_refused_for_every_action(approved):
    for action in sorted(kdp_freeze.NEW_TITLE_ACTIONS):
        with pytest.raises(kdp_freeze.KDPFrozenError):
            kdp_freeze.assert_kdp_mutation_allowed(action, "some-other-book")


def test_no_live_catalogue_slug_is_ever_on_the_approved_list():
    """A guard against approving an existing book by mistake."""
    import json
    from pathlib import Path

    live = set()
    for listing_file in Path("/root/kdp").glob("*/listing.json"):
        try:
            data = json.loads(listing_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("asin"):
            live.add(listing_file.parent.name)
    assert not (set(kdp_freeze.APPROVED_UPLOADS) & live)


def test_freeze_has_no_override_switch():
    """No env override, expiry, force flag, or approval token may exist in code."""
    import ast
    import pathlib

    tree = ast.parse(pathlib.Path(kdp_freeze.__file__).read_text(encoding="utf-8"))
    names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    names |= {node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported |= {
        node.module.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert imported == {"dataclasses"}
    assert not names & {"environ", "getenv", "getattr", "eval", "exec"}
