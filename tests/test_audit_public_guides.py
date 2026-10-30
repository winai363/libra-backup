import pytest

from scripts.audit_public_guides import audit_failed


def clean_page(**changes):
    return {
        "status": 200, "violations": [], "small_targets": [],
        "overflow": False, "console_errors": [], **changes,
    }


@pytest.mark.parametrize("impact", ["critical", "serious"])
def test_audit_failed_for_severe_violation(impact):
    assert audit_failed(clean_page(violations=[{"impact": impact}]))


@pytest.mark.parametrize("changes", [
    {"small_targets": [{"text": "CTA", "width": 40, "height": 44}]},
    {"overflow": True},
    {"console_errors": ["Uncaught error"]},
    {"status": 404},
    {"status": None},
    {"error": "axe unavailable"},
])
def test_audit_failed_for_incomplete_or_failed_page(changes):
    assert audit_failed(clean_page(**changes))


@pytest.mark.parametrize("impact", ["moderate", "minor", None])
def test_audit_accepts_nonblocking_axe_violations(impact):
    assert not audit_failed(clean_page(violations=[{"impact": impact}]))


def test_audit_accepts_clean_page():
    assert not audit_failed(clean_page())
    assert not audit_failed({
        "violations": [], "small_targets": [], "overflow": False, "console_errors": [],
    })


EXPECTED_PATHS = (
    "/guides/",
    "/guides/rutinas-tdah-adultos",
    "/growth/articles/adhd-lista-dos-columnas-es?c=search-surface",
)


def test_runner_uses_fresh_synthetic_mobile_contexts_and_local_axe(tmp_path, monkeypatch):
    import sys
    from types import SimpleNamespace
    from scripts import audit_public_guides as audit

    contexts = []
    axe_file = tmp_path / "axe.min.js"
    axe_file.write_text("window.axe = {};")

    class Page:
        def on(self, event, handler):
            self.handlers[event] = handler

        def goto(self, url, *, wait_until):
            assert wait_until == "networkidle"
            self.url = url
            if len(contexts) == 1:
                self.handlers["console"](SimpleNamespace(type="error", text="console failure"))
                self.handlers["console"](SimpleNamespace(type="warning", text="not an error"))
                self.handlers["pageerror"]("uncaught failure")
            return SimpleNamespace(status=200 if len(contexts) != 2 else 503)

        def add_script_tag(self, *, path):
            assert path == str(axe_file.resolve())
            if len(contexts) == 2:
                raise RuntimeError("axe injection failed")

        def evaluate(self, expression):
            if "axe.run" in expression:
                for tag in ("wcag2a", "wcag2aa", "wcag21a", "wcag21aa"):
                    assert tag in expression
                return {"violations": [{"impact": "minor", "id": "test"}]}
            assert "a[href], button, input, select, textarea" in expression
            assert "44" in expression
            assert "scrollWidth" in expression
            return {"overflow": False, "small_targets": []}

    class Context:
        def new_page(self):
            self.page = Page()
            self.page.handlers = {}
            return self.page

        def close(self):
            self.closed = True

    class Browser:
        def new_context(self, **options):
            assert options == {
                "viewport": {"width": 390, "height": 844},
                "extra_http_headers": {"X-Libra-Measurement": "synthetic"},
            }
            context = Context()
            contexts.append(context)
            return context

        def close(self):
            self.closed = True

    browser = Browser()

    class Playwright:
        def __enter__(self):
            return SimpleNamespace(chromium=SimpleNamespace(launch=lambda: browser))

        def __exit__(self, *args):
            pass

    monkeypatch.setitem(sys.modules, "playwright.sync_api", SimpleNamespace(sync_playwright=Playwright))
    report = audit.run_audit("https://example.test/libra/", axe_file)

    assert tuple(row["path"] for row in report["pages"]) == EXPECTED_PATHS
    assert [context.page.url for context in contexts] == [
        "https://example.test/libra" + path for path in EXPECTED_PATHS
    ]
    assert all(context.closed for context in contexts)
    assert browser.closed
    assert report["pages"][0]["console_errors"] == ["console failure", "uncaught failure"]
    assert report["pages"][0]["violations"][0]["id"] == "test"
    assert report["pages"][1]["status"] == 503
    assert "axe injection failed" in report["pages"][1]["error"]
    assert not audit_failed(report["pages"][2])


def test_report_is_replaced_atomically_and_failed_write_keeps_previous(tmp_path, monkeypatch):
    import json
    from scripts import audit_public_guides as audit

    output = tmp_path / "audit.json"
    output.write_text("previous report")
    original_replace = audit.os.replace
    replacements = []

    def check_replace(source, target):
        assert output.read_text() == "previous report"
        assert source.parent == output.parent
        assert json.loads(source.read_text()) == {"pages": []}
        replacements.append((source, target))
        original_replace(source, target)

    monkeypatch.setattr(audit.os, "replace", check_replace)
    audit.write_report(output, {"pages": []})
    assert len(replacements) == 1
    assert json.loads(output.read_text()) == {"pages": []}

    def fail_dump(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(audit.json, "dump", fail_dump)
    with pytest.raises(OSError, match="disk full"):
        audit.write_report(output, {"pages": ["incomplete"]})
    assert json.loads(output.read_text()) == {"pages": []}
    assert list(tmp_path.iterdir()) == [output]


@pytest.mark.parametrize("status, expected", [(200, 0), (404, 1)])
def test_cli_exit_status_uses_all_page_results(tmp_path, monkeypatch, status, expected):
    import json
    from scripts import audit_public_guides as audit

    output = tmp_path / "audit.json"
    axe_file = tmp_path / "axe.min.js"
    report = {"pages": [clean_page(status=status), clean_page(), clean_page()]}

    def run(base_url, axe_script):
        assert base_url == "https://example.test/libra"
        assert axe_script == axe_file
        return report

    monkeypatch.setattr(audit, "run_audit", run)
    assert audit.main([
        "--base-url", "https://example.test/libra", "--axe-script", str(axe_file),
        "--output", str(output),
    ]) == expected
    assert json.loads(output.read_text()) == report


def test_cli_records_setup_failure_for_every_page(tmp_path, monkeypatch):
    import json
    from scripts import audit_public_guides as audit

    output = tmp_path / "audit.json"

    def fail_run(*args):
        raise RuntimeError("Chromium unavailable")

    monkeypatch.setattr(audit, "run_audit", fail_run)
    assert audit.main([
        "--base-url", "https://example.test/libra", "--axe-script", "missing.js",
        "--output", str(output),
    ]) == 1
    rows = json.loads(output.read_text())["pages"]
    assert tuple(row["path"] for row in rows) == EXPECTED_PATHS
    assert all(audit_failed(row) and "Chromium unavailable" in row["error"] for row in rows)
