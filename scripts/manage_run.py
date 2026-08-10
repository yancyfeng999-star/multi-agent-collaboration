#!/usr/bin/env python3
"""Create and maintain protocol-v3 run documents without hand-editing YAML."""

from __future__ import annotations

import argparse
import hashlib
from project_memory_lib import exclusive_lock
from claim_lib import effective_owner
import json
import os
import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

from protocol_lib import (
    PROTOCOL_VERSION,
    ProtocolError,
    atomic_write,
    event_records,
    frontmatter,
    json_string_list,
    json_string_map,
    now_iso,
    parse_agent_profiles,
    path_within,
    paths_overlap,
    quote,
    refresh_runtime_documents,
    rebuild_state,
    replace_flat_scalar,
    scalar_map,
    sha256,
)
from record_agent_activity import record_agent_activity


RUNTIMES = ("codex_thread", "codex_subagent", "document", "document_subagent")
RUN_CONFIG_FIELDS = {
    "change_id",
    "registry_ref",
    "git_branch",
    "git_status_ref",
    "environment_impact_ref",
    "rollback_ref",
    "security_review_ref",
    "release_environment",
    "release_authorization_ref",
    "clean_worktree_ref",
    "execution_profile",
    "dispatch_policy",
    "preflight_required",
    "scope_freeze_ref",
    "retry_policy_ref",
}


def safe_identifier(value: str, label: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise SystemExit(f"{label} must contain only letters, digits, dot, underscore, or dash")
    return value


def add_common_run_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--run-dir", required=True)


def add_activity_bridge_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--activity-project-root")
    parser.add_argument("--activity-session-id")
    parser.add_argument("--activity-runtime-profile-ref")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    add_agent = subparsers.add_parser("add-agent", help="Register one run-local agent")
    add_common_run_argument(add_agent)
    add_agent.add_argument("--agent-id", required=True)
    add_agent.add_argument("--runtime", choices=RUNTIMES, required=True)
    add_agent.add_argument("--role", required=True)
    add_agent.add_argument("--parent-agent-id")
    add_agent.add_argument("--delegation-depth", type=int, default=0)
    add_agent.add_argument("--readable-path", action="append", default=[])
    add_agent.add_argument("--writable-path", action="append", default=[])
    add_agent.add_argument("--forbidden-path", action="append", default=[])
    add_agent.add_argument("--handoff-to")
    add_agent.add_argument("--capability", action="append", default=[])

    create_task = subparsers.add_parser("create-task", help="Freeze one draft task")
    add_common_run_argument(create_task)
    create_task.add_argument("--task-id", required=True)
    create_task.add_argument("--title", required=True)
    create_task.add_argument("--objective", required=True)
    create_task.add_argument("--owner-agent", required=True)
    create_task.add_argument("--reviewer-agent")
    create_task.add_argument("--qa-agent")
    create_task.add_argument("--release-agent")
    create_task.add_argument("--dependency", action="append", default=[])
    create_task.add_argument("--owned-path", action="append", default=[])
    create_task.add_argument("--forbidden-path", action="append", default=[])
    create_task.add_argument("--risk-flag", action="append", default=[])
    create_task.add_argument("--human-gate", action="append", default=[])
    create_task.add_argument("--acceptance", action="append", default=[])
    create_task.add_argument("--verification", action="append", default=[])
    create_task.add_argument(
        "--resource-step",
        action="append",
        default=[],
        help="JSON object describing one resource step, e.g. '{\"step_id\":\"lock\",\"resources\":[\"logical:x\"],\"required\":true}'",
    )
    create_task.add_argument(
        "--assignment-mode",
        choices=("fixed", "claimable"),
        default="fixed",
    )
    create_task.add_argument("--eligible-agent", action="append", default=[])
    create_task.add_argument("--published-by")
    create_task.add_argument("--parent-task")

    gate = subparsers.add_parser("record-gate", help="Record a human gate decision")
    add_common_run_argument(gate)
    gate.add_argument("--gate-id", required=True)
    gate.add_argument("--scope", required=True)
    gate.add_argument("--status", choices=("pending", "approved", "rejected"), required=True)
    gate.add_argument("--task-id")
    gate.add_argument("--decided-by", default="user")
    gate.add_argument("--summary", required=True)
    gate.add_argument(
        "--human-confirmed",
        action="store_true",
        help="Required when recording an approved or rejected human decision",
    )

    evidence = subparsers.add_parser("record-evidence", help="Record immutable review or QA evidence")
    add_common_run_argument(evidence)
    evidence.add_argument("--evidence-id", required=True)
    evidence.add_argument(
        "--kind",
        choices=(
            "review",
            "qa",
            "security",
            "git_status",
            "environment_impact",
            "rollback",
            "release",
            "verification",
        ),
        required=True,
    )
    evidence.add_argument("--status", required=True)
    evidence.add_argument("--task-id")
    evidence.add_argument("--agent-id", required=True)
    evidence.add_argument("--summary", required=True)
    evidence.add_argument("--artifact-ref", action="append", default=[])
    evidence.add_argument("--attempt-id", default="ATTEMPT-001")
    add_activity_bridge_arguments(evidence)

    configure = subparsers.add_parser("configure-run", help="Set governed manifest fields")
    add_common_run_argument(configure)
    configure.add_argument(
        "--set",
        action="append",
        default=[],
        metavar="FIELD=VALUE",
        help=f"Allowed fields: {', '.join(sorted(RUN_CONFIG_FIELDS))}",
    )

    candidate = subparsers.add_parser(
        "record-release-candidate",
        help="Record the next immutable release candidate under Coordinator governance",
    )
    add_common_run_argument(candidate)
    candidate.add_argument("--summary", required=True)
    candidate.add_argument("--implementation-commit")
    candidate.add_argument("--artifact-ref", action="append", default=[])

    ack = subparsers.add_parser("write-ack", help="Write the task owner's ACK document")
    add_common_run_argument(ack)
    ack.add_argument("--task-id", required=True)
    ack.add_argument("--agent-id", required=True)
    ack.add_argument("--attempt-id", default="ATTEMPT-001")
    ack.add_argument("--lease-seconds", type=int)
    ack.add_argument("--idempotency-key", required=True)
    add_activity_bridge_arguments(ack)

    lease = subparsers.add_parser("write-lease", help="Write an immutable lease or renewal")
    add_common_run_argument(lease)
    lease.add_argument("--task-id", required=True)
    lease.add_argument("--agent-id", required=True)
    lease.add_argument("--lease-id", required=True)
    lease.add_argument("--attempt-id", default="ATTEMPT-001")
    lease.add_argument("--lease-seconds", type=int)
    add_activity_bridge_arguments(lease)

    result = subparsers.add_parser("write-result", help="Write one immutable owner result")
    add_common_run_argument(result)
    result.add_argument("--task-id", required=True)
    result.add_argument("--agent-id", required=True)
    result.add_argument("--attempt-id", default="ATTEMPT-001")
    result.add_argument("--status", choices=("completed", "blocked", "failed"), required=True)
    result.add_argument("--outcome", required=True)
    result.add_argument("--changed-file", action="append", default=[])
    result.add_argument("--implementation-commit")
    result.add_argument("--uncommitted-reason")
    result.add_argument(
        "--verification-status",
        choices=("passed", "failed", "not_run"),
        required=True,
    )
    result.add_argument("--verification-ref", action="append", default=[])
    result.add_argument("--risk-summary", required=True)
    result.add_argument("--rollback-plan", required=True)
    result.add_argument("--handoff-to")
    add_activity_bridge_arguments(result)

    dead_letter = subparsers.add_parser(
        "write-dead-letter",
        help="Write an immutable dead-letter record after retry exhaustion",
    )
    add_common_run_argument(dead_letter)
    dead_letter.add_argument("--task-id", required=True)
    dead_letter.add_argument("--failed-event-id", required=True)
    dead_letter.add_argument("--attempts", type=int, required=True)
    dead_letter.add_argument("--reason", required=True)
    dead_letter.add_argument(
        "--side-effect-state",
        choices=("none", "unknown", "confirmed"),
        required=True,
    )
    dead_letter.add_argument("--recovery-owner", default="coordinator")
    dead_letter.add_argument("--requires-human", action="store_true")

    lock = subparsers.add_parser("lock", help="Acquire, renew, or release a resource lock")
    add_common_run_argument(lock)
    lock.add_argument("action", choices=("acquire", "renew", "release"))
    lock.add_argument("--lock-id", required=True)
    lock.add_argument("--task-id")
    lock.add_argument("--agent-id")
    lock.add_argument("--resource")
    lock.add_argument("--step-id")
    lock.add_argument("--queue-key")
    lock.add_argument("--lease-seconds", type=int, default=1800)

    rebuild = subparsers.add_parser("rebuild-state", help="Replay events into state.yaml")
    add_common_run_argument(rebuild)

    archive = subparsers.add_parser("archive-run", help="Validate and archive a finished run")
    add_common_run_argument(archive)

    return parser.parse_args()


def load_run(run_dir_value: str) -> tuple[Path, Path, dict[str, str], Path, dict[str, dict[str, object]]]:
    run_dir = Path(run_dir_value).expanduser().resolve()
    manifest_path = run_dir / "manifest.yaml"
    agents_path = run_dir / "agents.yaml"
    project_path = run_dir.parent.parent / "project.yaml"
    if not manifest_path.is_file() or not agents_path.is_file() or not project_path.is_file():
        raise SystemExit(f"invalid protocol v3 run: {run_dir}")
    try:
        manifest = scalar_map(manifest_path.read_text(encoding="utf-8"), source=str(manifest_path))
        agents = parse_agent_profiles(agents_path.read_text(encoding="utf-8"), source=str(agents_path))
    except ProtocolError as exc:
        raise SystemExit(str(exc)) from exc
    if manifest.get("protocol_version") != PROTOCOL_VERSION:
        raise SystemExit(f"manifest protocol_version must be {PROTOCOL_VERSION}")
    if manifest.get("status") == "archived" or (
        run_dir / "archive" / "ARCHIVED.yaml"
    ).is_file():
        raise SystemExit("archived run is read-only")
    return run_dir, manifest_path, manifest, project_path, agents


def project_context(project_path: Path) -> tuple[Path, list[str]]:
    project = scalar_map(project_path.read_text(encoding="utf-8"), source=str(project_path))
    project_root = Path(project["project_root"]).resolve()
    allowed_roots = json_string_list(
        project.get("allowed_roots", "[]"),
        field="allowed_roots",
        source=str(project_path),
    )
    return project_root, allowed_roots


def record_source_activity(
    args: argparse.Namespace,
    *,
    manifest: dict[str, str],
    source_path: Path,
    source_kind: str,
    record_kind: str,
    attempt_status: str,
    task_status: str,
    outcome: str | None,
    summary: str,
    evidence_refs: list[dict[str, object]] | None = None,
) -> None:
    supplied = (
        args.activity_project_root,
        args.activity_session_id,
        args.activity_runtime_profile_ref,
    )
    if not any(supplied):
        return
    if not all(supplied):
        raise SystemExit("activity bridge requires project root, session id, and runtime profile ref")
    governance_project = Path(args.run_dir).expanduser().resolve().parent.parent
    activity_agent = (governance_project / "agents" / args.agent_id).resolve()
    profile = (activity_agent / args.activity_runtime_profile_ref).resolve()
    if not profile.is_relative_to(activity_agent):
        raise SystemExit("activity runtime profile must stay inside the governance Agent store")
    if not profile.is_file():
        raise SystemExit("activity runtime profile does not exist")
    try:
        snapshot = json.loads(profile.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit("activity runtime profile is invalid") from exc

    def known(field: str) -> str | None:
        value = snapshot.get(field)
        return str(value.get("value")) if isinstance(value, dict) and value.get("status") == "known" else None

    payload = {
        "schema_version": 1,
        "record_kind": record_kind,
        "recorded_at": now_iso(),
        "run_id": manifest["run_id"],
        "task_id": args.task_id,
        "attempt_id": args.attempt_id,
        "agent_id": args.agent_id,
        "session_id": args.activity_session_id,
        "parent_agent_id": None,
        "runtime_profile": {
            "runtime": known("runtime_kind") or "document",
            "provider": known("provider"),
            "model": known("model"),
            "profile_name": known("profile"),
            "node_id": None,
            "host_fingerprint": None,
            "native_binding_ref": args.activity_runtime_profile_ref,
            "native_binding_sha256": sha256(profile),
        },
        "status": {
            "attempt_status": attempt_status,
            "task_status_observed": task_status,
            "outcome": outcome,
            "reason_code": None,
            "summary": summary,
        },
        "tool_summary": None,
        "verification": None,
        "artifacts": [],
        "evidence_refs": evidence_refs or [],
        "usage": {
            "input_tokens": None, "output_tokens": None, "cached_input_tokens": None,
            "reasoning_tokens": None, "total_tokens": None, "cost_minor_units": None,
            "currency": None, "usage_source": "unavailable", "source_ref": None,
            "source_sha256": None, "reported_at": None,
        },
        "source": {
            "source_kind": source_kind,
            "source_ref": source_path.relative_to(Path(args.activity_project_root).resolve()).as_posix()
            if source_path.is_relative_to(Path(args.activity_project_root).resolve())
            else source_path.relative_to(Path(args.run_dir).resolve()).as_posix(),
            "source_sha256": sha256(source_path),
            "source_event_id": None,
            "correlation_id": f"{manifest['run_id']}:{args.task_id}:{args.attempt_id}",
            "causation_id": None,
        },
        "supersedes_record_sha256": None,
    }
    try:
        record_agent_activity(
            project_root=args.activity_project_root,
            agent_id=args.agent_id,
            payload=payload,
            governance_root=governance_project.parent.parent,
        )
    except Exception as exc:
        code = exc.args[0] if getattr(exc, "args", None) else type(exc).__name__
        raise SystemExit(f"activity bridge write failed: {code}") from exc


def write_source_with_activity(path: Path, content: str, callback) -> None:
    atomic_write(path, content)
    try:
        callback()
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def load_version_contract(
    run_dir: Path,
    manifest: dict[str, str],
) -> tuple[Path, dict[str, str]]:
    contract_path = Path(manifest.get("version_contract_ref", "")).expanduser()
    if not contract_path.is_absolute():
        contract_path = run_dir / contract_path
    contract_path = contract_path.resolve()
    expected_path = (run_dir / "versions" / "version-contract.yaml").resolve()
    if contract_path != expected_path or not contract_path.is_file():
        raise SystemExit("run requires its canonical version contract")
    if manifest.get("version_contract_ref_sha256") != sha256(contract_path):
        raise SystemExit("version contract hash mismatch")
    try:
        contract = scalar_map(
            contract_path.read_text(encoding="utf-8"),
            source=str(contract_path),
        )
    except ProtocolError as exc:
        raise SystemExit(str(exc)) from exc
    expected = {
        "protocol_version": PROTOCOL_VERSION,
        "kind": "version_contract",
        "run_id": manifest["run_id"],
        "release_train_id": manifest["release_train_id"],
        "versioning_mode": manifest["versioning_mode"],
        "baseline_version": manifest["baseline_version"],
        "target_version": manifest["target_version"],
        "owner_agent": "coordinator",
    }
    if any(contract.get(key) != value for key, value in expected.items()):
        raise SystemExit("version contract does not match manifest")
    return contract_path, contract


def render_agent_block(args: argparse.Namespace, project_root: Path) -> str:
    readable = args.readable_path or [str(project_root)]
    writable = args.writable_path
    return "\n".join(
        (
            f"  - agent_id: {quote(args.agent_id)}",
            f"    runtime: {quote(args.runtime)}",
            f"    role: {quote(args.role)}",
            '    status: "ready"',
            f"    parent_agent_id: {quote(args.parent_agent_id) if args.parent_agent_id else 'null'}",
            f"    delegation_depth: {args.delegation_depth}",
            "    readable_paths:",
            *[f"      - {quote(path)}" for path in readable],
            "    writable_paths:",
            *([f"      - {quote(path)}" for path in writable] or ["      - null"]),
            "    forbidden_paths:",
            *([f"      - {quote(path)}" for path in args.forbidden_path] or ["      - null"]),
            f"    capabilities: {json.dumps(sorted(set(args.capability)), ensure_ascii=False)}",
            "    thread_id: null",
            f"    inbox: {quote(f'inbox/{args.agent_id}')}",
            f"    outbox: {quote(f'outbox/{args.agent_id}')}",
            "    current_task: null",
            f"    handoff_to: {quote(args.handoff_to) if args.handoff_to else 'null'}",
            "",
        )
    ).replace("    writable_paths:\n      - null", "    writable_paths: []").replace(
        "    forbidden_paths:\n      - null", "    forbidden_paths: []"
    )


def command_add_agent(args: argparse.Namespace) -> None:
    run_dir, _, manifest, project_path, agents = load_run(args.run_dir)
    safe_identifier(args.agent_id, "agent id")
    if args.agent_id in agents:
        raise SystemExit(f"agent already exists: {args.agent_id}")
    project_root, allowed_roots = project_context(project_path)
    readable_paths = args.readable_path or [str(project_root)]
    for path in readable_paths + args.writable_path:
        if not path_within(path, allowed_roots, project_root):
            raise SystemExit(f"agent path exceeds project allowed_roots: {path}")
    for path in args.writable_path:
        if not path_within(path, readable_paths, project_root):
            raise SystemExit(f"writable path exceeds readable scope: {path}")
    if args.runtime == "document_subagent":
        parent = agents.get(args.parent_agent_id or "")
        if not parent:
            raise SystemExit("document_subagent requires a registered parent")
        parent_depth = int(str(parent.get("delegation_depth", "0")))
        if args.delegation_depth != parent_depth + 1:
            raise SystemExit("document_subagent depth must be parent depth + 1")
        if args.delegation_depth > int(manifest["max_document_delegation_depth"]):
            raise SystemExit("document_subagent depth exceeds run policy")
        for path in args.readable_path:
            if not path_within(path, [str(item) for item in parent.get("readable_paths", [])], project_root):
                raise SystemExit(f"readable path exceeds parent scope: {path}")
        for path in args.writable_path:
            if not path_within(path, [str(item) for item in parent.get("writable_paths", [])], project_root):
                raise SystemExit(f"writable path exceeds parent scope: {path}")
        inherited_forbidden = [str(item) for item in parent.get("forbidden_paths", [])]
        for path in inherited_forbidden:
            if not path_within(path, args.forbidden_path, project_root):
                args.forbidden_path.append(path)
    for path in args.forbidden_path:
        if not path_within(path, allowed_roots, project_root):
            raise SystemExit(f"forbidden path exceeds project allowed_roots: {path}")
    agents_path = run_dir / "agents.yaml"
    inbox_path = run_dir / "inbox" / args.agent_id
    outbox_path = run_dir / "outbox" / args.agent_id
    if inbox_path.exists() or outbox_path.exists():
        raise SystemExit("agent inbox or outbox already exists")
    new_registry = (
        agents_path.read_text(encoding="utf-8").rstrip()
        + "\n"
        + render_agent_block(args, project_root)
    )
    inbox_path.mkdir(parents=False, exist_ok=False)
    try:
        outbox_path.mkdir(parents=False, exist_ok=False)
        atomic_write(agents_path, new_registry)
    except Exception:
        if outbox_path.is_dir():
            outbox_path.rmdir()
        if inbox_path.is_dir():
            inbox_path.rmdir()
        raise
    print(args.agent_id)


def task_path_for(run_dir: Path, task_id: str) -> Path:
    path = run_dir / "tasks" / f"{task_id}.md"
    if not path.is_file():
        raise SystemExit(f"task does not exist: {task_id}")
    return path


def update_manifest_list(manifest_path: Path, field: str, value: str) -> None:
    manifest = scalar_map(manifest_path.read_text(encoding="utf-8"), source=str(manifest_path))
    items = json_string_list(manifest.get(field, "[]"), field=field, source=str(manifest_path))
    if value not in items:
        items.append(value)
    replace_flat_scalar(
        manifest_path,
        field,
        json.dumps(sorted(items), ensure_ascii=False),
    )


def command_create_task(args: argparse.Namespace) -> None:
    run_dir, manifest_path, manifest, project_path, agents = load_run(args.run_dir)
    contract_path, _ = load_version_contract(run_dir, manifest)
    safe_identifier(args.task_id, "task id")
    if args.assignment_mode == "fixed" and args.owner_agent not in agents:
        raise SystemExit(f"owner is not registered: {args.owner_agent}")
    if args.assignment_mode == "claimable" and args.owner_agent != "pool":
        raise SystemExit("claimable tasks must use owner-agent pool")
    published_by = args.published_by or "coordinator"
    if published_by not in agents:
        raise SystemExit(f"publisher is not registered: {published_by}")
    if args.assignment_mode == "claimable" and not args.eligible_agent:
        raise SystemExit("claimable tasks require at least one eligible-agent")
    if len(args.eligible_agent) != len(set(args.eligible_agent)):
        raise SystemExit("eligible-agent values must be unique")
    for eligible in args.eligible_agent:
        if eligible not in agents:
            raise SystemExit(f"eligible agent is not registered: {eligible}")
    parent_path: Path | None = None
    parent_hash = "null"
    if args.parent_task:
        parent_path = task_path_for(run_dir, args.parent_task)
        parent_hash = sha256(parent_path)
    for role in (args.reviewer_agent, args.qa_agent, args.release_agent):
        if role and role not in agents:
            raise SystemExit(f"task role is not registered: {role}")
    task_path = run_dir / "tasks" / f"{args.task_id}.md"
    if task_path.exists():
        raise SystemExit(f"task already exists: {task_path}")
    existing_tasks = json_string_list(
        manifest.get("tasks", "[]"),
        field="tasks",
        source=str(manifest_path),
    )
    for field, items in (
        ("dependency", args.dependency),
        ("owned path", args.owned_path),
        ("forbidden path", args.forbidden_path),
        ("risk flag", args.risk_flag),
        ("human gate", args.human_gate),
    ):
        if len(items) != len(set(items)):
            raise SystemExit(f"{field} values must be unique")
    resource_steps: list[dict[str, object]] = []
    for raw_step in args.resource_step:
        try:
            parsed_step = json.loads(raw_step)
        except json.JSONDecodeError as exc:
            raise SystemExit("resource-step must be an inline JSON object") from exc
        if not isinstance(parsed_step, dict) or not isinstance(parsed_step.get("step_id"), str):
            raise SystemExit("resource-step requires a string step_id")
        resources = parsed_step.get("resources", [])
        if not isinstance(resources, list) or not all(isinstance(item, str) and item for item in resources):
            raise SystemExit("resource-step resources must be a non-empty string list")
        if parsed_step["step_id"] in {str(item.get("step_id")) for item in resource_steps}:
            raise SystemExit("resource-step step_id values must be unique")
        parsed_step["resources"] = sorted(set(resources))
        parsed_step["required"] = bool(parsed_step.get("required", True))
        resource_steps.append(parsed_step)
    for dependency in args.dependency:
        if dependency not in existing_tasks:
            raise SystemExit(f"dependency must already exist: {dependency}")
    project_root, allowed_roots = project_context(project_path)
    owner = agents.get(args.owner_agent, {"writable_paths": [], "forbidden_paths": []})
    eligible_profiles = [agents[item] for item in args.eligible_agent if item in agents]
    effective_forbidden = list(args.forbidden_path)
    for path in owner.get("forbidden_paths", []):
        if not path_within(str(path), effective_forbidden, project_root):
            effective_forbidden.append(str(path))
    if args.assignment_mode == "claimable":
        for eligible_profile in eligible_profiles:
            for path in eligible_profile.get("forbidden_paths", []):
                if not path_within(str(path), effective_forbidden, project_root):
                    effective_forbidden.append(str(path))
    for forbidden_path in effective_forbidden:
        if not path_within(forbidden_path, allowed_roots, project_root):
            raise SystemExit(
                f"forbidden path exceeds project allowed_roots: {forbidden_path}"
            )
    for owned_path in args.owned_path:
        if args.assignment_mode == "fixed":
            writable_scopes = [str(item) for item in owner.get("writable_paths", [])]
            if not path_within(owned_path, writable_scopes, project_root):
                raise SystemExit(f"owned path exceeds owner writable scope: {owned_path}")
        else:
            writable_scopes = [
                str(scope)
                for profile in eligible_profiles
                for scope in profile.get("writable_paths", [])
            ]
            if not writable_scopes or any(
                not path_within(owned_path, [str(scope) for scope in profile.get("writable_paths", [])], project_root)
                for profile in eligible_profiles
            ):
                raise SystemExit("claimable owned paths must be writable by every eligible agent")
        if path_within(
            owned_path,
            [str(item) for item in effective_forbidden],
            project_root,
        ):
            raise SystemExit(f"owned path is forbidden for owner: {owned_path}")
    for existing_task_id in existing_tasks:
        existing_path = run_dir / "tasks" / f"{existing_task_id}.md"
        existing = frontmatter(existing_path)
        existing_owned = json_string_list(
            existing.get("owned_paths", "[]"),
            field="owned_paths",
            source=str(existing_path),
        )
        if any(
            paths_overlap(new_path, old_path, project_root)
            for new_path in args.owned_path
            for old_path in existing_owned
        ) and existing_task_id not in args.dependency:
            raise SystemExit(
                f"owned_paths overlap {existing_task_id}; add it as a dependency to serialize"
            )
    created_at = now_iso()
    task_idempotency_key = f"{manifest['run_id']}:{args.task_id}:v1"
    human_gate_hashes: dict[str, str] = {}
    for gate_id in args.human_gate:
        gate_path = run_dir / "decisions" / f"{gate_id}.yaml"
        if not gate_path.is_file():
            raise SystemExit(f"declared human gate does not exist: {gate_id}")
        gate_values = scalar_map(
            gate_path.read_text(encoding="utf-8"),
            source=str(gate_path),
        )
        if gate_values.get("gate_id") != gate_id:
            raise SystemExit(f"human gate file identity mismatch: {gate_id}")
        human_gate_hashes[gate_id] = sha256(gate_path)
    content = "\n".join(
        (
            "---",
            f"protocol_version: {PROTOCOL_VERSION}",
            f"run_id: {quote(manifest['run_id'])}",
            f"task_id: {quote(args.task_id)}",
            f"title: {quote(args.title)}",
            'status: "draft"',
            f"owner_agent: {quote(args.owner_agent)}",
            f"assignment_mode: {quote(args.assignment_mode)}",
            f"eligible_agents: {json.dumps(args.eligible_agent, ensure_ascii=False)}",
            f"published_by: {quote(published_by)}",
            f"parent_task_id: {quote(args.parent_task) if args.parent_task else 'null'}",
            f"parent_task_sha256: {quote(parent_hash)}",
            f"reviewer_agent: {quote(args.reviewer_agent) if args.reviewer_agent else 'null'}",
            f"qa_agent: {quote(args.qa_agent) if args.qa_agent else 'null'}",
            f"release_agent: {quote(args.release_agent) if args.release_agent else 'null'}",
            f"release_train_id: {quote(manifest['release_train_id'])}",
            f"delivery_version: {quote(manifest['target_version']) if manifest['target_version'] != 'null' else 'null'}",
            f"version_contract_sha256: {quote(sha256(contract_path))}",
            f"dependencies: {json.dumps(args.dependency, ensure_ascii=False)}",
            f"owned_paths: {json.dumps(args.owned_path, ensure_ascii=False)}",
            f"forbidden_paths: {json.dumps(effective_forbidden, ensure_ascii=False)}",
            f"resource_steps: {json.dumps(resource_steps, ensure_ascii=False, sort_keys=True)}",
            f"risk_flags: {json.dumps(args.risk_flag, ensure_ascii=False)}",
            f"human_gates: {json.dumps(args.human_gate, ensure_ascii=False)}",
            f"human_gate_hashes: {json.dumps(human_gate_hashes, ensure_ascii=False, sort_keys=True)}",
            f"idempotency_key: {quote(task_idempotency_key)}",
            f"created_at: {quote(created_at)}",
            "---",
            "",
            "# Objective",
            "",
            args.objective,
            "",
            "# Acceptance Criteria",
            "",
            *([f"- {item}" for item in args.acceptance] or ["- Define observable pass/fail conditions."]),
            "",
            "# Verification",
            "",
            *([f"- {item}" for item in args.verification] or ["- Record required commands and evidence."]),
            "",
            "# Boundaries",
            "",
            "- Preserve unrelated changes.",
            "- Do not exceed owned_paths.",
            "- Stop at human gates and prohibited operations.",
            "",
        )
    )
    atomic_write(task_path, content)
    update_manifest_list(manifest_path, "tasks", args.task_id)
    print(task_path)


def command_record_release_candidate(args: argparse.Namespace) -> None:
    run_dir, manifest_path, manifest, project_path, _ = load_run(args.run_dir)
    contract_path, _ = load_version_contract(run_dir, manifest)
    if manifest.get("versioning_mode") != "tracked":
        raise SystemExit("release candidates require tracked versioning")
    project_root, allowed_roots = project_context(project_path)
    candidate_ids = json_string_list(
        manifest.get("release_candidates", "[]"),
        field="release_candidates",
        source=str(manifest_path),
    )
    next_number = len(candidate_ids) + 1
    candidate_id = f"RC-{next_number:03d}"
    if candidate_id in candidate_ids:
        raise SystemExit(f"release candidate already exists: {candidate_id}")
    artifact_refs: list[str] = []
    artifact_hashes: dict[str, str] = {}
    for value in args.artifact_ref:
        reference = Path(value).expanduser()
        if not reference.is_absolute():
            reference = run_dir / reference
        reference = reference.resolve()
        if not reference.is_file():
            raise SystemExit(f"candidate artifact does not exist: {reference}")
        if not path_within(reference, [run_dir, *allowed_roots], project_root):
            raise SystemExit(f"candidate artifact exceeds run and project roots: {reference}")
        rendered = str(reference)
        if rendered in artifact_refs:
            raise SystemExit(f"duplicate candidate artifact: {rendered}")
        artifact_refs.append(rendered)
        artifact_hashes[rendered] = sha256(reference)
    target_version = manifest["target_version"]
    candidate_version = f"{target_version}-rc.{next_number}"
    candidate_path = run_dir / "versions" / "candidates" / f"{candidate_id}.yaml"
    atomic_write(
        candidate_path,
        "\n".join(
            (
                f"protocol_version: {PROTOCOL_VERSION}",
                'kind: "release_candidate"',
                f"run_id: {quote(manifest['run_id'])}",
                f"release_train_id: {quote(manifest['release_train_id'])}",
                f"candidate_id: {quote(candidate_id)}",
                f"candidate_version: {quote(candidate_version)}",
                f"target_version: {quote(target_version)}",
                f"version_contract_sha256: {quote(sha256(contract_path))}",
                'owner_agent: "coordinator"',
                f"implementation_commit: {quote(args.implementation_commit) if args.implementation_commit else 'null'}",
                f"artifact_refs: {json.dumps(artifact_refs, ensure_ascii=False)}",
                f"artifact_hashes: {json.dumps(artifact_hashes, ensure_ascii=False, sort_keys=True)}",
                f"summary: {quote(args.summary)}",
                f"created_at: {quote(now_iso())}",
                "",
            )
        ),
    )
    update_manifest_list(manifest_path, "release_candidates", candidate_id)
    print(candidate_path)


def command_record_gate(args: argparse.Namespace) -> None:
    run_dir, manifest_path, manifest, _, _ = load_run(args.run_dir)
    safe_identifier(args.gate_id, "gate id")
    if args.status in {"approved", "rejected"} and not args.human_confirmed:
        raise SystemExit(
            "--human-confirmed is required for approved or rejected human gates"
        )
    if args.task_id:
        task_path_for(run_dir, args.task_id)
    path = run_dir / "decisions" / f"{args.gate_id}.yaml"
    if path.exists():
        raise SystemExit(f"gate already exists; decisions are immutable: {path}")
    created_at = now_iso()
    atomic_write(
        path,
        "\n".join(
            (
                f"protocol_version: {PROTOCOL_VERSION}",
                'kind: "human_gate"',
                f"run_id: {quote(manifest['run_id'])}",
                f"gate_id: {quote(args.gate_id)}",
                f"task_id: {quote(args.task_id) if args.task_id else 'null'}",
                f"scope: {quote(args.scope)}",
                f"status: {quote(args.status)}",
                f"approved_by: {quote(args.decided_by)}",
                f"approved_at: {quote(created_at)}",
                f"summary: {quote(args.summary)}",
                "",
            )
        ),
    )
    update_manifest_list(manifest_path, "human_gates", args.gate_id)
    print(path)


def command_record_evidence(args: argparse.Namespace) -> None:
    run_dir, _, manifest, project_path, agents = load_run(args.run_dir)
    safe_identifier(args.evidence_id, "evidence id")
    if args.agent_id not in agents:
        raise SystemExit(f"agent is not registered: {args.agent_id}")
    if args.task_id:
        task_path_for(run_dir, args.task_id)
    path = run_dir / "evidence" / f"{args.evidence_id}.yaml"
    if path.exists():
        raise SystemExit(f"evidence already exists; evidence is immutable: {path}")
    project_root, allowed_roots = project_context(project_path)
    artifact_hashes: dict[str, str] = {}
    for reference in args.artifact_ref:
        ref_path = Path(reference).expanduser()
        if not ref_path.is_absolute():
            ref_path = run_dir / ref_path
        ref_path = ref_path.resolve()
        if not ref_path.is_file():
            raise SystemExit(f"artifact reference does not exist: {reference}")
        if not path_within(ref_path, [run_dir, *allowed_roots], project_root):
            raise SystemExit(f"artifact reference exceeds run and project roots: {reference}")
        artifact_hashes[reference] = sha256(ref_path)
    content = "\n".join(
            (
                f"protocol_version: {PROTOCOL_VERSION}",
                f"kind: {quote(args.kind)}",
                f"run_id: {quote(manifest['run_id'])}",
                f"evidence_id: {quote(args.evidence_id)}",
                f"task_id: {quote(args.task_id) if args.task_id else 'null'}",
                f"agent_id: {quote(args.agent_id)}",
                f"status: {quote(args.status)}",
                f"summary: {quote(args.summary)}",
                f"artifact_refs: {json.dumps(args.artifact_ref, ensure_ascii=False)}",
                f"artifact_hashes: {json.dumps(artifact_hashes, ensure_ascii=False, sort_keys=True)}",
                f"created_at: {quote(now_iso())}",
                "",
            )
    )
    write_source_with_activity(path, content, lambda: record_source_activity(
        args, manifest=manifest, source_path=path, source_kind="evidence", record_kind="artifact_evidence",
        attempt_status="reviewing", task_status="reviewing", outcome=None, summary=args.summary,
        evidence_refs=[{"evidence_id": args.evidence_id, "kind": args.kind,
            "ref": path.relative_to(run_dir).as_posix(), "sha256": sha256(path),
            "status": args.status if args.status in {"passed", "failed", "not_run", "inconclusive"} else "inconclusive"}]))
    print(path)


def command_configure_run(args: argparse.Namespace) -> None:
    run_dir, manifest_path, _, project_path, _ = load_run(args.run_dir)
    project_root, allowed_roots = project_context(project_path)
    if not args.set:
        raise SystemExit("configure-run requires at least one --set FIELD=VALUE")
    updates: dict[str, str] = {}
    for assignment in args.set:
        if "=" not in assignment:
            raise SystemExit(f"invalid assignment: {assignment}")
        field, value = assignment.split("=", 1)
        if field not in RUN_CONFIG_FIELDS:
            raise SystemExit(f"field cannot be configured: {field}")
        if field in updates:
            raise SystemExit(f"field configured more than once: {field}")
        if field.endswith("_ref"):
            reference = Path(value).expanduser()
            if not reference.is_absolute():
                reference = run_dir / reference
            reference = reference.resolve()
            if not reference.is_file():
                raise SystemExit(f"reference does not exist: {reference}")
            if not path_within(reference, [run_dir, *allowed_roots], project_root):
                raise SystemExit(f"reference exceeds run and project roots: {reference}")
            value = str(reference)
        updates[field] = quote(value)
        if field.endswith("_ref"):
            updates[f"{field}_sha256"] = quote(sha256(Path(value)))
    content = manifest_path.read_text(encoding="utf-8")
    for field, rendered_value in updates.items():
        content, count = re.subn(
            rf"^{re.escape(field)}:\s*.*$",
            f"{field}: {rendered_value}",
            content,
            flags=re.MULTILINE,
        )
        if count != 1:
            raise SystemExit(f"manifest must contain exactly one {field} field")
    atomic_write(manifest_path, content)
    print(manifest_path)


def command_write_ack(args: argparse.Namespace) -> None:
    run_dir, _, manifest, _, agents = load_run(args.run_dir)
    task_path = task_path_for(run_dir, args.task_id)
    task = frontmatter(task_path)
    if effective_owner(run_dir, task) != args.agent_id or args.agent_id not in agents:
        raise SystemExit("ACK agent must be the registered task owner")
    lease_seconds = args.lease_seconds or int(manifest["lease_seconds"])
    acknowledged_at = datetime.now().astimezone()
    lease_expires = acknowledged_at + timedelta(seconds=lease_seconds)
    safe_identifier(args.attempt_id, "attempt id")
    path = (
        run_dir
        / "outbox"
        / args.agent_id
        / f"{args.task_id}-ack-{args.attempt_id}.yaml"
    )
    if path.exists():
        raise SystemExit(f"ACK already exists and is immutable: {path}")
    content = "\n".join(
            (
                f"protocol_version: {PROTOCOL_VERSION}",
                'kind: "ack"',
                f"run_id: {quote(manifest['run_id'])}",
                f"task_id: {quote(args.task_id)}",
                f"task_sha256: {quote(sha256(task_path))}",
                f"agent_id: {quote(args.agent_id)}",
                f"attempt_id: {quote(args.attempt_id)}",
                f"acknowledged_at: {quote(acknowledged_at.isoformat(timespec='seconds'))}",
                f"lease_expires_at: {quote(lease_expires.isoformat(timespec='seconds'))}",
                f"idempotency_key: {quote(args.idempotency_key)}",
                "",
            )
    )
    write_source_with_activity(path, content, lambda: record_source_activity(
        args, manifest=manifest, source_path=path, source_kind="ack", record_kind="attempt_started",
        attempt_status="acknowledged", task_status="acknowledged", outcome=None, summary="Task attempt acknowledged"))
    print(path)


def command_write_lease(args: argparse.Namespace) -> None:
    run_dir, _, manifest, _, agents = load_run(args.run_dir)
    safe_identifier(args.lease_id, "lease id")
    safe_identifier(args.attempt_id, "attempt id")
    task_path = task_path_for(run_dir, args.task_id)
    task = frontmatter(task_path)
    if effective_owner(run_dir, task) != args.agent_id or args.agent_id not in agents:
        raise SystemExit("lease agent must be the registered task owner")
    lease_seconds = args.lease_seconds or int(manifest["lease_seconds"])
    acquired_at = datetime.now().astimezone()
    lease_expires = acquired_at + timedelta(seconds=lease_seconds)
    path = (
        run_dir
        / "outbox"
        / args.agent_id
        / f"{args.task_id}-lease-{args.attempt_id}-{args.lease_id}.yaml"
    )
    if path.exists():
        raise SystemExit(f"lease already exists and is immutable: {path}")
    content = "\n".join(
            (
                f"protocol_version: {PROTOCOL_VERSION}",
                'kind: "lease"',
                f"run_id: {quote(manifest['run_id'])}",
                f"task_id: {quote(args.task_id)}",
                f"task_sha256: {quote(sha256(task_path))}",
                f"agent_id: {quote(args.agent_id)}",
                f"attempt_id: {quote(args.attempt_id)}",
                f"lease_id: {quote(args.lease_id)}",
                f"acquired_at: {quote(acquired_at.isoformat(timespec='seconds'))}",
                f"lease_expires_at: {quote(lease_expires.isoformat(timespec='seconds'))}",
                "",
            )
    )
    write_source_with_activity(path, content, lambda: record_source_activity(
        args, manifest=manifest, source_path=path, source_kind="lease", record_kind="status_transition",
        attempt_status="running", task_status="running", outcome=None, summary=f"Lease {args.lease_id} acquired"))
    print(path)


def command_write_result(args: argparse.Namespace) -> None:
    run_dir, _, manifest, _, agents = load_run(args.run_dir)
    task_path = task_path_for(run_dir, args.task_id)
    task = frontmatter(task_path)
    if effective_owner(run_dir, task) != args.agent_id or args.agent_id not in agents:
        raise SystemExit("result agent must be the registered task owner")
    if args.implementation_commit and args.uncommitted_reason:
        raise SystemExit("choose implementation_commit or uncommitted_reason, not both")
    if (
        manifest.get("governance") in {"standard", "strict"}
        and args.verification_status == "passed"
        and not args.verification_ref
    ):
        raise SystemExit("passed verification requires at least one --verification-ref")
    for reference in args.verification_ref:
        ref_path = Path(reference).expanduser()
        if not ref_path.is_absolute():
            ref_path = run_dir / ref_path
        if not ref_path.is_file():
            raise SystemExit(f"verification reference does not exist: {reference}")
    safe_identifier(args.attempt_id, "attempt id")
    path = (
        run_dir
        / "outbox"
        / args.agent_id
        / f"{args.task_id}-result-{args.attempt_id}.md"
    )
    if path.exists():
        raise SystemExit(f"result already exists and is immutable: {path}")
    result_idempotency_key = (
        f"{manifest['run_id']}:{args.task_id}:result:{args.attempt_id}:v1"
    )
    content = "\n".join(
            (
                "---",
                f"protocol_version: {PROTOCOL_VERSION}",
                'kind: "result"',
                f"run_id: {quote(manifest['run_id'])}",
                f"task_id: {quote(args.task_id)}",
                f"task_sha256: {quote(sha256(task_path))}",
                f"agent_id: {quote(args.agent_id)}",
                f"attempt_id: {quote(args.attempt_id)}",
                f"status: {quote(args.status)}",
                f"idempotency_key: {quote(result_idempotency_key)}",
                f"created_at: {quote(now_iso())}",
                f"changed_files: {json.dumps(args.changed_file, ensure_ascii=False)}",
                f"implementation_commit: {quote(args.implementation_commit) if args.implementation_commit else 'null'}",
                f"uncommitted_reason: {quote(args.uncommitted_reason) if args.uncommitted_reason else 'null'}",
                f"verification_status: {quote(args.verification_status)}",
                f"verification_refs: {json.dumps(args.verification_ref, ensure_ascii=False)}",
                f"risk_summary: {quote(args.risk_summary)}",
                f"rollback_plan: {quote(args.rollback_plan)}",
                f"handoff_to: {quote(args.handoff_to) if args.handoff_to else 'null'}",
                "---",
                "",
                "# Outcome",
                "",
                args.outcome,
                "",
            )
    )
    attempt_status = {"completed": "completed", "blocked": "blocked", "failed": "failed"}[args.status]
    write_source_with_activity(path, content, lambda: record_source_activity(
        args, manifest=manifest, source_path=path, source_kind="result", record_kind="attempt_finished",
        attempt_status=attempt_status, task_status=attempt_status,
        outcome="success" if args.status == "completed" else "failure", summary=args.outcome))
    print(path)


def command_write_dead_letter(args: argparse.Namespace) -> None:
    run_dir, _, manifest, _, agents = load_run(args.run_dir)
    task_path_for(run_dir, args.task_id)
    if args.attempts < 1:
        raise SystemExit("dead-letter attempts must be at least 1")
    if args.recovery_owner not in agents:
        raise SystemExit("dead-letter recovery owner must be registered")
    try:
        records = event_records(run_dir / "events")
    except ProtocolError as exc:
        raise SystemExit(str(exc)) from exc
    failure_events = {
        "TASK_FAILED",
        "THREAD_FAILED",
        "SUBAGENT_FAILED",
        "DOCUMENT_SUBAGENT_FAILED",
    }
    failed_record = next(
        (
            values
            for _, values in records
            if values.get("event_id") == args.failed_event_id
        ),
        None,
    )
    if (
        not failed_record
        or failed_record.get("task_id") != args.task_id
        or failed_record.get("event") not in failure_events
    ):
        raise SystemExit("failed_event_id must identify a failure event for this task")
    actual_attempts = sum(
        1
        for _, values in records
        if values.get("task_id") == args.task_id and values.get("event") == "ACK"
    )
    max_attempts = int(manifest["max_attempts"])
    if args.attempts != actual_attempts:
        raise SystemExit("dead-letter attempts must equal event-derived ACK attempts")
    if actual_attempts < max_attempts:
        raise SystemExit("dead-letter attempts have not reached max_attempts")
    requires_human = args.requires_human or args.side_effect_state == "unknown"
    path = run_dir / "dead-letter" / f"{args.task_id}.yaml"
    if path.exists():
        raise SystemExit(f"dead-letter already exists and is immutable: {path}")
    atomic_write(
        path,
        "\n".join(
            (
                f"protocol_version: {PROTOCOL_VERSION}",
                'kind: "dead_letter"',
                f"run_id: {quote(manifest['run_id'])}",
                f"task_id: {quote(args.task_id)}",
                f"event_id: {quote(args.failed_event_id)}",
                f"attempts: {args.attempts}",
                f"last_attempt_at: {quote(now_iso())}",
                f"reason: {quote(args.reason)}",
                f"side_effect_state: {quote(args.side_effect_state)}",
                f"recovery_owner: {quote(args.recovery_owner)}",
                f"requires_human: {'true' if requires_human else 'false'}",
                "",
            )
        ),
    )
    print(path)


def lock_path(run_dir: Path, lock_id: str) -> Path:
    safe_identifier(lock_id, "lock id")
    return run_dir / "locks" / f"{lock_id}.yaml"


def command_lock(args: argparse.Namespace) -> None:
    run_dir, _, manifest, project_path, agents = load_run(args.run_dir)
    path = lock_path(run_dir, args.lock_id)
    if args.action == "acquire":
        if path.exists():
            raise SystemExit(f"lock already exists: {path}")
        if not args.task_id or not args.agent_id or not args.resource:
            raise SystemExit("acquire requires --task-id, --agent-id, and --resource")
        task = frontmatter(task_path_for(run_dir, args.task_id))
        if effective_owner(run_dir, task) != args.agent_id or args.agent_id not in agents:
            raise SystemExit("lock owner must be the task owner")
        if args.queue_key:
            safe_identifier(args.queue_key, "queue key")
            if not args.step_id:
                raise SystemExit("--queue-key requires --step-id")
            queue_dir = run_dir / "locks" / "queue"
            queued: list[tuple[datetime, str, dict[str, str]]] = []
            for request_path in sorted(queue_dir.glob("*.yaml")) if queue_dir.is_dir() else []:
                try:
                    request_values = scalar_map(request_path.read_text(encoding="utf-8"), source=str(request_path))
                    created = datetime.fromisoformat(request_values["created_at"].replace("Z", "+00:00"))
                except (OSError, KeyError, ValueError, ProtocolError):
                    continue
                grant_path = queue_dir / "grants" / f"{request_values.get('request_id', request_path.stem)}.yaml"
                if request_values.get("status") == "queued" and request_values.get("queue_key") == args.queue_key and not grant_path.is_file():
                    queued.append((created, request_values.get("request_id", request_path.name), request_values))
            queued.sort(key=lambda item: (item[0], item[1]))
            if not queued:
                raise SystemExit("queue grant requires an existing queued request")
            first = queued[0][2]
            if (first.get("task_id"), first.get("agent_id"), first.get("step_id")) != (args.task_id, args.agent_id, args.step_id):
                raise SystemExit("wait_for_queue_grant: another request owns the FIFO head")
            request_id = first.get("request_id", "")
            if not request_id:
                raise SystemExit("queue request is missing request_id")
        if args.step_id:
            safe_identifier(args.step_id, "step id")
        project_root, _ = project_context(project_path)
        owned_paths = json_string_list(
            task.get("owned_paths", "[]"),
            field="owned_paths",
            source=args.task_id,
        )
        if not args.resource.startswith("logical:") and not path_within(
            args.resource, owned_paths, project_root
        ):
            raise SystemExit("path lock resource must be within task owned_paths")
        for existing in (run_dir / "locks").glob("*.yaml"):
            values = scalar_map(existing.read_text(encoding="utf-8"), source=str(existing))
            other = values.get("resource", "")
            overlap = other == args.resource
            if not other.startswith("logical:") and not args.resource.startswith("logical:"):
                overlap = paths_overlap(other, args.resource, project_root)
            if overlap:
                raise SystemExit(f"resource conflicts with active lock: {existing}")
        acquired = datetime.now().astimezone()
        expires = acquired + timedelta(seconds=args.lease_seconds)
        atomic_write(
            path,
            "\n".join(
                (
                    f"protocol_version: {PROTOCOL_VERSION}",
                    'kind: "lock"',
                    f"run_id: {quote(manifest['run_id'])}",
                    f"lock_id: {quote(args.lock_id)}",
                    f"resource: {quote(args.resource)}",
                    f"owner_task: {quote(args.task_id)}",
                    f"owner_agent: {quote(args.agent_id)}",
                    f"step_id: {quote(args.step_id) if args.step_id else 'null'}",
                    f"queue_key: {quote(args.queue_key) if args.queue_key else 'null'}",
                    'status: "active"',
                    f"acquired_at: {quote(acquired.isoformat(timespec='seconds'))}",
                    f"lease_expires_at: {quote(expires.isoformat(timespec='seconds'))}",
                    "",
                )
            ),
        )
        if args.queue_key:
            grant_dir = run_dir / "locks" / "queue" / "grants"
            grant_path = grant_dir / f"{request_id}.yaml"
            grant_dir.mkdir(parents=True, exist_ok=True)
            if grant_path.exists():
                raise SystemExit(f"queue grant already exists: {grant_path}")
            atomic_write(
                grant_path,
                "\n".join(
                    (
                        f"protocol_version: {PROTOCOL_VERSION}",
                        'kind: "resource_grant"',
                        f"run_id: {quote(manifest['run_id'])}",
                        f"request_id: {quote(request_id)}",
                        f"task_id: {quote(args.task_id)}",
                        f"agent_id: {quote(args.agent_id)}",
                        f"step_id: {quote(args.step_id)}",
                        f"queue_key: {quote(args.queue_key)}",
                        f"lock_id: {quote(args.lock_id)}",
                        f"granted_at: {quote(acquired.isoformat(timespec='seconds'))}",
                        'status: "granted"',
                        "",
                    )
                ),
            )
    elif args.action == "renew":
        if not path.is_file():
            raise SystemExit(f"lock does not exist: {path}")
        expires = datetime.now().astimezone() + timedelta(seconds=args.lease_seconds)
        replace_flat_scalar(
            path,
            "lease_expires_at",
            quote(expires.isoformat(timespec="seconds")),
        )
    else:
        if not path.is_file():
            raise SystemExit(f"lock does not exist: {path}")
        archive_dir = run_dir / "archive" / "locks"
        archive_dir.mkdir(parents=True, exist_ok=True)
        target = archive_dir / path.name
        if target.exists():
            raise SystemExit(f"archived lock already exists: {target}")
        os.replace(path, target)
        path = target
    print(path)


def command_rebuild(args: argparse.Namespace) -> None:
    run_dir, _, _, _, _ = load_run(args.run_dir)
    if (run_dir / "archive" / "ARCHIVED.yaml").is_file():
        raise SystemExit("archived run is read-only")
    _, errors = rebuild_state(run_dir)
    if errors:
        raise SystemExit("\n".join(errors))
    print(run_dir / "state.yaml")


def command_archive(args: argparse.Namespace) -> None:
    run_dir, manifest_path, _, _, _ = load_run(args.run_dir)
    state = scalar_map((run_dir / "state.yaml").read_text(encoding="utf-8"), source="state.yaml")
    if state.get("status") not in {"completed", "cancelled", "superseded"}:
        raise SystemExit(f"run is not terminal: {state.get('status')}")
    if any((run_dir / "locks").glob("*.yaml")):
        raise SystemExit("active locks must be released before archive")
    validator = Path(__file__).with_name("validate_run.py")
    result = subprocess.run(
        ["python3", str(validator), str(run_dir), "--phase", "completion"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise SystemExit(result.stdout + result.stderr)
    replace_flat_scalar(manifest_path, "status", '"archived"')
    replace_flat_scalar(run_dir / "state.yaml", "status", '"archived"')
    manifest = scalar_map(
        manifest_path.read_text(encoding="utf-8"),
        source=str(manifest_path),
    )
    task_states = json_string_map(
        state.get("task_states", "{}"),
        field="task_states",
        source=str(run_dir / "state.yaml"),
    )
    refresh_runtime_documents(
        run_dir,
        manifest,
        task_states,
        status_override="archived",
    )
    marker = run_dir / "archive" / "ARCHIVED.yaml"
    atomic_write(
        marker,
        "\n".join(
            (
                f"protocol_version: {PROTOCOL_VERSION}",
                'kind: "run_archive"',
                f"run_id: {quote(state['run_id'])}",
                f"archived_at: {quote(now_iso())}",
                f"validation_sha256: {quote(sha256(run_dir / 'state.yaml'))}",
                "",
            )
        ),
    )
    print(marker)


def main() -> int:
    args = parse_args()
    commands = {
        "add-agent": command_add_agent,
        "create-task": command_create_task,
        "record-gate": command_record_gate,
        "record-evidence": command_record_evidence,
        "configure-run": command_configure_run,
        "record-release-candidate": command_record_release_candidate,
        "write-ack": command_write_ack,
        "write-lease": command_write_lease,
        "write-result": command_write_result,
        "write-dead-letter": command_write_dead_letter,
        "lock": command_lock,
        "rebuild-state": command_rebuild,
        "archive-run": command_archive,
    }
    run_dir = Path(args.run_dir).expanduser().resolve()
    lock_path = run_dir / "events" / ".sequence.lock"
    if not lock_path.parent.is_dir():
        raise SystemExit(f"invalid run directory: {run_dir}")
    with exclusive_lock(lock_path):
        commands[args.command](args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
