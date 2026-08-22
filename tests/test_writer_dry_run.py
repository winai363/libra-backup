"""Tests for the no-write writer preflight."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import gpt_fallback_writer


def test_dry_run_passes_with_paused_libra_cron(monkeypatch):
    class Result:
        stdout = "0 5 * * * /opt/newton/setup/app-autoupdate.sh\n"

    monkeypatch.setattr(
        gpt_fallback_writer.subprocess,
        "run",
        lambda *args, **kwargs: Result(),
    )
    monkeypatch.setattr(gpt_fallback_writer.shutil, "which", lambda command: f"/usr/bin/{command}")

    assert gpt_fallback_writer.run_dry_run() == 0


def test_dry_run_fails_when_libra_cron_is_active(monkeypatch):
    class Result:
        stdout = "0 3 * * * /bin/bash /root/libra/auto-generate.sh\n"

    monkeypatch.setattr(
        gpt_fallback_writer.subprocess,
        "run",
        lambda *args, **kwargs: Result(),
    )
    monkeypatch.setattr(gpt_fallback_writer.shutil, "which", lambda command: f"/usr/bin/{command}")

    assert gpt_fallback_writer.run_dry_run() == 1


def test_read_only_libra_cron_does_not_fail_the_staging_boundary(monkeypatch):
    """Sales sync, roster, and reporting crons are allowed under the freeze."""
    class Result:
        stdout = (
            "15 9 * * * python3 /root/libra/kdp_sales_sync.py\n"
            "45 8 * * * /usr/bin/python3 /root/libra/kdp_bookshelf_roster.py --alert\n"
            "30 8 * * * /usr/bin/python3 /root/libra/kdp_session_ensure.py\n"
        )

    monkeypatch.setattr(
        gpt_fallback_writer.subprocess, "run", lambda *args, **kwargs: Result()
    )
    monkeypatch.setattr(gpt_fallback_writer.shutil, "which", lambda command: f"/usr/bin/{command}")

    assert gpt_fallback_writer.run_dry_run() == 0


def test_active_queue_processor_cron_still_fails_the_boundary(monkeypatch):
    class Result:
        stdout = "30 2 * * * bash /root/libra/scripts/process_kdp_queue.sh\n"

    monkeypatch.setattr(
        gpt_fallback_writer.subprocess, "run", lambda *args, **kwargs: Result()
    )
    monkeypatch.setattr(gpt_fallback_writer.shutil, "which", lambda command: f"/usr/bin/{command}")

    assert gpt_fallback_writer.run_dry_run() == 1
