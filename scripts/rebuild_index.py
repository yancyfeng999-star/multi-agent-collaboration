#!/usr/bin/env python3
"""Build deterministic, fail-closed human and machine project indexes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from project_memory_lib import bus_root, exclusive_lock, parse_frontmatter, project_root
from protocol_lib import ProtocolError, now_iso, scalar_map

INDEX_KINDS = {"archive", "checkpoint", "task", "handoff", "decision", "evidence", "artifact"}
RUNTIME_KINDS = {
    "runtime_profile": "runtime-profile",
    "runtime-profile": "runtime-profile",
    "activity": "activity",
    "agent_profile": "agent-profile",
    "agent-profile": "agent-profile",
    "project_checkpoint": "project-checkpoint",
    "project-checkpoint": "project-checkpoint",
    "run_memory_bridge": "bridge",
    "bridge": "bridge",
}
IGNORED_NAMES = {"INDEX.md", "index.jsonl"}
PATH_FIELDS = {
    "related_files",
    "changed_files",
    "owned_paths",
    "artifact_refs",
    "verification_refs",
    "deliverable",
    "deliverables",
}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="重建确定性多智能体项目索引")
    value.add_argument("--project-root", required=True)
    value.add_argument("--governance-root")
    value.add_argument("--project-id")
    return value


def _decoded(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if value in {"null", "true", "false"} or value.startswith(("[", "{")):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    if len(value) >= 2 and value[0] == value[-1] == '"':
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def load_document(path: Path) -> dict[str, Any]:
    try:
        if path.suffix == ".md":
            return parse_frontmatter(path)
        if path.suffix == ".json":
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ProtocolError(f"indexed JSON must be an object: {path}")
            return value
        return {key: _decoded(value) for key, value in scalar_map(path.read_text(encoding="utf-8"), source=str(path)).items()}
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"cannot parse indexed document {path}: {exc}") from exc


def as_strings(value: Any) -> list[str]:
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if item is not None and str(item)]
    if isinstance(value, dict):
        return [str(item) for item in value.values() if item is not None and str(item)]
    return [str(value)]


def unique_sorted(values: list[str]) -> list[str]:
    return sorted(set(values), key=lambda item: (item.casefold(), item))


def classify(path: Path, data: dict[str, Any]) -> str | None:
    declared = str(data.get("doc_type") or data.get("kind") or "").lower()
    if declared in RUNTIME_KINDS:
        return RUNTIME_KINDS[declared]
    if data.get("record_kind") and "activity" in path.parts:
        return "activity"
    if declared == "result":
        return "handoff"
    if declared in INDEX_KINDS:
        return declared
    parts = set(path.parts)
    if "archive" in parts:
        return "archive"
    if "checkpoints" in parts or "project-checkpoints" in parts:
        return "checkpoint"
    if "tasks" in parts and path.suffix == ".md":
        return "task"
    if "decisions" in parts:
        return "decision"
    if "evidence" in parts:
        return "evidence"
    if "artifacts" in parts:
        return "artifact"
    if "runtime" in parts and "agent_id" in data:
        return "runtime-profile"
    if path.name == "AGENT_PROFILE.json" and "agent_id" in data:
        return "agent-profile"
    if "bridges" in parts and "run_id" in data:
        return "bridge"
    if "outbox" in parts and ("task_id" in data or "task_ids" in data):
        return "handoff"
    return None


def content_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_record(path: Path, bus: Path, data: dict[str, Any], kind: str) -> dict[str, Any]:
    agent_values: list[str] = []
    for field in ("agent_id", "owner_agent", "owner", "reviewer_agent", "qa_agent", "release_agent"):
        agent_values.extend(as_strings(data.get(field)))
    task_values = as_strings(data.get("task_id")) + as_strings(data.get("task_ids"))
    related: list[str] = []
    for field in ("related_files", "changed_files", "owned_paths", "artifact_refs"):
        related.extend(as_strings(data.get(field)))
    verification = as_strings(data.get("verification"))
    verification.extend(as_strings(data.get("verification_status")))
    verification.extend(as_strings(data.get("verification_refs")))
    deliverable = as_strings(data.get("deliverable")) + as_strings(data.get("deliverables"))
    risk = as_strings(data.get("risk")) + as_strings(data.get("risk_flags")) + as_strings(data.get("risk_summary"))
    owners = unique_sorted(as_strings(data.get("owner_agent")) + as_strings(data.get("owner")))
    runs = as_strings(data.get("run_id")) + as_strings(data.get("associated_runs"))
    runtimes: list[str] = []
    activities: list[str] = []

    def collect_relations(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"native_binding_ref", "runtime_profile_path"}:
                    runtimes.extend(as_strings(item))
                elif key == "runtime_profiles" and isinstance(item, list):
                    runtimes.extend(str(entry["ref"]) for entry in item if isinstance(entry, dict) and entry.get("ref"))
                elif key == "activity_record_path":
                    activities.extend(as_strings(item))
                elif key == "activity_refs" and isinstance(item, list):
                    activities.extend(str(entry["ref"]) for entry in item if isinstance(entry, dict) and entry.get("ref"))
                if key in {"agent_id", "owner_agent", "owner"}:
                    agent_values.extend(as_strings(item))
                elif key in {"task_id", "task_ids"}:
                    task_values.extend(as_strings(item))
                elif key in {"run_id", "associated_runs"}:
                    runs.extend(as_strings(item))
                collect_relations(item)
        elif isinstance(value, list):
            for item in value:
                collect_relations(item)

    collect_relations(data)
    file_hash = content_sha256(path)
    return {
        "path": path.relative_to(bus).as_posix(),
        "kind": kind,
        "doc_type": kind,
        "agents": unique_sorted(agent_values),
        "agent": unique_sorted(agent_values),
        "owner": owners,
        "tasks": unique_sorted(task_values),
        "task": unique_sorted(task_values),
        "run": unique_sorted(runs),
        "runtime": unique_sorted(runtimes),
        "activity": unique_sorted(activities),
        "status": str(data.get("status") or ""),
        "risk": unique_sorted(risk),
        "keywords": unique_sorted(as_strings(data.get("keywords"))),
        "related_files": unique_sorted(related),
        "verification": unique_sorted(verification),
        "deliverable": unique_sorted(deliverable),
        "sha256": file_hash,
        "hash": file_hash,
    }


def discover(bus: Path) -> list[tuple[Path, dict[str, Any], str]]:
    documents: list[tuple[Path, dict[str, Any], str]] = []
    structural_parts = {"archive", "checkpoints", "project-checkpoints", "tasks", "outbox", "decisions", "evidence", "artifacts", "runtime", "activity", "agents", "bridges"}
    for path in sorted(bus.rglob("*"), key=lambda item: item.relative_to(bus).as_posix()):
        if not path.is_file() or path.name in IGNORED_NAMES or path.name.startswith("."):
            continue
        if path.suffix not in {".md", ".yaml", ".yml", ".json"}:
            continue
        relative = path.relative_to(bus)
        if "templates" in relative.parts:
            continue
        # Structured registries/manifests are not index entities and may use full
        # YAML or placeholder JSON. Only protocol entity directories contain
        # machine-format index records; Markdown can additionally declare a kind.
        if path.suffix in {".yaml", ".yml", ".json"} and not (set(relative.parts) & structural_parts):
            continue
        data = load_document(path)
        kind = classify(relative, data)
        if kind:
            documents.append((path, data, kind))
    return documents


def _reference_exists(value: str, root: Path, bus: Path) -> bool:
    if not value or "://" in value or value.startswith(("sha256:", "git:")):
        return True
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.exists()
    return (root / path).exists() or (bus / path).exists()


def _resolve_reference(value: str, root: Path, bus: Path, source: Path) -> Path | None:
    candidate = Path(value).expanduser()
    candidates = [candidate] if candidate.is_absolute() else [root / candidate, bus / candidate]
    # Runtime profiles sometimes use refs relative to their owning agent directory.
    if not candidate.is_absolute() and "agents" in source.parts:
        agent_index = source.parts.index("agents")
        if len(source.parts) > agent_index + 1:
            candidates.append(Path(*source.parts[:agent_index + 2]) / candidate)
    return next((path for path in candidates if path.is_file()), None)


def _hashed_references(value: Any) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    if isinstance(value, dict):
        for ref_key, hash_key in (
            ("ref", "sha256"),
            ("native_binding_ref", "native_binding_sha256"),
            ("source_ref", "source_sha256"),
            ("runtime_profile_path", "runtime_profile_sha256"),
            ("activity_record_path", "activity_record_sha256"),
        ):
            if value.get(ref_key) and value.get(hash_key):
                pairs.append((str(value[ref_key]), str(value[hash_key])))
        for item in value.values():
            pairs.extend(_hashed_references(item))
    elif isinstance(value, list):
        for item in value:
            pairs.extend(_hashed_references(item))
    return pairs


def validate(documents: list[tuple[Path, dict[str, Any], str]], root: Path, bus: Path) -> None:
    task_sources: dict[str, Path] = {}
    task_owners: dict[str, str] = {}
    for path, data, kind in documents:
        if kind != "task":
            continue
        task_id = str(data.get("task_id") or "")
        if not task_id:
            raise ProtocolError(f"task has no task_id: {path}")
        if task_id in task_sources:
            raise ProtocolError(f"duplicate task ID {task_id}: {task_sources[task_id]} and {path}")
        task_sources[task_id] = path
        task_owners[task_id] = str(data.get("owner_agent") or data.get("owner") or data.get("agent_id") or "")

    for path, data, kind in documents:
        references = unique_sorted(as_strings(data.get("task_id")) + as_strings(data.get("task_ids")))
        if kind != "task":
            for task_id in references:
                if task_id not in task_sources:
                    raise ProtocolError(f"dangling task reference {task_id}: {path}")
        if kind == "handoff":
            for task_id in references:
                owner = task_owners.get(task_id, "")
                agent = str(data.get("agent_id") or "")
                if owner and agent != owner:
                    raise ProtocolError(f"task-handoff mismatch for {task_id}: owner {owner}, handoff agent {agent or '<missing>'}")
        for field in PATH_FIELDS:
            for value in as_strings(data.get(field)):
                if not _reference_exists(value, root, bus):
                    raise ProtocolError(f"missing referenced path {value} in {path} field {field}")
        for reference, expected_hash in _hashed_references(data):
            target = _resolve_reference(reference, root, bus, path)
            if target is None:
                raise ProtocolError(f"missing referenced path {reference} in {path}")
            actual_hash = content_sha256(target)
            if actual_hash != expected_hash:
                raise ProtocolError(
                    f"hash mismatch for referenced path {reference} in {path}: "
                    f"expected {expected_hash}, got {actual_hash}"
                )


def machine_content(records: list[dict[str, Any]]) -> str:
    return "".join(json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n" for record in records)


def human_content(records: list[dict[str, Any]], generated_at: str, content_hash: str) -> str:
    rows = []
    for record in records:
        cells = [
            f"`{record['path']}`",
            str(record["kind"]),
            ", ".join(record["agents"]),
            ", ".join(record["tasks"]),
            str(record["status"]),
            ", ".join(record["risk"]),
            ", ".join(record["keywords"]),
            ", ".join(record["related_files"]),
            ", ".join(record["verification"]),
            ", ".join(record["deliverable"]),
            str(record["sha256"]),
        ]
        rows.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
    return "\n".join((
        "---",
        'schema_version: "2.0"',
        'doc_type: "project_index"',
        f"generated_at: {json.dumps(generated_at, ensure_ascii=False)}",
        f'content_sha256: "{content_hash}"',
        "---",
        "",
        "# 项目索引",
        "",
        "| 路径 | 类型 | Agent | 任务 | 状态 | 风险 | 关键词 | 关联文件 | 验证 | 交付物 | SHA-256 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        *rows,
        "",
    ))


def install_pair(json_path: Path, json_content: str, md_path: Path, md_content: str) -> None:
    temporaries: list[tuple[Path, Path]] = []
    try:
        for destination, content in ((json_path, json_content), (md_path, md_content)):
            temporary = destination.with_name(f".{destination.name}.{os.getpid()}.tmp")
            temporary.write_text(content, encoding="utf-8")
            temporaries.append((temporary, destination))
        for temporary, destination in temporaries:
            os.replace(temporary, destination)
    finally:
        for temporary, _ in temporaries:
            temporary.unlink(missing_ok=True)


def rebuild(root: Path, bus: Path) -> bool:
    documents = discover(bus)
    validate(documents, root, bus)
    records = [make_record(path, bus, data, kind) for path, data, kind in documents]
    records.sort(key=lambda record: str(record["path"]))
    json_content = machine_content(records)
    json_path = bus / "index.jsonl"
    md_path = bus / "INDEX.md"
    content_hash = hashlib.sha256(json_content.encode("utf-8")).hexdigest()
    if json_path.exists() and md_path.exists() and json_path.read_text(encoding="utf-8") == json_content:
        existing_md = md_path.read_text(encoding="utf-8")
        existing_meta = parse_frontmatter(md_path)
        generated_at = str(existing_meta.get("generated_at") or "")
        if generated_at and existing_meta.get("content_sha256") == content_hash:
            if existing_md == human_content(records, generated_at, content_hash):
                return False
    md_content = human_content(records, now_iso(), content_hash)
    install_pair(json_path, json_content, md_path, md_content)
    return True


def main() -> int:
    args = parser().parse_args()
    try:
        root = project_root(args.project_root)
        bus = bus_root(root, governance_root=args.governance_root, project_id=args.project_id)
        with exclusive_lock(bus / ".rebuild-index.lock"):
            changed = rebuild(root, bus)
        print(f"{bus / 'INDEX.md'} ({'updated' if changed else 'unchanged'})")
        return 0
    except (ProtocolError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
