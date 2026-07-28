"""Pure-logic content quality gate for the Libra Growth Autopilot. No I/O, no
database access — every function is a deterministic function of its
arguments so it stays fully unit-testable.

Two responsibilities:

1. `build_content_request` builds a GROUNDED generation brief for a piece of
   promotional content (Reddit post, article, etc): the listing's own
   language, a hash of the source excerpt actually pulled from the book (so a
   generator is anchored to real book text, not invention), the campaign's
   target reader, its explicit allow-list of claims, and one canonical CTA.
   Nothing here writes prose — it only fences in what a downstream generator
   is allowed to work from.

2. `validate_growth_content` checks GENERATED content against the listing
   before it is ever allowed out the door. It returns a list of stable error
   codes (never raises, never guesses) so a caller can log/report exactly
   why content was refused. Two gates today:
   - `language_mismatch`: the content's declared language doesn't match the
     listing's language, normalizing both code (`es`) and name (`Spanish`)
     forms (case/whitespace-insensitive) against a fixed alias table. A
     missing/non-string language on either side always fails closed to a
     mismatch. A language string outside the alias table is NOT treated as
     missing — it normalizes to its own lowercased form, so two sides using
     the identical unrecognized string still match; only DIFFERING
     unrecognized strings mismatch. Add new languages to the alias table as
     they come up rather than relying on this fallback.
   - `unsupported_claim`: absolute medical-claim verbs ("cure"/"cures"/
     "cured"/"curing", "heal"/"heals"/"healed"/"healing", "diagnose"/
     "diagnoses"/"diagnosed"/"diagnosing") are refused in ANY listing, since
     there is no niche where an absolute cure claim is a supportable claim.
     A second, health-domain-only word list additionally catches ambiguous
     verbs ("treat"/"treats"/"treated"/"treating", "prevent"/"prevents"/
     "prevented"/"preventing") that are only a medical claim when the
     listing's `risk_domain` is `"health"` — the same word used
     non-medically (e.g. "this chapter treats the history of jazz", or
     "the essay keeps treating the topic sensitively") elsewhere is not
     flagged. All matching is whole-word (`\\b`-bounded), so an inflection
     is caught without false-positiving on an unrelated word that merely
     contains the stem (e.g. "treatment" does not match the "treat" family).
"""
from __future__ import annotations

import hashlib
import re

# Absolute medical-claim verb families (all common inflections), refused
# regardless of the listing's risk_domain. Non-capturing groups, longest
# alternative first, so there's no ambiguity in what a bare stem match means.
_ALWAYS_FLAGGED_CLAIM_PATTERNS = (
    r"cur(?:es|ed|ing|e)",          # cures, cured, curing, cure
    r"heal(?:s|ed|ing)?",          # heals, healed, healing, heal
    r"diagnos(?:es|ed|ing|e)",     # diagnoses, diagnosed, diagnosing, diagnose
)

# Additional verb families that are only a medical claim in context —
# flagged only when the listing's risk_domain is "health".
_HEALTH_DOMAIN_CLAIM_PATTERNS = _ALWAYS_FLAGGED_CLAIM_PATTERNS + (
    r"treat(?:s|ed|ing)?",         # treats, treated, treating, treat
    r"prevent(?:s|ed|ing)?",       # prevents, prevented, preventing, prevent
)

_LANGUAGE_ALIASES = {
    "en": "english", "english": "english",
    "es": "spanish", "spanish": "spanish", "español": "spanish",
    "fr": "french", "french": "french",
    "de": "german", "german": "german",
    "it": "italian", "italian": "italian",
    "pt": "portuguese", "portuguese": "portuguese",
    "nl": "dutch", "dutch": "dutch",
    "ja": "japanese", "japanese": "japanese",
    "zh": "chinese", "chinese": "chinese",
    "ko": "korean", "korean": "korean",
    "ru": "russian", "russian": "russian",
    "pl": "polish", "polish": "polish",
    "sv": "swedish", "swedish": "swedish",
    "th": "thai", "thai": "thai",
}


def _normalize_language(value) -> str | None:
    if not isinstance(value, str):
        return None
    key = value.strip().lower()
    return _LANGUAGE_ALIASES.get(key) or (key or None)


def _has_claim_word(body: str, patterns: tuple[str, ...]) -> bool:
    lowered = body.lower()
    return any(re.search(rf"\b{pattern}\b", lowered) for pattern in patterns)


def build_content_request(listing: dict, source_excerpt: str, campaign: dict) -> dict:
    """Build a grounded content-generation request. Never invents a claim,
    reader, or CTA the campaign didn't explicitly supply — missing campaign
    fields default to empty/None rather than a guessed value."""
    excerpt = source_excerpt if isinstance(source_excerpt, str) else ""
    campaign = campaign if isinstance(campaign, dict) else {}
    return {
        "language": listing.get("language") if isinstance(listing, dict) else None,
        "source_excerpt_hash": hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
        "target_reader": campaign.get("target_reader"),
        "allowed_claims": list(campaign.get("allowed_claims") or []),
        "canonical_cta": campaign.get("cta") or campaign.get("canonical_cta"),
    }


def validate_growth_content(content: dict, listing: dict) -> list[str]:
    """Validate generated content against its listing. Returns stable error
    codes; empty list means the content passed every gate. Fails closed on
    malformed/missing input rather than raising."""
    content = content if isinstance(content, dict) else {}
    listing = listing if isinstance(listing, dict) else {}
    errors: list[str] = []

    content_language = _normalize_language(content.get("language"))
    listing_language = _normalize_language(listing.get("language"))
    if content_language is None or listing_language is None or content_language != listing_language:
        errors.append("language_mismatch")

    body = content.get("body")
    body = body if isinstance(body, str) else ""
    claim_patterns = _HEALTH_DOMAIN_CLAIM_PATTERNS if listing.get("risk_domain") == "health" else _ALWAYS_FLAGGED_CLAIM_PATTERNS
    if _has_claim_word(body, claim_patterns):
        errors.append("unsupported_claim")

    return errors
