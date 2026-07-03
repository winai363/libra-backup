#!/usr/bin/env python3
"""
kdp_enroll_batch.py — Enroll every LIVE, not-yet-enrolled book in KDP Select.

Wraps scripts/kdp_enroll_v2.py (one subprocess per book, title-substring safety,
screenshots). On verified success ("looks enrolled: True" or "ALREADY ENROLLED"),
stamps listing.json kdp_select so the catalog reflects reality.

Usage:
  python3 scripts/kdp_enroll_batch.py            # all LIVE not enrolled
  python3 scripts/kdp_enroll_batch.py --only a,b
"""
import json
import subprocess
import sys
from datetime import date, timedelta
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
        if d.get("live_status") != "LIVE":
            continue
        if (d.get("kdp_select") or {}).get("status") == "Enrolled":
            continue
        if not d.get("kdp_book_id"):
            print(f"SKIP {slug}: no kdp_book_id")
            continue
        out.append((slug, d["kdp_book_id"], d.get("title", "")))
    return out


def main():
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    todo = worklist(only)
    print(f"Enrolling {len(todo)} book(s) in KDP Select\n", flush=True)
    term_start = date.today().isoformat()
    term_end = (date.today() + timedelta(days=89)).isoformat()
    ok, fail = [], []
    for i, (slug, bid, title) in enumerate(todo):
        if i % 6 == 0:
            subprocess.run([PY, "kdp_session_ensure.py"], cwd=str(LIBRA),
                           capture_output=True, text=True)
        # distinctive substring: first 25 chars of the real title, verbatim
        sub = title[:25]
        try:
            r = subprocess.run(
                [PY, "scripts/kdp_enroll_v2.py", bid, sub, f"batch-{slug[:40]}"],
                cwd=str(LIBRA), capture_output=True, text=True, timeout=240)
            blob = (r.stdout or "") + (r.stderr or "")
        except subprocess.TimeoutExpired:
            blob = "TIMEOUT"
        enrolled = ("looks enrolled: True" in blob) or ("ALREADY ENROLLED" in blob)
        if enrolled:
            lf = KDP / slug / "listing.json"
            d = json.loads(lf.read_text())
            d["kdp_select"] = {
                "status": "Enrolled",
                "term_start": term_start,
                "term_end": term_end,
                "source": f"kdp_enroll_batch {term_start}",
            }
            lf.write_text(json.dumps(d, ensure_ascii=False, indent=2))
            ok.append(slug)
            print(f"  [{i+1}/{len(todo)}] OK   {slug}", flush=True)
        else:
            fail.append(slug)
            last = blob.strip().splitlines()[-1:] or ["?"]
            print(f"  [{i+1}/{len(todo)}] FAIL {slug} :: {last[0][:120]}", flush=True)
    print(f"\nSUMMARY: {len(ok)} enrolled, {len(fail)} failed")
    if fail:
        print("FAILED:", ",".join(fail))
        sys.exit(1)


if __name__ == "__main__":
    main()
