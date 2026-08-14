#!/usr/bin/env python3
"""Load and validate an optional, project-specific integration adapter policy.

The policy is deliberately read-only.  It describes facts that the generic
Skill may use later (branch names, candidate submission permissions and
conflict hints); it never creates a branch, invokes a command or changes a
target project.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

from protocol_lib import ProtocolError, json_string_list, scalar_map, sha256


POLICY_SCHEMA_VERSION = "1.0"
ALLOWED_FIELDS = {
    "schema_version",
    "canonical_branch",
    "working_branch",
    "candidate_submit_mode",
    "candidate_submit_command",
    "candidate_complete_command",
    "high_conflict_paths",
    "version_authority",
    "release_freeze_supported",
    "integration_method",
}

DEFAULTS: dict[str, str] = {
    "candidate_submit_mode": "manual",
    "candidate_submit_command": "null",
    "candidate_complete_command": "null",
    "high_conflict_paths": "[]",
    "version_authority": "null",
    "release_freeze_supported": "false",
    "integration_method": "merge_preserve_candidate",
}

SHELL_META_RE = re.compile(r"[;&|$<>`\\\n\r(){}!]")
BRANCH_FORBIDDEN_RE = re.compile(r"[\x00-\x20~^:?*\\\[]")


class PolicyNotConfigured(ProtocolError):
    """Raised when no adapter policy exists; callers must remain read-only."""


def _string(raw: str, *, field: str, source: str, allow_empty: bool = False) -> str:
    if raw == "null" or raw.startswith(("[", "{")):
        raise ProtocolError(f"{source}: {field} must be a quoted string")
    value = raw
    if not isinstance(value, str) or value in {"true", "false"} or re.fullmatch(r"-?\d+", value):
        raise ProtocolError(f"{source}: {field} must be a quoted string")
    if not allow_empty and not value:
        raise ProtocolError(f"{source}: {field} cannot be empty")
    return value


def _optional_string(raw: str, *, field: str, source: str) -> str | None:
    if raw == "null":
        return None
    return _string(raw, field=field, source=source)


def _boolean(raw: str, *, field: str, source: str) -> bool:
    if raw == "true":
        return True
    if raw == "false":
        return False
    raise ProtocolError(f"{source}: {field} must be true or false")


def _enum(raw: str, *, field: str, source: str, allowed: set[str]) -> str:
    value = _string(raw, field=field, source=source)
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ProtocolError(f"{source}: {field} must be one of: {choices}")
    return value


def _validate_branch(value: str, *, field: str, source: str) -> str:
    if not value or value.startswith("-") or value.startswith("/") or value.endswith(("/", ".")):
        raise ProtocolError(f"{source}: {field} is not a safe Git branch name")
    if value.endswith(".lock") or ".." in value or "//" in value or "@{" in value:
        raise ProtocolError(f"{source}: {field} is not a safe Git branch name")
    if BRANCH_FORBIDDEN_RE.search(value):
        raise ProtocolError(f"{source}: {field} contains a forbidden Git branch character")
    return value


def _validate_relative_path(value: str, *, field: str, source: str) -> str:
    if not value or value.startswith(("/", "~")) or "\\" in value or "\x00" in value:
        raise ProtocolError(f"{source}: {field} must contain relative POSIX paths")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise ProtocolError(f"{source}: {field} contains an invalid or escaping path")
    return value


def _validate_command(command: list[str], *, field: str, source: str) -> list[str]:
    if not command:
        return []
    for index, token in enumerate(command):
        if not isinstance(token, str) or not token:
            raise ProtocolError(f"{source}: {field}[{index}] must be a non-empty string")
        if token.startswith(("/", "~")) or "\x00" in token or SHELL_META_RE.search(token):
            raise ProtocolError(
                f"{source}: {field}[{index}] must not contain an absolute path, shell syntax or control character"
            )
        if any(part == ".." for part in re.split(r"[/\\]", token)):
            raise ProtocolError(f"{source}: {field}[{index}] must not escape through '..'")
    return command


def _command(raw: str, *, field: str, source: str) -> list[str]:
    if raw == "null":
        return []
    if not raw.startswith("["):
        raise ProtocolError(f"{source}: {field} must be a JSON inline argv list or null")
    return _validate_command(json_string_list(raw, field=field, source=source), field=field, source=source)


def _paths(raw: str, *, field: str, source: str) -> list[str]:
    if not raw.startswith("["):
        raise ProtocolError(f"{source}: {field} must be a JSON inline path list")
    values = json_string_list(raw, field=field, source=source)
    normalized = [_validate_relative_path(value, field=f"{field}[{index}]", source=source) for index, value in enumerate(values)]
    if len(normalized) != len(set(normalized)):
        raise ProtocolError(f"{source}: {field} must not contain duplicate paths")
    return normalized


def _validate_version_authority(value: str | None, *, source: str) -> str | None:
    if value is None:
        return None
    if value.startswith(("/", "~")) or "\x00" in value or SHELL_META_RE.search(value):
        raise ProtocolError(f"{source}: version_authority must be an adapter reference, not a command/path escape")
    if any(part == ".." for part in re.split(r"[/\\]", value)):
        raise ProtocolError(f"{source}: version_authority must not contain '..'")
    return value


def load_integration_policy(policy_path: str | Path, project_root: str | Path) -> dict[str, Any]:
    """Return a normalized policy or fail closed before any project write."""

    source_path = Path(policy_path).expanduser()
    if not source_path.exists():
        raise PolicyNotConfigured(f"integration policy is not configured: {source_path}")
    if not source_path.is_file():
        raise ProtocolError(f"integration policy is not a regular file: {source_path}")
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ProtocolError(f"project_root must be an existing directory: {root}")
    path = source_path.resolve()
    source = str(path)
    raw = scalar_map(path.read_text(encoding="utf-8"), source=source)
    unknown = set(raw) - ALLOWED_FIELDS
    if unknown:
        raise ProtocolError(f"{source}: unknown integration policy fields: {', '.join(sorted(unknown))}")
    for field in ("schema_version", "canonical_branch", "working_branch"):
        if field not in raw:
            raise ProtocolError(f"{source}: missing required field {field}")

    schema_version = _string(raw["schema_version"], field="schema_version", source=source)
    if schema_version != POLICY_SCHEMA_VERSION:
        raise ProtocolError(f"{source}: unsupported schema_version {schema_version!r}")
    canonical_branch = _validate_branch(
        _string(raw["canonical_branch"], field="canonical_branch", source=source),
        field="canonical_branch",
        source=source,
    )
    working_branch = _validate_branch(
        _string(raw["working_branch"], field="working_branch", source=source),
        field="working_branch",
        source=source,
    )
    if canonical_branch == working_branch:
        raise ProtocolError(f"{source}: canonical_branch and working_branch must differ")

    values = {**DEFAULTS, **raw}
    candidate_submit_mode = _enum(
        values["candidate_submit_mode"],
        field="candidate_submit_mode",
        source=source,
        allowed={"manual", "authorized_auto"},
    )
    candidate_submit_command = _command(values["candidate_submit_command"], field="candidate_submit_command", source=source)
    candidate_complete_command = _command(values["candidate_complete_command"], field="candidate_complete_command", source=source)
    if candidate_submit_mode == "authorized_auto" and not candidate_submit_command:
        raise ProtocolError(f"{source}: authorized_auto requires candidate_submit_command")
    high_conflict_paths = _paths(values["high_conflict_paths"], field="high_conflict_paths", source=source)
    version_authority = _validate_version_authority(
        _optional_string(values["version_authority"], field="version_authority", source=source),
        source=source,
    )
    release_freeze_supported = _boolean(values["release_freeze_supported"], field="release_freeze_supported", source=source)
    integration_method = _enum(
        values["integration_method"],
        field="integration_method",
        source=source,
        allowed={"fast_forward_only", "merge_preserve_candidate"},
    )
    return {
        "schema_version": schema_version,
        "canonical_branch": canonical_branch,
        "working_branch": working_branch,
        "candidate_submit_mode": candidate_submit_mode,
        "candidate_submit_command": candidate_submit_command,
        "candidate_complete_command": candidate_complete_command,
        "high_conflict_paths": high_conflict_paths,
        "version_authority": version_authority,
        "release_freeze_supported": release_freeze_supported,
        "integration_method": integration_method,
        "policy_path": str(path),
        "policy_sha256": sha256(path),
        "project_root": str(root),
        "read_only": True,
        "write_performed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", required=True)
    parser.add_argument("--project-root", required=True)
    args = parser.parse_args()
    try:
        result = load_integration_policy(args.policy, args.project_root)
    except (OSError, ProtocolError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
