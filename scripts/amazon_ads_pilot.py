#!/usr/bin/env python3
"""Build the Libra Amazon Ads pilot gate; no live adapter is configured here."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from amazon_ads_pilot import build_pilot_gate


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} must contain an object")
    return value


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inputs", type=Path, default=ROOT / "data/amazon_ads_pilot_inputs.json")
    parser.add_argument("--retail", type=Path, default=ROOT / "data/retail_readiness_latest.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/amazon_ads_pilot_latest.json")
    parser.add_argument("--as-of", type=datetime.fromisoformat)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm")
    args = parser.parse_args(argv)

    try:
        inputs = _load(args.inputs)
        retail = _load(args.retail)
    except (OSError, json.JSONDecodeError, ValueError) as error:
        print(json.dumps({"status": "ERROR", "reason": "input_unavailable", "error_class": error.__class__.__name__}))
        return 1
    observed = args.as_of or datetime.now().astimezone()
    gate = build_pilot_gate(inputs, retail, observed)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_name(f".{args.output.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, args.output)
    print(json.dumps(gate, ensure_ascii=False, indent=2))

    if args.apply:
        expected = (gate.get("plan") or {}).get("idempotency_key")
        if gate.get("status") != "APPLY_READY":
            print(json.dumps({"status": "BLOCKED", "reason": "pilot_gate_not_apply_ready"}))
            return 2
        if not expected or args.confirm != expected:
            print(json.dumps({"status": "BLOCKED", "reason": "exact_confirmation_missing"}))
            return 2
        print(json.dumps({"status": "BLOCKED", "reason": "no_live_write_adapter_configured"}))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
