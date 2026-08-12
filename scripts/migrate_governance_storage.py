#!/usr/bin/env python3
"""Safely copy a legacy project-local governance store to Governance Home."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

from governance_paths import STORAGE_SCHEMA, resolve_governance_project
from protocol_lib import ProtocolError, atomic_write, now_iso


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inventory(source: Path) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    for path in sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix()):
        relative = path.relative_to(source).as_posix()
        if path.is_symlink():
            raise ProtocolError(f"symlink is not allowed in legacy governance storage: {relative}")
        if path.is_dir():
            continue
        if not path.is_file():
            raise ProtocolError(f"special filesystem entry is not allowed: {relative}")
        files.append({"path": relative, "size": path.stat().st_size, "sha256": _sha256(path)})
    return files


def _inventory_hash(files: list[dict[str, Any]]) -> str:
    encoded = json.dumps(files, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def build_plan(args: argparse.Namespace) -> tuple[dict[str, Any], Any]:
    project = Path(args.project_root).expanduser().resolve()
    source = project / ".multi-agent-collaboration"
    if not source.is_dir():
        raise ProtocolError(f"legacy governance source does not exist: {source}")
    paths = resolve_governance_project(
        project, args.project_id, args.governance_root, require_existing=False,
    )
    files = _inventory(source)
    inventory_hash = _inventory_hash(files)
    static = {
        "storage_schema": STORAGE_SCHEMA,
        "source": str(source),
        "target": str(paths.project_dir),
        "project_id": args.project_id,
        "project_name": args.project_name,
        "source_inventory_sha256": inventory_hash,
        "files": files,
    }
    plan_hash = hashlib.sha256(
        json.dumps(static, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {**static, "plan_sha256": plan_hash, "conflicts": ["target_exists"] if paths.project_dir.exists() else []}, paths


def _write_binding(target: Path, plan: dict[str, Any], project_root: Path, project_key: str) -> None:
    value = {
        "storage_schema": STORAGE_SCHEMA,
        "project_id": plan["project_id"],
        "project_name": plan["project_name"],
        "project_root": str(project_root),
        "project_key": project_key,
        "allowed_roots": [str(project_root)],
        "created_at": now_iso(),
    }
    atomic_write(target / "project-binding.yaml", json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def apply_plan(plan: dict[str, Any], paths: Any) -> dict[str, Any]:
    source = Path(plan["source"])
    target = Path(plan["target"])
    if target.exists():
        raise ProtocolError(f"target already exists; refusing partial or destructive migration: {target}")
    paths.governance_root.mkdir(parents=True, exist_ok=True)
    staging_root = Path(tempfile.mkdtemp(prefix=".governance-migration-", dir=paths.governance_root))
    staged_target = staging_root / "project"
    try:
        shutil.copytree(source, staged_target, symlinks=False)
        if os.environ.get("GOVERNANCE_MIGRATION_FAIL_AFTER") == "copy":
            raise OSError("injected migration failure after copy")
        copied = _inventory(staged_target)
        if copied != plan["files"] or _inventory_hash(copied) != plan["source_inventory_sha256"]:
            raise ProtocolError("copied governance inventory does not match source")
        if os.environ.get("GOVERNANCE_MIGRATION_FAIL_AFTER") == "verify":
            raise OSError("injected migration failure after verify")
        _write_binding(staged_target, plan, paths.project_root, paths.project_key)
        migration_id = "MIG-GOV-" + plan["plan_sha256"][:16]
        manifest_path = staged_target / "migrations/governance-storage" / f"{migration_id}.json"
        manifest = {
            "schema_version": "1.0",
            "doc_type": "governance_storage_migration",
            "migration_id": migration_id,
            "status": "committed",
            "source": plan["source"],
            "target": plan["target"],
            "project_id": plan["project_id"],
            "created_at": now_iso(),
            "plan_sha256": plan["plan_sha256"],
            "source_inventory_sha256": plan["source_inventory_sha256"],
            "files": plan["files"],
            "source_preserved": True,
        }
        atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            raise ProtocolError(f"target appeared during migration: {target}")
        os.replace(staged_target, target)
        return {
            "status": "committed",
            "target": str(target),
            "manifest": str(target / manifest_path.relative_to(staged_target)),
            "source_inventory_sha256": plan["source_inventory_sha256"],
            "source_preserved": True,
        }
    except Exception as exc:
        if target.exists():
            raise ProtocolError(f"migration publish needs manual inspection: {target}") from exc
        raise ProtocolError(f"migration rolled back: {exc}") from exc
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--governance-root")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        plan, paths = build_plan(args)
        if args.dry_run:
            print(json.dumps({**plan, "dry_run": True}, ensure_ascii=False, indent=2, sort_keys=True))
            return 0
        print(json.dumps(apply_plan(plan, paths), ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (ProtocolError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
