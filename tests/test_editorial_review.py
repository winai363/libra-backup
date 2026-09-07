"""Editorial evidence must agree with its decision before passing a gate."""

import json
from types import SimpleNamespace

import pytest

import editorial_review as editorial


def valid_report():
    return {
        "scores": {name: 8 for name in editorial.REQUIRED_SCORES},
        "critical_issues": [],
        "recommended_action": "pass",
        "fact_checks": [
            {"claim": f"Claim {index}", "result": "supported",
             "source_url": f"https://example.org/source/{index}"}
            for index in range(5)
        ],
    }


@pytest.mark.parametrize("change", [
    {"recommended_action": "revise"},
    {"fact_checks": [{"claim": "Claim", "result": "uncertain", "source_url": "not a URL"}]},
])
def test_review_book_cannot_override_missing_evidence(tmp_path, monkeypatch, change):
    book = tmp_path / "guide"
    book.mkdir()
    (book / "listing.json").write_text(json.dumps({"title": "Watercolour guide"}))
    (book / "ebook.md").write_text("A complete guide.")
    (book / "content-research.md").write_text("Sources")
    payload = {**valid_report(), **change}
    response = SimpleNamespace(output_text=json.dumps(payload), output=[], usage=None)
    monkeypatch.setattr(editorial, "OpenAI", lambda **kwargs: SimpleNamespace(
        responses=SimpleNamespace(create=lambda **kwargs: response)))
    monkeypatch.setattr(editorial, "load_dotenv", lambda *args: None)

    result = editorial.review_book("guide", root=tmp_path)

    assert result["passed"] is False
    assert result["evidence_failures"]
    assert result["source_sha256"] == editorial.editorial_source_hashes(book)


def test_complete_supported_report_passes_without_trusting_stored_flag():
    report = valid_report()
    report["passed"] = False
    assert editorial.validate_editorial_report(report)["passed"] is True


@pytest.mark.parametrize("score", [True, "8", None, float("nan"), float("inf"), 11, 6])
def test_invalid_scores_fail_closed(score):
    report = valid_report()
    report["scores"]["reader_value"] = score
    result = editorial.validate_editorial_report(report)
    assert result["passed"] is False
    assert result["score_failures"]


@pytest.mark.parametrize("change", [
    {"scores": None}, {"critical_issues": None}, {"critical_issues": ""},
    {"critical_issues": ["Unsafe advice"]}, {"recommended_action": None},
    {"fact_checks": None}, {"fact_checks": [None] * 5},
    {"fact_checks": valid_report()["fact_checks"][:4]},
])
def test_malformed_or_incomplete_report_fails_closed(change):
    assert editorial.validate_editorial_report({**valid_report(), **change})["passed"] is False


@pytest.mark.parametrize("field,value", [
    ("result", "uncertain"), ("result", "contradicted"), ("result", "unknown"),
    ("claim", ""), ("source_url", "source unavailable"),
    ("source_url", "file:///tmp/source"), ("source_url", "https://"),
    ("source_url", "https://bad host/source"), ("source_url", "https://[broken"),
])
def test_each_fact_check_needs_supported_claim_and_source_url(field, value):
    report = valid_report()
    report["fact_checks"][0][field] = value
    result = editorial.validate_editorial_report(report)
    assert result["passed"] is False
    assert result["evidence_failures"]


def test_fiction_exempts_minimum_checks_but_not_contradictions():
    report = valid_report()
    report["fact_checks"] = []
    assert editorial.validate_editorial_report(report, fiction=True)["passed"] is True
    report["fact_checks"] = [{"claim": "Historical event", "result": "contradicted",
                              "source_url": "https://example.org/history"}]
    assert editorial.validate_editorial_report(report, fiction=True)["passed"] is False


@pytest.mark.parametrize("report", [None, [], "passed"])
def test_invalid_root_is_a_failure_not_an_exception(report):
    assert editorial.validate_editorial_report(report)["passed"] is False


@pytest.mark.parametrize("listing,expected", [
    ({"categories": ["Fiction / Fantasy"]}, True),
    ({"type": "fiction"}, True),
    ({"genre": "romance"}, True),
    ({"categories": ["Non-fiction / Science"]}, False),
    ({"type": "nonfiction", "categories": ["Fiction / Fantasy"]}, False),
    ({"title": "How to Write a Novel", "keywords": ["fiction", "fantasy"]}, False),
    ({"description": "Fiction and romance techniques"}, False),
    ({"categories": ["Science Fiction"]}, True),
    ({"categories": ["Literary Criticism / Science Fiction & Fantasy"]}, False),
    ({"categories": ["Books > Literature & Fiction > Genre Fiction"]}, True),
    ({"categories": ["Education > Writing Fiction"]}, False),
])
def test_fiction_exemption_uses_declared_classification(listing, expected):
    assert editorial.is_fiction_listing(listing) is expected


def test_editorial_evidence_requires_exact_current_source_hashes(tmp_path):
    (tmp_path / "ebook.md").write_bytes(b"Original manuscript")
    (tmp_path / "listing.json").write_bytes(b'{"title":"Guide"}')
    expected = editorial.editorial_source_hashes(tmp_path)
    assert set(expected) == {"ebook.md", "listing.json"}
    report = valid_report()
    assert not editorial.validate_editorial_report(report, expected_sources=expected)["passed"]
    report["source_sha256"] = expected
    assert editorial.validate_editorial_report(report, expected_sources=expected)["passed"]
    (tmp_path / "ebook.md").write_bytes(b"Changed manuscript")
    current = editorial.editorial_source_hashes(tmp_path)
    assert not editorial.validate_editorial_report(report, expected_sources=current)["passed"]


def test_review_hashes_inputs_before_provider_call(tmp_path, monkeypatch):
    book = tmp_path / "guide"
    book.mkdir()
    (book / "listing.json").write_text('{"title":"Guide"}')
    (book / "ebook.md").write_text("Original manuscript")
    (book / "content-research.md").write_text("Sources")
    expected = editorial.editorial_source_hashes(book)

    def create(**kwargs):
        (book / "ebook.md").write_text("Modified during review")
        return SimpleNamespace(output_text=json.dumps(valid_report()), output=[], usage=None)

    monkeypatch.setattr(editorial, "OpenAI", lambda **kwargs: SimpleNamespace(
        responses=SimpleNamespace(create=create)))
    monkeypatch.setattr(editorial, "load_dotenv", lambda *args: None)
    report = editorial.review_book("guide", root=tmp_path)
    assert report["source_sha256"] == expected
    assert report["source_sha256"] != editorial.editorial_source_hashes(book)
