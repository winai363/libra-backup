"""Regression coverage: every KDP mutation entry point fails closed.

Covers Python callables, the HTTP API, and the action executor. The shell queue
processor is covered in tests/test_queue_processor.py.
"""

import asyncio
import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import app as libra_app
import kdp_finish_publish
import kdp_upload
from kdp_freeze import KDPFrozenError
from scripts import kdp_action_executor


def test_uploader_blocks_before_quality_gate(monkeypatch):
    monkeypatch.setattr(
        kdp_upload, "require_quality_gate", lambda slug: pytest.fail("gate touched")
    )
    with pytest.raises(KDPFrozenError):
        asyncio.run(kdp_upload.upload_to_kdp("pilot"))


@pytest.mark.parametrize(
    "func_name", ["update_ebook_content", "update_cover", "update_metadata"]
)
def test_update_paths_block_before_browser(monkeypatch, func_name):
    monkeypatch.setattr(
        kdp_upload, "async_playwright", lambda: pytest.fail("browser started")
    )
    with pytest.raises(KDPFrozenError):
        asyncio.run(getattr(kdp_upload, func_name)("pilot"))


def test_finish_publish_blocks_before_session_access(monkeypatch):
    monkeypatch.setattr(kdp_finish_publish, "SESSION_FILE", object())
    with pytest.raises(KDPFrozenError):
        asyncio.run(kdp_finish_publish.finish_publish("pilot"))


def _staged_book(tmp_path, monkeypatch):
    book = tmp_path / "pilot"
    book.mkdir()
    (book / "listing.json").write_text(
        json.dumps({"status": "staged_quality_passed", "kdp_uploading": False})
    )
    monkeypatch.setattr(libra_app, "KDP_DIR", tmp_path)
    monkeypatch.setattr(libra_app, "check_auth", lambda request: None)
    monkeypatch.setattr(
        libra_app.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned")
    )
    return book / "listing.json"


def test_approve_kdp_returns_423_without_mutating_or_spawning(tmp_path, monkeypatch):
    listing = _staged_book(tmp_path, monkeypatch)

    response = TestClient(libra_app.app).post("/api/books/pilot/approve-kdp")

    assert response.status_code == 423
    assert response.json()["detail"]["code"] == "total_kdp_freeze"
    assert json.loads(listing.read_text())["kdp_uploading"] is False


def test_request_approval_returns_423_without_mutating(tmp_path, monkeypatch):
    listing = _staged_book(tmp_path, monkeypatch)

    response = TestClient(libra_app.app).post("/api/books/pilot/request-approval")

    assert response.status_code == 423
    assert "approval_pending" not in json.loads(listing.read_text())


def test_status_ready_returns_423_but_archived_stays_local(tmp_path, monkeypatch):
    listing = _staged_book(tmp_path, monkeypatch)
    client = TestClient(libra_app.app)

    blocked = client.patch("/api/books/pilot/status", json={"status": "ready"})
    assert blocked.status_code == 423
    assert json.loads(listing.read_text())["status"] == "staged_quality_passed"

    monkeypatch.setattr(libra_app, "notify", _noop_notify)
    monkeypatch.setattr(libra_app, "translate_th", _noop_translate)
    allowed = client.patch("/api/books/pilot/status", json={"status": "archived"})
    assert allowed.status_code == 200
    assert json.loads(listing.read_text())["status"] == "archived"


async def _noop_notify(message):
    return None


async def _noop_translate(text):
    return ""


def test_action_executor_freeze_precedes_action_specific_validation():
    allowed, reason, evidence = kdp_action_executor.validate_action(
        {"kind": "price_update", "slug": "pilot"},
        {"status": "live", "asin": "B000000000"},
        set(),
    )
    assert allowed is False
    assert reason == "total_kdp_freeze"
    assert evidence["freeze"]["active"] is True


def test_watchdog_never_resets_stuck_book_to_ready():
    source = Path(__file__).resolve().parent.parent / "watchdog.sh"
    text = source.read_text(encoding="utf-8")
    assert 'data["status"] = "ready"' not in text
    assert 'data["publish_blocked"] = "total_kdp_freeze"' in text


# ── Additional live mutators found by the static scan (Task 2, Step 6) ──────
# Each must raise before opening a browser, session, or KDP page.

def test_every_additional_kdp_mutator_fails_closed(monkeypatch):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import aplus_resume_submit
    import aplus_upload
    import author_photo_upload
    import author_url_retry
    import free_promo_auto
    import kdp_fix_book
    import kdp_fix_publish
    import kdp_live_replace
    import kdp_paperback_upload
    import kdp_unpublish
    import set_price

    calls = [
        (aplus_upload.run, ("pilot", True, 0)),
        (aplus_resume_submit.run, ("pilot", False)),
        (author_photo_upload.run, (None, True)),
        (author_url_retry.main, ()),
        (free_promo_auto.schedule_one, ("pilot", "B01", "Title", True)),
        (kdp_fix_book.fix_book, ("B000000000",)),
        (kdp_fix_publish.main, ()),
        (kdp_live_replace.replace_on_kdp, ({"slug": "pilot"},)),
        (kdp_paperback_upload.create_paperback, ("pilot", "9.99")),
        (kdp_unpublish.run, (True,)),
        (set_price.set_price, ("pilot", "2.99", True)),
    ]
    for func, args in calls:
        with pytest.raises(KDPFrozenError):
            asyncio.run(func(*args))


def test_kdp_select_enroll_script_refuses_on_import():
    """kdp_enroll_v2 runs at import time, so the guard must fire there."""
    import subprocess

    libra = Path(__file__).resolve().parent.parent
    result = subprocess.run(
        [sys.executable, str(libra / "scripts" / "kdp_enroll_v2.py"), "slug", "B01", "1"],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "total_kdp_freeze" in result.stderr


# ── The one authorised new title ─────────────────────────────────────────────

def test_the_approved_book_can_reach_the_uploader(monkeypatch):
    """The guard must let an authorised NEW title through — and only it."""
    import kdp_freeze

    approved = "a-book-bui-authorised"
    monkeypatch.setattr(kdp_freeze, "APPROVED_UPLOADS", {approved: "test fixture"})
    reached = {}
    monkeypatch.setattr(kdp_upload, "require_quality_gate",
                        lambda slug: reached.setdefault("slug", slug) and False)

    asyncio.run(kdp_upload.upload_to_kdp(approved))

    assert reached["slug"] == approved


def test_existing_live_books_stay_untouchable_even_now(monkeypatch):
    """A price or cover change is what caused the last two blocks."""
    monkeypatch.setattr(kdp_upload, "async_playwright", lambda: pytest.fail("browser started"))
    for slug in ("adhd-adults-workbook-es", "beginner-watercolor-spanish"):
        with pytest.raises(KDPFrozenError):
            asyncio.run(kdp_upload.update_cover(slug))
        with pytest.raises(KDPFrozenError):
            asyncio.run(kdp_upload.update_metadata(slug))
        with pytest.raises(KDPFrozenError):
            asyncio.run(kdp_upload.upload_to_kdp(slug))
