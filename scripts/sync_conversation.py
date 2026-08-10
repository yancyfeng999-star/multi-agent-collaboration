#!/usr/bin/env python3
"""Incrementally import a conversation export into an agent archive."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

from project_memory_lib import (
    agent_root,
    body_sha256,
    contains_secret,
    exclusive_lock,
    next_immutable_path,
    project_root,
    read_json,
    redact,
)
from protocol_lib import ProtocolError, atomic_write, now_iso


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="同步对话导出到项目外 Agent 治理归档")
    value.add_argument("--project-root", required=True)
    value.add_argument("--governance-root")
    value.add_argument("--project-id")
    value.add_argument("--agent-id", required=True)
    value.add_argument("--source-file", required=True, help="Markdown/text/JSON 对话导出文件")
    value.add_argument("--platform", required=True, choices=["hermes", "claude-code", "codex", "other"])
    value.add_argument("--session-id", required=True)
    value.add_argument("--task-id", default="UNSCOPED")
    value.add_argument("--message-start", type=int)
    value.add_argument("--message-end", type=int)
    value.add_argument("--allow-gap", action="store_true", help="显式允许并记录消息 ID/range 缺口")
    value.add_argument("--no-redact", action="store_true", help="仅用于已确认不含敏感信息的输入")
    value.add_argument("--no-redact-reason", help="禁用脱敏的审计理由")
    value.add_argument("--confirm-no-redact", action="store_true", help="强确认理解禁用脱敏的风险")
    return value


def _message_id(item: dict[str, Any]) -> int:
    raw = item.get("id", item.get("message_id"))
    if raw is None or isinstance(raw, bool):
        raise ProtocolError("JSON message id must be a positive integer")
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ProtocolError("JSON list messages require a stable integer id/message_id") from exc
    if value < 1 or str(raw).strip() != str(value):
        raise ProtocolError("JSON message id must be a canonical positive integer")
    return value


def _canonical_message(item: dict[str, Any]) -> str:
    return json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _message_hash(item: dict[str, Any]) -> str:
    return body_sha256(_canonical_message(item))


def _render_messages(messages: list[dict[str, Any]]) -> str:
    lines = []
    for item in messages:
        role = item.get("role", "unknown")
        content = item.get("content", item)
        rendered = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, sort_keys=True)
        lines.append(f"## {role}\n\n{rendered}")
    return "\n\n".join(lines)


def _read_source(path: Path) -> tuple[bytes, str, list[dict[str, Any]] | None]:
    if not path.is_file():
        raise ProtocolError(f"source file does not exist: {path}")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ProtocolError(f"source file is not UTF-8: {path}") from exc
    if path.suffix.lower() != ".json":
        return raw, text, None
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON export: {exc}") from exc
    if not isinstance(value, list):
        raise ProtocolError("incremental JSON export must be a list of messages")
    messages: list[dict[str, Any]] = []
    seen: set[int] = set()
    for item in value:
        if not isinstance(item, dict):
            raise ProtocolError("JSON list messages must be objects with stable ids")
        message_id = _message_id(item)
        if message_id in seen:
            raise ProtocolError(f"duplicate message id in source: {message_id}")
        seen.add(message_id)
        messages.append(item)
    messages.sort(key=_message_id)
    if not messages:
        raise ProtocolError("JSON message list is empty")
    return raw, text, messages


def _gap_ids(start: int, ids: list[int]) -> list[int]:
    if not ids:
        return []
    present = set(ids)
    return [value for value in range(start, ids[-1] + 1) if value not in present]


def _validate_no_redact(args: argparse.Namespace, text: str) -> None:
    if not args.no_redact:
        if args.no_redact_reason or args.confirm_no_redact:
            raise ProtocolError("--no-redact-reason/--confirm-no-redact require --no-redact")
        return
    if not args.no_redact_reason or not args.no_redact_reason.strip():
        raise ProtocolError("--no-redact requires a non-empty --no-redact-reason")
    if not args.confirm_no_redact:
        raise ProtocolError("--no-redact requires --confirm-no-redact")
    if contains_secret(text):
        raise ProtocolError("high-confidence secret detected; --no-redact fails closed")


def _active_mapping(mapping: Any, args: argparse.Namespace) -> dict[str, Any]:
    if not isinstance(mapping, dict) or not isinstance(mapping.get("active"), dict):
        raise ProtocolError("SESSION_MAP has no active session binding")
    active = mapping["active"]
    if active.get("platform") != args.platform or active.get("session_id") != args.session_id:
        raise ProtocolError("source platform/session does not match the active SESSION_MAP binding")
    cursor = active.get("last_synced_message_id", 0)
    if isinstance(cursor, bool) or not isinstance(cursor, int) or cursor < 0:
        raise ProtocolError("active SESSION_MAP has an invalid last_synced_message_id")
    return active


def _canonical_hash(value: dict[str, Any]) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _runtime_field(profile: dict[str, Any], field: str) -> tuple[str, str | None]:
    resolved = profile.get(field)
    if not isinstance(resolved, dict):
        raise ProtocolError(f"runtime profile has invalid {field} metadata")
    status = resolved.get("status")
    value = resolved.get("value")
    if status == "known":
        if not isinstance(value, str) or not value or value in {"unknown", "conflict"}:
            raise ProtocolError(f"runtime profile has invalid known {field}")
        return status, value
    if status in {"unknown", "conflict"} and value is None:
        return status, None
    raise ProtocolError(f"runtime profile has invalid {field} status")


def _runtime_binding(agent: Path, active: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    profile_id = active.get("runtime_profile_id")
    expected_hash = active.get("runtime_profile_sha256")
    if not isinstance(profile_id, str) or re.fullmatch(r"RP-[0-9]{6}", profile_id) is None:
        raise ProtocolError("active SESSION_MAP runtime profile reference is missing or invalid")
    if not isinstance(expected_hash, str) or re.fullmatch(r"[a-f0-9]{64}", expected_hash) is None:
        raise ProtocolError("active SESSION_MAP runtime profile hash is missing or invalid")
    profile_path = agent / "runtime" / "profiles" / f"{profile_id}.json"
    if not profile_path.is_file():
        raise ProtocolError(f"runtime profile does not exist: {profile_id}")
    profile = read_json(profile_path)
    if not isinstance(profile, dict):
        raise ProtocolError("runtime profile is not an object")
    if profile.get("runtime_profile_id") != profile_id:
        raise ProtocolError("runtime profile ID does not match SESSION_MAP")
    if profile.get("agent_id") != args.agent_id:
        raise ProtocolError("runtime profile agent does not match archive agent")
    session = profile.get("session")
    if not isinstance(session, dict) or session.get("status") != "known" or session.get("value") != args.session_id:
        raise ProtocolError("runtime profile session does not match archive session")
    record_hash = profile.get("record_hash")
    actual_hash = record_hash.get("value") if isinstance(record_hash, dict) else None
    unhashed = dict(profile)
    unhashed.pop("record_hash", None)
    if actual_hash != _canonical_hash(unhashed) or actual_hash != expected_hash:
        raise ProtocolError("runtime profile hash does not match profile content or SESSION_MAP")
    model_status, model = _runtime_field(profile, "model")
    provider_status, provider = _runtime_field(profile, "provider")
    return {
        "runtime_profile_id": profile_id, "runtime_profile_sha256": expected_hash,
        "actual_model_status": model_status, "actual_model": model,
        "actual_provider_status": provider_status, "actual_provider": provider,
    }


def sync(args: argparse.Namespace) -> Path:
    for label, value in (("task-id", args.task_id), ("session-id", args.session_id)):
        if not isinstance(value, str) or not value or value in {".", ".."} or "/" in value or "\\" in value or "\x00" in value:
            raise ProtocolError(f"invalid --{label}: path separators and traversal are forbidden")
    root = project_root(args.project_root)
    agent = agent_root(
        root, args.agent_id,
        governance_root=args.governance_root,
        project_id=args.project_id,
    )
    source = Path(args.source_file).expanduser().resolve()
    raw, source_body, messages = _read_source(source)
    _validate_no_redact(args, source_body)
    mapping_path = agent / "conversations" / "SESSION_MAP.json"

    with exclusive_lock(mapping_path.with_suffix(".lock")):
        mapping = read_json(mapping_path)
        active = _active_mapping(mapping, args)
        runtime = _runtime_binding(agent, active, args)
        cursor = active.get("last_synced_message_id", 0)
        previous_hashes = active.get("synced_message_hashes", {})
        if not isinstance(previous_hashes, dict):
            raise ProtocolError("active SESSION_MAP has invalid synced_message_hashes")

        gap: list[int] = []
        hashes_to_add: dict[str, str] = {}
        message_count: int
        if messages is not None:
            ids = [_message_id(item) for item in messages]
            if args.message_start is not None and args.message_start != ids[0]:
                raise ProtocolError("--message-start does not match first JSON message id")
            if args.message_end is not None and args.message_end != ids[-1]:
                raise ProtocolError("--message-end does not match last JSON message id")
            if ids[-1] < cursor:
                raise ProtocolError("backward sync rejected: source ends before current cursor")
            for item in messages:
                message_id = _message_id(item)
                if message_id <= cursor:
                    expected = previous_hashes.get(str(message_id))
                    if expected is None or expected != _message_hash(item):
                        raise ProtocolError(f"overlap mismatch or unverifiable overlap at message id {message_id}")
            new_messages = [item for item in messages if _message_id(item) > cursor]
            if not new_messages:
                last = active.get("last_archive")
                if not isinstance(last, str):
                    raise ProtocolError("duplicate sync has no prior archive reference")
                return agent / "conversations" / last
            new_ids = [_message_id(item) for item in new_messages]
            gap = _gap_ids(cursor + 1, new_ids)
            text = _render_messages(new_messages)
            start, end = new_ids[0], new_ids[-1]
            message_count = len(new_messages)
            hashes_to_add = {str(_message_id(item)): _message_hash(item) for item in messages}
        else:
            if args.message_start is None or args.message_end is None:
                raise ProtocolError("Markdown/text sync requires explicit --message-start and --message-end")
            start, end = args.message_start, args.message_end
            if start < 1 or end < start:
                raise ProtocolError("invalid message range")
            if end <= cursor:
                raise ProtocolError("backward or duplicate text range rejected (no stable message IDs)")
            if start <= cursor:
                raise ProtocolError("overlapping text range cannot be verified without stable message IDs")
            gap = list(range(cursor + 1, start))
            text = source_body
            message_count = end - start + 1

        if gap and not args.allow_gap:
            raise ProtocolError(f"message gap rejected: missing {gap[0]}-{gap[-1]} (use --allow-gap to record it)")

        redacted = False
        replacements = 0
        if not args.no_redact:
            text, replacements = redact(text)
            redacted = True
        normalized = text.rstrip()
        source_digest = hashlib.sha256(raw).hexdigest()
        body_digest = body_sha256(normalized)
        month = now_iso()[:7]
        archive = next_immutable_path(
            agent / "conversations" / "archive" / month,
            f"{args.task_id}_{args.session_id}_{start}-{end}",
        )
        reason = args.no_redact_reason.strip() if args.no_redact else None
        document = f'''---
schema_version: "1.1"
doc_type: "conversation_archive"
agent_id: "{args.agent_id}"
task_ids: {json.dumps([args.task_id], ensure_ascii=False)}
platform: "{args.platform}"
session_id: "{args.session_id}"
runtime_profile_id: {json.dumps(runtime["runtime_profile_id"], ensure_ascii=False)}
runtime_profile_sha256: {json.dumps(runtime["runtime_profile_sha256"], ensure_ascii=False)}
actual_model_status: {json.dumps(runtime["actual_model_status"], ensure_ascii=False)}
actual_model: {json.dumps(runtime["actual_model"], ensure_ascii=False)}
actual_provider_status: {json.dumps(runtime["actual_provider_status"], ensure_ascii=False)}
actual_provider: {json.dumps(runtime["actual_provider"], ensure_ascii=False)}
exported_at: "{now_iso()}"
source_message_range: "{start}-{end}"
message_count: {message_count}
source_file_sha256: "{source_digest}"
source_sha256: "{source_digest}"
normalized_body_sha256: "{body_digest}"
content_sha256: "{body_digest}"
redacted: {str(redacted).lower()}
redaction_count: {replacements}
no_redact_reason: {json.dumps(reason, ensure_ascii=False)}
gap_allowed: {str(bool(gap)).lower()}
missing_message_ids: {json.dumps(gap)}
---

# 完整对话归档

{normalized}
'''
        atomic_write(archive, document)
        try:
            active["last_synced_at"] = now_iso()
            active["last_archive"] = archive.relative_to(agent / "conversations").as_posix()
            active["last_synced_message_id"] = end
            if hashes_to_add:
                updated_hashes = dict(previous_hashes)
                updated_hashes.update(hashes_to_add)
                active["synced_message_hashes"] = updated_hashes
            active["last_source_file_sha256"] = source_digest
            active["last_normalized_body_sha256"] = body_digest
            active["last_message_count"] = message_count
            if gap:
                active.setdefault("sync_gaps", []).append({
                    "recorded_at": now_iso(), "from_cursor": cursor,
                    "missing_message_ids": gap, "accepted_range": f"{start}-{end}",
                })
            atomic_write(mapping_path, json.dumps(mapping, ensure_ascii=False, indent=2) + "\n")
        except Exception:
            archive.unlink(missing_ok=True)
            raise
        return archive


def main() -> int:
    args = parser().parse_args()
    try:
        print(sync(args))
        return 0
    except (ProtocolError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
