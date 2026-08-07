#!/usr/bin/env python3
"""Generate a compact, project-local recovery brief for any supported runtime."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from project_memory_lib import agent_root, bus_root, find_high_confidence_secrets, parse_frontmatter, project_root
from protocol_lib import ProtocolError, atomic_write, now_iso
from runtime_metadata import DetectionRejected, detect_runtime_metadata

ACTIVE_TASK_STATES = {
    "ready", "dispatched", "acknowledged", "running", "blocked", "waiting_external",
    "waiting_user_approval", "handoff_ready", "reviewing", "changes_requested",
    "qa_running", "qa_failed", "qa_passed", "release_ready",
}


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="生成 Agent 最小恢复包")
    value.add_argument("--project-root", required=True)
    value.add_argument("--agent-id", required=True)
    value.add_argument("--output")
    value.add_argument("--safe-output-dir", action="append", default=[])
    value.add_argument("--detect-drift", action="store_true")
    value.add_argument("--fail-on-drift", action="store_true")
    return value


def existing(paths: list[Path]) -> list[Path]:
    return [path for path in paths if path.is_file()]


def created_at(path: Path) -> str:
    try:
        value = parse_frontmatter(path).get("created_at")
    except (OSError, ProtocolError):
        return ""
    return value if isinstance(value, str) else ""


def newest_by_created_at(paths: list[Path]) -> Path | None:
    candidates = [path for path in paths if path.is_file()]
    return max(candidates, key=lambda path: (created_at(path), path.as_posix())) if candidates else None


def confined_path(raw: str, bases: list[Path]) -> Path | None:
    candidate = Path(raw).expanduser()
    choices = [candidate] if candidate.is_absolute() else [base / candidate for base in bases]
    for choice in choices:
        resolved = choice.resolve()
        if any(resolved == base or base in resolved.parents for base in bases):
            return resolved
    return None


def select_checkpoint(agent: Path, current_meta: dict[str, Any]) -> tuple[Path | None, list[str]]:
    missing: list[str] = []
    latest = current_meta.get("latest_checkpoint")
    if isinstance(latest, str) and latest:
        candidate = confined_path(latest, [agent])
        if candidate and candidate.is_file():
            return candidate, missing
        missing.append(latest)
        return None, missing
    directory = agent / "conversations" / "checkpoints"
    return newest_by_created_at(list(directory.glob("*.md"))), missing


def resolve_active_task(root: Path, bus: Path, agent: Path, agent_id: str, current_meta: dict[str, Any]) -> tuple[Path | None, str]:
    active = current_meta.get("active_task")
    if isinstance(active, str) and active:
        for base in (agent, root):
            direct = confined_path(active, [base])
            if direct and direct.is_file():
                return direct, "CURRENT_CONTEXT.active_task"
        matches = []
        for path in [*agent.glob("tasks/*.md"), *bus.glob("runs/*/tasks/*.md")]:
            meta = parse_frontmatter(path)
            if active in {path.name, path.stem, meta.get("task_id")}:
                matches.append(path)
        if matches:
            return newest_by_created_at(matches), "CURRENT_CONTEXT.active_task"

    candidates: list[Path] = []
    for state_path in bus.glob("runs/*/state.yaml"):
        try:
            states = parse_frontmatter(state_path).get("task_states")
            if not states:
                states = _flat_value(state_path, "task_states")
        except (OSError, ProtocolError):
            continue
        if not isinstance(states, dict):
            continue
        for task_id, state in states.items():
            if state not in ACTIVE_TASK_STATES:
                continue
            for task in state_path.parent.glob("tasks/*.md"):
                meta = parse_frontmatter(task)
                if meta.get("task_id") == task_id and meta.get("owner_agent") in {None, agent_id}:
                    candidates.append(task)
    selected = newest_by_created_at(candidates)
    if selected:
        return selected, "run_state_created_at"
    selected = newest_by_created_at(list((agent / "tasks").glob("*.md")))
    return selected, "task_frontmatter_created_at" if selected else "none"


def _flat_value(path: Path, key: str) -> Any:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}:"):
            raw = line.split(":", 1)[1].strip()
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw.strip('"')
    return None


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(value: dict[str, Any], hash_field: str) -> str:
    copy = dict(value)
    copy.pop(hash_field, None)
    return hashlib.sha256(json.dumps(copy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def safe_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or find_high_confidence_secrets(json.dumps(value, ensure_ascii=False, sort_keys=True)):
        return None
    return value


def resolved_value(profile: dict[str, Any], field: str) -> str | None:
    value = profile.get(field)
    if not isinstance(value, dict) or value.get("status") != "known" or not isinstance(value.get("value"), str):
        return None
    return value["value"]


def runtime_summary(root: Path, agent: Path, agent_id: str) -> dict[str, Any]:
    runtime = agent / "runtime"
    pointer = safe_json(runtime / "CURRENT_RUNTIME.json")
    profile: dict[str, Any] | None = None
    verified = False
    if pointer and isinstance(pointer.get("path"), str) and isinstance(pointer.get("record_hash"), str):
        path = confined_path(pointer["path"], [runtime])
        candidate = safe_json(path) if path and path.is_file() else None
        if candidate:
            record = candidate.get("record_hash")
            expected = record.get("value") if isinstance(record, dict) else None
            verified = (
                expected == pointer["record_hash"] == canonical_hash(candidate, "record_hash")
                and candidate.get("runtime_profile_id") == pointer.get("runtime_profile_id")
                and candidate.get("agent_id") == agent_id
            )
            if verified:
                profile = candidate

    stored = {field: resolved_value(profile or {}, field) for field in ("model", "provider", "platform", "session", "profile")}
    try:
        detected = detect_runtime_metadata(
            project_root=root, agent_id=agent_id, environ=os.environ,
            allowed_roots=(root, Path.cwd()), required_fields=("platform", "session_id", "profile"),
        )
    except DetectionRejected:
        detected = {"status": "insufficient", "platform": None, "session_id": None, "profile": None}
    actual = {
        "model": os.environ.get("HERMES_MODEL"), "provider": os.environ.get("HERMES_PROVIDER"),
        "status": detected.get("status"), "platform": detected.get("platform"),
        "session": detected.get("session_id"), "profile": detected.get("profile"),
    }
    for field in ("model", "provider"):
        value = actual[field]
        if not isinstance(value, str) or not value or find_high_confidence_secrets(value):
            actual[field] = None
    comparison: dict[str, str] = {}
    drift_fields: list[str] = []
    for field in stored:
        before, now = stored[field], actual[field]
        if before is None or now is None:
            comparison[field] = "unknown"
        elif before == now:
            comparison[field] = "same"
        else:
            comparison[field] = "changed"
            drift_fields.append(field)
    return {
        "runtime_profile_id": profile.get("runtime_profile_id") if profile else None,
        "capture_status": profile.get("capture_status") if profile else None,
        "profile_verified": verified, "stored": stored, "actual": actual,
        "comparison": comparison, "drift_fields": drift_fields,
    }


def recent_activity(agent: Path) -> dict[str, Any]:
    candidates: list[tuple[str, Path, dict[str, Any]]] = []
    for pointer_path in agent.glob("activity/*/*/*/CURRENT.json"):
        pointer = safe_json(pointer_path)
        if not pointer or not isinstance(pointer.get("path"), str):
            continue
        path = confined_path(pointer["path"], [pointer_path.parent])
        record = safe_json(path) if path and path.is_file() else None
        if record and path is not None:
            candidates.append((str(record.get("recorded_at") or ""), path, record))
    if not candidates:
        return {"activity_id": None, "verified": False, "usage_source": None, "usage_source_verified": False}
    _time, _path, record = max(candidates, key=lambda item: (item[0], item[1].as_posix()))
    expected = record.get("record_sha256")
    verified = isinstance(expected, str) and expected == canonical_hash(record, "record_sha256")
    usage = record.get("usage") if isinstance(record.get("usage"), dict) else {}
    source_verified = False
    source_ref = usage.get("source_ref")
    source_digest = usage.get("source_sha256")
    if verified and isinstance(source_ref, str) and isinstance(source_digest, str):
        source = confined_path(source_ref, [agent])
        source_verified = bool(source and source.is_file() and sha256(source) == source_digest)
    return {
        "activity_id": record.get("activity_id") if verified else None,
        "recorded_at": record.get("recorded_at") if verified else None,
        "record_kind": record.get("record_kind") if verified else None,
        "status": record.get("status") if verified else None,
        "verified": verified,
        "usage_source": usage.get("usage_source") if verified else None,
        "usage_source_verified": source_verified,
    }


def git_state(root: Path) -> dict[str, Any]:
    head = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True)
    status = subprocess.run(["git", "-C", str(root), "status", "--porcelain=v1", "-z"], capture_output=True, text=True)
    if head.returncode or status.returncode:
        return {"available": False, "actual_head": None, "actual_dirty_files": []}
    dirty = []
    records = status.stdout.split("\0")
    index = 0
    while index < len(records):
        record = records[index]
        if not record:
            index += 1
            continue
        dirty.append(record[3:])
        if record[:2] in {"R ", "C ", "RM", "CM"}:
            index += 1
        index += 1
    return {"available": True, "actual_head": head.stdout.strip(), "actual_dirty_files": sorted(dirty)}


def reference_contract(meta: dict[str, Any]) -> tuple[list[str], dict[str, str]]:
    refs: list[str] = []
    hashes: dict[str, str] = {}
    for key in ("referenced_files", "source_archives", "must_read", "must_read_files"):
        value = meta.get(key)
        if isinstance(value, list):
            refs.extend(item for item in value if isinstance(item, str))
    explicit = meta.get("reference_hashes")
    if isinstance(explicit, dict):
        hashes.update({str(path): str(value) for path, value in explicit.items()})
    for key, value in meta.items():
        if not key.endswith("_ref") or not isinstance(value, str) or not value:
            continue
        refs.append(value)
        expected = meta.get(f"{key}_sha256")
        if isinstance(expected, str) and expected:
            hashes[value] = expected
    return list(dict.fromkeys(refs)), hashes


def detect_drift(root: Path, checkpoint: Path | None, initial_missing: list[str]) -> dict[str, Any]:
    meta = parse_frontmatter(checkpoint) if checkpoint else {}
    actual_git = git_state(root)
    expected_head = meta.get("git_head") or meta.get("git_commit")
    expected_dirty = meta.get("dirty_files")
    if not isinstance(expected_dirty, list):
        expected_dirty = None
    git = {
        **actual_git,
        "expected_head": expected_head,
        "head_matches": expected_head is None or expected_head == actual_git["actual_head"],
        "expected_dirty_files": expected_dirty,
        "dirty_files_match": expected_dirty is None or sorted(expected_dirty) == actual_git["actual_dirty_files"],
    }
    expected_root = meta.get("project_root")
    project_path = {
        "expected": expected_root,
        "actual": str(root),
        "matches": expected_root is None or Path(str(expected_root)).expanduser().resolve() == root,
    }
    refs, hashes = reference_contract(meta)
    missing = list(initial_missing)
    mismatches = []
    for raw in refs:
        path = confined_path(raw, [root])
        if path is None or not path.is_file():
            missing.append(raw)
            continue
        expected = hashes.get(raw)
        if expected:
            actual = sha256(path)
            if actual != expected:
                mismatches.append({"path": raw, "expected_sha256": expected, "actual_sha256": actual})
    detected = (
        not git["head_matches"] or not git["dirty_files_match"] or bool(missing)
        or bool(mismatches) or not project_path["matches"]
    )
    return {
        "checked": True,
        "detected": detected,
        "git": git,
        "missing_references": sorted(set(missing)),
        "hash_mismatches": mismatches,
        "project_path": project_path,
    }


def output_path(root: Path, agent: Path, raw: str | None, safe_dirs: list[str]) -> Path:
    output = Path(raw).expanduser().resolve() if raw else agent / "conversations" / "RESUME_BRIEF.md"
    allowed = [root, *(Path(value).expanduser().resolve() for value in safe_dirs)]
    if not any(output == directory or directory in output.parents for directory in allowed):
        raise ProtocolError("output is outside project root; pass --safe-output-dir for an explicit safe directory")
    return output


def main() -> int:
    args = parser().parse_args()
    try:
        root = project_root(args.project_root)
        bus = bus_root(root)
        agent = agent_root(root, args.agent_id)
        current = agent / "conversations" / "CURRENT_CONTEXT.md"
        current_meta = parse_frontmatter(current)
        checkpoint, missing = select_checkpoint(agent, current_meta)
        task, task_source = resolve_active_task(root, bus, agent, args.agent_id, current_meta)
        handoff = newest_by_created_at(list((agent / "handoffs").glob("*.md")))
        must_read = existing([
            root / "AGENTS.md", bus / "PROTOCOL.md", bus / "TEAM.yaml", bus / "CURRENT_PROJECT_CONTEXT.md",
            bus / "DECISIONS.md", agent / "ROLE.md", agent / "SYSTEM_PROMPT.md", current,
        ])
        must_read.extend(path for path in (checkpoint, task, handoff) if path and path not in must_read)
        rels = [path.relative_to(root).as_posix() for path in must_read]
        should_detect = args.detect_drift or args.fail_on_drift
        drift = detect_drift(root, checkpoint, missing) if should_detect else {"checked": False, "detected": False}
        runtime = runtime_summary(root, agent, args.agent_id)
        activity = recent_activity(agent)
        if should_detect and runtime["drift_fields"]:
            drift["detected"] = True
            drift["runtime_fields"] = runtime["drift_fields"]
        output = output_path(root, agent, args.output, args.safe_output_dir)
        content = f'''---
schema_version: "1.1"
doc_type: "resume_brief"
agent_id: {json.dumps(args.agent_id)}
generated_at: {json.dumps(now_iso())}
project_root: {json.dumps(str(root))}
active_task_source: {json.dumps(task_source)}
drift: {json.dumps(drift, ensure_ascii=False, sort_keys=True)}
runtime: {json.dumps(runtime, ensure_ascii=False, sort_keys=True)}
recent_activity: {json.dumps(activity, ensure_ascii=False, sort_keys=True)}
---

# Agent 最小恢复包

## 身份

- Agent ID：`{args.agent_id}`
- 项目根目录：`{root}`
- 最新检查点：`{checkpoint.name if checkpoint else '无'}`
- 当前任务：`{task.name if task else '无'}`（来源：`{task_source}`）
- 最近交接：`{handoff.name if handoff else '无'}`
- 漂移：`{'已检测到' if drift['detected'] else '未检测到' if drift['checked'] else '未检查'}`

## Runtime Profile

- Profile：`{runtime['runtime_profile_id'] or 'unknown'}`（验证：`{runtime['profile_verified']}`）
- Capture Status：`{json.dumps(runtime['capture_status'], ensure_ascii=False)}`
- Actual model/provider/status：`{runtime['actual']['model'] or 'unknown'}` / `{runtime['actual']['provider'] or 'unknown'}` / `{runtime['actual']['status']}`
- Actual platform/session：`{runtime['actual']['platform'] or 'unknown'}` / `{runtime['actual']['session'] or 'unknown'}`
- Runtime drift：`{', '.join(runtime['drift_fields']) or 'none'}`（unknown 不判为变化）

## 最近 Activity 与 Usage

- Activity：`{activity['activity_id'] or 'unknown'}`（验证：`{activity['verified']}`）
- Usage 来源：`{activity['usage_source'] or 'unknown'}`（引用/hash 验证：`{activity['usage_source_verified']}`）

## 按顺序读取

{chr(10).join(f'{index}. `{path}`' for index, path in enumerate(rels, 1))}

## 恢复后必须汇报

1. Agent ID 与项目根目录。
2. 当前任务、已确认决策、未完成事项和下一步。
3. 实际文件、Git 和运行环境与检查点是否一致。
4. 发现的漂移、冲突、风险或原文缺口。

恢复不依赖平台私有会话数据库；项目内文件与真实验证结果优先。不要仅凭摘要宣称事实，不要读取或输出密钥。
'''
        atomic_write(output, content)
        print(output)
        return 2 if args.fail_on_drift and drift["detected"] else 0
    except (ProtocolError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
