import hashlib

from growth_content import build_content_request, validate_growth_content


def test_content_rejects_wrong_language_and_unverified_health_claim():
    errors = validate_growth_content(
        {"language": "en", "body": "This treatment cures ADHD."},
        {"language": "Spanish", "risk_domain": "health"},
    )
    assert "language_mismatch" in errors
    assert "unsupported_claim" in errors


def test_content_accepts_matching_language_and_clean_claims():
    errors = validate_growth_content(
        {"language": "es", "body": "Este libro explica ejercicios de respiracion."},
        {"language": "Spanish", "risk_domain": "health"},
    )
    assert errors == []


def test_language_mismatch_normalizes_code_vs_name_both_directions():
    # listing uses a bare ISO code, content uses the English name — same
    # language, should NOT be flagged.
    errors = validate_growth_content(
        {"language": "Spanish", "body": "Contenido limpio."},
        {"language": "es"},
    )
    assert "language_mismatch" not in errors


def test_language_mismatch_when_language_missing_fails_closed():
    errors = validate_growth_content(
        {"body": "No language declared."},
        {"language": "en"},
    )
    assert "language_mismatch" in errors


def test_unsupported_claim_flags_absolute_verbs_regardless_of_risk_domain():
    # "heals" is an absolute medical claim verb — flagged even when the
    # listing's risk_domain is NOT health.
    errors = validate_growth_content(
        {"language": "en", "body": "This routine heals your back pain."},
        {"language": "en", "risk_domain": "fitness"},
    )
    assert "unsupported_claim" in errors


def test_unsupported_claim_allows_non_medical_use_of_ambiguous_word_outside_health():
    # "treats" is ambiguous (also means "covers a topic") — only flagged in
    # a health risk_domain, not for an unrelated niche.
    errors = validate_growth_content(
        {"language": "en", "body": "Chapter three treats the history of jazz."},
        {"language": "en", "risk_domain": "music"},
    )
    assert "unsupported_claim" not in errors


def test_unsupported_claim_flags_ambiguous_word_when_risk_domain_is_health():
    errors = validate_growth_content(
        {"language": "en", "body": "This plan treats chronic fatigue."},
        {"language": "en", "risk_domain": "health"},
    )
    assert "unsupported_claim" in errors


def test_unsupported_claim_flags_curing_inflection_regardless_of_domain():
    # "curing" (an inflection of the always-flagged "cure" family) must be
    # caught even in a non-health risk_domain.
    errors = validate_growth_content(
        {"language": "en", "body": "There is no curing this disease overnight."},
        {"language": "en", "risk_domain": "fitness"},
    )
    assert "unsupported_claim" in errors


def test_unsupported_claim_flags_healing_inflection_in_health_domain():
    errors = validate_growth_content(
        {"language": "en", "body": "This tea is healing your anxiety."},
        {"language": "en", "risk_domain": "health"},
    )
    assert "unsupported_claim" in errors


def test_unsupported_claim_flags_diagnosing_inflection_regardless_of_domain():
    errors = validate_growth_content(
        {"language": "en", "body": "This quiz is diagnosing your learning style."},
        {"language": "en", "risk_domain": "education"},
    )
    assert "unsupported_claim" in errors


def test_unsupported_claim_flags_prevented_inflection_in_health_domain():
    errors = validate_growth_content(
        {"language": "en", "body": "This supplement prevented her migraines."},
        {"language": "en", "risk_domain": "health"},
    )
    assert "unsupported_claim" in errors


def test_unsupported_claim_allows_treating_inflection_non_medically_outside_health():
    # "treating" a topic, not a patient — same non-medical-use exemption as
    # the bare "treats" case, extended to the inflected form.
    errors = validate_growth_content(
        {"language": "en", "body": "The essay keeps treating the topic of jazz history sensitively."},
        {"language": "en", "risk_domain": "music"},
    )
    assert "unsupported_claim" not in errors


def test_unsupported_claim_does_not_false_positive_on_unrelated_word_treatment():
    # "treatment" is a distinct word from the "treat" family stem match —
    # must not trip the health-domain gate on its own.
    errors = validate_growth_content(
        {"language": "en", "body": "This book explores the treatment of grief in literature."},
        {"language": "en", "risk_domain": "health"},
    )
    assert "unsupported_claim" not in errors


def test_build_content_request_is_grounded_in_listing_and_campaign():
    listing = {"language": "es", "slug": "book-a", "risk_domain": "health"}
    excerpt = "Excerpt text from the book body."
    campaign = {
        "target_reader": "new parents",
        "allowed_claims": ["may support relaxation"],
        "cta": "Read more on Amazon",
    }
    request = build_content_request(listing, excerpt, campaign)
    assert request["language"] == "es"
    assert request["target_reader"] == "new parents"
    assert request["allowed_claims"] == ["may support relaxation"]
    assert request["canonical_cta"] == "Read more on Amazon"
    assert request["source_excerpt_hash"] == hashlib.sha256(excerpt.encode("utf-8")).hexdigest()


def test_build_content_request_hash_changes_with_excerpt():
    listing = {"language": "en"}
    campaign = {"target_reader": "x", "allowed_claims": [], "cta": "y"}
    a = build_content_request(listing, "first excerpt", campaign)
    b = build_content_request(listing, "second excerpt", campaign)
    assert a["source_excerpt_hash"] != b["source_excerpt_hash"]


def test_build_content_request_missing_campaign_fields_default_safely():
    request = build_content_request({"language": "en"}, "excerpt", {})
    assert request["target_reader"] is None
    assert request["allowed_claims"] == []
    assert request["canonical_cta"] is None
