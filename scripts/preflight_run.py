#!/usr/bin/env python3
"""Run a read-only, mode-aware preflight and print one JSON report."""

from __future__ import annotations

import argparse
import json
from preflight_lib import run_preflight


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--mode", choices=("light", "standard", "strict"))
    parser.add_argument("--dry-run", action="store_true", help="Report only; preflight is always read-only")
    args = parser.parse_args()
    report = run_preflight(args.run_dir, args.task_id or None)
    if args.mode and report.get("governance") not in {None, args.mode}:
        report["blocked_by"].append({"task_id": "run", "reason": f"mode_mismatch:{report.get('governance')}!={args.mode}"})
        report["ready"] = False
        report["next_action"] = "use_manifest_governance"
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    if report.get("governance") is None and report.get("next_action") == "repair_run_structure":
        return 3
    return 0 if report.get("ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
