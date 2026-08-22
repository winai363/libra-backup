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


@pytest.mark.parametrize("mode", ["live", "Test", "", "production"])
def test_only_lowercase_test_mode_is_accepted(mode):
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
