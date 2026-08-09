#!/usr/bin/env python3
"""Manage stable, long-lived project agent identities and lifecycle state."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))
from protocol_lib import ProtocolError, atomic_write
from init_project_agents import catalog_for
from project_memory_lib import exclusive_lock

AGENT_ID_RE = re.compile(r"^A\d{2}-[a-z0-9][a-z0-9-]*$")
STATUSES = {"active", "paused", "retired"}
REQUIRED_DIRS = (
    "conversations", "conversations/archive", "conversations/checkpoints",
    "tasks", "handoffs", "artifacts", "runtime", "runtime/profiles", "activity",
)
RUNTIME_MANAGED = {"profiles", "CURRENT_RUNTIME.json", "RUNTIME_INDEX.jsonl", ".runtime.lock"}
ACTIVITY_RECORD_RE = re.compile(r"^ACTIVITY-(\d{6})\.json$")
RUNTIME_PROFILE_RE = re.compile(r"^RP-(\d{6})\.json$")


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_store(project_root: str) -> tuple[Path, Path, dict]:
    root = Path(project_root).expanduser().resolve()
    bus = root / ".multi-agent-collaboration"
    team_path = bus / "TEAM.yaml"
    if not team_path.is_file():
        raise ProtocolError(f"TEAM.yaml not found: {team_path}")
    try:
        team = json.loads(team_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"invalid TEAM.yaml: {exc}") from exc
    return bus, team_path, team


def save_team(path: Path, team: dict) -> None:
    team["updated_at"] = now()
    atomic_write(path, json.dumps(team, ensure_ascii=False, indent=2) + "\n")


def find_agent(team: dict, agent_id: str) -> dict:
    for record in team.get("agents", []):
        if record.get("agent_id") == agent_id:
            return record
    raise ProtocolError(f"agent does not exist: {agent_id}")


def sync_profile(bus: Path, record: dict, *, role_changed: bool = False) -> None:
    """Keep stable profile identity, catalog projection, and lifecycle in sync."""
    relative = record.get("agent_profile_file") or f"agents/{record['agent_id']}/AGENT_PROFILE.json"
    path = bus / relative
    if not path.is_file():
        return
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"invalid agent profile: {path}: {exc}") from exc
    role_name = record.get("role_name", "member")
    role_relative = record.get("role_file") or f"agents/{record['agent_id']}/ROLE.md"
    role_path = bus / role_relative
    if not role_path.is_file():
        raise ProtocolError(f"role file not found: {role_path}")
    profile["role"] = {
        "role_id": role_name,
        "path": role_relative,
        "sha256": hashlib.sha256(role_path.read_bytes()).hexdigest(),
    }
    profile["profile_version"] = int(profile.get("profile_version", 1)) + 1
    profile.setdefault("metadata", {})["display_name"] = role_name.replace("-", " ").title()
    profile.setdefault("metadata", {}).setdefault("labels", [role_name])
    if role_changed or "catalog" not in profile:
        profile["catalog"] = catalog_for(role_name)
    lifecycle = profile.setdefault("lifecycle", {})
    status = record.get("status", "active")
    history = record.get("status_history", [])
    transition = history[-1] if history else {}
    lifecycle.update({
        "status": status,
        "updated_at": now(),
        "paused_at": transition.get("at") if status == "paused" else None,
        "retired_at": transition.get("at") if status == "retired" else None,
        "retirement_reason": (transition.get("reason") or "retired") if status == "retired" else None,
    })
    atomic_write(path, json.dumps(profile, ensure_ascii=False, indent=2) + "\n")


def validate_id(agent_id: str) -> None:
    if not AGENT_ID_RE.fullmatch(agent_id):
        raise ProtocolError(f"invalid agent id: {agent_id}")


def role_document(agent_id: str, role: str, domain: str | None, created: str) -> str:
    stamp = now()
    return f'''---
schema_version: "1.0"
doc_type: role
agent_id: "{agent_id}"
created_at: "{created}"
updated_at: "{stamp}"
---

# Agent 岗位章程

## 基本信息

| 字段 | 值 |
|------|-----|
| Agent ID | {agent_id} |
| 岗位名称 | {role} |
| 专业领域 | {domain or "待定义"} |

## 长期使命

待定义
'''


def prompt_document(agent_id: str, role: str, root: str, created: str) -> str:
    return f'''---
schema_version: "1.0"
doc_type: system_prompt
agent_id: "{agent_id}"
created_at: "{created}"
updated_at: "{now()}"
---

# Agent 恢复提示词

项目根目录：`{root}`

你是 **{agent_id}**，当前岗位为 **{role}**。Agent ID 是不可变身份；岗位调整不得改变 ID，历史保留在 TEAM.yaml 的 role_history。
'''


def checklist_document(agent_id: str, created: str) -> str:
    return f'''---
schema_version: "1.0"
doc_type: checklist
agent_id: "{agent_id}"
created_at: "{created}"
---

# Agent 检查清单

- [ ] 检查身份、状态、任务和实际文件。
- [ ] 保留 archive、checkpoint 与岗位历史。
'''


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _repair_contents(bus: Path, record: dict, project_root: str) -> dict[str, bytes]:
    agent_id = record["agent_id"]
    created = record.get("created_at") or now()
    role_name = record.get("role_name", "member")
    role_content = role_document(agent_id, role_name, record.get("domain"), created).encode()
    role_path = bus / "agents" / agent_id / "ROLE.md"
    role_hash = hashlib.sha256(role_path.read_bytes()).hexdigest() if role_path.is_file() else hashlib.sha256(role_content).hexdigest()
    status = record.get("status", "active")
    history = record.get("status_history", [])
    last_transition = history[-1] if history else {}
    lifecycle = {
        "status": status,
        "created_at": created,
        "updated_at": now(),
        "paused_at": last_transition.get("at") if status == "paused" else None,
        "retired_at": last_transition.get("at") if status == "retired" else None,
        "retirement_reason": (last_transition.get("reason") or "retired") if status == "retired" else None,
    }
    profile = {
        "schema_version": "1.0",
        "doc_type": "agent_profile",
        "profile_version": 1,
        "agent_id": agent_id,
        "role": {
            "role_id": role_name,
            "path": f"agents/{agent_id}/ROLE.md",
            "sha256": role_hash,
        },
        "declared_model_policy": {
            "policy_kind": "declared_default",
            "preferred_models": [],
            "preferred_provider": None,
            "runtime_kind": None,
            "source": "team_registry",
        },
        "lifecycle": lifecycle,
        "metadata": {"display_name": role_name.replace("-", " ").title(), "labels": [role_name]},
        "catalog": catalog_for(role_name),
    }
    return {
        "ROLE.md": role_content,
        "AGENT_PROFILE.json": _json_bytes(profile),
        "SYSTEM_PROMPT.md": prompt_document(agent_id, role_name, project_root, created).encode(),
        "CHECKLIST.md": checklist_document(agent_id, created).encode(),
        "conversations/SESSION_MAP.json": _json_bytes({"schema_version": "1.0", "agent_id": agent_id, "active": None, "history": []}),
        "conversations/CURRENT_CONTEXT.md": f"---\nschema_version: \"1.0\"\ndoc_type: current_agent_context\nagent_id: \"{agent_id}\"\nupdated_at: \"{now()}\"\n---\n\n# 当前 Agent 上下文\n".encode(),
        "conversations/README.md": b"",
        "conversations/INDEX.md": b"",
    }


def _runtime_rebuild(agent: Path) -> dict[str, bytes]:
    profiles_dir = agent / "runtime" / "profiles"
    profiles: list[tuple[int, Path, dict]] = []
    if profiles_dir.is_dir():
        for path in profiles_dir.iterdir():
            match = RUNTIME_PROFILE_RE.fullmatch(path.name) if path.is_file() else None
            if not match:
                continue
            try:
                profile = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            profiles.append((int(match.group(1)), path, profile))
    profiles.sort()
    if not profiles:
        return {}
    index = []
    for number, path, profile in profiles:
        profile_id = f"RP-{number:06d}"
        record_hash = profile.get("record_hash")
        digest = record_hash.get("value") if isinstance(record_hash, dict) else hashlib.sha256(path.read_bytes()).hexdigest()
        index.append({"runtime_profile_id": profile_id, "agent_id": profile.get("agent_id"),
                      "captured_at": profile.get("captured_at"),
                      "capture_status": (profile.get("capture_status") or {}).get("name") if isinstance(profile.get("capture_status"), dict) else None,
                      "record_hash": digest, "path": f"profiles/{profile_id}.json"})
    last = index[-1]
    return {
        "runtime/RUNTIME_INDEX.jsonl": b"".join((json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n").encode() for item in index),
        "runtime/CURRENT_RUNTIME.json": _json_bytes({"runtime_profile_id": last["runtime_profile_id"], "record_hash": last["record_hash"], "path": last["path"]}),
    }


def _activity_rebuild(agent: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    activity = agent / "activity"
    if not activity.is_dir():
        return result
    ledgers = {path.parents[3] for path in activity.rglob("ACTIVITY-*.json") if len(path.relative_to(activity).parts) >= 4}
    for ledger in sorted(ledgers):
        records: list[tuple[int, Path, dict]] = []
        for path in ledger.rglob("ACTIVITY-*.json"):
            match = ACTIVITY_RECORD_RE.fullmatch(path.name)
            if not match:
                continue
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                record = {}
            records.append((int(match.group(1)), path, record))
        records.sort()
        if not records:
            continue
        index = []
        for number, path, record in records:
            index.append({"activity_id": f"ACTIVITY-{number:06d}", "sequence": number,
                          "record_sha256": record.get("record_sha256") or hashlib.sha256(path.read_bytes()).hexdigest(),
                          "path": path.relative_to(ledger).as_posix(), "recorded_at": record.get("recorded_at"),
                          "run_id": record.get("run_id"), "task_id": record.get("task_id"),
                          "attempt_id": record.get("attempt_id"), "agent_id": record.get("agent_id"),
                          "session_id": record.get("session_id"), "previous_record_sha256": record.get("previous_record_sha256")})
        relative = ledger.relative_to(agent).as_posix()
        result[f"{relative}/INDEX.jsonl"] = b"".join((json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n").encode() for item in index)
        last = index[-1]
        result[f"{relative}/CURRENT.json"] = _json_bytes({key: last[key] for key in ("activity_id", "sequence", "record_sha256", "path")})
    return result


def build_repair_plan(bus: Path, record: dict, project_root: str) -> tuple[list[dict], dict[str, bytes]]:
    agent = bus / "agents" / record["agent_id"]
    writes = _repair_contents(bus, record, project_root)
    writes.update(_runtime_rebuild(agent))
    writes.update(_activity_rebuild(agent))
    operations: list[dict] = []
    for relative in REQUIRED_DIRS:
        path = agent / relative
        if not path.exists():
            operations.append({"action": "mkdir", "path": relative + "/"})
        elif not path.is_dir():
            raise ProtocolError(f"cannot repair directory over file: {path}")
    for relative in list(writes):
        path = agent / relative
        if path.exists():
            if not path.is_file():
                raise ProtocolError(f"cannot repair file over directory: {path}")
            del writes[relative]
        else:
            operations.append({"action": "write", "path": relative})
    runtime = agent / "runtime"
    if runtime.is_dir():
        for child in sorted(runtime.iterdir()):
            if child.name not in RUNTIME_MANAGED:
                operations.append({"action": "quarantine", "path": child.relative_to(agent).as_posix()})
    operations.sort(key=lambda item: (item["path"], item["action"]))
    return operations, writes


def apply_repair(bus: Path, record: dict, operations: list[dict], writes: dict[str, bytes]) -> str:
    agent = bus / "agents" / record["agent_id"]
    backups = bus / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    backup = backups / f"repair-{record['agent_id']}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')}.zip"
    with zipfile.ZipFile(backup, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in agent.rglob("*"):
            if path.is_file() and not path.name.endswith(".lock"):
                archive.write(path, path.relative_to(agent).as_posix())
    snapshot = {path.relative_to(agent).as_posix(): path.read_bytes() for path in agent.rglob("*") if path.is_file()}
    try:
        fail_after = int(os.environ.get("AGENT_REPAIR_FAIL_AFTER", "0"))
        for count, operation in enumerate(operations, 1):
            relative = operation["path"].rstrip("/")
            path = agent / relative
            if operation["action"] == "mkdir":
                path.mkdir(parents=True, exist_ok=True)
            elif operation["action"] == "write":
                path.parent.mkdir(parents=True, exist_ok=True)
                atomic_write(path, writes[relative].decode("utf-8"))
            else:
                quarantine = agent / ".repair-quarantine" / relative
                quarantine.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(quarantine))
            if fail_after and count >= fail_after:
                raise RuntimeError("injected repair failure")
    except Exception as exc:
        for path in sorted(agent.rglob("*"), reverse=True):
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path != agent:
                path.rmdir()
        for relative, content in snapshot.items():
            path = agent / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        raise ProtocolError(f"repair rolled back: {exc}") from exc
    return backup.relative_to(bus).as_posix()


def repair_files(bus: Path, record: dict, project_root: str) -> list[str]:
    """Compatibility helper used by add: create all missing scaffolding immediately."""
    operations, writes = build_repair_plan(bus, record, project_root)
    apply_repair(bus, record, operations, writes)
    return [item["path"] for item in operations]


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subs = result.add_subparsers(dest="action", required=True)
    for action in ("list", "add", "update", "pause", "resume", "retire", "archive", "repair"):
        sub = subs.add_parser(action)
        sub.add_argument("--project-root", required=True)
        if action != "list":
            sub.add_argument("--agent-id", required=True)
        if action == "add":
            sub.add_argument("--role-name", required=True)
            sub.add_argument("--domain")
        if action == "update":
            sub.add_argument("--role-name")
            sub.add_argument("--domain")
            sub.add_argument("--new-agent-id")
        if action in {"pause", "retire", "archive"}:
            sub.add_argument("--reason", required=True)
        if action == "repair":
            sub.add_argument("--apply", action="store_true")
            sub.add_argument("--plan-hash")
    return result


def execute(args: argparse.Namespace) -> int:
    try:
        bus, team_path, team = load_store(args.project_root)
        if args.action == "list":
            print(json.dumps(team.get("agents", []), ensure_ascii=False, indent=2))
            return 0
        validate_id(args.agent_id)
        if args.action == "add":
            if any(a.get("agent_id") == args.agent_id for a in team.get("agents", [])) or (bus / "agents" / args.agent_id).exists():
                raise ProtocolError(f"agent id already exists and will not be silently renamed: {args.agent_id}")
            stamp = now()
            record = {"agent_id": args.agent_id, "role_name": args.role_name, "domain": args.domain,
                      "status": "active", "created_at": stamp, "status_history": [], "role_history": [],
                      "role_file": f"agents/{args.agent_id}/ROLE.md",
                      "system_prompt_file": f"agents/{args.agent_id}/SYSTEM_PROMPT.md",
                      "agent_profile_file": f"agents/{args.agent_id}/AGENT_PROFILE.json"}
            repair_files(bus, record, str(Path(args.project_root).resolve()))
            team.setdefault("agents", []).append(record)
            save_team(team_path, team)
        else:
            record = find_agent(team, args.agent_id)
            if args.action == "update":
                if args.new_agent_id and args.new_agent_id != args.agent_id:
                    raise ProtocolError("agent_id is immutable; transfers must update role_name, never rename identity")
                if record.get("status") == "retired":
                    raise ProtocolError("retired agent history is immutable")
                if args.role_name is None and args.domain is None:
                    raise ProtocolError("update requires --role-name or --domain")
                if args.role_name is not None and args.role_name != record.get("role_name"):
                    record.setdefault("role_history", []).append({"role_name": record.get("role_name"), "domain": record.get("domain"), "ended_at": now()})
                    record["role_name"] = args.role_name
                    record["role_started_at"] = now()
                if args.domain is not None:
                    record["domain"] = args.domain
                # ROLE is mutable current charter; archived role transition remains immutable in TEAM.
                atomic_write(bus / record["role_file"], role_document(args.agent_id, record.get("role_name", "member"), record.get("domain"), record.get("created_at", now())))
                sync_profile(bus, record, role_changed=args.role_name is not None)
            elif args.action in {"pause", "resume", "retire", "archive"}:
                target = {"pause": "paused", "resume": "active", "retire": "retired", "archive": "retired"}[args.action]
                current = record.get("status", "active")
                if current == "retired" and target != "retired":
                    raise ProtocolError("retired agent cannot resume; add a new stable identity instead")
                if args.action == "resume" and current != "paused":
                    raise ProtocolError("only paused agents can resume")
                if current != target:
                    event = {"from": current, "to": target, "at": now()}
                    if hasattr(args, "reason"):
                        event["reason"] = args.reason
                    record.setdefault("status_history", []).append(event)
                    record["status"] = target
                    sync_profile(bus, record)
            elif args.action == "repair":
                operations, writes = build_repair_plan(bus, record, str(Path(args.project_root).resolve()))
                plan_hash = hashlib.sha256(json.dumps(operations, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                result = {"agent_id": args.agent_id, "dry_run": not args.apply,
                          "plan_hash": plan_hash, "operations": operations}
                if args.apply:
                    if not args.plan_hash or args.plan_hash != plan_hash:
                        raise ProtocolError("repair plan hash does not match current plan")
                    result["backup"] = apply_repair(bus, record, operations, writes)
                print(json.dumps(result, ensure_ascii=False))
            if args.action != "repair":
                save_team(team_path, team)
        if args.action != "repair":
            print(json.dumps(find_agent(team, args.agent_id), ensure_ascii=False))
        return 0
    except ProtocolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    args = parser().parse_args()
    root = Path(args.project_root).expanduser().resolve()
    with exclusive_lock(root / ".multi-agent-collaboration" / ".init.lock"):
        return execute(args)


if __name__ == "__main__":
    raise SystemExit(main())
