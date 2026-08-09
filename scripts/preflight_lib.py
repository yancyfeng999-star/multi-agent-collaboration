"""Read-only mode-aware preflight helpers for Protocol v3 runs.

The preflight layer intentionally reports all actionable gaps in one pass.  It
never emits events, wakes agents, changes task state, or grants release
permission.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from protocol_lib import (
    PROTOCOL_VERSION,
    ProtocolError,
    event_records,
    frontmatter,
    json_string_list,
    parse_agent_profiles,
    paths_overlap,
    replay_task_states,
    scalar_map,
    sha256,
    valid_iso8601,
)


GOVERNANCE = {"light", "standard", "strict"}
EXECUTION_PROFILES = {"fast", "normal"}
DISPATCH_POLICIES = {"central", "hybrid", "self_service"}
CAPABILITIES = {"task_publish", "task_claim", "thread_claim"}


def default_dispatch_policy(governance: str) -> str:
    return "central" if governance == "strict" else "hybrid"


def validate_execution_profile(governance: str, profile: str) -> None:
    if governance not in GOVERNANCE:
        raise ValueError(f"invalid governance: {governance}")
    if profile not in EXECUTION_PROFILES:
        raise ValueError(f"invalid execution profile: {profile}")
    if governance == "strict" and profile == "fast":
        raise ValueError("strict governance cannot use the fast execution profile")


def resolve_dispatch_policy(governance: str, policy: str | None) -> str:
    if policy in {None, "", "auto"}:
        return default_dispatch_policy(governance)
    if policy not in DISPATCH_POLICIES:
        raise ValueError(f"invalid dispatch policy: {policy}")
    if governance == "strict" and policy != "central":
        raise ValueError("strict governance requires central dispatch policy")
    return policy


def _json_list(value: str | None, *, field: str, source: str) -> list[str]:
    if value in {None, "", "null"}:
        return []
    return json_string_list(value, field=field, source=source)


def _project_context(run_dir: Path) -> tuple[Path, list[str]]:
    project_path = run_dir.parent.parent / "project.yaml"
    project = scalar_map(project_path.read_text(encoding="utf-8"), source=str(project_path))
    allowed = _json_list(project.get("allowed_roots"), field="allowed_roots", source=str(project_path))
    return Path(project["project_root"]).expanduser().resolve(), allowed


def _active_locks(run_dir: Path, now: datetime) -> list[dict[str, str]]:
    locks: list[dict[str, str]] = []
    for path in sorted((run_dir / "locks").glob("*.yaml")):
        try:
            values = scalar_map(path.read_text(encoding="utf-8"), source=str(path))
        except ProtocolError:
            locks.append({"lock_id": path.stem, "resource": "malformed", "source": str(path)})
            continue
        expires = values.get("lease_expires_at", "")
        if not valid_iso8601(expires):
            locks.append({**values, "source": str(path)})
            continue
        if datetime.fromisoformat(expires.replace("Z", "+00:00")) > now:
            locks.append({**values, "source": str(path)})
    return locks


def resource_step_status(
    run_dir: str | Path,
    task: dict[str, str],
    active_locks: list[dict[str, str]],
    claims: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Report resource-free/resource-required steps without granting a lock."""

    raw_steps = task.get("resource_steps", "[]")
    try:
        steps = json.loads(raw_steps)
    except json.JSONDecodeError:
        return [{"step_id": "invalid", "required": True, "ready": False, "reason": "resource_steps_invalid"}]
    if not isinstance(steps, list):
        return [{"step_id": "invalid", "required": True, "ready": False, "reason": "resource_steps_invalid"}]
    run_dir = Path(run_dir).expanduser().resolve()
    queue_dir = run_dir / "locks" / "queue"
    grant_dir = queue_dir / "grants"
    granted: set[str] = set()
    for grant_path in sorted(grant_dir.glob("*.yaml")) if grant_dir.is_dir() else []:
        try:
            grant = scalar_map(grant_path.read_text(encoding="utf-8"), source=str(grant_path))
        except (OSError, ProtocolError):
            continue
        if grant.get("status") == "granted" and grant.get("request_id"):
            granted.add(grant["request_id"])
    queued: list[dict[str, str]] = []
    for path in sorted(queue_dir.glob("*.yaml")) if queue_dir.is_dir() else []:
        try:
            values = scalar_map(path.read_text(encoding="utf-8"), source=str(path))
        except (OSError, ProtocolError):
            continue
        if values.get("status") == "queued" and values.get("request_id") not in granted:
            queued.append(values)
    queued.sort(key=lambda value: (value.get("created_at", ""), value.get("request_id", "")))
    statuses: list[dict[str, Any]] = []
    for step in steps:
        if not isinstance(step, dict):
            statuses.append({"step_id": "invalid", "required": True, "ready": False, "reason": "resource_step_not_object"})
            continue
        step_id = str(step.get("step_id", ""))
        resources = [str(item) for item in step.get("resources", [])] if isinstance(step.get("resources", []), list) else []
        required = bool(step.get("required", True))
        owner_task = task.get("task_id", "")
        queue_key = str(step.get("queue_key", "null"))
        held = [
            lock.get("resource", "")
            for lock in active_locks
            if lock.get("owner_task") == owner_task
            and (
                lock.get("step_id") == step_id
                or (queue_key not in {"", "null"} and lock.get("queue_key") == queue_key)
            )
        ]
        ready = not required or all(resource in held for resource in resources)
        position = None
        if not ready and queue_key not in {"", "null"}:
            waiting = [item for item in queued if item.get("queue_key") == queue_key]
            # `resource_steps.step_id` identifies the declared bundle.  A
            # queue request may use a more granular execution step id (for
            # example, two retries or sub-steps can request the same bundle),
            # so queue ownership is keyed by task + queue_key.  Prefer an
            # exact step match when present, otherwise use the first request
            # for the task and queue.  This keeps FIFO visible without
            # allowing another task to inherit the grant.
            owned_waiting = [item for item in waiting if item.get("task_id") == owner_task]
            exact_waiting = [item for item in owned_waiting if item.get("step_id") == step_id]
            candidates = exact_waiting or owned_waiting
            position = next((index + 1 for index, item in enumerate(waiting) if item in candidates), None)
        statuses.append({
            "step_id": step_id,
            "required": required,
            "resources": resources,
            "held_resources": held,
            "ready": ready,
            "queue_key": queue_key,
            "queue_position": position,
            "next_action": "wait_for_queue_grant" if not ready and position is not None else ("acquire_resource_bundle" if not ready else "continue"),
        })
    return statuses


def _task_documents(run_dir: Path) -> dict[str, tuple[Path, dict[str, str]]]:
    tasks: dict[str, tuple[Path, dict[str, str]]] = {}
    for path in sorted((run_dir / "tasks").glob("*.md")):
        values = frontmatter(path)
        task_id = values.get("task_id")
        if not task_id:
            continue
        tasks[task_id] = (path, values)
    return tasks


def _load_context(run_dir_value: str | Path) -> dict[str, Any]:
    run_dir = Path(run_dir_value).expanduser().resolve()
    manifest_path = run_dir / "manifest.yaml"
    agents_path = run_dir / "agents.yaml"
    if not manifest_path.is_file() or not agents_path.is_file():
        raise ProtocolError(f"invalid protocol v3 run: {run_dir}")
    manifest = scalar_map(manifest_path.read_text(encoding="utf-8"), source=str(manifest_path))
    if manifest.get("protocol_version") != PROTOCOL_VERSION:
        raise ProtocolError(f"manifest protocol_version must be {PROTOCOL_VERSION}")
    agents = parse_agent_profiles(agents_path.read_text(encoding="utf-8"), source=str(agents_path))
    project_root, allowed_roots = _project_context(run_dir)
    records = event_records(run_dir / "events")
    states, state_errors = replay_task_states(records, manifest.get("governance", ""))
    tasks = _task_documents(run_dir)
    return {
        "run_dir": run_dir,
        "manifest": manifest,
        "manifest_path": manifest_path,
        "agents": agents,
        "project_root": project_root,
        "allowed_roots": allowed_roots,
        "records": records,
        "states": states,
        "state_errors": state_errors,
        "tasks": tasks,
    }


def _item(field: str, owner: str, reason: str) -> dict[str, str]:
    return {"field": field, "owner": owner, "reason": reason}


def _report(
    context: dict[str, Any],
    task_ids: list[str],
    *,
    missing: list[dict[str, str]] | None = None,
    conflicts: list[dict[str, str]] | None = None,
    blocked_by: list[dict[str, str]] | None = None,
    next_action: str | None = None,
) -> dict[str, Any]:
    manifest = context["manifest"]
    governance = manifest.get("governance", "")
    profile = manifest.get("execution_profile", "normal")
    policy = resolve_dispatch_policy(governance, manifest.get("dispatch_policy"))
    missing = sorted(missing or [], key=lambda value: (value["field"], value["owner"], value["reason"]))
    conflicts = sorted(conflicts or [], key=lambda value: (value.get("task_id", ""), value.get("reason", "")))
    blocked_by = sorted(blocked_by or [], key=lambda value: (value.get("task_id", ""), value.get("reason", "")))
    if next_action is None:
        next_action = "ready" if not missing and not conflicts and not blocked_by else "resolve_preflight"
    handoffs = 0 if governance == "light" else 1
    return {
        "schema_version": "1.0",
        "run_id": manifest.get("run_id"),
        "task_ids": sorted(task_ids),
        "governance": governance,
        "execution_profile": profile,
        "dispatch_policy": policy,
        "ready": not missing and not conflicts and not blocked_by,
        "missing": missing,
        "conflicts": conflicts,
        "blocked_by": blocked_by,
        "required_actions": [next_action] if next_action != "ready" else [],
        "estimated_handoffs": handoffs,
        "next_action": next_action,
        "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def _git_status(project_root: Path) -> tuple[str, list[str]]:
    result = subprocess.run(
        ["git", "-C", str(project_root), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return "unavailable", []
    paths: list[str] = []
    for line in result.stdout.splitlines():
        path = line[3:] if len(line) > 3 else line
        if path == ".multi-agent-collaboration" or path.startswith(".multi-agent-collaboration/"):
            continue
        paths.append(path)
    return ("clean" if not paths else "dirty"), paths


def _reference_missing(context: dict[str, Any], field: str, owner: str, reason: str, missing: list[dict[str, str]]) -> None:
    manifest = context["manifest"]
    value = manifest.get(field, "")
    if value in {"", "null", None}:
        missing.append(_item(field, owner, reason))
        return
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = context["run_dir"] / path
    if not path.is_file():
        missing.append(_item(field, owner, f"reference does not exist: {field}"))
        return
    expected_hash = manifest.get(f"{field}_sha256", "")
    if expected_hash not in {"", "null"} and expected_hash != sha256(path):
        missing.append(_item(f"{field}_sha256", owner, "reference hash does not match"))


def run_preflight(
    run_dir: str | Path,
    task_ids: list[str] | None = None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now().astimezone()
    try:
        context = _load_context(run_dir)
    except (OSError, ProtocolError, KeyError, ValueError) as exc:
        return {
            "schema_version": "1.0",
            "run_id": None,
            "task_ids": sorted(task_ids or []),
            "governance": None,
            "execution_profile": None,
            "dispatch_policy": None,
            "ready": False,
            "missing": [],
            "conflicts": [],
            "blocked_by": [{"task_id": "run", "reason": str(exc)}],
            "required_actions": ["repair_run_structure"],
            "estimated_handoffs": 0,
            "next_action": "repair_run_structure",
            "checked_at": now.isoformat(timespec="seconds"),
        }

    manifest = context["manifest"]
    governance = manifest.get("governance", "")
    profile = manifest.get("execution_profile", "normal")
    missing: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    blocked: list[dict[str, str]] = []
    queue_waiting = False
    try:
        validate_execution_profile(governance, profile)
        resolve_dispatch_policy(governance, manifest.get("dispatch_policy"))
    except ValueError as exc:
        blocked.append({"task_id": "run", "reason": str(exc)})
    if context["state_errors"]:
        blocked.extend({"task_id": "run", "reason": error} for error in context["state_errors"])

    tasks = context["tasks"]
    selected = sorted(task_ids or tasks.keys())
    if not selected:
        missing.append(_item("tasks", "coordinator", "create and approve the task graph"))

    for task_id in selected:
        if context["states"].get(task_id) in {"completed", "failed", "cancelled", "superseded", "expired", "dead_letter"}:
            # Dispatch preflight is not a completion audit.  Terminal tasks
            # keep their historical resource declarations but must not block
            # the next wave after their locks have been released.
            continue
        pair = tasks.get(task_id)
        if pair is None:
            missing.append(_item(f"task:{task_id}", "coordinator", "frozen task document is missing"))
            continue
        task_path, task = pair
        owner = task.get("owner_agent", "")
        assignment_mode = task.get("assignment_mode", "fixed")
        if assignment_mode == "claimable":
            eligible = _json_list(task.get("eligible_agents"), field="eligible_agents", source=str(task_path))
            if task.get("owner_agent") != "pool" or not eligible:
                missing.append(_item(f"task:{task_id}:claim", "coordinator", "claimable task needs pool owner and eligible_agents"))
        elif owner not in context["agents"]:
            missing.append(_item(f"task:{task_id}:owner", "coordinator", "owner must be registered"))
        if governance in {"standard", "strict"}:
            for role in ("reviewer_agent", "qa_agent"):
                role_agent = task.get(role, "null")
                if role_agent in {"", "null", None} or role_agent not in context["agents"]:
                    missing.append(_item(f"task:{task_id}:{role}", "coordinator", f"{governance} requires a registered {role}"))
            if task.get("owner_agent") in {task.get("reviewer_agent"), task.get("qa_agent")}:
                conflicts.append({"task_id": task_id, "reason": "Owner cannot self-review or self-QA"})
        dependencies = _json_list(task.get("dependencies"), field="dependencies", source=str(task_path))
        for dependency in dependencies:
            state = context["states"].get(dependency)
            if state != "completed":
                blocked.append({"task_id": task_id, "reason": f"dependency_not_completed:{dependency}"})
        for resource_status in resource_step_status(
            context["run_dir"],
            task,
            _active_locks(context["run_dir"], now),
        ):
            if resource_status.get("required") and not resource_status.get("ready"):
                queue_waiting = queue_waiting or resource_status.get("next_action") == "wait_for_queue_grant"
                blocked.append({
                    "task_id": task_id,
                    "reason": f"resource_step_not_ready:{resource_status.get('step_id', 'unknown')}",
                    "queue_position": str(resource_status.get("queue_position") or "unknown"),
                })

    active_states = {"dispatched", "acknowledged", "running", "handoff_ready", "reviewing", "qa_running", "release_ready"}
    active_tasks = [
        (task_id, pair[1])
        for task_id, pair in tasks.items()
        if context["states"].get(task_id) in active_states
    ]
    for task_id in selected:
        pair = tasks.get(task_id)
        if pair is None:
            continue
        owned = _json_list(pair[1].get("owned_paths"), field="owned_paths", source=str(pair[0]))
        for other_id, other in active_tasks:
            if other_id == task_id:
                continue
            other_owned = _json_list(other.get("owned_paths"), field="owned_paths", source=other_id)
            if any(paths_overlap(left, right, context["project_root"]) for left in owned for right in other_owned):
                conflicts.append({"task_id": task_id, "reason": f"active_owned_path_conflict:{other_id}"})

    for lock in _active_locks(context["run_dir"], now):
        resource = lock.get("resource", "")
        if resource == "malformed":
            conflicts.append({"task_id": "run", "reason": f"malformed_lock:{lock.get('source', '')}"})
            continue
        for task_id in selected:
            pair = tasks.get(task_id)
            if pair is None:
                continue
            owned = _json_list(pair[1].get("owned_paths"), field="owned_paths", source=str(pair[0]))
            if not resource.startswith("logical:") and any(paths_overlap(resource, path, context["project_root"]) for path in owned):
                conflicts.append({"task_id": task_id, "reason": f"active_lock:{lock.get('lock_id', 'unknown')}"})

    if manifest.get("versioning_mode") == "tracked":
        contract = Path(manifest.get("version_contract_ref", "")).expanduser()
        if not contract.is_file() or manifest.get("version_contract_ref_sha256") != sha256(contract):
            missing.append(_item("version_contract_ref", "coordinator", "tracked version contract must exist and match its hash"))
    if governance == "strict":
        for field in ("change_id", "registry_ref", "git_branch", "git_status_ref", "environment_impact_ref", "rollback_ref", "security_review_ref"):
            _reference_missing(context, field, "coordinator", "strict dispatch evidence", missing) if field.endswith("_ref") else None
            if field == "change_id" and manifest.get(field) in {"", "null", None}:
                missing.append(_item(field, "coordinator", "strict dispatch requires change id"))
            if field == "git_branch" and manifest.get(field) in {"", "null", None}:
                missing.append(_item(field, "coordinator", "strict dispatch requires an attached branch"))

    scope_ref = manifest.get("scope_freeze_ref", "null")
    if manifest.get("preflight_required", "false") == "true" and selected and scope_ref in {"", "null", None}:
        missing.append(_item("scope_freeze_ref", "coordinator", "freeze the requested paths before dispatch"))
    if scope_ref not in {"", "null", None}:
        scope_path = Path(scope_ref).expanduser()
        if not scope_path.is_absolute():
            scope_path = context["run_dir"] / scope_path
        if not scope_path.is_file() or manifest.get("scope_freeze_ref_sha256") != sha256(scope_path):
            missing.append(_item("scope_freeze_ref", "coordinator", "scope freeze is missing or hash-mismatched"))

    if governance in {"standard", "strict"}:
        status, dirty = _git_status(context["project_root"])
        if status == "unavailable":
            missing.append(_item("git_status", "coordinator", "record a readable Git status"))
        elif governance == "strict" and dirty:
            conflicts.append({"task_id": "run", "reason": f"strict_worktree_dirty:{','.join(dirty)}"})

    return _report(
        context,
        selected,
        missing=missing,
        conflicts=conflicts,
        blocked_by=blocked,
        next_action="wait_for_queue_grant" if queue_waiting else None,
    )


def _latest_result(run_dir: Path, task: dict[str, str]) -> tuple[Path, dict[str, str]] | None:
    owner = task.get("owner_agent", "")
    paths = list((run_dir / "outbox" / owner).glob(f"{task.get('task_id', '')}-result-*.md")) if owner else []
    if not paths:
        paths = list((run_dir / "outbox").glob(f"*/{task.get('task_id', '')}-result-*.md"))
    if not paths:
        return None
    path = sorted(paths)[-1]
    values = frontmatter(path)
    text = path.read_text(encoding="utf-8")
    parts = text.split("---\n", 2)
    values["outcome"] = parts[2].strip() if len(parts) == 3 else ""
    return path, values


def run_completion_preflight(run_dir: str | Path, task_id: str) -> dict[str, Any]:
    try:
        context = _load_context(run_dir)
    except (OSError, ProtocolError, KeyError, ValueError) as exc:
        return {
            "schema_version": "1.0", "run_id": None, "task_ids": [task_id],
            "governance": None, "execution_profile": None, "dispatch_policy": None,
            "ready": False, "missing": [], "conflicts": [],
            "blocked_by": [{"task_id": task_id, "reason": str(exc)}],
            "required_actions": ["repair_run_structure"], "estimated_handoffs": 0,
            "next_action": "repair_run_structure",
            "checked_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
    task_pair = context["tasks"].get(task_id)
    if task_pair is None:
        return _report(context, [task_id], missing=[_item(f"task:{task_id}", "coordinator", "task document is missing")])
    _, task = task_pair
    missing: list[dict[str, str]] = []
    conflicts: list[dict[str, str]] = []
    blocked: list[dict[str, str]] = []
    result_pair = _latest_result(context["run_dir"], task)
    if result_pair is None:
        missing.append(_item("result", task.get("owner_agent", "owner"), "write an immutable owner result"))
    else:
        result_path, result = result_pair
        if result.get("status") != "completed":
            blocked.append({"task_id": task_id, "reason": f"result_status:{result.get('status', 'missing')}"})
        if not result.get("outcome", "").strip():
            missing.append(_item("summary", task.get("owner_agent", "owner"), "result outcome must contain a summary"))
        if context["manifest"].get("governance") in {"standard", "strict"}:
            if result.get("implementation_commit") in {"", "null", None} and result.get("uncommitted_reason") in {"", "null", None}:
                missing.append(_item("implementation_commit", task.get("owner_agent", "owner"), "record a commit or explicit uncommitted reason"))
            if result.get("verification_status") != "passed":
                missing.append(_item("verification_status", task.get("owner_agent", "owner"), "record passed verification evidence"))
            if not _json_list(result.get("verification_refs"), field="verification_refs", source=str(result_path)):
                missing.append(_item("verification_refs", task.get("owner_agent", "owner"), "reference passed verification evidence"))
            events = {values.get("event") for _, values in context["records"] if values.get("task_id") == task_id}
            if "REVIEW_APPROVED" not in events:
                missing.append(_item("review_evidence", task.get("reviewer_agent", "reviewer"), "record approved review evidence"))
            if "QA_PASSED" not in events:
                missing.append(_item("qa_evidence", task.get("qa_agent", "qa"), "record passed QA evidence"))
    if context["manifest"].get("governance") in {"standard", "strict"}:
        if context["manifest"].get("git_branch") in {"", "null", None}:
            missing.append(_item("git_branch", "coordinator", "record the implementation branch"))
        _reference_missing(context, "git_status_ref", "coordinator", "accepted Git status evidence", missing)
    if context["manifest"].get("governance") == "strict":
        for field in ("change_id", "registry_ref", "version_contract_ref", "environment_impact_ref", "rollback_ref", "security_review_ref", "release_authorization_ref"):
            if field == "change_id":
                if context["manifest"].get(field) in {"", "null", None}:
                    missing.append(_item(field, "coordinator", "strict completion requires change ID"))
            elif field == "version_contract_ref":
                if context["manifest"].get("versioning_mode") != "tracked":
                    missing.append(_item(field, "coordinator", "strict delivery requires tracked version contract"))
            else:
                _reference_missing(context, field, "coordinator", "strict completion evidence", missing)
    return _report(context, [task_id], missing=missing, conflicts=conflicts, blocked_by=blocked)
