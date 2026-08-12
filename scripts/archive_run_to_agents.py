#!/usr/bin/env python3
"""Validate a terminal Protocol v3 Run and bridge immutable facts into persistent Agents."""
from __future__ import annotations

import argparse
from project_memory_lib import exclusive_lock
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from protocol_lib import ProtocolError, atomic_write, frontmatter, json_string_list, scalar_map, sha256
from governance_paths import load_project_binding

BRIDGE_VERSION = "1"


def persistent_task_id(run_id: str, task_id: str) -> str:
    run_part = run_id[4:] if run_id.startswith("RUN-") else run_id
    task_part = task_id[5:] if task_id.startswith("TASK-") else task_id
    return f"TASK-{run_part}--{task_part}"


def markdown_document(meta: dict[str, Any], body: str) -> bytes:
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False, sort_keys=True)}")
    lines.extend(("---", "", body.rstrip(), ""))
    return "\n".join(lines).encode("utf-8")


def section(text: str, heading: str) -> str:
    match = re.search(rf"(?ms)^# {re.escape(heading)}\s*$\n(.*?)(?=^# |\Z)", text)
    return match.group(1).strip() if match else ""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument(
        "--agent-map",
        action="append",
        default=[],
        metavar="RUN_AGENT=PERSISTENT_AGENT",
        help="Map a run-local Agent ID to an Axx-* persistent Agent ID",
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def digest_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical_record_hash(record: dict[str, Any]) -> str:
    value = {**record, "record_sha256": None}
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return digest_bytes(encoded.encode("utf-8"))


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot parse JSON {path}: {exc}") from exc


def parse_mapping(assignments: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for assignment in assignments:
        if assignment.count("=") != 1:
            raise ProtocolError(f"invalid --agent-map: {assignment}")
        source, target = (part.strip() for part in assignment.split("=", 1))
        if not source or not target or source in result:
            raise ProtocolError(f"invalid or duplicate --agent-map: {assignment}")
        result[source] = target
    return result


def run_inventory_hash(run_dir: Path) -> str:
    inventory: list[dict[str, str]] = []
    for path in sorted(run_dir.rglob("*")):
        if path.is_file() and path.name != ".sequence.lock":
            inventory.append(
                {
                    "path": path.relative_to(run_dir).as_posix(),
                    "sha256": sha256(path),
                }
            )
    encoded = json.dumps(inventory, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return digest_bytes(encoded.encode("utf-8"))


def resolve_source(
    reference: str,
    run_dir: Path,
    project_root: Path,
    governance_project: Path,
    *,
    project_only: bool = False,
) -> Path:
    path = Path(reference).expanduser()
    if not path.is_absolute():
        path = run_dir / path
    path = path.resolve()
    if not path.is_file():
        raise ProtocolError(f"referenced source does not exist: {reference}")
    roots = (project_root,) if project_only else (project_root, governance_project)
    if not any(path == root or root in path.parents for root in roots):
        raise ProtocolError(f"referenced source is outside allowed project/governance roots: {path}")
    return path


def source_relative(path: Path, project_root: Path, governance_project: Path) -> str:
    try:
        return "project://" + path.relative_to(project_root).as_posix()
    except ValueError:
        try:
            return "governance://" + path.relative_to(governance_project).as_posix()
        except ValueError as exc:
            raise ProtocolError(f"source is outside bound roots: {path}") from exc


def source_record(
    kind: str, path: Path, project_root: Path, governance_project: Path,
) -> dict[str, str]:
    return {
        "kind": kind,
        "source_path": str(path),
        "source_relative_path": source_relative(path, project_root, governance_project),
        "source_sha256": sha256(path),
    }


def destination_entry(path: Path, content: bytes, source: Path | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {
        "path": str(path),
        "sha256": digest_bytes(content),
        "content": content,
    }
    if source:
        value["source_path"] = str(source)
        value["source_sha256"] = sha256(source)
    return value


def execution_evidence(
    *, agent: Path, bus: Path, run_id: str, task_id: str, attempt_id: str, result_path: Path
) -> dict[str, Any]:
    ledger = agent / "activity" / run_id / task_id / attempt_id
    matches: list[tuple[Path, dict[str, Any]]] = []
    for activity_path in sorted(ledger.glob("*/*/*/ACTIVITY-*.json")):
        activity = read_json(activity_path)
        if activity.get("record_sha256") != canonical_record_hash(activity):
            raise ProtocolError(f"activity record hash mismatch: {activity_path}")
        source = activity.get("source", {})
        status = activity.get("status", {})
        if (
            activity.get("record_kind") == "attempt_finished"
            and status.get("attempt_status") == "completed"
            and source.get("source_sha256") == sha256(result_path)
        ):
            matches.append((activity_path, activity))
    if len(matches) != 1:
        raise ProtocolError(
            f"expected exactly one completed activity record for {run_id}/{task_id}/{attempt_id}"
        )
    activity_path, activity = matches[0]
    runtime = activity.get("runtime_profile", {})
    profile_ref = runtime.get("native_binding_ref")
    profile_hash = runtime.get("native_binding_sha256")
    if not isinstance(profile_ref, str) or not isinstance(profile_hash, str):
        raise ProtocolError(f"activity runtime binding is incomplete: {activity_path}")
    profile_path = (agent / profile_ref).resolve()
    try:
        profile_path.relative_to(agent.resolve())
    except ValueError as exc:
        raise ProtocolError(f"activity runtime profile escapes Agent store: {profile_ref}") from exc
    if not profile_path.is_file() or sha256(profile_path) != profile_hash:
        raise ProtocolError(f"activity runtime profile hash mismatch: {profile_path}")
    profile = read_json(profile_path)
    profile_id = profile.get("runtime_profile_id")
    if not isinstance(profile_id, str) or re.fullmatch(r"RP-[0-9]{6}", profile_id) is None:
        raise ProtocolError(f"activity runtime profile identity is invalid: {profile_path}")

    def actual(field: str) -> tuple[str, str | None]:
        value = profile.get(field, {})
        status = value.get("status") if isinstance(value, dict) else None
        actual_value = value.get("value") if isinstance(value, dict) else None
        if status not in {"known", "unknown", "not_collected", "conflict"}:
            raise ProtocolError(f"runtime profile {field} status is invalid: {profile_path}")
        if status == "known" and not isinstance(actual_value, str):
            raise ProtocolError(f"runtime profile {field} value is invalid: {profile_path}")
        return status, actual_value if status == "known" else None

    model_status, model = actual("model")
    provider_status, provider = actual("provider")
    usage = activity.get("usage")
    if not isinstance(usage, dict):
        raise ProtocolError(f"activity usage evidence is missing: {activity_path}")
    return {
        "runtime_profile_id": profile_id,
        "runtime_profile_path": str(profile_path),
        "runtime_profile_sha256": profile_hash,
        "activity_record_path": str(activity_path.resolve()),
        "activity_record_relative_path": activity_path.relative_to(bus).as_posix(),
        "activity_record_sha256": sha256(activity_path),
        "actual_model_status": model_status,
        "actual_model": model,
        "actual_provider_status": provider_status,
        "actual_provider": provider,
        "usage_summary": {
            key: usage.get(key) for key in (
                "usage_source", "input_tokens", "output_tokens", "cached_input_tokens",
                "reasoning_tokens", "total_tokens", "cost_minor_units", "currency",
                "source_ref", "source_sha256",
            )
        },
    }


def build_bridge(run_dir: Path, mapping: dict[str, str]) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    bus = run_dir.parent.parent.resolve()
    if run_dir.parent.name != "runs":
        raise ProtocolError("run directory must be inside a governance project runs/ directory")
    binding = load_project_binding(bus)
    project_root = Path(binding["project_root"]).expanduser().resolve()
    manifest_path = run_dir / "manifest.yaml"
    state_path = run_dir / "state.yaml"
    team_path = bus / "TEAM.yaml"
    if not all(path.is_file() for path in (manifest_path, state_path, team_path)):
        raise ProtocolError("run and persistent Agent store must share a valid governance project")
    manifest = scalar_map(manifest_path.read_text(encoding="utf-8"), source=str(manifest_path))
    state = scalar_map(state_path.read_text(encoding="utf-8"), source=str(state_path))
    run_id = manifest.get("run_id", "")
    if run_dir.name != run_id or state.get("run_id") != run_id:
        raise ProtocolError("run identity mismatch")
    team = read_json(team_path)
    persistent_ids = {
        item.get("agent_id") for item in team.get("agents", []) if isinstance(item, dict)
    }
    unknown = sorted(set(mapping.values()) - persistent_ids)
    if unknown:
        raise ProtocolError(f"persistent Agent mapping targets do not exist: {unknown}")

    event_sequence = int(state.get("event_sequence", "-1"))
    events = sorted((run_dir / "events").glob("*.yaml"))
    if event_sequence != len(events):
        raise ProtocolError("event sequence does not match immutable event files")

    task_paths = sorted((run_dir / "tasks").glob("*.md"))
    outputs: list[dict[str, Any]] = []
    task_entries: list[dict[str, Any]] = []
    for task_path in task_paths:
        task = frontmatter(task_path)
        task_id = task.get("task_id", "")
        owner = task.get("owner_agent", "")
        target_id = mapping.get(owner)
        if not target_id:
            raise ProtocolError(f"missing --agent-map for task owner: {owner}")
        agent = bus / "agents" / target_id
        archived_task_id = persistent_task_id(run_id, task_id)
        task_text = task_path.read_text(encoding="utf-8")
        target_task = agent / "tasks" / f"{archived_task_id}.md"
        task_content = markdown_document({
            "schema_version": "1.0", "doc_type": "task", "task_id": archived_task_id,
            "title": task.get("title") or task_id, "owner": target_id,
            "goal": section(task_text, "Objective") or "Preserve completed Run task facts",
            "dependencies": [], "allowed_writes": json_string_list(task.get("owned_paths", "[]"), field="owned_paths", source=str(task_path)),
            "forbidden_writes": json_string_list(task.get("forbidden_paths", "[]"), field="forbidden_paths", source=str(task_path)),
            "acceptance_commands": [], "expected_outputs": [],
            "created_at": task.get("created_at") or datetime.now(timezone.utc).isoformat(),
        }, f"# Archived Run Task\n\n- Run: `{run_id}`\n- Original task: `{task_id}`\n- Source: `{source_relative(task_path, project_root, bus)}`\n- Source SHA-256: `{sha256(task_path)}`")
        outputs.append(destination_entry(target_task, task_content, task_path))

        results = []
        result_bindings: list[dict[str, Any]] = []
        for result_path in sorted((run_dir / "outbox" / owner).glob(f"{task_id}-result-*.md")):
            values = frontmatter(result_path)
            attempt = values.get("attempt_id", "unknown")
            binding = execution_evidence(
                agent=agent, bus=bus, run_id=run_id, task_id=task_id,
                attempt_id=attempt, result_path=result_path,
            )
            target_result = agent / "handoffs" / f"{archived_task_id}--{attempt}.md"
            evidence_records = []
            for reference in json_string_list(values.get("verification_refs", "[]"), field="verification_refs", source=str(result_path)):
                evidence_source = resolve_source(reference, run_dir, project_root, bus)
                evidence_target = agent / "artifacts" / f"{run_id}--{task_id}--result-evidence--{evidence_source.name}"
                outputs.append(destination_entry(evidence_target, evidence_source.read_bytes(), evidence_source))
                evidence_records.append({"path": evidence_target.relative_to(bus).as_posix(), "sha256": sha256(evidence_source)})
            changed_records = []
            for reference in json_string_list(values.get("changed_files", "[]"), field="changed_files", source=str(result_path)):
                changed = resolve_source(reference, run_dir, project_root, bus, project_only=True)
                changed_records.append({"path": changed.relative_to(project_root).as_posix(), "sha256": sha256(changed)})
            result_content = markdown_document({
                "schema_version": "1.1", "doc_type": "handoff", "task_id": archived_task_id,
                "agent_id": target_id, "status": values.get("status", "failed"),
                "summary": section(result_path.read_text(encoding="utf-8"), "Outcome") or "Archived Run result",
                "created_at": values.get("created_at") or datetime.now(timezone.utc).isoformat(),
                "runtime_profile_id": binding["runtime_profile_id"],
                "runtime_profile_sha256": binding["runtime_profile_sha256"],
                "activity_record_path": binding["activity_record_relative_path"],
                "activity_record_sha256": binding["activity_record_sha256"],
                "actual_model_status": binding["actual_model_status"],
                "actual_model": binding["actual_model"],
                "actual_provider_status": binding["actual_provider_status"],
                "actual_provider": binding["actual_provider"],
                "usage_summary": binding["usage_summary"],
                "changed_files": [item["path"] for item in changed_records],
                "acceptance_evidence": evidence_records, "artifacts": evidence_records,
                "risks": [] if str(values.get("risk_summary", "")).lower() in {"", "none", "null"} else [values.get("risk_summary")],
                "unresolved": [], "rollback_note": values.get("rollback_plan", ""),
            }, f"# Archived Run Handoff\n\n- Run: `{run_id}`\n- Original task: `{task_id}`\n- Source: `{source_relative(result_path, project_root, bus)}`\n- Source SHA-256: `{sha256(result_path)}`")
            outputs.append(destination_entry(target_result, result_content, result_path))
            results.append(source_record("handoff", result_path.resolve(), project_root, bus))
            result_bindings.append({
                **binding,
                "result_source_path": str(result_path.resolve()),
                "result_source_sha256": sha256(result_path),
            })

        if len(result_bindings) != 1:
            raise ProtocolError(f"expected exactly one terminal result for task: {task_id}")
        result_binding = result_bindings[0]

        records: list[dict[str, str]] = []
        evidence_entries = []
        for evidence_path in sorted((run_dir / "evidence").glob("*.yaml")):
            values = scalar_map(evidence_path.read_text(encoding="utf-8"), source=str(evidence_path))
            if values.get("task_id") != task_id:
                continue
            evidence_id = values.get("evidence_id", evidence_path.stem)
            target_evidence = agent / "artifacts" / f"{run_id}--{task_id}--evidence--{evidence_id}{evidence_path.suffix}"
            outputs.append(destination_entry(target_evidence, evidence_path.read_bytes(), evidence_path))
            record = source_record("evidence", evidence_path.resolve(), project_root, bus)
            evidence_entries.append(record)
            records.append(record)
            refs = json_string_list(
                values.get("artifact_refs", "[]"), field="artifact_refs", source=str(evidence_path)
            )
            hashes = json.loads(values.get("artifact_hashes", "{}"))
            for reference in refs:
                artifact = resolve_source(reference, run_dir, project_root, bus, project_only=True)
                actual = sha256(artifact)
                if hashes.get(reference) != actual:
                    raise ProtocolError(f"artifact hash mismatch: {artifact}")
                records.append(source_record("artifact", artifact, project_root, bus))

        for result in results:
            records.append(result)
        bundle = {
            "schema_version": "1.0",
            "doc_type": "run_task_artifact_bundle",
            "run_id": run_id,
            "event_sequence": event_sequence,
            "task_id": archived_task_id,
            "source_task_id": task_id,
            "run_agent_id": owner,
            "persistent_agent_id": target_id,
            "task_source_path": str(task_path.resolve()),
            "task_source_sha256": sha256(task_path),
            "records": records,
        }
        bundle_content = (json.dumps(bundle, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        bundle_path = agent / "artifacts" / f"{run_id}--{task_id}--bundle.json"
        outputs.append(destination_entry(bundle_path, bundle_content))
        task_entries.append(
            {
                "task_id": archived_task_id,
                "source_task_id": task_id,
                "run_agent_id": owner,
                "persistent_agent_id": target_id,
                "task_source_path": str(task_path.resolve()),
                "task_source_sha256": sha256(task_path),
                "task_archive_path": str(target_task),
                "handoffs": results,
                "evidence": evidence_entries,
                "artifact_bundle_path": str(bundle_path),
                "artifact_bundle_sha256": digest_bytes(bundle_content),
                "runtime_profile_id": result_binding["runtime_profile_id"],
                "runtime_profile_path": result_binding["runtime_profile_path"],
                "runtime_profile_sha256": result_binding["runtime_profile_sha256"],
                "activity_record_path": result_binding["activity_record_path"],
                "activity_record_sha256": result_binding["activity_record_sha256"],
                "result_source_path": result_binding["result_source_path"],
                "result_source_sha256": result_binding["result_source_sha256"],
            }
        )

    bridge_path = bus / "bridges" / f"{run_id}.json"
    bridge = {
        "schema_version": "1.1",
        "doc_type": "run_memory_bridge",
        "bridge_version": BRIDGE_VERSION,
        "run_id": run_id,
        "event_sequence": event_sequence,
        "source_run_path": str(run_dir),
        "source_run_sha256": run_inventory_hash(run_dir),
        "source_manifest_path": str(manifest_path.resolve()),
        "source_manifest_sha256": sha256(manifest_path),
        "source_state_path": str(state_path.resolve()),
        "source_state_sha256": sha256(state_path),
        "agent_map": mapping,
        "tasks": task_entries,
    }
    bridge_content = (json.dumps(bridge, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    outputs.append(destination_entry(bridge_path, bridge_content))
    return bridge_path, bridge, outputs


def validate_run(run_dir: Path) -> None:
    validator = Path(__file__).with_name("validate_run.py")
    result = subprocess.run(
        [sys.executable, str(validator), str(run_dir), "--phase", "completion"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        detail = (result.stdout + result.stderr).strip()
        raise ProtocolError(f"run completion validation failed:\n{detail}")


def verify_destinations(outputs: list[dict[str, Any]]) -> bool:
    existing = 0
    for item in outputs:
        path: Path = item["path"] if isinstance(item["path"], Path) else Path(item["path"])
        if not path.exists():
            continue
        existing += 1
        if not path.is_file() or sha256(path) != item["sha256"]:
            raise ProtocolError(f"bridge conflict at immutable destination: {path}")
    if existing not in {0, len(outputs)}:
        raise ProtocolError("bridge conflict: partial destination set exists")
    return existing == len(outputs)


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir).expanduser().resolve()
    try:
        if not run_dir.is_dir():
            raise ProtocolError(f"run directory does not exist: {run_dir}")
        mapping = parse_mapping(args.agent_map)
        validate_run(run_dir)
        bridge_path, bridge, outputs = build_bridge(run_dir, mapping)
        plan = {
            "dry_run": bool(args.dry_run),
            "bridge_manifest": str(bridge_path),
            "run_id": bridge["run_id"],
            "event_sequence": bridge["event_sequence"],
            "source_run_sha256": bridge["source_run_sha256"],
            "writes": [{"path": item["path"], "sha256": item["sha256"]} for item in outputs],
        }
        if args.dry_run:
            verify_destinations(outputs)
            print(json.dumps(plan, ensure_ascii=False, indent=2))
            return 0
        lock_path = run_dir.parent.parent / ".run-memory-bridge.lock"
        with exclusive_lock(lock_path):
            already_complete = verify_destinations(outputs)
            if not already_complete:
                installed: list[Path] = []
                try:
                    for item in outputs:
                        target = Path(item["path"])
                        atomic_write(target, item["content"].decode("utf-8"))
                        installed.append(target)
                except Exception:
                    for target in reversed(installed):
                        target.unlink(missing_ok=True)
                    raise
        print(bridge_path)
        return 0
    except (ProtocolError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
