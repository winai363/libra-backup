#!/usr/bin/env python3
"""
kdp_session_ensure.py — keep the KDP browser session alive for the unattended
cron jobs (kdp_bookshelf_roster, kdp_sales_sync).

Why: the saved session (kdp_session.json) is one storage_state shared by
kdp.amazon.com (bookshelf) and kdpreports.amazon.com (sales). The *reports*
side's SSO cookie expires first — when it does, sales-sync silently dies with
an HTML signin page (seen 2026-06-25). The bookshelf side stays valid longer,
so checking kdpreports is the early-warning canary.

This script loads the reports dashboard; if it bounces to Amazon sign-in it runs
the automated TOTP login (kdp_login_full.py) to refresh the main Amazon cookie —
which lets every downstream goto() re-establish its own SSO. Idempotent and cheap
when the session is healthy (one page load, no login).

Run it from cron just BEFORE the roster/sales jobs.

Usage:
  python3 kdp_session_ensure.py            # check, re-login only if expired
  python3 kdp_session_ensure.py --force    # always re-login
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from datetime import datetime
from pathlib import Path

LIBRA_DIR = Path(__file__).parent
SESSION_FILE = LIBRA_DIR / "kdp_session.json"
LOGIN_SCRIPT = LIBRA_DIR / "kdp_login_full.py"
LOG_FILE = LIBRA_DIR / "logs" / "session-ensure.log"
REPORTS_DASH = "https://kdpreports.amazon.com/dashboard"


def _log(msg: str) -> None:
    line = f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


async def session_valid() -> bool:
    """True if the reports dashboard loads without bouncing to sign-in."""
    if not SESSION_FILE.exists():
        return False
    from playwright.async_api import async_playwright
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            ctx = await browser.new_context(storage_state=str(SESSION_FILE))
            page = await ctx.new_page()
            await page.goto(REPORTS_DASH, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2500)
            url = page.url
            return "signin" not in url and "/ap/" not in url
        except Exception as exc:
            _log(f"  session check error: {exc}")
            return False
        finally:
            await browser.close()


def relogin() -> bool:
    """Run the automated TOTP login to refresh the session. Returns success."""
    _log("  re-login: running kdp_login_full.py …")
    try:
        r = subprocess.run(
            [sys.executable, str(LOGIN_SCRIPT)],
            capture_output=True, text=True, timeout=180,
        )
        ok = "Session saved" in r.stdout
        tail = (r.stdout.strip().splitlines() or ["(no output)"])[-1]
        _log(f"  re-login {'OK' if ok else 'FAILED'} — {tail}")
        return ok
    except subprocess.TimeoutExpired:
        _log("  re-login timed out")
        return False


def main() -> int:
    force = "--force" in sys.argv
    _log(f"=== session ensure (force={force}) ===")
    if not force and asyncio.run(session_valid()):
        _log("session healthy — no action")
        return 0
    _log("session expired or forced — refreshing")
    ok = relogin()
    if ok and asyncio.run(session_valid()):
        _log("session healthy after re-login ✓")
        return 0
    _log("!! session still invalid after re-login")
    return 1


if __name__ == "__main__":
    sys.exit(main())
