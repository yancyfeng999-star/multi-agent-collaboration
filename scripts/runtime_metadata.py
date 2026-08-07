#!/usr/bin/env python3
"""Safe, deterministic runtime metadata detection.

The detector consumes only explicit structured inputs and a fixed environment
allowlist.  It never enumerates the process environment and never returns raw
rejected/conflicting session identifiers.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from project_memory_lib import find_high_confidence_secrets

SCHEMA_VERSION = "runtime-detection/v1"
PLATFORMS = ("hermes", "codex", "claude-code", "other")
FIELDS = ("platform", "session_id", "profile", "workspace")
STRUCTURED_FIELDS = frozenset(FIELDS)
ENVIRONMENT_ALLOWLIST: tuple[tuple[str, str, str], ...] = (
    ("HERMES_SESSION_ID", "hermes", "session_id"),
    ("HERMES_PROFILE", "hermes", "profile"),
    ("HERMES_PROJECT_ROOT", "hermes", "workspace"),
    ("HERMES_WORKSPACE", "hermes", "workspace"),
    ("CODEX_SESSION_ID", "codex", "session_id"),
    ("CODEX_PROFILE", "codex", "profile"),
    ("CODEX_PROJECT_ROOT", "codex", "workspace"),
    ("CODEX_WORKSPACE", "codex", "workspace"),
    ("CLAUDE_CODE_SESSION_ID", "claude-code", "session_id"),
    ("CLAUDE_CODE_PROFILE", "claude-code", "profile"),
    ("CLAUDE_CODE_PROJECT_ROOT", "claude-code", "workspace"),
    ("CLAUDE_CODE_WORKSPACE", "claude-code", "workspace"),
    ("PWD", "", "workspace"),
)
SOURCE_SCORE = {
    "explicit": 100,
    "trusted_context": 95,
    "session_map": 90,
    "allowlisted_env": 65,
    "default": 30,
    "cwd": 30,
}
SESSION_RE = re.compile(r"^[A-Za-z0-9._:@/-]{1,256}$")
PROFILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
JWT_RE = re.compile(r"^eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}$")


class DetectionRejected(ValueError):
    """A fail-closed detection error whose message never includes input values."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class _Candidate:
    field: str
    value: str
    source: str
    evidence_name: str
    platform: str | None
    score: int

    def summary(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source": self.source,
            "evidence_name": self.evidence_name,
            "score": self.score,
        }
        if self.platform:
            result["platform"] = self.platform
        return result


def _platform(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DetectionRejected("SOURCE_SCHEMA_INVALID")
    normalized = value.strip().lower().replace("_", "-")
    if normalized == "claude":
        normalized = "claude-code"
    return normalized if normalized in PLATFORMS else "other"


def _safe_scalar(field: str, value: Any, project_root: Path, allowed_roots: tuple[Path, ...]) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise DetectionRejected("SENSITIVE_INPUT_REJECTED")
    if find_high_confidence_secrets(value):
        raise DetectionRejected("SENSITIVE_INPUT_REJECTED")
    if field == "platform":
        return _platform(value)
    if field == "session_id":
        if not SESSION_RE.fullmatch(value) or JWT_RE.fullmatch(value) or ("=" in value and len(value) > 32):
            raise DetectionRejected("SENSITIVE_INPUT_REJECTED")
        return value
    if field == "profile":
        if not PROFILE_RE.fullmatch(value):
            raise DetectionRejected("SENSITIVE_INPUT_REJECTED")
        return value
    if field == "workspace":
        if any(mark in value for mark in ("?", "#")) or "://" in value:
            raise DetectionRejected("SENSITIVE_INPUT_REJECTED")
        path = Path(value).expanduser().resolve()
        if not path.is_dir():
            raise DetectionRejected("WORKSPACE_NOT_DIRECTORY")
        if not any(path == root or path.is_relative_to(root) for root in allowed_roots):
            raise DetectionRejected("WORKSPACE_OUTSIDE_ALLOWED_ROOTS")
        return str(path)
    raise DetectionRejected("SOURCE_SCHEMA_INVALID")


def _validate_structured(value: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping) or set(value) - STRUCTURED_FIELDS:
        raise DetectionRejected("SOURCE_SCHEMA_INVALID")
    return value


def detect_runtime_metadata(
    *,
    project_root: str | Path,
    agent_id: str | None = None,
    explicit: Mapping[str, Any] | None = None,
    trusted_context: Mapping[str, Any] | None = None,
    session_map: Mapping[str, Any] | None = None,
    declared_defaults: Mapping[str, Any] | None = None,
    allowed_roots: tuple[str | Path, ...] | list[str | Path] | None = None,
    environ: Any,
    cwd: str | Path | None = None,
    required_fields: tuple[str, ...] = FIELDS,
) -> dict[str, Any]:
    """Detect non-secret runtime identity from narrowly scoped evidence.

    ``environ`` is deliberately required: production callers may pass
    ``os.environ`` while tests can pass a get-only object.  Only keys in
    ``ENVIRONMENT_ALLOWLIST`` are accessed with ``get``.
    """
    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise DetectionRejected("PROJECT_ROOT_INVALID")
    roots = tuple(Path(item).expanduser().resolve() for item in (allowed_roots or (root,)))
    if any(item == Path(item.anchor) or not item.is_dir() for item in roots):
        raise DetectionRejected("ALLOWED_ROOT_INVALID")
    explicit = _validate_structured(explicit)
    trusted_context = _validate_structured(trusted_context)
    defaults = _validate_structured(declared_defaults)
    if any(field not in FIELDS for field in required_fields):
        raise DetectionRejected("SOURCE_SCHEMA_INVALID")

    candidates: dict[str, list[_Candidate]] = {field: [] for field in FIELDS}

    def add(field: str, raw: Any, source: str, evidence: str, platform: str | None = None, score: int | None = None) -> None:
        if raw is None:
            return
        value = _safe_scalar(field, raw, root, roots)
        provenance = value if field == "platform" else platform
        candidates[field].append(_Candidate(field, value, source, evidence, provenance, score if score is not None else SOURCE_SCORE[source]))

    # Highest-priority, caller-provided evidence first.
    explicit_platform = _platform(explicit["platform"]) if "platform" in explicit else None
    trusted_platform = _platform(trusted_context["platform"]) if "platform" in trusted_context else None
    for field in FIELDS:
        add(field, explicit.get(field), "explicit", f"explicit.{field}", explicit_platform)
    for field in FIELDS:
        add(field, trusted_context.get(field), "trusted_context", f"trusted_context.{field}", trusted_platform)

    map_active: Mapping[str, Any] = {}
    map_agent_mismatch = False
    if session_map is not None:
        if not isinstance(session_map, Mapping) or set(session_map) - {"schema_version", "agent_id", "active", "history"}:
            raise DetectionRejected("SOURCE_SCHEMA_INVALID")
        active = session_map.get("active")
        if active is not None:
            if not isinstance(active, Mapping) or set(active) - {"platform", "session_id", "profile", "workspace", "started_at", "last_synced_at", "last_archive", "last_synced_message_id", "runtime_profile_id", "runtime_profile_hash"}:
                raise DetectionRejected("SOURCE_SCHEMA_INVALID")
            map_active = active
        map_agent_mismatch = bool(agent_id and session_map.get("agent_id") != agent_id)
    map_platform = _platform(map_active["platform"]) if "platform" in map_active else None
    if not map_agent_mismatch:
        for field in FIELDS:
            add(field, map_active.get(field), "session_map", f"active.{field}", map_platform)

    # Fixed get-only environment access. Session IDs are stronger than other env fields.
    for key, platform, field in ENVIRONMENT_ALLOWLIST:
        raw = environ.get(key)
        if raw is not None:
            add(field, raw, "allowlisted_env", key, platform or None, 75 if field == "session_id" else (45 if key == "PWD" else 65))

    default_platform = _platform(defaults["platform"]) if "platform" in defaults else None
    for field in FIELDS:
        source = "default"
        add(field, defaults.get(field), source, f"declared.{field}", default_platform)
    if not defaults.get("profile"):
        add("profile", "default", "default", "safe_default.profile", default_platform)
    add("workspace", str(Path(cwd).resolve() if cwd is not None else root), "cwd", "cwd", default_platform)

    conflicts: list[dict[str, Any]] = []
    if map_agent_mismatch:
        conflicts.append({"code": "AGENT_ID_MISMATCH", "field": "agent_id", "candidates": [], "resolution": "explicit_confirmation_required"})

    # Multiple platform-specific session signals are ambiguous regardless of stable ordering.
    env_platform_signals: dict[str, _Candidate] = {}
    for candidate in candidates["session_id"]:
        if candidate.source == "allowlisted_env" and candidate.platform:
            env_platform_signals.setdefault(candidate.platform, candidate)
    if len(env_platform_signals) > 1:
        ordered = [env_platform_signals[name] for name in PLATFORMS if name in env_platform_signals]
        conflicts.append({"code": "MULTI_PLATFORM_STRONG_SIGNAL", "field": "platform", "candidates": [item.summary() for item in ordered], "resolution": "explicit_confirmation_required"})

    # Explicit parameters may not silently replace an active binding.
    for field in FIELDS:
        high = next((item for item in candidates[field] if item.source == "explicit"), None)
        active = next((item for item in candidates[field] if item.source == "session_map"), None)
        if high and active and high.value != active.value:
            code = "EXPLICIT_CONTRADICTS_ACTIVE_BINDING"
            conflicts.append({"code": code, "field": field, "candidates": [high.summary(), active.summary()], "resolution": "explicit_confirmation_required"})

    # Strong actual observations with different values remain conflicts. Lower
    # defaults/env hints never manufacture a conflict with stronger evidence.
    conflict_codes = {
        "platform": "MULTI_PLATFORM_STRONG_SIGNAL",
        "session_id": "SESSION_ID_MISMATCH",
        "profile": "PROFILE_MISMATCH",
        "workspace": "WORKSPACE_MISMATCH",
    }
    for field in FIELDS:
        strong = [item for item in candidates[field] if item.source in {"explicit", "trusted_context", "session_map"}]
        distinct: list[_Candidate] = []
        for item in strong:
            if all(item.value != seen.value for seen in distinct):
                distinct.append(item)
        if len(distinct) > 1 and not any(item["field"] == field for item in conflicts):
            conflicts.append({
                "code": conflict_codes[field], "field": field,
                "candidates": [item.summary() for item in distinct],
                "resolution": "explicit_confirmation_required",
            })

    conflicted_fields = {item["field"] for item in conflicts}
    selected: dict[str, _Candidate | None] = {}
    chosen_platform: str | None = None
    if "platform" not in conflicted_fields:
        platform_candidates = candidates["platform"]
        if not platform_candidates and len(env_platform_signals) == 1:
            signal = next(iter(env_platform_signals.values()))
            platform_candidates = [_Candidate("platform", signal.platform or "other", signal.source, signal.evidence_name, signal.platform, signal.score)]
        selected["platform"] = platform_candidates[0] if platform_candidates else None
        chosen_platform = selected["platform"].value if selected["platform"] else None
    else:
        selected["platform"] = None

    for field in ("session_id", "profile", "workspace"):
        if field in conflicted_fields:
            selected[field] = None
            continue
        eligible = [item for item in candidates[field] if item.platform in (None, chosen_platform)]
        selected[field] = eligible[0] if eligible else None

    confidence = 0.0
    required_selected = [selected.get(field) for field in required_fields]
    if not conflicts and all(required_selected):
        confidence = min(item.score for item in required_selected if item is not None) / 100
    status = "ambiguous" if conflicts else ("resolved" if confidence >= .9 else "insufficient")
    result = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        **{field: item.value if (item := selected.get(field)) is not None else None for field in FIELDS},
        "source": {field: item.source if (item := selected.get(field)) is not None else None for field in FIELDS},
        "confidence": confidence,
        "conflicts": conflicts,
        "warnings": [] if status == "resolved" else ["explicit_confirmation_required" if status == "ambiguous" else "insufficient_evidence"],
        "security": {"allowlist_version": SCHEMA_VERSION, "secret_scan": "passed", "environment_snapshot_taken": False},
    }
    # Last-line safety gate over the exact public result.
    if find_high_confidence_secrets(json.dumps(result, ensure_ascii=False, sort_keys=True)):
        raise DetectionRejected("SENSITIVE_OUTPUT_REJECTED")
    return result


__all__ = ["DetectionRejected", "ENVIRONMENT_ALLOWLIST", "detect_runtime_metadata"]
