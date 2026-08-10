#!/usr/bin/env python3
"""Record immutable, hash-chained Agent Activity Ledger entries."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

from project_memory_lib import agent_root, ensure_no_high_confidence_secrets, exclusive_lock

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "assets" / "schemas" / "agent-activity.schema.json"
GENERATED_FIELDS = {"activity_id", "sequence", "idempotency_key", "previous_record_sha256", "record_sha256"}
PARTITION_RE = {
    "run_id": re.compile(r"^RUN-[A-Za-z0-9][A-Za-z0-9._-]*$"),
    "task_id": re.compile(r"^TASK-[A-Za-z0-9][A-Za-z0-9._-]*$"),
    "attempt_id": re.compile(r"^ATTEMPT-[A-Za-z0-9][A-Za-z0-9._-]*$"),
}
ACTIVITY_RE = re.compile(r"^ACTIVITY-(\d{6})$")
SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key|secret|token|password|passwd|pwd|credential|"
    r"authorization|cookie|private[_-]?key|client[_-]?secret|refresh[_-]?token|"
    r"session[_-]?token|bearer|signature|webhook)"
)
ALLOWED_USAGE_KEYS = {"input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens", "total_tokens"}


class ActivityRecordError(ValueError):
    """Fail-closed error with a non-sensitive error code."""


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record_hash(record: Mapping[str, Any]) -> str:
    value = copy.deepcopy(dict(record))
    value["record_sha256"] = None
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _parse_time(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ActivityRecordError("RECORDED_AT_INVALID")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ActivityRecordError("RECORDED_AT_INVALID") from exc
    if parsed.tzinfo is None:
        raise ActivityRecordError("RECORDED_AT_INVALID")
    return parsed


def _resolve_ref(agent: Path, raw: Any, code: str) -> Path:
    if not isinstance(raw, str) or not raw or "://" in raw or "?" in raw or "#" in raw:
        raise ActivityRecordError(code)
    candidate = (agent / raw).resolve()
    try:
        candidate.relative_to(agent.resolve())
    except ValueError as exc:
        raise ActivityRecordError(code) from exc
    if not candidate.is_file():
        raise ActivityRecordError(code)
    return candidate


def _verify_bound_file(agent: Path, reference: Any, expected_hash: Any, code: str) -> None:
    path = _resolve_ref(agent, reference, code)
    if not isinstance(expected_hash, str) or hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
        raise ActivityRecordError(code)


def _schema_errors(value: Any, schema: Mapping[str, Any], root: Mapping[str, Any], where: str = "$") -> list[str]:
    if "$ref" in schema:
        target: Any = root
        for part in str(schema["$ref"]).removeprefix("#/").split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return _schema_errors(value, target, root, where)
    errors: list[str] = []
    if "anyOf" in schema and not any(not _schema_errors(value, branch, root, where) for branch in schema["anyOf"]):
        errors.append(f"{where}: no anyOf branch matched")
    for branch in schema.get("allOf", []):
        errors.extend(_schema_errors(value, branch, root, where))
    if "if" in schema and not _schema_errors(value, schema["if"], root, where):
        errors.extend(_schema_errors(value, schema.get("then", {}), root, where))
    if "not" in schema and not _schema_errors(value, schema["not"], root, where):
        errors.append(f"{where}: forbidden")
    allowed = schema.get("type")
    if allowed is not None:
        allowed_types = allowed if isinstance(allowed, list) else [allowed]
        checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "null": lambda item: item is None,
            "boolean": lambda item: isinstance(item, bool),
        }
        if not any(checks[kind](value) for kind in allowed_types):
            return errors + [f"{where}: wrong type"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: wrong const")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: invalid enum")
    if isinstance(value, str):
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{where}: pattern mismatch")
        if len(value) < schema.get("minLength", 0) or len(value) > schema.get("maxLength", len(value)):
            errors.append(f"{where}: length invalid")
        if schema.get("format") == "date-time":
            try:
                _parse_time(value)
            except ActivityRecordError:
                errors.append(f"{where}: datetime invalid")
    if isinstance(value, int) and not isinstance(value, bool) and value < schema.get("minimum", value):
        errors.append(f"{where}: below minimum")
    if isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(_schema_errors(item, schema.get("items", {}), root, f"{where}[{index}]"))
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{where}: missing {key}")
        if schema.get("additionalProperties") is False:
            errors.extend(f"{where}: unknown {key}" for key in value.keys() - properties.keys())
        for key, item in value.items():
            if key in properties:
                errors.extend(_schema_errors(item, properties[key], root, f"{where}.{key}"))
    return errors


def _snapshot_files(directory: Path) -> dict[Path, bytes]:
    if not directory.exists():
        return {}
    return {path: path.read_bytes() for path in directory.rglob("*") if path.is_file() and path.name != ".activity.lock"}


def _restore_snapshot(directory: Path, snapshot: Mapping[Path, bytes]) -> None:
    if directory.exists():
        for path in list(directory.rglob("*")):
            if path.is_file() and path.name != ".activity.lock" and path not in snapshot:
                path.unlink()
    for path, content in snapshot.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)


def _stage(directory: Path, name: str, content: bytes) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp", dir=directory)
    path = Path(raw_path)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        return path
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _publish_transaction(ledger: Path, publications: list[tuple[Path, bytes]]) -> None:
    snapshot = _snapshot_files(ledger)
    staged: list[tuple[Path, Path]] = []
    try:
        for destination, content in publications:
            destination.parent.mkdir(parents=True, exist_ok=True)
            staged.append((_stage(destination.parent, destination.name, content), destination))
        for source, destination in staged:
            os.replace(source, destination)
    except Exception:
        for source, _destination in staged:
            source.unlink(missing_ok=True)
        _restore_snapshot(ledger, snapshot)
        raise


def _existing_state(ledger: Path) -> tuple[int, dict[str, Any] | None, str]:
    index_path = ledger / "INDEX.jsonl"
    index_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    try:
        entries = [json.loads(line) for line in index_text.splitlines() if line]
    except json.JSONDecodeError as exc:
        raise ActivityRecordError("ACTIVITY_INDEX_CORRUPT") from exc
    expected = list(range(1, len(entries) + 1))
    if [entry.get("sequence") for entry in entries] != expected:
        raise ActivityRecordError("ACTIVITY_SEQUENCE_CORRUPT")
    if [entry.get("activity_id") for entry in entries] != [f"ACTIVITY-{number:06d}" for number in expected]:
        raise ActivityRecordError("ACTIVITY_SEQUENCE_CORRUPT")
    current_path = ledger / "CURRENT.json"
    current = None
    if current_path.exists():
        try:
            current = json.loads(current_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ActivityRecordError("ACTIVITY_CURRENT_CORRUPT") from exc
    if bool(entries) != bool(current):
        raise ActivityRecordError("ACTIVITY_LEDGER_INCONSISTENT")
    if entries and (current.get("activity_id") != entries[-1].get("activity_id") or current.get("record_sha256") != entries[-1].get("record_sha256")):
        raise ActivityRecordError("ACTIVITY_CURRENT_STALE")
    for entry in entries:
        path = ledger / entry.get("path", "")
        if not path.is_file():
            raise ActivityRecordError("ACTIVITY_LEDGER_INCONSISTENT")
    return len(entries) + 1, current, index_text


def _scan_secret_fields(value: Any, path: str = "$") -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if SECRET_KEY_RE.search(str(key)) and key not in ALLOWED_USAGE_KEYS:
                raise ActivityRecordError("SECRET_FIELD_DETECTED")
            _scan_secret_fields(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_secret_fields(child, f"{path}[{index}]")


def _validate_payload(payload: Mapping[str, Any], agent_id: str, agent: Path) -> datetime:
    if not isinstance(payload, dict) or GENERATED_FIELDS.intersection(payload):
        raise ActivityRecordError("ACTIVITY_INPUT_INVALID")
    _scan_secret_fields(payload)
    ensure_no_high_confidence_secrets(_canonical(payload))
    for field, pattern in PARTITION_RE.items():
        if not isinstance(payload.get(field), str) or pattern.fullmatch(payload[field]) is None:
            raise ActivityRecordError("ACTIVITY_PARTITION_INVALID")
    if payload.get("agent_id") != agent_id:
        raise ActivityRecordError("AGENT_ID_MISMATCH")
    session = payload.get("session_id")
    if not isinstance(session, str) or not session:
        raise ActivityRecordError("SESSION_ID_REQUIRED")
    runtime = payload.get("runtime_profile")
    if not isinstance(runtime, dict):
        raise ActivityRecordError("RUNTIME_PROFILE_REQUIRED")
    _verify_bound_file(agent, runtime.get("native_binding_ref"), runtime.get("native_binding_sha256"), "RUNTIME_PROFILE_BINDING_INVALID")
    usage = payload.get("usage")
    if not isinstance(usage, dict) or "usage_source" not in usage:
        raise ActivityRecordError("USAGE_INVALID")
    if usage.get("usage_source") in {"provider_response", "runtime_meter", "billing_export"}:
        _verify_bound_file(agent, usage.get("source_ref"), usage.get("source_sha256"), "USAGE_RECEIPT_INVALID")
    return _parse_time(payload.get("recorded_at"))


def record_agent_activity(
    *, project_root: str | Path, agent_id: str, payload: Mapping[str, Any],
    governance_root: str | Path | None = None, project_id: str | None = None,
) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ActivityRecordError("PROJECT_ROOT_INVALID")
    agent = agent_root(
        root, agent_id,
        governance_root=governance_root,
        project_id=project_id,
    )
    payload = copy.deepcopy(dict(payload))
    recorded = _validate_payload(payload, agent_id, agent)
    ledger = agent / "activity" / payload["run_id"] / payload["task_id"] / payload["attempt_id"]
    ledger.mkdir(parents=True, exist_ok=True)
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    with exclusive_lock(ledger / ".activity.lock"):
        next_number, current, index_text = _existing_state(ledger)
        activity_id = f"ACTIVITY-{next_number:06d}"
        record = {
            **payload,
            "activity_id": activity_id,
            "sequence": next_number,
            "idempotency_key": f"{payload['run_id']}:{payload['task_id']}:{payload['attempt_id']}:{agent_id}:{activity_id}:v1",
            "previous_record_sha256": None if current is None else current["record_sha256"],
            "record_sha256": None,
        }
        record["record_sha256"] = _record_hash(record)
        errors = _schema_errors(record, schema, schema)
        if errors:
            raise ActivityRecordError("ACTIVITY_SCHEMA_INVALID")
        if _record_hash(record) != record["record_sha256"]:
            raise ActivityRecordError("ACTIVITY_HASH_INVALID")

        relative_path = f"{recorded.year:04d}/{recorded.month:02d}/{recorded.day:02d}/{activity_id}.json"
        destination = ledger / relative_path
        if destination.exists():
            raise ActivityRecordError("IMMUTABLE_ACTIVITY_EXISTS")
        pointer = {"activity_id": activity_id, "sequence": next_number, "record_sha256": record["record_sha256"], "path": relative_path}
        index_record = {
            **pointer, "recorded_at": record["recorded_at"], "run_id": record["run_id"],
            "task_id": record["task_id"], "attempt_id": record["attempt_id"], "agent_id": agent_id,
            "session_id": record["session_id"], "runtime_profile_ref": record["runtime_profile"]["native_binding_ref"],
            "previous_record_sha256": record["previous_record_sha256"],
        }
        record_bytes = (json.dumps(record, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        pointer_bytes = (json.dumps(pointer, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        index_bytes = (index_text + json.dumps(index_record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for content in (record_bytes, pointer_bytes, index_bytes):
            ensure_no_high_confidence_secrets(content.decode("utf-8"))
        _publish_transaction(ledger, [
            (destination, record_bytes),
            (ledger / "INDEX.jsonl", index_bytes),
            (ledger / "CURRENT.json", pointer_bytes),
        ])
        return pointer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--governance-root")
    parser.add_argument("--project-id")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--input", required=True, help="JSON activity payload path, or - for stdin")
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.input == "-":
            payload = json.load(sys.stdin)
        else:
            payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
        pointer = record_agent_activity(
            project_root=args.project_root, agent_id=args.agent_id, payload=payload,
            governance_root=args.governance_root, project_id=args.project_id,
        )
    except Exception as exc:
        code = exc.args[0] if isinstance(exc, ActivityRecordError) and exc.args else "ACTIVITY_RECORD_FAILED"
        print(str(code), file=sys.stderr)
        return 1
    print(json.dumps(pointer, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
