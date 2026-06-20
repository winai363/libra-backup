"""Integration tests for queue retention and idempotent removal."""

import json
import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parent.parent / "scripts" / "process_kdp_queue.sh"


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


def test_upload_failure_retains_first_queue_item(tmp_path):
    libra, _, env = _fixture(tmp_path, upload_exit=1)

    result = subprocess.run([str(SCRIPT)], env=env, check=False)

    assert result.returncode == 1
    assert (libra / "queue.txt").read_text().splitlines() == ["book-one", "book-two"]


def test_upload_success_removes_only_completed_item(tmp_path):
    libra, _, env = _fixture(tmp_path, upload_exit=0)

    result = subprocess.run([str(SCRIPT)], env=env, check=False)

    assert result.returncode == 0
    assert (libra / "queue.txt").read_text().splitlines() == ["book-two"]


def test_quality_failure_rotates_but_does_not_delete_item(tmp_path):
    libra, kdp, env = _fixture(tmp_path, gate_exit=2)

    result = subprocess.run([str(SCRIPT)], env=env, check=False)

    assert result.returncode == 2
    assert (libra / "queue.txt").read_text().splitlines() == ["book-two", "book-one"]
    listing = json.loads((kdp / "book-one" / "listing.json").read_text())
    assert listing["status"] == "quality_failed"


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


def test_detected_title_limit_exit_preserves_queue(tmp_path):
    libra, _, env = _fixture(tmp_path, upload_exit=42)

    result = subprocess.run([str(SCRIPT)], env=env, check=False)

    assert result.returncode == 0
    assert (libra / "queue.txt").read_text().splitlines() == ["book-one", "book-two"]


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
