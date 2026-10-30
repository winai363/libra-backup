#!/usr/bin/env python3
"""Read-only mobile audit; requires a caller-supplied local axe.min.js."""

import argparse
import json
import os
from pathlib import Path
import tempfile


PAGES = (
    "/guides/",
    "/guides/rutinas-tdah-adultos",
    "/growth/articles/adhd-lista-dos-columnas-es?c=search-surface",
)
VIEWPORT = {"width": 390, "height": 844}

AXE_AUDIT = """() => axe.run(document, {
    runOnly: {type: 'tag', values: ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']}
})"""
LAYOUT_AUDIT = """() => {
    const small_targets = [];
    for (const element of document.querySelectorAll('a[href], button, input, select, textarea')) {
        const rect = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        if (rect.width <= 0 || rect.height <= 0 || style.visibility === 'hidden'
            || style.visibility === 'collapse' || style.display === 'none') continue;
        if (rect.width < 44 || rect.height < 44) {
            small_targets.push({
                tag: element.tagName.toLowerCase(),
                text: (element.getAttribute('aria-label') || element.textContent || '').trim(),
                width: rect.width, height: rect.height,
            });
        }
    }
    return {
        overflow: Math.max(document.documentElement.scrollWidth, document.body.scrollWidth)
            > window.innerWidth,
        small_targets,
    };
}"""


def audit_failed(row: dict) -> bool:
    severe = any(item.get("impact") in {"critical", "serious"} for item in row["violations"])
    return (
        row.get("status", 200) != 200
        or severe
        or bool(row["small_targets"] or row["overflow"] or row["console_errors"])
        or bool(row.get("error"))
    )


def _page_result(base_url: str, path: str) -> dict:
    return {
        "path": path, "url": base_url.rstrip("/") + path, "status": None,
        "violations": [], "small_targets": [], "overflow": None, "console_errors": [],
    }


def run_audit(base_url: str, axe_script: Path) -> dict:
    # Import only for the optional CLI: application startup never requires Playwright.
    from playwright.sync_api import sync_playwright

    axe_script = axe_script.resolve(strict=True)
    report = {"base_url": base_url, "viewport": VIEWPORT.copy(), "pages": []}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        try:
            for path in PAGES:
                row = _page_result(base_url, path)
                report["pages"].append(row)
                context = None
                try:
                    context = browser.new_context(
                        viewport=VIEWPORT.copy(),
                        extra_http_headers={"X-Libra-Measurement": "synthetic"},
                    )
                    page = context.new_page()
                    errors = row["console_errors"]
                    page.on("console", lambda message, errors=errors: errors.append(message.text)
                            if message.type == "error" else None)
                    page.on("pageerror", lambda error, errors=errors: errors.append(str(error)))
                    response = page.goto(row["url"], wait_until="networkidle")
                    row["status"] = response.status if response else None
                    page.add_script_tag(path=str(axe_script))
                    row["violations"] = page.evaluate(AXE_AUDIT)["violations"]
                    row.update(page.evaluate(LAYOUT_AUDIT))
                except Exception as error:
                    row["error"] = str(error)
                finally:
                    if context is not None:
                        context.close()
        finally:
            browser.close()
    return report


def write_report(output: Path, report: dict) -> None:
    """Replace the report only after its complete JSON has reached a sibling file."""
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=output.parent,
            prefix=f".{output.name}.", suffix=".tmp", delete=False,
        ) as stream:
            temporary = Path(stream.name)
            json.dump(report, stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True, help="Application root, including /libra if mounted")
    parser.add_argument("--axe-script", type=Path, required=True, help="Local axe.min.js (never downloaded)")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = run_audit(args.base_url, args.axe_script)
    except Exception as error:
        report = {"base_url": args.base_url, "viewport": VIEWPORT.copy(), "pages": [
            {**_page_result(args.base_url, path), "error": str(error)} for path in PAGES
        ]}
    write_report(args.output, report)
    return int(any(audit_failed(row) for row in report["pages"]))


if __name__ == "__main__":
    raise SystemExit(main())
