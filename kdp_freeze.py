"""TOTAL KDP FREEZE — executable source of truth.

Lifting the freeze requires a reviewed source change. Do not add an
environment-variable override, date expiry, approval token, or force flag.
"""

from dataclasses import dataclass

FREEZE_CODE = "total_kdp_freeze"
FREEZE_REASON = (
    "TOTAL KDP FREEZE is active after four account content blocks; "
    "all KDP mutations are disabled."
)


@dataclass(frozen=True)
class FreezeDecision:
    allowed: bool
    code: str
    reason: str
    action: str


class KDPFrozenError(RuntimeError):
    def __init__(self, action: str):
        self.code = FREEZE_CODE
        self.action = action
        self.decision = FreezeDecision(False, FREEZE_CODE, FREEZE_REASON, action)
        super().__init__(f"{FREEZE_CODE}: {action}: {FREEZE_REASON}")


def freeze_state() -> dict:
    return {
        "active": True,
        "code": FREEZE_CODE,
        "reason": FREEZE_REASON,
        "allowed": ["local_staging", "read_only_reporting"],
    }


def assert_kdp_mutation_allowed(action: str) -> None:
    raise KDPFrozenError(action)
