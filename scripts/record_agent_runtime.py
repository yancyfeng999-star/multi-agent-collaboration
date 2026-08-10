#!/usr/bin/env python3
"""Record immutable, hash-chained Agent Runtime Profile snapshots."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from project_memory_lib import (
    agent_root,
    ensure_no_high_confidence_secrets,
    ensure_no_secret_fields,
    exclusive_lock,
    project_root,
)

FIELDS = ("model", "provider", "platform", "session", "profile", "workspace", "runtime_kind")
ENVIRONMENT_ALLOWLIST = {
    "HERMES_MODEL": ("model", "hermes"),
    "HERMES_PROVIDER": ("provider", "hermes"),
    "CODEX_MODEL": ("model", "codex"),
    "CODEX_PROVIDER": ("provider", "codex"),
    "CLAUDE_MODEL": ("model", "claude-code"),
    "CLAUDE_PROVIDER": ("provider", "claude-code"),
    "AGENT_MODEL": ("model", None),
    "AGENT_PROVIDER": ("provider", None),
}
PROFILE_RE = re.compile(r"^RP-(\d{6})$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")
PLATFORMS = {"hermes", "claude-code", "codex", "document", "other"}


class RuntimeRecordError(ValueError):
    """Fail-closed error whose message is always a non-sensitive code."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(value: Any) -> str:
    # The profile schema names RFC 8785. Profile values contain only strings,
    # nulls, booleans, arrays and objects, for which this stable form is JCS.
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_value(field: str, raw: Any, root: Path) -> str:
    if not isinstance(raw, str) or not raw or raw != raw.strip() or any(ord(char) < 32 or ord(char) == 127 for char in raw):
        raise RuntimeRecordError("INVALID_RUNTIME_VALUE")
    ensure_no_high_confidence_secrets(raw)
    if raw == "unknown":
        raise RuntimeRecordError("INVALID_RUNTIME_VALUE")
    if field in {"provider", "runtime_kind"}:
        # Provider aliases commonly use ``custom:name`` while the published
        # schema deliberately stores a portable slug.
        value = raw.lower().replace(":", "-")
        if not SLUG_RE.fullmatch(value):
            raise RuntimeRecordError("INVALID_RUNTIME_VALUE")
        return value
    if field == "platform":
        value = raw.lower().replace("_", "-")
        if value == "claude":
            value = "claude-code"
        if value not in PLATFORMS:
            value = "other"
        return value
    if field == "profile" and not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", raw):
        raise RuntimeRecordError("INVALID_RUNTIME_VALUE")
    if field == "session" and not re.fullmatch(r"[A-Za-z0-9._:@/-]{1,256}", raw):
        raise RuntimeRecordError("INVALID_RUNTIME_VALUE")
    if field == "workspace":
        path = Path(raw).expanduser().resolve()
        if not path.is_dir() or not (path == root or path.is_relative_to(root)):
            raise RuntimeRecordError("WORKSPACE_INVALID")
        return str(path)
    return raw


def _unknown(reason: str = "U001_NOT_EXPOSED") -> dict[str, Any]:
    return {
        "status": "unknown", "value": None, "confidence": "none",
        "selected_source_ids": [], "conflict_candidate_ids": [],
        "unknown_reason_code": reason,
        "resolution_note": "actual runtime value was not exposed by an approved source",
    }


def _source(source_id: str, source_type: str, locator: str, observed_at: str, values: Mapping[str, str]) -> dict[str, Any]:
    return {
        "source_id": source_id, "source_type": source_type, "claim_kind": "observed_actual",
        "locator": locator, "observed_at": observed_at, "freshness": "live",
        "trust": "strong" if source_type == "runtime_context" else "supporting",
        "probe_status": "success" if values else "empty",
        "evidence_hash": _digest(dict(sorted(values.items()))) if values else None,
        "error_code": None,
    }


def _snapshot_files(runtime: Path) -> dict[Path, bytes]:
    return {path: path.read_bytes() for path in runtime.rglob("*") if path.is_file() and path.name != ".runtime.lock"}


def _restore_snapshot(runtime: Path, snapshot: Mapping[Path, bytes]) -> None:
    for path in list(runtime.rglob("*")):
        if path.is_file() and path.name != ".runtime.lock" and path not in snapshot:
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


def _publish_transaction(runtime: Path, publications: list[tuple[Path, bytes]]) -> None:
    snapshot = _snapshot_files(runtime)
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
        _restore_snapshot(runtime, snapshot)
        raise


def _existing_state(runtime: Path) -> tuple[int, dict[str, Any] | None, str]:
    profiles = runtime / "profiles"
    numbers: list[int] = []
    if profiles.exists():
        for path in profiles.iterdir():
            match = PROFILE_RE.fullmatch(path.stem) if path.suffix == ".json" else None
            if match:
                numbers.append(int(match.group(1)))
    numbers.sort()
    if numbers != list(range(1, len(numbers) + 1)):
        raise RuntimeRecordError("RUNTIME_PROFILE_SEQUENCE_CORRUPT")
    current_path = runtime / "CURRENT_RUNTIME.json"
    current = None
    if current_path.exists():
        try:
            current = json.loads(current_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeRecordError("CURRENT_RUNTIME_CORRUPT") from exc
    index_path = runtime / "RUNTIME_INDEX.jsonl"
    index_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    if bool(numbers) != bool(current) or len([line for line in index_text.splitlines() if line]) != len(numbers):
        raise RuntimeRecordError("RUNTIME_LEDGER_INCONSISTENT")
    if current and current.get("runtime_profile_id") != f"RP-{numbers[-1]:06d}":
        raise RuntimeRecordError("CURRENT_RUNTIME_STALE")
    return len(numbers) + 1, current, index_text


def record_agent_runtime(
    *,
    project_root: str | Path,
    agent_id: str,
    observed: Mapping[str, Any] | None = None,
    environ: Any,
    capture_started_at: str | None = None,
    commit_callback: Callable[[dict[str, Any]], None] | None = None,
    governance_root: str | Path | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    """Build and transactionally publish one immutable Runtime Profile."""
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise RuntimeRecordError("PROJECT_ROOT_INVALID")
    agent = agent_root(
        root, agent_id,
        governance_root=governance_root,
        project_id=project_id,
    )
    observed = dict(observed or {})
    if set(observed) - set(FIELDS):
        raise RuntimeRecordError("SOURCE_SCHEMA_INVALID")

    # First safety gate: inspect only explicit input and fixed allowlisted keys.
    ensure_no_secret_fields(observed)
    ensure_no_high_confidence_secrets(_canonical(observed))
    started_at = capture_started_at or _now()
    normalized_explicit = {field: _safe_value(field, raw, root) for field, raw in observed.items() if raw is not None}
    normalized_env: dict[str, list[tuple[str, str]]] = {}
    for key, (field, platform) in ENVIRONMENT_ALLOWLIST.items():
        raw = environ.get(key)
        if raw is not None:
            value = _safe_value(field, raw, root)
            normalized_env.setdefault(field, []).append((value, key))

    runtime = agent / "runtime"
    runtime.mkdir(parents=True, exist_ok=True)
    with exclusive_lock(runtime / ".runtime.lock"):
        next_number, current, index_text = _existing_state(runtime)
        profile_id = f"RP-{next_number:06d}"
        captured_at = _now()

        source_values: list[tuple[str, str, str, dict[str, str]]] = []
        if normalized_explicit:
            source_values.append(("SRC-001", "runtime_context", "cli:approved-runtime-fields", normalized_explicit))
        env_flat = {field: values[0][0] for field, values in normalized_env.items() if values}
        if env_flat:
            source_values.append((f"SRC-{len(source_values) + 1:03d}", "runtime_context", "environment:fixed-allowlist", env_flat))
        if not source_values:
            source_values.append(("SRC-001", "runtime_context", "runtime:approved-probe-empty", {}))
        sources = [_source(source_id, kind, locator, captured_at, values) for source_id, kind, locator, values in source_values]
        source_for = {locator: source_id for source_id, _kind, locator, _values in source_values}

        candidates: list[dict[str, Any]] = []
        resolved: dict[str, dict[str, Any]] = {}
        for field in FIELDS:
            evidence: list[tuple[str, str]] = []
            if field in normalized_explicit:
                evidence.append((normalized_explicit[field], source_for["cli:approved-runtime-fields"]))
            for value, _key in normalized_env.get(field, []):
                evidence.append((value, source_for["environment:fixed-allowlist"]))
            grouped: dict[str, list[str]] = {}
            for value, source_id in evidence:
                if source_id not in grouped.setdefault(value, []):
                    grouped[value].append(source_id)
            field_ids: list[str] = []
            for value, source_ids in grouped.items():
                candidate_id = f"CND-{len(candidates) + 1:03d}"
                field_ids.append(candidate_id)
                candidates.append({
                    "candidate_id": candidate_id, "field": field, "normalized_value": value,
                    "source_ids": source_ids, "claim_kind": "observed_actual",
                    "confidence": "high" if source_ids[0] == "SRC-001" and normalized_explicit else "medium",
                    "selected": len(grouped) == 1,
                })
            if not grouped:
                resolved[field] = _unknown()
            elif len(grouped) > 1:
                resolved[field] = {
                    "status": "conflict", "value": None, "confidence": "none",
                    "selected_source_ids": [], "conflict_candidate_ids": field_ids,
                    "unknown_reason_code": None,
                    "resolution_note": "approved actual-runtime observations disagree; no value was selected",
                }
            else:
                value, source_ids = next(iter(grouped.items()))
                resolved[field] = {
                    "status": "known", "value": value,
                    "confidence": "high" if source_ids[0] == "SRC-001" and normalized_explicit else "medium",
                    "selected_source_ids": source_ids, "conflict_candidate_ids": [],
                    "unknown_reason_code": None, "resolution_note": "selected from an approved actual-runtime observation",
                }

        statuses = [resolved[field]["status"] for field in FIELDS]
        if "conflict" in statuses:
            capture_status = {"code": "S002", "name": "conflicted"}
        elif all(status == "unknown" for status in statuses):
            capture_status = {"code": "S003", "name": "unresolved"}
        elif all(status == "known" for status in statuses):
            capture_status = {"code": "S000", "name": "complete"}
        else:
            capture_status = {"code": "S001", "name": "partial"}

        fingerprint_fields = [field for field in ("model", "provider", "platform", "profile", "runtime_kind") if resolved[field]["status"] == "known"]
        if fingerprint_fields:
            config_fingerprint = {
                "status": "known", "algorithm": "sha256", "canonicalization": "jcs-rfc8785",
                "scope_version": "runtime-config-v1", "included_fields": fingerprint_fields,
                "value": _digest({field: resolved[field]["value"] for field in fingerprint_fields}),
                "unknown_reason_code": None,
            }
        else:
            config_fingerprint = {
                "status": "unknown", "algorithm": "sha256", "canonicalization": "jcs-rfc8785",
                "scope_version": "runtime-config-v1", "included_fields": [], "value": None,
                "unknown_reason_code": "U004_INSUFFICIENT_EVIDENCE",
            }

        previous = None if current is None else {
            "runtime_profile_id": current["runtime_profile_id"], "record_hash": current["record_hash"],
        }
        profile: dict[str, Any] = {
            "schema_version": "1.0", "doc_type": "runtime_profile",
            "runtime_profile_id": profile_id, "agent_id": agent_id,
            "capture_status": capture_status, "capture_started_at": started_at, "captured_at": captured_at,
            **resolved, "sources": sources, "candidates": candidates, "declared_defaults": [],
            "config_fingerprint": config_fingerprint, "previous_profile": previous,
        }
        profile["record_hash"] = {
            "algorithm": "sha256", "canonicalization": "jcs-rfc8785", "value": _digest(profile),
        }
        pointer = {
            "runtime_profile_id": profile_id, "record_hash": profile["record_hash"]["value"],
            "path": f"profiles/{profile_id}.json",
        }
        index_record = {
            "runtime_profile_id": profile_id, "agent_id": agent_id, "captured_at": captured_at,
            "capture_status": capture_status["name"], "record_hash": profile["record_hash"]["value"],
            "path": pointer["path"],
        }
        profile_bytes = (json.dumps(profile, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        pointer_bytes = (json.dumps(pointer, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        index_bytes = (index_text + json.dumps(index_record, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")

        # Second safety gate covers the exact bytes destined for persistent storage.
        for content in (profile_bytes, pointer_bytes, index_bytes):
            ensure_no_high_confidence_secrets(content.decode("utf-8"))
        destination = runtime / "profiles" / f"{profile_id}.json"
        if destination.exists():
            raise RuntimeRecordError("IMMUTABLE_PROFILE_EXISTS")
        snapshot = _snapshot_files(runtime)
        try:
            _publish_transaction(runtime, [
                (destination, profile_bytes),
                (runtime / "RUNTIME_INDEX.jsonl", index_bytes),
                (runtime / "CURRENT_RUNTIME.json", pointer_bytes),
            ])
            if commit_callback is not None:
                commit_callback(pointer)
        except Exception:
            _restore_snapshot(runtime, snapshot)
            raise
        return pointer


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--governance-root")
    parser.add_argument("--project-id")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--model")
    parser.add_argument("--provider")
    parser.add_argument("--platform")
    parser.add_argument("--session-id", dest="session")
    parser.add_argument("--profile")
    parser.add_argument("--workspace")
    parser.add_argument("--runtime-kind")
    parser.add_argument("--capture-started-at")
    return parser


def main() -> int:
    args = _parser().parse_args()
    observed = {field: getattr(args, field) for field in FIELDS if getattr(args, field) is not None}
    try:
        pointer = record_agent_runtime(
            project_root=args.project_root, agent_id=args.agent_id, observed=observed,
            environ=os.environ, capture_started_at=args.capture_started_at,
            governance_root=args.governance_root, project_id=args.project_id,
        )
    except Exception as exc:
        code = exc.args[0] if isinstance(exc, RuntimeRecordError) and exc.args else "RUNTIME_RECORD_FAILED"
        print(str(code), file=sys.stderr)
        return 1
    print(json.dumps(pointer, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
