"""One source of truth for Libra's `.env` and commerce configuration.

Commerce is TEST MODE ONLY. Anything missing, malformed, live-mode, or from an
unexpected account fails closed — no default is ever guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

MAX_WEBHOOK_BYTES = 256 * 1024
MIN_PAYHIP_TOKEN_LENGTH = 32


class CommerceConfigError(RuntimeError):
    """Raised when commerce configuration is absent or unusable."""


def load_env_file(path: Path) -> dict:
    """Parse a KEY=VALUE file. Never mutates os.environ."""
    values: dict = {}
    path = Path(path)
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _csv(raw: str) -> frozenset:
    return frozenset(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True, repr=False)
class CommerceSettings:
    mode: str
    stripe_webhook_secret: str
    stripe_expected_account: str
    payhip_webhook_token: str
    payhip_allowed_hosts: frozenset
    payhip_product_ids: frozenset
    max_webhook_bytes: int = MAX_WEBHOOK_BYTES

    @classmethod
    def from_sources(cls, env: Mapping) -> "CommerceSettings":
        mode = env.get("LIBRA_COMMERCE_MODE", "")
        if mode != "test":
            raise CommerceConfigError("commerce_mode_missing_or_not_test")
        required = {
            "stripe_webhook_secret": env.get("STRIPE_WEBHOOK_SECRET_TEST", ""),
            "stripe_expected_account": env.get("STRIPE_EXPECTED_ACCOUNT_TEST", ""),
            "payhip_webhook_token": env.get("PAYHIP_WEBHOOK_TOKEN_TEST", ""),
        }
        missing = sorted(name for name, value in required.items() if not value)
        if missing:
            raise CommerceConfigError("missing:" + ",".join(missing))
        if len(required["payhip_webhook_token"]) < MIN_PAYHIP_TOKEN_LENGTH:
            raise CommerceConfigError("payhip_webhook_token_too_short")
        return cls(
            mode=mode,
            payhip_allowed_hosts=_csv(env.get("PAYHIP_ALLOWED_HOSTS", "")),
            payhip_product_ids=_csv(env.get("PAYHIP_PRODUCT_IDS_TEST", "")),
            **required,
        )

    @staticmethod
    def readiness(env: Mapping) -> dict:
        """Boolean + stable reason codes only. Never returns a secret value."""
        try:
            settings = CommerceSettings.from_sources(env)
        except CommerceConfigError as exc:
            return {"ready": False, "mode": env.get("LIBRA_COMMERCE_MODE", ""), "reasons": [str(exc)]}
        reasons = []
        if not settings.payhip_allowed_hosts:
            reasons.append("payhip_allowed_hosts_empty")
        if not settings.payhip_product_ids:
            reasons.append("payhip_product_ids_empty")
        return {"ready": not reasons, "mode": settings.mode, "reasons": reasons}

    def __repr__(self) -> str:
        return "CommerceSettings(mode='test', secrets='<redacted>')"

    __str__ = __repr__
