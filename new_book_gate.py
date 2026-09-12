"""New-book gate — autonomous new-book production is off.

Why: 64 titles exist, 38 ebooks are live, 30 of those have earned nothing, and
five titles were blocked by Amazon. The bottleneck is acquisition, not supply, so
book 65 may not be started on a hunch. This gate makes that a runtime rule rather
than a note in a document.

A concept may enter research only when one of two doors is open:
  A) an existing-book experiment showed meaningful buyer demand, or
  B) independent research found a clearly stronger opportunity.

And before a full book is written, the concept must carry all six pieces of
evidence in REQUIRED_EVIDENCE. Missing one is a refusal, not a warning.

Fail closed: a missing file, unreadable JSON, or a concept that is not listed is
refused. There is no environment override. Opening the gate is an owner edit to
data/new_book_gate.json, and it names one concept at a time.

This gate is about *producing* books. It is independent of the KDP freeze, which
blocks publishing and stays in force regardless of what this file says.
"""
from __future__ import annotations

import json
from pathlib import Path

GATE_FILE = Path(__file__).parent / "data" / "new_book_gate.json"

REQUIRED_EVIDENCE = (
    "buyer_evidence",        # who buys, observed, not imagined
    "differentiated_promise",
    "competitive_gap",
    "acquisition_plan",      # how a reader finds it, on a channel we may use
    "compliance_review",     # niche, claims, rights, AI disclosure
    "expected_economics",    # price, royalty, and what it must sell to matter
)

OPENING_DOORS = ("existing_book_demand_demonstrated", "stronger_opportunity_found")


class NewBookGateClosed(RuntimeError):
    """Raised when new-book work is attempted while the gate is closed."""


def _load(path: Path | None = None) -> dict:
    try:
        data = json.loads(Path(path or GATE_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def gate_state(*, path: Path | None = None) -> dict:
    gate = _load(path)
    doors = {door: gate.get(door) is True for door in OPENING_DOORS}
    return {
        "autonomous_new_books": gate.get("autonomous_new_books") is True,
        "doors": doors,
        "any_door_open": any(doors.values()),
        "concepts": sorted((gate.get("concepts") or {}).keys())
        if isinstance(gate.get("concepts"), dict) else [],
    }


def research_allowed(*, path: Path | None = None) -> tuple:
    """(allowed, reason) for letting a new concept into research at all."""
    state = gate_state(path=path)
    if not state["any_door_open"]:
        return False, ("new-book gate closed: neither demonstrated demand from an existing-book "
                       "experiment nor a clearly stronger researched opportunity is on record")
    open_doors = [d for d, is_open in state["doors"].items() if is_open]
    return True, f"door open: {', '.join(open_doors)}"


def build_allowed(concept: str, *, path: Path | None = None) -> tuple:
    """(allowed, reason) for writing a full book for `concept`."""
    allowed, reason = research_allowed(path=path)
    if not allowed:
        return False, reason
    concepts = _load(path).get("concepts")
    record = concepts.get(concept) if isinstance(concepts, dict) else None
    if not isinstance(record, dict):
        return False, f"concept {concept!r} is not on record in the gate file"
    missing = [key for key in REQUIRED_EVIDENCE
               if not str(record.get(key) or "").strip()]
    if missing:
        return False, f"concept {concept!r} is missing evidence: {', '.join(missing)}"
    return True, f"concept {concept!r} carries all six pieces of evidence"


def assert_new_book_allowed(concept: str | None = None, *, path: Path | None = None) -> None:
    """Raise NewBookGateClosed unless autonomous production is on AND (for a named
    concept) its evidence is complete. Call this before spending a token or a
    cent on a new title."""
    state = gate_state(path=path)
    if not state["autonomous_new_books"]:
        raise NewBookGateClosed(
            "autonomous new-book production is disabled (data/new_book_gate.json). "
            "Prove demand on the existing catalogue first, or record a stronger "
            "researched opportunity; then the owner opens this gate.")
    allowed, reason = build_allowed(concept, path=path) if concept else research_allowed(path=path)
    if not allowed:
        raise NewBookGateClosed(reason)
