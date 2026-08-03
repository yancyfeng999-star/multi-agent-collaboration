#!/usr/bin/env python3
"""Shared protocol primitives for the multi-agent-collaboration document bus."""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Iterable


PROTOCOL_VERSION = "3"

RUN_STATUSES = {
    "initializing",
    "active",
    "blocked",
    "waiting_external",
    "waiting_user_approval",
    "release_ready",
    "completed",
    "failed",
    "cancelled",
    "superseded",
    "archived",
}

TASK_STATUSES = {
    "ready",
    "dispatched",
    "acknowledged",
    "running",
    "blocked",
    "waiting_external",
    "waiting_user_approval",
    "handoff_ready",
    "reviewing",
    "changes_requested",
    "qa_running",
    "qa_failed",
    "qa_passed",
    "release_ready",
    "completed",
    "failed",
    "cancelled",
    "superseded",
    "expired",
    "dead_letter",
}

TERMINAL_TASK_STATUSES = {
    "completed",
    "failed",
    "cancelled",
    "superseded",
    "expired",
    "dead_letter",
}

NATIVE_EVENTS = {
    "THREAD_CREATE_REQUESTED",
    "THREAD_PROVISIONING",
    "THREAD_READY",
    "THREAD_MESSAGE_SENT",
    "THREAD_RUNNING",
    "THREAD_PROGRESS",
    "THREAD_RESULT_RECEIVED",
    "THREAD_FAILED",
    "THREAD_HANDOFF_STARTED",
    "THREAD_HANDOFF_COMPLETED",
    "THREAD_HANDOFF_FAILED",
    "THREAD_ARCHIVED",
    "SUBAGENT_SPAWNED",
    "SUBAGENT_MESSAGE_SENT",
    "SUBAGENT_RESULT_RECEIVED",
    "SUBAGENT_FAILED",
    "SUBAGENT_CLOSED",
}

DOCUMENT_SUBAGENT_EVENTS = {
    "DOCUMENT_SUBAGENT_DELEGATED",
    "DOCUMENT_SUBAGENT_RESULT_RECEIVED",
    "DOCUMENT_SUBAGENT_FAILED",
    "DOCUMENT_SUBAGENT_CLOSED",
}

TASK_EVENTS = {
    "TASK_READY",
    "TASK_DISPATCHED",
    "ACK",
    "LEASE_ACQUIRED",
    "LEASE_RENEWED",
    "HANDOFF_READY",
    "REVIEW_STARTED",
    "CHANGES_REQUESTED",
    "REVIEW_APPROVED",
    "QA_FAILED",
    "QA_PASSED",
    "BLOCKED",
    "WAITING_USER_APPROVAL",
    "APPROVAL_GRANTED",
    "APPROVAL_REJECTED",
    "TASK_RESUMED",
    "RELEASE_READY",
    "TASK_COMPLETED",
    "TASK_FAILED",
    "TASK_CANCELLED",
    "TASK_SUPERSEDED",
    "TASK_EXPIRED",
    "RETRY_SCHEDULED",
    "DEAD_LETTERED",
}

EVENT_NAMES = TASK_EVENTS | NATIVE_EVENTS | DOCUMENT_SUBAGENT_EVENTS

REPEATABLE_EVENTS = {
    "LEASE_RENEWED",
    "THREAD_PROGRESS",
    "SUBAGENT_MESSAGE_SENT",
    "RETRY_SCHEDULED",
}

EVENT_PAYLOAD_KINDS = {
    "TASK_READY": "task",
    "TASK_DISPATCHED": "task",
    "ACK": "ack",
    "LEASE_ACQUIRED": "lease",
    "LEASE_RENEWED": "lease",
    "HANDOFF_READY": "result",
    "CHANGES_REQUESTED": "review",
    "REVIEW_APPROVED": "review",
    "QA_FAILED": "qa",
    "QA_PASSED": "qa",
    "BLOCKED": "result_or_evidence",
    "WAITING_USER_APPROVAL": "gate",
    "APPROVAL_GRANTED": "gate",
    "APPROVAL_REJECTED": "gate",
    "RELEASE_READY": "gate",
    "TASK_COMPLETED": "result",
    "TASK_FAILED": "result",
    "DEAD_LETTERED": "dead_letter",
    "THREAD_RESULT_RECEIVED": "result",
    "DOCUMENT_SUBAGENT_RESULT_RECEIVED": "result",
}

RISK_TO_GATE = {
    "schema": "migration",
    "migration": "migration",
    "production_credentials": "production_credentials",
    "production_data": "production_data",
    "funds": "funds",
    "payment": "funds",
    "deploy": "deploy",
    "release": "release",
    "rollback": "rollback",
    "delete": "delete",
    "permission_expansion": "permission_expansion",
    "managed_subagent": "managed_subagent",
}


class ProtocolError(ValueError):
    """Raised when a protocol document cannot be parsed safely."""


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid_iso8601(value: str, *, require_timezone: bool = True) -> bool:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return not require_timezone or parsed.tzinfo is not None


def parse_scalar(raw: str) -> str:
    value = raw.strip()
    if value == "":
        return ""
    if value.startswith('"'):
        if not value.endswith('"'):
            raise ProtocolError(f"unterminated quoted scalar: {value}")
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"invalid quoted scalar: {value}") from exc
        if not isinstance(decoded, str):
            raise ProtocolError(f"quoted scalar is not a string: {value}")
        return decoded
    if value.startswith(("[", "{")):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ProtocolError(f"invalid JSON inline collection: {value}") from exc
        if not isinstance(decoded, (list, dict)):
            raise ProtocolError(f"inline collection must be a list or object: {value}")
        return value
    if value in {"null", "true", "false"} or re.fullmatch(r"-?\d+", value):
        return value
    raise ProtocolError(
        f"unquoted protocol string is not allowed: {value}; use JSON double quotes"
    )


def scalar_map(text: str, *, source: str = "<document>") -> dict[str, str]:
    """Parse the protocol's flat YAML subset and reject duplicate keys."""

    values: dict[str, str] = {}
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z][A-Za-z0-9_-]*):\s*(.*?)\s*", line)
        if not match:
            raise ProtocolError(
                f"{source}:{line_number}: only flat key/value protocol fields are allowed"
            )
        key = match.group(1)
        if key in values:
            raise ProtocolError(f"{source}:{line_number}: duplicate key {key}")
        values[key] = parse_scalar(match.group(2))
    return values


def frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    if text.startswith("\ufeff"):
        raise ProtocolError(f"{path}: UTF-8 BOM is not allowed")
    if "\r\n" in text:
        raise ProtocolError(f"{path}: CRLF is not allowed in frozen protocol documents")
    if not text.startswith("---\n"):
        raise ProtocolError(f"{path}: missing YAML frontmatter")
    parts = text.split("---\n", 2)
    if len(parts) != 3:
        raise ProtocolError(f"{path}: unclosed YAML frontmatter")
    return scalar_map(parts[1], source=str(path))


def json_string_list(value: str, *, field: str, source: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"{source}: {field} must be a JSON inline list") from exc
    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise ProtocolError(f"{source}: {field} must contain only strings")
    return parsed


def json_string_map(value: str, *, field: str, source: str) -> dict[str, str]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"{source}: {field} must be a JSON inline object") from exc
    if not isinstance(parsed, dict) or not all(
        isinstance(key, str) and isinstance(item, str) for key, item in parsed.items()
    ):
        raise ProtocolError(f"{source}: {field} must map strings to strings")
    return parsed


def unquote(value: str) -> str:
    return parse_scalar(value)


def parse_agent_profiles(text: str, *, source: str = "agents.yaml") -> dict[str, dict[str, object]]:
    """Parse the deliberately small agents.yaml shape and reject duplicate agents."""

    marker = "\nagents:\n"
    if marker not in text:
        raise ProtocolError(f"{source}: missing agents registry")
    registry_text = text.split(marker, 1)[1]
    scalar_fields = {
        "runtime",
        "role",
        "status",
        "parent_agent_id",
        "delegation_depth",
        "thread_id",
        "inbox",
        "outbox",
        "current_task",
        "handoff_to",
    }
    list_fields = {"readable_paths", "writable_paths", "forbidden_paths"}
    current_list: str | None = None
    for line_number, line in enumerate(
        registry_text.splitlines(),
        start=text.split(marker, 1)[0].count("\n") + 3,
    ):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        agent_match = re.fullmatch(r"  - agent_id:\s*(.+?)\s*", line)
        if agent_match:
            unquote(agent_match.group(1))
            current_list = None
            continue
        field_match = re.fullmatch(r"    ([A-Za-z][A-Za-z0-9_-]*):\s*(.*?)\s*", line)
        if field_match:
            field, raw_value = field_match.groups()
            if field not in scalar_fields | list_fields:
                raise ProtocolError(
                    f"{source}:{line_number}: unknown agent field {field}"
                )
            if field in list_fields:
                current_list = field if raw_value == "" else None
                if raw_value:
                    json_string_list(
                        raw_value,
                        field=field,
                        source=f"{source}:{line_number}",
                    )
            else:
                if raw_value == "":
                    raise ProtocolError(
                        f"{source}:{line_number}: agent field {field} cannot be empty"
                    )
                unquote(raw_value)
                current_list = None
            continue
        item_match = re.fullmatch(r"      -\s+(.+?)\s*", line)
        if item_match and current_list:
            unquote(item_match.group(1))
            continue
        raise ProtocolError(
            f"{source}:{line_number}: invalid agent registry structure"
        )

    profiles: dict[str, dict[str, object]] = {}
    pattern = re.compile(
        r"(?ms)^[ \t]*-[ \t]+agent_id:[ \t]*(.+?)[ \t]*$"
        r"(.*?)(?=^[ \t]*-[ \t]+agent_id:|\Z)"
    )
    for match in pattern.finditer(text):
        agent_id = unquote(match.group(1))
        if agent_id in profiles:
            raise ProtocolError(f"{source}: duplicate agent_id {agent_id}")
        block = match.group(2)
        profile: dict[str, object] = {}
        seen_fields: set[str] = set()
        scalar_profile_fields = (
            "runtime",
            "role",
            "status",
            "parent_agent_id",
            "delegation_depth",
            "thread_id",
            "inbox",
            "outbox",
            "current_task",
            "handoff_to",
        )
        for field in scalar_profile_fields:
            matches = list(
                re.finditer(
                    rf"^[ \t]+{re.escape(field)}:[ \t]*(.+?)[ \t]*$",
                    block,
                    re.MULTILINE,
                )
            )
            if len(matches) > 1:
                raise ProtocolError(f"{source}: duplicate field {field} for {agent_id}")
            if matches:
                seen_fields.add(field)
                profile[field] = unquote(matches[0].group(1))
        for field in ("readable_paths", "writable_paths", "forbidden_paths"):
            matches = list(
                re.finditer(
                    rf"^[ \t]+{re.escape(field)}:[ \t]*(.*?)[ \t]*$",
                    block,
                    re.MULTILINE,
                )
            )
            if len(matches) != 1:
                if not matches:
                    raise ProtocolError(
                        f"{source}: missing field {field} for {agent_id}"
                    )
                raise ProtocolError(f"{source}: duplicate field {field} for {agent_id}")
            seen_fields.add(field)
            inline_value = matches[0].group(1)
            if inline_value:
                profile[field] = json_string_list(
                    inline_value,
                    field=field,
                    source=f"{source}:{agent_id}",
                )
                continue
            list_start = matches[0].end()
            items: list[str] = []
            for line in block[list_start:].splitlines():
                item_match = re.match(r"^[ \t]+-[ \t]+(.+?)[ \t]*$", line)
                if item_match:
                    items.append(unquote(item_match.group(1)))
                    continue
                if line.strip():
                    break
            profile[field] = items
        missing_scalar_fields = set(scalar_profile_fields) - seen_fields
        if missing_scalar_fields:
            raise ProtocolError(
                f"{source}: missing fields for {agent_id}: "
                + ", ".join(sorted(missing_scalar_fields))
            )
        profiles[agent_id] = profile
    if not profiles:
        raise ProtocolError(f"{source}: no agents found")
    return profiles


def resolve_protocol_path(value: str, project_root: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = project_root / path
    return path.resolve(strict=False)


def path_within(child: str | Path, parents: Iterable[str | Path], project_root: Path) -> bool:
    child_path = resolve_protocol_path(str(child), project_root)
    for parent in parents:
        parent_path = resolve_protocol_path(str(parent), project_root)
        try:
            child_path.relative_to(parent_path)
            return True
        except ValueError:
            continue
    return False


def paths_overlap(left: str | Path, right: str | Path, project_root: Path) -> bool:
    left_path = resolve_protocol_path(str(left), project_root)
    right_path = resolve_protocol_path(str(right), project_root)
    try:
        left_path.relative_to(right_path)
        return True
    except ValueError:
        pass
    try:
        right_path.relative_to(left_path)
        return True
    except ValueError:
        return False


def next_task_state(current: str | None, event: str, governance: str) -> str | None:
    if event in NATIVE_EVENTS or event in DOCUMENT_SUBAGENT_EVENTS:
        return current if current is not None else None
    if event == "TASK_READY":
        return "ready" if current is None else None
    if event == "TASK_DISPATCHED":
        return "dispatched" if current == "ready" else None
    if event == "ACK":
        return "acknowledged" if current in {"ready", "dispatched"} else None
    if event == "LEASE_ACQUIRED":
        return "running" if current == "acknowledged" else None
    if event == "LEASE_RENEWED":
        return "running" if current == "running" else None
    if event == "HANDOFF_READY":
        return "handoff_ready" if current == "running" else None
    if event == "REVIEW_STARTED":
        return "reviewing" if current == "handoff_ready" else None
    if event == "CHANGES_REQUESTED":
        return "changes_requested" if current in {"handoff_ready", "reviewing"} else None
    if event == "REVIEW_APPROVED":
        return "qa_running" if current in {"handoff_ready", "reviewing"} else None
    if event == "QA_FAILED":
        return "qa_failed" if current == "qa_running" else None
    if event == "QA_PASSED":
        return "qa_passed" if current == "qa_running" else None
    if event == "RELEASE_READY":
        return "release_ready" if current == "qa_passed" else None
    if event == "BLOCKED":
        return "blocked" if current not in {None, *TERMINAL_TASK_STATUSES} else None
    if event == "WAITING_USER_APPROVAL":
        return (
            "waiting_user_approval"
            if current not in {None, *TERMINAL_TASK_STATUSES}
            else None
        )
    if event == "APPROVAL_GRANTED":
        return "ready" if current == "waiting_user_approval" else None
    if event == "APPROVAL_REJECTED":
        return "cancelled" if current == "waiting_user_approval" else None
    if event == "TASK_RESUMED":
        return (
            "ready"
            if current
            in {"blocked", "waiting_external", "changes_requested", "qa_failed"}
            else None
        )
    if event == "TASK_COMPLETED":
        allowed = {"qa_passed", "release_ready"}
        if governance == "light":
            allowed.update({"running", "handoff_ready"})
        return "completed" if current in allowed else None
    if event == "TASK_FAILED":
        return "failed" if current not in {None, "completed", "dead_letter"} else None
    if event == "TASK_CANCELLED":
        return "cancelled" if current not in {None, *TERMINAL_TASK_STATUSES} else None
    if event == "TASK_SUPERSEDED":
        return "superseded" if current not in {None, *TERMINAL_TASK_STATUSES} else None
    if event == "TASK_EXPIRED":
        return "expired" if current not in {None, *TERMINAL_TASK_STATUSES} else None
    if event == "RETRY_SCHEDULED":
        return "waiting_external" if current in {"blocked", "failed"} else None
    if event == "DEAD_LETTERED":
        return "dead_letter" if current not in {None, "completed"} else None
    return None


def derive_run_status(task_states: dict[str, str]) -> str:
    if not task_states:
        return "initializing"
    states = set(task_states.values())
    if states == {"completed"}:
        return "completed"
    if states.issubset(TERMINAL_TASK_STATUSES):
        if states.intersection({"failed", "dead_letter"}):
            return "failed"
        if "completed" in states:
            return "completed"
        return "cancelled" if "cancelled" in states else "superseded"
    if "waiting_user_approval" in states:
        return "waiting_user_approval"
    if states.intersection({"blocked", "dead_letter", "failed"}):
        return "blocked"
    if "waiting_external" in states:
        return "waiting_external"
    if "release_ready" in states:
        return "release_ready"
    return "active"


def event_records(events_dir: Path) -> list[tuple[Path, dict[str, str]]]:
    records: list[tuple[Path, dict[str, str]]] = []
    for path in sorted(events_dir.glob("*.yaml")):
        values = scalar_map(path.read_text(encoding="utf-8"), source=str(path))
        records.append((path, values))
    return records


def replay_task_states(
    records: Iterable[tuple[Path, dict[str, str]]],
    governance: str,
) -> tuple[dict[str, str], list[str]]:
    states: dict[str, str] = {}
    errors: list[str] = []
    for path, values in records:
        task_id = values.get("task_id", "")
        event = values.get("event", "")
        current = states.get(task_id)
        new_state = next_task_state(current, event, governance)
        if new_state is None:
            errors.append(
                f"{path.name}: illegal transition {current or 'none'} -> {event or 'missing'}"
            )
            continue
        states[task_id] = new_state
    return states, errors


def render_state(run_id: str, task_states: dict[str, str], sequence: int, updated_at: str) -> str:
    run_status = derive_run_status(task_states)
    lines = [
        f"protocol_version: {PROTOCOL_VERSION}",
        f"run_id: {quote(run_id)}",
        f"status: {quote(run_status)}",
        f"event_sequence: {sequence}",
        f"task_states: {json.dumps(task_states, ensure_ascii=False, sort_keys=True)}",
    ]
    for status in sorted(TASK_STATUSES):
        task_ids = sorted(task_id for task_id, value in task_states.items() if value == status)
        lines.append(f"{status}_tasks: {json.dumps(task_ids, ensure_ascii=False)}")
    lines.extend((f"updated_at: {quote(updated_at)}", ""))
    return "\n".join(lines)


def replace_flat_scalar(path: Path, key: str, rendered_value: str) -> None:
    content = path.read_text(encoding="utf-8")
    content, count = re.subn(
        rf"^{re.escape(key)}:\s*.*$",
        f"{key}: {rendered_value}",
        content,
        flags=re.MULTILINE,
    )
    if count != 1:
        raise ProtocolError(f"{path}: expected exactly one {key} field")
    atomic_write(path, content)


def refresh_runtime_documents(
    run_dir: Path,
    manifest: dict[str, str],
    task_states: dict[str, str],
    *,
    status_override: str | None = None,
) -> None:
    run_status = status_override or derive_run_status(task_states)
    ready = sorted(
        task_id
        for task_id, status in task_states.items()
        if status in {"ready", "dispatched"}
    )
    waiting_user = sorted(
        task_id
        for task_id, status in task_states.items()
        if status == "waiting_user_approval"
    )
    blocked = sorted(
        task_id
        for task_id, status in task_states.items()
        if status in {"blocked", "failed", "dead_letter"}
    )
    if waiting_user:
        target = "user"
        action = f"review pending decisions for {', '.join(waiting_user)}"
        gate = "user_approval"
    elif blocked:
        target = "coordinator"
        action = f"recover blocked tasks: {', '.join(blocked)}"
        gate = "recovery_required"
    elif ready:
        target = "coordinator"
        action = f"check dependencies, locks, and dispatch: {', '.join(ready)}"
        gate = "none"
    elif run_status in {"completed", "cancelled", "superseded", "archived"}:
        target = "none"
        action = "archive the run" if run_status != "archived" else "none"
        gate = "none"
    elif task_states:
        target = "coordinator"
        action = "monitor active tasks and persist the next event"
        gate = "none"
    else:
        target = "coordinator"
        action = "define run-local agents and create the approved task graph"
        gate = "none"
    atomic_write(
        run_dir / "next-action.md",
        "\n".join(
            (
                "# Next Action",
                "",
                f"- Run: `{manifest['run_id']}`",
                f"- Current status: `{run_status}`",
                f"- Ready task: `{','.join(ready) if ready else 'none'}`",
                f"- Target agent: `{target}`",
                f"- Transport: `{manifest.get('transport', 'unknown')}`",
                f"- Version governance: `{manifest.get('versioning_mode', 'unknown')}`",
                f"- Release train: `{manifest.get('release_train_id', 'unknown')}`",
                f"- Delivery version: `{manifest.get('target_version', 'null')}`",
                f"- Action: {action}",
                f"- Blocking gate: `{gate}`",
                "",
            )
        ),
    )

    commits: list[str] = []
    verification: list[str] = []
    risks: list[str] = []
    for result_path in sorted((run_dir / "outbox").glob("*/*-result-*.md")):
        try:
            result = frontmatter(result_path)
        except ProtocolError:
            continue
        task_id = result.get("task_id", result_path.stem)
        attempt_id = result.get("attempt_id", "unknown-attempt")
        status = result.get("status", "unknown")
        label = f"{task_id}/{attempt_id} [{status}]"
        commit = result.get("implementation_commit", "null")
        if commit != "null":
            commits.append(f"{label}: {commit}")
        verification.append(
            f"{label}: {result.get('verification_status', 'unknown')}"
        )
        risk = result.get("risk_summary", "not recorded")
        risks.append(f"{label}: {risk}")
    task_lines = [
        f"- {task_id}: {status}" for task_id, status in sorted(task_states.items())
    ] or ["- none"]
    atomic_write(
        run_dir / "summary.md",
        "\n".join(
            (
                "# Run Summary",
                "",
                f"- Run: `{manifest['run_id']}`",
                f"- Objective: {manifest.get('objective', '')}",
                f"- Status: {run_status}",
                f"- Version governance: {manifest.get('versioning_mode', 'unknown')}",
                f"- Release train: {manifest.get('release_train_id', 'unknown')}",
                f"- Delivery version: {manifest.get('target_version', 'null')}",
                "",
                "## Tasks",
                "",
                *task_lines,
                "",
                "## Commits",
                "",
                *([f"- {item}" for item in commits] or ["- none"]),
                "",
                "## Verification",
                "",
                *([f"- {item}" for item in verification] or ["- none"]),
                "",
                "## Residual Risks",
                "",
                *([f"- {item}" for item in risks] or ["- none recorded"]),
                "",
            )
        ),
    )


def rebuild_state(run_dir: Path) -> tuple[dict[str, str], list[str]]:
    manifest = scalar_map(
        (run_dir / "manifest.yaml").read_text(encoding="utf-8"),
        source=str(run_dir / "manifest.yaml"),
    )
    records = event_records(run_dir / "events")
    states, errors = replay_task_states(records, manifest.get("governance", ""))
    if errors:
        return states, errors
    sequence = 0
    if records:
        sequence = max(int(values["sequence"]) for _, values in records)
    updated_at = now_iso()
    atomic_write(
        run_dir / "state.yaml",
        render_state(manifest["run_id"], states, sequence, updated_at),
    )
    run_status = derive_run_status(states)
    if (run_dir / "archive" / "ARCHIVED.yaml").is_file():
        run_status = "archived"
        replace_flat_scalar(
            run_dir / "state.yaml",
            "status",
            quote(run_status),
        )
    replace_flat_scalar(
        run_dir / "manifest.yaml",
        "status",
        quote(run_status),
    )
    manifest["status"] = run_status
    refresh_runtime_documents(
        run_dir,
        manifest,
        states,
        status_override=run_status,
    )
    return states, []
