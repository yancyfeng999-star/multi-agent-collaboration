#!/usr/bin/env python3
"""Build a release-candidate summary without granting release permission."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from preflight_lib import _load_context, _latest_result, run_completion_preflight
from protocol_lib import json_string_list, sha256


def build_candidate_index(run_dir_value: str | Path, task_ids: list[str] | None = None) -> dict[str, Any]:
    context = _load_context(run_dir_value)
    manifest = context["manifest"]
    selected = sorted(task_ids or [task_id for task_id, state in context["states"].items() if state in {"completed", "release_ready"}])
    candidates: list[dict[str, Any]] = []
    event_map: dict[str, set[str]] = {}
    for _, event in context["records"]:
        task_id = event.get("task_id")
        if task_id:
            event_map.setdefault(task_id, set()).add(event.get("event", ""))
    for task_id in selected:
        pair = context["tasks"].get(task_id)
        if pair is None:
            candidates.append({"task_id": task_id, "blocked_reason": "task_missing"})
            continue
        _, task = pair
        result_pair = _latest_result(context["run_dir"], task)
        result_path = result_pair[0] if result_pair else None
        result = result_pair[1] if result_pair else {}
        completion = run_completion_preflight(context["run_dir"], task_id)
        permission = "not_applicable"
        if manifest.get("release_authorization_ref") not in {"", "null", None}:
            permission = "granted" if manifest.get("release_environment") not in {"", "null", None} else "missing"
        elif manifest.get("governance") == "strict":
            permission = "missing"
        blocked = completion.get("missing") or completion.get("blocked_by")
        candidates.append(
            {
                "release_scope_id": manifest.get("release_train_id"),
                "task_id": task_id,
                "change_id": manifest.get("change_id"),
                "implementation_commit": result.get("implementation_commit"),
                "handoff_ref": str(result_path) if "HANDOFF_READY" in event_map.get(task_id, set()) and result_path else None,
                "review_ref": "review evidence" if "REVIEW_APPROVED" in event_map.get(task_id, set()) else None,
                "qa_ref": "qa evidence" if "QA_PASSED" in event_map.get(task_id, set()) else None,
                "release_permission": permission,
                "target_environment": manifest.get("release_environment"),
                "blocked_reason": blocked or None,
                "task_sha256": sha256(pair[0]),
            }
        )
    return {
        "schema_version": "1.0",
        "run_id": manifest.get("run_id"),
        "governance": manifest.get("governance"),
        "versioning_mode": manifest.get("versioning_mode"),
        "release_candidates": candidates,
        "release_authority": "project_release_adapter",
        "dry_run": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true", help="The command is always read-only")
    args = parser.parse_args()
    print(json.dumps(build_candidate_index(args.run_dir, args.task_id or None), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
