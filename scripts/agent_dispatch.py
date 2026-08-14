#!/usr/bin/env python3
"""Publish and dispatch one scoped child task without waking Coordinator."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from project_memory_lib import exclusive_lock
from claim_lib import effective_owner
from conflict_model import find_conflict
from executor_pool import allocate_executor, release_executor
from preflight_lib import _load_context
from protocol_lib import atomic_write, frontmatter, json_string_list, path_within, paths_overlap, scalar_map, sha256, valid_iso8601
from wake_agent import wake_agent
from message_contract import compact_messages


def _capabilities(profile: dict[str, object]) -> set[str]:
    value = profile.get("capabilities", [])
    return {str(item) for item in value} if isinstance(value, list) else set()


def _validate_publication(
    run_dir: Path,
    publisher: str,
    parent_task_id: str,
    owner: str,
    child_owned: list[str],
    assignment_mode: str,
    logical_resources: list[str],
    environment_resources: list[str],
    workspace: str | None,
    workspace_policy: str,
    release_lane: str,
) -> dict[str, Any]:
    context = _load_context(run_dir)
    manifest = context["manifest"]
    agents = context["agents"]
    if publisher not in agents:
        raise ValueError(f"publisher is not registered: {publisher}")
    if publisher != "coordinator" and "task_publish" not in _capabilities(agents[publisher]):
        raise ValueError("publisher lacks task_publish capability")
    if publisher != "coordinator" and manifest.get("dispatch_policy", "central") == "central":
        raise ValueError("central dispatch policy requires Coordinator publication")
    if manifest.get("governance") == "strict" and manifest.get("dispatch_policy", "central") != "central":
        raise ValueError("strict governance requires central dispatch policy")
    parent_pair = context["tasks"].get(parent_task_id)
    if parent_pair is None:
        raise ValueError(f"parent task does not exist: {parent_task_id}")
    parent_path, parent = parent_pair
    parent_owner = effective_owner(run_dir, parent)
    if context["states"].get(parent_task_id) in {"completed", "failed", "cancelled", "superseded", "expired", "dead_letter"}:
        raise ValueError("parent task is terminal and cannot publish another child")
    if publisher not in {"coordinator", parent_owner, parent.get("published_by")}:
        collaborators = json_string_list(parent.get("collaborating_agents", "[]"), field="collaborating_agents", source=str(parent_path)) if parent.get("collaborating_agents") else []
        if publisher not in collaborators:
            raise ValueError("publisher is not the parent owner or declared collaborator")
    if owner != "pool" and owner not in agents:
        raise ValueError(f"child owner is not registered: {owner}")
    if owner == "pool" and assignment_mode != "claimable":
        raise ValueError("pool owner requires claimable assignment")
    if assignment_mode == "claimable" and owner != "pool":
        raise ValueError("claimable assignment requires pool owner")
    if owner == "pool" and manifest.get("dispatch_policy", "central") == "central":
        raise ValueError("central dispatch policy does not allow claimable child tasks")
    if not child_owned:
        raise ValueError("self-service child task requires at least one owned path")
    parent_owned = json_string_list(parent.get("owned_paths", "[]"), field="owned_paths", source=str(parent_path))
    parent_forbidden = json_string_list(parent.get("forbidden_paths", "[]"), field="forbidden_paths", source=str(parent_path))
    scope_ref = manifest.get("scope_freeze_ref", "null")
    scope_paths: list[str] = []
    scope_forbidden: list[str] = []
    if scope_ref not in {"", "null", None}:
        scope_path = Path(scope_ref).expanduser()
        if not scope_path.is_absolute():
            scope_path = run_dir / scope_path
        if not scope_path.is_file() or manifest.get("scope_freeze_ref_sha256") != sha256(scope_path):
            raise ValueError("scope freeze is missing or hash-mismatched")
        scope_values = scalar_map(scope_path.read_text(encoding="utf-8"), source=str(scope_path))
        scope_paths = json_string_list(scope_values.get("requested_paths", "[]"), field="requested_paths", source=str(scope_path))
        scope_forbidden = json_string_list(scope_values.get("forbidden_paths", "[]"), field="forbidden_paths", source=str(scope_path))
    else:
        scope_required = (
            manifest.get("execution_profile", "normal") != "emergency"
            or manifest.get("governance") == "strict"
        )
        if manifest.get("preflight_required", "false") == "true" and scope_required:
            raise ValueError("self-service publication requires a frozen scope")
    for path in child_owned:
        if any(path_within(path, [forbidden], context["project_root"]) for forbidden in [*parent_forbidden, *scope_forbidden]):
            raise ValueError(f"child owned path is forbidden by parent or scope: {path}")
        if not path_within(path, parent_owned, context["project_root"]) and not any(
            path_within(path, [scope], context["project_root"]) for scope in scope_paths
        ):
            raise ValueError(f"child owned path exceeds parent or frozen scope: {path}")
    active_tasks: list[dict[str, str]] = []
    for task_id, (_, task) in context["tasks"].items():
        if context["states"].get(task_id) not in {"dispatched", "acknowledged", "running", "handoff_ready", "reviewing", "qa_running", "release_ready"}:
            continue
        active_tasks.append(task)
        existing_owned = json_string_list(task.get("owned_paths", "[]"), field="owned_paths", source=task_id)
        if any(paths_overlap(left, right, context["project_root"]) for left in child_owned for right in existing_owned):
            raise ValueError(f"child owned path conflicts with active task: {task_id}")
    conflict = find_conflict(
        {
            "task_id": "child-publication",
            "owned_paths": json.dumps(child_owned, ensure_ascii=False),
            "logical_resources": json.dumps(logical_resources, ensure_ascii=False),
            "environment_resources": json.dumps(environment_resources, ensure_ascii=False),
            "workspace": workspace or "",
            "workspace_policy": workspace_policy,
            "release_lane": release_lane,
        },
        active_tasks,
        context["project_root"],
    )
    if conflict:
        raise ValueError(f"child task conflicts with active task: {conflict}")
    now = datetime.now().astimezone()
    for lock_path in sorted((run_dir / "locks").glob("*.yaml")):
        values = scalar_map(lock_path.read_text(encoding="utf-8"), source=str(lock_path))
        expires = values.get("lease_expires_at", "")
        if not valid_iso8601(expires):
            raise ValueError(f"active lock has invalid lease: {lock_path.name}")
        if datetime.fromisoformat(expires.replace("Z", "+00:00")) <= now:
            continue
        resource = values.get("resource", "")
        if resource and not resource.startswith("logical:") and any(
            paths_overlap(path, resource, context["project_root"]) for path in child_owned
        ):
            raise ValueError(f"child owned path conflicts with active lock: {lock_path.name}")
    return {
        "run_id": manifest["run_id"],
        "parent_task_id": parent_task_id,
        "parent_task_sha256": sha256(parent_path),
        "publisher_agent": publisher,
        "owner_agent": owner,
        "assignment_mode": "claimable" if owner == "pool" else "fixed",
        "dispatch_policy": manifest.get("dispatch_policy", "central"),
    }


def _create_task_command(args: argparse.Namespace, run_dir: Path) -> list[str]:
    command = [
        sys.executable,
        str(Path(__file__).with_name("manage_run.py")),
        "create-task",
        "--run-dir",
        str(run_dir),
        "--task-id",
        args.task_id,
        "--title",
        args.title,
        "--objective",
        args.objective,
        "--owner-agent",
        args.owner_agent,
        "--published-by",
        args.publisher_agent,
        "--parent-task",
        args.parent_task,
        "--assignment-mode",
        args.assignment_mode,
    ]
    for value in args.eligible_agent:
        command.extend(("--eligible-agent", value))
    for value in args.reviewer_agent:
        command.extend(("--reviewer-agent", value))
    for value in args.qa_agent:
        command.extend(("--qa-agent", value))
    for value in args.release_agent:
        command.extend(("--release-agent", value))
    for option, values in (
        ("--owned-path", args.owned_path),
        ("--forbidden-path", args.forbidden_path),
        ("--dependency", args.dependency),
        ("--risk-flag", args.risk_flag),
        ("--human-gate", args.human_gate),
        ("--acceptance", args.acceptance),
        ("--verification", args.verification),
    ):
        for value in values:
            command.extend((option, value))
    for option, values in (
        ("--required-capability", args.required_capability),
        ("--logical-resource", args.logical_resource),
        ("--environment-resource", args.environment_resource),
    ):
        for value in values:
            command.extend((option, value))
    for option, value in (
        ("--role-ref", args.role_ref),
        ("--workspace", args.workspace),
        ("--workspace-policy", args.workspace_policy),
        ("--release-lane", args.release_lane),
    ):
        if value:
            command.extend((option, value))
    return command


def publish(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir).expanduser().resolve()
    for field in ("reviewer_agent", "qa_agent", "release_agent"):
        if len(getattr(args, field)) > 1:
            raise ValueError(f"{field} accepts at most one Agent")
    context = _load_context(run_dir)
    decision = _validate_publication(
        run_dir,
        args.publisher_agent,
        args.parent_task,
        args.owner_agent,
        args.owned_path,
        args.assignment_mode,
        args.logical_resource,
        args.environment_resource,
        args.workspace,
        args.workspace_policy,
        args.release_lane,
    )
    for dependency in args.dependency:
        if context["states"].get(dependency) != "completed":
            raise ValueError(f"dependency is not completed: {dependency}")
    if args.assignment_mode == "fixed":
        active_states = {"dispatched", "acknowledged", "running", "handoff_ready", "reviewing", "qa_running", "release_ready"}
        if sum(state in active_states for state in context["states"].values()) >= int(context["manifest"].get("max_parallel", "1")):
            raise ValueError("publication would exceed max_parallel")
    if context["manifest"].get("governance") in {"standard", "strict"}:
        quality_agents = [value for value in (args.reviewer_agent + args.qa_agent) if value]
        if len(args.reviewer_agent) != 1 or len(args.qa_agent) != 1:
            raise ValueError("standard/strict publication requires one Reviewer and one QA Agent")
        owner_for_quality = args.owner_agent if args.assignment_mode == "fixed" else "pool"
        if owner_for_quality in quality_agents:
            raise ValueError("publisher child Owner cannot self-review or self-QA")
    executor_binding: dict[str, Any] | None = None
    if (
        args.assignment_mode == "fixed"
        and context["manifest"].get("executor_policy", "fixed") == "capability_pool"
    ):
        owner_profile = context["agents"].get(args.owner_agent)
        if owner_profile is None:
            raise ValueError(f"child owner is not registered: {args.owner_agent}")
        executor_binding = allocate_executor(
            run_dir,
            task_id=args.task_id,
            principal_agent_id=args.owner_agent,
            role_ref=args.role_ref or args.owner_agent,
            required_capabilities=args.required_capability,
            runtime=str(owner_profile.get("runtime", "document")),
            workspace=args.workspace or context["project_root"],
            worktree_policy=args.workspace_policy,
            dry_run=args.dry_run,
        )
    baseline = context["manifest"].get("baseline_commit") or context["manifest"].get("version_contract_ref_sha256") or "unknown"
    result: dict[str, Any] = {
        "ready": True,
        **decision,
        "task_id": args.task_id,
        "dry_run": args.dry_run,
        "coordination_messages": compact_messages([
            {
                "kind": "STARTED",
                "task_id": args.task_id,
                "owner": args.owner_agent,
                "paths": args.owned_path or ["unknown"],
                "baseline": baseline,
            }
        ]),
    }
    if executor_binding:
        result["executor_id"] = executor_binding["executor_id"]
    if args.dry_run:
        return result
    manifest_path = run_dir / "manifest.yaml"
    task_path = run_dir / "tasks" / f"{args.task_id}.md"
    manifest_before = manifest_path.read_text(encoding="utf-8")
    with exclusive_lock(run_dir / "locks" / ".task-publication.lock"):
        command = _create_task_command(args, run_dir)
        created = subprocess.run(command, capture_output=True, text=True)
        if created.returncode:
            raise RuntimeError(created.stderr.strip() or created.stdout.strip())
        try:
            event_names = ("TASK_READY",) if args.assignment_mode == "claimable" else ("TASK_READY", "TASK_DISPATCHED")
            event_target = "coordinator" if args.assignment_mode == "claimable" else args.owner_agent
            for event in event_names:
                event_command = [
                    sys.executable,
                    str(Path(__file__).with_name("emit_event.py")),
                    "--run-dir",
                    str(run_dir),
                    "--task-id",
                    args.task_id,
                    "--event",
                    event,
                    "--from-agent",
                    args.publisher_agent,
                    "--to-agent",
                    event_target,
                    "--summary",
                    f"self-service {event.lower()}",
                    "--payload-file",
                    str(task_path),
                    "--idempotency-key",
                    f"{decision['run_id']}:{args.task_id}:{event}:self-service:v1",
                ]
                parent_events = [
                    values.get("event_id")
                    for _, values in context["records"]
                    if values.get("task_id") == args.parent_task and values.get("event_id")
                ]
                if parent_events:
                    event_command.extend(("--causation-id", parent_events[-1]))
                emitted = subprocess.run(event_command, capture_output=True, text=True)
                if emitted.returncode:
                    raise RuntimeError(emitted.stderr.strip() or emitted.stdout.strip())
        except Exception:
            if task_path.exists():
                task_path.unlink()
            atomic_write(manifest_path, manifest_before)
            if executor_binding and not executor_binding.get("dry_run") and not executor_binding.get("reused"):
                release_executor(
                    run_dir,
                    str(executor_binding["executor_id"]),
                    str(executor_binding["principal_agent_id"]),
                    "child publication rolled back",
                )
            raise
    result["task_path"] = str(task_path)
    result["task_sha256"] = sha256(task_path)
    if args.assignment_mode == "claimable":
        result["dispatch"] = {
            "status": "awaiting_claim",
            "target": "pool",
            "next_action": "eligible_agent_claim_task",
        }
    else:
        result["dispatch"] = wake_agent(
            run_dir,
            args.task_id,
            args.owner_agent,
            executor_id=str(executor_binding["executor_id"]) if executor_binding else None,
            dry_run=False,
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("publish", nargs="?", help="Publish one child task")
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--publisher-agent", required=True)
    parser.add_argument("--parent-task", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--objective", required=True)
    parser.add_argument("--owner-agent", required=True)
    parser.add_argument("--assignment-mode", choices=("fixed", "claimable"), default="fixed")
    parser.add_argument("--eligible-agent", action="append", default=[])
    parser.add_argument("--reviewer-agent", action="append", default=[])
    parser.add_argument("--qa-agent", action="append", default=[])
    parser.add_argument("--release-agent", action="append", default=[])
    parser.add_argument("--owned-path", action="append", default=[])
    parser.add_argument("--forbidden-path", action="append", default=[])
    parser.add_argument("--dependency", action="append", default=[])
    parser.add_argument("--risk-flag", action="append", default=[])
    parser.add_argument("--human-gate", action="append", default=[])
    parser.add_argument("--acceptance", action="append", default=[])
    parser.add_argument("--verification", action="append", default=[])
    parser.add_argument("--role-ref")
    parser.add_argument("--required-capability", action="append", default=[])
    parser.add_argument("--logical-resource", action="append", default=[])
    parser.add_argument("--environment-resource", action="append", default=[])
    parser.add_argument("--workspace")
    parser.add_argument(
        "--workspace-policy",
        choices=("isolated_writer", "shared_read_only", "shared_no_git_mutation"),
        default="isolated_writer",
    )
    parser.add_argument("--release-lane", default="none")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(publish(args), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
