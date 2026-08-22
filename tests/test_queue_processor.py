"""Integration tests for queue retention and idempotent removal.

TOTAL KDP FREEZE: the processor now exits 73 before touching the queue, so the
retention/rotation behaviour below is unreachable. Those tests are kept (and
skipped) as the specification to restore if the freeze is ever lifted by a
reviewed source change.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parent.parent / "scripts" / "process_kdp_queue.sh"

FROZEN = pytest.mark.skip(
    reason="unreachable under TOTAL KDP FREEZE (process_kdp_queue.sh exits 73)"
)


def _fixture(tmp_path, gate_exit=0, upload_exit=0):
    libra = tmp_path / "libra"
    kdp = tmp_path / "kdp"
    libra.mkdir()
    (kdp / "book-one").mkdir(parents=True)
    (kdp / "book-two").mkdir(parents=True)
    (libra / "queue.txt").write_text("book-one\nbook-two\n")
    for slug in ("book-one", "book-two"):
        (kdp / slug / "listing.json").write_text(
            json.dumps({"status": "ready", "kdp_book_id": f"id-{slug}"})
        )

    gate = libra / "gate.py"
    gate.write_text(f"raise SystemExit({gate_exit})\n")
    uploader = libra / "upload.py"
    uploader.write_text(f"raise SystemExit({upload_exit})\n")

    env = {
        **os.environ,
        "LIBRA_DIR": str(libra),
        "KDP_DIR": str(kdp),
        "QUALITY_GATE": str(gate),
        "KDP_UPLOAD": str(uploader),
        "UPLOAD_TIMEOUT": "5s",
        "TITLE_LIMIT_STATE": str(libra / "data" / "kdp-title-limit.json"),
    }
    return libra, kdp, env


@FROZEN
def test_upload_failure_rotates_first_queue_item(tmp_path):
    libra, _, env = _fixture(tmp_path, upload_exit=1)

    result = subprocess.run([str(SCRIPT)], env=env, check=False)

    assert result.returncode == 1
    assert (libra / "queue.txt").read_text().splitlines() == ["book-two", "book-one"]


@FROZEN
def test_upload_success_removes_only_completed_item(tmp_path):
    libra, _, env = _fixture(tmp_path, upload_exit=0)

    result = subprocess.run([str(SCRIPT)], env=env, check=False)

    assert result.returncode == 0
    assert (libra / "queue.txt").read_text().splitlines() == ["book-two"]


@FROZEN
def test_quality_failure_rotates_but_does_not_delete_item(tmp_path):
    libra, kdp, env = _fixture(tmp_path, gate_exit=2)

    result = subprocess.run([str(SCRIPT)], env=env, check=False)

    assert result.returncode == 2
    assert (libra / "queue.txt").read_text().splitlines() == ["book-two", "book-one"]
    listing = json.loads((kdp / "book-one" / "listing.json").read_text())
    assert listing["status"] == "quality_failed"


@FROZEN
def test_active_title_limit_preserves_queue_without_calling_uploader(tmp_path):
    libra, _, env = _fixture(tmp_path, upload_exit=99)
    state = Path(env["TITLE_LIMIT_STATE"])
    state.parent.mkdir()
    state.write_text(json.dumps({
        "active": True,
        "retry_after": "2999-01-01T00:00:00",
    }))

    result = subprocess.run([str(SCRIPT)], env=env, check=False)

    assert result.returncode == 0
    assert (libra / "queue.txt").read_text().splitlines() == ["book-one", "book-two"]


@FROZEN
def test_detected_title_limit_exit_preserves_queue(tmp_path):
    libra, _, env = _fixture(tmp_path, upload_exit=42)

    result = subprocess.run([str(SCRIPT)], env=env, check=False)

    assert result.returncode == 0
    assert (libra / "queue.txt").read_text().splitlines() == ["book-one", "book-two"]


@FROZEN
def test_success_clears_expired_title_limit_state(tmp_path):
    libra, _, env = _fixture(tmp_path, upload_exit=0)
    state = Path(env["TITLE_LIMIT_STATE"])
    state.parent.mkdir()
    state.write_text(json.dumps({
        "active": True,
        "retry_after": "2000-01-01T00:00:00",
    }))

    result = subprocess.run([str(SCRIPT)], env=env, check=False)

    assert result.returncode == 0
    assert not state.exists()


def test_total_freeze_exits_before_queue_or_uploader(tmp_path):
    libra, _, env = _fixture(tmp_path, upload_exit=99)
    before = (libra / "queue.txt").read_bytes()

    result = subprocess.run(
        [str(SCRIPT)], env=env, check=False, capture_output=True, text=True
    )

    assert result.returncode == 73
    assert "total_kdp_freeze" in result.stderr
    assert (libra / "queue.txt").read_bytes() == before
    assert not (libra / ".queue.lock").exists()
    assert not (libra / "queue_log.txt").exists()
