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
     forms. Missing/unrecognized language on either side fails closed to a
     mismatch — content can never ship without a confirmed language match.
   - `unsupported_claim`: absolute medical-claim verbs ("cures", "heals",
     "diagnoses", ...) are refused in ANY listing, since there is no niche
     where an absolute cure claim is a supportable claim. A second,
     health-domain-only word list additionally catches ambiguous verbs
     ("treats", "prevents") that are only a medical claim when the listing's
     `risk_domain` is `"health"` — the same word used non-medically (e.g.
     "this chapter treats the history of jazz") elsewhere is not flagged.
"""
from __future__ import annotations

import hashlib
import re

# Absolute medical-claim verbs: unambiguous cure/diagnosis language, refused
# regardless of the listing's risk_domain.
_ALWAYS_FLAGGED_CLAIM_WORDS = {"cures", "cure", "heals", "heal", "diagnoses", "diagnose"}

# Additional verbs that are only a medical claim in context — flagged only
# when the listing's risk_domain is "health".
_HEALTH_DOMAIN_CLAIM_WORDS = _ALWAYS_FLAGGED_CLAIM_WORDS | {"treats", "treat", "prevents", "prevent"}

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


def _has_claim_word(body: str, words: set[str]) -> bool:
    lowered = body.lower()
    return any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in words)


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
    claim_words = _HEALTH_DOMAIN_CLAIM_WORDS if listing.get("risk_domain") == "health" else _ALWAYS_FLAGGED_CLAIM_WORDS
    if _has_claim_word(body, claim_words):
        errors.append("unsupported_claim")

    return errors
