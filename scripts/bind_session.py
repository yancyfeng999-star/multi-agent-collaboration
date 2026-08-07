#!/usr/bin/env python3
"""Bind a platform session to a persistent project Agent and Runtime Profile."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from project_memory_lib import (
    agent_root,
    ensure_no_secret_fields,
    exclusive_lock,
    project_root,
    read_json,
    write_json,
)
from protocol_lib import ProtocolError, now_iso
from record_agent_runtime import RuntimeRecordError, record_agent_runtime


_RUNTIME_ENV = {
    "hermes": {"model": "HERMES_MODEL", "provider": "HERMES_PROVIDER"},
    "codex": {"model": "CODEX_MODEL", "provider": "CODEX_PROVIDER"},
    "claude-code": {"model": "CLAUDE_MODEL", "provider": "CLAUDE_PROVIDER"},
}
_GENERIC_ENV = {"model": "AGENT_MODEL", "provider": "AGENT_PROVIDER"}
_RUNTIME_KIND = {
    "hermes": "hermes-thread",
    "codex": "codex-thread",
    "claude-code": "claude-code-thread",
    "other": "other-session",
}


class RuntimeMetadataRequired(ProtocolError):
    def __init__(self, fields: list[str], reason: str) -> None:
        self.requirement = {
            "code": "RUNTIME_METADATA_REQUIRED",
            "status": "explicit_input_required",
            "reason": reason,
            "fields": fields,
            "required_options": [f"--{field}" for field in fields],
        }
        super().__init__(self.requirement["code"])


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="绑定平台会话到 Agent")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--agent-id", required=True, help="Agent ID")
    parser.add_argument("--platform", required=True, choices=["hermes", "claude-code", "codex", "other"])
    parser.add_argument("--session-id", required=True, help="非敏感平台会话 ID")
    parser.add_argument("--profile", default="default", help="平台 profile")
    parser.add_argument("--workspace", help="工作空间路径（默认为项目根目录，且必须位于其中）")
    parser.add_argument("--model", help="实际运行模型；自动探测无法确定时必须显式提供")
    parser.add_argument("--provider", help="实际运行 provider；自动探测无法确定时必须显式提供")
    return parser.parse_args()


def _hermes_config_runtime() -> dict[str, str]:
    """Read only non-secret model identity keys from the local Hermes config."""
    path = Path.home() / ".hermes" / "config.yaml"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    result: dict[str, str] = {}
    in_model = False
    for line in lines:
        if line and not line[0].isspace():
            in_model = line.strip() == "model:"
            continue
        if not in_model:
            continue
        stripped = line.strip()
        for key, field in (("default:", "model"), ("provider:", "provider")):
            if stripped.startswith(key):
                value = stripped[len(key):].strip().strip("'\"")
                if value:
                    result[field] = value
    return result


def _runtime_identity(args: argparse.Namespace) -> dict[str, str]:
    """Resolve model/provider from explicit values and approved probes."""
    resolved: dict[str, str] = {}
    unknown: list[str] = []
    conflicted: list[str] = []
    platform_keys = _RUNTIME_ENV.get(args.platform, {})
    for field in ("model", "provider"):
        explicit = getattr(args, field)
        observed = [
            value for key in (platform_keys.get(field), _GENERIC_ENV[field])
            if key and (value := os.environ.get(key)) is not None
        ]
        evidence = ([explicit] if explicit is not None else []) + observed
        distinct: list[str] = []
        for value in evidence:
            normalized = value.lower().replace(":", "-") if field == "provider" else value
            if normalized not in distinct:
                distinct.append(normalized)
        if len(distinct) > 1:
            conflicted.append(field)
        elif distinct:
            resolved[field] = distinct[0]
        else:
            unknown.append(field)
    if conflicted:
        raise RuntimeMetadataRequired(conflicted, "conflict")
    if unknown:
        raise RuntimeMetadataRequired(unknown, "unknown")
    return resolved


def _existing_session_ids(session_map: dict[str, Any]) -> list[Any]:
    existing: list[Any] = []
    active = session_map.get("active")
    if isinstance(active, dict):
        existing.append(active.get("session_id"))
    existing.extend(
        item.get("session_id") for item in session_map.get("history", [])
        if isinstance(item, dict)
    )
    return existing


def main() -> int:
    args = parse_args()
    try:
        root = project_root(args.project_root)
        agent = agent_root(root, args.agent_id)
        session_map_path = agent / "conversations" / "SESSION_MAP.json"
        workspace = Path(args.workspace).expanduser().resolve() if args.workspace else root
        try:
            workspace.relative_to(root)
        except ValueError as exc:
            raise ProtocolError("workspace 必须位于项目根目录内") from exc
        if not workspace.is_dir():
            raise ProtocolError("workspace 必须是项目根目录内的目录")

        with exclusive_lock(session_map_path.with_suffix(".lock")):
            session_map = read_json(session_map_path)
            if args.session_id in _existing_session_ids(session_map):
                raise ProtocolError(f"session ID 已存在于 Agent 映射中: {args.session_id}")

            identity = _runtime_identity(args)
            now = now_iso()

            def publish_session_map(pointer: dict[str, Any]) -> None:
                active = session_map.get("active")
                if isinstance(active, dict):
                    old_active = dict(active)
                    old_active["ended_at"] = now
                    old_active["superseded_by"] = args.session_id
                    session_map.setdefault("history", []).append(old_active)

                session_map["schema_version"] = "1.1"
                session_map["active"] = {
                    "platform": args.platform,
                    "session_id": args.session_id,
                    "profile": args.profile,
                    "workspace": str(workspace),
                    "started_at": now,
                    "last_synced_message_id": 0,
                    "last_synced_at": now,
                    "runtime_profile_id": pointer["runtime_profile_id"],
                    "runtime_profile_sha256": pointer["record_hash"],
                }
                ensure_no_secret_fields(session_map)
                write_json(session_map_path, session_map)

            pointer = record_agent_runtime(
                project_root=root,
                agent_id=args.agent_id,
                observed={
                    **identity,
                    "platform": args.platform,
                    "session": args.session_id,
                    "profile": args.profile,
                    "workspace": str(workspace),
                    "runtime_kind": _RUNTIME_KIND[args.platform],
                },
                environ=os.environ,
                commit_callback=publish_session_map,
            )

        print("✓ 会话绑定成功")
        print(f"  Agent ID: {args.agent_id}")
        print(f"  平台: {args.platform}")
        print(f"  会话 ID: {args.session_id}")
        print(f"  Profile: {args.profile}")
        print(f"  Runtime Profile: {pointer['runtime_profile_id']}")
        print(f"  工作空间: {workspace}")
        print(f"  绑定时间: {now}")
        return 0
    except RuntimeMetadataRequired as exc:
        print(json.dumps(exc.requirement, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        return 2
    except RuntimeRecordError as exc:
        code = exc.args[0] if exc.args else "RUNTIME_RECORD_FAILED"
        print(json.dumps({"code": str(code), "status": "binding_rejected"}, sort_keys=True), file=sys.stderr)
        return 1
    except (ProtocolError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
