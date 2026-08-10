#!/usr/bin/env python3
"""Add optimization config to a legacy v3 Run without rewriting history."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from protocol_lib import atomic_write, scalar_map, sha256


def _policy(run_id: str) -> str:
    return "\n".join(
        (
            "protocol_version: 3",
            'kind: "retry_policy"',
            f'run_id: "{run_id}"',
            "ack_timeout_seconds: 600",
            "progress_timeout_seconds: 900",
            "result_timeout_seconds: 600",
            "max_attempts_light: 2",
            "max_attempts_standard: 2",
            "max_attempts_strict: 1",
            'owner_noop_action: "blocked_then_reassign"',
            "auto_retry_light: true",
            "auto_retry_standard: false",
            "auto_retry_strict: false",
            "immutable_events: true",
            "",
        )
    )


def migrate(run_dir_value: str | Path, *, apply: bool = False, rollback: bool = False) -> dict[str, object]:
    run_dir = Path(run_dir_value).expanduser().resolve()
    manifest_path = run_dir / "manifest.yaml"
    manifest = scalar_map(manifest_path.read_text(encoding="utf-8"), source=str(manifest_path))
    fields = {
        "execution_profile": '"normal"',
        "dispatch_policy": '"central"',
        "preflight_scope": '"run"',
        "executor_policy": '"fixed"',
        "executor_scale_authorized": "false",
        "max_instances_per_role": "{}",
        "incident_ref": "null",
        "preflight_required": "false",
        "scope_freeze_ref": "null",
        "scope_freeze_ref_sha256": "null",
        "self_service_parent_scope": '"task_owner_or_declared_collaborator"',
    }
    config_dir = run_dir / "config"
    retry_path = config_dir / "retry-policy.yaml"
    backup_path = config_dir / "manifest.before-optimization.yaml"
    fields["retry_policy_ref"] = f'"{retry_path}"'
    fields["retry_policy_ref_sha256"] = f'"{sha256(retry_path)}"' if retry_path.is_file() else '"null"'
    missing = [field for field in fields if field not in manifest]
    claim_files = [path for path in (run_dir / "claims").glob("**/*") if path.is_file()] if (run_dir / "claims").exists() else []
    if rollback:
        if claim_files:
            raise ValueError("cannot rollback optimization config after claim evidence exists")
        if not backup_path.is_file():
            raise ValueError("optimization migration backup is missing; rollback is unavailable")
        original = backup_path.read_text(encoding="utf-8")
        atomic_write(manifest_path, original)
        rollback_digest = sha256(retry_path)[:12] if retry_path.is_file() else "missing"
        rollback_path = config_dir / f"retry-policy.rollback-{rollback_digest}.yaml"
        if retry_path.is_file() and not rollback_path.exists():
            os.replace(retry_path, rollback_path)
        return {
            "run_id": manifest.get("run_id"),
            "rollback": True,
            "ready": True,
            "changed": ["manifest", "retry_policy"],
            "backup_path": str(backup_path),
            "rollback_policy_path": str(rollback_path) if rollback_path.exists() else None,
        }
    if apply and missing:
        config_dir.mkdir(parents=True, exist_ok=True)
        if not backup_path.exists():
            atomic_write(backup_path, manifest_path.read_text(encoding="utf-8"))
        if not retry_path.exists():
            atomic_write(retry_path, _policy(manifest["run_id"]))
        fields["retry_policy_ref_sha256"] = f'"{sha256(retry_path)}"'
        content = manifest_path.read_text(encoding="utf-8").rstrip() + "\n"
        for field in missing:
            content += f"{field}: {fields[field]}\n"
        atomic_write(manifest_path, content)
    return {
        "run_id": manifest.get("run_id"),
        "ready": True,
        "apply": apply,
        "changed": missing if apply else [],
        "would_add": missing,
        "dispatch_policy": manifest.get("dispatch_policy", "central"),
        "execution_profile": manifest.get("execution_profile", "normal"),
        "backup_path": str(backup_path) if backup_path.is_file() else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.apply and args.rollback:
        raise SystemExit("choose --apply or --rollback")
    result = migrate(args.run_dir, apply=args.apply and not args.dry_run, rollback=args.rollback)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
