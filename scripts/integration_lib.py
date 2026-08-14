#!/usr/bin/env python3
"""Shared candidate and Git facts for the serial integration lane."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable

from protocol_lib import ProtocolError


CANDIDATE_SCHEMA_VERSION = "1.0"
CANDIDATE_FIELDS = {
    "schema_version",
    "candidate_id",
    "baseline_commit",
    "candidate_commit",
    "changed_paths",
    "verification",
    "risk_flags",
    "owner",
    "status",
    "dependencies",
    "logical_resources",
    "environment_resources",
    "workspace",
    "version_source",
    "migration_order",
    "release_lane",
    "quality_required",
    "quality_status",
}
VERIFICATION_FIELDS = {"command", "status", "completed_at", "evidence_ref"}
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
SHELL_META_RE = re.compile(r"[;&|$<>`\\\n\r(){}!]")


def _require_string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"candidate {field} must be a non-empty string")
    return value


def _commit(value: Any, *, field: str) -> str:
    value = _require_string(value, field=field)
    if not COMMIT_RE.fullmatch(value):
        raise ProtocolError(f"candidate {field} must be a hexadecimal Git commit id")
    return value.lower()


def _relative_path(value: Any, *, field: str) -> str:
    value = _require_string(value, field=field)
    if value.startswith(("/", "~")) or "\\" in value or "\x00" in value:
        raise ProtocolError(f"candidate {field} must be a relative POSIX path")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ProtocolError(f"candidate {field} contains an escaping or empty path segment")
    return value


def _string_list(value: Any, *, field: str, allow_empty: bool = True) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ProtocolError(f"candidate {field} must be a list of non-empty strings")
    if not allow_empty and not value:
        raise ProtocolError(f"candidate {field} cannot be empty")
    if len(value) != len(set(value)):
        raise ProtocolError(f"candidate {field} must not contain duplicates")
    return list(value)


def _safe_argv(value: Any, *, field: str) -> list[str]:
    values = _string_list(value, field=field, allow_empty=False)
    for index, token in enumerate(values):
        if token.startswith(("/", "~")) or "\x00" in token or SHELL_META_RE.search(token):
            raise ProtocolError(f"candidate {field}[{index}] contains unsafe command syntax")
        if any(part == ".." for part in re.split(r"[/\\]", token)):
            raise ProtocolError(f"candidate {field}[{index}] contains a path escape")
    return values


def _verification(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ProtocolError("candidate verification must contain at least one record")
    records: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ProtocolError(f"candidate verification[{index}] must be an object")
        unknown = set(item) - VERIFICATION_FIELDS
        if unknown:
            raise ProtocolError(f"candidate verification[{index}] has unknown fields: {', '.join(sorted(unknown))}")
        required = {"command", "status", "completed_at"}
        if required - item.keys():
            raise ProtocolError(f"candidate verification[{index}] is missing fields: {', '.join(sorted(required - item.keys()))}")
        status = _require_string(item["status"], field=f"verification[{index}].status")
        if status not in {"passed", "failed", "not_run"}:
            raise ProtocolError(f"candidate verification[{index}].status is invalid")
        completed_at = _require_string(item["completed_at"], field=f"verification[{index}].completed_at")
        record: dict[str, Any] = {
            "command": _safe_argv(item["command"], field=f"verification[{index}].command"),
            "status": status,
            "completed_at": completed_at,
        }
        if "evidence_ref" in item:
            record["evidence_ref"] = _require_string(item["evidence_ref"], field=f"verification[{index}].evidence_ref")
        records.append(record)
    return records


def load_candidate(path: str | Path) -> dict[str, Any]:
    candidate_path = Path(path).expanduser().resolve()
    try:
        value = json.loads(candidate_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot parse candidate JSON {candidate_path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError(f"candidate must be a JSON object: {candidate_path}")
    unknown = set(value) - CANDIDATE_FIELDS
    if unknown:
        raise ProtocolError(f"candidate has unknown fields: {', '.join(sorted(unknown))}")
    required = CANDIDATE_FIELDS
    missing = required - value.keys()
    if missing:
        raise ProtocolError(f"candidate is missing fields: {', '.join(sorted(missing))}")
    if value.get("schema_version") != CANDIDATE_SCHEMA_VERSION:
        raise ProtocolError(f"candidate schema_version must be {CANDIDATE_SCHEMA_VERSION}")
    candidate_id = _require_string(value["candidate_id"], field="candidate_id")
    if not ID_RE.fullmatch(candidate_id):
        raise ProtocolError("candidate candidate_id contains unsupported characters")
    changed_paths = [_relative_path(item, field=f"changed_paths[{index}]") for index, item in enumerate(value["changed_paths"])] if isinstance(value["changed_paths"], list) else None
    if changed_paths is None or len(changed_paths) != len(set(changed_paths)):
        raise ProtocolError("candidate changed_paths must be a unique list of relative paths")
    quality_required = value["quality_required"]
    if not isinstance(quality_required, bool):
        raise ProtocolError("candidate quality_required must be boolean")
    quality_status = _require_string(value["quality_status"], field="quality_status")
    if quality_status not in {"not_required", "passed", "failed", "unknown"}:
        raise ProtocolError("candidate quality_status is invalid")
    if quality_required and quality_status == "not_required":
        raise ProtocolError("quality_required candidate cannot use not_required quality_status")
    if not quality_required and quality_status != "not_required":
        raise ProtocolError("quality_status must be not_required when quality_required is false")
    migration_order = value["migration_order"]
    if isinstance(migration_order, bool) or not isinstance(migration_order, int) or migration_order < 0:
        raise ProtocolError("candidate migration_order must be a non-negative integer")
    status = _require_string(value["status"], field="status")
    if status not in {"ready", "submitted", "integrated", "deferred", "blocked"}:
        raise ProtocolError("candidate status is invalid")
    version_source = value["version_source"]
    if version_source is not None and (not isinstance(version_source, str) or not version_source):
        raise ProtocolError("candidate version_source must be a non-empty string or null")
    normalized = {
        "schema_version": CANDIDATE_SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "baseline_commit": _commit(value["baseline_commit"], field="baseline_commit"),
        "candidate_commit": _commit(value["candidate_commit"], field="candidate_commit"),
        "changed_paths": changed_paths,
        "verification": _verification(value["verification"]),
        "risk_flags": _string_list(value["risk_flags"], field="risk_flags"),
        "owner": _require_string(value["owner"], field="owner"),
        "status": status,
        "dependencies": _string_list(value["dependencies"], field="dependencies"),
        "logical_resources": _string_list(value["logical_resources"], field="logical_resources"),
        "environment_resources": _string_list(value["environment_resources"], field="environment_resources"),
        "workspace": _require_string(value["workspace"], field="workspace"),
        "version_source": version_source,
        "migration_order": migration_order,
        "release_lane": _require_string(value["release_lane"], field="release_lane"),
        "quality_required": quality_required,
        "quality_status": quality_status,
        "candidate_path": str(candidate_path),
    }
    return normalized


def git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(root),
        capture_output=True,
        text=True,
        check=False,
    )


def git_output(root: Path, *args: str) -> str:
    result = git(root, *args)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ProtocolError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout.strip()


def resolve_commit(root: Path, commit: str) -> str:
    result = git(root, "rev-parse", "--verify", f"{commit}^{{commit}}")
    if result.returncode:
        raise ProtocolError(f"Git commit is not available: {commit}")
    return result.stdout.strip()


def is_ancestor(root: Path, ancestor: str, descendant: str) -> bool:
    return git(root, "merge-base", "--is-ancestor", ancestor, descendant).returncode == 0


def changed_paths(root: Path, baseline: str, candidate: str) -> list[str]:
    result = git(root, "diff", "--name-only", "-z", baseline, candidate, "--")
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ProtocolError(f"cannot inspect candidate diff: {detail}")
    return sorted(item for item in result.stdout.split("\0") if item)


def branch_commit(root: Path, branch: str) -> str:
    return resolve_commit(root, f"refs/heads/{branch}")


def common_git_dir(root: Path) -> Path:
    raw = git_output(root, "rev-parse", "--git-common-dir")
    path = Path(raw)
    if not path.is_absolute():
        path = root / path
    return path.resolve()


def branch_checked_out(root: Path, branch: str) -> bool:
    result = git(root, "worktree", "list", "--porcelain")
    if result.returncode:
        raise ProtocolError(f"cannot inspect Git worktrees: {result.stderr.strip()}")
    expected = f"refs/heads/{branch}"
    return any(line.strip() == f"branch {expected}" for line in result.stdout.splitlines())


def worktree_clean(root: Path) -> bool:
    result = git(root, "status", "--porcelain", "--untracked-files=all")
    if result.returncode:
        raise ProtocolError(f"cannot inspect worktree status: {result.stderr.strip()}")
    return not result.stdout.strip()


def paths_overlap(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _touches_high_conflict(candidate: dict[str, Any], path: str) -> bool:
    return any(paths_overlap(changed, path) for changed in candidate["changed_paths"])


def conflict_dimensions(left: dict[str, Any], right: dict[str, Any], high_conflict_paths: Iterable[str]) -> list[str]:
    dimensions: list[str] = []
    if any(paths_overlap(a, b) for a in left["changed_paths"] for b in right["changed_paths"]):
        dimensions.append("changed_paths")
    high_paths = list(high_conflict_paths)
    if any(_touches_high_conflict(left, path) and _touches_high_conflict(right, path) for path in high_paths):
        dimensions.append("high_conflict_paths")
    if left["dependencies"] and right["candidate_id"] in left["dependencies"]:
        dimensions.append("dependencies")
    if right["dependencies"] and left["candidate_id"] in right["dependencies"] and "dependencies" not in dimensions:
        dimensions.append("dependencies")
    if set(left["logical_resources"]) & set(right["logical_resources"]):
        dimensions.append("logical_resources")
    if set(left["environment_resources"]) & set(right["environment_resources"]):
        dimensions.append("environment_resources")
    if left["workspace"] == right["workspace"]:
        dimensions.append("workspace")
    if left["version_source"] and left["version_source"] == right["version_source"]:
        dimensions.append("version_source")
    if left["migration_order"] and left["migration_order"] == right["migration_order"]:
        dimensions.append("migration_order")
    if left["release_lane"] != "none" and left["release_lane"] == right["release_lane"]:
        dimensions.append("release_lane")
    return dimensions


def read_freeze(path: str | Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    freeze_path = Path(path).expanduser().resolve()
    try:
        value = json.loads(freeze_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot parse release freeze {freeze_path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProtocolError("release freeze must be a JSON object")
    return value
