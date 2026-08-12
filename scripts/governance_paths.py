#!/usr/bin/env python3
"""Resolve external development-governance storage for a target project."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # Support package imports in tests and direct script imports at runtime.
    from .protocol_lib import ProtocolError, atomic_write, now_iso
except ImportError:  # pragma: no cover - exercised by top-level CLI imports.
    from protocol_lib import ProtocolError, atomic_write, now_iso


STORAGE_SCHEMA = "1.1"
SUPPORTED_STORAGE_SCHEMAS = {"1.0", STORAGE_SCHEMA}
DEFAULT_GOVERNANCE_PARTS = (".codex", "governance", "multi-agent-collaboration")


@dataclass(frozen=True)
class GovernancePaths:
    project_root: Path
    governance_root: Path
    project_id: str
    project_key: str
    project_dir: Path
    agents_dir: Path
    runs_dir: Path


def default_governance_root() -> Path:
    return Path.home().joinpath(*DEFAULT_GOVERNANCE_PARTS).resolve()


def _project_key(project_id: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", project_id.strip()).strip("-").lower()
    if not value:
        raise ProtocolError("project_id must contain at least one letter or digit")
    return value


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def _validate_separation(project_root: Path, governance_root: Path) -> None:
    if _is_relative_to(governance_root, project_root) or _is_relative_to(project_root, governance_root):
        raise ProtocolError("governance_root must stay outside the target project")


def resolve_governance_project(
    project_root: str | Path,
    project_id: str,
    governance_root: str | Path | None = None,
    *,
    require_existing: bool,
) -> GovernancePaths:
    project = Path(project_root).expanduser().resolve()
    if not project.is_dir():
        raise ProtocolError(f"project root does not exist: {project}")
    root = (
        default_governance_root()
        if governance_root is None
        else Path(governance_root).expanduser().resolve()
    )
    _validate_separation(project, root)
    key = _project_key(project_id)
    project_dir = root / "projects" / key
    binding_path = project_dir / "project-binding.yaml"
    if binding_path.is_file():
        existing = load_project_binding(project_dir)
        if existing["project_id"] != project_id or existing["project_root"] != str(project):
            suffix = hashlib.sha256(str(project).encode("utf-8")).hexdigest()[:10]
            key = f"{key}-{suffix}"
            project_dir = root / "projects" / key
    if require_existing:
        binding = load_project_binding(project_dir)
        if binding["project_id"] != project_id or binding["project_root"] != str(project):
            raise ProtocolError("governance project binding does not match the target project")
    return GovernancePaths(
        project_root=project,
        governance_root=root,
        project_id=project_id,
        project_key=key,
        project_dir=project_dir,
        agents_dir=project_dir / "agents",
        runs_dir=project_dir / "runs",
    )


def discover_governance_project(
    project_root: str | Path,
    governance_root: str | Path | None = None,
) -> GovernancePaths:
    """Find the single external binding for a project without guessing its ID."""
    project = Path(project_root).expanduser().resolve()
    root = (
        default_governance_root()
        if governance_root is None
        else Path(governance_root).expanduser().resolve()
    )
    _validate_separation(project, root)
    matches: list[dict[str, Any]] = []
    for binding_path in sorted((root / "projects").glob("*/project-binding.yaml")):
        try:
            binding = load_project_binding(binding_path.parent)
        except ProtocolError:
            continue
        if binding["project_root"] == str(project):
            matches.append(binding)
    if not matches:
        raise ProtocolError(f"no external governance binding found for project: {project}")
    if len(matches) > 1:
        raise ProtocolError("multiple governance bindings match the target project; pass --project-id")
    binding = matches[0]
    return resolve_governance_project(
        project,
        binding["project_id"],
        root,
        require_existing=True,
    )


def load_project_binding(governance_project_root: str | Path) -> dict[str, Any]:
    project_dir = Path(governance_project_root).expanduser().resolve()
    path = project_dir / "project-binding.yaml"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot read governance project binding: {path}") from exc
    required = {
        "storage_schema",
        "project_id",
        "project_name",
        "project_root",
        "project_key",
        "allowed_roots",
        "created_at",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ProtocolError(f"invalid governance project binding fields: {path}")
    if value["storage_schema"] not in SUPPORTED_STORAGE_SCHEMAS:
        raise ProtocolError(f"unsupported governance storage schema: {value['storage_schema']}")
    allowed_roots = value["allowed_roots"]
    if allowed_roots != [value["project_root"]]:
        raise ProtocolError(f"governance binding allowed_roots must contain only project_root: {path}")
    return value


def write_project_binding(paths: GovernancePaths, project_name: str) -> Path:
    if not project_name.strip():
        raise ProtocolError("project_name must not be empty")
    path = paths.project_dir / "project-binding.yaml"
    if path.exists():
        existing = load_project_binding(paths.project_dir)
        if existing["project_id"] != paths.project_id or existing["project_root"] != str(paths.project_root):
            raise ProtocolError("existing governance binding belongs to another project")
        return path
    value = {
        "storage_schema": STORAGE_SCHEMA,
        "project_id": paths.project_id,
        "project_name": project_name,
        "project_root": str(paths.project_root),
        "project_key": paths.project_key,
        "allowed_roots": [str(paths.project_root)],
        "created_at": now_iso(),
    }
    atomic_write(path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return path
