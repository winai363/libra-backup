"""Unit coverage for the immutable, fail-closed TOTAL KDP FREEZE policy."""

from dataclasses import asdict

import pytest

import kdp_freeze


def test_freeze_state_is_machine_readable_and_active():
    assert kdp_freeze.freeze_state() == {
        "active": True,
        "code": "total_kdp_freeze",
        "reason": kdp_freeze.FREEZE_REASON,
        "allowed": ["local_staging", "read_only_reporting"],
    }


@pytest.mark.parametrize(
    "action", ["new_title", "republish", "price", "metadata", "cover", "publish"]
)
def test_every_kdp_mutation_fails_closed(action):
    with pytest.raises(kdp_freeze.KDPFrozenError) as exc:
        kdp_freeze.assert_kdp_mutation_allowed(action)
    assert exc.value.code == "total_kdp_freeze"
    assert exc.value.action == action
    assert asdict(exc.value.decision)["allowed"] is False


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
