#!/usr/bin/env python3
"""Small, decision-oriented coordination message contracts."""

from __future__ import annotations

from typing import Any, Iterable

from protocol_lib import ProtocolError


MESSAGE_KINDS = {"STARTED", "BLOCKED", "CANDIDATE_READY", "INTEGRATED"}
COMMON_OPTIONAL = {"message_id", "created_at", "evidence_ref"}


def _string(value: Any, *, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ProtocolError(f"coordination message {field} must be a non-empty string")
    return value


def _strings(value: Any, *, field: str, required: bool = True) -> list[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ProtocolError(f"coordination message {field} must be a list of non-empty strings")
    if required and not value:
        raise ProtocolError(f"coordination message {field} cannot be empty")
    return list(value)


def validate_message(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolError("coordination message must be an object")
    kind = _string(value.get("kind"), field="kind")
    if kind not in MESSAGE_KINDS:
        raise ProtocolError(f"unsupported coordination message kind: {kind}")
    required_by_kind = {
        "STARTED": {"task_id", "owner", "paths", "baseline"},
        "BLOCKED": {"task_id", "blocker_code", "observed", "scope_impact", "safe_default", "recommended_disposition"},
        "CANDIDATE_READY": {"candidate_id", "commit", "paths", "checks"},
        "INTEGRATED": {"main_hash", "candidate_status", "remaining_work"},
    }
    missing = required_by_kind[kind] - value.keys()
    if missing:
        raise ProtocolError(f"{kind} message is missing fields: {', '.join(sorted(missing))}")
    allowed = required_by_kind[kind] | COMMON_OPTIONAL | {"kind"}
    unknown = set(value) - allowed
    if unknown:
        raise ProtocolError(f"{kind} message has unknown fields: {', '.join(sorted(unknown))}")
    result: dict[str, Any] = {"kind": kind}
    if kind == "STARTED":
        result.update(
            task_id=_string(value["task_id"], field="task_id"),
            owner=_string(value["owner"], field="owner"),
            paths=_strings(value["paths"], field="paths"),
            baseline=_string(value["baseline"], field="baseline"),
        )
    elif kind == "BLOCKED":
        result.update(
            task_id=_string(value["task_id"], field="task_id"),
            blocker_code=_string(value["blocker_code"], field="blocker_code"),
            observed=_string(value["observed"], field="observed"),
            scope_impact=_strings(value["scope_impact"], field="scope_impact"),
            safe_default=_string(value["safe_default"], field="safe_default"),
            recommended_disposition=_string(value["recommended_disposition"], field="recommended_disposition"),
        )
    elif kind == "CANDIDATE_READY":
        result.update(
            candidate_id=_string(value["candidate_id"], field="candidate_id"),
            commit=_string(value["commit"], field="commit"),
            paths=_strings(value["paths"], field="paths"),
            checks=_strings(value["checks"], field="checks"),
        )
    else:
        result.update(
            main_hash=_string(value["main_hash"], field="main_hash"),
            candidate_status=_string(value["candidate_status"], field="candidate_status"),
            remaining_work=_strings(value["remaining_work"], field="remaining_work", required=False),
        )
    for field in COMMON_OPTIONAL:
        if field in value:
            result[field] = _string(value[field], field=field)
    return result


def _subject(message: dict[str, Any]) -> str:
    return str(message.get("task_id") or message.get("candidate_id") or message.get("main_hash"))


def compact_messages(messages: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop ordinary progress and deduplicate actionable messages in order."""

    result: list[dict[str, Any]] = []
    positions: dict[tuple[str, str], int] = {}
    for value in messages:
        if not isinstance(value, dict) or value.get("kind") not in MESSAGE_KINDS:
            continue
        message = validate_message(value)
        key = (message["kind"], _subject(message))
        if key in positions:
            result[positions[key]] = message
        else:
            positions[key] = len(result)
            result.append(message)
    return result
