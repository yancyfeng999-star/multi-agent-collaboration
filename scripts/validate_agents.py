#!/usr/bin/env python3
"""Fail-closed validation for persistent project Agent archives (stdlib only)."""
from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from project_memory_lib import AGENT_ID_RE, bus_root, contains_secret, parse_frontmatter, project_root, read_json
from protocol_lib import ProtocolError
from rebuild_index import discover as discover_index_documents

REQUIRED_PROJECT_FILES = ["TEAM.yaml", "PROTOCOL.md", "CURRENT_PROJECT_CONTEXT.md", "DECISIONS.md", "INDEX.md"]
REQUIRED_SUPPORT_DIRS = ["schemas", "templates"]
REQUIRED_AGENT_FILES = ["ROLE.md", "SYSTEM_PROMPT.md", "CHECKLIST.md", "conversations/SESSION_MAP.json", "conversations/CURRENT_CONTEXT.md", "conversations/INDEX.md"]
REQUIRED_AGENT_DIRS = ["conversations/archive", "conversations/checkpoints", "tasks", "handoffs", "artifacts"]
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
SECRET_FIELD_RE = re.compile(r"(?i)(?:api[_-]?key|access[_-]?token|refresh[_-]?token|authorization|cookie|password|passwd|secret|private[_-]?key)")



def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="验证项目 Agent 持久化结构")
    value.add_argument("--project-root", required=True)
    value.add_argument("--governance-root")
    value.add_argument("--project-id")
    return value


def document_body(path: Path, heading: str | None = None) -> str:
    text = path.read_text(encoding="utf-8")
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end < 0:
            raise ProtocolError(f"unterminated frontmatter: {path}")
        text = text[end + 5:].lstrip("\n")
    if heading:
        prefix = heading + "\n\n"
        if text.startswith(prefix):
            text = text[len(prefix):]
    return text.strip()


def is_iso_datetime(value: Any) -> bool:
    if not isinstance(value, str) or "T" not in value:
        return False
    try:
        datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        return True
    except ValueError:
        return False


def _schema_pointer(root: dict[str, Any], reference: str) -> dict[str, Any]:
    if not reference.startswith("#/"):
        raise ValueError(f"unsupported schema reference {reference!r}")
    value: Any = root
    for token in reference[2:].split("/"):
        value = value[token.replace("~1", "/").replace("~0", "~")]
    if not isinstance(value, dict):
        raise ValueError(f"schema reference is not an object: {reference!r}")
    return value


def schema_errors(value: Any, schema: dict[str, Any], where: str = "$", root_schema: dict[str, Any] | None = None) -> list[str]:
    """Validate the protocol's required JSON-Schema draft-07 subset."""
    errors: list[str] = []
    root_schema = schema if root_schema is None else root_schema
    if "$ref" in schema:
        try:
            return schema_errors(value, _schema_pointer(root_schema, schema["$ref"]), where, root_schema)
        except (KeyError, ValueError) as exc:
            return [f"{where}: invalid $ref: {exc}"]
    if "allOf" in schema:
        for child in schema["allOf"]:
            errors.extend(schema_errors(value, child, where, root_schema))
    if "anyOf" in schema and not any(not schema_errors(value, child, where, root_schema) for child in schema["anyOf"]):
        errors.append(f"{where}: anyOf matched no schema")
    if "oneOf" in schema:
        matches = sum(not schema_errors(value, child, where, root_schema) for child in schema["oneOf"])
        if matches != 1:
            errors.append(f"{where}: oneOf matched {matches} schemas")
    if "not" in schema and not schema_errors(value, schema["not"], where, root_schema):
        errors.append(f"{where}: not schema matched")
    if "if" in schema:
        branch = "then" if not schema_errors(value, schema["if"], where, root_schema) else "else"
        if branch in schema:
            errors.extend(schema_errors(value, schema[branch], where, root_schema))
    allowed_types = schema.get("type")
    if allowed_types is not None:
        names = allowed_types if isinstance(allowed_types, list) else [allowed_types]
        checks = {
            "object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
            "string": lambda v: isinstance(v, str), "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
            "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
            "boolean": lambda v: isinstance(v, bool), "null": lambda v: v is None,
        }
        if not any(checks.get(name, lambda _v: False)(value) for name in names):
            return [f"{where}: type must be {allowed_types!r}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: const must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: enum does not contain {value!r}")
    if isinstance(value, str):
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{where}: pattern {schema['pattern']!r} does not match")
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{where}: shorter than minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{where}: longer than maxLength")
        if schema.get("format") == "date-time" and not is_iso_datetime(value):
            errors.append(f"{where}: invalid ISO date-time")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{where}: less than minimum")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{where}: fewer than minItems")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            errors.append(f"{where}: items must be unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(schema_errors(item, item_schema, f"{where}[{index}]", root_schema))
    if isinstance(value, dict):
        if len(value) < schema.get("minProperties", 0):
            errors.append(f"{where}: fewer than minProperties")
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{where}: required property {key!r} is missing")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value.keys() - properties.keys():
                errors.append(f"{where}: additional property {key!r} is forbidden")
        for key, child in value.items():
            if key in properties:
                errors.extend(schema_errors(child, properties[key], f"{where}.{key}", root_schema))
            elif isinstance(schema.get("additionalProperties"), dict):
                errors.extend(schema_errors(child, schema["additionalProperties"], f"{where}.{key}", root_schema))
    return errors


def apply_schema(value: Any, schema_path: Path, subject: Path, errors: list[str]) -> None:
    try:
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        for detail in schema_errors(value, schema, root_schema=schema):
            errors.append(f"{subject}: schema violation: {detail}")
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{schema_path}: invalid schema: {exc}")


def validate_team(path: Path, root: Path, errors: list[str]) -> set[str]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{path}: invalid machine-readable YAML/JSON: {exc}")
        return set()
    if not isinstance(value, dict):
        errors.append(f"{path}: TEAM.yaml must be a mapping")
        return set()
    required = {"schema_version", "doc_type", "project_id", "project_name", "project_root", "governance_mode", "max_parallel", "agents"}
    if required - value.keys():
        errors.append(f"{path}: missing fields: {', '.join(sorted(required - value.keys()))}")
    if value.get("schema_version") != "1.0" or value.get("doc_type") != "team":
        errors.append(f"{path}: invalid schema_version/doc_type")
    try:
        if Path(str(value.get("project_root"))).expanduser().resolve() != root:
            errors.append(f"{path}: project_root does not match the validated project")
    except OSError as exc:
        errors.append(f"{path}: invalid project_root: {exc}")
    if not isinstance(value.get("max_parallel"), int) or isinstance(value.get("max_parallel"), bool) or value.get("max_parallel", 0) < 1:
        errors.append(f"{path}: max_parallel must be a positive integer")
    agents = value.get("agents")
    if not isinstance(agents, list) or not agents:
        errors.append(f"{path}: agents must be a non-empty list")
        return set()
    ids: list[str] = []
    for index, record in enumerate(agents):
        if not isinstance(record, dict):
            errors.append(f"{path}: agents[{index}] must be a mapping")
            continue
        agent_id = record.get("agent_id")
        if not isinstance(agent_id, str) or not AGENT_ID_RE.fullmatch(agent_id):
            errors.append(f"{path}: invalid agents[{index}].agent_id")
            continue
        ids.append(agent_id)
        if record.get("role_file") != f"agents/{agent_id}/ROLE.md" or record.get("system_prompt_file") != f"agents/{agent_id}/SYSTEM_PROMPT.md":
            errors.append(f"{path}: Agent {agent_id} identity paths are inconsistent")
    if len(ids) != len(set(ids)):
        errors.append(f"{path}: duplicate Agent IDs")
    return set(ids)


def validate_archive(path: Path, agent_id: str, errors: list[str]) -> str | None:
    meta = parse_frontmatter(path)
    if meta.get("doc_type") != "conversation_archive":
        errors.append(f"{path}: invalid archive doc_type")
    if meta.get("agent_id") != agent_id:
        errors.append(f"{path}: agent_id mismatch")
    body = document_body(path, "# 完整对话归档")
    actual = hashlib.sha256(body.encode("utf-8")).hexdigest()
    if meta.get("content_sha256") != actual:
        errors.append(f"{path}: content_sha256 mismatch")
    source_hash = meta.get("source_file_sha256", meta.get("source_sha256"))
    if source_hash is not None and (not isinstance(source_hash, str) or not HASH_RE.fullmatch(source_hash)):
        errors.append(f"{path}: invalid source hash metadata")
    normalized_hash = meta.get("normalized_body_sha256")
    if normalized_hash is not None and normalized_hash != actual:
        errors.append(f"{path}: normalized_body_sha256 mismatch")
    if not is_iso_datetime(meta.get("exported_at")) and "exported_at" in meta:
        errors.append(f"{path}: exported_at is not an ISO date-time")
    if contains_secret(path.read_text(encoding="utf-8")):
        errors.append(f"{path}: possible credential content")
    return actual


def validate_checkpoint_chain(bus: Path, agent: Path, archive_hashes: dict[str, str], errors: list[str]) -> None:
    checkpoints = sorted((agent / "conversations/checkpoints").glob("CP-*.md"))
    previous: str | None = None
    for index, path in enumerate(checkpoints, 1):
        expected = f"CP-{index:04d}"
        meta = parse_frontmatter(path)
        if path.stem != expected:
            errors.append(f"{agent.name}: checkpoint sequence expected {expected}, found {path.stem}")
        if meta.get("checkpoint_id") != expected or meta.get("agent_id") != agent.name:
            errors.append(f"{path}: checkpoint identity mismatch")
        if meta.get("previous_checkpoint") != previous:
            errors.append(f"{path}: previous_checkpoint must be {previous!r}")
        actual = hashlib.sha256(document_body(path, "# 上下文检查点").encode("utf-8")).hexdigest()
        if meta.get("content_sha256") != actual:
            errors.append(f"{path}: content_sha256 mismatch")
        sources = meta.get("source_archives")
        if not isinstance(sources, list) or not sources:
            errors.append(f"{path}: source_archives must be a non-empty list")
            sources = []
        expected_hashes = {str(source): archive_hashes.get(str(source)) for source in sources}
        supplied_hashes = meta.get("source_archive_hashes")
        if supplied_hashes is not None and supplied_hashes != expected_hashes:
            errors.append(f"{path}: source_archive_hashes do not match actual archives")
        for source in sources:
            target = bus / str(source)
            if not target.is_file():
                errors.append(f"{path}: missing source archive {source}")
            elif str(source) not in archive_hashes:
                errors.append(f"{path}: source is not a valid conversation archive: {source}")
        if contains_secret(path.read_text(encoding="utf-8")):
            errors.append(f"{path}: possible credential content")
        previous = expected
    current = agent / "conversations/CURRENT_CONTEXT.md"
    if checkpoints and current.is_file():
        expected_ref = f"conversations/checkpoints/{checkpoints[-1].name}"
        if parse_frontmatter(current).get("latest_checkpoint") != expected_ref:
            errors.append(f"{current}: latest_checkpoint must reference {expected_ref}")


def paths_overlap(left: str, right: str) -> bool:
    def prefix(pattern: str) -> str:
        return re.split(r"[*?[]", pattern, 1)[0].rstrip("/")
    a, b = left.rstrip("/"), right.rstrip("/")
    return fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a) or bool(prefix(a) and prefix(b) and (prefix(a).startswith(prefix(b) + "/") or prefix(b).startswith(prefix(a) + "/") or prefix(a) == prefix(b)))


def validate_tasks(bus: Path, agent_dirs: list[Path], team_ids: set[str], errors: list[str]) -> dict[str, tuple[dict[str, Any], Path]]:
    schema = bus / "schemas/task.schema.json"
    tasks: dict[str, tuple[dict[str, Any], Path]] = {}
    for agent in agent_dirs:
        for path in sorted((agent / "tasks").glob("*.md")):
            meta = parse_frontmatter(path)
            apply_schema(meta, schema, path, errors)
            task_id = meta.get("task_id")
            if not isinstance(task_id, str):
                continue
            if task_id in tasks:
                errors.append(f"{path}: duplicate task_id {task_id} (also {tasks[task_id][1]})")
            else:
                tasks[task_id] = (meta, path)
            owner = meta.get("owner")
            if owner not in team_ids:
                errors.append(f"{path}: owner is not declared in TEAM.yaml")
            if owner != agent.name:
                errors.append(f"{path}: task owner/directory mismatch")
    graph: dict[str, list[str]] = {}
    for task_id, (meta, path) in tasks.items():
        deps = meta.get("dependencies", [])
        if not isinstance(deps, list):
            deps = []
        graph[task_id] = [str(dep) for dep in deps]
        for dep in graph[task_id]:
            if dep not in tasks:
                errors.append(f"{path}: dependency does not exist: {dep}")
    state: dict[str, int] = {}
    def visit(node: str, trail: list[str]) -> None:
        if state.get(node) == 1:
            errors.append(f"task dependency cycle: {' -> '.join(trail + [node])}")
            return
        if state.get(node) == 2:
            return
        state[node] = 1
        for dep in graph.get(node, []):
            if dep in graph:
                visit(dep, trail + [node])
        state[node] = 2
    for task_id in graph:
        visit(task_id, [])
    def ordered(a: str, b: str) -> bool:
        seen: set[str] = set()
        stack = list(graph.get(a, []))
        while stack:
            node = stack.pop()
            if node == b:
                return True
            if node not in seen:
                seen.add(node); stack.extend(graph.get(node, []))
        return False
    ids = list(tasks)
    for i, left_id in enumerate(ids):
        left = tasks[left_id][0].get("allowed_writes", [])
        for right_id in ids[i + 1:]:
            right = tasks[right_id][0].get("allowed_writes", [])
            left_after_right = ordered(left_id, right_id)
            right_after_left = ordered(right_id, left_id)
            if left_after_right == right_after_left and any(paths_overlap(str(a), str(b)) for a in left for b in right):
                errors.append(f"tasks {left_id} and {right_id}: allowed_writes overlap but tasks are not serialized by dependencies")
    return tasks


def validate_evidence(bus: Path, value: Any, subject: Path, label: str, errors: list[str]) -> None:
    if not isinstance(value, list):
        return
    for index, record in enumerate(value):
        if not isinstance(record, dict) or set(record) != {"path", "sha256"}:
            errors.append(f"{subject}: {label}[{index}] must contain path and sha256")
            continue
        target = bus / str(record["path"])
        try:
            target.resolve().relative_to(bus.resolve())
        except ValueError:
            errors.append(f"{subject}: {label}[{index}] path escapes persistent store")
            continue
        if not target.is_file():
            errors.append(f"{subject}: {label}[{index}] path does not exist: {record['path']}")
        elif hashlib.sha256(target.read_bytes()).hexdigest() != record["sha256"]:
            errors.append(f"{subject}: {label} hash mismatch: {record['path']}")


def canonical_record_hash(record: dict[str, Any], field: str) -> str:
    value = json.loads(json.dumps(record))
    if field == "record_sha256":
        value[field] = None
    else:
        value.pop(field, None)
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def validate_secret_tree(value: Any, subject: Path, errors: list[str]) -> None:
    def walk(item: Any) -> bool:
        if isinstance(item, dict):
            return any(SECRET_FIELD_RE.search(str(key)) or walk(child) for key, child in item.items())
        if isinstance(item, list):
            return any(walk(child) for child in item)
        return isinstance(item, str) and contains_secret(item)
    if walk(value):
        errors.append(f"{subject}: possible credential field or value")


def checked_ref(bus: Path, subject: Path, ref: Any, digest: Any, label: str, errors: list[str], *, runtime_hash: bool = False) -> Path | None:
    if not isinstance(ref, str) or not isinstance(digest, str) or not HASH_RE.fullmatch(digest):
        errors.append(f"{subject}: invalid {label} reference/hash")
        return None
    target = Path(ref) if Path(ref).is_absolute() else bus / ref
    try:
        target.resolve().relative_to(bus.resolve())
    except ValueError:
        errors.append(f"{subject}: {label} reference escapes store")
        return None
    if not target.is_file():
        errors.append(f"{subject}: missing {label} reference: {ref}")
        return None
    actual = hashlib.sha256(target.read_bytes()).hexdigest()
    if runtime_hash:
        try:
            actual = read_json(target).get("record_hash", {}).get("value", actual)
        except ProtocolError:
            pass
    if actual != digest:
        errors.append(f"{subject}: {label} hash mismatch: {ref}")
    return target


def validate_runtime_material(bus: Path, agent_dirs: list[Path], tasks: dict[str, tuple[dict[str, Any], Path]], errors: list[str]) -> None:
    runtime_refs: set[str] = set()
    for agent in agent_dirs:
        profile_path = agent / "AGENT_PROFILE.json"
        if profile_path.is_file():
            try:
                profile = read_json(profile_path)
                apply_schema(profile, bus / "schemas/agent-profile.schema.json", profile_path, errors)
                if profile.get("agent_id") != agent.name:
                    errors.append(f"{profile_path}: agent_id mismatch")
                role = profile.get("role", {})
                checked_ref(bus, profile_path, role.get("path"), role.get("sha256"), "role", errors)
                validate_secret_tree(profile, profile_path, errors)
            except ProtocolError as exc:
                errors.append(str(exc))

        mapping_path = agent / "conversations/SESSION_MAP.json"
        mapping = read_json(mapping_path) if mapping_path.is_file() else {}
        bindings = ([mapping.get("active")] if mapping.get("active") else []) + list(mapping.get("history", []))
        for binding in bindings:
            if not isinstance(binding, dict) or not binding.get("runtime_profile_id"):
                continue
            ref = f"agents/{agent.name}/runtime/profiles/{binding['runtime_profile_id']}.json"
            runtime_refs.add(ref)
            target = checked_ref(bus, mapping_path, ref, binding.get("runtime_profile_sha256"), "runtime_profile_sha256", errors, runtime_hash=True)
            if target and isinstance(binding.get("session_id"), str):
                value = read_json(target).get("session", {})
                if value.get("status") == "known" and value.get("value") != binding["session_id"]:
                    errors.append(f"{mapping_path}: session/runtime profile mismatch")

        profiles = sorted((agent / "runtime/profiles").glob("RP-*.json"))
        previous: dict[str, Any] | None = None
        for number, path in enumerate(profiles, 1):
            expected = f"RP-{number:06d}"
            try:
                value = read_json(path)
            except ProtocolError as exc:
                errors.append(str(exc)); continue
            apply_schema(value, bus / "schemas/runtime-profile.schema.json", path, errors)
            if path.stem != expected or value.get("runtime_profile_id") != expected:
                errors.append(f"{path}: runtime profile sequence expected {expected}")
            if value.get("agent_id") != agent.name:
                errors.append(f"{path}: runtime profile agent_id mismatch")
            expected_previous = None if previous is None else {"runtime_profile_id": previous["runtime_profile_id"], "record_hash": previous["record_hash"]["value"]}
            if value.get("previous_profile") != expected_previous:
                errors.append(f"{path}: previous_profile hash chain mismatch")
            record_hash = value.get("record_hash", {}).get("value")
            if record_hash != canonical_record_hash(value, "record_hash"):
                errors.append(f"{path}: runtime record_hash mismatch")
            validate_secret_tree(value, path, errors)
            previous = value

        for path in sorted((agent / "activity").glob("**/ACTIVITY-*.json")):
            try:
                value = read_json(path)
            except ProtocolError as exc:
                errors.append(str(exc)); continue
            apply_schema(value, bus / "schemas/agent-activity.schema.json", path, errors)
            parts = path.relative_to(agent / "activity").parts
            run_id, task_id, attempt_id = parts[:3] if len(parts) >= 3 else (None, None, None)
            if (value.get("run_id"), value.get("task_id"), value.get("attempt_id"), value.get("agent_id")) != (run_id, task_id, attempt_id, agent.name):
                errors.append(f"{path}: activity Agent/Run/Task/Attempt attribution mismatch")
            task = tasks.get(str(task_id))
            if task is not None and task[0].get("owner") != agent.name:
                errors.append(f"{path}: activity task owner mismatch or missing task")
            runtime = value.get("runtime_profile", {})
            runtime_ref = runtime.get("native_binding_ref")
            if isinstance(runtime_ref, str) and not runtime_ref.startswith("agents/"):
                runtime_ref = f"agents/{agent.name}/{runtime_ref}"
            checked_ref(bus, path, runtime_ref, runtime.get("native_binding_sha256"), "runtime profile", errors)
            if isinstance(runtime_ref, str): runtime_refs.add(runtime_ref)
            source = value.get("source", {})
            source_ref = source.get("source_ref")
            if isinstance(source_ref, str) and source_ref.startswith(".multi-agent-collaboration/"):
                source_ref = source_ref.removeprefix(".multi-agent-collaboration/")
            elif isinstance(source_ref, str) and not source_ref.startswith("agents/"):
                source_ref = f"agents/{agent.name}/{source_ref}"
            checked_ref(bus, path, source_ref, source.get("source_sha256"), "activity source", errors)
            usage = value.get("usage", {})
            receipt_sources = {"provider_response", "runtime_meter", "billing_export"}
            token_fields = ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens", "total_tokens", "cost_minor_units")
            if usage.get("usage_source") not in receipt_sources and any(usage.get(key) is not None for key in token_fields):
                errors.append(f"{path}: usage values require a receipt-backed usage_source")
            if usage.get("usage_source") in receipt_sources:
                ref = usage.get("source_ref")
                if isinstance(ref, str) and not ref.startswith("agents/"): ref = f"agents/{agent.name}/{ref}"
                checked_ref(bus, path, ref, usage.get("source_sha256"), "usage receipt", errors)
            if value.get("record_sha256") != canonical_record_hash(value, "record_sha256"):
                errors.append(f"{path}: activity record_sha256 mismatch")
            validate_secret_tree(value, path, errors)

        for ledger in sorted((agent / "activity").glob("*/*/*")):
            if not ledger.is_dir(): continue
            records = sorted(ledger.glob("**/ACTIVITY-*.json"))
            previous_hash = None
            for number, path in enumerate(records, 1):
                value = read_json(path)
                if path.stem != f"ACTIVITY-{number:06d}" or value.get("activity_id") != path.stem or value.get("sequence") != number:
                    errors.append(f"{path}: activity sequence is not contiguous")
                if value.get("previous_record_sha256") != previous_hash:
                    errors.append(f"{path}: activity previous_record_sha256 mismatch")
                previous_hash = value.get("record_sha256")

    for path in sorted((bus / "bridges").glob("**/*.json")):
        value = read_json(path)
        run_id = value.get("run_id")
        for item in value.get("tasks", []):
            agent_id = item.get("agent_id", item.get("persistent_agent_id"))
            task_id = item.get("source_task_id", item.get("task_id"))
            archived_id = item.get("task_id")
            archived_task = next((meta for archived_id, (meta, _path) in tasks.items()
                                  if archived_id == item.get("task_id") and meta.get("owner") == agent_id), None)
            if (archived_id not in tasks or tasks[archived_id][0].get("owner") != agent_id) and archived_task is None:
                errors.append(f"{path}: bridge task/Agent owner mismatch")
            for ref_key, hash_key, label in (("runtime_profile_path", "runtime_profile_sha256", "bridge runtime"), ("activity_record_path", "activity_record_sha256", "bridge activity")):
                target = checked_ref(bus, path, item.get(ref_key), item.get(hash_key), label, errors)
                if target and label.endswith("activity"):
                    activity = read_json(target)
                    if activity.get("run_id") != run_id or activity.get("task_id") != task_id or (agent_id and activity.get("agent_id") != agent_id):
                        errors.append(f"{path}: bridge activity Run/Task/Agent mismatch")
                if label.endswith("runtime") and isinstance(item.get(ref_key), str): runtime_refs.add(item[ref_key])

    for agent in agent_dirs:
        for path in (agent / "runtime/profiles").glob("RP-*.json"):
            relative = path.relative_to(bus).as_posix()
            if relative not in runtime_refs:
                errors.append(f"{path}: orphan runtime profile has no legal session/activity/bridge source")


def validate_handoffs(bus: Path, agent_dirs: list[Path], tasks: dict[str, tuple[dict[str, Any], Path]], errors: list[str]) -> None:
    schema = bus / "schemas/handoff.schema.json"
    for agent in agent_dirs:
        for path in sorted((agent / "handoffs").glob("*.md")):
            meta = parse_frontmatter(path)
            apply_schema(meta, schema, path, errors)
            task_id = meta.get("task_id")
            if task_id not in tasks:
                errors.append(f"{path}: handoff references missing task {task_id}")
                continue
            owner = tasks[task_id][0].get("owner")
            if meta.get("agent_id") != owner or agent.name != owner:
                errors.append(f"{path}: handoff agent_id does not match task owner")
            if meta.get("status") == "completed":
                evidence = meta.get("acceptance_evidence")
                artifacts = meta.get("artifacts")
                if not evidence and not artifacts:
                    errors.append(f"{path}: completed handoff requires acceptance evidence or artifact")
                validate_evidence(bus, evidence, path, "evidence", errors)
                validate_evidence(bus, artifacts, path, "artifact", errors)


def validate_index(bus: Path, errors: list[str]) -> None:
    expected = {path.relative_to(bus).as_posix() for path, _data, _kind in discover_index_documents(bus)}
    index = bus / "index.jsonl"
    if not index.is_file():
        return
    actual: list[str] = []
    malformed = False
    for number, line in enumerate(index.read_text(encoding="utf-8").splitlines(), 1):
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            errors.append(f"index.jsonl:{number}: invalid JSON")
            malformed = True
            continue
        path = record.get("path") if isinstance(record, dict) else None
        if not isinstance(path, str) or not (bus / path).is_file():
            errors.append(f"index.jsonl:{number}: dangling path {path}")
            malformed = True
        else:
            actual.append(path)
            digest = record.get("hash") if isinstance(record, dict) else None
            if digest != hashlib.sha256((bus / path).read_bytes()).hexdigest():
                errors.append(f"index.jsonl:{number}: index hash mismatch: {path}")
                malformed = True
    if malformed or set(actual) != expected:
        missing = sorted(expected - set(actual))
        extra = sorted(set(actual) - expected)
        errors.append(f"index is not rebuildable from documents (missing={missing}, extra={extra})")


def validate_project_checkpoints(bus: Path, errors: list[str]) -> None:
    directory = bus / "project-checkpoints"
    paths = sorted(directory.glob("PCP-*.md")) if directory.is_dir() else []
    previous = None
    for number, path in enumerate(paths, 1):
        meta = parse_frontmatter(path)
        apply_schema(meta, bus / "schemas/project-checkpoint.schema.json", path, errors)
        expected_id = f"PCP-{number:04d}"
        if path.stem != expected_id or meta.get("checkpoint_id") != expected_id:
            errors.append(f"{path}: project checkpoint chain is not contiguous")
        if meta.get("previous_checkpoint") != previous:
            errors.append(f"{path}: previous_checkpoint mismatch")
        actual_body = hashlib.sha256(document_body(path).encode("utf-8")).hexdigest()
        if meta.get("content_sha256") != actual_body:
            errors.append(f"{path}: content_sha256 mismatch")
        source_hashes = meta.get("source_hashes")
        if isinstance(source_hashes, dict):
            for relative, expected in source_hashes.items():
                source = bus / str(relative)
                try:
                    source.resolve().relative_to(bus.resolve())
                except ValueError:
                    errors.append(f"{path}: project checkpoint source escapes store: {relative}")
                    continue
                if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != expected:
                    errors.append(f"{path}: project checkpoint source hash mismatch: {relative}")
        snapshots = meta.get("agent_runtime_snapshots", [])
        if isinstance(snapshots, list):
            for snapshot in snapshots:
                if not isinstance(snapshot, dict): continue
                agent_id = snapshot.get("agent_id")
                for key, label in (("runtime_profiles", "PCP runtime"), ("activity_refs", "PCP activity"), ("handoff_refs", "PCP handoff")):
                    for item in snapshot.get(key, []):
                        if not isinstance(item, dict): continue
                        target = checked_ref(bus, path, item.get("ref"), item.get("sha256"), label, errors)
                        if target and agent_id not in target.relative_to(bus).parts:
                            errors.append(f"{path}: {label} Agent attribution mismatch")
        previous = expected_id
    context = bus / "CURRENT_PROJECT_CONTEXT.md"
    if paths and context.is_file():
        meta = parse_frontmatter(context)
        expected_ref = paths[-1].relative_to(bus).as_posix()
        if meta.get("latest_project_checkpoint") != expected_ref:
            errors.append(f"{context}: latest_project_checkpoint does not point to latest PCP")


def validate_agent(bus: Path, agent: Path, errors: list[str]) -> dict[str, str]:
    if not AGENT_ID_RE.fullmatch(agent.name):
        errors.append(f"invalid agent directory name: {agent.name}")
    for relative in REQUIRED_AGENT_FILES:
        if not (agent / relative).is_file(): errors.append(f"{agent.name}: missing {relative}")
    for relative in REQUIRED_AGENT_DIRS:
        if not (agent / relative).is_dir(): errors.append(f"{agent.name}: missing directory {relative}")
    mapping_path = agent / "conversations/SESSION_MAP.json"
    mapping: dict[str, Any] = {}
    if mapping_path.is_file():
        try:
            mapping = read_json(mapping_path)
            apply_schema(mapping, bus / "schemas/session-map.schema.json", mapping_path, errors)
            if isinstance(mapping, dict) and mapping.get("agent_id") != agent.name:
                errors.append(f"{mapping_path}: agent_id mismatch")
            sessions: list[str] = []
            if isinstance(mapping, dict):
                if isinstance(mapping.get("active"), dict): sessions.append(str(mapping["active"].get("session_id")))
                for item in mapping.get("history", []):
                    if isinstance(item, dict): sessions.append(str(item.get("session_id")))
            if len(sessions) != len(set(sessions)): errors.append(f"{mapping_path}: duplicate session ID")
            if contains_secret(mapping_path.read_text(encoding="utf-8")): errors.append(f"{mapping_path}: possible credential content")
        except ProtocolError as exc:
            errors.append(str(exc))
    archive_hashes: dict[str, str] = {}
    for archive in sorted((agent / "conversations/archive").glob("**/*.md")):
        digest = validate_archive(archive, agent.name, errors)
        if digest is not None:
            archive_hashes[archive.relative_to(bus).as_posix()] = digest
    active = mapping.get("active") if isinstance(mapping, dict) else None
    if isinstance(active, dict) and isinstance(active.get("last_archive"), str):
        archive_path = agent / "conversations" / active["last_archive"]
        if not archive_path.is_file():
            errors.append(f"{mapping_path}: last_archive does not exist")
        else:
            archive_meta = parse_frontmatter(archive_path)
            if active.get("last_source_file_sha256") != archive_meta.get("source_file_sha256"):
                errors.append(f"{mapping_path}: last_source_file_sha256 does not match archive source hash metadata")
            if active.get("last_normalized_body_sha256") != archive_meta.get("normalized_body_sha256"):
                errors.append(f"{mapping_path}: last_normalized_body_sha256 does not match archive body hash metadata")
    validate_checkpoint_chain(bus, agent, archive_hashes, errors)
    return archive_hashes


def main() -> int:
    args = parser().parse_args(); errors: list[str] = []
    try:
        root = project_root(args.project_root)
        bus = bus_root(
            root,
            governance_root=args.governance_root,
            project_id=args.project_id,
        )
        for relative in REQUIRED_PROJECT_FILES:
            if not (bus / relative).is_file(): errors.append(f"missing project file: {relative}")
        declared = validate_team(bus / "TEAM.yaml", root, errors) if (bus / "TEAM.yaml").is_file() else set()
        for relative in REQUIRED_SUPPORT_DIRS:
            if not (bus / relative).is_dir(): errors.append(f"missing support directory: {relative}")
        agents_root = bus / "agents"
        agent_dirs = sorted(path for path in agents_root.iterdir() if path.is_dir()) if agents_root.is_dir() else []
        if not agents_root.is_dir(): errors.append("missing agents directory")
        if not agent_dirs: errors.append("no persistent Agent directories found")
        for agent in agent_dirs: validate_agent(bus, agent, errors)
        directory_ids = {agent.name for agent in agent_dirs}
        if declared != directory_ids: errors.append(f"TEAM.yaml Agent set {sorted(declared)} does not match agents/ directories {sorted(directory_ids)}")
        tasks = validate_tasks(bus, agent_dirs, declared, errors)
        validate_runtime_material(bus, agent_dirs, tasks, errors)
        validate_handoffs(bus, agent_dirs, tasks, errors)
        validate_project_checkpoints(bus, errors)
        validate_index(bus, errors)
    except (ProtocolError, OSError) as exc:
        errors.append(str(exc))
    if errors:
        print(f"FAIL: {len(errors)} error(s)")
        for error in errors: print(f"- {error}")
        return 1
    print("PASS: persistent Agent structure and references are valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
