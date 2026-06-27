#!/usr/bin/env python3
"""Re-run seo_optimizer (real write) across all LIVE books — prep for the
combined metadata re-upload. Isolated per book: one failure never aborts the
batch. Logs a per-book verdict."""
import json
import sys
import traceback
from pathlib import Path

import seo_optimizer

KDP = Path("/root/kdp")


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
    skip = set(sys.argv[1].split(",")) if len(sys.argv) > 1 else set()
    todo = [s for s in live_slugs() if s not in skip]
    print(f"SEO re-optimizing {len(todo)} LIVE book(s)\n", flush=True)
    ok = 0
    for i, slug in enumerate(todo, 1):
        try:
            a = seo_optimizer.optimize(slug, dry_run=False)
            kw = len(a.get("keywords", []))
            cats = len(a.get("categories", []))
            print(f"[{i}/{len(todo)}] OK   {slug} — {kw} kw, {cats} cats", flush=True)
            ok += 1
        except Exception as e:
            print(f"[{i}/{len(todo)}] FAIL {slug} — {e}", flush=True)
            traceback.print_exc()
    print(f"\nSEO BATCH DONE: {ok}/{len(todo)} optimized", flush=True)


if __name__ == "__main__":
    main()
