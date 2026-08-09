#!/usr/bin/env python3
"""Validate an agent binding and record one immutable wake operation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

from adapters import codex, document, hermes
from claim_lib import active_task_claim, effective_owner
from protocol_lib import frontmatter, parse_agent_profiles, path_within, scalar_map, sha256


def _operation_id(run_id: str, task_id: str, agent_id: str, task_hash: str) -> str:
    material = f"{run_id}\0{task_id}\0{agent_id}\0{task_hash}".encode()
    return "WAKE-" + hashlib.sha256(material).hexdigest()[:24]


def _load_project(run_dir: Path) -> tuple[Path, list[str]]:
    project_path = run_dir.parent.parent / "project.yaml"
    values = scalar_map(project_path.read_text(encoding="utf-8"), source=str(project_path))
    root = Path(values["project_root"]).expanduser().resolve()
    allowed = json.loads(values.get("allowed_roots", "[]"))
    return root, allowed


def _validate_mapping(path: Path, agent_id: str, workspace: Path, platform: str) -> dict[str, Any]:
    mapping = json.loads(path.read_text(encoding="utf-8"))
    active = mapping.get("active") or {}
    if mapping.get("agent_id") != agent_id:
        raise ValueError("SESSION_MAP identity does not match agent")
    if Path(active.get("workspace", "")).expanduser().resolve() != workspace:
        raise ValueError("SESSION_MAP workspace does not match project workspace")
    if active.get("platform") != platform:
        raise ValueError("SESSION_MAP platform does not match requested adapter")
    if not active.get("session_id"):
        raise ValueError("SESSION_MAP has no active session identity")
    return mapping


def wake_agent(
    run_dir: str | Path,
    task_id: str,
    agent_id: str,
    *,
    requested_adapter: str | None = None,
    session_map: str | Path | None = None,
    hermes_command: Sequence[str] | None = None,
    codex_command: Sequence[str] | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    run_dir = Path(run_dir).expanduser().resolve()
    manifest = scalar_map((run_dir / "manifest.yaml").read_text(encoding="utf-8"), source="manifest")
    agents = parse_agent_profiles((run_dir / "agents.yaml").read_text(encoding="utf-8"), source="agents")
    task_path = run_dir / "tasks" / f"{task_id}.md"
    task = frontmatter(task_path)
    resolved_owner = effective_owner(run_dir, task)
    if resolved_owner != agent_id or agent_id not in agents:
        raise ValueError("wake identity is not the registered task owner")
    workspace, allowed_roots = _load_project(run_dir)
    if not workspace.is_dir():
        raise ValueError("project workspace does not exist")
    for owned in json.loads(task.get("owned_paths", "[]")):
        if not path_within(owned, allowed_roots, workspace):
            raise ValueError(f"owned path exceeds project roots: {owned}")
    runtime = str(agents[agent_id].get("runtime", "document"))
    adapter = requested_adapter or ("codex" if runtime.startswith("codex") else "document")
    if adapter in {"hermes", "codex"}:
        if session_map is None and (hermes_command if adapter == "hermes" else codex_command):
            raise ValueError("external wake requires SESSION_MAP validation")
        if session_map is not None:
            _validate_mapping(Path(session_map), agent_id, workspace, adapter)
    task_hash = sha256(task_path)
    claim = active_task_claim(run_dir, task_id)
    operation = {
        "protocol_version": 3,
        "kind": "wake_operation",
        "operation_id": _operation_id(manifest["run_id"], task_id, agent_id, task_hash),
        "run_id": manifest["run_id"], "task_id": task_id, "agent_id": agent_id,
        "adapter": adapter, "workspace": str(workspace), "task_path": str(task_path),
        "task_sha256": task_hash, "owned_paths": json.loads(task.get("owned_paths", "[]")),
        "forbidden_paths": json.loads(task.get("forbidden_paths", "[]")),
        "claim_id": claim.get("claim_id") if claim else None,
        "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }
    operation_path = run_dir / "operations" / f'{operation["operation_id"]}.json'
    stable = {key: value for key, value in operation.items() if key != "created_at"}
    if operation_path.exists():
        existing = json.loads(operation_path.read_text(encoding="utf-8"))
        if {key: value for key, value in existing.items() if key != "created_at"} != stable:
            raise ValueError(f"immutable wake operation collision: {operation_path}")
        operation = existing
    elif not dry_run:
        operation_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = operation_path.with_name(f".{operation_path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(operation, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temporary, operation_path)

    try:
        if adapter == "document":
            result = document.dispatch(run_dir, operation, dry_run=dry_run)
        elif adapter == "hermes":
            result = hermes.dispatch(operation, hermes_command)
        elif adapter == "codex":
            result = codex.dispatch(operation, codex_command)
        else:
            result = {"adapter": adapter, "status": "unsupported", "reason": "unknown adapter"}
    except (OSError, ValueError, RuntimeError) as exc:
        result = {"adapter": adapter, "status": "failed", "reason": str(exc)}
    if result["status"] in {"unsupported", "failed"}:
        unsupported = adapter
        try:
            result = document.dispatch(run_dir, operation, dry_run=dry_run)
            result["status"] = "fallback_document" if not dry_run else "planned_fallback_document"
            result["unsupported_adapter"] = unsupported
        except (OSError, ValueError, RuntimeError) as exc:
            result = {"adapter": adapter, "status": "failed", "reason": str(exc), "unsupported_adapter": unsupported}
    result.update({"operation_id": operation["operation_id"], "operation_path": str(operation_path)})
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True); parser.add_argument("--task-id", required=True)
    parser.add_argument("--agent-id", required=True); parser.add_argument("--adapter", choices=("document", "hermes", "codex"))
    parser.add_argument("--session-map"); parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(wake_agent(args.run_dir, args.task_id, args.agent_id, requested_adapter=args.adapter, session_map=args.session_map, dry_run=args.dry_run), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
