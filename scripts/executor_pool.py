"""Allocate short-lived Run-local execution instances without adding Agent roles."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

from project_memory_lib import exclusive_lock
from protocol_lib import atomic_write, parse_agent_profiles, path_within, quote, scalar_map


RUNTIMES = {"codex_thread", "hermes", "document"}
WORKTREE_POLICIES = {"isolated_writer", "shared_read_only", "shared_no_git_mutation"}


def _now() -> datetime:
    return datetime.now().astimezone()


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _json_value(value: object, default: object) -> object:
    if value in {None, "", "null"}:
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return default


def _slug(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return result or "value"


def _time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _active_bindings(run_dir: Path, now: datetime) -> list[dict[str, str]]:
    active: list[dict[str, str]] = []
    directory = run_dir / "executors"
    if not directory.is_dir():
        return active
    released: set[str] = set()
    release_dir = directory / "releases"
    if release_dir.is_dir():
        for release_path in release_dir.glob("*.yaml"):
            try:
                release = scalar_map(release_path.read_text(encoding="utf-8"), source=str(release_path))
            except (OSError, ValueError):
                continue
            if release.get("status") in {"released", "expired", "failed"} and release.get("executor_id"):
                released.add(release["executor_id"])
    for path in sorted(directory.glob("EXEC-*.yaml")):
        try:
            values = scalar_map(path.read_text(encoding="utf-8"), source=str(path))
        except (OSError, ValueError):
            continue
        expires = _time(values.get("lease_expires_at", ""))
        if values.get("status") == "active" and values.get("executor_id") not in released and expires is not None and expires > now:
            active.append(values)
    return active


def _project_root(run_dir: Path) -> Path:
    project_file = run_dir.parent.parent / "project.yaml"
    values = scalar_map(project_file.read_text(encoding="utf-8"), source=str(project_file))
    return Path(values["project_root"]).expanduser().resolve()


def _manifest(run_dir: Path) -> dict[str, str]:
    return scalar_map((run_dir / "manifest.yaml").read_text(encoding="utf-8"), source="manifest")


def _binding_id(run_id: str, task_id: str, principal_agent_id: str, attempt_id: str) -> str:
    digest = hashlib.sha256(
        f"{run_id}\0{task_id}\0{principal_agent_id}\0{attempt_id}".encode("utf-8")
    ).hexdigest()[:16]
    return f"EXEC-{_slug(principal_agent_id)}-{_slug(task_id)}-{digest}"


def _render_binding(values: dict[str, Any]) -> str:
    capabilities = json.dumps(values["required_capabilities"], ensure_ascii=False)
    return "\n".join(
        (
            'schema_version: "1.0"',
            f"executor_id: {quote(values['executor_id'])}",
            f"principal_agent_id: {quote(values['principal_agent_id'])}",
            f"role_ref: {quote(values['role_ref'])}",
            f"task_id: {quote(values['task_id'])}",
            f"attempt_id: {quote(values['attempt_id'])}",
            f"required_capabilities: {capabilities}",
            f"runtime: {quote(values['runtime'])}",
            f"session_id: {quote(values['session_id']) if values.get('session_id') else 'null'}",
            f"thread_id: {quote(values['thread_id']) if values.get('thread_id') else 'null'}",
            f"workspace: {quote(values['workspace'])}",
            f"worktree_policy: {quote(values['worktree_policy'])}",
            f"lease_acquired_at: {quote(values['lease_acquired_at'])}",
            f"lease_expires_at: {quote(values['lease_expires_at'])}",
            f"status: {quote(values['status'])}",
            "",
        )
    )


def _existing_for_task(active: Iterable[dict[str, str]], task_id: str) -> dict[str, str] | None:
    return next((binding for binding in active if binding.get("task_id") == task_id), None)


def _workspace_conflict(
    active: Iterable[dict[str, str]],
    workspace: Path,
    worktree_policy: str,
) -> str | None:
    """Return the active executor that makes this workspace unsafe to share."""

    for binding in active:
        if Path(binding.get("workspace", "")).expanduser().resolve() != workspace:
            continue
        if "isolated_writer" in {worktree_policy, binding.get("worktree_policy", "")}:
            return binding.get("executor_id") or "unknown"
    return None


def allocate_executor(
    run_dir_value: str | Path,
    *,
    task_id: str,
    principal_agent_id: str,
    role_ref: str,
    required_capabilities: list[str] | None = None,
    runtime: str,
    workspace: str | Path,
    worktree_policy: str = "isolated_writer",
    attempt_id: str = "ATTEMPT-001",
    session_id: str | None = None,
    thread_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Allocate or reuse one active executor for a single task attempt."""

    run_dir = Path(run_dir_value).expanduser().resolve()
    manifest = _manifest(run_dir)
    agents = parse_agent_profiles((run_dir / "agents.yaml").read_text(encoding="utf-8"), source="agents")
    if principal_agent_id not in agents:
        raise ValueError(f"principal Agent is not registered: {principal_agent_id}")
    if runtime not in RUNTIMES:
        raise ValueError(f"unsupported executor runtime: {runtime}")
    if worktree_policy not in WORKTREE_POLICIES:
        raise ValueError(f"unsupported worktree policy: {worktree_policy}")
    required = sorted({str(item) for item in (required_capabilities or [])})
    available = {str(item) for item in agents[principal_agent_id].get("capabilities", [])}
    if required and not set(required).issubset(available):
        missing = sorted(set(required) - available)
        raise ValueError(f"principal Agent lacks required capabilities: {','.join(missing)}")
    project_root = _project_root(run_dir)
    workspace_path = Path(workspace).expanduser().resolve()
    if not path_within(workspace_path, [str(project_root)], project_root):
        raise ValueError("executor workspace must be inside the project root")
    if not workspace_path.is_dir():
        raise ValueError(f"executor workspace does not exist: {workspace_path}")
    now = _now()
    expires = now + timedelta(seconds=int(manifest.get("lease_seconds", "1800")))
    executor_id = _binding_id(manifest["run_id"], task_id, principal_agent_id, attempt_id)
    binding_path = run_dir / "executors" / f"{executor_id}.yaml"
    lock_path = run_dir / "locks" / ".executor-pool.lock"
    if not dry_run:
        expire_stale_executors(run_dir, now=now)
    active = _active_bindings(run_dir, now)
    existing = _existing_for_task(active, task_id)
    if existing:
        if existing.get("principal_agent_id") != principal_agent_id:
            raise ValueError("task already has an executor for another principal Agent")
        return {**existing, "binding_path": str(binding_path), "reused": True, "dry_run": dry_run}
    workspace_conflict = _workspace_conflict(active, workspace_path, worktree_policy)
    if workspace_conflict:
        raise ValueError(f"workspace is already bound by active executor: {workspace_conflict}")
    if not _as_bool(manifest.get("executor_scale_authorized", "false")) and runtime in {"codex_thread", "hermes"}:
        raise ValueError("executor scale-up authorization is required for a new native executor")
    max_parallel = int(manifest.get("max_parallel", "1"))
    if len(active) >= max_parallel:
        raise ValueError("executor allocation would exceed max_parallel")
    role_limits = _json_value(manifest.get("max_instances_per_role"), {})
    if not isinstance(role_limits, dict):
        role_limits = {}
    role_limit = int(role_limits.get(role_ref, max_parallel))
    role_count = sum(binding.get("role_ref") == role_ref for binding in active)
    if role_count >= role_limit:
        raise ValueError(f"executor allocation would exceed max_instances_per_role for {role_ref}")
    values: dict[str, Any] = {
        "executor_id": executor_id,
        "principal_agent_id": principal_agent_id,
        "role_ref": role_ref,
        "task_id": task_id,
        "attempt_id": attempt_id,
        "required_capabilities": required,
        "runtime": runtime,
        "session_id": session_id,
        "thread_id": thread_id,
        "workspace": str(workspace_path),
        "worktree_policy": worktree_policy,
        "lease_acquired_at": now.isoformat(timespec="seconds"),
        "lease_expires_at": expires.isoformat(timespec="seconds"),
        "status": "active",
    }
    if dry_run:
        return {**values, "binding_path": str(binding_path), "reused": False, "dry_run": True}
    with exclusive_lock(lock_path):
        active = _active_bindings(run_dir, _now())
        existing = _existing_for_task(active, task_id)
        if existing:
            return {**existing, "binding_path": str(run_dir / "executors" / f"{existing['executor_id']}.yaml"), "reused": True, "dry_run": False}
        workspace_conflict = _workspace_conflict(active, workspace_path, worktree_policy)
        if workspace_conflict:
            raise ValueError(f"workspace is already bound by active executor: {workspace_conflict}")
        if len(active) >= max_parallel:
            raise ValueError("executor allocation would exceed max_parallel")
        role_count = sum(binding.get("role_ref") == role_ref for binding in active)
        if role_count >= role_limit:
            raise ValueError(f"executor allocation would exceed max_instances_per_role for {role_ref}")
        if not _as_bool(manifest.get("executor_scale_authorized", "false")) and runtime in {"codex_thread", "hermes"}:
            raise ValueError("executor scale-up authorization is required for a new native executor")
        if binding_path.exists():
            raise ValueError(f"executor binding collision: {binding_path}")
        atomic_write(binding_path, _render_binding(values))
    return {**values, "binding_path": str(binding_path), "reused": False, "dry_run": False}


def load_executor_binding(run_dir_value: str | Path, executor_id: str) -> dict[str, str]:
    run_dir = Path(run_dir_value).expanduser().resolve()
    path = run_dir / "executors" / f"{executor_id}.yaml"
    if not path.is_file():
        raise ValueError(f"executor binding does not exist: {executor_id}")
    return scalar_map(path.read_text(encoding="utf-8"), source=str(path))


def release_executor(
    run_dir_value: str | Path,
    executor_id: str,
    released_by: str,
    reason: str,
    *,
    status: str = "released",
    dry_run: bool = False,
) -> dict[str, object]:
    """Append an immutable release/expiry record for one active executor binding."""

    if not released_by.strip():
        raise ValueError("released_by must not be empty")
    if not reason.strip():
        raise ValueError("release reason must not be empty")
    if status not in {"released", "expired", "failed"}:
        raise ValueError("executor lifecycle status must be released, expired, or failed")
    run_dir = Path(run_dir_value).expanduser().resolve()
    binding_path = run_dir / "executors" / f"{executor_id}.yaml"
    binding = load_executor_binding(run_dir, executor_id)
    if binding.get("executor_id") != executor_id:
        raise ValueError("executor binding identity mismatch")
    if binding.get("status") != "active":
        raise ValueError("only active executor bindings can be released")
    release_path = run_dir / "executors" / "releases" / f"{executor_id}.yaml"
    binding_sha256 = hashlib.sha256(binding_path.read_bytes()).hexdigest()

    def existing_release() -> dict[str, object] | None:
        if not release_path.is_file():
            return None
        try:
            existing = scalar_map(
                release_path.read_text(encoding="utf-8"),
                source=str(release_path),
            )
        except (OSError, ValueError):
            raise ValueError(f"executor release is malformed: {release_path}")
        if (
            existing.get("status") not in {"released", "expired", "failed"}
            or existing.get("executor_id") != executor_id
            or existing.get("binding_sha256") != binding_sha256
        ):
            raise ValueError(f"executor release conflicts with immutable binding: {release_path}")
        return {
            "ready": True,
            "executor_id": executor_id,
            "binding_path": str(binding_path),
            "release_path": str(release_path),
            "released_by": existing.get("released_by", released_by),
            "reason": existing.get("reason", reason.strip()),
            "status": existing.get("status", status),
            "already_released": True,
            "dry_run": dry_run,
        }

    already_released = existing_release()
    if already_released is not None:
        return already_released
    expires = _time(binding.get("lease_expires_at", ""))
    if status == "released" and expires is not None and expires <= _now():
        raise ValueError("executor lease expired; append an expiry record instead")
    if status == "expired" and (expires is None or expires > _now()):
        raise ValueError("executor lease has not expired")
    released_at = _now().isoformat(timespec="seconds")
    kind = "executor_expiry" if status == "expired" else "executor_release"
    content = "\n".join(
        (
            'schema_version: "1.0"',
            f"kind: {quote(kind)}",
            f"run_id: {quote(_manifest(run_dir).get('run_id', ''))}",
            f"executor_id: {quote(executor_id)}",
            f"binding_ref: {quote(str(binding_path))}",
            f"binding_sha256: {quote(binding_sha256)}",
            f"released_by: {quote(released_by)}",
            f"released_at: {quote(released_at)}",
            f"reason: {quote(reason.strip())}",
            f"status: {quote(status)}",
            "",
        )
    )
    if not dry_run:
        with exclusive_lock(run_dir / "locks" / ".executor-pool.lock"):
            already_released = existing_release()
            if already_released is not None:
                return already_released
            release_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write(release_path, content)
    return {
        "ready": True,
        "executor_id": executor_id,
        "binding_path": str(binding_path),
        "release_path": str(release_path),
        "released_by": released_by,
        "reason": reason.strip(),
        "status": status,
        "already_released": False,
        "dry_run": dry_run,
    }


def expire_stale_executors(
    run_dir_value: str | Path,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> list[dict[str, object]]:
    """Append immutable expiry records for active bindings whose lease elapsed."""

    run_dir = Path(run_dir_value).expanduser().resolve()
    moment = now or _now()
    expired: list[dict[str, object]] = []
    directory = run_dir / "executors"
    if not directory.is_dir():
        return expired
    for binding_path in sorted(directory.glob("EXEC-*.yaml")):
        try:
            binding = scalar_map(binding_path.read_text(encoding="utf-8"), source=str(binding_path))
        except (OSError, ValueError):
            continue
        expires = _time(binding.get("lease_expires_at", ""))
        if binding.get("status") != "active" or expires is None or expires > moment:
            continue
        try:
            expired.append(
                release_executor(
                    run_dir,
                    binding["executor_id"],
                    binding.get("principal_agent_id", "coordinator"),
                    "executor lease expired",
                    status="expired",
                    dry_run=dry_run,
                )
            )
        except ValueError:
            # A concurrent lifecycle writer may have already published the
            # immutable record; the next scan will observe it as inactive.
            continue
    return expired


def validate_binding(binding: dict[str, Any], project_root: str | Path) -> list[str]:
    """Return deterministic structural errors for one executor binding."""

    errors: list[str] = []
    required = (
        "executor_id",
        "principal_agent_id",
        "role_ref",
        "task_id",
        "attempt_id",
        "runtime",
        "workspace",
        "worktree_policy",
        "lease_acquired_at",
        "lease_expires_at",
        "status",
    )
    errors.extend(f"missing:{field}" for field in required if not binding.get(field))
    if binding.get("runtime") not in RUNTIMES:
        errors.append("invalid:runtime")
    if binding.get("worktree_policy") not in WORKTREE_POLICIES:
        errors.append("invalid:worktree_policy")
    if binding.get("status") not in {"active", "released", "expired", "failed"}:
        errors.append("invalid:status")
    acquired = _time(str(binding.get("lease_acquired_at", "")))
    expires = _time(str(binding.get("lease_expires_at", "")))
    if acquired is None or expires is None or expires <= acquired:
        errors.append("invalid:lease_interval")
    root = Path(project_root).expanduser().resolve()
    workspace = binding.get("workspace")
    if workspace and not path_within(str(workspace), [str(root)], root):
        errors.append("workspace_outside_project")
    return sorted(set(errors))
