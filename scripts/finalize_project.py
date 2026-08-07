#!/usr/bin/env python3
"""Fail-closed project finalization with immutable, idempotent audit outputs."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable

from project_memory_lib import exclusive_lock

BUS = ".multi-agent-collaboration"
FINAL_FILES = ("PROJECT_FINAL_REPORT.md", "AUDIT_MANIFEST.json", "ARTIFACT_INDEX.jsonl")
ALLOWED_RUN_STATUSES = {"completed", "archived"}
ALLOWED_TASK_STATUSES = {"completed", "cancelled", "superseded"}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _flat(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        try:
            result[key.strip()] = json.loads(value.strip())
        except json.JSONDecodeError:
            result[key.strip()] = value.strip()
    return result


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---\n", 4)
    if end < 0:
        raise ValueError(f"unterminated frontmatter: {path}")
    result: dict[str, Any] = {}
    for raw in text[4:end].splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        try:
            result[key.strip()] = json.loads(value.strip())
        except json.JSONDecodeError:
            result[key.strip()] = value.strip()
    return result


def _relative(path: Path, bus: Path) -> str:
    try:
        return path.resolve().relative_to(bus.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"artifact outside project store: {path}") from exc


def _atomic(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _default_validators(root: Path, runs: list[Path]) -> list[str]:
    scripts = Path(__file__).resolve().parent
    commands = [[sys.executable, str(scripts / "validate_agents.py"), "--project-root", str(root)]]
    for run in runs:
        commands.append([sys.executable, str(scripts / "validate_run.py"), str(run), "--phase", "completion"])
        manifest = _flat(run / "manifest.yaml")
        if manifest.get("versioning_mode") == "tracked" or list((run / "events").glob("*release_ready*.yaml")):
            commands.append([sys.executable, str(scripts / "validate_run.py"), str(run), "--phase", "release"])
    errors: list[str] = []
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode:
            errors.append(f"validator failed ({' '.join(command)}): {(result.stdout + result.stderr).strip()}")
    return errors


def _select_runs(bus: Path, run_ids: Iterable[str] | None) -> list[Path]:
    ids = list(run_ids or [])
    if not ids:
        ids = sorted(path.name for path in (bus / "runs").glob("RUN-*") if path.is_dir())
    if not ids:
        raise ValueError("no associated runs")
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate run id")
    runs = []
    for run_id in ids:
        if not re.fullmatch(r"RUN-[A-Za-z0-9._-]+", run_id):
            raise ValueError(f"invalid run id: {run_id}")
        run = bus / "runs" / run_id
        if not run.is_dir():
            raise ValueError(f"associated run does not exist: {run_id}")
        runs.append(run)
    return runs


def _gate_and_collect(bus: Path, runs: list[Path]) -> dict[str, Any]:
    data: dict[str, Any] = {name: [] for name in ("tasks", "agents", "runs", "decisions", "handoffs", "evidence", "risks", "unresolved", "approvals")}
    team_path = bus / "TEAM.yaml"
    decisions_path = bus / "DECISIONS.md"
    if not team_path.is_file() or not decisions_path.is_file():
        raise ValueError("TEAM.yaml and DECISIONS.md are required")
    team = json.loads(team_path.read_text(encoding="utf-8"))
    data["agents"] = team.get("agents", [])
    data["decisions"].append({"path": "DECISIONS.md", "sha256": _sha(decisions_path), "scope": "project"})
    source_paths: set[Path] = {team_path, decisions_path}
    current = bus / "CURRENT_PROJECT_CONTEXT.md"
    if current.is_file():
        source_paths.add(current)
    for run in runs:
        run_approvals: list[dict[str, Any]] = []
        required = [run / "manifest.yaml", run / "state.yaml", run / "summary.md"]
        if any(not path.is_file() for path in required):
            raise ValueError(f"run missing manifest/state/summary: {run.name}")
        source_paths.update(required)
        manifest, state = _flat(required[0]), _flat(required[1])
        status = state.get("status")
        if status not in ALLOWED_RUN_STATUSES:
            raise ValueError(f"run must be terminal completed/archived: {run.name}={status}")
        task_states = state.get("task_states")
        if not isinstance(task_states, dict) or not task_states:
            raise ValueError(f"run has no event-derived task states: {run.name}")
        bad = {key: value for key, value in task_states.items() if value not in ALLOWED_TASK_STATUSES}
        if bad:
            raise ValueError(f"run tasks are not completion/release terminal: {run.name}: {bad}")
        data["runs"].append({"run_id": run.name, "status": status, "summary": _relative(required[2], bus), "task_states": task_states})
        for task in sorted((run / "tasks").glob("*.md")):
            meta = _frontmatter(task)
            task_id = str(meta.get("task_id", task.stem))
            record = {"run_id": run.name, "task_id": task_id, "status": task_states.get(task_id), "owner_agent": meta.get("owner_agent"), "path": _relative(task, bus)}
            data["tasks"].append(record); source_paths.add(task)
        for result in sorted((run / "outbox").glob("*/*-result-*.md")):
            meta = _frontmatter(result); source_paths.add(result)
            record = {"run_id": run.name, "task_id": meta.get("task_id"), "agent_id": meta.get("agent_id"), "status": meta.get("status"), "handoff_to": meta.get("handoff_to"), "path": _relative(result, bus)}
            data["handoffs"].append(record)
            if meta.get("risk_summary") not in {None, "", "none", "null", "not recorded"}:
                data["risks"].append({"run_id": run.name, "task_id": meta.get("task_id"), "summary": meta.get("risk_summary")})
            if meta.get("status") != "completed" or meta.get("verification_status") != "passed":
                data["unresolved"].append({"run_id": run.name, "task_id": meta.get("task_id"), "status": meta.get("status"), "verification": meta.get("verification_status")})
        for evidence in sorted((run / "evidence").glob("*")):
            if evidence.is_file():
                values = _frontmatter(evidence) if evidence.suffix == ".md" else _flat(evidence)
                source_paths.add(evidence); data["evidence"].append({"run_id": run.name, "kind": values.get("kind"), "status": values.get("status"), "summary": values.get("summary"), "path": _relative(evidence, bus)})
        for decision in sorted((run / "decisions").glob("*")):
            if not decision.is_file(): continue
            values = _frontmatter(decision) if decision.suffix == ".md" else _flat(decision)
            source_paths.add(decision)
            record = {"run_id": run.name, "kind": values.get("kind"), "status": values.get("status"), "summary": values.get("summary"), "path": _relative(decision, bus)}
            data["decisions"].append(record)
            if values.get("kind") == "human_gate":
                if values.get("status") != "approved":
                    raise ValueError(f"human gate is not approved: {decision}")
                data["approvals"].append(record)
                run_approvals.append(record)
        release_events = list((run / "events").glob("*release_ready*.yaml"))
        if release_events and not run_approvals:
            raise ValueError(f"release gate missing approval: {run.name}")
        source_paths.update(release_events)
    data["source_paths"] = sorted(source_paths)
    data["team"] = team
    return data


def _bullets(records: list[dict[str, Any]]) -> list[str]:
    return ["- " + json.dumps(record, ensure_ascii=False, sort_keys=True) for record in records] or ["- none"]


def _report(data: dict[str, Any], finalized_at: str) -> str:
    sections = [("Tasks", "tasks"), ("Agents", "agents"), ("Runs", "runs"), ("Decisions", "decisions"), ("Handoffs", "handoffs"), ("Evidence", "evidence"), ("Risks", "risks"), ("Unresolved Items", "unresolved"), ("Approvals and Risk Acceptance", "approvals")]
    lines = ["# Project Final Report", "", f"- Project: `{data['team']['project_id']}`", f"- Finalized at: `{finalized_at}`", ""]
    for title, key in sections:
        lines.extend([f"## {title}", "", *_bullets(data[key]), ""])
    return "\n".join(lines)


def _require_persistent_closure(bus: Path, runs: list[Path]) -> None:
    context = bus / "CURRENT_PROJECT_CONTEXT.md"
    context_meta = _frontmatter(context) if context.is_file() else {}
    checkpoint_ref = context_meta.get("latest_project_checkpoint")
    checkpoint = bus / str(checkpoint_ref) if checkpoint_ref else None
    if not checkpoint or not checkpoint.is_file():
        raise ValueError("finalization requires a latest project checkpoint")
    checkpoint_meta = _frontmatter(checkpoint)
    selected = {run.name for run in runs}
    if set(checkpoint_meta.get("associated_runs", [])) != selected:
        raise ValueError("latest project checkpoint does not cover selected runs")
    index = bus / "index.jsonl"
    if not index.is_file():
        raise ValueError("finalization requires a rebuilt deterministic index")
    indexed = {json.loads(line).get("path") for line in index.read_text(encoding="utf-8").splitlines() if line.strip()}
    if checkpoint.relative_to(bus).as_posix() not in indexed:
        raise ValueError("deterministic index does not include latest project checkpoint")
    for run in runs:
        bridge_path = bus / "bridges" / f"{run.name}.json"
        if not bridge_path.is_file():
            raise ValueError(f"run has not been bridged to persistent Agents: {run.name}")
        bridge = json.loads(bridge_path.read_text(encoding="utf-8"))
        if Path(str(bridge.get("source_run_path", ""))).resolve() != run.resolve():
            raise ValueError(f"bridge source path mismatch: {run.name}")
        for label in ("manifest", "state"):
            source = run / f"{label}.yaml"
            if bridge.get(f"source_{label}_sha256") != _sha(source):
                raise ValueError(f"bridge {label} hash mismatch: {run.name}")
        source_tasks = {str(_frontmatter(path).get("task_id", path.stem)) for path in (run / "tasks").glob("*.md")}
        bridged_tasks = {str(item.get("source_task_id")) for item in bridge.get("tasks", []) if isinstance(item, dict)}
        if source_tasks != bridged_tasks:
            raise ValueError(f"bridge task coverage mismatch: {run.name}")


def _audit_runtime_sources(bus: Path, runs: list[Path]) -> list[Path]:
    """Collect the gated persistent-runtime records that make final audit portable."""
    index = bus / "index.jsonl"
    selected_runs = {run.name for run in runs}
    records: list[dict[str, Any]] = []
    for number, line in enumerate(index.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"invalid deterministic index line {number}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"invalid deterministic index record at line {number}")
        records.append(record)

    context_meta = _frontmatter(bus / "CURRENT_PROJECT_CONTEXT.md")
    checkpoint_ref = str(context_meta.get("latest_project_checkpoint") or "")
    required = {checkpoint_ref, *(f"bridges/{run.name}.json" for run in runs)}
    sources = {index}
    for record in records:
        relative = record.get("path")
        kind = record.get("kind") or record.get("doc_type")
        record_runs = record.get("run", [])
        include = relative in required or kind in {"runtime-profile", "agent-profile"}
        include = include or (kind == "activity" and isinstance(record_runs, list) and bool(selected_runs & set(record_runs)))
        if not include:
            continue
        if not isinstance(relative, str) or not relative:
            raise ValueError("deterministic index contains an invalid audit path")
        source = bus / relative
        try:
            source.resolve().relative_to(bus.resolve())
        except ValueError as exc:
            raise ValueError(f"audit source escapes project store: {relative}") from exc
        if not source.is_file() or record.get("sha256") != _sha(source):
            raise ValueError(f"deterministic index hash mismatch: {relative}")
        sources.add(source)
    indexed_paths = {str(record.get("path")) for record in records}
    missing = required - indexed_paths
    if missing:
        raise ValueError(f"deterministic index missing finalization sources: {sorted(missing)}")
    return sorted(sources)


def finalize_project(project_root: str | Path, run_ids: Iterable[str] | None = None, *, dry_run: bool = False,
                     validator_runner: Callable[[Path, list[Path]], list[str]] | None = None) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve(); bus = root / BUS
    if not root.is_dir() or not bus.is_dir(): raise ValueError("project persistent store does not exist")
    with exclusive_lock(bus / ".project-finalization.lock"):
        outputs = [bus / name for name in FINAL_FILES]
        existing = [path.exists() for path in outputs]
        if any(existing):
            if not all(existing): raise ValueError("partial finalization bundle exists; refusing to overwrite")
            audit = json.loads(outputs[1].read_text(encoding="utf-8"))
            requested = list(run_ids or audit.get("runs", []))
            if requested != audit.get("runs"): raise ValueError("project already finalized for a different run set")
            if audit.get("report_sha256") != _sha(outputs[0]):
                raise ValueError("existing final report hash mismatch")
            source_hashes = audit.get("source_hashes")
            if not isinstance(source_hashes, dict):
                raise ValueError("existing audit manifest has invalid source_hashes")
            for relative, expected_hash in source_hashes.items():
                source = bus / str(relative)
                try:
                    source.resolve().relative_to(bus.resolve())
                except ValueError as exc:
                    raise ValueError(f"audit source escapes project store: {relative}") from exc
                if not source.is_file() or _sha(source) != expected_hash:
                    raise ValueError(f"source hash mismatch after finalization: {relative}")
            index_records: dict[str, dict[str, Any]] = {}
            for number, line in enumerate(outputs[2].read_text(encoding="utf-8").splitlines(), 1):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid artifact index line {number}") from exc
                path_value = record.get("path")
                if not isinstance(path_value, str) or path_value in index_records:
                    raise ValueError(f"invalid or duplicate artifact index path at line {number}")
                index_records[path_value] = record
                if record.get("path") == "PROJECT_FINAL_REPORT.md" and record.get("sha256") != _sha(outputs[0]):
                    raise ValueError("artifact index final report hash mismatch")
                if record.get("path") == "AUDIT_MANIFEST.json" and record.get("sha256") != _sha(outputs[1]):
                    raise ValueError("artifact index audit manifest hash mismatch")
            expected_paths = set(source_hashes) | {"PROJECT_FINAL_REPORT.md", "AUDIT_MANIFEST.json"}
            if set(index_records) != expected_paths:
                raise ValueError("artifact index record set is incomplete or contains extras")
            for relative, expected_hash in source_hashes.items():
                if index_records[relative].get("sha256") != expected_hash or index_records[relative].get("kind") != "source":
                    raise ValueError(f"artifact index source record mismatch: {relative}")
            return {"status": "already_finalized", "outputs": FINAL_FILES}
        runs = _select_runs(bus, run_ids)
        data = _gate_and_collect(bus, runs)
        _require_persistent_closure(bus, runs)
        errors = (validator_runner or _default_validators)(root, runs)
        if errors: raise ValueError("persistent validator gate failed: " + "; ".join(errors))
        runtime_sources = _audit_runtime_sources(bus, runs)
        finalized_at = _now()
        source_paths = set(data.pop("source_paths")) | set(runtime_sources)
        source_hashes = {_relative(path, bus): _sha(path) for path in source_paths}
        report = _report(data, finalized_at)
        manifest = {"schema_version": "1.0", "doc_type": "project_final_audit", "project_id": data["team"]["project_id"], "finalized_at": finalized_at,
                    "runs": [run.name for run in runs], "validators": "passed", "source_hashes": source_hashes,
                    "report_sha256": hashlib.sha256(report.encode()).hexdigest(), "artifact_index": "ARTIFACT_INDEX.jsonl"}
        manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        artifact_paths = sorted(set(source_hashes) | {"PROJECT_FINAL_REPORT.md", "AUDIT_MANIFEST.json"})
        index_lines = []
        for relative in artifact_paths:
            if relative == "PROJECT_FINAL_REPORT.md": digest, kind = manifest["report_sha256"], "final_report"
            elif relative == "AUDIT_MANIFEST.json": digest, kind = hashlib.sha256(manifest_text.encode()).hexdigest(), "audit_manifest"
            else: digest, kind = source_hashes[relative], "source"
            index_lines.append(json.dumps({"path": relative, "sha256": digest, "kind": kind}, ensure_ascii=False, sort_keys=True))
        if dry_run: return {"status": "ready", "runs": manifest["runs"], "source_hashes": source_hashes, "outputs": FINAL_FILES}
        installed: list[Path] = []
        try:
            for path, content in ((outputs[0], report), (outputs[1], manifest_text), (outputs[2], "\n".join(index_lines) + "\n")):
                _atomic(path, content)
                installed.append(path)
        except Exception:
            for path in reversed(installed):
                path.unlink(missing_ok=True)
            raise
        return {"status": "finalized", "runs": manifest["runs"], "outputs": FINAL_FILES}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--project-root", required=True); parser.add_argument("--run-id", action="append", default=[]); parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(finalize_project(args.project_root, args.run_id, dry_run=args.dry_run), ensure_ascii=False, sort_keys=True)); return 0
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr); return 1


if __name__ == "__main__": raise SystemExit(main())
