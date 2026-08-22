# Libra KDP Frozen Staging Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a fully automated, preparation-only KDP pilot pipeline that creates and validates a fixed French senior-smartphone book in isolated staging while making every live KDP mutation executable path fail closed under TOTAL KDP FREEZE.

**Architecture:** First introduce one dependency-free policy module and enforce it at every direct upload/publish/queue entry point before building staging. Then add a staging orchestrator that reuses Libra's writer, PDF, editorial, and quality modules through an injected workspace, emits an auditable manifest, and can never write `queue.txt`, set a publishable status, start Playwright, or contact KDP. The pilot topic is a checked-in deterministic specification; market research may refresh evidence, but it cannot silently change the product.

**Tech Stack:** Python 3.12, FastAPI, pytest, Bash, Pandoc/EPUBCheck, existing Libra generation modules, JSON artifacts.

## Global Constraints

- TOTAL KDP FREEZE remains in force: no new upload, republish, price, metadata, content, cover, category, appeal, or browser action against KDP.
- The implementation must not call KDP, open Playwright, append `/root/libra/queue.txt`, or mark a staged title `ready`, `uploaded`, or `live`.
- Run pytest only from `/root/libra` and always name the test path; never run pytest from `/root`.
- Preserve existing user changes in `CLAUDE.md`, `HANDOFF.md`, and `memory.md`.
- Do not unpause `auto-generate.sh` or `scripts/process_kdp_queue.sh` cron entries.
- External research and LLM generation are allowed only in an explicitly requested non-test staging run; unit/integration tests must be offline.
- A visual smartphone guide cannot pass staging without instructional images and an image provenance manifest.
- A successful staging run ends at `staged_quality_passed` with `publish_blocked: total_kdp_freeze`.
- Before implementation run `ai-work status`; ownership is already registered as `codex / libra` for this task.

---

## File Map

- Create `kdp_freeze.py`: single source of truth for freeze state, machine-readable denial, and exception.
- Create `tests/test_kdp_freeze.py`: unit coverage for immutable fail-closed policy.
- Modify `kdp_upload.py`: block all CLI mutation modes and async uploader functions before validation, files, session, or Playwright.
- Modify `kdp_finish_publish.py`: block finishing publish before any session/browser access.
- Modify `app.py`: return HTTP 423 for approval and publish-adjacent state transitions without changing `listing.json` or spawning a process.
- Modify `scripts/process_kdp_queue.sh`: exit before reading/mutating the queue or running repair, QA, uploader, or Telegram.
- Modify `scripts/kdp_action_executor.py`: reject every KDP mutation while frozen, before existing action-specific policy.
- Modify `watchdog.sh`: never reset stale upload state to `ready` during freeze.
- Create `tests/test_kdp_freeze_entrypoints.py`: regression coverage across Python, HTTP, shell, and action-executor entry points.
- Create `data/pilots/senior-smartphone-fr.json`: fixed product specification and acceptance criteria.
- Create `staging_pipeline.py`: dependency-injected preparation orchestrator and manifest writer.
- Create `tests/test_staging_pipeline.py`: offline TDD coverage for isolation, fixed topic, artifacts, status, and failure behavior.
- Modify `quality_gate.py`: accept an explicit root directory and enforce visual-niche artifacts when requested.
- Create `tests/test_quality_gate_visual.py`: image/provenance/render gate coverage.
- Modify `gpt_fallback_writer.py`: accept an explicit output root and preparation mode; never infer the live KDP directory for staging.
- Create `scripts/prepare_kdp_pilot.py`: CLI wrapper for the fixed pilot, with `--dry-run` and explicit `--execute` modes.
- Create `tests/test_prepare_kdp_pilot_cli.py`: subprocess-level offline boundary tests.
- Modify `CLAUDE.md`: document the executable guard and permitted frozen staging lane without weakening TOTAL KDP FREEZE.
- Modify `memory.md`: record verified implementation and remaining human/external boundaries after tests.

---

### Task 1: Central Executable Freeze Policy

**Files:**
- Create: `kdp_freeze.py`
- Create: `tests/test_kdp_freeze.py`

**Interfaces:**
- Consumes: no project imports or environment variables.
- Produces: `FREEZE_CODE: str`, `FREEZE_REASON: str`, `KDPFrozenError`, `freeze_state() -> dict[str, object]`, `assert_kdp_mutation_allowed(action: str) -> None`.

- [ ] **Step 1: Write the failing policy tests**

```python
from dataclasses import asdict

import pytest

import kdp_freeze


def test_freeze_state_is_machine_readable_and_active():
    assert kdp_freeze.freeze_state() == {
        "active": True,
        "code": "total_kdp_freeze",
        "reason": kdp_freeze.FREEZE_REASON,
        "allowed": ["local_staging", "read_only_reporting"],
    }


@pytest.mark.parametrize("action", ["new_title", "republish", "price", "metadata", "cover", "publish"])
def test_every_kdp_mutation_fails_closed(action):
    with pytest.raises(kdp_freeze.KDPFrozenError) as exc:
        kdp_freeze.assert_kdp_mutation_allowed(action)
    assert exc.value.code == "total_kdp_freeze"
    assert exc.value.action == action
    assert asdict(exc.value.decision)["allowed"] is False
```

- [ ] **Step 2: Run the test and verify the missing module failure**

Run: `cd /root/libra && pytest tests/test_kdp_freeze.py -q`

Expected: FAIL during collection with `ModuleNotFoundError: No module named 'kdp_freeze'`.

- [ ] **Step 3: Implement the dependency-free policy module**

```python
from dataclasses import dataclass

FREEZE_CODE = "total_kdp_freeze"
FREEZE_REASON = (
    "TOTAL KDP FREEZE is active after four account content blocks; "
    "all KDP mutations are disabled."
)


@dataclass(frozen=True)
class FreezeDecision:
    allowed: bool
    code: str
    reason: str
    action: str


class KDPFrozenError(RuntimeError):
    def __init__(self, action: str):
        self.code = FREEZE_CODE
        self.action = action
        self.decision = FreezeDecision(False, FREEZE_CODE, FREEZE_REASON, action)
        super().__init__(f"{FREEZE_CODE}: {action}: {FREEZE_REASON}")


def freeze_state() -> dict[str, object]:
    return {
        "active": True,
        "code": FREEZE_CODE,
        "reason": FREEZE_REASON,
        "allowed": ["local_staging", "read_only_reporting"],
    }


def assert_kdp_mutation_allowed(action: str) -> None:
    raise KDPFrozenError(action)
```

Do not add an environment-variable override, date expiry, approval token, or force flag. Lifting the freeze must require a reviewed source change.

- [ ] **Step 4: Run the focused test**

Run: `cd /root/libra && pytest tests/test_kdp_freeze.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the policy**

```bash
git add kdp_freeze.py tests/test_kdp_freeze.py
git commit -m "safety: codify total KDP freeze"
```

---

### Task 2: Block Every Direct KDP Mutation Entrypoint

**Files:**
- Modify: `kdp_upload.py:1-45,432-440,1880-1910`
- Modify: `kdp_finish_publish.py:1-30`
- Modify: `app.py:745-899`
- Modify: `scripts/process_kdp_queue.sh:1-18`
- Modify: `scripts/kdp_action_executor.py:164-180`
- Modify: `watchdog.sh:37-70`
- Create: `tests/test_kdp_freeze_entrypoints.py`
- Modify: `tests/test_queue_processor.py`

**Interfaces:**
- Consumes: `assert_kdp_mutation_allowed(action)` and `KDPFrozenError` from Task 1.
- Produces: HTTP 423 JSON denial; CLI/shell exit code `73`; no file/process/browser side effects.

- [ ] **Step 1: Write failing Python and HTTP entrypoint tests**

```python
import asyncio
import json

import pytest
from fastapi.testclient import TestClient

import app as libra_app
import kdp_finish_publish
import kdp_upload


def test_uploader_blocks_before_quality_gate(monkeypatch):
    monkeypatch.setattr(kdp_upload, "require_quality_gate", lambda slug: pytest.fail("gate touched"))
    with pytest.raises(kdp_upload.KDPFrozenError):
        asyncio.run(kdp_upload.upload_to_kdp("pilot"))


def test_finish_publish_blocks_before_session_access(monkeypatch):
    monkeypatch.setattr(kdp_finish_publish, "SESSION_FILE", object())
    with pytest.raises(kdp_finish_publish.KDPFrozenError):
        asyncio.run(kdp_finish_publish.finish_publish("pilot"))


def test_approve_kdp_returns_423_without_mutating_or_spawning(tmp_path, monkeypatch):
    book = tmp_path / "pilot"
    book.mkdir()
    listing = book / "listing.json"
    listing.write_text(json.dumps({"status": "staged_quality_passed", "kdp_uploading": False}))
    monkeypatch.setattr(libra_app, "KDP_DIR", tmp_path)
    monkeypatch.setattr(libra_app, "check_auth", lambda request: None)
    monkeypatch.setattr(libra_app.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"), raising=False)

    response = TestClient(libra_app.app).post("/api/books/pilot/approve-kdp")

    assert response.status_code == 423
    assert response.json()["detail"]["code"] == "total_kdp_freeze"
    assert json.loads(listing.read_text())["kdp_uploading"] is False
```

Import `subprocess` at module scope in `app.py`; this makes process spawning patchable and avoids a test that passes while patching the wrong object.

- [ ] **Step 2: Write failing shell and action-executor tests**

Add to `tests/test_queue_processor.py`:

```python
def test_total_freeze_exits_before_queue_or_uploader(tmp_path):
    libra, _, env = _fixture(tmp_path, upload_exit=99)
    before = (libra / "queue.txt").read_bytes()
    result = subprocess.run([str(SCRIPT)], env=env, check=False, capture_output=True, text=True)
    assert result.returncode == 73
    assert "total_kdp_freeze" in result.stderr
    assert (libra / "queue.txt").read_bytes() == before
```

Add action-executor coverage to `tests/test_kdp_freeze_entrypoints.py`:

```python
from scripts import kdp_action_executor


def test_action_executor_freeze_precedes_action_specific_validation():
    allowed, reason, evidence = kdp_action_executor.validate_action(
        {"kind": "price_update", "slug": "pilot"},
        {"status": "live", "asin": "B000000000"},
        set(),
    )
    assert allowed is False
    assert reason == "total_kdp_freeze"
    assert evidence["freeze"]["active"] is True
```

- [ ] **Step 3: Run the entrypoint tests and verify they fail**

Run: `cd /root/libra && pytest tests/test_kdp_freeze_entrypoints.py tests/test_queue_processor.py -q`

Expected: FAIL because the entry points still proceed and the queue processor returns its old result.

- [ ] **Step 4: Add the guard as the first executable operation in each path**

Use this exact pattern for Python mutation functions:

```python
from kdp_freeze import KDPFrozenError, assert_kdp_mutation_allowed, freeze_state


async def upload_to_kdp(slug: str):
    assert_kdp_mutation_allowed("new_title")
    # existing body remains below
```

Guard `upload_to_kdp`, `update_cover`, `update_metadata`, `update_ebook_content`, and `finish_publish`. In the `kdp_upload.py` CLI, run the guard before dispatch for `--update`, `--cover`, `--meta`, and default upload; keep `--preflight-update` and `--inspect-title` blocked too because they may touch session/browser state. Catch only at the CLI boundary, print the stable code to stderr, and return `73`.

Use this FastAPI helper in `app.py` before reading or writing `listing.json`:

```python
def reject_frozen_kdp_mutation(action: str) -> None:
    try:
        assert_kdp_mutation_allowed(action)
    except KDPFrozenError as exc:
        raise HTTPException(status_code=423, detail={
            "code": exc.code,
            "action": exc.action,
            "reason": str(exc),
        }) from exc
```

Call it at the start of `approve_kdp`, `request_approval`, and before accepting `new_status == "ready"` in `update_status`. `archived` remains local and allowed.

At the top of `scripts/process_kdp_queue.sh`, after variable declarations but before `cd`, lock, queue read, Telegram env read, or writes:

```bash
if "$PYTHON_BIN" -c 'from kdp_freeze import assert_kdp_mutation_allowed; assert_kdp_mutation_allowed("queue_publish")' 2>/dev/null; then
    :
else
    echo "total_kdp_freeze: KDP queue processing disabled" >&2
    exit 73
fi
```

Set `PYTHONPATH="$LIBRA_DIR${PYTHONPATH:+:$PYTHONPATH}"` for that command so test fixtures still import the production policy module deliberately; do not make the policy path injectable.

Make `scripts/kdp_action_executor.py::validate_action` return the denial before inspecting action kind or listing:

```python
state = freeze_state()
if state["active"]:
    return False, "total_kdp_freeze", {"freeze": state}
```

In `watchdog.sh`, replace the stale reset with a freeze-safe normalization:

```python
data["kdp_uploading"] = False
data["status"] = "staged_freeze"
data["publish_blocked"] = "total_kdp_freeze"
```

- [ ] **Step 5: Run focused coverage**

Run: `cd /root/libra && pytest tests/test_kdp_freeze.py tests/test_kdp_freeze_entrypoints.py tests/test_queue_processor.py tests/test_kdp_publish_confirmation.py -q`

Expected: PASS. Update old queue tests to expect exit `73` and unchanged queue under the permanent policy; preserve their former behavior as helper-level tests only if the processor logic is extracted and can be tested without bypassing the freeze.

- [ ] **Step 6: Prove no unguarded mutator remains**

Run:

```bash
cd /root/libra
rg -n "async def (upload_to_kdp|update_cover|update_metadata|update_ebook_content|finish_publish)|approve-kdp|process_kdp_queue|Save and Publish|page\.click" --glob '*.py' --glob '*.sh'
```

Expected: every reported live mutation entry point imports/calls the central freeze guard, or is a helper reachable only through a guarded public entry point. Add a regression test for any additional public mutator found; do not merely document it.

- [ ] **Step 7: Commit executable freeze coverage**

```bash
git add app.py kdp_upload.py kdp_finish_publish.py scripts/process_kdp_queue.sh scripts/kdp_action_executor.py watchdog.sh tests/test_kdp_freeze_entrypoints.py tests/test_queue_processor.py tests/test_kdp_publish_confirmation.py
git commit -m "safety: block all KDP mutation entrypoints"
```

---

### Task 3: Fixed Pilot Specification and Isolated Staging State Machine

**Files:**
- Create: `data/pilots/senior-smartphone-fr.json`
- Create: `staging_pipeline.py`
- Create: `tests/test_staging_pipeline.py`

**Interfaces:**
- Consumes: pilot JSON and injected `StageDependencies` callables.
- Produces: `StageResult`, `/root/kdp-staging/<slug>/staging-manifest.json`, and never a publish queue entry.

- [ ] **Step 1: Check in the exact pilot specification**

```json
{
  "schema_version": 1,
  "pilot_id": "senior-smartphone-fr-v1",
  "slug": "smartphone-sans-stress-seniors-fr",
  "title": "Le Smartphone Sans Stress",
  "subtitle": "Guide pratique grand format pour seniors : appels, WhatsApp, photos et sécurité pas à pas",
  "language": "French",
  "lang_code": "fr",
  "marketplace": "amazon.fr",
  "audience": "French-speaking seniors and family caregivers",
  "niche": "senior smartphone practical guide",
  "description_en": "A large-print, illustrated guide to essential Android smartphone tasks, WhatsApp, photos, video calls, accessibility, and scam prevention.",
  "required_sections": ["Android basics", "accessibility", "WhatsApp", "photos", "video calls", "online safety", "scam prevention", "caregiver checklist"],
  "visual_required": true,
  "minimum_instructional_images": 12,
  "prohibited_claims": ["guaranteed safety", "official WhatsApp guide", "official Android guide"],
  "publish_target": "kdp",
  "publish_blocked": "total_kdp_freeze"
}
```

- [ ] **Step 2: Write failing isolated-state tests**

```python
import json
from pathlib import Path

from staging_pipeline import StageDependencies, prepare_pilot


def test_prepare_pilot_isolated_and_never_queues(tmp_path):
    live = tmp_path / "kdp"
    staging = tmp_path / "staging"
    queue = tmp_path / "queue.txt"
    live.mkdir()
    queue.write_text("existing-book\n")
    before = queue.read_bytes()

    def generate(spec, book_dir):
        book_dir.mkdir(parents=True)
        (book_dir / "listing.json").write_text(json.dumps({"title": spec["title"]}))
        return book_dir

    deps = StageDependencies(
        generate=generate,
        build_pdf=lambda slug, root: root / slug / "paperback.pdf",
        editorial=lambda slug, root: {"passed": True},
        validate=lambda slug, root: {"passed": True, "errors": []},
    )
    result = prepare_pilot(
        spec_path=Path("data/pilots/senior-smartphone-fr.json"),
        staging_root=staging,
        live_root=live,
        queue_path=queue,
        dependencies=deps,
    )

    assert result.status == "staged_quality_passed"
    assert result.publish_blocked == "total_kdp_freeze"
    assert queue.read_bytes() == before
    assert list(live.iterdir()) == []
    manifest = json.loads((result.book_dir / "staging-manifest.json").read_text())
    assert manifest["pilot_id"] == "senior-smartphone-fr-v1"
    assert manifest["publish_blocked"] == "total_kdp_freeze"
```

Also test: invalid spec fails before creating a directory; output slug must equal the checked-in slug; existing staging directory aborts without overwrite; failed editorial/quality results in `staged_quality_failed`; any dependency returning a path outside `staging_root` raises `StagingBoundaryError`; queue bytes and live-tree snapshot remain identical on success and failure.

- [ ] **Step 3: Run tests and verify the missing module failure**

Run: `cd /root/libra && pytest tests/test_staging_pipeline.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'staging_pipeline'`.

- [ ] **Step 4: Implement the minimal state machine**

```python
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from typing import Callable

from kdp_freeze import FREEZE_CODE


class StagingBoundaryError(RuntimeError):
    pass


@dataclass(frozen=True)
class StageDependencies:
    generate: Callable[[dict, Path], Path]
    build_pdf: Callable[[str, Path], Path]
    editorial: Callable[[str, Path], dict]
    validate: Callable[[str, Path], dict]


@dataclass(frozen=True)
class StageResult:
    status: str
    publish_blocked: str
    book_dir: Path
    manifest_path: Path


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def prepare_pilot(*, spec_path: Path, staging_root: Path, live_root: Path,
                  queue_path: Path, dependencies: StageDependencies) -> StageResult:
    spec_bytes = spec_path.read_bytes()
    spec = json.loads(spec_bytes)
    required = {"pilot_id", "slug", "title", "language", "lang_code", "visual_required", "publish_blocked"}
    if required - spec.keys() or spec["publish_blocked"] != FREEZE_CODE:
        raise ValueError("invalid frozen-stage pilot specification")
    book_dir = staging_root / spec["slug"]
    if book_dir.exists() or not _inside(book_dir, staging_root):
        raise StagingBoundaryError("staging destination exists or escapes staging root")
    queue_before = queue_path.read_bytes() if queue_path.exists() else None
    live_before = sorted(str(p.relative_to(live_root)) for p in live_root.rglob("*"))
    produced = dependencies.generate(spec, book_dir)
    if not _inside(produced, staging_root):
        raise StagingBoundaryError("generator escaped staging root")
    dependencies.build_pdf(spec["slug"], staging_root)
    editorial = dependencies.editorial(spec["slug"], staging_root)
    quality = dependencies.validate(spec["slug"], staging_root)
    status = "staged_quality_passed" if editorial.get("passed") and quality.get("passed") else "staged_quality_failed"
    manifest = {
        "schema_version": 1,
        "pilot_id": spec["pilot_id"],
        "slug": spec["slug"],
        "status": status,
        "publish_blocked": FREEZE_CODE,
        "spec_sha256": hashlib.sha256(spec_bytes).hexdigest(),
        "editorial": editorial,
        "quality": quality,
    }
    manifest_path = book_dir / "staging-manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    queue_after = queue_path.read_bytes() if queue_path.exists() else None
    live_after = sorted(str(p.relative_to(live_root)) for p in live_root.rglob("*"))
    if queue_after != queue_before or live_after != live_before:
        raise StagingBoundaryError("live KDP state changed during staging")
    return StageResult(status, FREEZE_CODE, book_dir, manifest_path)
```

Expand the final implementation only enough to guarantee a failure manifest for pipeline failures and to preserve the live/queue snapshot in `finally`. Do not add resume, retries, scheduling, multi-pilot support, or publishing.

- [ ] **Step 5: Run focused tests**

Run: `cd /root/libra && pytest tests/test_staging_pipeline.py -q`

Expected: PASS.

- [ ] **Step 6: Commit staging boundaries**

```bash
git add data/pilots/senior-smartphone-fr.json staging_pipeline.py tests/test_staging_pipeline.py
git commit -m "feat: add isolated frozen KDP staging state"
```

---

### Task 4: Visual-Niche Quality Gate and Explicit Root Injection

**Files:**
- Modify: `quality_gate.py:403-423,564-674`
- Create: `tests/test_quality_gate_visual.py`
- Modify: `tests/test_quality_gate.py`

**Interfaces:**
- Consumes: `validate_book(slug, ..., root: Path | None = None, require_visuals: bool = False)`.
- Produces: deterministic errors for missing/invalid instructional images and `image-provenance.json`.

- [ ] **Step 1: Write failing visual gate tests**

```python
import json

from tests.test_quality_gate import _make_book, _stub_epubcheck
from quality_gate import validate_book


def test_visual_pilot_fails_without_instructional_images(tmp_path, monkeypatch):
    _stub_epubcheck(monkeypatch)
    _make_book(tmp_path)
    report = validate_book("test-book", root=tmp_path, require_visuals=True)
    assert any("instructional images" in error for error in report.errors)


def test_visual_pilot_requires_provenance_for_every_image(tmp_path, monkeypatch):
    _stub_epubcheck(monkeypatch)
    book = _make_book(tmp_path)
    images = book / "images"
    images.mkdir()
    for index in range(12):
        (images / f"step-{index:02}.png").write_bytes(b"not-empty")
    (book / "image-provenance.json").write_text(json.dumps({"images": []}))
    report = validate_book("test-book", root=tmp_path, require_visuals=True)
    assert any("provenance" in error for error in report.errors)
```

Add a passing case with 12 Pillow-created PNGs and 12 provenance rows containing exact fields: `file`, `source_kind`, `source`, `captured_at`, `device`, `os_version`, `app_version`, `license`, `contains_personal_data`, `alt_text`. Reject any row with `contains_personal_data: true`, missing alt text, a file outside `images/`, or unmatched files.

- [ ] **Step 2: Run tests and verify signature failure**

Run: `cd /root/libra && pytest tests/test_quality_gate_visual.py -q`

Expected: FAIL with `TypeError: validate_book() got an unexpected keyword argument 'root'`.

- [ ] **Step 3: Add explicit root and visual validation**

Change the signature without changing the live default:

```python
def validate_book(slug: str, require_pdf: bool = False, check_urls: bool = False,
                  require_editorial: bool = False, *, root: Path | None = None,
                  require_visuals: bool = False) -> GateReport:
    source_root = root if root is not None else KDP_DIR
    book_dir = source_root / slug
```

Add `_validate_visual_assets(book_dir: Path, minimum: int = 12) -> list[str]`. It must parse JSON structurally, open each PNG/JPEG with Pillow and call `verify()`, require exact file/provenance set equality, forbid personal data, and return stable error strings. Call it only when `require_visuals=True`; never infer visual requirements from generated prose.

- [ ] **Step 4: Run existing and new quality tests**

Run: `cd /root/libra && pytest tests/test_quality_gate.py tests/test_quality_gate_visual.py -q`

Expected: PASS.

- [ ] **Step 5: Commit the visual gate**

```bash
git add quality_gate.py tests/test_quality_gate.py tests/test_quality_gate_visual.py
git commit -m "feat: gate visual guides on verified instructional assets"
```

---

### Task 5: Preparation-Only Writer Adapter and Pilot CLI

**Files:**
- Modify: `gpt_fallback_writer.py:483-572,641-910`
- Create: `scripts/prepare_kdp_pilot.py`
- Create: `tests/test_prepare_kdp_pilot_cli.py`
- Modify: `tests/test_writer_dry_run.py`

**Interfaces:**
- Consumes: `prepare_pilot(...)`, fixed pilot JSON, writer functions with explicit `output_root`.
- Produces: `python3 scripts/prepare_kdp_pilot.py --dry-run` and explicit `--execute`; neither publishes or queues.

- [ ] **Step 1: Write failing CLI boundary tests**

```python
import json
import os
import subprocess
from pathlib import Path


SCRIPT = Path(__file__).parent.parent / "scripts" / "prepare_kdp_pilot.py"


def test_dry_run_is_offline_and_write_free(tmp_path):
    env = {**os.environ, "KDP_STAGING_ROOT": str(tmp_path / "staging"), "KDP_DIR": str(tmp_path / "live")}
    result = subprocess.run(["python3", str(SCRIPT), "--dry-run"], env=env, capture_output=True, text=True)
    assert result.returncode == 0
    assert "total_kdp_freeze" in result.stdout
    assert not (tmp_path / "staging").exists()
    assert not (tmp_path / "live").exists()


def test_cli_has_no_publish_or_queue_option():
    result = subprocess.run(["python3", str(SCRIPT), "--help"], capture_output=True, text=True)
    assert result.returncode == 0
    assert "--publish" not in result.stdout
    assert "--queue" not in result.stdout
    assert "--force" not in result.stdout
```

Add an in-process `--execute` test that monkeypatches the production dependency factory; assert each callable receives the staging root, `quality_gate.validate_book` receives `require_pdf=True`, `check_urls=True`, `require_editorial=True`, `require_visuals=True`, and no OpenAI/browser function is invoked in tests.

- [ ] **Step 2: Run tests and verify missing CLI failure**

Run: `cd /root/libra && pytest tests/test_prepare_kdp_pilot_cli.py tests/test_writer_dry_run.py -q`

Expected: FAIL because the CLI does not exist.

- [ ] **Step 3: Refactor writer filesystem functions to require an output root in staging mode**

Introduce these signatures:

```python
def get_existing_books(root: Path = KDP_DIR) -> str: ...

def step4_create_files(topic, content, listing, market_research="", content_research="",
                       *, output_root: Path = KDP_DIR) -> Path:
    book_dir = output_root / topic["slug"]
    ...

def write_book_from_topic(topic: dict, *, output_root: Path, preparation_only: bool) -> Path:
    if not preparation_only:
        raise ValueError("frozen staging requires preparation_only=True")
    ...
```

Move the existing generation body behind `write_book_from_topic`; keep `main()` as a compatibility wrapper but call `assert_kdp_mutation_allowed("writer_live_output")` whenever its output root resolves to live `KDP_DIR`. Remove queue/approval behavior from the reusable function. The adapter must call `editorial_review.review_book` and `quality_gate.validate_book` with the same staging root rather than relying on module globals; add root parameters to editorial review using the same live-default pattern as Task 4.

Replace `run_dry_run`'s broad “any Libra cron” check with exact mutation-capable needles:

```python
MUTATING_CRON_NEEDLES = (
    "/root/libra/auto-generate.sh",
    "/root/libra/scripts/process_kdp_queue.sh",
    "/root/libra/kdp_upload.py",
    "/root/libra/kdp_finish_publish.py",
)
```

Read-only sales, roster, reporting, and session-health crons must not fail staging dry-run.

- [ ] **Step 4: Implement the fixed pilot CLI**

```python
#!/usr/bin/env python3
import argparse
import os
from pathlib import Path

from kdp_freeze import freeze_state
from staging_pipeline import prepare_pilot

LIBRA_DIR = Path(__file__).resolve().parent.parent
SPEC = LIBRA_DIR / "data" / "pilots" / "senior-smartphone-fr.json"


def parse_args():
    parser = argparse.ArgumentParser(description="Prepare the frozen French KDP pilot locally")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--execute", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    state = freeze_state()
    if args.dry_run:
        print(f"PASS: {state['code']} active; staging only; no writes or external calls")
        return 0
    staging_root = Path(os.getenv("KDP_STAGING_ROOT", "/root/kdp-staging"))
    live_root = Path(os.getenv("KDP_DIR", "/root/kdp"))
    result = prepare_pilot(
        spec_path=SPEC,
        staging_root=staging_root,
        live_root=live_root,
        queue_path=LIBRA_DIR / "queue.txt",
        dependencies=production_dependencies(staging_root),
    )
    print(result.manifest_path)
    return 0 if result.status == "staged_quality_passed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
```

Define `production_dependencies(staging_root)` in the same file with small adapters to `write_book_from_topic`, `build_paperback_pdf`, `review_book`, and `validate_book`. Every adapter must pass `staging_root` explicitly. Do not add cron installation.

- [ ] **Step 5: Run CLI, writer, staging, and freeze tests**

Run:

```bash
cd /root/libra
pytest tests/test_writer_dry_run.py tests/test_prepare_kdp_pilot_cli.py tests/test_staging_pipeline.py tests/test_kdp_freeze.py tests/test_kdp_freeze_entrypoints.py -q
python3 scripts/prepare_kdp_pilot.py --dry-run
```

Expected: all tests PASS; CLI prints a PASS line containing `total_kdp_freeze`; no staging/live files are created.

- [ ] **Step 6: Commit the preparation CLI**

```bash
git add gpt_fallback_writer.py editorial_review.py scripts/prepare_kdp_pilot.py tests/test_prepare_kdp_pilot_cli.py tests/test_writer_dry_run.py
git commit -m "feat: automate preparation-only KDP pilot staging"
```

---

### Task 6: Full Safety Verification and Operational Documentation

**Files:**
- Modify: `CLAUDE.md`
- Modify: `memory.md`

**Interfaces:**
- Consumes: all prior tasks.
- Produces: verified operator instructions and current source-of-truth memory.

- [ ] **Step 1: Run the complete relevant offline suite**

Run:

```bash
cd /root/libra
pytest tests/test_kdp_freeze.py tests/test_kdp_freeze_entrypoints.py tests/test_queue_processor.py tests/test_kdp_publish_confirmation.py tests/test_quality_gate.py tests/test_quality_gate_visual.py tests/test_staging_pipeline.py tests/test_prepare_kdp_pilot_cli.py tests/test_writer_dry_run.py tests/test_cover_generator.py -q
```

Expected: PASS with zero network/browser calls.

- [ ] **Step 2: Run static safety scans**

Run:

```bash
cd /root/libra
rg -n "queue\.txt|kdp_upload|finish_publish|async_playwright|playwright" staging_pipeline.py scripts/prepare_kdp_pilot.py
rg -n "assert_kdp_mutation_allowed|freeze_state" app.py kdp_upload.py kdp_finish_publish.py scripts/process_kdp_queue.sh scripts/kdp_action_executor.py
git diff --check
```

Expected: first command has no matches except an inert `queue_path` snapshot reference and no uploader/browser imports; second command shows guards in every mutation entry point; `git diff --check` is clean.

- [ ] **Step 3: Verify cron remains frozen**

Run:

```bash
crontab -l | rg "auto-generate\.sh|process_kdp_queue\.sh"
```

Expected: both lines remain commented with `[PAUSED ...]`; do not change crontab.

- [ ] **Step 4: Document the permitted staging command and prohibited publish actions**

Append to the TOTAL KDP FREEZE section of `CLAUDE.md`:

```markdown
### Frozen staging lane (21 Aug 2026)

- Allowed: `python3 scripts/prepare_kdp_pilot.py --dry-run` and an explicitly requested `--execute` run that writes only under `/root/kdp-staging/`.
- A successful stage is `staged_quality_passed` with `publish_blocked: total_kdp_freeze`.
- `kdp_freeze.py` is the executable source of truth. Do not add force flags, environment overrides, expiry dates, or approval-token bypasses.
- Staging must never append `queue.txt`, set `ready`/`uploaded`/`live`, start Playwright, or call KDP.
```

- [ ] **Step 5: Update memory with verified facts only**

Record: files added, exact tests and counts, dry-run result, cron confirmation, staging command, and that no live KDP action occurred. Do not claim the actual book was generated unless an explicit `--execute` staging run was separately authorized and completed.

- [ ] **Step 6: Review the final diff**

Run:

```bash
cd /root/libra
git status --short
git diff --stat
git diff -- kdp_freeze.py app.py kdp_upload.py kdp_finish_publish.py scripts/process_kdp_queue.sh scripts/kdp_action_executor.py watchdog.sh staging_pipeline.py quality_gate.py gpt_fallback_writer.py editorial_review.py scripts/prepare_kdp_pilot.py CLAUDE.md memory.md
```

Expected: only planned files plus pre-existing user changes; no `.env`, session file, generated book, queue, logs, screenshots, or credentials staged.

- [ ] **Step 7: Commit documentation and verification record**

```bash
git add CLAUDE.md memory.md
git commit -m "docs: record frozen KDP staging workflow"
```

- [ ] **Step 8: Release ownership after the parent workflow completes both projects**

Do not call `ai-work finish` from this isolated KDP task if the Payhip implementation remains active. The owning parent agent must update the shared handoff and run:

```bash
ai-work finish "Implemented frozen KDP staging and Payhip Stripe automation; verified no live KDP mutation"
```

Expected: ownership releases only after all requested implementation, verification, and `memory.md` work is complete.

---

## Acceptance Checklist

- [ ] Every public KDP mutation entry point fails before file mutation, process spawn, session access, or browser launch.
- [ ] Queue processing exits `73` and preserves queue bytes.
- [ ] API mutation requests return HTTP `423` with `code=total_kdp_freeze`.
- [ ] The fixed French pilot cannot be silently replaced by autonomous topic selection.
- [ ] Staging output is isolated from `/root/kdp` and ends with a manifest.
- [ ] The visual guide cannot pass without 12 valid instructional images and complete provenance.
- [ ] Offline tests name explicit paths and run from `/root/libra`.
- [ ] Mutation-capable cron entries remain paused.
- [ ] No KDP, Playwright, Telegram approval, queue, or publish action occurs during verification.
- [ ] `memory.md` records only evidence verified in this implementation.
