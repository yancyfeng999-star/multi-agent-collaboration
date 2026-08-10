#!/usr/bin/env python3
"""Transactional migration for persistent project-agent storage."""
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

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from protocol_lib import ProtocolError, atomic_write
from project_memory_lib import bus_root, exclusive_lock, project_root

CURRENT_SCHEMA = "1.1"
PROTECTED_NAMES = {"archive", "checkpoints", "checkpoint"}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(bus: Path) -> dict[str, str]:
    return {p.relative_to(bus).as_posix(): digest(p) for p in sorted(bus.rglob("*")) if p.is_file()}


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--governance-root")
    parser.add_argument("--project-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--target-version", default=CURRENT_SCHEMA, choices=[CURRENT_SCHEMA])
    return parser.parse_args()


def migration_plan(bus: Path, target: str) -> list[str]:
    storage = bus / "STORAGE.json"
    if storage.exists():
        try:
            current = json.loads(storage.read_text(encoding="utf-8")).get("schema_version")
        except (OSError, json.JSONDecodeError) as exc:
            raise ProtocolError(f"invalid STORAGE.json: {exc}") from exc
        if current == target:
            return []
        raise ProtocolError(f"unsupported storage schema migration: {current} -> {target}")
    return ["create STORAGE.json", "normalize lifecycle metadata in TEAM.yaml"]


def migrated_team(team_path: Path) -> str:
    try:
        team = json.loads(team_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"invalid TEAM.yaml: {exc}") from exc
    for record in team.get("agents", []):
        record.setdefault("role_history", [])
        record.setdefault("status_history", [])
        record.setdefault("status", "active")
        # The existing agent_id is adopted verbatim. Migration never derives or renames it.
    team["updated_at"] = now()
    return json.dumps(team, ensure_ascii=False, indent=2) + "\n"


def verify_protected(before: dict[str, str], after: dict[str, str]) -> None:
    for relative, checksum in before.items():
        parts = set(Path(relative).parts)
        if parts & PROTECTED_NAMES and after.get(relative) != checksum:
            raise ProtocolError(f"migration changed or deleted protected history: {relative}")


def execute(args: argparse.Namespace) -> int:
    root = project_root(args.project_root)
    bus = bus_root(root, governance_root=args.governance_root, project_id=args.project_id)
    team_path = bus / "TEAM.yaml"
    try:
        if not team_path.is_file():
            raise ProtocolError(f"TEAM.yaml not found: {team_path}")
        plan = migration_plan(bus, args.target_version)
        if not plan:
            print(f"already at storage schema {args.target_version}; no changes")
            return 0
        if args.dry_run:
            print(json.dumps({"dry-run": True, "from": "legacy", "to": args.target_version, "operations": plan}, ensure_ascii=False, indent=2))
            return 0

        before = inventory(bus)
        backup_root = Path(tempfile.mkdtemp(prefix="agent-migration-", dir=bus.parent))
        backup_bus = backup_root / "store"
        shutil.copytree(bus, backup_bus)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_dir = bus / "migrations" / f"{stamp}-to-{args.target_version}"
        manifest_path = backup_dir / "manifest.json"
        try:
            team_content = migrated_team(team_path)
            fail_after = int(os.environ.get("AGENT_MIGRATION_FAIL_AFTER", "0"))
            atomic_write(team_path, team_content)
            if fail_after == 1:
                raise RuntimeError("injected migration failure")
            storage = {"schema_version": args.target_version, "migrated_at": now(), "source_schema_version": "legacy"}
            atomic_write(bus / "STORAGE.json", json.dumps(storage, ensure_ascii=False, indent=2) + "\n")
            if fail_after == 2:
                raise RuntimeError("injected migration failure")
            after = inventory(bus)
            verify_protected(before, after)
            backup_dir.mkdir(parents=True, exist_ok=False)
            shutil.copytree(backup_bus, backup_dir / "backup")
            manifest = {"schema_version": "1.0", "migration": {"from": "legacy", "to": args.target_version},
                        "created_at": now(), "before": before, "after": after,
                        "backup": "backup", "protected_history_verified": True}
            atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        except Exception as exc:
            # Roll back only files this migration may write. Never remove the store,
            # agent directories, archive, checkpoints, or unrelated concurrent history.
            original_team = backup_bus / "TEAM.yaml"
            atomic_write(team_path, original_team.read_text(encoding="utf-8"))
            storage_path = bus / "STORAGE.json"
            if storage_path.exists() and not (backup_bus / "STORAGE.json").exists():
                storage_path.unlink()
            elif (backup_bus / "STORAGE.json").exists():
                atomic_write(storage_path, (backup_bus / "STORAGE.json").read_text(encoding="utf-8"))
            if backup_dir.exists():
                shutil.rmtree(backup_dir)
            raise ProtocolError(f"migration failed and rolled back: {exc}") from exc
        finally:
            shutil.rmtree(backup_root, ignore_errors=True)
        print(manifest_path)
        return 0
    except (ProtocolError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    args = parse_args()
    root = project_root(args.project_root)
    bus = bus_root(root, governance_root=args.governance_root, project_id=args.project_id)
    with exclusive_lock(bus / ".init.lock"):
        return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
