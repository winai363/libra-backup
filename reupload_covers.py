#!/usr/bin/env python3
"""
reupload_covers.py — Re-upload redesigned covers to all LIVE KDP books.
Runs each book in its own subprocess (isolated — one failure never aborts the
batch), refreshes the KDP session every few books, logs per-book outcome.

Usage:
  python3 reupload_covers.py                 # all LIVE except --skip
  python3 reupload_covers.py --skip a,b,c
"""
import json
import subprocess
import sys
from pathlib import Path

LIBRA = Path(__file__).parent
KDP = Path("/root/kdp")
PY = sys.executable


def live_slugs():
    out = []
    for lf in sorted(KDP.glob("*/listing.json")):
        try:
            if json.loads(lf.read_text()).get("live_status") == "LIVE":
                out.append(lf.parent.name)
        except Exception:
            pass
    return out


def main():
    skip = set()
    if "--skip" in sys.argv:
        skip = set(sys.argv[sys.argv.index("--skip") + 1].split(","))
    todo = [s for s in live_slugs() if s not in skip]
    print(f"Re-uploading {len(todo)} cover(s); skipping {len(skip)}\n", flush=True)
    results = []
    for i, slug in enumerate(todo):
        if i % 6 == 0:
            subprocess.run([PY, "kdp_session_ensure.py"], cwd=str(LIBRA),
                           capture_output=True, text=True)
        try:
            r = subprocess.run([PY, "kdp_upload.py", slug, "--cover"],
                               cwd=str(LIBRA), capture_output=True, text=True,
                               timeout=420)
            # kdp_upload logs to stderr — check both streams.
            blob = (r.stdout or "") + (r.stderr or "")
            ok = "Cover update complete" in blob
            err = "" if ok else (blob.strip().splitlines()[-1:] or ["?"])
        except subprocess.TimeoutExpired:
            ok, err = False, "timeout"
        except Exception as e:
            ok, err = False, str(e)[:120]
        results.append((slug, ok))
        print(f"  [{i+1}/{len(todo)}] {'OK  ' if ok else 'FAIL'} {slug}"
              + ("" if ok else f"  — {err}"), flush=True)
    good = sum(1 for _, o in results if o)
    print(f"\nSUMMARY: {good}/{len(results)} re-uploaded", flush=True)
    for s, o in results:
        if not o:
            print("  ✗ FAILED:", s, flush=True)


if __name__ == "__main__":
    main()
