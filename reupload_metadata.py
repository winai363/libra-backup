#!/usr/bin/env python3
"""
reupload_metadata.py — Re-publish all LIVE books through the full kdp_upload
flow so the new Amazon-2026 metadata lands: sanitized keywords, HTML blurb, and
the corrected Business/Finance/etc. categories (the React-cascade setter).

Each book runs in its own subprocess (one failure never aborts the batch);
the KDP session is refreshed every few books. Per-book OK/FAIL is logged, and
failures are listed at the end for a retry pass.

Usage:
  python3 reupload_metadata.py                 # all LIVE except --skip
  python3 reupload_metadata.py --skip a,b,c
  python3 reupload_metadata.py --only a,b      # just these slugs
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
    only = None
    if "--skip" in sys.argv:
        skip = set(sys.argv[sys.argv.index("--skip") + 1].split(","))
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    todo = [s for s in live_slugs() if s not in skip and (only is None or s in only)]
    print(f"Re-publishing metadata for {len(todo)} book(s); skipping {len(skip)}\n", flush=True)
    results = []
    for i, slug in enumerate(todo):
        if i % 5 == 0:
            subprocess.run([PY, "kdp_session_ensure.py"], cwd=str(LIBRA),
                           capture_output=True, text=True)
        try:
            r = subprocess.run([PY, "kdp_upload.py", slug, "--meta"],
                               cwd=str(LIBRA), capture_output=True, text=True,
                               timeout=600)
            blob = (r.stdout or "") + (r.stderr or "")
            ok = "Metadata update complete" in blob
            err = "" if ok else (blob.strip().splitlines()[-1:] or ["?"])[0][:160]
        except subprocess.TimeoutExpired:
            ok, err = False, "timeout"
        except Exception as e:
            ok, err = False, str(e)[:120]
        results.append((slug, ok))
        print(f"  [{i+1}/{len(todo)}] {'OK  ' if ok else 'FAIL'} {slug}"
              + ("" if ok else f"  — {err}"), flush=True)
    good = sum(1 for _, o in results if o)
    print(f"\nSUMMARY: {good}/{len(results)} re-published", flush=True)
    fails = [s for s, o in results if not o]
    if fails:
        print("RETRY:", ",".join(fails), flush=True)


if __name__ == "__main__":
    main()
