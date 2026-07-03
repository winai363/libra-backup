#!/usr/bin/env python3
"""
aplus_batch.py — Submit A+ Content for every LIVE book that has assets
and hasn't been submitted yet.

Wraps aplus_upload.py (one subprocess per book; it verifies each stage and
stamps listing.json aplus.status on success).

Usage:
  python3 scripts/aplus_batch.py            # all pending
  python3 scripts/aplus_batch.py --only a,b
  python3 scripts/aplus_batch.py --limit 5
"""
import json
import subprocess
import sys
from pathlib import Path

LIBRA = Path(__file__).resolve().parent.parent
KDP = Path("/root/kdp")
PY = sys.executable


def worklist(only=None):
    out = []
    for lf in sorted(KDP.glob("*/listing.json")):
        slug = lf.parent.name
        if only is not None and slug not in only:
            continue
        try:
            d = json.loads(lf.read_text())
        except Exception:
            continue
        if d.get("live_status") != "LIVE" or not d.get("asin"):
            continue
        if (d.get("aplus") or {}).get("status") == "submitted":
            continue
        if not (KDP / slug / "aplus" / "content.json").exists():
            print(f"SKIP {slug}: no aplus assets")
            continue
        out.append(slug)
    return out


def main():
    only = None
    limit = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    if "--limit" in sys.argv:
        limit = int(sys.argv[sys.argv.index("--limit") + 1])
    todo = worklist(only)
    if limit:
        todo = todo[:limit]
    print(f"A+ submitting {len(todo)} book(s)\n", flush=True)
    ok, fail = [], []
    for i, slug in enumerate(todo):
        try:
            r = subprocess.run([PY, "aplus_upload.py", slug], cwd=str(LIBRA),
                               capture_output=True, text=True, timeout=540)
            blob = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            blob = "TIMEOUT"
        if "SUBMITTED" in blob:
            ok.append(slug)
            print(f"  [{i+1}/{len(todo)}] OK   {slug}", flush=True)
        else:
            fail.append(slug)
            last = [l for l in blob.strip().splitlines() if l.strip()][-1:] or ["?"]
            print(f"  [{i+1}/{len(todo)}] FAIL {slug} :: {last[0][:120]}",
                  flush=True)
    print(f"\nSUMMARY: {len(ok)} submitted, {len(fail)} failed")
    if fail:
        print("FAILED:", ",".join(fail))
        sys.exit(1)


if __name__ == "__main__":
    main()
