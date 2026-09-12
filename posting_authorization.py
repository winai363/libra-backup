"""Channel-scoped authorization for anything that publishes outside this server.

The standing rule (owner decision 2026-08-30, recorded in CLAUDE.md and in the
cancelled promo crons) is that Libra posts nothing automatically: no ads, no
promo runs, no posting. This module does not weaken that rule — it makes it
explicit and narrow, so a single channel can be opened by the owner without
re-opening the whole promo lane, and so code can never "just post" by default.

Fail closed: a missing file, unreadable JSON, an unknown channel, or a channel
without an explicit owner record is NOT authorized. There is no environment
override and no global "allow all".

    import posting_authorization as auth
    if not auth.channel_authorized("pinterest-rss"):
        ...refuse...
"""
from __future__ import annotations

import json
from pathlib import Path

AUTHORIZATION_FILE = Path(__file__).parent / "data" / "posting_authorization.json"

# Channels this codebase knows how to serve. A name outside this set is never
# authorized, so a typo in the file cannot silently open something else.
KNOWN_CHANNELS = frozenset({"pinterest-rss"})


def _load(path: Path | None = None) -> dict:
    try:
        data = json.loads(Path(path or AUTHORIZATION_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def channel_record(channel: str, *, path: Path | None = None) -> dict:
    channels = _load(path).get("channels")
    record = channels.get(channel) if isinstance(channels, dict) else None
    return record if isinstance(record, dict) else {}


def channel_authorized(channel: str, *, path: Path | None = None) -> bool:
    """True only when the owner has authorized this exact channel and said who
    and when. `authorized: true` on its own is not enough: an authorization with
    no owner and no date is indistinguishable from a stray edit."""
    if channel not in KNOWN_CHANNELS:
        return False
    record = channel_record(channel, path=path)
    if record.get("authorized") is not True:
        return False
    return bool(str(record.get("authorized_by") or "").strip()) and \
        bool(str(record.get("authorized_at") or "").strip())


def authorization_state(*, path: Path | None = None) -> dict:
    """Every known channel and whether it is open, for reports and dashboards."""
    return {
        channel: {
            "authorized": channel_authorized(channel, path=path),
            "record": channel_record(channel, path=path),
        }
        for channel in sorted(KNOWN_CHANNELS)
    }
