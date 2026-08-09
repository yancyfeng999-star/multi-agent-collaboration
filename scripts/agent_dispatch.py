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
from preflight_lib import _load_context
from protocol_lib import atomic_write, frontmatter, json_string_list, path_within, paths_overlap, scalar_map, sha256, valid_iso8601
from wake_agent import wake_agent


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
    elif manifest.get("preflight_required", "false") == "true":
        raise ValueError("self-service publication requires a frozen scope")
    for path in child_owned:
        if any(path_within(path, [forbidden], context["project_root"]) for forbidden in [*parent_forbidden, *scope_forbidden]):
            raise ValueError(f"child owned path is forbidden by parent or scope: {path}")
        if not path_within(path, parent_owned, context["project_root"]) and not any(
            path_within(path, [scope], context["project_root"]) for scope in scope_paths
        ):
            raise ValueError(f"child owned path exceeds parent or frozen scope: {path}")
    for task_id, (_, task) in context["tasks"].items():
        if context["states"].get(task_id) not in {"dispatched", "acknowledged", "running", "handoff_ready", "reviewing", "qa_running", "release_ready"}:
            continue
        existing_owned = json_string_list(task.get("owned_paths", "[]"), field="owned_paths", source=task_id)
        if any(paths_overlap(left, right, context["project_root"]) for left in child_owned for right in existing_owned):
            raise ValueError(f"child owned path conflicts with active task: {task_id}")
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
    return command


def publish(args: argparse.Namespace) -> dict[str, Any]:
    run_dir = Path(args.run_dir).expanduser().resolve()
    for field in ("reviewer_agent", "qa_agent", "release_agent"):
        if len(getattr(args, field)) > 1:
            raise ValueError(f"{field} accepts at most one Agent")
    decision = _validate_publication(
        run_dir,
        args.publisher_agent,
        args.parent_task,
        args.owner_agent,
        args.owned_path,
        args.assignment_mode,
    )
    context = _load_context(run_dir)
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
    result: dict[str, Any] = {"ready": True, **decision, "task_id": args.task_id, "dry_run": args.dry_run}
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
                    "--causation-id",
                    args.parent_task,
                    "--idempotency-key",
                    f"{decision['run_id']}:{args.task_id}:{event}:self-service:v1",
                ]
                emitted = subprocess.run(event_command, capture_output=True, text=True)
                if emitted.returncode:
                    raise RuntimeError(emitted.stderr.strip() or emitted.stdout.strip())
        except Exception:
            if task_path.exists():
                task_path.unlink()
            atomic_write(manifest_path, manifest_before)
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
        result["dispatch"] = wake_agent(run_dir, args.task_id, args.owner_agent, dry_run=False)
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
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(publish(args), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
