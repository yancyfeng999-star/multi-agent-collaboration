#!/usr/bin/env python3
"""Freeze a Run's requested paths and delivery boundary exactly once."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

from project_memory_lib import exclusive_lock
from protocol_lib import atomic_write, json_string_list, path_within, paths_overlap, quote, scalar_map, sha256, replace_flat_scalar


def _project(run_dir: Path) -> tuple[Path, list[str]]:
    path = run_dir.parent.parent / "project.yaml"
    values = scalar_map(path.read_text(encoding="utf-8"), source=str(path))
    return Path(values["project_root"]).expanduser().resolve(), json_string_list(
        values.get("allowed_roots", "[]"), field="allowed_roots", source=str(path)
    )


def _normalize(value: str, root: Path) -> str:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return str(path.resolve())


def _dirty(root: Path) -> list[str]:
    result = subprocess.run(["git", "-C", str(root), "status", "--porcelain"], capture_output=True, text=True)
    if result.returncode:
        return []
    return sorted(line[3:] if len(line) > 3 else line for line in result.stdout.splitlines())


def freeze_scope(
    run_dir_value: str | Path,
    requested_paths: list[str],
    forbidden_paths: list[str],
    target_environment: str,
    *,
    dry_run: bool = False,
) -> dict[str, object]:
    run_dir = Path(run_dir_value).expanduser().resolve()
    manifest_path = run_dir / "manifest.yaml"
    manifest = scalar_map(manifest_path.read_text(encoding="utf-8"), source=str(manifest_path))
    root, allowed = _project(run_dir)
    path_values = [_normalize(path, root) for path in requested_paths] or [str(root)]
    forbidden_values = [_normalize(path, root) for path in forbidden_paths]
    for path in path_values + forbidden_values:
        if not path_within(path, allowed, root):
            raise ValueError(f"scope path exceeds project allowed_roots: {path}")
    for index, left in enumerate(path_values):
        for right in path_values[index + 1 :]:
            if paths_overlap(left, right, root):
                raise ValueError(f"requested scope paths overlap: {left} and {right}")
    for requested in path_values:
        for forbidden in forbidden_values:
            if paths_overlap(requested, forbidden, root):
                raise ValueError(f"requested and forbidden scope paths overlap: {requested} and {forbidden}")
    scope_path = run_dir / "decisions" / "scope-freeze.yaml"
    if scope_path.exists():
        raise ValueError(f"scope freeze already exists and is immutable: {scope_path}")
    objective_hash = hashlib.sha256(manifest.get("objective", "").encode("utf-8")).hexdigest()
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    scope_id = "SCOPE-" + hashlib.sha256(
        f"{manifest.get('run_id')}\0{objective_hash}\0{'|'.join(path_values)}".encode("utf-8")
    ).hexdigest()[:16]
    content = "\n".join(
        (
            "protocol_version: 3",
            'kind: "scope_freeze"',
            f"run_id: {quote(manifest['run_id'])}",
            f"scope_id: {quote(scope_id)}",
            f"objective_sha256: {quote(objective_hash)}",
            f"requested_paths: {json.dumps(sorted(path_values), ensure_ascii=False)}",
            f"forbidden_paths: {json.dumps(sorted(forbidden_values), ensure_ascii=False)}",
            f"excluded_dirty_files: {json.dumps(_dirty(root), ensure_ascii=False)}",
            f"target_environment: {quote(target_environment)}",
            f"max_parallel: {manifest.get('max_parallel', '1')}",
            f"governance: {quote(manifest.get('governance', 'light'))}",
            f"execution_profile: {quote(manifest.get('execution_profile', 'normal'))}",
            f"dispatch_policy: {quote(manifest.get('dispatch_policy', 'central'))}",
            f"version_contract_sha256: {quote(manifest.get('version_contract_ref_sha256', 'null'))}",
            'owner_agent: "coordinator"',
            f"created_at: {quote(created_at)}",
            "",
        )
    )
    if not dry_run:
        with exclusive_lock(run_dir / "locks" / ".scope-freeze.lock"):
            if scope_path.exists():
                raise ValueError(f"scope freeze already exists and is immutable: {scope_path}")
            atomic_write(scope_path, content)
            replace_flat_scalar(manifest_path, "scope_freeze_ref", quote(str(scope_path)))
            replace_flat_scalar(manifest_path, "scope_freeze_ref_sha256", quote(sha256(scope_path)))
    return {
        "scope_id": scope_id,
        "run_id": manifest.get("run_id"),
        "scope_path": str(scope_path),
        "scope_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--requested-path", action="append", default=[])
    parser.add_argument("--forbidden-path", action="append", default=[])
    parser.add_argument("--target-environment", default="local")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(freeze_scope(args.run_dir, args.requested_path, args.forbidden_path, args.target_environment, dry_run=args.dry_run), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
