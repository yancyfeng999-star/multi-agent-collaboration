#!/usr/bin/env python3
"""Validate generic release freezes and evidence layers without inferring success."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from protocol_lib import ProtocolError, json_string_list, scalar_map, valid_iso8601


SCHEMA_VERSION = "1.0"
FREEZE_FIELDS = {
    "schema_version",
    "freeze_id",
    "active",
    "canonical_branch",
    "canonical_commit",
    "scope_paths",
    "reason",
    "created_at",
    "expires_at",
}
EVIDENCE_STATUSES = {"verified", "not_verified", "blocked_unknown", "failed", "not_applicable"}
COMMIT_RE = re.compile(r"^[0-9a-fA-F]{7,64}$")
ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def _string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"{field} must be a non-empty string")
    return value


def _commit(value: Any, *, field: str) -> str:
    value = _string(value, field=field)
    if not COMMIT_RE.fullmatch(value):
        raise ProtocolError(f"{field} must be a hexadecimal Git commit id")
    return value.lower()


def _relative_path(value: Any, *, field: str) -> str:
    value = _string(value, field=field)
    if value.startswith(("/", "~")) or "\\" in value or "\x00" in value:
        raise ProtocolError(f"{field} must be a relative POSIX path")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise ProtocolError(f"{field} contains an escaping path segment")
    return value


def _list(value: Any, *, field: str) -> list[str]:
    if isinstance(value, str):
        parsed = json.loads(value)
    else:
        parsed = value
    if not isinstance(parsed, list) or not all(isinstance(item, str) and item for item in parsed):
        raise ProtocolError(f"{field} must be a list of non-empty strings")
    if len(parsed) != len(set(parsed)):
        raise ProtocolError(f"{field} must not contain duplicates")
    return list(parsed)


def _bool(value: Any, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if value == "true":
        return True
    if value == "false":
        return False
    raise ProtocolError(f"{field} must be true or false")


def _load_flat_or_json(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if text.lstrip().startswith("{"):
        value = json.loads(text)
    else:
        values = scalar_map(text, source=str(path))
        value = dict(values)
    if not isinstance(value, dict):
        raise ProtocolError(f"{path} must contain a mapping")
    return value


def load_release_freeze(path: str | Path, project_root: str | Path | None = None) -> dict[str, Any]:
    freeze_path = Path(path).expanduser().resolve()
    if not freeze_path.is_file():
        raise ProtocolError(f"release freeze is not a regular file: {freeze_path}")
    raw = _load_flat_or_json(freeze_path)
    unknown = set(raw) - FREEZE_FIELDS
    if unknown:
        raise ProtocolError(f"release freeze has unknown fields: {', '.join(sorted(unknown))}")
    missing = FREEZE_FIELDS - raw.keys()
    if missing:
        raise ProtocolError(f"release freeze is missing fields: {', '.join(sorted(missing))}")
    if raw["schema_version"] != SCHEMA_VERSION:
        raise ProtocolError(f"release freeze schema_version must be {SCHEMA_VERSION}")
    freeze_id = _string(raw["freeze_id"], field="freeze_id")
    if not ID_RE.fullmatch(freeze_id):
        raise ProtocolError("freeze_id contains unsupported characters")
    active = _bool(raw["active"], field="active")
    branch = _string(raw["canonical_branch"], field="canonical_branch")
    if branch.startswith(("/", "-")) or any(char.isspace() for char in branch) or ".." in branch or "@{" in branch:
        raise ProtocolError("canonical_branch is not a safe branch name")
    scope_raw = raw["scope_paths"]
    scope_values = _list(scope_raw, field="scope_paths")
    scope_paths = [_relative_path(value, field=f"scope_paths[{index}]") for index, value in enumerate(scope_values)]
    created_at = _string(raw["created_at"], field="created_at")
    expires_at = _string(raw["expires_at"], field="expires_at")
    if not valid_iso8601(created_at) or not valid_iso8601(expires_at):
        raise ProtocolError("release freeze created_at/expires_at must be timezone-aware ISO timestamps")
    result = {
        "schema_version": SCHEMA_VERSION,
        "freeze_id": freeze_id,
        "active": active,
        "canonical_branch": branch,
        "canonical_commit": _commit(raw["canonical_commit"], field="canonical_commit"),
        "scope_paths": scope_paths,
        "reason": _string(raw["reason"], field="reason"),
        "created_at": created_at,
        "expires_at": expires_at,
        "freeze_path": str(freeze_path),
        "read_only": True,
        "write_performed": False,
    }
    if project_root is not None:
        root = Path(project_root).expanduser().resolve()
        if not root.is_dir():
            raise ProtocolError(f"project_root must be an existing directory: {root}")
        result["project_root"] = str(root)
    return result


def freeze_active(freeze: dict[str, Any], *, now: datetime | None = None) -> bool:
    if not bool(freeze.get("active", False)):
        return False
    expires_at = freeze.get("expires_at")
    if not isinstance(expires_at, str) or not valid_iso8601(expires_at):
        raise ProtocolError("release freeze expires_at is missing or invalid")
    current = now or datetime.now(timezone.utc)
    expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
    return expiry > current


def canonical_movement_gate(
    freeze: dict[str, Any],
    canonical_branch: str,
    current_commit: str,
    *,
    requested_scope_paths: list[str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return a fail-closed canonical movement decision.

    A scope change never disables an active freeze or repairs a canonical
    commit mismatch; both remain visible in the blocker list.
    """

    active = freeze_active(freeze, now=now)
    blockers: list[str] = []
    if active:
        blockers.append("release_freeze_active")
        if freeze.get("canonical_branch") != canonical_branch:
            blockers.append("canonical_branch_mismatch")
        if str(current_commit).lower() != str(freeze.get("canonical_commit", "")).lower():
            blockers.append("canonical_commit_mismatch")
    return {
        "allowed": not blockers,
        "active": active,
        "canonical_branch": canonical_branch,
        "current_commit": current_commit,
        "requested_scope_paths": requested_scope_paths or [],
        "blockers": blockers,
        "read_only": True,
        "write_performed": False,
    }


def _layer(value: dict[str, Any] | None, *, field: str) -> dict[str, Any]:
    if value is None:
        return {"status": "not_verified", "commit": None, "evidence_refs": []}
    if not isinstance(value, dict):
        raise ProtocolError(f"evidence layer {field} must be an object")
    allowed = {"status", "commit", "evidence_refs"}
    unknown = set(value) - allowed
    if unknown:
        raise ProtocolError(f"evidence layer {field} has unknown fields: {', '.join(sorted(unknown))}")
    status = _string(value.get("status"), field=f"{field}.status")
    if status not in EVIDENCE_STATUSES:
        raise ProtocolError(f"evidence layer {field}.status is invalid")
    commit = value.get("commit")
    if commit is not None:
        commit = _commit(commit, field=f"{field}.commit")
    refs = value.get("evidence_refs", [])
    refs = _list(refs, field=f"{field}.evidence_refs") if refs else []
    return {"status": status, "commit": commit, "evidence_refs": refs}


def _evidence_entries(value: list[dict[str, Any]] | None, *, field: str, identity_key: str) -> list[dict[str, Any]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ProtocolError(f"evidence {field} must be a list")
    entries: list[dict[str, Any]] = []
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            raise ProtocolError(f"evidence {field}[{index}] must be an object")
        allowed = {identity_key, "status", "commit", "evidence_ref"}
        unknown = set(item) - allowed
        if unknown:
            raise ProtocolError(f"evidence {field}[{index}] has unknown fields: {', '.join(sorted(unknown))}")
        identity = _string(item.get(identity_key), field=f"{field}[{index}].{identity_key}")
        status = _string(item.get("status"), field=f"{field}[{index}].status")
        if status not in EVIDENCE_STATUSES:
            raise ProtocolError(f"evidence {field}[{index}].status is invalid")
        entry: dict[str, Any] = {identity_key: identity, "status": status}
        if item.get("commit") is not None:
            entry["commit"] = _commit(item["commit"], field=f"{field}[{index}].commit")
        if item.get("evidence_ref") is not None:
            entry["evidence_ref"] = _string(item["evidence_ref"], field=f"{field}[{index}].evidence_ref")
        entries.append(entry)
    return entries


def build_evidence_layers(
    candidate_id: str,
    candidate_commit: str | None,
    *,
    local: dict[str, Any] | None = None,
    candidate: dict[str, Any] | None = None,
    quality: dict[str, Any] | None = None,
    canonical: dict[str, Any] | None = None,
    deployments: list[dict[str, Any]] | None = None,
    external_acceptance: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    candidate_id = _string(candidate_id, field="candidate_id")
    if not ID_RE.fullmatch(candidate_id):
        raise ProtocolError("candidate_id contains unsupported characters")
    normalized_commit = None if candidate_commit is None else _commit(candidate_commit, field="candidate_commit")
    result = {
        "schema_version": SCHEMA_VERSION,
        "candidate_id": candidate_id,
        "candidate_commit": normalized_commit,
        "local": _layer(local, field="local"),
        "candidate": _layer(candidate, field="candidate"),
        "quality": _layer(quality, field="quality"),
        "canonical": _layer(canonical, field="canonical"),
        "deployments": _evidence_entries(deployments, field="deployments", identity_key="environment"),
        "external_acceptance": _evidence_entries(external_acceptance, field="external_acceptance", identity_key="acceptor"),
        "read_only": True,
        "write_performed": False,
    }
    return result


def validate_evidence_layers(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("evidence layers must be an object")
    required = {"schema_version", "candidate_id", "candidate_commit", "local", "candidate", "quality", "canonical", "deployments", "external_acceptance"}
    missing = required - value.keys()
    if missing:
        raise ProtocolError(f"evidence layers are missing fields: {', '.join(sorted(missing))}")
    return build_evidence_layers(
        value["candidate_id"],
        value["candidate_commit"],
        local=value["local"],
        candidate=value["candidate"],
        quality=value["quality"],
        canonical=value["canonical"],
        deployments=value["deployments"],
        external_acceptance=value["external_acceptance"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--candidate-commit", required=True)
    parser.add_argument("--evidence-json")
    args = parser.parse_args()
    if args.evidence_json:
        try:
            provided = json.loads(Path(args.evidence_json).read_text(encoding="utf-8"))
            result = validate_evidence_layers(provided)
        except (OSError, json.JSONDecodeError, ProtocolError) as error:
            parser.error(str(error))
    else:
        result = build_evidence_layers(args.candidate_id, args.candidate_commit)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
