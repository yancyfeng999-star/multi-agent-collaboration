#!/usr/bin/env python3
"""Run a read-only completion preflight for one task."""

from __future__ import annotations

import argparse
import json

from preflight_lib import run_completion_preflight


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--dry-run", action="store_true", help="Report only; preflight is always read-only")
    args = parser.parse_args()
    report = run_completion_preflight(args.run_dir, args.task_id)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report.get("ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
