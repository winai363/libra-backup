"""Tests for new_book_gate.py — autonomous new-book production is off.

The gate must fail closed on every shape of missing or broken input, and an open
gate must still demand the six pieces of evidence before a book is written.
It must never be confused with the KDP freeze, which blocks publishing.
"""
import json

import pytest

from new_book_gate import (NewBookGateClosed, REQUIRED_EVIDENCE, assert_new_book_allowed,
                           build_allowed, gate_state, research_allowed)


def evidence(**overrides) -> dict:
    record = {key: f"recorded {key}" for key in REQUIRED_EVIDENCE}
    record.update(overrides)
    return record


def gate(tmp_path, **overrides):
    data = {"autonomous_new_books": False, "existing_book_demand_demonstrated": False,
            "stronger_opportunity_found": False, "concepts": {}}
    data.update(overrides)
    path = tmp_path / "new_book_gate.json"
    path.write_text(json.dumps(data))
    return path


# ── fail closed ─────────────────────────────────────────────────────────────

def test_missing_gate_file_refuses(tmp_path):
    with pytest.raises(NewBookGateClosed):
        assert_new_book_allowed(path=tmp_path / "absent.json")


def test_broken_gate_file_refuses(tmp_path):
    path = tmp_path / "new_book_gate.json"
    path.write_text("{not json")

    with pytest.raises(NewBookGateClosed):
        assert_new_book_allowed(path=path)


def test_the_shipped_gate_is_closed():
    """The repository must never ship an open new-book gate."""
    with pytest.raises(NewBookGateClosed):
        assert_new_book_allowed()
    assert gate_state()["autonomous_new_books"] is False
    assert gate_state()["any_door_open"] is False


def test_production_flag_alone_is_not_enough(tmp_path):
    path = gate(tmp_path, autonomous_new_books=True)

    with pytest.raises(NewBookGateClosed) as error:
        assert_new_book_allowed(path=path)

    assert "neither demonstrated demand" in str(error.value)


def test_a_door_alone_is_not_enough(tmp_path):
    path = gate(tmp_path, existing_book_demand_demonstrated=True)

    with pytest.raises(NewBookGateClosed) as error:
        assert_new_book_allowed(path=path)

    assert "disabled" in str(error.value)


# ── research door ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("door", ["existing_book_demand_demonstrated",
                                  "stronger_opportunity_found"])
def test_either_door_opens_research(tmp_path, door):
    path = gate(tmp_path, autonomous_new_books=True, **{door: True})

    allowed, reason = research_allowed(path=path)

    assert allowed is True
    assert door in reason
    assert_new_book_allowed(path=path)  # research, no concept named


# ── build evidence ──────────────────────────────────────────────────────────

def test_full_evidence_allows_a_build(tmp_path):
    path = gate(tmp_path, autonomous_new_books=True, stronger_opportunity_found=True,
                concepts={"cu-tep-listening": evidence()})

    allowed, reason = build_allowed("cu-tep-listening", path=path)

    assert allowed is True
    assert "all six" in reason
    assert_new_book_allowed("cu-tep-listening", path=path)


@pytest.mark.parametrize("missing", REQUIRED_EVIDENCE)
def test_one_missing_piece_of_evidence_refuses_the_build(tmp_path, missing):
    path = gate(tmp_path, autonomous_new_books=True, stronger_opportunity_found=True,
                concepts={"concept-a": evidence(**{missing: ""})})

    allowed, reason = build_allowed("concept-a", path=path)

    assert allowed is False
    assert missing in reason
    with pytest.raises(NewBookGateClosed):
        assert_new_book_allowed("concept-a", path=path)


def test_unlisted_concept_refuses_the_build(tmp_path):
    path = gate(tmp_path, autonomous_new_books=True, stronger_opportunity_found=True,
                concepts={"concept-a": evidence()})

    allowed, reason = build_allowed("concept-b", path=path)

    assert allowed is False
    assert "not on record" in reason


def test_whitespace_is_not_evidence(tmp_path):
    path = gate(tmp_path, autonomous_new_books=True, stronger_opportunity_found=True,
                concepts={"concept-a": evidence(buyer_evidence="   ")})

    assert build_allowed("concept-a", path=path)[0] is False


def test_gate_state_lists_what_an_operator_needs_to_see(tmp_path):
    path = gate(tmp_path, autonomous_new_books=True, existing_book_demand_demonstrated=True,
                concepts={"concept-a": evidence()})

    state = gate_state(path=path)

    assert state == {
        "autonomous_new_books": True,
        "doors": {"existing_book_demand_demonstrated": True, "stronger_opportunity_found": False},
        "any_door_open": True,
        "concepts": ["concept-a"],
    }


# ── the creation route refuses while the gate is closed ─────────────────────

def test_create_book_route_is_refused_with_423(monkeypatch, tmp_path):
    """The cheapest place to stop a new title is before it is registered."""
    from fastapi.testclient import TestClient

    import app as libra_app

    monkeypatch.setattr(libra_app, "TOKEN", "test-token")
    client = TestClient(libra_app.app)

    response = client.post("/api/books", json={"slug": "a-new-book"},
                           cookies={"libra_token": "test-token"})

    assert response.status_code == 423
    assert "new-book production is disabled" in response.json()["detail"]
