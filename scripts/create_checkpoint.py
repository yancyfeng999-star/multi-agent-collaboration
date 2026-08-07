#!/usr/bin/env python3
"""Create an immutable checkpoint after verifying its source archives."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

from project_memory_lib import (
    agent_root,
    body_sha256,
    bus_root,
    exclusive_lock,
    next_checkpoint_id,
    parse_frontmatter,
    project_root,
    relative_to_bus,
)
from protocol_lib import ProtocolError, now_iso

REQUIRED_HEADINGS = [
    "长期使命", "当前总目标", "当前任务与状态", "已确认需求", "关键决策及原因",
    "已完成事项", "修改文件", "命令与真实结果", "失败尝试和踩坑", "未解决事项",
    "风险与假设", "下一步", "恢复时必须读取", "可按需读取的原文",
]
CURRENT_SECTIONS = [
    ("当前任务", "当前任务与状态"),
    ("决策", "关键决策及原因"),
    ("待完成", "未解决事项"),
    ("关键文件", "修改文件"),
    ("验证", "命令与真实结果"),
    ("风险", "风险与假设"),
    ("下一步", "下一步"),
]


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="创建不可变 Agent 上下文检查点")
    value.add_argument("--project-root", required=True)
    value.add_argument("--agent-id", required=True)
    value.add_argument("--summary-file", required=True, help="包含检查点各必填栏目正文的 Markdown")
    value.add_argument("--source-archive", action="append", required=True)
    value.add_argument("--task-id", action="append", default=[])
    value.add_argument("--source-message-range", default="unknown")
    return value


def document_body(path: Path) -> str:
    """Return the immutable content covered by a document's content_sha256."""
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---\n", 4)
    if not text.startswith("---\n") or end < 0:
        raise ProtocolError(f"unterminated frontmatter: {path}")
    body = text[end + len("\n---\n"):].lstrip("\n")
    prefix = "# 完整对话归档\n\n"
    return (body[len(prefix):] if body.startswith(prefix) else body).rstrip()


def summary_sections(body: str) -> dict[str, str]:
    sections: dict[str, str] = {}
    current: str | None = None
    lines: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current = line[3:].strip()
            lines = []
        elif current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    return sections


def staged_write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    return temporary


def canonical_profile_hash(profile: dict) -> str:
    unhashed = dict(profile)
    unhashed.pop("record_hash", None)
    payload = json.dumps(unhashed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def archive_runtime_binding(source: Path, meta: dict, agent: Path, bus: Path, agent_id: str) -> tuple[str, str]:
    profile_id = meta.get("runtime_profile_id")
    declared_hash = meta.get("runtime_profile_sha256")
    if not isinstance(profile_id, str) or re.fullmatch(r"RP-[0-9]{6}", profile_id) is None:
        raise ProtocolError(f"source archive runtime profile ID is missing or invalid: {source}")
    if not isinstance(declared_hash, str) or re.fullmatch(r"[a-f0-9]{64}", declared_hash) is None:
        raise ProtocolError(f"source archive runtime profile hash is missing or invalid: {source}")
    profile_path = (agent / "runtime" / "profiles" / f"{profile_id}.json").resolve()
    if not profile_path.is_file():
        raise ProtocolError(f"runtime profile does not exist: {profile_id}")
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProtocolError(f"runtime profile is unreadable: {profile_id}") from exc
    if not isinstance(profile, dict) or profile.get("runtime_profile_id") != profile_id:
        raise ProtocolError(f"runtime profile ID mismatch: {profile_id}")
    if profile.get("agent_id") != agent_id:
        raise ProtocolError(f"runtime profile agent mismatch: {profile_id}")
    session = profile.get("session")
    if not isinstance(session, dict) or session.get("status") != "known" or session.get("value") != meta.get("session_id"):
        raise ProtocolError(f"runtime profile session mismatch: {profile_id}")
    record_hash = profile.get("record_hash")
    stored_hash = record_hash.get("value") if isinstance(record_hash, dict) else None
    if stored_hash != canonical_profile_hash(profile) or stored_hash != declared_hash:
        raise ProtocolError(f"runtime profile hash mismatch: {profile_id}")
    for field in ("model", "provider"):
        resolved = profile.get(field)
        status = resolved.get("status") if isinstance(resolved, dict) else None
        value = resolved.get("value") if isinstance(resolved, dict) else None
        if meta.get(f"actual_{field}_status") != status or meta.get(f"actual_{field}") != value:
            raise ProtocolError(f"source archive {field} does not match runtime profile: {source}")
    return relative_to_bus(profile_path, bus), declared_hash


def main() -> int:
    args = parser().parse_args()
    staged: list[Path] = []
    checkpoint_path: Path | None = None
    checkpoint_installed = False
    try:
        root = project_root(args.project_root)
        bus = bus_root(root)
        agent = agent_root(root, args.agent_id)
        summary_path = Path(args.summary_file).expanduser().resolve()
        if not summary_path.is_file():
            raise ProtocolError(f"summary file does not exist: {summary_path}")
        body = summary_path.read_text(encoding="utf-8").strip()
        sections = summary_sections(body)
        missing = [heading for heading in REQUIRED_HEADINGS if heading not in sections]
        if missing:
            raise ProtocolError(f"checkpoint summary is missing headings: {', '.join(missing)}")

        # The lock covers validation, numbering, both writes, and CURRENT_CONTEXT publication.
        lock_path = agent / "conversations" / ".checkpoint.lock"
        with exclusive_lock(lock_path):
            sources: list[str] = []
            source_hashes: dict[str, str] = {}
            runtime_profile_hashes: dict[str, str] = {}
            for raw in args.source_archive:
                source = Path(raw).expanduser().resolve()
                if not source.is_file():
                    raise ProtocolError(f"source archive does not exist: {source}")
                meta = parse_frontmatter(source)
                if meta.get("doc_type") != "conversation_archive":
                    raise ProtocolError(f"not a conversation archive: {source}")
                expected_archive_root = (agent / "conversations" / "archive").resolve()
                try:
                    source.relative_to(expected_archive_root)
                except ValueError as exc:
                    raise ProtocolError(f"source archive does not belong to {args.agent_id}: {source}") from exc
                if meta.get("agent_id") != args.agent_id:
                    raise ProtocolError(f"source archive agent_id mismatch: {source}")
                actual = body_sha256(document_body(source))
                declared = meta.get("content_sha256")
                if not isinstance(declared, str) or declared != actual:
                    raise ProtocolError(f"source archive content_sha256 mismatch: {source}")
                relative = relative_to_bus(source, bus)
                if relative in source_hashes:
                    raise ProtocolError(f"duplicate source archive: {source}")
                sources.append(relative)
                source_hashes[relative] = actual

                profile_relative, profile_hash = archive_runtime_binding(source, meta, agent, bus, args.agent_id)
                previous_hash = runtime_profile_hashes.get(profile_relative)
                if previous_hash is not None and previous_hash != profile_hash:
                    raise ProtocolError(f"conflicting runtime profile hash: {profile_relative}")
                runtime_profile_hashes[profile_relative] = profile_hash

            runtime_profile_hashes = dict(sorted(runtime_profile_hashes.items()))
            runtime_profiles = list(runtime_profile_hashes)

            directory = agent / "conversations" / "checkpoints"
            checkpoint_id = next_checkpoint_id(directory)
            previous = None if checkpoint_id == "CP-0001" else f"CP-{int(checkpoint_id[-4:]) - 1:04d}"
            if previous and not (directory / f"{previous}.md").is_file():
                raise ProtocolError(f"checkpoint chain is broken before {checkpoint_id}: missing {previous}")
            digest = body_sha256(body)
            checkpoint_path = directory / f"{checkpoint_id}.md"
            if checkpoint_path.exists():
                raise ProtocolError(f"checkpoint already exists: {checkpoint_path}")
            timestamp = now_iso()
            document = f'''---
schema_version: "1.0"
doc_type: "checkpoint"
checkpoint_id: "{checkpoint_id}"
agent_id: "{args.agent_id}"
task_ids: {json.dumps(args.task_id, ensure_ascii=False)}
created_at: "{timestamp}"
previous_checkpoint: {json.dumps(previous)}
source_archives: {json.dumps(sources, ensure_ascii=False)}
source_archive_hashes: {json.dumps(source_hashes, ensure_ascii=False, sort_keys=True)}
source_runtime_profiles: {json.dumps(runtime_profiles, ensure_ascii=False)}
source_runtime_profile_hashes: {json.dumps(runtime_profile_hashes, ensure_ascii=False)}
source_message_range: {json.dumps(args.source_message_range, ensure_ascii=False)}
content_sha256: "{digest}"
---

# 上下文检查点

{body}
'''
            summary_blocks = "\n\n".join(
                f"## {title}\n\n{sections[source_heading]}" for title, source_heading in CURRENT_SECTIONS
            )
            current = agent / "conversations" / "CURRENT_CONTEXT.md"
            current_document = f'''---
schema_version: "1.0"
doc_type: "current_agent_context"
agent_id: "{args.agent_id}"
updated_at: "{timestamp}"
latest_checkpoint: "conversations/checkpoints/{checkpoint_id}.md"
active_task: {json.dumps(args.task_id[-1] if args.task_id else None, ensure_ascii=False)}
---

# 当前 Agent 上下文

最新有效上下文见 [{checkpoint_id}](checkpoints/{checkpoint_id}.md)。

{summary_blocks}

## 恢复顺序

1. 读取本文件。
2. 读取最新检查点。
3. 读取检查点引用的任务、交接和必要原文。
4. 对照实际文件、Git 与运行环境，记录漂移后再继续。
'''
            checkpoint_temp = staged_write(checkpoint_path, document)
            staged.append(checkpoint_temp)
            current_temp = staged_write(current, current_document)
            staged.append(current_temp)
            try:
                os.replace(checkpoint_temp, checkpoint_path)
                staged.remove(checkpoint_temp)
                checkpoint_installed = True
                os.replace(current_temp, current)
                staged.remove(current_temp)
            except OSError:
                if checkpoint_installed:
                    checkpoint_path.unlink(missing_ok=True)
                    checkpoint_installed = False
                raise

        print(checkpoint_path)
        return 0
    except (ProtocolError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    finally:
        for temporary in staged:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
