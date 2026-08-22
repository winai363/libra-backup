"""TOTAL KDP FREEZE — executable source of truth.

The account carries four accumulated content blocks; a fifth risks losing all
38 live titles. So the default is: every KDP mutation fails closed.

The single exception is a *named new book* that Bui has personally reviewed and
authorised. Adding a slug to APPROVED_UPLOADS is a deliberate, reviewed source
change — and even then it only permits putting that ONE new book on the shelf.
Nothing here can ever touch an existing listing: no republish, no price change,
no metadata edit, no cover swap. Those are what triggered the last two blocks.

Do not add an environment-variable override, date expiry, approval token, or
force flag.
"""

from dataclasses import dataclass

FREEZE_CODE = "total_kdp_freeze"
FREEZE_REASON = (
    "TOTAL KDP FREEZE is active after four account content blocks; "
    "all KDP mutations are disabled."
)

# slug -> why it was authorised. One book at a time; remove it once published.
APPROVED_UPLOADS = {
    "aquarelle-botanique-debutants-fr": (
        "authorised by Bui on 2026-08-22 after reviewing the staged book "
        "(73 pages, 12 instructional images, editorial 8/8)"
    ),
}

# The only actions an approved slug unlocks — enough to publish a NEW title and
# nothing more. Deliberately excludes republish/price/metadata/cover.
NEW_TITLE_ACTIONS = frozenset({
    "new_title",
    "queue_publish",
    "publish",
    "writer_live_output",
})


@dataclass(frozen=True)
class FreezeDecision:
    allowed: bool
    code: str
    reason: str
    action: str


class KDPFrozenError(RuntimeError):
    def __init__(self, action: str, slug: str | None = None):
        self.code = FREEZE_CODE
        self.action = action
        self.slug = slug
        self.decision = FreezeDecision(False, FREEZE_CODE, FREEZE_REASON, action)
        target = f" [{slug}]" if slug else ""
        super().__init__(f"{FREEZE_CODE}: {action}{target}: {FREEZE_REASON}")


def freeze_state() -> dict:
    return {
        "active": True,
        "code": FREEZE_CODE,
        "reason": FREEZE_REASON,
        "allowed": ["local_staging", "read_only_reporting"],
        "approved_uploads": sorted(APPROVED_UPLOADS),
        "approved_actions": sorted(NEW_TITLE_ACTIONS),
    }


def upload_approved(slug: str | None) -> bool:
    return bool(slug) and slug in APPROVED_UPLOADS


def assert_kdp_mutation_allowed(action: str, slug: str | None = None) -> None:
    if action in NEW_TITLE_ACTIONS and upload_approved(slug):
        return
    raise KDPFrozenError(action, slug)
