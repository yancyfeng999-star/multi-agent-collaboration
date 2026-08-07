#!/usr/bin/env python3
"""Plan and transactionally migrate legacy Agent runtime facts into Runtime Profiles."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from project_memory_lib import exclusive_lock

FIELDS = ("model", "provider", "platform", "session", "profile", "workspace", "runtime_kind")
PROTECTED_PARTS = {"archive", "checkpoints", "checkpoint", "events", "evidence", "bridge"}
TOOL_VERSION = "1.0"


class MigrationError(RuntimeError):
    def __init__(self, code: str, exit_code: int = 2):
        super().__init__(code)
        self.code = code
        self.exit_code = exit_code


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def value_hash(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (OSError, ValueError):
        return False


def _agents(bus: Path) -> list[Path]:
    root = bus / "agents"
    return sorted((p for p in root.iterdir() if p.is_dir()), key=lambda p: p.name) if root.is_dir() else []


def _source_inventory(bus: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for path in sorted(bus.rglob("*"), key=lambda p: p.relative_to(bus).as_posix()):
        relative = path.relative_to(bus)
        if not path.is_file() or "runtime" in relative.parts or "migrations" in relative.parts or path.name == ".runtime-migration.lock":
            continue
        if path.is_symlink() or not _inside(path, bus):
            raise MigrationError("PATH_ESCAPE")
        result[relative.as_posix()] = {"size": path.stat().st_size, "sha256": file_hash(path)}
    return result


def _protected_inventory(bus: Path) -> dict[str, str]:
    return {
        relative: metadata["sha256"]
        for relative, metadata in _source_inventory(bus).items()
        if set(Path(relative).parts) & PROTECTED_PARTS
    }


def _read_session_map(agent: Path) -> dict[str, Any]:
    path = agent / "conversations" / "SESSION_MAP.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError("LEGACY_SOURCE_CORRUPT") from exc
    return value if isinstance(value, dict) else {}


def _target_state(agent: Path) -> str:
    runtime = agent / "runtime"
    if runtime.is_symlink() or (runtime.exists() and not _inside(runtime, agent)):
        raise MigrationError("PATH_ESCAPE")
    if not runtime.exists():
        return "absent"
    profile = runtime / "profiles" / "RP-000001.json"
    pointer = runtime / "CURRENT_RUNTIME.json"
    index = runtime / "RUNTIME_INDEX.jsonl"
    present = [profile.is_file(), pointer.is_file(), index.is_file()]
    if not all(present):
        raise MigrationError("PARTIAL_TARGET")
    try:
        profile_value = json.loads(profile.read_text(encoding="utf-8"))
        pointer_value = json.loads(pointer.read_text(encoding="utf-8"))
        rows = [json.loads(line) for line in index.read_text(encoding="utf-8").splitlines() if line]
    except (OSError, json.JSONDecodeError) as exc:
        raise MigrationError("TARGET_CORRUPT") from exc
    digest = profile_value.get("record_hash", {}).get("value")
    if (profile_value.get("runtime_profile_id") != "RP-000001" or
            profile_value.get("capture_status") != {"code": "S004", "name": "legacy_imported"} or
            pointer_value.get("record_hash") != digest or len(rows) != 1 or rows[0].get("record_hash") != digest):
        raise MigrationError("TARGET_CONFLICT")
    unhashed = dict(profile_value)
    unhashed.pop("record_hash", None)
    if digest != value_hash(unhashed):
        raise MigrationError("TARGET_CONFLICT")
    extras = [p for p in runtime.rglob("*") if p.is_file() and p not in {profile, pointer, index} and p.name != ".runtime.lock"]
    if extras:
        raise MigrationError("PARTIAL_TARGET")
    return "current"


def make_plan(project_root: Path) -> dict[str, Any]:
    root = project_root.expanduser().resolve()
    bus = root / ".multi-agent-collaboration"
    if not bus.is_dir():
        raise MigrationError("UNINITIALIZED")
    agents = _agents(bus)
    states = [(agent, _target_state(agent)) for agent in agents]
    absent = [agent for agent, state in states if state == "absent"]
    current = [agent for agent, state in states if state == "current"]
    if absent and current:
        raise MigrationError("PARTIAL_TARGET")
    inventory = _source_inventory(bus)
    plan: dict[str, Any] = {
        "schema_version": "1.0",
        "doc_type": "runtime_migration_plan",
        "tool_version": TOOL_VERSION,
        "classification": "legacy-recognized" if absent else "current",
        "source_inventory_sha256": value_hash(inventory),
        "protected_history_inventory_sha256": value_hash(_protected_inventory(bus)),
        "operations": [
            {"agent_id": agent.name, "action": "create_runtime_profile"} for agent in absent
        ],
        "writes": [
            f"agents/{agent.name}/runtime/{relative}"
            for agent in absent
            for relative in ("profiles/RP-000001.json", "RUNTIME_INDEX.jsonl", "CURRENT_RUNTIME.json")
        ],
    }
    plan["plan_hash"] = value_hash(plan)
    return plan


def _missing(reason: str = "U008_LEGACY_NOT_COLLECTED") -> dict[str, Any]:
    return {
        "status": "not_collected", "value": None, "confidence": "none",
        "selected_source_ids": [], "conflict_candidate_ids": [],
        "unknown_reason_code": reason,
        "resolution_note": "source schema did not report this runtime fact",
    }


def _known(value: str) -> dict[str, Any]:
    return {
        "status": "known", "value": value, "confidence": "low",
        "selected_source_ids": ["SRC-001"], "conflict_candidate_ids": [],
        "unknown_reason_code": None,
        "resolution_note": "imported verbatim from a legacy runtime field",
    }


def _legacy_values(agent: Path) -> dict[str, str]:
    active = _read_session_map(agent).get("active")
    active = active if isinstance(active, dict) else {}
    values: dict[str, str] = {}
    platform = active.get("platform")
    if isinstance(platform, str) and platform in {"hermes", "claude-code", "codex", "document", "other"}:
        values["platform"] = platform
    session = active.get("session_id") or active.get("session")
    if isinstance(session, str) and session and len(session) <= 256:
        values["session"] = session
    return values


def _profile(agent: Path, captured_at: str) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    values = _legacy_values(agent)
    sources = [{
        "source_id": "SRC-001", "source_type": "legacy_document", "claim_kind": "legacy_import",
        "locator": "conversations/SESSION_MAP.json", "observed_at": captured_at,
        "freshness": "historical", "trust": "weak",
        "probe_status": "success" if values else "empty",
        "evidence_hash": value_hash(values) if values else None, "error_code": None,
    }]
    candidates: list[dict[str, Any]] = []
    resolved = {field: _missing() for field in FIELDS}
    for field, value in values.items():
        source_id = f"SRC-{len(sources) + 1:03d}"
        candidate_id = f"CND-{len(candidates) + 1:03d}"
        sources.append({
            "source_id": source_id, "source_type": "legacy_document", "claim_kind": "observed_actual",
            "locator": f"conversations/SESSION_MAP.json:active.{field}", "observed_at": captured_at,
            "freshness": "historical", "trust": "weak", "probe_status": "success",
            "evidence_hash": value_hash({field: value}), "error_code": None,
        })
        candidates.append({
            "candidate_id": candidate_id, "field": field, "normalized_value": value,
            "source_ids": [source_id], "claim_kind": "observed_actual", "confidence": "low", "selected": True,
        })
        resolved[field] = {
            **_known(value), "selected_source_ids": [source_id],
        }
    fingerprint_fields = [field for field in ("platform",) if field in values]
    fingerprint = ({
        "status": "known", "algorithm": "sha256", "canonicalization": "jcs-rfc8785",
        "scope_version": "runtime-config-v1", "included_fields": fingerprint_fields,
        "value": value_hash({field: values[field] for field in fingerprint_fields}), "unknown_reason_code": None,
    } if fingerprint_fields else {
        "status": "unknown", "algorithm": "sha256", "canonicalization": "jcs-rfc8785",
        "scope_version": "runtime-config-v1", "included_fields": [], "value": None,
        "unknown_reason_code": "U004_INSUFFICIENT_EVIDENCE",
    })
    profile: dict[str, Any] = {
        "schema_version": "1.0", "doc_type": "runtime_profile", "runtime_profile_id": "RP-000001",
        "agent_id": agent.name, "capture_status": {"code": "S004", "name": "legacy_imported"},
        "capture_started_at": captured_at, "captured_at": captured_at, **resolved,
        "sources": sources, "candidates": candidates, "declared_defaults": [],
        "config_fingerprint": fingerprint, "previous_profile": None,
    }
    profile["record_hash"] = {"algorithm": "sha256", "canonicalization": "jcs-rfc8785", "value": value_hash(profile)}
    digest = profile["record_hash"]["value"]
    pointer = {"runtime_profile_id": "RP-000001", "record_hash": digest, "path": "profiles/RP-000001.json"}
    index = {"runtime_profile_id": "RP-000001", "agent_id": agent.name, "captured_at": captured_at,
             "capture_status": "legacy_imported", "record_hash": digest, "path": "profiles/RP-000001.json"}
    return profile, pointer, index


def _bytes(value: Any, *, compact: bool = False) -> bytes:
    text = json.dumps(value, ensure_ascii=False, sort_keys=compact, indent=None if compact else 2)
    return (text + "\n").encode("utf-8")


def _atomic_replace(destination: Path, content: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, raw = tempfile.mkstemp(prefix=f".{destination.name}.", suffix=".tmp", dir=destination.parent)
    temporary = Path(raw)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def apply_plan(root: Path, supplied_hash: str) -> dict[str, Any]:
    bus = root / ".multi-agent-collaboration"
    lock = bus / ".runtime-migration.lock"
    try:
        with exclusive_lock(lock):
            plan = make_plan(root)
            if supplied_hash != plan["plan_hash"]:
                raise MigrationError("SOURCE_DRIFT", 3)
            if not plan["operations"]:
                return {"status": "NO_OP_CURRENT", "plan_hash": supplied_hash}
            protected_before = _protected_inventory(bus)
            captured_at = utc_now()
            publications: list[tuple[Path, bytes]] = []
            created_runtime: list[Path] = []
            for operation in plan["operations"]:
                agent = bus / "agents" / operation["agent_id"]
                profile, pointer, index = _profile(agent, captured_at)
                runtime = agent / "runtime"
                created_runtime.append(runtime)
                publications.extend([
                    (runtime / "profiles" / "RP-000001.json", _bytes(profile)),
                    (runtime / "RUNTIME_INDEX.jsonl", _bytes(index, compact=True)),
                    (runtime / "CURRENT_RUNTIME.json", _bytes(pointer)),
                ])
            migration_id = "MIG-" + supplied_hash[:16]
            staging = Path(tempfile.mkdtemp(prefix=".runtime-migration-", dir=bus))
            replaced = 0
            try:
                # Stage a byte-identical tree first so dry-run/apply share the plan while apply validates serialization.
                for destination, content in publications:
                    relative = destination.relative_to(bus)
                    staged = staging / relative
                    staged.parent.mkdir(parents=True, exist_ok=True)
                    staged.write_bytes(content)
                    json.loads(content.decode("utf-8"))
                for destination, content in publications:
                    if destination.exists():
                        raise MigrationError("PARTIAL_TARGET")
                    _atomic_replace(destination, content)
                    replaced += 1
                    if os.environ.get("RUNTIME_MIGRATION_FAIL_AFTER") == f"after_replace_{replaced}":
                        raise OSError("injected failure")
                if _protected_inventory(bus) != protected_before:
                    raise MigrationError("PROTECTED_HISTORY_CHANGED")
                manifest = {
                    "schema_version": "1.0", "doc_type": "runtime_migration_manifest",
                    "migration_id": migration_id, "status": "COMMITTED", "plan_sha256": supplied_hash,
                    "source_inventory_sha256": plan["source_inventory_sha256"],
                    "protected_history_inventory_sha256": plan["protected_history_inventory_sha256"],
                    "protected_history_verified": True, "started_at": captured_at, "finished_at": utc_now(),
                    "writes": plan["writes"], "concerns": [],
                }
                manifest_path = bus / "migrations" / migration_id / "manifest.json"
                _atomic_replace(manifest_path, _bytes(manifest))
                return {"status": "COMMITTED", "migration_id": migration_id,
                        "manifest": manifest_path.relative_to(bus).as_posix(), "plan_hash": supplied_hash}
            except Exception as exc:
                for runtime in created_runtime:
                    shutil.rmtree(runtime, ignore_errors=True)
                migrations = bus / "migrations"
                if migrations.exists():
                    shutil.rmtree(migrations, ignore_errors=True)
                if _protected_inventory(bus) != protected_before:
                    raise MigrationError("NEEDS_MANUAL_RECOVERY", 5) from exc
                raise MigrationError("ROLLED_BACK", 4) from exc
            finally:
                shutil.rmtree(staging, ignore_errors=True)
    finally:
        lock.unlink(missing_ok=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--plan-hash")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        root = Path(args.project_root).expanduser().resolve()
        if not root.is_dir():
            raise MigrationError("PROJECT_ROOT_INVALID")
        if args.apply:
            if not args.plan_hash:
                raise MigrationError("PLAN_HASH_REQUIRED")
            result = apply_plan(root, args.plan_hash)
        else:
            result = make_plan(root)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except MigrationError as exc:
        print(exc.code, file=sys.stderr)
        return exc.exit_code
    except Exception:
        print("MIGRATION_FAILED", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
