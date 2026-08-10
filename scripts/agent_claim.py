#!/usr/bin/env python3
"""Serialize task and Native-thread claims without creating another Agent."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

from project_memory_lib import exclusive_lock
from claim_lib import active_task_claim, effective_owner
from executor_pool import allocate_executor, load_executor_binding, release_executor
from preflight_lib import _active_locks, _load_context
from protocol_lib import atomic_write, event_records, frontmatter, json_string_list, path_within, paths_overlap, quote, replay_task_states, scalar_map, sha256


def _now() -> datetime:
    return datetime.now().astimezone()


def _capabilities(profile: dict[str, object]) -> set[str]:
    value = profile.get("capabilities", [])
    return {str(item) for item in value} if isinstance(value, list) else set()


def _active_claims(directory: Path, now: datetime) -> list[dict[str, str]]:
    active: list[dict[str, str]] = []
    release_dir = directory / "releases"
    released: set[str] = set()
    if release_dir.is_dir():
        for release_path in release_dir.glob("*.yaml"):
            try:
                release = scalar_map(release_path.read_text(encoding="utf-8"), source=str(release_path))
            except (OSError, ValueError):
                continue
            if release.get("status") == "released" and release.get("claim_id"):
                released.add(release["claim_id"])
    for path in sorted(directory.glob("*.yaml")):
        try:
            values = scalar_map(path.read_text(encoding="utf-8"), source=str(path))
            expiry = datetime.fromisoformat(values["lease_expires_at"].replace("Z", "+00:00"))
        except (OSError, KeyError, ValueError):
            continue
        if values.get("status") == "active" and values.get("claim_id") not in released and expiry > now:
            active.append({**values, "_path": str(path)})
    return active


def _claim_id(prefix: str, run_id: str, resource: str, agent: str, executor_id: str | None = None) -> str:
    material = f"{run_id}\0{resource}\0{agent}"
    if executor_id:
        material += f"\0{executor_id}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]
    return f"CLAIM-{prefix}-{digest}"


def _executor_binding(
    context: dict[str, object],
    task_id: str,
    agent_id: str,
    executor_id: str | None,
) -> dict[str, str] | None:
    if not executor_id:
        return None
    binding = load_executor_binding(context["run_dir"], executor_id)
    if binding.get("task_id") != task_id:
        raise ValueError("executor binding task does not match claim task")
    if binding.get("principal_agent_id") != agent_id:
        raise ValueError("executor binding principal does not match claim agent")
    if binding.get("status") != "active":
        raise ValueError("executor binding is not active")
    return binding


def _validate_claim_runtime(context: dict[str, object], task: dict[str, str], now: datetime) -> None:
    """Enforce capacity, path locks, and the frozen scope before claiming."""

    run_dir = context["run_dir"]
    manifest = context["manifest"]
    states = context["states"]
    active_states = {"dispatched", "acknowledged", "running", "handoff_ready", "reviewing", "qa_running", "release_ready"}
    if sum(state in active_states for state in states.values()) >= int(manifest.get("max_parallel", "1")):
        raise ValueError("claim would exceed max_parallel")
    task_path, _ = context["tasks"][task["task_id"]]
    owned = json_string_list(task.get("owned_paths", "[]"), field="owned_paths", source=str(task_path))
    root = context["project_root"]
    for task_id, pair in context["tasks"].items():
        if states.get(task_id) not in active_states or task_id == task["task_id"]:
            continue
        other_owned = json_string_list(pair[1].get("owned_paths", "[]"), field="owned_paths", source=str(pair[0]))
        if any(paths_overlap(left, right, root) for left in owned for right in other_owned):
            raise ValueError(f"claim path conflicts with active task: {task_id}")
    for lock in _active_locks(run_dir, now):
        resource = lock.get("resource", "")
        if resource and not resource.startswith("logical:") and any(paths_overlap(resource, path, root) for path in owned):
            raise ValueError("claim path conflicts with an active lock")
    scope_required = (
        manifest.get("execution_profile", "normal") != "emergency"
        or manifest.get("governance") == "strict"
    )
    if manifest.get("preflight_required", "false") != "true" or not scope_required:
        return
    scope_ref = manifest.get("scope_freeze_ref", "null")
    if scope_ref in {"", "null", None}:
        raise ValueError("claim requires a frozen scope")
    scope_path = Path(scope_ref).expanduser()
    if not scope_path.is_absolute():
        scope_path = run_dir / scope_path
    if not scope_path.is_file() or manifest.get("scope_freeze_ref_sha256") != sha256(scope_path):
        raise ValueError("scope freeze is missing or hash-mismatched")
    scope = scalar_map(scope_path.read_text(encoding="utf-8"), source=str(scope_path))
    requested = json_string_list(scope.get("requested_paths", "[]"), field="requested_paths", source=str(scope_path))
    forbidden = json_string_list(scope.get("forbidden_paths", "[]"), field="forbidden_paths", source=str(scope_path))
    for path in owned:
        if not any(path_within(path, [scope_path_value], root) for scope_path_value in requested):
            raise ValueError(f"claim owned path exceeds frozen scope: {path}")
        if any(path_within(path, [forbidden_path], root) for forbidden_path in forbidden):
            raise ValueError(f"claim owned path is forbidden by scope: {path}")


def claim_task(
    run_dir_value: str | Path,
    task_id: str,
    agent_id: str,
    lease_seconds: int,
    *,
    executor_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    context = _load_context(run_dir_value)
    run_dir = context["run_dir"]
    if context["manifest"].get("governance") == "strict" or context["manifest"].get("dispatch_policy", "central") not in {"hybrid", "self_service"}:
        raise ValueError("task claims require hybrid or self_service dispatch under non-strict governance")
    if agent_id not in context["agents"]:
        raise ValueError(f"agent is not registered: {agent_id}")
    binding = _executor_binding(context, task_id, agent_id, executor_id)
    if "task_claim" not in _capabilities(context["agents"][agent_id]):
        raise ValueError("agent lacks task_claim capability")
    pair = context["tasks"].get(task_id)
    if pair is None:
        raise ValueError(f"task does not exist: {task_id}")
    task_path, task = pair
    if task.get("assignment_mode", "fixed") != "claimable" or task.get("owner_agent") != "pool":
        raise ValueError("only claimable pool tasks can be claimed")
    eligible = json.loads(task.get("eligible_agents", "[]"))
    if agent_id not in eligible:
        raise ValueError("agent is not eligible for this task")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be at least 1")
    now = _now()
    _validate_claim_runtime(context, task, now)
    claim_dir = run_dir / "claims" / "tasks"
    if not dry_run:
        claim_dir.mkdir(parents=True, exist_ok=True)
    claim_id = _claim_id("TASK", context["manifest"]["run_id"], task_id, agent_id, executor_id)
    claim_path = claim_dir / f"{claim_id}.yaml"
    active = [item for item in _active_claims(claim_dir, now) if item.get("task_id") == task_id]
    if active:
        holder = active[0]
        return {
            "ready": False,
            "conflict": True,
            "task_id": task_id,
            "holder_agent": holder.get("claimer_agent"),
            "lease_expires_at": holder.get("lease_expires_at"),
            "blocked_by": "active_task_claim",
            "next_action": "wait_for_claim_release_or_timeout_recovery",
        }
    states, state_errors = replay_task_states(context["records"], context["manifest"].get("governance", ""))
    if state_errors:
        raise ValueError("invalid event history: " + "; ".join(state_errors))
    if states.get(task_id) is None and not dry_run:
        publisher = task.get("published_by", "coordinator")
        if publisher != "coordinator" and "task_publish" not in _capabilities(context["agents"].get(publisher, {})):
            raise ValueError("unpublished claimable task requires a Coordinator or authorized publisher")
        ready_command = [
            sys.executable,
            str(Path(__file__).with_name("emit_event.py")),
            "--run-dir", str(run_dir),
            "--task-id", task_id,
            "--event", "TASK_READY",
            "--from-agent", publisher,
            "--to-agent", "coordinator",
            "--summary", "claimable task ready",
            "--payload-file", str(task_path),
            "--idempotency-key", f"{context['manifest']['run_id']}:{task_id}:TASK_READY:claimable:v1",
        ]
        ready = subprocess.run(ready_command, capture_output=True, text=True)
        if ready.returncode:
            raise RuntimeError(ready.stderr.strip() or ready.stdout.strip())
        states = {task_id: "ready"}
    if states.get(task_id) is None and dry_run:
        states = {task_id: "ready"}
    if states.get(task_id) != "ready":
        raise ValueError("claimable task must be TASK_READY before it can be claimed")
    acquired = now.isoformat(timespec="seconds")
    expires = (now + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
    executor_created = False
    if executor_id is None and context["manifest"].get("executor_policy", "fixed") == "capability_pool":
        executor = allocate_executor(
            run_dir,
            task_id=task_id,
            principal_agent_id=agent_id,
            role_ref=task.get("role_ref", agent_id),
            required_capabilities=json_string_list(
                task.get("required_capabilities", "[]"),
                field="required_capabilities",
                source=str(task_path),
            ),
            runtime=str(context["agents"][agent_id].get("runtime", "document")),
            workspace=task.get("workspace", str(context["project_root"])),
            worktree_policy=task.get("workspace_policy", "isolated_writer"),
            dry_run=dry_run,
        )
        executor_id = str(executor["executor_id"])
        executor_created = not bool(executor.get("reused"))
        claim_id = _claim_id("TASK", context["manifest"]["run_id"], task_id, agent_id, executor_id)
        claim_path = claim_dir / f"{claim_id}.yaml"
    content = "\n".join(
        (
            "protocol_version: 3",
            'kind: "task_claim"',
            f"claim_id: {quote(claim_id)}",
            f"run_id: {quote(context['manifest']['run_id'])}",
            f"task_id: {quote(task_id)}",
            f"task_sha256: {quote(sha256(task_path))}",
            f"claimer_agent: {quote(agent_id)}",
            f"executor_id: {quote(executor_id) if executor_id else 'null'}",
            f"eligible_agents: {json.dumps(eligible, ensure_ascii=False)}",
            f"lease_acquired_at: {quote(acquired)}",
            f"lease_expires_at: {quote(expires)}",
            f"parent_causation_id: {quote(task.get('parent_task_id', 'null'))}",
            'status: "active"',
            "",
        )
    )
    if not dry_run:
        conflict_result: dict[str, object] | None = None
        with exclusive_lock(run_dir / "locks" / ".task-claim.lock"):
            active = [item for item in _active_claims(claim_dir, _now()) if item.get("task_id") == task_id]
            if active:
                holder = active[0]
                conflict_result = {
                    "ready": False,
                    "conflict": True,
                    "task_id": task_id,
                    "holder_agent": holder.get("claimer_agent"),
                    "lease_expires_at": holder.get("lease_expires_at"),
                    "blocked_by": "active_task_claim",
                    "next_action": "wait_for_claim_release_or_timeout_recovery",
                }
            elif claim_path.exists():
                raise ValueError(f"claim id collision: {claim_path}")
            else:
                atomic_write(claim_path, content)
        if conflict_result is not None:
            if executor_id and executor_created:
                release_executor(run_dir, executor_id, agent_id, "claim lost race", dry_run=False)
            return conflict_result
        dispatch = _dispatch_claimed_task(run_dir, task_id, agent_id, claim_id, executor_id)
    else:
        dispatch = {"dry_run": True}
    return {
        "ready": True,
        "conflict": False,
        "claim_id": claim_id,
        "claim_path": str(claim_path),
        "task_id": task_id,
        "claimer_agent": agent_id,
        "executor_id": executor_id,
        "lease_expires_at": expires,
        "dispatch": dispatch,
        "dry_run": dry_run,
    }


def _dispatch_claimed_task(
    run_dir: Path,
    task_id: str,
    agent_id: str,
    claim_id: str,
    executor_id: str | None = None,
) -> dict[str, object]:
    """Emit the claimant-owned dispatch event and wake exactly that claimant."""

    task_path = run_dir / "tasks" / f"{task_id}.md"
    event_command = [
        sys.executable,
        str(Path(__file__).with_name("emit_event.py")),
        "--run-dir", str(run_dir),
        "--task-id", task_id,
        "--event", "TASK_DISPATCHED",
        "--from-agent", agent_id,
        "--to-agent", agent_id,
        "--summary", "claimant dispatch",
        "--payload-file", str(task_path),
        "--idempotency-key", f"{task_id}:{claim_id}:TASK_DISPATCHED:v1",
    ]
    emitted = subprocess.run(event_command, capture_output=True, text=True)
    if emitted.returncode:
        raise RuntimeError(emitted.stderr.strip() or emitted.stdout.strip())
    from wake_agent import wake_agent

    return wake_agent(run_dir, task_id, agent_id, executor_id=executor_id, dry_run=False)


def claim_thread(
    run_dir_value: str | Path,
    task_id: str,
    agent_id: str,
    thread_id: str,
    platform: str,
    workspace: str,
    lease_seconds: int,
    session_id: str | None = None,
    *,
    executor_id: str | None = None,
    dry_run: bool = False,
) -> dict[str, object]:
    context = _load_context(run_dir_value)
    if context["manifest"].get("governance") == "strict" or context["manifest"].get("dispatch_policy", "central") != "self_service":
        raise ValueError("thread claims require self_service dispatch under non-strict governance")
    if agent_id not in context["agents"]:
        raise ValueError(f"agent is not registered: {agent_id}")
    binding = _executor_binding(context, task_id, agent_id, executor_id)
    if "thread_claim" not in _capabilities(context["agents"][agent_id]):
        raise ValueError("agent lacks thread_claim capability")
    task_pair = context["tasks"].get(task_id)
    if task_pair is None:
        raise ValueError(f"task does not exist: {task_id}")
    task = task_pair[1]
    if effective_owner(context["run_dir"], task) != agent_id:
        raise ValueError("thread claimant must own the task; claim the task first")
    now = _now()
    task_claim = active_task_claim(context["run_dir"], task_id, now=now)
    if executor_id is None and task_claim and task_claim.get("executor_id") not in {None, "", "null"}:
        executor_id = task_claim["executor_id"]
        binding = _executor_binding(context, task_id, agent_id, executor_id)
    workspace_path = Path(workspace).expanduser().resolve()
    expected_workspace = Path(binding["workspace"]).expanduser().resolve() if binding else context["project_root"]
    if workspace_path != expected_workspace:
        raise ValueError("thread workspace must equal the executor workspace")
    if platform not in {"codex", "hermes", "document"}:
        raise ValueError("unsupported thread platform")
    effective_session_id = session_id or (binding.get("session_id") if binding else None)
    if platform in {"codex", "hermes"} and not effective_session_id:
        raise ValueError("native thread claims require a real session_id")
    if lease_seconds < 1:
        raise ValueError("lease_seconds must be at least 1")
    claim_dir = context["run_dir"] / "claims" / "threads"
    if not dry_run:
        claim_dir.mkdir(parents=True, exist_ok=True)
    active = [item for item in _active_claims(claim_dir, now) if item.get("thread_id") == thread_id]
    if active:
        holder = active[0]
        return {
            "ready": False,
            "conflict": True,
            "thread_id": thread_id,
            "holder_agent": holder.get("claimer_agent"),
            "lease_expires_at": holder.get("lease_expires_at"),
            "blocked_by": "active_thread_claim",
            "next_action": "wait_for_thread_release_or_timeout_recovery",
        }
    claim_id = _claim_id("THREAD", context["manifest"]["run_id"], thread_id, agent_id, executor_id)
    claim_path = claim_dir / f"{claim_id}.yaml"
    acquired = now.isoformat(timespec="seconds")
    expires = (now + timedelta(seconds=lease_seconds)).isoformat(timespec="seconds")
    content = "\n".join(
        (
            "protocol_version: 3",
            'kind: "thread_claim"',
            f"claim_id: {quote(claim_id)}",
            f"run_id: {quote(context['manifest']['run_id'])}",
            f"task_id: {quote(task_id)}",
            f"task_sha256: {quote(sha256(task_pair[0]))}",
            f"task_claim_id: {quote(task_claim.get('claim_id', 'null') if task_claim else 'null')}",
            f"thread_id: {quote(thread_id)}",
            f"claimer_agent: {quote(agent_id)}",
            f"executor_id: {quote(executor_id) if executor_id else 'null'}",
            f"platform: {quote(platform)}",
            f"session_id: {quote(effective_session_id) if effective_session_id else 'null'}",
            f"workspace: {quote(str(workspace_path))}",
            f"lease_acquired_at: {quote(acquired)}",
            f"lease_expires_at: {quote(expires)}",
            f"parent_causation_id: {quote(task.get('parent_task_id', 'null'))}",
            'status: "active"',
            "",
        )
    )
    if not dry_run:
        with exclusive_lock(context["run_dir"] / "locks" / ".thread-claim.lock"):
            active = [item for item in _active_claims(claim_dir, _now()) if item.get("thread_id") == thread_id]
            if active:
                holder = active[0]
                return {
                    "ready": False,
                    "conflict": True,
                    "thread_id": thread_id,
                    "holder_agent": holder.get("claimer_agent"),
                    "lease_expires_at": holder.get("lease_expires_at"),
                    "blocked_by": "active_thread_claim",
                    "next_action": "wait_for_thread_release_or_timeout_recovery",
                }
            if claim_path.exists():
                raise ValueError(f"claim id collision: {claim_path}")
            atomic_write(claim_path, content)
    return {
        "ready": True,
        "conflict": False,
        "claim_id": claim_id,
        "claim_path": str(claim_path),
        "thread_id": thread_id,
        "claimer_agent": agent_id,
        "executor_id": executor_id,
        "lease_expires_at": expires,
        "dry_run": dry_run,
    }


def release_claim(
    run_dir_value: str | Path,
    claim_ref: str | Path,
    agent_id: str,
    reason: str,
    kind: str,
    *,
    dry_run: bool = False,
) -> dict[str, object]:
    """Append an immutable release record for the current claim holder."""

    context = _load_context(run_dir_value)
    run_dir = context["run_dir"]
    claim_path = Path(claim_ref).expanduser()
    if not claim_path.is_absolute():
        claim_path = run_dir / claim_path
    claim_path = claim_path.resolve()
    expected_dir = (run_dir / "claims" / ("tasks" if kind == "task_claim" else "threads")).resolve()
    try:
        claim_path.relative_to(expected_dir)
    except ValueError as exc:
        raise ValueError("claim reference must belong to this run's claim directory") from exc
    if not claim_path.is_file():
        raise ValueError(f"claim does not exist: {claim_path}")
    values = scalar_map(claim_path.read_text(encoding="utf-8"), source=str(claim_path))
    if values.get("kind") != kind or values.get("run_id") != context["manifest"].get("run_id"):
        raise ValueError("claim kind or run does not match")
    if values.get("status") != "active":
        raise ValueError("only active claims can be released")
    if values.get("claimer_agent") != agent_id:
        raise ValueError("only the claim holder can release this claim")
    if not reason.strip():
        raise ValueError("release reason must not be empty")
    now = _now()
    release_dir = claim_path.parent / "releases"
    release_path = release_dir / f"{claim_path.stem}.yaml"
    content = "\n".join(
        (
            "protocol_version: 3",
            'kind: "claim_release"',
            f"run_id: {quote(context['manifest']['run_id'])}",
            f"claim_kind: {quote(kind)}",
            f"claim_id: {quote(values.get('claim_id', claim_path.stem))}",
            f"claim_ref: {quote(str(claim_path))}",
            f"claim_sha256: {quote(sha256(claim_path))}",
            f"released_by: {quote(agent_id)}",
            f"released_at: {quote(now.isoformat(timespec='seconds'))}",
            f"reason: {quote(reason.strip())}",
            'status: "released"',
            "",
        )
    )
    if not dry_run:
        lock_name = ".task-claim.lock" if kind == "task_claim" else ".thread-claim.lock"
        with exclusive_lock(run_dir / "locks" / lock_name):
            if release_path.exists():
                raise ValueError(f"claim release already exists: {release_path}")
            release_dir.mkdir(parents=True, exist_ok=True)
            atomic_write(release_path, content)
    executor_release = None
    executor_ref = values.get("executor_id")
    if executor_ref not in {None, "", "null"}:
        executor_release = release_executor(
            run_dir,
            executor_ref,
            agent_id,
            f"claim release: {reason.strip()}",
            dry_run=dry_run,
        )
    return {
        "ready": True,
        "claim_id": values.get("claim_id", claim_path.stem),
        "claim_path": str(claim_path),
        "release_path": str(release_path),
        "released_by": agent_id,
        "reason": reason.strip(),
        "executor_release": executor_release,
        "dry_run": dry_run,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    task = subparsers.add_parser("claim-task")
    task.add_argument("--run-dir", required=True); task.add_argument("--task-id", required=True)
    task.add_argument("--agent-id", required=True); task.add_argument("--lease-seconds", type=int, default=600)
    task.add_argument("--executor-id")
    task.add_argument("--dry-run", action="store_true")
    thread = subparsers.add_parser("claim-thread")
    thread.add_argument("--run-dir", required=True); thread.add_argument("--task-id", required=True)
    thread.add_argument("--agent-id", required=True); thread.add_argument("--thread-id", required=True)
    thread.add_argument("--platform", required=True); thread.add_argument("--session-id")
    thread.add_argument("--workspace", required=True)
    thread.add_argument("--lease-seconds", type=int, default=600); thread.add_argument("--dry-run", action="store_true")
    thread.add_argument("--executor-id")
    release_task = subparsers.add_parser("release-task")
    release_task.add_argument("--run-dir", required=True); release_task.add_argument("--claim-ref", required=True)
    release_task.add_argument("--agent-id", required=True); release_task.add_argument("--reason", required=True)
    release_task.add_argument("--dry-run", action="store_true")
    release_thread = subparsers.add_parser("release-thread")
    release_thread.add_argument("--run-dir", required=True); release_thread.add_argument("--claim-ref", required=True)
    release_thread.add_argument("--agent-id", required=True); release_thread.add_argument("--reason", required=True)
    release_thread.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.command == "claim-task":
        result = claim_task(args.run_dir, args.task_id, args.agent_id, args.lease_seconds, executor_id=args.executor_id, dry_run=args.dry_run)
    elif args.command == "claim-thread":
        result = claim_thread(args.run_dir, args.task_id, args.agent_id, args.thread_id, args.platform, args.workspace, args.lease_seconds, session_id=args.session_id, executor_id=args.executor_id, dry_run=args.dry_run)
    elif args.command == "release-task":
        result = release_claim(args.run_dir, args.claim_ref, args.agent_id, args.reason, "task_claim", dry_run=args.dry_run)
    else:
        result = release_claim(args.run_dir, args.claim_ref, args.agent_id, args.reason, "thread_claim", dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
