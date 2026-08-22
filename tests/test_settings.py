"""Fail-closed commerce configuration."""

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from settings import CommerceConfigError, CommerceSettings, load_env_file

VALID_ENV = {
    "LIBRA_COMMERCE_MODE": "test",
    "STRIPE_WEBHOOK_SECRET_TEST": "whsec_test_fixture",
    "STRIPE_EXPECTED_ACCOUNT_TEST": "acct_test_fixture",
    "PAYHIP_WEBHOOK_TOKEN_TEST": "p" * 48,
    "PAYHIP_ALLOWED_HOSTS": "payhip.com,www.payhip.com",
    "PAYHIP_PRODUCT_IDS_TEST": "kit-fr-test",
}


def test_commerce_settings_require_explicit_test_mode_and_secrets():
    with pytest.raises(CommerceConfigError, match="commerce_mode"):
        CommerceSettings.from_sources({})

    settings = CommerceSettings.from_sources(VALID_ENV)
    assert settings.mode == "test"
    assert settings.max_webhook_bytes == 262144
    assert settings.payhip_allowed_hosts == frozenset({"payhip.com", "www.payhip.com"})
    assert settings.payhip_product_ids == frozenset({"kit-fr-test"})
    assert "whsec_test_fixture" not in repr(settings)
    assert "whsec_test_fixture" not in str(settings)


@pytest.mark.parametrize(
    "missing", ["STRIPE_WEBHOOK_SECRET_TEST", "STRIPE_EXPECTED_ACCOUNT_TEST", "PAYHIP_WEBHOOK_TOKEN_TEST"]
)
def test_missing_secret_fails_closed(missing):
    env = {k: v for k, v in VALID_ENV.items() if k != missing}
    with pytest.raises(CommerceConfigError, match="missing"):
        CommerceSettings.from_sources(env)


@pytest.mark.parametrize("mode", ["Test", "", "production", "sandbox"])
def test_only_exact_lowercase_mode_names_are_accepted(mode):
    with pytest.raises(CommerceConfigError, match="commerce_mode"):
        CommerceSettings.from_sources({**VALID_ENV, "LIBRA_COMMERCE_MODE": mode})


def test_short_payhip_token_is_refused():
    with pytest.raises(CommerceConfigError, match="payhip_webhook_token_too_short"):
        CommerceSettings.from_sources({**VALID_ENV, "PAYHIP_WEBHOOK_TOKEN_TEST": "short"})


def test_readiness_reports_reasons_without_secret_values():
    ready = CommerceSettings.readiness(VALID_ENV)
    assert ready["ready"] is True
    assert ready["mode"] == "test"
    assert ready["reasons"] == []

    broken = CommerceSettings.readiness({"LIBRA_COMMERCE_MODE": "test"})
    assert broken["ready"] is False
    assert broken["reasons"]
    serialised = str(broken)
    for secret in VALID_ENV.values():
        assert secret not in serialised or secret == "test"


def test_env_file_parser_does_not_write_process_environment(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    path.write_text(
        "# comment\nLIBRA_COMMERCE_MODE=test\n\nEMPTY=\nQUOTED=\"quoted value\"\nbad line\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("LIBRA_COMMERCE_MODE", raising=False)

    parsed = load_env_file(path)

    assert parsed["LIBRA_COMMERCE_MODE"] == "test"
    assert parsed["QUOTED"] == "quoted value"
    assert parsed["EMPTY"] == ""
    assert "bad line" not in parsed
    assert "LIBRA_COMMERCE_MODE" not in os.environ


def test_env_file_missing_returns_empty_mapping(tmp_path):
    assert load_env_file(tmp_path / "nope.env") == {}


# ── live mode (authorised by Bui 2026-08-22) ─────────────────────────────────
# Payhip has no test mode: every real sale is a live Stripe event. Live is now
# a permitted value — but it is never a default, and never silently inferred.

LIVE_ENV = {
    "LIBRA_COMMERCE_MODE": "live",
    "STRIPE_WEBHOOK_SECRET_LIVE": "whsec_live_fixture",
    "STRIPE_EXPECTED_ACCOUNT_LIVE": "acct_live_fixture",
    "PAYHIP_WEBHOOK_TOKEN_LIVE": "L" * 48,
    "PAYHIP_ALLOWED_HOSTS": "payhip.com,www.payhip.com",
    "PAYHIP_PRODUCT_IDS_LIVE": "GDRi5",
}


def test_live_mode_reads_its_own_keys_never_the_test_ones():
    settings = CommerceSettings.from_sources(LIVE_ENV)

    assert settings.mode == "live"
    assert settings.stripe_webhook_secret == "whsec_live_fixture"
    assert settings.stripe_expected_account == "acct_live_fixture"
    assert settings.payhip_product_ids == frozenset({"GDRi5"})


def test_a_test_secret_can_never_satisfy_live_mode():
    """Mixing the two would let a test-mode forgery approve real revenue."""
    mixed = {**LIVE_ENV}
    del mixed["STRIPE_WEBHOOK_SECRET_LIVE"]
    mixed["STRIPE_WEBHOOK_SECRET_TEST"] = "whsec_test_fixture"

    with pytest.raises(CommerceConfigError, match="missing"):
        CommerceSettings.from_sources(mixed)


@pytest.mark.parametrize("mode", ["Live", "LIVE", "production", "prod", ""])
def test_only_exact_lowercase_modes_are_accepted(mode):
    with pytest.raises(CommerceConfigError, match="commerce_mode"):
        CommerceSettings.from_sources({**LIVE_ENV, "LIBRA_COMMERCE_MODE": mode})


def test_expected_livemode_flag_matches_the_configured_mode():
    assert CommerceSettings.from_sources(VALID_ENV).expect_livemode is False
    assert CommerceSettings.from_sources(LIVE_ENV).expect_livemode is True


def test_readiness_reports_the_mode_it_is_actually_in():
    assert CommerceSettings.readiness(LIVE_ENV)["mode"] == "live"
    assert CommerceSettings.readiness(LIVE_ENV)["ready"] is True
