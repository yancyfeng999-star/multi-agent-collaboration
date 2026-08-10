"""Task/resource conflict fingerprints used by the bounded coordinator."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from protocol_lib import paths_overlap, resolve_protocol_path


def _list(task: dict[str, Any], field: str) -> list[str]:
    value = task.get(field, "[]")
    if isinstance(value, list):
        return [str(item) for item in value]
    if value in {None, "", "null"}:
        return []
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _overlap(left: str, right: str) -> bool:
    if left == right:
        return True
    left_prefix = left.rstrip("/") + "/"
    right_prefix = right.rstrip("/") + "/"
    return left.startswith(right_prefix) or right.startswith(left_prefix)


def conflict_fingerprint(task: dict[str, Any], project_root: str | Path) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    workspace = str(task.get("workspace", "") or "")
    workspace_policy = str(task.get("workspace_policy", "isolated_writer"))
    # New task documents always declare a workspace policy.  Treat an omitted
    # workspace under the writer policy as the shared project root so the
    # scheduler never invents parallel writers in one implicit worktree.  Old
    # Protocol v3 task documents without this field retain their legacy
    # path-only behavior.
    if not workspace and "workspace_policy" in task and workspace_policy == "isolated_writer":
        workspace = str(root)
    return {
        "task_id": str(task.get("task_id", "")),
        "owned_paths": [str(resolve_protocol_path(path, root)) for path in _list(task, "owned_paths")],
        "logical_resources": sorted(set(_list(task, "logical_resources"))),
        "environment_resources": sorted(set(_list(task, "environment_resources"))),
        "workspace": str(resolve_protocol_path(workspace, root)) if workspace else "",
        "workspace_policy": workspace_policy,
        "release_lane": str(task.get("release_lane", "none") or "none"),
    }


def find_conflict(
    candidate: dict[str, Any],
    active_tasks: list[dict[str, Any]],
    project_root: str | Path,
) -> str | None:
    candidate_fp = conflict_fingerprint(candidate, project_root)
    for active in active_tasks:
        active_fp = conflict_fingerprint(active, project_root)
        active_id = active_fp["task_id"] or "unknown"
        if candidate_fp["task_id"] == active_id:
            return f"task_conflict:{active_id}"
        if any(
            paths_overlap(left, right, Path(project_root).expanduser().resolve())
            for left in candidate_fp["owned_paths"]
            for right in active_fp["owned_paths"]
        ):
            return f"owned_path_conflict:{active_id}"
        if any(_overlap(left, right) for left in candidate_fp["logical_resources"] for right in active_fp["logical_resources"]):
            return f"logical_resource_conflict:{active_id}"
        if any(_overlap(left, right) for left in candidate_fp["environment_resources"] for right in active_fp["environment_resources"]):
            return f"environment_resource_conflict:{active_id}"
        if (
            candidate_fp["release_lane"] != "none"
            and candidate_fp["release_lane"] == active_fp["release_lane"]
        ):
            return f"release_lane_conflict:{active_id}"
        if (
            candidate_fp["workspace"]
            and candidate_fp["workspace"] == active_fp["workspace"]
            and "isolated_writer" in {candidate_fp["workspace_policy"], active_fp["workspace_policy"]}
        ):
            return f"workspace_conflict:{active_id}"
    return None
