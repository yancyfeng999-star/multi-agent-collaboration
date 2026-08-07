#!/usr/bin/env python3
"""Validate, atomically emit, and reduce one immutable document-bus event."""

from __future__ import annotations

import argparse
from project_memory_lib import exclusive_lock
import os
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from protocol_lib import (
    EVENT_NAMES,
    EVENT_PAYLOAD_KINDS,
    NATIVE_EVENTS,
    PROTOCOL_VERSION,
    REPEATABLE_EVENTS,
    RISK_TO_GATE,
    ProtocolError,
    event_records,
    frontmatter,
    json_string_list,
    json_string_map,
    next_task_state,
    now_iso,
    parse_agent_profiles,
    path_within,
    paths_overlap,
    quote,
    rebuild_state,
    replay_task_states,
    scalar_map,
    sha256,
    valid_iso8601,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--event", choices=sorted(EVENT_NAMES), required=True)
    parser.add_argument("--from-agent", required=True)
    parser.add_argument("--to-agent", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--payload-file")
    parser.add_argument("--causation-id")
    parser.add_argument("--correlation-id")
    parser.add_argument("--idempotency-key")
    parser.add_argument(
        "--event-key",
        help="Stable occurrence key required for repeatable events without a payload",
    )
    return parser.parse_args()


def task_documents(run_dir: Path) -> dict[str, Path]:
    documents: dict[str, Path] = {}
    for task_path in sorted((run_dir / "tasks").glob("*.md")):
        values = frontmatter(task_path)
        task_id = values.get("task_id")
        if not task_id:
            raise ProtocolError(f"{task_path}: missing task_id")
        if task_id in documents:
            raise ProtocolError(f"duplicate task_id {task_id}")
        documents[task_id] = task_path.resolve()
    return documents


def is_attempt_document(
    path: Path,
    *,
    run_dir: Path,
    owner: str,
    task_id: str,
    document_kind: str,
    suffix: str,
) -> bool:
    return (
        path.parent == (run_dir / "outbox" / owner).resolve()
        and path.name.startswith(f"{task_id}-{document_kind}-")
        and path.name.endswith(suffix)
    )


def payload_values(path: Path) -> dict[str, str]:
    if path.suffix == ".md":
        return frontmatter(path)
    return scalar_map(path.read_text(encoding="utf-8"), source=str(path))


def git_worktree_clean(project_root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(project_root), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return False
    return not any(
        line
        for line in result.stdout.splitlines()
        if (line[3:] if len(line) > 3 else line)
        != ".multi-agent-collaboration"
        and not (line[3:] if len(line) > 3 else line).startswith(
            ".multi-agent-collaboration/"
        )
    )


def strict_commit_error(
    project_root: Path,
    branch: str,
    commit: str,
    changed_files: list[str],
) -> str | None:
    if not changed_files:
        return "strict result requires at least one changed_file"
    branch_ref = f"refs/heads/{branch}"
    branch_check = subprocess.run(
        ["git", "-C", str(project_root), "show-ref", "--verify", "--quiet", branch_ref],
        capture_output=True,
        text=True,
    )
    if branch_check.returncode:
        return "strict git_branch must identify a local branch"
    ancestry = subprocess.run(
        ["git", "-C", str(project_root), "merge-base", "--is-ancestor", commit, branch_ref],
        capture_output=True,
        text=True,
    )
    if ancestry.returncode:
        return "implementation commit is not reachable from git_branch"
    diff = subprocess.run(
        [
            "git",
            "-C",
            str(project_root),
            "diff-tree",
            "--root",
            "--no-commit-id",
            "--name-only",
            "-r",
            commit,
        ],
        capture_output=True,
        text=True,
    )
    if diff.returncode:
        return "cannot inspect implementation commit changed files"
    committed_paths = {line for line in diff.stdout.splitlines() if line}
    for changed_file in changed_files:
        changed_path = Path(changed_file).expanduser()
        if not changed_path.is_absolute():
            changed_path = project_root / changed_path
        try:
            relative = changed_path.resolve().relative_to(project_root).as_posix()
        except ValueError:
            return f"strict changed_file exceeds project root: {changed_file}"
        if relative not in committed_paths:
            return f"strict changed_file is not included in implementation commit: {changed_file}"
    return None


def latest_event_payload(
    records: list[tuple[Path, dict[str, str]]],
    *,
    task_id: str,
    event_names: set[str],
) -> Path | None:
    for _, values in reversed(records):
        if values.get("task_id") != task_id or values.get("event") not in event_names:
            continue
        payload_value = values.get("payload_path", "null")
        if payload_value == "null":
            continue
        payload = Path(payload_value).expanduser()
        if not payload.is_absolute():
            payload = records[0][0].parent.parent / payload
        return payload.resolve()
    return None


def verified_record_payload(
    record: dict[str, str],
    *,
    run_dir: Path,
) -> Path | None:
    payload_value = record.get("payload_path", "null")
    if payload_value == "null":
        return None
    payload = Path(payload_value).expanduser()
    if not payload.is_absolute():
        payload = run_dir / payload
    payload = payload.resolve()
    if not payload.is_file() or record.get("payload_sha256") != sha256(payload):
        raise SystemExit(
            f"historical {record.get('event', 'event')} payload is missing or was modified"
        )
    return payload


def latest_attempt_id(
    records: list[tuple[Path, dict[str, str]]],
    *,
    run_dir: Path,
    task_id: str,
    event_names: set[str],
) -> str | None:
    for _, values in reversed(records):
        if values.get("task_id") != task_id or values.get("event") not in event_names:
            continue
        payload = verified_record_payload(values, run_dir=run_dir)
        return payload_values(payload).get("attempt_id") if payload else None
    return None


def expected_payload_path(
    *,
    run_dir: Path,
    task_path: Path,
    task: dict[str, str],
    event: str,
    payload: Path,
) -> None:
    """Fail closed when a semantic event points at the wrong document."""

    owner = task["owner_agent"]
    kind = EVENT_PAYLOAD_KINDS.get(event)
    if not kind:
        return
    resolved = payload.resolve()
    if kind == "task" and resolved != task_path:
        raise SystemExit(f"{event} payload must be the exact frozen task document: {task_path}")
    if kind == "ack":
        if not is_attempt_document(
            resolved,
            run_dir=run_dir,
            owner=owner,
            task_id=task["task_id"],
            document_kind="ack",
            suffix=".yaml",
        ):
            raise SystemExit("ACK payload must be an immutable attempt-specific owner ACK")
    if kind == "lease":
        if not is_attempt_document(
            resolved,
            run_dir=run_dir,
            owner=owner,
            task_id=task["task_id"],
            document_kind="lease",
            suffix=".yaml",
        ):
            raise SystemExit(f"{event} payload must be an immutable attempt-specific owner lease")
    if kind == "result":
        if not is_attempt_document(
            resolved,
            run_dir=run_dir,
            owner=owner,
            task_id=task["task_id"],
            document_kind="result",
            suffix=".md",
        ):
            raise SystemExit(f"{event} payload must be an immutable attempt-specific owner result")
    if kind == "dead_letter" and resolved.parent != (run_dir / "dead-letter").resolve():
        raise SystemExit("DEAD_LETTERED payload must be under dead-letter/")
    if kind == "gate" and resolved.parent != (run_dir / "decisions").resolve():
        raise SystemExit(f"{event} payload must be a run-local decision document")
    if kind in {"review", "qa"} and resolved.parent != (run_dir / "evidence").resolve():
        raise SystemExit(f"{event} payload must be a run-local evidence document")
    if kind == "result_or_evidence":
        owner_result = is_attempt_document(
            resolved,
            run_dir=run_dir,
            owner=owner,
            task_id=task["task_id"],
            document_kind="result",
            suffix=".md",
        )
        if not owner_result and resolved.parent != (run_dir / "evidence").resolve():
            raise SystemExit(f"{event} payload must be the result or run-local evidence")


def validate_payload_document(
    event: str,
    payload: Path,
    task_id: str,
    from_agent: str,
    owner_agent: str,
) -> None:
    kind = EVENT_PAYLOAD_KINDS.get(event)
    if kind == "task":
        values = frontmatter(payload)
    elif payload.suffix == ".md":
        values = frontmatter(payload)
    else:
        values = scalar_map(payload.read_text(encoding="utf-8"), source=str(payload))
    if values.get("protocol_version") != PROTOCOL_VERSION:
        raise SystemExit(f"{payload}: protocol_version must be {PROTOCOL_VERSION}")
    if values.get("task_id") not in {task_id, "null", None}:
        raise SystemExit(f"{payload}: task_id does not match {task_id}")
    if kind in {"ack", "lease", "result", "result_or_evidence"} and values.get(
        "kind"
    ) in {"ack", "lease", "result"}:
        attempt_id = values.get("attempt_id", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", attempt_id):
            raise SystemExit(f"{payload}: attempt_id is missing or invalid")
        if values.get("agent_id") != owner_agent:
            raise SystemExit(f"{payload}: owner document agent_id must be {owner_agent}")
        document_kind = values["kind"]
        if document_kind == "ack":
            expected_name = f"{task_id}-ack-{attempt_id}.yaml"
        elif document_kind == "result":
            expected_name = f"{task_id}-result-{attempt_id}.md"
        else:
            lease_id = values.get("lease_id", "")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", lease_id):
                raise SystemExit(f"{payload}: lease_id is missing or invalid")
            expected_name = f"{task_id}-lease-{attempt_id}-{lease_id}.yaml"
        if payload.name != expected_name:
            raise SystemExit(f"{payload}: filename does not match its attempt identity")
    expected_kind = {
        "ACK": "ack",
        "LEASE_ACQUIRED": "lease",
        "LEASE_RENEWED": "lease",
        "CHANGES_REQUESTED": "review",
        "REVIEW_APPROVED": "review",
        "QA_FAILED": "qa",
        "QA_PASSED": "qa",
        "WAITING_USER_APPROVAL": "human_gate",
        "APPROVAL_GRANTED": "human_gate",
        "APPROVAL_REJECTED": "human_gate",
        "RELEASE_READY": "human_gate",
        "DEAD_LETTERED": "dead_letter",
    }.get(event)
    if expected_kind and values.get("kind") != expected_kind:
        raise SystemExit(f"{payload}: {event} requires kind {expected_kind}")
    if event == "REVIEW_APPROVED" and values.get("status") != "approved":
        raise SystemExit("REVIEW_APPROVED requires approved review evidence")
    if event == "CHANGES_REQUESTED" and values.get("status") != "changes_requested":
        raise SystemExit("CHANGES_REQUESTED requires changes_requested review evidence")
    if event == "QA_PASSED" and values.get("status") != "passed":
        raise SystemExit("QA_PASSED requires passed QA evidence")
    if event == "QA_FAILED" and values.get("status") != "failed":
        raise SystemExit("QA_FAILED requires failed QA evidence")
    if event == "APPROVAL_GRANTED" and values.get("status") != "approved":
        raise SystemExit("APPROVAL_GRANTED requires an approved human gate")
    if event == "APPROVAL_REJECTED" and values.get("status") != "rejected":
        raise SystemExit("APPROVAL_REJECTED requires a rejected human gate")
    if event == "RELEASE_READY":
        if values.get("status") != "approved" or values.get("scope") != "release":
            raise SystemExit("RELEASE_READY requires an approved release gate")
    if event in {"HANDOFF_READY", "TASK_COMPLETED"} and values.get("status") != "completed":
        raise SystemExit(f"{event} requires a completed owner result")
    if event == "TASK_FAILED" and values.get("status") != "failed":
        raise SystemExit("TASK_FAILED requires a failed owner result")
    if event == "BLOCKED" and values.get("kind") == "result" and values.get("status") != "blocked":
        raise SystemExit("BLOCKED result payload must have blocked status")
    if event in {"CHANGES_REQUESTED", "REVIEW_APPROVED", "QA_FAILED", "QA_PASSED"}:
        if values.get("agent_id") != from_agent:
            raise SystemExit(f"{event} evidence agent must match event sender")
    if event in {"LEASE_ACQUIRED", "LEASE_RENEWED"}:
        if values.get("agent_id") != from_agent and from_agent != "coordinator":
            raise SystemExit(f"{event} lease owner must match event sender or Coordinator proxy")
        acquired = values.get("acquired_at", "")
        expires = values.get("lease_expires_at", "")
        if not valid_iso8601(acquired) or not valid_iso8601(expires):
            raise SystemExit(f"{event} lease timestamps require ISO 8601 timezone")
        if datetime.fromisoformat(expires.replace("Z", "+00:00")) <= datetime.fromisoformat(
            acquired.replace("Z", "+00:00")
        ):
            raise SystemExit(f"{event} lease expiry must be after acquisition")


def validate_event_actor(
    event: str,
    from_agent: str,
    to_agent: str,
    owner: str,
    task: dict[str, str],
) -> None:
    coordinator_events = {
        "TASK_READY",
        "TASK_DISPATCHED",
        "LEASE_ACQUIRED",
        "LEASE_RENEWED",
        "WAITING_USER_APPROVAL",
        "APPROVAL_GRANTED",
        "APPROVAL_REJECTED",
        "TASK_RESUMED",
        "RETRY_SCHEDULED",
        "DEAD_LETTERED",
        "RELEASE_READY",
        "TASK_COMPLETED",
        "TASK_CANCELLED",
        "TASK_SUPERSEDED",
        "TASK_EXPIRED",
        "DOCUMENT_SUBAGENT_DELEGATED",
        "DOCUMENT_SUBAGENT_RESULT_RECEIVED",
        "DOCUMENT_SUBAGENT_FAILED",
        "DOCUMENT_SUBAGENT_CLOSED",
        *NATIVE_EVENTS,
    }
    if event in coordinator_events and from_agent != "coordinator":
        raise SystemExit(f"{event} must be emitted by coordinator")
    if event in {"TASK_READY", "TASK_DISPATCHED", "LEASE_ACQUIRED", "TASK_RESUMED"}:
        if to_agent != owner:
            raise SystemExit(f"{event} must target the task owner {owner}")
    if event == "ACK" and from_agent not in {owner, "coordinator"}:
        raise SystemExit("ACK must come from the owner or a Coordinator proxy")
    if event == "HANDOFF_READY" and from_agent != owner:
        raise SystemExit("HANDOFF_READY must come from the task owner")
    if event == "HANDOFF_READY":
        reviewer = task.get("reviewer_agent")
        if reviewer not in {None, "null", ""} and to_agent != reviewer:
            raise SystemExit(f"HANDOFF_READY must target reviewer {reviewer}")
    if event in {"REVIEW_STARTED", "CHANGES_REQUESTED", "REVIEW_APPROVED"}:
        reviewer = task.get("reviewer_agent")
        if reviewer in {None, "null", ""} or from_agent != reviewer:
            raise SystemExit(f"{event} must come from reviewer {reviewer}")
    if event in {"QA_FAILED", "QA_PASSED"}:
        qa_agent = task.get("qa_agent")
        if qa_agent in {None, "null", ""} or from_agent != qa_agent:
            raise SystemExit(f"{event} must come from QA {qa_agent}")
    if event == "REVIEW_APPROVED" and to_agent != task.get("qa_agent"):
        raise SystemExit(f"REVIEW_APPROVED must target QA {task.get('qa_agent')}")
    if event == "RELEASE_READY":
        release_agent = task.get("release_agent")
        if release_agent in {None, "null", ""}:
            raise SystemExit("RELEASE_READY requires a task release_agent")
        if to_agent != release_agent:
            raise SystemExit(f"RELEASE_READY must target release agent {release_agent}")
    if event in {"TASK_FAILED", "BLOCKED"} and from_agent not in {
        owner,
        "coordinator",
    }:
        raise SystemExit(f"{event} must come from owner or coordinator")


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir).expanduser().resolve()
    manifest_path = run_dir / "manifest.yaml"
    events_dir = run_dir / "events"
    agents_path = run_dir / "agents.yaml"
    if not manifest_path.is_file() or not events_dir.is_dir() or not agents_path.is_file():
        raise SystemExit(f"Invalid protocol v3 run directory: {run_dir}")

    try:
        manifest = scalar_map(manifest_path.read_text(encoding="utf-8"), source=str(manifest_path))
        if manifest.get("protocol_version") != PROTOCOL_VERSION:
            raise ProtocolError(f"manifest protocol_version must be {PROTOCOL_VERSION}")
        run_id = manifest["run_id"]
        governance = manifest["governance"]
        if manifest.get("status") == "archived" or (
            run_dir / "archive" / "ARCHIVED.yaml"
        ).is_file():
            raise ProtocolError("archived run is read-only")
        tasks = task_documents(run_dir)
        task_path = tasks.get(args.task_id)
        if not task_path:
            raise ProtocolError(f"task does not exist: {args.task_id}")
        task = frontmatter(task_path)
        if task.get("run_id") != run_id:
            raise ProtocolError("task run_id does not match manifest")
        if task.get("status") != "draft":
            raise ProtocolError("frozen task status must remain draft; runtime state belongs in events")
        agents = parse_agent_profiles(agents_path.read_text(encoding="utf-8"), source=str(agents_path))
    except (KeyError, ProtocolError) as exc:
        raise SystemExit(str(exc)) from exc
    contract_path = Path(manifest.get("version_contract_ref", "")).expanduser()
    if not contract_path.is_absolute():
        contract_path = run_dir / contract_path
    contract_path = contract_path.resolve()
    if (
        contract_path != (run_dir / "versions" / "version-contract.yaml").resolve()
        or not contract_path.is_file()
        or manifest.get("version_contract_ref_sha256") != sha256(contract_path)
    ):
        raise SystemExit("run version contract is missing or has changed")
    try:
        version_contract = scalar_map(
            contract_path.read_text(encoding="utf-8"),
            source=str(contract_path),
        )
    except ProtocolError as exc:
        raise SystemExit(str(exc)) from exc
    for field in (
        "run_id",
        "release_train_id",
        "versioning_mode",
        "baseline_version",
        "target_version",
    ):
        if version_contract.get(field) != manifest.get(field):
            raise SystemExit(f"version contract {field} does not match manifest")
    if (
        task.get("release_train_id") != manifest.get("release_train_id")
        or task.get("delivery_version") != manifest.get("target_version")
        or task.get("version_contract_sha256") != sha256(contract_path)
    ):
        raise SystemExit("task is not bound to the run version contract")

    for agent in (args.from_agent, args.to_agent, task["owner_agent"]):
        if agent not in agents and agent not in {"user", "system", "external"}:
            raise SystemExit(f"unregistered agent: {agent}")
    validate_event_actor(
        args.event,
        args.from_agent,
        args.to_agent,
        task["owner_agent"],
        task,
    )

    project_path = run_dir.parent.parent / "project.yaml"
    try:
        project = scalar_map(project_path.read_text(encoding="utf-8"), source=str(project_path))
        project_root = Path(project["project_root"]).resolve()
        allowed_roots = json_string_list(
            project.get("allowed_roots", "[]"),
            field="allowed_roots",
            source=str(project_path),
        )
        owner_profile = agents[task["owner_agent"]]
        owned_paths = json_string_list(
            task.get("owned_paths", "[]"),
            field="owned_paths",
            source=str(task_path),
        )
        forbidden_paths = json_string_list(
            task.get("forbidden_paths", "[]"),
            field="forbidden_paths",
            source=str(task_path),
        )
        risk_flags = json_string_list(
            task.get("risk_flags", "[]"),
            field="risk_flags",
            source=str(task_path),
        )
        human_gates = json_string_list(
            task.get("human_gates", "[]"),
            field="human_gates",
            source=str(task_path),
        )
        human_gate_hashes = json_string_map(
            task.get("human_gate_hashes", "{}"),
            field="human_gate_hashes",
            source=str(task_path),
        )
    except (KeyError, ProtocolError) as exc:
        raise SystemExit(str(exc)) from exc
    for owned_path in owned_paths:
        if not path_within(
            owned_path,
            [str(item) for item in owner_profile.get("writable_paths", [])],
            project_root,
        ):
            raise SystemExit(f"owned path exceeds owner writable scope: {owned_path}")
        if path_within(
            owned_path,
            [str(item) for item in owner_profile.get("forbidden_paths", [])],
            project_root,
        ):
            raise SystemExit(f"owned path is forbidden: {owned_path}")
    for owner_forbidden in owner_profile.get("forbidden_paths", []):
        if not path_within(str(owner_forbidden), forbidden_paths, project_root):
            raise SystemExit("task must inherit every owner forbidden path")
    if set(human_gate_hashes) != set(human_gates):
        raise SystemExit("human_gate_hashes must exactly cover human_gates")
    for gate_id in human_gates:
        gate_path = run_dir / "decisions" / f"{gate_id}.yaml"
        if not gate_path.is_file() or human_gate_hashes[gate_id] != sha256(gate_path):
            raise SystemExit(f"declared human gate hash mismatch: {gate_id}")
    if args.event == "TASK_READY":
        confirmation = Path(manifest.get("user_confirmation_ref", "")).expanduser()
        if not confirmation.is_absolute():
            confirmation = run_dir / confirmation
        if not confirmation.is_file():
            raise SystemExit("TASK_READY requires the run user confirmation")
        if manifest.get("user_confirmation_ref_sha256") != sha256(confirmation):
            raise SystemExit("run user confirmation hash mismatch")
        confirmation_values = scalar_map(
            confirmation.read_text(encoding="utf-8"),
            source=str(confirmation),
        )
        if (
            confirmation_values.get("kind") != "human_gate"
            or confirmation_values.get("scope") != "run_initialization"
            or confirmation_values.get("status") != "approved"
        ):
            raise SystemExit("run user confirmation is not approved")
        if manifest.get("versioning_mode") == "tracked":
            source = Path(version_contract.get("version_source_ref", "")).expanduser()
            if (
                not source.is_file()
                or version_contract.get("version_source_sha256") != sha256(source)
            ):
                raise SystemExit(
                    "tracked version source changed from the reserved baseline"
                )
        elif manifest.get("versioning_mode") != "not_applicable":
            raise SystemExit("run requires an explicit versioning assessment")
        if governance in {"standard", "strict"}:
            quality_agents: list[str] = []
            for role in ("reviewer_agent", "qa_agent"):
                role_value = task.get(role)
                if role_value in {None, "", "null"} or role_value not in agents:
                    raise SystemExit(f"{governance} task requires registered {role}")
                quality_agents.append(str(role_value))
            if task["owner_agent"] in quality_agents:
                raise SystemExit(
                    f"{governance} task requires quality review independent from Owner; "
                    "Reviewer and QA may be the same agent"
                )
        if governance == "strict":
            if manifest.get("change_id") in {None, "", "null"}:
                raise SystemExit("strict dispatch requires change_id")
            if manifest.get("git_branch") in {None, "", "null"}:
                raise SystemExit("strict dispatch requires git_branch")
            git_root = subprocess.run(
                ["git", "-C", str(project_root), "rev-parse", "--show-toplevel"],
                capture_output=True,
                text=True,
            )
            current_branch = subprocess.run(
                ["git", "-C", str(project_root), "branch", "--show-current"],
                capture_output=True,
                text=True,
            )
            if (
                git_root.returncode
                or Path(git_root.stdout.strip()).resolve() != project_root
                or current_branch.returncode
                or not current_branch.stdout.strip()
            ):
                raise SystemExit("strict dispatch requires an attached project Git worktree")
            if current_branch.stdout.strip() != manifest["git_branch"]:
                raise SystemExit("strict dispatch git_branch does not match the current branch")
            if not git_worktree_clean(project_root):
                raise SystemExit("strict dispatch requires a clean project worktree")
            for field in (
                "registry_ref",
                "git_status_ref",
                "environment_impact_ref",
                "rollback_ref",
                "security_review_ref",
            ):
                reference = Path(manifest.get(field, "")).expanduser()
                if not reference.is_absolute():
                    reference = run_dir / reference
                if not reference.is_file() or not path_within(
                    reference,
                    [run_dir, *allowed_roots],
                    project_root,
                ):
                    raise SystemExit(f"strict dispatch requires valid {field}")
                if manifest.get(f"{field}_sha256") != sha256(reference):
                    raise SystemExit(f"strict dispatch {field} hash mismatch")
                expected_evidence = {
                    "git_status_ref": ("git_status", {"clean"}),
                    "environment_impact_ref": (
                        "environment_impact",
                        {"reviewed", "approved"},
                    ),
                    "rollback_ref": ("rollback", {"ready", "approved"}),
                    "security_review_ref": (
                        "security",
                        {"approved", "passed"},
                    ),
                }.get(field)
                if expected_evidence:
                    evidence = scalar_map(
                        reference.read_text(encoding="utf-8"),
                        source=str(reference),
                    )
                    expected_kind, expected_statuses = expected_evidence
                    if (
                        evidence.get("kind") != expected_kind
                        or evidence.get("status") not in expected_statuses
                    ):
                        raise SystemExit(
                            f"{field} must reference {expected_kind} evidence with an accepted status"
                        )
            decisions: dict[str, dict[str, str]] = {}
            for decision_path in (run_dir / "decisions").glob("*.yaml"):
                values = scalar_map(
                    decision_path.read_text(encoding="utf-8"),
                    source=str(decision_path),
                )
                if values.get("gate_id"):
                    decisions[values["gate_id"]] = values
            for risk in risk_flags:
                required_scope = RISK_TO_GATE.get(risk)
                if not required_scope:
                    continue
                if not any(
                    gate_id in decisions
                    and decisions[gate_id].get("scope") == required_scope
                    and decisions[gate_id].get("status") == "approved"
                    and decisions[gate_id].get("task_id") in {"null", args.task_id}
                    for gate_id in human_gates
                ):
                    raise SystemExit(
                        f"strict risk {risk} requires approved {required_scope} gate"
                    )
    if args.event == "RELEASE_READY":
        candidate_ids = json_string_list(
            manifest.get("release_candidates", "[]"),
            field="release_candidates",
            source=str(manifest_path),
        )
        if manifest.get("versioning_mode") == "tracked" and not candidate_ids:
            raise SystemExit("tracked release requires at least one release candidate")

    payload_path: str | None = None
    payload_hash: str | None = None
    payload: Path | None = None
    if args.payload_file:
        payload = Path(args.payload_file).expanduser().resolve()
        if not payload.is_file():
            raise SystemExit(f"Payload file does not exist: {payload}")
        expected_payload_path(
            run_dir=run_dir,
            task_path=task_path,
            task=task,
            event=args.event,
            payload=payload,
        )
        validate_payload_document(
            args.event,
            payload,
            args.task_id,
            args.from_agent,
            task["owner_agent"],
        )
        payload_path = str(payload)
        payload_hash = sha256(payload)
    if args.event in EVENT_PAYLOAD_KINDS and not payload:
        raise SystemExit(f"{args.event} requires --payload-file")
    if args.event == "TASK_COMPLETED" and governance in {"standard", "strict"}:
        assert payload is not None
        completion_result = frontmatter(payload)
        if completion_result.get("verification_status") != "passed":
            raise SystemExit("governed completion requires passed verification")
        verification_refs = json_string_list(
            completion_result.get("verification_refs", "[]"),
            field="verification_refs",
            source=str(payload),
        )
        if not verification_refs:
            raise SystemExit("governed completion requires verification evidence")
        for reference in verification_refs:
            evidence_path = Path(reference).expanduser()
            if not evidence_path.is_absolute():
                evidence_path = run_dir / evidence_path
            evidence_path = evidence_path.resolve()
            if (
                not evidence_path.is_file()
                or evidence_path.parent != (run_dir / "evidence").resolve()
            ):
                raise SystemExit("verification_refs must point to run-local evidence")
            evidence = scalar_map(
                evidence_path.read_text(encoding="utf-8"),
                source=str(evidence_path),
            )
            if (
                evidence.get("protocol_version") != PROTOCOL_VERSION
                or evidence.get("run_id") != run_id
                or evidence.get("kind") != "verification"
                or evidence.get("status") != "passed"
                or evidence.get("task_id") not in {"null", args.task_id}
            ):
                raise SystemExit("verification evidence is not valid for governed completion")
            artifact_refs = json_string_list(
                evidence.get("artifact_refs", "[]"),
                field="artifact_refs",
                source=str(evidence_path),
            )
            artifact_hashes = json_string_map(
                evidence.get("artifact_hashes", "{}"),
                field="artifact_hashes",
                source=str(evidence_path),
            )
            if set(artifact_hashes) != set(artifact_refs):
                raise SystemExit("verification evidence artifact hashes are incomplete")
            for artifact_ref in artifact_refs:
                artifact = Path(artifact_ref).expanduser()
                if not artifact.is_absolute():
                    artifact = run_dir / artifact
                artifact = artifact.resolve()
                if (
                    not artifact.is_file()
                    or not path_within(
                        artifact,
                        [run_dir, *allowed_roots],
                        project_root,
                    )
                    or artifact_hashes[artifact_ref] != sha256(artifact)
                ):
                    raise SystemExit("verification evidence artifact is missing or modified")
        if completion_result.get("risk_summary") in {"", "null", None}:
            raise SystemExit("governed completion requires a risk summary")
        if completion_result.get("rollback_plan") in {"", "null", None}:
            raise SystemExit("governed completion requires a rollback plan")
        commit = completion_result.get("implementation_commit", "null")
        uncommitted_reason = completion_result.get("uncommitted_reason", "null")
        if commit == "null" and uncommitted_reason == "null":
            raise SystemExit("governed completion requires a commit or uncommitted reason")
        if manifest.get("git_branch") in {"", "null", None}:
            raise SystemExit("governed completion requires git_branch")
        git_status_ref = Path(manifest.get("git_status_ref", "")).expanduser()
        if not git_status_ref.is_absolute():
            git_status_ref = run_dir / git_status_ref
        if (
            not git_status_ref.is_file()
            or manifest.get("git_status_ref_sha256") != sha256(git_status_ref)
        ):
            raise SystemExit("governed completion requires immutable git_status_ref")
        git_status_evidence = scalar_map(
            git_status_ref.read_text(encoding="utf-8"),
            source=str(git_status_ref),
        )
        if (
            git_status_evidence.get("kind") != "git_status"
            or git_status_evidence.get("status")
            not in {"clean", "recorded", "not_applicable"}
        ):
            raise SystemExit("git_status_ref must be accepted git status evidence")
        if governance == "strict":
            if commit == "null":
                raise SystemExit("strict completion requires an implementation commit")
            commit_check = subprocess.run(
                [
                    "git",
                    "-C",
                    str(project_root),
                    "cat-file",
                    "-e",
                    f"{commit}^{{commit}}",
                ],
                capture_output=True,
                text=True,
            )
            if commit_check.returncode:
                raise SystemExit("strict completion implementation commit does not exist")
            changed_files = json_string_list(
                completion_result.get("changed_files", "[]"),
                field="changed_files",
                source=str(payload),
            )
            commit_problem = strict_commit_error(
                project_root,
                manifest["git_branch"],
                commit,
                changed_files,
            )
            if commit_problem:
                raise SystemExit(commit_problem)
    if args.event == "RELEASE_READY":
        if "local_only" in risk_flags:
            raise SystemExit("local_only task cannot enter release")
        if manifest.get("release_environment") in {None, "", "null"}:
            raise SystemExit("release requires release_environment")
        authorization = Path(manifest.get("release_authorization_ref", "")).expanduser()
        if not authorization.is_absolute():
            authorization = run_dir / authorization
        if not authorization.is_file() or authorization.resolve() != payload:
            raise SystemExit(
                "RELEASE_READY payload must equal manifest release_authorization_ref"
            )
        if manifest.get("release_authorization_ref_sha256") != sha256(authorization):
            raise SystemExit("release authorization hash mismatch")
        clean_reference = Path(manifest.get("clean_worktree_ref", "")).expanduser()
        if not clean_reference.is_absolute():
            clean_reference = run_dir / clean_reference
        if not clean_reference.is_file():
            raise SystemExit("release requires clean_worktree_ref")
        if manifest.get("clean_worktree_ref_sha256") != sha256(clean_reference):
            raise SystemExit("clean worktree evidence hash mismatch")
        clean_evidence = scalar_map(
            clean_reference.read_text(encoding="utf-8"),
            source=str(clean_reference),
        )
        if (
            clean_evidence.get("kind") != "git_status"
            or clean_evidence.get("status") != "clean"
        ):
            raise SystemExit("clean_worktree_ref must be clean git_status evidence")
        if governance == "strict":
            git_status = subprocess.run(
                ["git", "-C", str(project_root), "status", "--porcelain"],
                capture_output=True,
                text=True,
            )
            if git_status.returncode:
                raise SystemExit("strict release requires an accessible Git worktree")
            dirty = []
            for line in git_status.stdout.splitlines():
                changed_path = line[3:] if len(line) > 3 else line
                if changed_path == ".multi-agent-collaboration" or changed_path.startswith(
                    ".multi-agent-collaboration/"
                ):
                    continue
                dirty.append(line)
            if dirty:
                raise SystemExit("strict release requires a clean project worktree")
        try:
            prior_records = event_records(events_dir)
        except ProtocolError as exc:
            raise SystemExit(str(exc)) from exc
        result_path = latest_event_payload(
            prior_records,
            task_id=args.task_id,
            event_names={"HANDOFF_READY"},
        )
        if result_path is None:
            raise SystemExit("release requires a HANDOFF_READY owner result")
        if not result_path.is_file():
            raise SystemExit("release requires an owner result")
        handoff_record = next(
            (
                values
                for _, values in reversed(prior_records)
                if values.get("task_id") == args.task_id
                and values.get("event") == "HANDOFF_READY"
            ),
            None,
        )
        if not handoff_record or verified_record_payload(
            handoff_record,
            run_dir=run_dir,
        ) != result_path:
            raise SystemExit("release HANDOFF_READY result is missing or was modified")
        release_result = frontmatter(result_path)
        release_commit = release_result.get("implementation_commit", "null")
        if release_commit == "null":
            raise SystemExit("release requires an implementation commit")
        commit_check = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "cat-file",
                "-e",
                f"{release_commit}^{{commit}}",
            ],
            capture_output=True,
            text=True,
        )
        if commit_check.returncode:
            raise SystemExit("release implementation commit does not exist")
        if governance == "strict":
            release_changed_files = json_string_list(
                release_result.get("changed_files", "[]"),
                field="changed_files",
                source=str(result_path),
            )
            commit_problem = strict_commit_error(
                project_root,
                manifest["git_branch"],
                release_commit,
                release_changed_files,
            )
            if commit_problem:
                raise SystemExit(commit_problem)
    if args.event in REPEATABLE_EVENTS and not payload and not (
        args.event_key or args.idempotency_key
    ):
        raise SystemExit(
            f"{args.event} is repeatable; provide --event-key or --idempotency-key"
        )

    correlation_id = args.correlation_id or f"{run_id}:{args.task_id}"
    occurrence = args.event_key or (payload_hash[:16] if payload_hash else "v1")
    idempotency_key = (
        args.idempotency_key
        or f"{correlation_id}:{args.event}:{occurrence}"
    )

    lock_path = events_dir / ".sequence.lock"
    with exclusive_lock(lock_path):
        try:
            existing = event_records(events_dir)
        except ProtocolError as exc:
            raise SystemExit(str(exc)) from exc
        for _, historical in existing:
            historical_event = historical.get("event", "")
            if (
                historical_event in EVENT_PAYLOAD_KINDS
                and historical.get("payload_path", "null") == "null"
            ):
                raise SystemExit(
                    f"historical {historical_event} event is missing its payload"
                )
            if historical.get("payload_path", "null") != "null":
                verified_record_payload(historical, run_dir=run_dir)
        for path, values in existing:
            if values.get("idempotency_key") != idempotency_key:
                continue
            same_operation = (
                values.get("event") == args.event
                and values.get("task_id") == args.task_id
                and values.get("from_agent") == args.from_agent
                and values.get("to_agent") == args.to_agent
                and values.get("payload_sha256") == (payload_hash or "null")
            )
            if same_operation:
                states, state_errors = rebuild_state(run_dir)
                if state_errors:
                    raise SystemExit("\n".join(state_errors))
                print(path)
                return 0
            raise SystemExit(
                f"Idempotency key already used by a different event: {idempotency_key}"
            )

        states, replay_errors = replay_task_states(existing, governance)
        if replay_errors:
            raise SystemExit("\n".join(replay_errors))
        current_state = states.get(args.task_id)
        new_state = next_task_state(current_state, args.event, governance)
        if new_state is None:
            raise SystemExit(
                f"Illegal transition {current_state or 'none'} -> {args.event}"
            )

        try:
            max_attempts = int(manifest["max_attempts"])
        except (KeyError, ValueError) as exc:
            raise SystemExit("manifest max_attempts must be an integer") from exc
        ack_attempts: list[str] = []
        for _, values in existing:
            if values.get("task_id") != args.task_id or values.get("event") != "ACK":
                continue
            ack_payload = verified_record_payload(values, run_dir=run_dir)
            if ack_payload:
                attempt_id = payload_values(ack_payload).get("attempt_id")
                if attempt_id and attempt_id not in ack_attempts:
                    ack_attempts.append(attempt_id)
        document_attempt = payload_values(payload).get("attempt_id") if payload else None
        current_attempt = latest_attempt_id(
            existing,
            run_dir=run_dir,
            task_id=args.task_id,
            event_names={"ACK"},
        )
        if args.event == "ACK":
            if document_attempt in ack_attempts:
                raise SystemExit("retry must use a new attempt_id and immutable ACK")
            if len(ack_attempts) >= max_attempts:
                raise SystemExit("ACK would exceed manifest max_attempts")
        if args.event in {
            "LEASE_ACQUIRED",
            "LEASE_RENEWED",
            "HANDOFF_READY",
            "TASK_FAILED",
        } and document_attempt != current_attempt:
            raise SystemExit(f"{args.event} payload must match the current ACK attempt")
        if args.event == "TASK_COMPLETED":
            handoff_result = latest_event_payload(
                existing,
                task_id=args.task_id,
                event_names={"HANDOFF_READY"},
            )
            if handoff_result is not None and payload != handoff_result:
                raise SystemExit("TASK_COMPLETED must use the latest HANDOFF_READY result")
            handoff_record = next(
                (
                    values
                    for _, values in reversed(existing)
                    if values.get("task_id") == args.task_id
                    and values.get("event") == "HANDOFF_READY"
                ),
                None,
            )
            if handoff_record and verified_record_payload(
                handoff_record,
                run_dir=run_dir,
            ) != payload:
                raise SystemExit("latest HANDOFF_READY result is missing or was modified")
            if document_attempt != current_attempt:
                raise SystemExit("TASK_COMPLETED payload must match the current ACK attempt")
        if args.event == "RETRY_SCHEDULED" and len(ack_attempts) >= max_attempts:
            raise SystemExit("retry attempts are exhausted; dead-letter the task")
        if args.event == "DEAD_LETTERED":
            assert payload is not None
            dead_letter = payload_values(payload)
            if dead_letter.get("attempts") != str(len(ack_attempts)):
                raise SystemExit(
                    "dead-letter attempts must equal event-derived ACK attempts"
                )
            if len(ack_attempts) < max_attempts:
                raise SystemExit("dead-letter requires exhausted max_attempts")
            failed_event_id = dead_letter.get("event_id")
            failed_record = next(
                (
                    values
                    for _, values in existing
                    if values.get("event_id") == failed_event_id
                ),
                None,
            )
            if (
                not failed_record
                or failed_record.get("task_id") != args.task_id
                or failed_record.get("event")
                not in {
                    "TASK_FAILED",
                    "THREAD_FAILED",
                    "SUBAGENT_FAILED",
                    "DOCUMENT_SUBAGENT_FAILED",
                }
            ):
                raise SystemExit(
                    "dead-letter event_id must identify this task's failure"
                )

        if args.event == "TASK_READY":
            manifest_tasks = json_string_list(
                manifest.get("tasks", "[]"),
                field="tasks",
                source=str(manifest_path),
            )
            if args.task_id not in manifest_tasks:
                raise SystemExit("task must be indexed in manifest before TASK_READY")
            dependencies = json_string_list(
                task.get("dependencies", "[]"),
                field="dependencies",
                source=str(task_path),
            )
            for dependency in dependencies:
                if states.get(dependency) != "completed":
                    raise SystemExit(f"dependency is not completed: {dependency}")
        if args.event == "TASK_DISPATCHED":
            try:
                max_parallel = int(manifest["max_parallel"])
            except (KeyError, ValueError) as exc:
                raise SystemExit("manifest max_parallel must be an integer") from exc
            active = {"dispatched", "acknowledged", "running", "reviewing", "qa_running"}
            if sum(value in active for value in states.values()) >= max_parallel:
                raise SystemExit("dispatch would exceed max_parallel")
            for other_task_id, other_state in states.items():
                if other_task_id == args.task_id or other_state not in active:
                    continue
                other_task = frontmatter(tasks[other_task_id])
                other_owned = json_string_list(
                    other_task.get("owned_paths", "[]"),
                    field="owned_paths",
                    source=str(tasks[other_task_id]),
                )
                if any(
                    paths_overlap(left, right, project_root)
                    for left in owned_paths
                    for right in other_owned
                ):
                    raise SystemExit(
                        f"dispatch conflicts with active task owned_paths: {other_task_id}"
                    )
            for lock_file in (run_dir / "locks").glob("*.yaml"):
                lock_values = scalar_map(
                    lock_file.read_text(encoding="utf-8"),
                    source=str(lock_file),
                )
                if lock_values.get("owner_task") == args.task_id:
                    continue
                resource = lock_values.get("resource", "")
                if resource.startswith("logical:"):
                    continue
                if any(paths_overlap(resource, owned, project_root) for owned in owned_paths):
                    raise SystemExit(f"owned path conflicts with active lock: {lock_file}")

        sequence = 1
        if existing:
            sequence = max(int(values["sequence"]) for _, values in existing) + 1
        event_id = str(uuid.uuid4())
        created_at = now_iso()
        values = (
            f"protocol_version: {PROTOCOL_VERSION}",
            f"run_id: {quote(run_id)}",
            f"event_id: {quote(event_id)}",
            f"sequence: {sequence}",
            f"event: {quote(args.event)}",
            f"task_id: {quote(args.task_id)}",
            f"from_agent: {quote(args.from_agent)}",
            f"to_agent: {quote(args.to_agent)}",
            f"created_at: {quote(created_at)}",
            f"correlation_id: {quote(correlation_id)}",
            f"causation_id: {quote(args.causation_id) if args.causation_id else 'null'}",
            f"idempotency_key: {quote(idempotency_key)}",
            f"summary: {quote(args.summary)}",
            f"payload_path: {quote(payload_path) if payload_path else 'null'}",
            f"payload_sha256: {quote(payload_hash) if payload_hash else 'null'}",
            "",
        )
        target = events_dir / f"{sequence:06d}-{args.event.lower()}-{event_id}.yaml"
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.write_text("\n".join(values), encoding="utf-8")
        os.replace(temporary, target)
        _, state_errors = rebuild_state(run_dir)
        if state_errors:
            raise SystemExit("\n".join(state_errors))
    print(target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
