"""Payhip has no product API, so products are managed through a browser.

These tests cover the part that can be proven offline: the guards, the
selector contract, the dry run, and the before/after evidence rule. The live
DOM can only be confirmed against a real logged-in session.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import payhip_admin


def test_credentials_are_required_and_never_defaulted(monkeypatch):
    monkeypatch.delenv("PAYHIP_EMAIL", raising=False)
    monkeypatch.delenv("PAYHIP_PASSWORD", raising=False)

    with pytest.raises(payhip_admin.PayhipAdminError, match="credentials_missing"):
        payhip_admin.load_credentials({})


def test_selectors_cover_every_step_we_drive():
    required = {
        "login_email", "login_password", "login_submit", "logged_in_marker",
        "product_new", "product_name", "product_price", "product_description",
        "product_file_input", "product_cover_input", "product_save", "product_saved_marker",
        "product_list_row", "settings_webhook_url", "settings_webhook_save",
    }
    assert required <= set(payhip_admin.SELECTORS)
    assert all(isinstance(v, str) and v for v in payhip_admin.SELECTORS.values())


def test_dry_run_plans_every_browser_action_without_a_browser(tmp_path):
    spec = {
        "slug": "aquarelle-botanique-debutants-fr",
        "title": "Aquarelle Botanique pour Débutants",
        "description": "desc",
        "price_display": "12.90",
        "currency": "EUR",
        "cover": str(tmp_path / "cover.jpg"),
    }
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(b"PK\x03\x04")

    plan = payhip_admin.plan_product_upsert(spec, bundle)

    kinds = [step["action"] for step in plan]
    assert kinds[:2] == ["open", "fill"]
    assert "upload_file" in kinds and "upload_cover" in kinds
    assert kinds[-2:] == ["click_save", "verify_listed"]
    assert all("payhip.com" in step.get("url", "payhip.com") for step in plan)


def test_evidence_requires_before_and_after_states():
    with pytest.raises(payhip_admin.PayhipAdminError, match="evidence"):
        payhip_admin.build_evidence(action="create_product", before=None, after={"listed": True})

    evidence = payhip_admin.build_evidence(
        action="create_product",
        before={"listed": False},
        after={"listed": True, "product_url": "https://payhip.com/b/abc12"},
        screenshots=["/tmp/x.png"],
    )
    assert evidence["verified_state_change"]["after"]["listed"] is True
    assert evidence["external_url"] == "https://payhip.com/b/abc12"


def test_a_product_is_not_executed_unless_the_after_state_shows_it(tmp_path):
    """A click that returns no visible product is manual_required, never success."""
    outcome = payhip_admin.classify_outcome(
        before={"listed": False}, after={"listed": False}
    )
    assert outcome == "manual_required"
    assert payhip_admin.classify_outcome(
        before={"listed": False}, after={"listed": True, "product_url": "https://payhip.com/b/x"}
    ) == "executed"


def test_session_file_lives_in_libra_and_is_private(tmp_path, monkeypatch):
    monkeypatch.setattr(payhip_admin, "SESSION_FILE", tmp_path / "payhip_session.json")
    payhip_admin.save_session({"cookies": []})

    mode = oct((tmp_path / "payhip_session.json").stat().st_mode & 0o777)
    assert mode == "0o600"


def test_webhook_url_plan_uses_the_secret_path_and_https():
    plan = payhip_admin.plan_webhook_setup("https://example.test/libra/api/webhooks/payhip/" + "p" * 48)
    assert plan[-1]["action"] == "click_save"
    with pytest.raises(payhip_admin.PayhipAdminError, match="https"):
        payhip_admin.plan_webhook_setup("http://example.test/hook")


def test_module_never_imports_kdp_code():
    source = (Path(__file__).resolve().parent.parent / "payhip_admin.py").read_text()
    for forbidden in ("kdp_upload", "kdp_session", "kdp_login", "kdp_action_executor"):
        assert forbidden not in source
