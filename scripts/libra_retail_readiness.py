#!/usr/bin/env python3
"""Generate an atomic, read-only Libra retail-readiness report."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from libra_retail_readiness import audit_portfolio


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, default=ROOT / "data/retail_readiness_evidence.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/retail_readiness_latest.json")
    parser.add_argument("--kdp-root", type=Path, default=Path("/root/kdp"))
    parser.add_argument("--ledger", type=Path, default=ROOT / "data/libra-business.db")
    args = parser.parse_args(argv)

    try:
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        print(json.dumps({"status": "ERROR", "reason": "evidence_unavailable", "error_class": error.__class__.__name__}))
        return 1
    if not isinstance(evidence, dict):
        print(json.dumps({"status": "ERROR", "reason": "evidence_not_object"}))
        return 1

    report = audit_portfolio(evidence, args.kdp_root, args.ledger)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
