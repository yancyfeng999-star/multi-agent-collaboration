#!/usr/bin/env python3
"""Fail-closed validation for a multi-agent-collaboration protocol-v3 run."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

from protocol_lib import (
    DOCUMENT_SUBAGENT_EVENTS,
    EVENT_NAMES,
    EVENT_PAYLOAD_KINDS,
    NATIVE_EVENTS,
    PROTOCOL_VERSION,
    RISK_TO_GATE,
    RUN_STATUSES,
    TASK_STATUSES,
    TERMINAL_TASK_STATUSES,
    ProtocolError,
    derive_run_status,
    event_records,
    frontmatter,
    json_string_list,
    json_string_map,
    next_task_state,
    parse_agent_profiles,
    path_within,
    paths_overlap,
    scalar_map,
    sha256,
    valid_iso8601,
)
from claim_lib import effective_owner
from governance_paths import STORAGE_SCHEMA, load_project_binding


REQUIRED_DIRS = (
    "tasks",
    "inbox",
    "outbox",
    "events",
    "decisions",
    "artifacts",
    "evidence",
    "locks",
    "dead-letter",
    "delegations",
    "native/threads",
    "native/operations",
    "versions/candidates",
    "archive",
)

EVENT_KEYS = (
    "protocol_version",
    "run_id",
    "event_id",
    "sequence",
    "event",
    "task_id",
    "from_agent",
    "to_agent",
    "created_at",
    "correlation_id",
    "causation_id",
    "idempotency_key",
    "summary",
    "payload_path",
    "payload_sha256",
)

TASK_KEYS = (
    "protocol_version",
    "run_id",
    "task_id",
    "title",
    "status",
    "owner_agent",
    "reviewer_agent",
    "qa_agent",
    "release_agent",
    "release_train_id",
    "delivery_version",
    "version_contract_sha256",
    "dependencies",
    "owned_paths",
    "forbidden_paths",
    "risk_flags",
    "human_gates",
    "human_gate_hashes",
    "idempotency_key",
    "created_at",
)

RESULT_KEYS = (
    "protocol_version",
    "kind",
    "run_id",
    "task_id",
    "task_sha256",
    "agent_id",
    "attempt_id",
    "status",
    "idempotency_key",
    "created_at",
    "changed_files",
    "implementation_commit",
    "uncommitted_reason",
    "verification_status",
    "verification_refs",
    "risk_summary",
    "rollback_plan",
    "handoff_to",
)

AGENT_RUNTIMES = {"codex_thread", "codex_subagent", "document", "document_subagent"}
NATIVE_BINDING_STATUSES = {
    "requested",
    "provisioning",
    "ready",
    "running",
    "blocked",
    "completed",
    "failed",
    "archived",
    "closed",
}
NATIVE_OPERATION_TYPES = {
    "create",
    "fork",
    "spawn",
    "send",
    "wait",
    "read",
    "handoff",
    "rename",
    "pin",
    "archive",
    "close",
    "resume",
}
NATIVE_OPERATION_STATUSES = {"requested", "pending", "succeeded", "failed", "unknown"}
DOCUMENT_SUBAGENT_BINDING_STATUSES = {
    "requested",
    "ready",
    "running",
    "blocked",
    "result_received",
    "failed",
    "closed",
}

NATIVE_EVENT_OPERATION_TYPES = {
    "THREAD_CREATE_REQUESTED": {"create", "fork"},
    "THREAD_PROVISIONING": {"create", "fork"},
    "THREAD_READY": {"create", "fork"},
    "THREAD_MESSAGE_SENT": {"send"},
    "THREAD_RUNNING": {"wait", "read"},
    "THREAD_PROGRESS": {"wait", "read"},
    "THREAD_RESULT_RECEIVED": {"wait", "read"},
    "THREAD_FAILED": {"create", "fork", "send", "wait", "read"},
    "THREAD_HANDOFF_STARTED": {"handoff"},
    "THREAD_HANDOFF_COMPLETED": {"handoff"},
    "THREAD_HANDOFF_FAILED": {"handoff"},
    "THREAD_ARCHIVED": {"archive"},
    "SUBAGENT_SPAWNED": {"spawn"},
    "SUBAGENT_MESSAGE_SENT": {"send"},
    "SUBAGENT_RESULT_RECEIVED": {"wait", "read"},
    "SUBAGENT_FAILED": {"spawn", "send", "wait", "read"},
    "SUBAGENT_CLOSED": {"close"},
}

SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:^|[\"'])(api[_-]?key|password|secret|bearer|database_url|cookie|session|"
    r"access[_-]?token|refresh[_-]?token|authorization|private[_-]?key)(?:[\"']?)"
    r"\s*[:=]\s*[\"']?([^\"'\s,}]+)"
)
SAFE_SECRET = re.compile(r"^(null|<[^>]+>|[A-Z][A-Z0-9_]*_(REF|PATH|NAME))$")
PRIVATE_KEY_MARKER = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----"
)
BEARER_VALUE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}")
URL_CREDENTIAL = re.compile(r"[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@", re.I)
TOKEN_SHAPES = re.compile(
    r"\b(?:AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9_]{30,}|sk-[A-Za-z0-9_-]{20,})\b"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir")
    parser.add_argument(
        "--phase",
        choices=("auto", "structure", "dispatch", "completion", "release"),
        default="auto",
    )
    return parser.parse_args()


def add_protocol_error(errors: list[str], callback, *args, **kwargs):
    try:
        return callback(*args, **kwargs)
    except (ProtocolError, KeyError, ValueError) as exc:
        errors.append(str(exc))
        return None


def parse_iso(value: str) -> datetime | None:
    if not valid_iso8601(value):
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def dependency_cycle(graph: dict[str, list[str]]) -> list[str] | None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str, path: list[str]) -> list[str] | None:
        if node in visiting:
            return path[path.index(node) :] + [node]
        if node in visited:
            return None
        visiting.add(node)
        for dependency in graph.get(node, []):
            cycle = visit(dependency, path + [dependency])
            if cycle:
                return cycle
        visiting.remove(node)
        visited.add(node)
        return None

    for task_id in graph:
        cycle = visit(task_id, [task_id])
        if cycle:
            return cycle
    return None


def depends_on(graph: dict[str, list[str]], task_id: str, candidate: str) -> bool:
    pending = list(graph.get(task_id, []))
    seen: set[str] = set()
    while pending:
        dependency = pending.pop()
        if dependency == candidate:
            return True
        if dependency in seen:
            continue
        seen.add(dependency)
        pending.extend(graph.get(dependency, []))
    return False


def resolve_reference(
    value: str,
    run_dir: Path,
    project_root: Path,
    allowed_roots: list[str],
) -> Path | None:
    if value in {"", "null"}:
        return None
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = run_dir / path
    path = path.resolve()
    if not path.is_file():
        return None
    if not path_within(path, [run_dir, *allowed_roots], project_root):
        return None
    return path


def expected_payload_path(
    run_dir: Path,
    task_path: Path,
    task: dict[str, str],
    event: str,
    payload: Path,
    event_time: datetime | None = None,
) -> bool:
    owner = effective_owner(run_dir, task, at=event_time, operational=False)
    kind = EVENT_PAYLOAD_KINDS.get(event)
    resolved = payload.resolve()
    if kind == "task":
        return resolved == task_path.resolve()
    if kind == "ack":
        return (
            resolved.parent == (run_dir / "outbox" / owner).resolve()
            and resolved.name.startswith(f"{task['task_id']}-ack-")
            and resolved.suffix == ".yaml"
        )
    if kind == "lease":
        return (
            resolved.parent == (run_dir / "outbox" / owner).resolve()
            and resolved.name.startswith(f"{task['task_id']}-lease-")
            and resolved.suffix == ".yaml"
        )
    if kind == "result":
        return (
            resolved.parent == (run_dir / "outbox" / owner).resolve()
            and resolved.name.startswith(f"{task['task_id']}-result-")
            and resolved.suffix == ".md"
        )
    if kind == "dead_letter":
        return resolved.parent == (run_dir / "dead-letter").resolve()
    if kind == "gate":
        return resolved.parent == (run_dir / "decisions").resolve()
    if kind in {"review", "qa"}:
        return resolved.parent == (run_dir / "evidence").resolve()
    if kind == "result_or_evidence":
        result = (
            resolved.parent == (run_dir / "outbox" / owner).resolve()
            and resolved.name.startswith(f"{task['task_id']}-result-")
            and resolved.suffix == ".md"
        )
        return result or resolved.parent == (run_dir / "evidence").resolve()
    return True


def validate_event_payload_semantics(
    event_name: str,
    payload: Path,
    task_id: str,
    from_agent: str,
    errors: list[str],
) -> None:
    try:
        values = frontmatter(payload) if payload.suffix == ".md" else scalar_map(
            payload.read_text(encoding="utf-8"),
            source=str(payload),
        )
    except ProtocolError as exc:
        errors.append(str(exc))
        return
    if values.get("protocol_version") != PROTOCOL_VERSION:
        errors.append(f"{payload}: payload protocol_version must be {PROTOCOL_VERSION}")
    if values.get("task_id") not in {task_id, "null", None}:
        errors.append(f"{payload}: payload task_id mismatch")
    if values.get("kind") in {"ack", "lease", "result"} and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9._-]*",
        values.get("attempt_id", ""),
    ):
        errors.append(f"{payload}: missing or invalid attempt_id")
    requirements = {
        "ACK": ("ack", None),
        "LEASE_ACQUIRED": ("lease", None),
        "LEASE_RENEWED": ("lease", None),
        "CHANGES_REQUESTED": ("review", "changes_requested"),
        "REVIEW_APPROVED": ("review", "approved"),
        "QA_FAILED": ("qa", "failed"),
        "QA_PASSED": ("qa", "passed"),
        "WAITING_USER_APPROVAL": ("human_gate", None),
        "APPROVAL_GRANTED": ("human_gate", "approved"),
        "APPROVAL_REJECTED": ("human_gate", "rejected"),
        "RELEASE_READY": ("human_gate", "approved"),
        "DEAD_LETTERED": ("dead_letter", None),
    }
    expected = requirements.get(event_name)
    if expected:
        kind, status = expected
        if values.get("kind") != kind:
            errors.append(f"{payload}: {event_name} requires kind {kind}")
        if status and values.get("status") != status:
            errors.append(f"{payload}: {event_name} requires status {status}")
    if event_name == "RELEASE_READY" and values.get("scope") != "release":
        errors.append(f"{payload}: RELEASE_READY requires release scope")
    if event_name in {"HANDOFF_READY", "TASK_COMPLETED"} and values.get("status") != "completed":
        errors.append(f"{payload}: {event_name} requires completed result status")
    if event_name == "TASK_FAILED" and values.get("status") != "failed":
        errors.append(f"{payload}: TASK_FAILED requires failed result status")
    if event_name == "BLOCKED" and values.get("kind") == "result" and values.get("status") != "blocked":
        errors.append(f"{payload}: BLOCKED result requires blocked status")
    if event_name in {"CHANGES_REQUESTED", "REVIEW_APPROVED", "QA_FAILED", "QA_PASSED"}:
        if values.get("agent_id") != from_agent:
            errors.append(f"{payload}: evidence agent must match {event_name} sender")
    if event_name in {"LEASE_ACQUIRED", "LEASE_RENEWED"}:
        acquired = parse_iso(values.get("acquired_at", ""))
        expires = parse_iso(values.get("lease_expires_at", ""))
        if not acquired or not expires or expires <= acquired:
            errors.append(f"{payload}: invalid lease interval")


def actor_error(
    event: str,
    from_agent: str,
    to_agent: str,
    task: dict[str, str],
    *,
    run_dir: Path | None = None,
    agents: dict[str, dict[str, object]] | None = None,
    event_time: datetime | None = None,
    governance: str | None = None,
    dispatch_policy: str | None = None,
) -> str | None:
    owner = effective_owner(run_dir, task, at=event_time, operational=False) if run_dir else task["owner_agent"]
    agents = agents or {}
    publisher_authorized = (
        event == "TASK_READY"
        and from_agent == task.get("published_by")
        and governance != "strict"
        and dispatch_policy in {"hybrid", "self_service"}
        and "task_publish" in set(agents.get(from_agent, {}).get("capabilities", []))
    )
    claim_authorized = (
        event == "TASK_DISPATCHED"
        and task.get("assignment_mode", "fixed") == "claimable"
        and governance != "strict"
        and dispatch_policy in {"hybrid", "self_service"}
        and from_agent == owner
        and "task_claim" in set(agents.get(from_agent, {}).get("capabilities", []))
    )
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
        *DOCUMENT_SUBAGENT_EVENTS,
        *NATIVE_EVENTS,
    }
    if event in coordinator_events and from_agent != "coordinator":
        if not (publisher_authorized or claim_authorized):
            return f"{event} must come from coordinator"
    if event in {"TASK_READY", "TASK_DISPATCHED", "LEASE_ACQUIRED", "TASK_RESUMED"}:
        if not (event == "TASK_READY" and task.get("assignment_mode", "fixed") == "claimable" and to_agent == "coordinator") and to_agent != owner:
            return f"{event} must target owner {owner}"
    if event == "ACK" and from_agent not in {owner, "coordinator"}:
        return "ACK must come from owner or coordinator proxy"
    if event == "HANDOFF_READY" and from_agent != owner:
        return "HANDOFF_READY must come from owner"
    if event == "HANDOFF_READY":
        reviewer = task.get("reviewer_agent")
        if reviewer not in {None, "null", ""} and to_agent != reviewer:
            return "HANDOFF_READY must target reviewer"
    if event in {"REVIEW_STARTED", "CHANGES_REQUESTED", "REVIEW_APPROVED"}:
        if from_agent != task.get("reviewer_agent"):
            return f"{event} must come from reviewer"
    if event in {"QA_FAILED", "QA_PASSED"} and from_agent != task.get("qa_agent"):
        return f"{event} must come from QA"
    if event == "REVIEW_APPROVED" and to_agent != task.get("qa_agent"):
        return "REVIEW_APPROVED must target QA"
    if event == "RELEASE_READY":
        release_agent = task.get("release_agent")
        if release_agent in {None, "null", ""}:
            return "RELEASE_READY requires release_agent"
        if to_agent != release_agent:
            return "RELEASE_READY must target release_agent"
    if event in {"TASK_FAILED", "BLOCKED"} and from_agent not in {
        owner,
        "coordinator",
    }:
        return f"{event} must come from owner or coordinator"
    return None


def git_commit_exists(project_root: Path, commit: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(project_root), "cat-file", "-e", f"{commit}^{{commit}}"],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def git_worktree_clean(project_root: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(project_root), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        return False
    return not result.stdout.splitlines()


def git_current_branch(project_root: Path) -> str | None:
    root = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
    )
    branch = subprocess.run(
        ["git", "-C", str(project_root), "branch", "--show-current"],
        capture_output=True,
        text=True,
    )
    if (
        root.returncode
        or Path(root.stdout.strip()).resolve() != project_root
        or branch.returncode
        or not branch.stdout.strip()
    ):
        return None
    return branch.stdout.strip()


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
            return f"strict changed_file is not in implementation commit: {changed_file}"
    return None


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir).expanduser().resolve()
    errors: list[str] = []
    warnings: list[str] = []
    if not run_dir.is_dir():
        raise SystemExit(f"Run directory does not exist: {run_dir}")

    bus_root = run_dir.parent.parent
    required_bus_files = ("protocol.yaml", "project.yaml")
    required_run_files = ("agents.yaml", "manifest.yaml", "state.yaml", "next-action.md", "summary.md")
    for filename in required_bus_files:
        if not (bus_root / filename).is_file():
            errors.append(f"missing bus file: {filename}")
    for filename in required_run_files:
        if not (run_dir / filename).is_file():
            errors.append(f"missing run file: {filename}")
    for dirname in REQUIRED_DIRS:
        if not (run_dir / dirname).is_dir():
            errors.append(f"missing run directory: {dirname}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    protocol = add_protocol_error(
        errors,
        scalar_map,
        (bus_root / "protocol.yaml").read_text(encoding="utf-8"),
        source=str(bus_root / "protocol.yaml"),
    ) or {}
    project = add_protocol_error(
        errors,
        scalar_map,
        (bus_root / "project.yaml").read_text(encoding="utf-8"),
        source=str(bus_root / "project.yaml"),
    ) or {}
    manifest = add_protocol_error(
        errors,
        scalar_map,
        (run_dir / "manifest.yaml").read_text(encoding="utf-8"),
        source=str(run_dir / "manifest.yaml"),
    ) or {}
    if manifest.get("preflight_required") == "true" or "dispatch_policy" in manifest:
        for dirname in ("claims/tasks", "claims/threads", "config", "operations"):
            if not (run_dir / dirname).is_dir():
                errors.append(f"missing optimized run directory: {dirname}")
    state = add_protocol_error(
        errors,
        scalar_map,
        (run_dir / "state.yaml").read_text(encoding="utf-8"),
        source=str(run_dir / "state.yaml"),
    ) or {}
    agents_text = (run_dir / "agents.yaml").read_text(encoding="utf-8")
    agents_marker = "\nagents:\n"
    if agents_marker not in agents_text:
        errors.append("agents.yaml is missing the agents registry")
    agents_header = agents_text.split(agents_marker, 1)[0] + "\n"
    agents_top = add_protocol_error(
        errors,
        scalar_map,
        agents_header,
        source=str(run_dir / "agents.yaml"),
    ) or {}
    agents = add_protocol_error(
        errors,
        parse_agent_profiles,
        agents_text,
        source=str(run_dir / "agents.yaml"),
    ) or {}

    for source_name, values in (
        ("protocol", protocol),
        ("project", project),
        ("manifest", manifest),
        ("state", state),
        ("agents", agents_top),
    ):
        if values.get("protocol_version") != PROTOCOL_VERSION:
            errors.append(f"{source_name} protocol_version must be {PROTOCOL_VERSION}")
    for key in ("name", "delivery", "canonical_record", "secrets_policy"):
        if key not in protocol:
            errors.append(f"protocol is missing {key}")
    for key in (
        "project_id",
        "project_root",
        "allowed_roots",
        "created_at",
        "coordinator",
        "secrets_policy",
    ):
        if key not in project:
            errors.append(f"project is missing {key}")
    for key in (
        "run_id",
        "objective",
        "status",
        "governance",
        "transport",
        "max_parallel",
        "max_document_delegation_depth",
        "delivery",
        "ack_timeout_seconds",
        "lease_seconds",
        "max_attempts",
        "created_at",
        "user_confirmation_ref",
        "user_confirmation_ref_sha256",
        "versioning_mode",
        "release_train_id",
        "baseline_version",
        "target_version",
        "version_contract_ref",
        "version_contract_ref_sha256",
        "release_candidates",
        "change_id",
        "registry_ref",
        "registry_ref_sha256",
        "git_branch",
        "git_status_ref",
        "git_status_ref_sha256",
        "environment_impact_ref",
        "environment_impact_ref_sha256",
        "rollback_ref",
        "rollback_ref_sha256",
        "security_review_ref",
        "security_review_ref_sha256",
        "release_environment",
        "release_authorization_ref",
        "release_authorization_ref_sha256",
        "clean_worktree_ref",
        "clean_worktree_ref_sha256",
        "tasks",
        "human_gates",
    ):
        if key not in manifest:
            errors.append(f"manifest is missing {key}")
    for key in ("run_id", "status", "event_sequence", "task_states", "updated_at"):
        if key not in state:
            errors.append(f"state is missing {key}")
    if not valid_iso8601(project.get("created_at", "")):
        errors.append("project created_at requires ISO 8601 timezone")
    if not valid_iso8601(manifest.get("created_at", "")):
        errors.append("manifest created_at requires ISO 8601 timezone")
    if not valid_iso8601(state.get("updated_at", "")):
        errors.append("state updated_at requires ISO 8601 timezone")
    if protocol.get("delivery") != "at_least_once" or manifest.get("delivery") != "at_least_once":
        errors.append("protocol delivery must be at_least_once")
    run_id = manifest.get("run_id", "")
    if not re.fullmatch(r"RUN-[A-Za-z0-9._-]+", run_id):
        errors.append("manifest run_id has invalid format")
    if not run_id or state.get("run_id") != run_id or agents_top.get("run_id") != run_id:
        errors.append("run_id must match across manifest, state, and run-local agents")
    if manifest.get("governance") not in {"light", "standard", "strict"}:
        errors.append("invalid manifest governance")
    governance = manifest.get("governance", "")
    dispatch_policy = manifest.get("dispatch_policy", "central")
    if dispatch_policy not in {"central", "hybrid", "self_service"}:
        errors.append("invalid manifest dispatch_policy")
    if governance == "strict" and dispatch_policy != "central":
        errors.append("strict governance requires central dispatch_policy")
    if manifest.get("transport") not in {"codex_native", "document_bus", "hybrid"}:
        errors.append("invalid manifest transport")
    if manifest.get("status") not in RUN_STATUSES or state.get("status") not in RUN_STATUSES:
        errors.append("invalid run status")
    current_run_file = bus_root / "current-run"
    if not current_run_file.is_file():
        errors.append("missing current-run pointer")
    else:
        current_run_id = current_run_file.read_text(encoding="utf-8").strip()
        if not current_run_id or not (bus_root / "runs" / current_run_id).is_dir():
            errors.append("current-run does not point to an existing Run")

    project_root_value = project.get("project_root", "")
    project_root = Path(project_root_value).resolve() if project_root_value else bus_root.parent
    coordination_mode = manifest.get("coordination_mode")
    if coordination_mode == "coordinated":
        if manifest.get("governance_storage_schema") != STORAGE_SCHEMA:
            errors.append(f"coordinated run governance_storage_schema must be {STORAGE_SCHEMA}")
        binding_value = project.get("project_binding_ref", "")
        binding_path = Path(binding_value).expanduser().resolve() if binding_value else None
        if binding_path != (bus_root / "project-binding.yaml").resolve():
            errors.append("coordinated run project_binding_ref must identify its governance project")
        else:
            try:
                binding = load_project_binding(bus_root)
                if binding["project_root"] != str(project_root):
                    errors.append("governance binding project_root does not match project metadata")
                if binding["project_id"] != project.get("project_id"):
                    errors.append("governance binding project_id does not match project metadata")
            except ProtocolError as exc:
                errors.append(str(exc))
    elif project_root != bus_root.parent.resolve():
        errors.append("legacy project_root does not match document-bus location")
    allowed_roots = add_protocol_error(
        errors,
        json_string_list,
        project.get("allowed_roots", "[]"),
        field="allowed_roots",
        source=str(bus_root / "project.yaml"),
    ) or []
    if not allowed_roots or not path_within(project_root, allowed_roots, project_root):
        errors.append("project allowed_roots must include project_root")

    try:
        max_parallel = int(manifest.get("max_parallel", "0"))
        max_depth = int(manifest.get("max_document_delegation_depth", "0"))
        ack_timeout = int(manifest.get("ack_timeout_seconds", "0"))
        lease_seconds = int(manifest.get("lease_seconds", "0"))
        max_attempts = int(manifest.get("max_attempts", "0"))
        if min(max_parallel, max_depth, ack_timeout, lease_seconds, max_attempts) < 1:
            raise ValueError
    except ValueError:
        errors.append("manifest numeric policies must be positive integers")
        max_parallel = 1
        max_depth = 1
        ack_timeout = 1
        lease_seconds = 1
        max_attempts = 1

    for reference_field in (
        "user_confirmation_ref",
        "registry_ref",
        "git_status_ref",
        "environment_impact_ref",
        "rollback_ref",
        "security_review_ref",
        "release_authorization_ref",
        "clean_worktree_ref",
        "version_contract_ref",
    ):
        reference_value = manifest.get(reference_field, "null")
        hash_value = manifest.get(f"{reference_field}_sha256", "null")
        if reference_value == "null":
            if hash_value != "null":
                errors.append(f"{reference_field}_sha256 must be null when reference is null")
            continue
        reference_path = resolve_reference(
            reference_value,
            run_dir,
            project_root,
            allowed_roots,
        )
        if reference_path is None or hash_value != sha256(reference_path):
            errors.append(f"manifest {reference_field} hash mismatch or invalid reference")

    contract_path = (run_dir / "versions" / "version-contract.yaml").resolve()
    version_contract: dict[str, str] = {}
    if manifest.get("version_contract_ref") != str(contract_path):
        errors.append("manifest version_contract_ref must use the canonical run-local path")
    if contract_path.is_file():
        version_contract = add_protocol_error(
            errors,
            scalar_map,
            contract_path.read_text(encoding="utf-8"),
            source=str(contract_path),
        ) or {}
    required_contract_keys = (
        "protocol_version",
        "kind",
        "contract_version",
        "run_id",
        "release_train_id",
        "versioning_mode",
        "version_scheme",
        "baseline_version",
        "baseline_commit",
        "target_version",
        "version_source_ref",
        "version_source_sha256",
        "version_policy_ref",
        "version_policy_sha256",
        "owner_agent",
        "reason",
        "created_at",
    )
    missing_contract = [
        key for key in required_contract_keys if key not in version_contract
    ]
    if missing_contract:
        errors.append(
            "version contract is missing keys " + ", ".join(missing_contract)
        )
    if version_contract:
        expected_contract = {
            "protocol_version": PROTOCOL_VERSION,
            "kind": "version_contract",
            "contract_version": "1",
            "run_id": run_id,
            "release_train_id": manifest.get("release_train_id", ""),
            "versioning_mode": manifest.get("versioning_mode", ""),
            "baseline_version": manifest.get("baseline_version", ""),
            "target_version": manifest.get("target_version", ""),
            "owner_agent": "coordinator",
        }
        for key, expected in expected_contract.items():
            if version_contract.get(key) != expected:
                errors.append(f"version contract {key} does not match run")
        if not valid_iso8601(version_contract.get("created_at", "")):
            errors.append("version contract created_at requires ISO 8601 timezone")
        if not version_contract.get("reason", "").strip():
            errors.append("version contract requires a reason")
    versioning_mode = manifest.get("versioning_mode", "")
    if versioning_mode not in {"tracked", "not_applicable"}:
        errors.append("manifest requires tracked or not_applicable versioning")
    if not re.fullmatch(r"REL-[A-Za-z0-9._-]+", manifest.get("release_train_id", "")):
        errors.append("manifest release_train_id has invalid format")
    version_source: Path | None = None
    if versioning_mode == "tracked" and version_contract:
        if version_contract.get("version_scheme") not in {
            "semver",
            "calendar",
            "registry_managed",
            "custom",
        }:
            errors.append("tracked version contract has invalid version_scheme")
        if (
            manifest.get("baseline_version") in {"", "null", None}
            or manifest.get("target_version") in {"", "null", None}
            or manifest.get("baseline_version") == manifest.get("target_version")
        ):
            errors.append("tracked versioning requires distinct baseline and target versions")
        source_value = version_contract.get("version_source_ref", "")
        version_source = resolve_reference(
            source_value,
            run_dir,
            project_root,
            allowed_roots,
        )
        if version_source is None:
            errors.append("tracked versioning requires a valid project version source")
        elif version_contract.get("version_source_sha256") in {"", "null", None}:
            errors.append("tracked versioning requires the baseline version-source hash")
    elif versioning_mode == "not_applicable" and version_contract:
        for field in (
            "version_scheme",
            "baseline_version",
            "baseline_commit",
            "target_version",
            "version_source_ref",
            "version_source_sha256",
        ):
            if version_contract.get(field) != "null":
                errors.append(f"not_applicable version contract requires {field}: null")
    if version_contract:
        policy_value = version_contract.get("version_policy_ref", "null")
        policy_hash = version_contract.get("version_policy_sha256", "null")
        if policy_value == "null":
            if policy_hash != "null":
                errors.append("version_policy_sha256 must be null without a policy")
        else:
            policy_path = resolve_reference(
                policy_value,
                run_dir,
                project_root,
                allowed_roots,
            )
            if policy_path is None or policy_hash != sha256(policy_path):
                errors.append("version policy hash mismatch or invalid reference")

    manifest_candidates = add_protocol_error(
        errors,
        json_string_list,
        manifest.get("release_candidates", "[]"),
        field="release_candidates",
        source=str(run_dir / "manifest.yaml"),
    ) or []
    candidate_ids: list[str] = []
    for index, candidate_path in enumerate(
        sorted((run_dir / "versions" / "candidates").glob("*.yaml")),
        start=1,
    ):
        candidate = add_protocol_error(
            errors,
            scalar_map,
            candidate_path.read_text(encoding="utf-8"),
            source=str(candidate_path),
        ) or {}
        required_candidate = (
            "protocol_version",
            "kind",
            "run_id",
            "release_train_id",
            "candidate_id",
            "candidate_version",
            "target_version",
            "version_contract_sha256",
            "owner_agent",
            "implementation_commit",
            "artifact_refs",
            "artifact_hashes",
            "summary",
            "created_at",
        )
        missing_candidate = [
            key for key in required_candidate if key not in candidate
        ]
        if missing_candidate:
            errors.append(
                f"{candidate_path.name}: missing candidate keys "
                + ", ".join(missing_candidate)
            )
            continue
        expected_id = f"RC-{index:03d}"
        candidate_id = candidate["candidate_id"]
        candidate_ids.append(candidate_id)
        if candidate_id != expected_id or candidate_path.name != f"{candidate_id}.yaml":
            errors.append(f"{candidate_path.name}: release candidates must be sequential")
        for field, expected in (
            ("protocol_version", PROTOCOL_VERSION),
            ("kind", "release_candidate"),
            ("run_id", run_id),
            ("release_train_id", manifest.get("release_train_id", "")),
            ("target_version", manifest.get("target_version", "")),
            ("version_contract_sha256", sha256(contract_path) if contract_path.is_file() else ""),
            ("owner_agent", "coordinator"),
        ):
            if candidate.get(field) != expected:
                errors.append(f"{candidate_path.name}: {field} does not match run")
        expected_candidate_version = (
            f"{manifest.get('target_version', '')}-rc.{index}"
        )
        if candidate.get("candidate_version") != expected_candidate_version:
            errors.append(f"{candidate_path.name}: candidate_version is not canonical")
        if not valid_iso8601(candidate.get("created_at", "")):
            errors.append(f"{candidate_path.name}: created_at requires timezone")
        artifact_refs = add_protocol_error(
            errors,
            json_string_list,
            candidate.get("artifact_refs", "[]"),
            field="artifact_refs",
            source=candidate_path.name,
        ) or []
        artifact_hashes = add_protocol_error(
            errors,
            json_string_map,
            candidate.get("artifact_hashes", "{}"),
            field="artifact_hashes",
            source=candidate_path.name,
        ) or {}
        if set(artifact_refs) != set(artifact_hashes):
            errors.append(f"{candidate_path.name}: artifact hashes must cover refs")
        for reference in artifact_refs:
            artifact = resolve_reference(
                reference,
                run_dir,
                project_root,
                allowed_roots,
            )
            if artifact is None or artifact_hashes.get(reference) != sha256(artifact):
                errors.append(f"{candidate_path.name}: artifact hash mismatch")
    if manifest_candidates != candidate_ids:
        errors.append("manifest release_candidates must exactly match versions/candidates")

    agent_ids = {"user", "system", "external", *agents}
    if "coordinator" not in agents:
        errors.append("run-local agents must register coordinator")
    inboxes: set[str] = set()
    outboxes: set[str] = set()
    for agent_id, profile in agents.items():
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", agent_id):
            errors.append(f"agent has invalid identifier: {agent_id}")
        runtime = str(profile.get("runtime", ""))
        if runtime not in AGENT_RUNTIMES:
            errors.append(f"agent {agent_id}: invalid runtime")
        for field in ("role", "status", "delegation_depth", "inbox", "outbox"):
            if profile.get(field) in {None, ""}:
                errors.append(f"agent {agent_id}: missing {field}")
        if profile.get("status") not in {"ready", "active", "blocked", "closed"}:
            errors.append(f"agent {agent_id}: invalid status")
        readable = [str(item) for item in profile.get("readable_paths", [])]
        writable = [str(item) for item in profile.get("writable_paths", [])]
        forbidden = [str(item) for item in profile.get("forbidden_paths", [])]
        for scope in readable + writable + forbidden:
            if not path_within(scope, allowed_roots, project_root):
                errors.append(f"agent {agent_id}: scope exceeds allowed_roots: {scope}")
        for scope in writable:
            if not path_within(scope, readable, project_root):
                errors.append(f"agent {agent_id}: writable scope exceeds readable scope")
        inbox = str(profile.get("inbox", ""))
        outbox = str(profile.get("outbox", ""))
        if inbox in inboxes or outbox in outboxes:
            errors.append(f"agent {agent_id}: inbox/outbox must be unique")
        inboxes.add(inbox)
        outboxes.add(outbox)
        if inbox != f"inbox/{agent_id}" or not (run_dir / inbox).is_dir():
            errors.append(f"agent {agent_id}: invalid or missing inbox")
        if outbox != f"outbox/{agent_id}" or not (run_dir / outbox).is_dir():
            errors.append(f"agent {agent_id}: invalid or missing outbox")
        if runtime == "document_subagent":
            parent_id = str(profile.get("parent_agent_id", ""))
            parent = agents.get(parent_id)
            if not parent or parent_id == agent_id:
                errors.append(f"document_subagent {agent_id}: invalid parent")
                continue
            if parent.get("runtime") not in {"document", "document_subagent"}:
                errors.append(f"document_subagent {agent_id}: parent must use document runtime")
            try:
                depth = int(str(profile.get("delegation_depth", "-1")))
                parent_depth = int(str(parent.get("delegation_depth", "-1")))
                if depth != parent_depth + 1 or depth > max_depth:
                    errors.append(f"document_subagent {agent_id}: invalid delegation depth")
            except ValueError:
                errors.append(f"document_subagent {agent_id}: non-integer delegation depth")
            for scope in readable:
                if not path_within(scope, [str(item) for item in parent.get("readable_paths", [])], project_root):
                    errors.append(f"document_subagent {agent_id}: readable scope exceeds parent")
            for scope in writable:
                if not path_within(scope, [str(item) for item in parent.get("writable_paths", [])], project_root):
                    errors.append(f"document_subagent {agent_id}: writable scope exceeds parent")
            parent_forbidden = [str(item) for item in parent.get("forbidden_paths", [])]
            for parent_scope in parent_forbidden:
                if not path_within(parent_scope, forbidden, project_root):
                    errors.append(f"document_subagent {agent_id}: must inherit parent forbidden scope")

    manifest_tasks = add_protocol_error(
        errors,
        json_string_list,
        manifest.get("tasks", "[]"),
        field="tasks",
        source=str(run_dir / "manifest.yaml"),
    ) or []
    if len(manifest_tasks) != len(set(manifest_tasks)):
        errors.append("manifest tasks contains duplicates")
    tasks: dict[str, dict[str, str]] = {}
    task_paths: dict[str, Path] = {}
    task_lists: dict[str, dict[str, list[str]]] = {}
    task_gate_hashes: dict[str, dict[str, str]] = {}
    dependency_graph: dict[str, list[str]] = {}
    task_idempotency_keys: set[str] = set()
    for task_path in sorted((run_dir / "tasks").glob("*.md")):
        values = add_protocol_error(errors, frontmatter, task_path)
        if values is None:
            continue
        missing = [key for key in TASK_KEYS if key not in values]
        if missing:
            errors.append(f"{task_path.name}: missing task keys {', '.join(missing)}")
            continue
        task_id = values["task_id"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", task_id):
            errors.append(f"{task_path.name}: invalid task_id format")
        if task_id in tasks:
            errors.append(f"duplicate task_id: {task_id}")
            continue
        if task_path.name != f"{task_id}.md":
            errors.append(f"{task_path.name}: filename must match task_id")
        if values["protocol_version"] != PROTOCOL_VERSION or values["run_id"] != run_id:
            errors.append(f"{task_path.name}: protocol or run mismatch")
        if values["status"] != "draft":
            errors.append(f"{task_path.name}: frozen task status must remain draft")
        if values["release_train_id"] != manifest.get("release_train_id"):
            errors.append(f"{task_path.name}: release_train_id does not match run")
        if values["delivery_version"] != manifest.get("target_version"):
            errors.append(f"{task_path.name}: delivery_version does not match run")
        if (
            not contract_path.is_file()
            or values["version_contract_sha256"] != sha256(contract_path)
        ):
            errors.append(f"{task_path.name}: version contract binding is invalid")
        if not valid_iso8601(values["created_at"]):
            errors.append(f"{task_path.name}: created_at requires ISO 8601 timezone")
        if values["idempotency_key"] in task_idempotency_keys:
            errors.append(f"{task_path.name}: duplicate task idempotency_key")
        task_idempotency_keys.add(values["idempotency_key"])
        owner = values["owner_agent"]
        assignment_mode = values.get("assignment_mode", "fixed")
        if assignment_mode not in {"fixed", "claimable"}:
            errors.append(f"{task_path.name}: invalid assignment_mode")
        eligible_agents: list[str] = []
        if assignment_mode == "claimable":
            if owner != "pool":
                errors.append(f"{task_path.name}: claimable task owner must be pool")
            try:
                eligible_agents = json_string_list(
                    values.get("eligible_agents", "[]"),
                    field="eligible_agents",
                    source=task_path.name,
                )
            except ProtocolError as exc:
                errors.append(str(exc))
            if not eligible_agents:
                errors.append(f"{task_path.name}: claimable task needs eligible_agents")
            for eligible in eligible_agents:
                if eligible not in agents:
                    errors.append(f"{task_path.name}: eligible agent is not registered: {eligible}")
        elif owner not in agents:
            errors.append(f"{task_path.name}: owner is not registered")
        published_by = values.get("published_by", "coordinator")
        if published_by not in agents:
            errors.append(f"{task_path.name}: published_by is not registered")
        parent_task_id = values.get("parent_task_id", "null")
        parent_hash = values.get("parent_task_sha256", "null")
        if parent_task_id not in {"null", "", None}:
            parent_path = run_dir / "tasks" / f"{parent_task_id}.md"
            if not parent_path.is_file() or parent_hash != sha256(parent_path):
                errors.append(f"{task_path.name}: parent task reference or hash is invalid")
        for role in ("reviewer_agent", "qa_agent", "release_agent"):
            role_agent = values.get(role, "null")
            if role_agent != "null" and role_agent not in agents:
                errors.append(f"{task_path.name}: {role} is not registered")
        lists: dict[str, list[str]] = {}
        for field in (
            "dependencies",
            "owned_paths",
            "forbidden_paths",
            "risk_flags",
            "human_gates",
        ):
            parsed = add_protocol_error(
                errors,
                json_string_list,
                values[field],
                field=field,
                source=task_path.name,
            )
            lists[field] = parsed or []
            if len(lists[field]) != len(set(lists[field])):
                errors.append(f"{task_path.name}: {field} contains duplicates")
        try:
            resource_steps = json.loads(values.get("resource_steps", "[]"))
        except json.JSONDecodeError:
            resource_steps = None
            errors.append(f"{task_path.name}: resource_steps must be an inline JSON list")
        if resource_steps is not None:
            if not isinstance(resource_steps, list):
                errors.append(f"{task_path.name}: resource_steps must be a list")
            else:
                step_ids: set[str] = set()
                for step in resource_steps:
                    if not isinstance(step, dict) or not isinstance(step.get("step_id"), str) or not step.get("step_id"):
                        errors.append(f"{task_path.name}: resource step needs a step_id")
                        continue
                    if step["step_id"] in step_ids:
                        errors.append(f"{task_path.name}: duplicate resource step_id {step['step_id']}")
                    step_ids.add(step["step_id"])
                    resources = step.get("resources", [])
                    if not isinstance(resources, list) or not resources or not all(isinstance(item, str) and item for item in resources):
                        errors.append(f"{task_path.name}: resource step {step['step_id']} needs non-empty resources")
        owner_profiles = [agents[item] for item in eligible_agents if item in agents] if assignment_mode == "claimable" else [agents.get(owner, {})]
        owner_writable = [str(item) for profile in owner_profiles for item in profile.get("writable_paths", [])]
        owner_forbidden = [str(item) for profile in owner_profiles for item in profile.get("forbidden_paths", [])]
        for owned_path in lists["owned_paths"]:
            if assignment_mode == "claimable":
                if any(not path_within(owned_path, [str(scope) for scope in profile.get("writable_paths", [])], project_root) for profile in owner_profiles):
                    errors.append(f"{task_path.name}: owned path is not writable by every eligible agent: {owned_path}")
            elif not path_within(owned_path, owner_writable, project_root):
                errors.append(f"{task_path.name}: owned path exceeds owner scope: {owned_path}")
            if any(path_within(owned_path, [str(scope) for scope in profile.get("forbidden_paths", [])], project_root) for profile in owner_profiles):
                errors.append(f"{task_path.name}: owned path is forbidden: {owned_path}")
        for forbidden_path in lists["forbidden_paths"]:
            if not path_within(forbidden_path, allowed_roots, project_root):
                errors.append(
                    f"{task_path.name}: forbidden path exceeds allowed_roots: {forbidden_path}"
                )
        for forbidden_path in owner_forbidden:
            if not path_within(forbidden_path, lists["forbidden_paths"], project_root):
                errors.append(f"{task_path.name}: task must inherit owner forbidden paths")
        owner_profile = owner_profiles[0] if owner_profiles else {}
        if assignment_mode == "fixed" and owner_profile.get("runtime") == "document_subagent":
            if values.get("reviewer_agent") != owner_profile.get("parent_agent_id"):
                errors.append(f"{task_path.name}: parent must review document_subagent task")
        tasks[task_id] = values
        task_paths[task_id] = task_path.resolve()
        task_lists[task_id] = lists
        task_gate_hashes[task_id] = add_protocol_error(
            errors,
            json_string_map,
            values["human_gate_hashes"],
            field="human_gate_hashes",
            source=task_path.name,
        ) or {}
        if set(task_gate_hashes[task_id]) != set(lists["human_gates"]):
            errors.append(
                f"{task_path.name}: human_gate_hashes must exactly cover human_gates"
            )
        dependency_graph[task_id] = lists["dependencies"]
    if set(manifest_tasks) != set(tasks):
        errors.append("manifest task index must exactly match tasks/")
    for task_id, dependencies in dependency_graph.items():
        for dependency in dependencies:
            if dependency not in tasks:
                errors.append(f"{task_id}: dependency does not exist: {dependency}")
    cycle = dependency_cycle(dependency_graph)
    if cycle:
        errors.append(f"task dependency cycle: {' -> '.join(cycle)}")

    decisions: dict[str, dict[str, str]] = {}
    decision_paths: dict[str, Path] = {}
    for path in sorted((run_dir / "decisions").glob("*.yaml")):
        if path.name == "scope-freeze.yaml":
            # Scope freeze is a boundary document, not a human_gate.  It is
            # validated separately below and must not be folded into the
            # manifest human_gates index.
            continue
        values = add_protocol_error(
            errors,
            scalar_map,
            path.read_text(encoding="utf-8"),
            source=str(path),
        )
        if values is None:
            continue
        required = (
            "protocol_version",
            "kind",
            "run_id",
            "gate_id",
            "task_id",
            "scope",
            "status",
            "approved_by",
            "approved_at",
            "summary",
        )
        missing = [key for key in required if key not in values]
        if missing:
            errors.append(f"{path.name}: missing gate keys {', '.join(missing)}")
            continue
        gate_id = values["gate_id"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", gate_id):
            errors.append(f"{path.name}: invalid gate_id format")
        if path.name != f"{gate_id}.yaml":
            errors.append(f"{path.name}: filename must match gate_id")
        if gate_id in decisions:
            errors.append(f"duplicate gate_id: {gate_id}")
        if values["protocol_version"] != PROTOCOL_VERSION or values["run_id"] != run_id:
            errors.append(f"{path.name}: gate protocol or run mismatch")
        if values["kind"] != "human_gate" or values["status"] not in {
            "pending",
            "approved",
            "rejected",
        }:
            errors.append(f"{path.name}: invalid human gate")
        if not valid_iso8601(values["approved_at"]):
            errors.append(f"{path.name}: approved_at requires timezone")
        if values["task_id"] not in {"null", *tasks}:
            errors.append(f"{path.name}: gate task does not exist")
        decisions[gate_id] = values
        decision_paths[gate_id] = path.resolve()
    scope_path = run_dir / "decisions" / "scope-freeze.yaml"
    if scope_path.is_file():
        scope_values = add_protocol_error(
            errors,
            scalar_map,
            scope_path.read_text(encoding="utf-8"),
            source=str(scope_path),
        ) or {}
        scope_required = (
            "protocol_version", "kind", "run_id", "scope_id", "objective_sha256",
            "requested_paths", "forbidden_paths", "target_environment", "max_parallel",
            "governance", "execution_profile", "dispatch_policy", "version_contract_sha256",
            "owner_agent", "created_at",
        )
        missing = [key for key in scope_required if key not in scope_values]
        if missing:
            errors.append(f"scope-freeze.yaml: missing scope keys {', '.join(missing)}")
        else:
            if (
                scope_values.get("protocol_version") != PROTOCOL_VERSION
                or scope_values.get("kind") != "scope_freeze"
                or scope_values.get("run_id") != run_id
                or scope_values.get("owner_agent") != "coordinator"
            ):
                errors.append("scope-freeze.yaml: scope protocol or owner mismatch")
            if not valid_iso8601(scope_values.get("created_at", "")):
                errors.append("scope-freeze.yaml: created_at requires timezone")
            requested = add_protocol_error(errors, json_string_list, scope_values.get("requested_paths", "[]"), field="requested_paths", source=str(scope_path)) or []
            forbidden = add_protocol_error(errors, json_string_list, scope_values.get("forbidden_paths", "[]"), field="forbidden_paths", source=str(scope_path)) or []
            for boundary in [*requested, *forbidden]:
                if not path_within(boundary, allowed_roots, project_root):
                    errors.append(f"scope-freeze.yaml: boundary exceeds project allowed_roots: {boundary}")
            for left in requested:
                if any(paths_overlap(left, right, project_root) for right in requested if right != left):
                    errors.append(f"scope-freeze.yaml: requested paths overlap: {left}")
            if scope_values.get("governance") != governance or scope_values.get("dispatch_policy") != manifest.get("dispatch_policy", "central"):
                errors.append("scope-freeze.yaml: run policy does not match manifest")
            if manifest.get("scope_freeze_ref") not in {"", "null", None}:
                ref = Path(manifest["scope_freeze_ref"]).expanduser()
                if not ref.is_absolute():
                    ref = run_dir / ref
                if ref.resolve() != scope_path.resolve() or manifest.get("scope_freeze_ref_sha256") != sha256(scope_path):
                    errors.append("manifest scope freeze reference or hash mismatch")
    elif manifest.get("preflight_required", "false") == "true":
        # Structure/lifecycle validation remains compatible with a newly
        # initialized Run before its dispatch boundary is frozen.  The
        # read-only preflight and Coordinator are the enforcement point and
        # will block dispatch until this document exists.
        warnings.append("preflight-required run has no scope freeze; dispatch remains blocked")
    manifest_gates = add_protocol_error(
        errors,
        json_string_list,
        manifest.get("human_gates", "[]"),
        field="human_gates",
        source=str(run_dir / "manifest.yaml"),
    ) or []
    if set(manifest_gates) != set(decisions):
        errors.append("manifest human_gates must exactly match decisions/")
    for task_id, gate_ids in (
        (task_id, task_lists[task_id]["human_gates"]) for task_id in tasks
    ):
        for gate_id in gate_ids:
            gate_path = decision_paths.get(gate_id)
            if not gate_path:
                errors.append(f"{task_id}: declared human gate does not exist: {gate_id}")
            elif task_gate_hashes[task_id].get(gate_id) != sha256(gate_path):
                errors.append(f"{task_id}: human gate hash mismatch: {gate_id}")
    confirmation_ref = resolve_reference(
        manifest.get("user_confirmation_ref", ""),
        run_dir,
        project_root,
        allowed_roots,
    )
    if confirmation_ref is None:
        errors.append("manifest user_confirmation_ref is missing or invalid")
    else:
        if manifest.get("user_confirmation_ref_sha256") != sha256(confirmation_ref):
            errors.append("manifest user confirmation hash mismatch")
        confirmation = next(
            (item for item in decisions.values() if decision_paths.get(item["gate_id"]) == confirmation_ref),
            None,
        )
        if not confirmation or confirmation.get("scope") != "run_initialization" or confirmation.get("status") != "approved":
            errors.append("run initialization requires an approved user confirmation")

    evidence_by_path: dict[Path, dict[str, str]] = {}
    evidence_ids: set[str] = set()
    for path in sorted((run_dir / "evidence").glob("*.yaml")):
        values = add_protocol_error(
            errors,
            scalar_map,
            path.read_text(encoding="utf-8"),
            source=str(path),
        )
        if values is None:
            continue
        required = (
            "protocol_version",
            "kind",
            "run_id",
            "evidence_id",
            "task_id",
            "agent_id",
            "status",
            "summary",
            "artifact_refs",
            "artifact_hashes",
            "created_at",
        )
        missing = [key for key in required if key not in values]
        if missing:
            errors.append(f"{path.name}: missing evidence keys {', '.join(missing)}")
            continue
        if values["protocol_version"] != PROTOCOL_VERSION or values["run_id"] != run_id:
            errors.append(f"{path.name}: evidence protocol or run mismatch")
        if values["evidence_id"] in evidence_ids:
            errors.append(f"{path.name}: duplicate evidence_id")
        evidence_ids.add(values["evidence_id"])
        if not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]*",
            values["evidence_id"],
        ):
            errors.append(f"{path.name}: invalid evidence_id format")
        if path.name != f"{values['evidence_id']}.yaml":
            errors.append(f"{path.name}: filename must match evidence_id")
        if values["agent_id"] not in agents:
            errors.append(f"{path.name}: evidence agent is not registered")
        if values["task_id"] not in {"null", *tasks}:
            errors.append(f"{path.name}: evidence task does not exist")
        if not valid_iso8601(values["created_at"]):
            errors.append(f"{path.name}: evidence created_at requires timezone")
        artifact_refs = add_protocol_error(
            errors,
            json_string_list,
            values["artifact_refs"],
            field="artifact_refs",
            source=path.name,
        ) or []
        artifact_hashes = add_protocol_error(
            errors,
            json_string_map,
            values["artifact_hashes"],
            field="artifact_hashes",
            source=path.name,
        ) or {}
        if set(artifact_hashes) != set(artifact_refs):
            errors.append(f"{path.name}: artifact_hashes must exactly cover artifact_refs")
        for reference in artifact_refs:
            artifact_path = resolve_reference(
                reference,
                run_dir,
                project_root,
                allowed_roots,
            )
            if artifact_path is None:
                errors.append(f"{path.name}: invalid artifact reference {reference}")
            elif artifact_hashes.get(reference) != sha256(artifact_path):
                errors.append(f"{path.name}: artifact hash mismatch {reference}")
        evidence_by_path[path.resolve()] = values

    try:
        records = event_records(run_dir / "events")
    except ProtocolError as exc:
        errors.append(str(exc))
        records = []
    sequences: set[int] = set()
    event_ids: set[str] = set()
    events_by_id: dict[str, dict[str, str]] = {}
    idempotency_keys: set[str] = set()
    task_states: dict[str, str] = {}
    task_event_names: dict[str, set[str]] = {}
    task_event_records: dict[str, list[dict[str, str]]] = {}
    task_hashes: dict[str, str] = {}
    event_payloads: dict[tuple[str, str], list[Path]] = {}
    payload_event_names: dict[Path, list[str]] = {}
    payload_values_by_path: dict[Path, dict[str, str]] = {}
    lease_expirations: dict[str, datetime] = {}
    for path, values in records:
        missing = [key for key in EVENT_KEYS if key not in values]
        if missing:
            errors.append(f"{path.name}: missing event keys {', '.join(missing)}")
            continue
        try:
            sequence = int(values["sequence"])
        except ValueError:
            errors.append(f"{path.name}: sequence must be an integer")
            continue
        if sequence in sequences or not path.name.startswith(f"{sequence:06d}-"):
            errors.append(f"{path.name}: duplicate or mismatched sequence")
        sequences.add(sequence)
        try:
            uuid.UUID(values["event_id"])
        except ValueError:
            errors.append(f"{path.name}: event_id must be a UUID")
        if values["event_id"] in event_ids:
            errors.append(f"{path.name}: duplicate event_id")
        causation = values["causation_id"]
        if causation != "null" and causation not in event_ids:
            errors.append(f"{path.name}: causation_id must reference an earlier event")
        event_ids.add(values["event_id"])
        events_by_id[values["event_id"]] = values
        if values["idempotency_key"] in idempotency_keys:
            errors.append(f"{path.name}: duplicate event idempotency_key")
        idempotency_keys.add(values["idempotency_key"])
        event_name = values["event"]
        task_id = values["task_id"]
        if values["protocol_version"] != PROTOCOL_VERSION or values["run_id"] != run_id:
            errors.append(f"{path.name}: event protocol or run mismatch")
        if event_name not in EVENT_NAMES:
            errors.append(f"{path.name}: unknown event")
        if task_id not in tasks:
            errors.append(f"{path.name}: task does not exist")
            continue
        claimable_task = tasks[task_id].get("assignment_mode", "fixed") == "claimable"
        valid_event_agents = agent_ids | ({"pool"} if claimable_task else set())
        if values["from_agent"] not in valid_event_agents or values["to_agent"] not in valid_event_agents:
            errors.append(f"{path.name}: event agent is not registered")
        actor_problem = actor_error(
            event_name,
            values["from_agent"],
            values["to_agent"],
            tasks[task_id],
            run_dir=run_dir,
            agents=agents,
            event_time=parse_iso(values.get("created_at", "")),
            governance=governance,
            dispatch_policy=manifest.get("dispatch_policy", "central"),
        )
        if actor_problem:
            errors.append(f"{path.name}: {actor_problem}")
        if not valid_iso8601(values["created_at"]):
            errors.append(f"{path.name}: created_at requires timezone")
        payload_value = values["payload_path"]
        payload_hash = values["payload_sha256"]
        if event_name in EVENT_PAYLOAD_KINDS and (
            payload_value == "null" or payload_hash == "null"
        ):
            errors.append(f"{path.name}: {event_name} requires a hashed payload")
        if payload_value != "null":
            payload = Path(payload_value)
            if not payload.is_absolute():
                payload = run_dir / payload
            payload = payload.resolve()
            if not payload.is_file():
                errors.append(f"{path.name}: payload does not exist")
            elif payload_hash == "null" or sha256(payload) != payload_hash:
                errors.append(f"{path.name}: payload hash mismatch")
            else:
                if not expected_payload_path(
                    run_dir,
                    task_paths[task_id],
                    tasks[task_id],
                    event_name,
                    payload,
                    parse_iso(values.get("created_at", "")),
                ):
                    errors.append(f"{path.name}: payload path is not valid for {event_name}")
                validate_event_payload_semantics(
                    event_name,
                    payload,
                    task_id,
                    values["from_agent"],
                    errors,
                )
                try:
                    payload_values_by_path[payload] = (
                        frontmatter(payload)
                        if payload.suffix == ".md"
                        else scalar_map(
                            payload.read_text(encoding="utf-8"),
                            source=str(payload),
                        )
                    )
                except ProtocolError:
                    pass
                if event_name in {"LEASE_ACQUIRED", "LEASE_RENEWED"}:
                    lease_values = scalar_map(
                        payload.read_text(encoding="utf-8"),
                        source=str(payload),
                    )
                    task = tasks[task_id]
                    expires = parse_iso(lease_values.get("lease_expires_at", ""))
                    if (
                        lease_values.get("agent_id") != effective_owner(
                            run_dir,
                            task,
                            at=parse_iso(values.get("created_at", "")),
                            operational=False,
                        )
                        or lease_values.get("task_sha256") != sha256(task_paths[task_id])
                    ):
                        errors.append(f"{path.name}: lease ownership or task hash mismatch")
                    if expires:
                        lease_expirations[task_id] = expires
                event_payloads.setdefault((task_id, event_name), []).append(payload)
                payload_event_names.setdefault(payload, []).append(event_name)
        if event_name == "TASK_READY":
            expected_hash = sha256(task_paths[task_id])
            if payload_hash != expected_hash:
                errors.append(f"{path.name}: TASK_READY hash is not the task document hash")
            task_hashes[task_id] = expected_hash
            for dependency in dependency_graph.get(task_id, []):
                if task_states.get(dependency) != "completed":
                    errors.append(f"{path.name}: dependency is not completed: {dependency}")
        current = task_states.get(task_id)
        new_state = next_task_state(current, event_name, governance)
        if new_state is None:
            errors.append(f"{path.name}: illegal transition {current or 'none'} -> {event_name}")
        else:
            task_states[task_id] = new_state
        task_event_names.setdefault(task_id, set()).add(event_name)
        task_event_records.setdefault(task_id, []).append(values)
    if sequences and sorted(sequences) != list(range(1, max(sequences) + 1)):
        errors.append("event sequence contains gaps")

    try:
        state_sequence = int(state.get("event_sequence", "-1"))
    except ValueError:
        state_sequence = -1
        errors.append("state event_sequence must be an integer")
    expected_sequence = max(sequences) if sequences else 0
    if state_sequence != expected_sequence:
        errors.append("state event_sequence does not match events")
    state_task_states = add_protocol_error(
        errors,
        json_string_map,
        state.get("task_states", "{}"),
        field="task_states",
        source=str(run_dir / "state.yaml"),
    ) or {}
    if state_task_states != task_states:
        errors.append("state task_states does not match event replay")
    for status_name in TASK_STATUSES:
        listed = add_protocol_error(
            errors,
            json_string_list,
            state.get(f"{status_name}_tasks", "[]"),
            field=f"{status_name}_tasks",
            source=str(run_dir / "state.yaml"),
        ) or []
        expected = sorted(task_id for task_id, value in task_states.items() if value == status_name)
        if sorted(listed) != expected:
            errors.append(f"state {status_name}_tasks does not match event replay")
    derived_status = derive_run_status(task_states)
    archived = (run_dir / "archive" / "ARCHIVED.yaml").is_file()
    expected_run_status = "archived" if archived else derived_status
    if state.get("status") != expected_run_status or manifest.get("status") != expected_run_status:
        errors.append("manifest/state status does not match event-derived run status")
    next_action_text = (run_dir / "next-action.md").read_text(encoding="utf-8")
    next_status = re.search(r"^- Current status: `([^`]+)`$", next_action_text, re.MULTILINE)
    if not next_status or next_status.group(1) != expected_run_status:
        errors.append("next-action status does not match event-derived run status")
    summary_text = (run_dir / "summary.md").read_text(encoding="utf-8")
    summary_status = re.search(r"^- Status: (.+)$", summary_text, re.MULTILINE)
    if not summary_status or summary_status.group(1).strip() != expected_run_status:
        errors.append("summary status does not match event-derived run status")
    if manifest.get("status") in {"completed", "release_ready", "archived"} and not tasks:
        errors.append("terminal or release-ready run must contain tasks")
    if archived:
        archive_marker = run_dir / "archive" / "ARCHIVED.yaml"
        archive_values = add_protocol_error(
            errors,
            scalar_map,
            archive_marker.read_text(encoding="utf-8"),
            source=str(archive_marker),
        ) or {}
        if (
            archive_values.get("protocol_version") != PROTOCOL_VERSION
            or archive_values.get("kind") != "run_archive"
            or archive_values.get("run_id") != run_id
            or not valid_iso8601(archive_values.get("archived_at", ""))
            or archive_values.get("validation_sha256") != sha256(run_dir / "state.yaml")
        ):
            errors.append("archive marker does not match immutable final state")

    active_parallel_states = {"dispatched", "acknowledged", "running", "reviewing", "qa_running"}
    if sum(state_value in active_parallel_states for state_value in task_states.values()) > max_parallel:
        errors.append("active tasks exceed manifest max_parallel")
    task_ids_sorted = sorted(tasks)
    for index, left_id in enumerate(task_ids_sorted):
        for right_id in task_ids_sorted[index + 1 :]:
            overlap = any(
                paths_overlap(left, right, project_root)
                for left in task_lists[left_id]["owned_paths"]
                for right in task_lists[right_id]["owned_paths"]
            )
            if overlap and not (
                depends_on(dependency_graph, left_id, right_id)
                or depends_on(dependency_graph, right_id, left_id)
            ):
                errors.append(
                    f"tasks {left_id} and {right_id} have overlapping owned_paths without serialization"
                )

    ack_tasks: set[str] = set()
    ack_attempts: set[tuple[str, str]] = set()
    ack_idempotency_keys: set[str] = set()
    result_values_by_path: dict[Path, dict[str, str]] = {}
    result_attempts: set[tuple[str, str]] = set()
    result_idempotency_keys: set[str] = set()
    for agent_id in agents:
        outbox = run_dir / "outbox" / agent_id
        for ack_path in sorted(outbox.glob("*-ack-*.yaml")):
            values = add_protocol_error(
                errors,
                scalar_map,
                ack_path.read_text(encoding="utf-8"),
                source=str(ack_path),
            )
            if values is None:
                continue
            required = (
                "protocol_version",
                "kind",
                "run_id",
                "task_id",
                "task_sha256",
                "agent_id",
                "attempt_id",
                "acknowledged_at",
                "lease_expires_at",
                "idempotency_key",
            )
            missing = [key for key in required if key not in values]
            if missing:
                errors.append(f"{ack_path.name}: missing ACK keys {', '.join(missing)}")
                continue
            task_id = values["task_id"]
            attempt_id = values["attempt_id"]
            attempt_key = (task_id, attempt_id)
            if attempt_key in ack_attempts:
                errors.append(f"{task_id}: duplicate ACK attempt {attempt_id}")
            ack_attempts.add(attempt_key)
            ack_tasks.add(task_id)
            if values["idempotency_key"] in ack_idempotency_keys:
                errors.append(f"{ack_path.name}: duplicate ACK idempotency_key")
            ack_idempotency_keys.add(values["idempotency_key"])
            task = tasks.get(task_id)
            if (
                values["protocol_version"] != PROTOCOL_VERSION
                or values["kind"] != "ack"
                or values["run_id"] != run_id
                or not task
                or values["agent_id"] != effective_owner(
                    run_dir,
                    task,
                    at=parse_iso(values.get("acknowledged_at", "")),
                    operational=False,
                )
                or ack_path.parent.name != values["agent_id"]
            ):
                errors.append(f"{ack_path.name}: ACK ownership or protocol mismatch")
            elif values["task_sha256"] != sha256(task_paths[task_id]):
                errors.append(f"{ack_path.name}: ACK task hash mismatch")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", attempt_id):
                errors.append(f"{ack_path.name}: invalid attempt_id")
            if ack_path.name != f"{task_id}-ack-{attempt_id}.yaml":
                errors.append(f"{ack_path.name}: filename does not match ACK attempt")
            acknowledged = parse_iso(values["acknowledged_at"])
            expires = parse_iso(values["lease_expires_at"])
            if not acknowledged or not expires or expires <= acknowledged:
                errors.append(f"{ack_path.name}: invalid ACK lease interval")
            payloads = event_payloads.get((task_id, "ACK"), [])
            if ack_path.resolve() not in payloads:
                errors.append(f"{ack_path.name}: ACK event must hash this exact document")

        for lease_path in sorted(outbox.glob("*-lease-*.yaml")):
            values = add_protocol_error(
                errors,
                scalar_map,
                lease_path.read_text(encoding="utf-8"),
                source=str(lease_path),
            )
            if values is None:
                continue
            required = (
                "protocol_version",
                "kind",
                "run_id",
                "task_id",
                "task_sha256",
                "agent_id",
                "attempt_id",
                "lease_id",
                "acquired_at",
                "lease_expires_at",
            )
            missing = [key for key in required if key not in values]
            if missing:
                errors.append(f"{lease_path.name}: missing lease keys {', '.join(missing)}")
                continue
            task_id = values["task_id"]
            attempt_id = values["attempt_id"]
            task = tasks.get(task_id)
            if (
                values["protocol_version"] != PROTOCOL_VERSION
                or values["kind"] != "lease"
                or values["run_id"] != run_id
                or not task
                or values["agent_id"] != effective_owner(
                    run_dir,
                    task,
                    at=parse_iso(values.get("acquired_at", "")),
                    operational=False,
                )
                or lease_path.parent.name != values["agent_id"]
            ):
                errors.append(f"{lease_path.name}: lease ownership or protocol mismatch")
            elif values["task_sha256"] != sha256(task_paths[task_id]):
                errors.append(f"{lease_path.name}: lease task hash mismatch")
            expected_name = (
                f"{task_id}-lease-{attempt_id}-{values['lease_id']}.yaml"
            )
            if not re.fullmatch(
                r"[A-Za-z0-9][A-Za-z0-9._-]*",
                values["lease_id"],
            ):
                errors.append(f"{lease_path.name}: invalid lease_id")
            if lease_path.name != expected_name:
                errors.append(f"{lease_path.name}: filename does not match lease attempt")
            acquired = parse_iso(values["acquired_at"])
            expires = parse_iso(values["lease_expires_at"])
            if not acquired or not expires or expires <= acquired:
                errors.append(f"{lease_path.name}: invalid lease interval")
            if (task_id, attempt_id) not in ack_attempts:
                errors.append(f"{lease_path.name}: lease attempt has no matching ACK")
            lease_events = {
                "LEASE_ACQUIRED",
                "LEASE_RENEWED",
            }.intersection(payload_event_names.get(lease_path.resolve(), []))
            if not lease_events:
                errors.append(f"{lease_path.name}: lease event must hash this exact document")
        for result_path in sorted(outbox.glob("*-result-*.md")):
            values = add_protocol_error(errors, frontmatter, result_path)
            if values is None:
                continue
            missing = [key for key in RESULT_KEYS if key not in values]
            if missing:
                errors.append(f"{result_path.name}: missing result keys {', '.join(missing)}")
                continue
            task_id = values["task_id"]
            attempt_id = values["attempt_id"]
            attempt_key = (task_id, attempt_id)
            if attempt_key in result_attempts:
                errors.append(f"{task_id}: duplicate result attempt {attempt_id}")
            result_attempts.add(attempt_key)
            result_values_by_path[result_path.resolve()] = values
            if values["idempotency_key"] in result_idempotency_keys:
                errors.append(f"{result_path.name}: duplicate result idempotency_key")
            result_idempotency_keys.add(values["idempotency_key"])
            task = tasks.get(task_id)
            if (
                values["protocol_version"] != PROTOCOL_VERSION
                or values["kind"] != "result"
                or values["run_id"] != run_id
                or not task
                or values["agent_id"] != effective_owner(
                    run_dir,
                    task,
                    at=parse_iso(values.get("created_at", "")),
                    operational=False,
                )
                or result_path.parent.name != values["agent_id"]
            ):
                errors.append(f"{result_path.name}: result ownership or protocol mismatch")
            elif values["task_sha256"] != sha256(task_paths[task_id]):
                errors.append(f"{result_path.name}: result task hash mismatch")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", attempt_id):
                errors.append(f"{result_path.name}: invalid attempt_id")
            if result_path.name != f"{task_id}-result-{attempt_id}.md":
                errors.append(f"{result_path.name}: filename does not match result attempt")
            if (task_id, attempt_id) not in ack_attempts:
                errors.append(f"{result_path.name}: result attempt has no matching ACK")
            if values["status"] not in {"completed", "blocked", "failed"}:
                errors.append(f"{result_path.name}: invalid result status")
            if not valid_iso8601(values["created_at"]):
                errors.append(f"{result_path.name}: created_at requires timezone")
            changed_files = add_protocol_error(
                errors,
                json_string_list,
                values["changed_files"],
                field="changed_files",
                source=result_path.name,
            ) or []
            verification_refs = add_protocol_error(
                errors,
                json_string_list,
                values["verification_refs"],
                field="verification_refs",
                source=result_path.name,
            ) or []
            if task:
                for changed_file in changed_files:
                    if not path_within(
                        changed_file,
                        task_lists[task_id]["owned_paths"],
                        project_root,
                    ):
                        errors.append(
                            f"{result_path.name}: changed file exceeds task owned_paths"
                        )
            for reference in verification_refs:
                verification_path = resolve_reference(
                    reference,
                    run_dir,
                    project_root,
                    allowed_roots,
                )
                if verification_path is None:
                    errors.append(
                        f"{result_path.name}: invalid verification reference {reference}"
                    )
                    continue
                if governance in {"standard", "strict"}:
                    evidence = evidence_by_path.get(verification_path)
                    if (
                        not evidence
                        or evidence.get("kind") != "verification"
                        or evidence.get("status") != "passed"
                    ):
                        errors.append(
                            f"{result_path.name}: governed verification ref must be passed verification evidence"
                        )
            if values["verification_status"] not in {"passed", "failed", "not_run"}:
                errors.append(f"{result_path.name}: invalid verification_status")
            if (
                governance in {"standard", "strict"}
                and values["verification_status"] == "passed"
                and not verification_refs
            ):
                errors.append(
                    f"{result_path.name}: passed governed result requires verification evidence"
                )

    result_event_names = {
        "HANDOFF_READY",
        "TASK_COMPLETED",
        "TASK_FAILED",
        "THREAD_RESULT_RECEIVED",
        "DOCUMENT_SUBAGENT_RESULT_RECEIVED",
    }
    for task_id, event_names in task_event_names.items():
        if "ACK" in event_names and task_id not in ack_tasks:
            errors.append(f"{task_id}: ACK event has no owner ACK document")
        for event_name in event_names.intersection(result_event_names):
            for result_path in event_payloads.get((task_id, event_name), []):
                if result_path not in result_values_by_path:
                    errors.append(f"{task_id}: {event_name} has no valid owner result document")

    result_values: dict[str, dict[str, str]] = {}
    for task_id in tasks:
        selected_path: Path | None = None
        state_value = task_states.get(task_id)
        if state_value == "completed":
            selection_order = ("TASK_COMPLETED",)
        elif state_value == "failed":
            selection_order = ("TASK_FAILED",)
        else:
            selection_order = ("HANDOFF_READY", "TASK_FAILED")
        for event_name in selection_order:
            payloads = event_payloads.get((task_id, event_name), [])
            if payloads:
                selected_path = payloads[-1]
                break
        if selected_path in result_values_by_path:
            result_values[task_id] = result_values_by_path[selected_path]
        completed_payloads = event_payloads.get((task_id, "TASK_COMPLETED"), [])
        handoff_payloads = event_payloads.get((task_id, "HANDOFF_READY"), [])
        if completed_payloads and handoff_payloads and completed_payloads[-1] != handoff_payloads[-1]:
            errors.append(f"{task_id}: TASK_COMPLETED must use latest HANDOFF_READY result")

    for task_id, records_for_task in task_event_records.items():
        current_attempt: str | None = None
        seen_attempts: set[str] = set()
        ack_count_at_event = 0
        for record in records_for_task:
            event_name = record["event"]
            payload_path_value = record.get("payload_path", "null")
            record_payload = None
            if payload_path_value != "null":
                record_payload = Path(payload_path_value)
                if not record_payload.is_absolute():
                    record_payload = run_dir / record_payload
                record_payload = record_payload.resolve()
            record_payload_values = (
                payload_values_by_path.get(record_payload, {})
                if record_payload is not None
                else {}
            )
            attempt_id = record_payload_values.get("attempt_id")
            if event_name == "ACK":
                if not attempt_id:
                    continue
                if attempt_id in seen_attempts:
                    errors.append(f"{task_id}: ACK attempt_id was reused: {attempt_id}")
                seen_attempts.add(attempt_id)
                current_attempt = attempt_id
                ack_count_at_event += 1
                if ack_count_at_event > max_attempts:
                    errors.append(f"{task_id}: ACK attempts exceed max_attempts")
            if event_name in {
                "LEASE_ACQUIRED",
                "LEASE_RENEWED",
                "HANDOFF_READY",
                "TASK_FAILED",
                "TASK_COMPLETED",
                "THREAD_RESULT_RECEIVED",
                "DOCUMENT_SUBAGENT_RESULT_RECEIVED",
            } and attempt_id != current_attempt:
                errors.append(f"{task_id}: {event_name} does not match current ACK attempt")
            if (
                event_name == "BLOCKED"
                and record_payload_values.get("kind") == "result"
                and attempt_id != current_attempt
            ):
                errors.append(f"{task_id}: BLOCKED result does not match current ACK attempt")
            if event_name == "RETRY_SCHEDULED" and ack_count_at_event >= max_attempts:
                errors.append(f"{task_id}: retry scheduled after max_attempts was exhausted")

    for task_id, state_value in task_states.items():
        if state_value == "running":
            expires = lease_expirations.get(task_id)
            if not expires:
                errors.append(f"{task_id}: running task lacks an immutable lease")
            elif expires <= datetime.now().astimezone():
                errors.append(f"{task_id}: running task lease has expired and needs recovery")

    ready_tasks = {task_id for task_id, names in task_event_names.items() if "TASK_READY" in names}
    for task_id in ready_tasks:
        task = tasks[task_id]
        if governance in {"standard", "strict"}:
            quality_agents: list[str] = []
            for role in ("reviewer_agent", "qa_agent"):
                role_value = task.get(role)
                if role_value in {"", "null", None} or role_value not in agents:
                    errors.append(f"{task_id}: {governance} task requires registered {role}")
                else:
                    quality_agents.append(str(role_value))
            if task["owner_agent"] in quality_agents:
                errors.append(
                    f"{task_id}: {governance} quality review must be independent from Owner; "
                    "Reviewer and QA may be the same agent"
                )
    completed_tasks = {
        task_id for task_id, state_value in task_states.items() if state_value == "completed"
    }
    if governance in {"standard", "strict"} and completed_tasks:
        if manifest.get("git_branch") in {"", "null", None}:
            errors.append("governed completion requires git_branch")
        git_status_path = resolve_reference(
            manifest.get("git_status_ref", ""),
            run_dir,
            project_root,
            allowed_roots,
        )
        git_status_evidence = (
            evidence_by_path.get(git_status_path) if git_status_path else None
        )
        if (
            not git_status_evidence
            or git_status_evidence.get("kind") != "git_status"
            or git_status_evidence.get("status")
            not in {"clean", "recorded", "not_applicable"}
        ):
            errors.append(
                "governed completion requires accepted git_status_ref evidence"
            )
    for task_id in completed_tasks:
        result = result_values.get(task_id)
        if not result:
            continue
        if governance in {"standard", "strict"}:
            events_for_task = task_event_names.get(task_id, set())
            if not {"REVIEW_APPROVED", "QA_PASSED", "TASK_COMPLETED"}.issubset(events_for_task):
                errors.append(f"{task_id}: completion requires Review, QA, and completion events")
            if result["verification_status"] != "passed":
                errors.append(f"{task_id}: completed governed task requires passed verification")
            if result["risk_summary"] in {"", "null"} or result["rollback_plan"] in {"", "null"}:
                errors.append(f"{task_id}: result requires risk and rollback details")
            if result["implementation_commit"] == "null" and result["uncommitted_reason"] == "null":
                errors.append(f"{task_id}: result needs commit or explicit uncommitted reason")
        if governance == "strict":
            commit = result["implementation_commit"]
            if commit == "null":
                errors.append(f"{task_id}: strict task requires implementation commit")
            elif not git_commit_exists(project_root, commit):
                errors.append(f"{task_id}: implementation commit does not exist")
            else:
                changed_files = add_protocol_error(
                    errors,
                    json_string_list,
                    result["changed_files"],
                    field="changed_files",
                    source=task_id,
                ) or []
                commit_problem = strict_commit_error(
                    project_root,
                    manifest.get("git_branch", ""),
                    commit,
                    changed_files,
                )
                if commit_problem:
                    errors.append(f"{task_id}: {commit_problem}")

    if governance == "strict" and ready_tasks:
        if manifest.get("change_id") in {"", "null", None}:
            errors.append("strict run requires change_id before dispatch")
        if manifest.get("git_branch") in {"", "null", None}:
            errors.append("strict run requires git_branch before dispatch")
        if any(
            task_states.get(task_id) in {"ready", "dispatched"}
            for task_id in ready_tasks
        ):
            current_branch = git_current_branch(project_root)
            if current_branch is None:
                errors.append("strict dispatch requires an attached project Git worktree")
            elif current_branch != manifest.get("git_branch"):
                errors.append("strict dispatch git_branch does not match current branch")
            if not git_worktree_clean(project_root):
                errors.append("strict dispatch requires a clean project worktree")
        strict_reference_paths: dict[str, Path] = {}
        for field in (
            "registry_ref",
            "git_status_ref",
            "environment_impact_ref",
            "rollback_ref",
            "security_review_ref",
        ):
            resolved = resolve_reference(
                manifest.get(field, ""),
                run_dir,
                project_root,
                allowed_roots,
            )
            if resolved is None:
                errors.append(f"strict run requires valid {field}")
            else:
                strict_reference_paths[field] = resolved
        evidence_requirements = {
            "git_status_ref": ("git_status", {"clean"}),
            "environment_impact_ref": (
                "environment_impact",
                {"reviewed", "approved"},
            ),
            "rollback_ref": ("rollback", {"ready", "approved"}),
            "security_review_ref": ("security", {"approved", "passed"}),
        }
        for field, (kind, statuses) in evidence_requirements.items():
            evidence = evidence_by_path.get(strict_reference_paths.get(field, Path("/nonexistent")))
            if not evidence or evidence.get("kind") != kind or evidence.get("status") not in statuses:
                errors.append(f"strict {field} must reference accepted {kind} evidence")
        for task_id in ready_tasks:
            task = tasks[task_id]
            gate_ids = task_lists[task_id]["human_gates"]
            task_records = task_event_records.get(task_id, [])
            task_ready_record = next(
                (item for item in task_records if item["event"] == "TASK_READY"),
                None,
            )
            ready_time = parse_iso(task_ready_record["created_at"]) if task_ready_record else None
            for risk in task_lists[task_id]["risk_flags"]:
                required_scope = RISK_TO_GATE.get(risk)
                if not required_scope:
                    continue
                matching = [
                    decisions.get(gate_id)
                    for gate_id in gate_ids
                    if decisions.get(gate_id)
                    and decisions[gate_id].get("scope") == required_scope
                    and decisions[gate_id].get("status") == "approved"
                    and decisions[gate_id].get("task_id") in {"null", task_id}
                ]
                if not matching:
                    errors.append(f"{task_id}: risk {risk} lacks approved {required_scope} gate")
                    continue
                if ready_time and all(
                    (parse_iso(item["approved_at"]) or ready_time) > ready_time
                    for item in matching
                ):
                    errors.append(f"{task_id}: {required_scope} gate was approved after dispatch")

    release_tasks = {
        task_id for task_id, names in task_event_names.items() if "RELEASE_READY" in names
    }
    if release_tasks or args.phase == "release":
        if versioning_mode != "tracked":
            errors.append("release requires tracked project version governance")
        if not candidate_ids:
            errors.append("release requires at least one immutable release candidate")
        for field in (
            "release_environment",
            "release_authorization_ref",
            "clean_worktree_ref",
        ):
            value = manifest.get(field, "")
            if field.endswith("_ref"):
                if resolve_reference(value, run_dir, project_root, allowed_roots) is None:
                    errors.append(f"release requires valid {field}")
            elif value in {"", "null"}:
                errors.append(f"release requires {field}")
        for task_id in release_tasks:
            release_payloads = event_payloads.get((task_id, "RELEASE_READY"), [])
            payload = release_payloads[-1] if release_payloads else None
            gate = None
            if payload:
                gate = next(
                    (item for gate_id, item in decisions.items() if decision_paths[gate_id] == payload),
                    None,
                )
            if not gate or gate.get("scope") != "release" or gate.get("status") != "approved":
                errors.append(f"{task_id}: release event lacks approved release gate")
            if task_lists[task_id]["risk_flags"] and "local_only" in task_lists[task_id]["risk_flags"]:
                errors.append(f"{task_id}: local_only task cannot enter release")
            result = result_values.get(task_id)
            commit = result.get("implementation_commit", "null") if result else "null"
            if commit == "null" or not git_commit_exists(project_root, commit):
                errors.append(f"{task_id}: release requires a real implementation commit")
            elif governance == "strict" and result:
                changed_files = add_protocol_error(
                    errors,
                    json_string_list,
                    result["changed_files"],
                    field="changed_files",
                    source=task_id,
                ) or []
                commit_problem = strict_commit_error(
                    project_root,
                    manifest.get("git_branch", ""),
                    commit,
                    changed_files,
                )
                if commit_problem:
                    errors.append(f"{task_id}: {commit_problem}")
        if candidate_ids:
            latest_candidate_path = (
                run_dir / "versions" / "candidates" / f"{candidate_ids[-1]}.yaml"
            )
            latest_candidate = (
                scalar_map(
                    latest_candidate_path.read_text(encoding="utf-8"),
                    source=str(latest_candidate_path),
                )
                if latest_candidate_path.is_file()
                else {}
            )
            candidate_commit = latest_candidate.get("implementation_commit", "null")
            release_commits = {
                result_values[task_id].get("implementation_commit", "null")
                for task_id in release_tasks
                if task_id in result_values
            }
            if (
                candidate_commit == "null"
                or not git_commit_exists(project_root, candidate_commit)
            ):
                errors.append("latest release candidate requires a real implementation commit")
            elif candidate_commit not in release_commits:
                errors.append(
                    "latest release candidate commit must match a release task result"
                )
        if version_source is None:
            errors.append("release cannot verify the project version source")
        else:
            try:
                version_source_text = version_source.read_text(encoding="utf-8")
            except (OSError, UnicodeError):
                version_source_text = ""
            target_version = manifest.get("target_version", "")
            if not target_version or target_version not in version_source_text:
                errors.append(
                    "release version source does not contain the reserved target version"
                )
        clean_ref = resolve_reference(
            manifest.get("clean_worktree_ref", ""),
            run_dir,
            project_root,
            allowed_roots,
        )
        clean_evidence = evidence_by_path.get(clean_ref) if clean_ref else None
        if (
            not clean_evidence
            or clean_evidence.get("kind") != "git_status"
            or clean_evidence.get("status") != "clean"
        ):
            errors.append("release clean_worktree_ref must be clean git_status evidence")
        authorization_ref = resolve_reference(
            manifest.get("release_authorization_ref", ""),
            run_dir,
            project_root,
            allowed_roots,
        )
        authorization = next(
            (
                item
                for gate_id, item in decisions.items()
                if decision_paths.get(gate_id) == authorization_ref
            ),
            None,
        )
        if (
            not authorization
            or authorization.get("scope") != "release"
            or authorization.get("status") != "approved"
        ):
            errors.append("release_authorization_ref must be an approved release gate")
        if governance == "strict" and not git_worktree_clean(project_root):
            errors.append("strict release requires a clean project worktree")

    active_locks: list[tuple[Path, dict[str, str]]] = []
    lock_ids: set[str] = set()
    for path in sorted((run_dir / "locks").glob("*.yaml")):
        values = add_protocol_error(
            errors,
            scalar_map,
            path.read_text(encoding="utf-8"),
            source=str(path),
        )
        if values is None:
            continue
        required = (
            "protocol_version",
            "kind",
            "run_id",
            "lock_id",
            "resource",
            "owner_task",
            "owner_agent",
            "acquired_at",
            "lease_expires_at",
        )
        missing = [key for key in required if key not in values]
        if missing:
            errors.append(f"{path.name}: missing lock keys {', '.join(missing)}")
            continue
        if values["protocol_version"] != PROTOCOL_VERSION or values["kind"] != "lock" or values["run_id"] != run_id:
            errors.append(f"{path.name}: lock protocol or run mismatch")
        if values["lock_id"] in lock_ids:
            errors.append(f"{path.name}: duplicate lock_id")
        lock_ids.add(values["lock_id"])
        if path.name != f"{values['lock_id']}.yaml":
            errors.append(f"{path.name}: filename must match lock_id")
        task_id = values["owner_task"]
        task = tasks.get(task_id)
        if not task or values["owner_agent"] != effective_owner(run_dir, task):
            errors.append(f"{path.name}: lock owner must be task owner")
        if values.get("queue_key", "null") not in {"", "null"} and values.get("step_id", "null") in {"", "null"}:
            errors.append(f"{path.name}: queue-backed lock requires step_id")
        elif task_states.get(task_id) in TERMINAL_TASK_STATUSES:
            errors.append(f"{path.name}: terminal task cannot retain active lock")
        resource = values["resource"]
        if task and not resource.startswith("logical:") and not path_within(
            resource,
            task_lists[task_id]["owned_paths"],
            project_root,
        ):
            errors.append(f"{path.name}: lock resource exceeds task owned_paths")
        acquired = parse_iso(values["acquired_at"])
        expires = parse_iso(values["lease_expires_at"])
        if not acquired or not expires or expires <= acquired:
            errors.append(f"{path.name}: invalid lock lease interval")
        elif expires <= datetime.now().astimezone():
            errors.append(f"{path.name}: active lock lease has expired and needs recovery")
        active_locks.append((path, values))
    for index, (left_path, left) in enumerate(active_locks):
        for right_path, right in active_locks[index + 1 :]:
            overlap = left["resource"] == right["resource"]
            if not left["resource"].startswith("logical:") and not right["resource"].startswith("logical:"):
                overlap = paths_overlap(left["resource"], right["resource"], project_root)
            if overlap:
                errors.append(f"{left_path.name} conflicts with {right_path.name}")

    queue_dir = run_dir / "locks" / "queue"
    for grant_path in sorted((queue_dir / "grants").glob("*.yaml")) if (queue_dir / "grants").is_dir() else []:
        values = add_protocol_error(
            errors,
            scalar_map,
            grant_path.read_text(encoding="utf-8"),
            source=str(grant_path),
        ) or {}
        required = ("protocol_version", "kind", "run_id", "request_id", "task_id", "agent_id", "step_id", "queue_key", "lock_id", "granted_at", "status")
        missing = [key for key in required if key not in values]
        if missing:
            errors.append(f"{grant_path.name}: missing resource grant keys {', '.join(missing)}")
            continue
        request_path = queue_dir / f"{values['request_id']}.yaml"
        lock_path = run_dir / "locks" / f"{values['lock_id']}.yaml"
        if (
            grant_path.name != f"{values['request_id']}.yaml"
            or values["protocol_version"] != PROTOCOL_VERSION
            or values["kind"] != "resource_grant"
            or values["run_id"] != run_id
            or values["status"] != "granted"
            or not valid_iso8601(values["granted_at"])
            or not request_path.is_file()
            or not lock_path.is_file()
        ):
            errors.append(f"{grant_path.name}: resource grant reference is invalid")
            continue
        request_values = add_protocol_error(errors, scalar_map, request_path.read_text(encoding="utf-8"), source=str(request_path)) or {}
        lock_values = add_protocol_error(errors, scalar_map, lock_path.read_text(encoding="utf-8"), source=str(lock_path)) or {}
        if (
            request_values.get("request_id") != values["request_id"]
            or request_values.get("task_id") != values["task_id"]
            or request_values.get("agent_id") != values["agent_id"]
            or request_values.get("step_id") != values["step_id"]
            or request_values.get("queue_key") != values["queue_key"]
            or lock_values.get("owner_task") != values["task_id"]
            or lock_values.get("owner_agent") != values["agent_id"]
            or lock_values.get("step_id") != values["step_id"]
            or lock_values.get("queue_key") != values["queue_key"]
            or lock_values.get("lock_id") != values["lock_id"]
        ):
            errors.append(f"{grant_path.name}: resource grant does not bind request and lock")

    dead_letter_tasks: set[str] = set()
    for path in sorted((run_dir / "dead-letter").glob("*.yaml")):
        values = add_protocol_error(
            errors,
            scalar_map,
            path.read_text(encoding="utf-8"),
            source=str(path),
        )
        if values is None:
            continue
        required = (
            "protocol_version",
            "kind",
            "run_id",
            "task_id",
            "event_id",
            "attempts",
            "last_attempt_at",
            "reason",
            "side_effect_state",
            "recovery_owner",
            "requires_human",
        )
        missing = [key for key in required if key not in values]
        if missing:
            errors.append(f"{path.name}: missing dead-letter keys {', '.join(missing)}")
            continue
        task_id = values["task_id"]
        dead_letter_tasks.add(task_id)
        if path.name != f"{task_id}.yaml":
            errors.append(f"{path.name}: filename must match dead-letter task_id")
        if (
            values["protocol_version"] != PROTOCOL_VERSION
            or values["kind"] != "dead_letter"
            or values["run_id"] != run_id
            or task_id not in tasks
        ):
            errors.append(f"{path.name}: dead-letter protocol or task mismatch")
        if values["side_effect_state"] not in {"none", "unknown", "confirmed"}:
            errors.append(f"{path.name}: invalid side_effect_state")
        try:
            attempts = int(values["attempts"])
            if attempts < max_attempts:
                errors.append(f"{path.name}: dead-letter attempts have not reached max_attempts")
        except ValueError:
            errors.append(f"{path.name}: attempts must be an integer")
        failed_event = events_by_id.get(values["event_id"])
        if (
            not failed_event
            or failed_event.get("task_id") != task_id
            or failed_event.get("event")
            not in {
                "TASK_FAILED",
                "THREAD_FAILED",
                "SUBAGENT_FAILED",
                "DOCUMENT_SUBAGENT_FAILED",
            }
        ):
            errors.append(
                f"{path.name}: failed event_id must identify this task's failure"
            )
        actual_attempts = sum(
            1
            for record in task_event_records.get(task_id, [])
            if record.get("event") == "ACK"
        )
        if values["attempts"].isdigit() and int(values["attempts"]) != actual_attempts:
            errors.append(
                f"{path.name}: attempts must equal event-derived ACK attempts"
            )
        if not valid_iso8601(values["last_attempt_at"]):
            errors.append(f"{path.name}: last_attempt_at requires timezone")
        if values["recovery_owner"] not in agents:
            errors.append(f"{path.name}: recovery owner is not registered")
        if values["requires_human"] not in {"true", "false"}:
            errors.append(f"{path.name}: requires_human must be true or false")
        if values["side_effect_state"] == "unknown" and values["requires_human"] != "true":
            errors.append(f"{path.name}: unknown side effect requires human handling")
        if path.resolve() not in event_payloads.get((task_id, "DEAD_LETTERED"), []):
            errors.append(f"{path.name}: DEAD_LETTERED event must hash this document")
    for task_id, state_value in task_states.items():
        if state_value == "dead_letter" and task_id not in dead_letter_tasks:
            errors.append(f"{task_id}: dead_letter state lacks document")

    # Claims are immutable ownership facts.  Validate them before native
    # bindings so event actor checks and adapter evidence cannot rely on an
    # unregistered, out-of-scope, or tampered claimant.
    claim_dirs = {
        "task_claim": run_dir / "claims" / "tasks",
        "thread_claim": run_dir / "claims" / "threads",
    }
    claim_values: dict[str, dict[str, str]] = {}
    claim_paths: dict[str, Path] = {}
    for kind, claim_dir in claim_dirs.items():
        for path in sorted(claim_dir.glob("*.yaml")) if claim_dir.is_dir() else []:
            values = add_protocol_error(
                errors,
                scalar_map,
                path.read_text(encoding="utf-8"),
                source=str(path),
            )
            if values is None:
                continue
            claim_id = values.get("claim_id", "")
            required = (
                "protocol_version", "kind", "claim_id", "run_id", "task_id",
                "task_sha256", "claimer_agent", "lease_acquired_at",
                "lease_expires_at", "status",
            )
            if kind == "task_claim":
                required += ("eligible_agents", "parent_causation_id")
            else:
                required += ("task_claim_id", "thread_id", "platform", "session_id", "workspace", "parent_causation_id")
            missing = [key for key in required if key not in values]
            if missing:
                errors.append(f"{path.name}: missing {kind} keys {', '.join(missing)}")
                continue
            if path.name != f"{claim_id}.yaml":
                errors.append(f"{path.name}: claim filename must match claim_id")
            if claim_id in claim_values:
                errors.append(f"{path.name}: duplicate claim_id")
            claim_values[claim_id] = values
            claim_paths[claim_id] = path
            task_id = values["task_id"]
            task = tasks.get(task_id)
            if (
                values["protocol_version"] != PROTOCOL_VERSION
                or values["kind"] != kind
                or values["run_id"] != run_id
                or task is None
            ):
                errors.append(f"{path.name}: claim protocol, run, kind, or task mismatch")
                continue
            if values["task_sha256"] != sha256(task_paths[task_id]):
                errors.append(f"{path.name}: claim task hash mismatch")
            acquired = parse_iso(values["lease_acquired_at"])
            expires = parse_iso(values["lease_expires_at"])
            if not acquired or not expires or expires <= acquired:
                errors.append(f"{path.name}: invalid claim lease interval")
            if values["claimer_agent"] not in agents:
                errors.append(f"{path.name}: claimant is not registered")
            elif kind == "task_claim" and "task_claim" not in set(agents[values["claimer_agent"]].get("capabilities", [])):
                errors.append(f"{path.name}: claimant lacks task_claim capability")
            elif kind == "thread_claim" and "thread_claim" not in set(agents[values["claimer_agent"]].get("capabilities", [])):
                errors.append(f"{path.name}: claimant lacks thread_claim capability")
            if kind == "task_claim":
                try:
                    eligible = json_string_list(values["eligible_agents"], field="eligible_agents", source=str(path))
                except ProtocolError:
                    eligible = []
                if task.get("assignment_mode", "fixed") != "claimable" or task.get("owner_agent") != "pool":
                    errors.append(f"{path.name}: task claim requires claimable pool task")
                if values["claimer_agent"] not in eligible:
                    errors.append(f"{path.name}: claimant is not in eligible_agents")
                if values["parent_causation_id"] != task.get("parent_task_id", "null"):
                    errors.append(f"{path.name}: parent causation does not match task")
            else:
                if task.get("assignment_mode", "fixed") == "claimable":
                    acquired_owner = effective_owner(run_dir, task, at=acquired, operational=False) if acquired else "pool"
                else:
                    acquired_owner = task.get("owner_agent")
                if values["claimer_agent"] != acquired_owner:
                    errors.append(f"{path.name}: thread claimant is not the task owner at acquisition")
                if values["workspace"] != str(project_root):
                    errors.append(f"{path.name}: thread workspace does not match project root")
                if values["platform"] not in {"codex", "hermes", "document"}:
                    errors.append(f"{path.name}: invalid thread platform")
                if values["platform"] in {"codex", "hermes"} and values.get("session_id") in {"", "null"}:
                    errors.append(f"{path.name}: native thread claim requires session_id")
                if values["task_claim_id"] not in {"null", ""}:
                    task_claim = claim_values.get(values["task_claim_id"])
                    if not task_claim or task_claim.get("task_id") != task_id or task_claim.get("claimer_agent") != values["claimer_agent"]:
                        errors.append(f"{path.name}: thread task_claim_id does not bind to claimant")

    for kind, claim_dir in claim_dirs.items():
        release_dir = claim_dir / "releases"
        for path in sorted(release_dir.glob("*.yaml")) if release_dir.is_dir() else []:
            values = add_protocol_error(
                errors,
                scalar_map,
                path.read_text(encoding="utf-8"),
                source=str(path),
            )
            if values is None:
                continue
            claim_id = values.get("claim_id", "")
            required = ("protocol_version", "kind", "run_id", "claim_kind", "claim_id", "claim_ref", "claim_sha256", "released_by", "released_at", "reason", "status")
            missing = [key for key in required if key not in values]
            if missing:
                errors.append(f"{path.name}: missing claim release keys {', '.join(missing)}")
                continue
            if path.name != f"{claim_id}.yaml":
                errors.append(f"{path.name}: release filename must match claim_id")
            original = claim_values.get(claim_id)
            if (
                values["protocol_version"] != PROTOCOL_VERSION
                or values["kind"] != "claim_release"
                or values["run_id"] != run_id
                or values["claim_kind"] != kind
                or not original
                or values["claim_sha256"] != sha256(claim_paths[claim_id])
                or values["released_by"] != original.get("claimer_agent")
                or values["status"] != "released"
                or not valid_iso8601(values["released_at"])
                or not values["reason"].strip()
            ):
                errors.append(f"{path.name}: claim release does not match immutable claim")

    # `wake_agent.py` operations are the common delivery fact for both Native
    # and Document adapters.  They are JSON rather than protocol YAML, but
    # still require the same run/task/hash/workspace binding.
    wake_operation_ids: set[str] = set()
    operations_dir = run_dir / "operations"
    for path in sorted(operations_dir.glob("*.json")) if operations_dir.is_dir() else []:
        try:
            values = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors.append(f"{path.name}: wake operation must be valid JSON")
            continue
        if not isinstance(values, dict):
            errors.append(f"{path.name}: wake operation must be an object")
            continue
        required = ("protocol_version", "kind", "operation_id", "run_id", "task_id", "agent_id", "adapter", "workspace", "task_path", "task_sha256", "owned_paths", "forbidden_paths", "created_at")
        missing = [key for key in required if key not in values]
        if missing:
            errors.append(f"{path.name}: wake operation missing {', '.join(missing)}")
            continue
        operation_id = str(values["operation_id"])
        if path.name != f"{operation_id}.json":
            errors.append(f"{path.name}: wake operation filename must match operation_id")
        if operation_id in wake_operation_ids:
            errors.append(f"{path.name}: duplicate wake operation_id")
        wake_operation_ids.add(operation_id)
        task_id = str(values["task_id"])
        task = tasks.get(task_id)
        operation_claim = claim_values.get(str(values.get("claim_id"))) if values.get("claim_id") not in {None, "null", ""} else None
        owner = operation_claim.get("claimer_agent") if operation_claim else (effective_owner(run_dir, task, operational=False) if task else "")
        task_path_value = Path(str(values["task_path"])).expanduser()
        if not task_path_value.is_absolute():
            task_path_value = run_dir / task_path_value
        if (
            str(values.get("protocol_version")) != PROTOCOL_VERSION
            or values.get("kind") != "wake_operation"
            or values.get("run_id") != run_id
            or task is None
            or values.get("agent_id") != owner
            or values.get("workspace") != str(project_root)
            or task_path_value.resolve() != task_paths.get(task_id)
            or values.get("task_sha256") != (sha256(task_paths[task_id]) if task_id in task_paths else "")
        ):
            errors.append(f"{path.name}: wake operation run/task/owner/hash/workspace mismatch")
        if values.get("adapter") not in {"document", "codex", "hermes"}:
            errors.append(f"{path.name}: invalid wake adapter")
        if not valid_iso8601(str(values.get("created_at", ""))):
            errors.append(f"{path.name}: wake operation created_at requires timezone")
        if not isinstance(values.get("owned_paths"), list) or not isinstance(values.get("forbidden_paths"), list):
            errors.append(f"{path.name}: wake operation path fields must be lists")
        if "claim_id" in values and task and task.get("assignment_mode", "fixed") == "claimable":
            claim = claim_values.get(str(values.get("claim_id")))
            if not claim or claim.get("task_id") != task_id or claim.get("claimer_agent") != values.get("agent_id"):
                errors.append(f"{path.name}: wake operation claim binding mismatch")

    for package in sorted((run_dir / "inbox").glob("*/*.json")):
        try:
            values = json.loads(package.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            errors.append(f"{package.name}: invocation package must be valid JSON")
            continue
        if not isinstance(values, dict) or values.get("kind") != "document_invocation":
            continue
        required = ("protocol_version", "kind", "operation_id", "run_id", "task_id", "agent_id", "workspace", "task_path", "task_sha256", "owned_paths", "forbidden_paths", "instruction")
        missing = [key for key in required if key not in values]
        if missing:
            errors.append(f"{package.name}: invocation package missing {', '.join(missing)}")
            continue
        task_id = str(values["task_id"])
        task = tasks.get(task_id)
        package_claim = claim_values.get(str(values.get("claim_id"))) if values.get("claim_id") not in {None, "null", ""} else None
        owner = package_claim.get("claimer_agent") if package_claim else (effective_owner(run_dir, task, operational=False) if task else "")
        task_path_value = Path(str(values["task_path"])).expanduser()
        if not task_path_value.is_absolute():
            task_path_value = run_dir / task_path_value
        if (
            str(values.get("protocol_version")) != PROTOCOL_VERSION
            or values.get("run_id") != run_id
            or task is None
            or values.get("agent_id") != owner
            or values.get("workspace") != str(project_root)
            or task_path_value.resolve() != task_paths.get(task_id)
            or values.get("task_sha256") != (sha256(task_paths[task_id]) if task_id in task_paths else "")
        ):
            errors.append(f"{package.name}: invocation package run/task/owner/hash/workspace mismatch")
        if package.parent.name != str(values.get("agent_id")):
            errors.append(f"{package.name}: invocation package must be in the owner inbox")
        if "claim_id" in values and task and task.get("assignment_mode", "fixed") == "claimable":
            claim = claim_values.get(str(values.get("claim_id")))
            if not claim or claim.get("task_id") != task_id or claim.get("claimer_agent") != values.get("agent_id"):
                errors.append(f"{package.name}: invocation package claim binding mismatch")

    native_binding_tasks: set[str] = set()
    native_ids: set[str] = set()
    binding_count = 0
    native_dir = run_dir / "native"
    for path in sorted((native_dir / "threads").glob("*.yaml")):
        values = add_protocol_error(
            errors,
            scalar_map,
            path.read_text(encoding="utf-8"),
            source=str(path),
        )
        if values is None:
            continue
        kind = values.get("kind")
        runtime = values.get("runtime")
        if kind not in {"codex_thread_binding", "codex_subagent_binding"}:
            errors.append(f"{path.name}: unknown native binding kind")
            continue
        required = (
            "protocol_version",
            "kind",
            "run_id",
            "task_id",
            "agent_id",
            "runtime",
            "task_path",
            "task_sha256",
            "status",
            "created_at",
            "updated_at",
        )
        missing = [key for key in required if key not in values]
        if missing:
            errors.append(f"{path.name}: missing binding keys {', '.join(missing)}")
            continue
        binding_count += 1
        task_id = values["task_id"]
        native_binding_tasks.add(task_id)
        task = tasks.get(task_id)
        if (
            values["protocol_version"] != PROTOCOL_VERSION
            or values["run_id"] != run_id
            or runtime not in {"codex_thread", "codex_subagent"}
            or not task
            or values["agent_id"] != task.get("owner_agent")
            or agents.get(values["agent_id"], {}).get("runtime") != runtime
        ):
            errors.append(f"{path.name}: native binding ownership or protocol mismatch")
        task_path_value = Path(values["task_path"])
        if not task_path_value.is_absolute():
            task_path_value = run_dir / task_path_value
        if not task_path_value.is_file() or (
            task and (
                task_path_value.resolve() != task_paths[task_id]
                or values["task_sha256"] != sha256(task_paths[task_id])
            )
        ):
            errors.append(f"{path.name}: native binding task reference mismatch")
        if values["status"] not in NATIVE_BINDING_STATUSES:
            errors.append(f"{path.name}: invalid native binding status")
        if runtime == "codex_thread":
            environment = values.get("environment")
            if environment not in {"local", "worktree", "same-directory", "projectless"}:
                errors.append(f"{path.name}: invalid thread environment")
            if environment != "projectless" and values.get("project_id") in {None, "null"}:
                errors.append(f"{path.name}: project thread requires project_id")
            if values["status"] == "provisioning" and values.get("pending_id") in {
                None,
                "null",
            }:
                errors.append(f"{path.name}: provisioning requires pending_id")
            if values["status"] in {"ready", "running", "completed", "archived"} and values.get(
                "thread_id"
            ) in {None, "null"}:
                errors.append(f"{path.name}: status requires real thread_id")
            if (
                environment == "worktree"
                and values["status"] in {"ready", "running", "completed", "archived"}
                and values.get("worktree_path") in {None, "null"}
            ):
                errors.append(f"{path.name}: ready worktree requires worktree_path")
            required_status_event = {
                "requested": "THREAD_CREATE_REQUESTED",
                "provisioning": "THREAD_PROVISIONING",
                "ready": "THREAD_READY",
                "running": "THREAD_RUNNING",
                "completed": "THREAD_RESULT_RECEIVED",
                "failed": "THREAD_FAILED",
                "archived": "THREAD_ARCHIVED",
            }.get(values["status"])
        else:
            if values["status"] in {"ready", "running", "completed", "closed"} and values.get(
                "native_agent_id"
            ) in {None, "null"}:
                errors.append(f"{path.name}: subagent status requires native_agent_id")
            required_status_event = {
                "ready": "SUBAGENT_SPAWNED",
                "running": "SUBAGENT_SPAWNED",
                "completed": "SUBAGENT_RESULT_RECEIVED",
                "failed": "SUBAGENT_FAILED",
                "closed": "SUBAGENT_CLOSED",
            }.get(values["status"])
        if required_status_event and required_status_event not in task_event_names.get(
            task_id, set()
        ):
            errors.append(f"{path.name}: status lacks {required_status_event}")
        opaque_id = values.get("thread_id") if runtime == "codex_thread" else values.get("native_agent_id")
        if opaque_id not in {None, "null"}:
            if opaque_id in native_ids:
                errors.append(f"{path.name}: duplicate native identifier")
            native_ids.add(opaque_id)
        for field in ("created_at", "updated_at"):
            if not valid_iso8601(values[field]):
                errors.append(f"{path.name}: {field} requires timezone")
    for task_id in ready_tasks:
        runtime = agents.get(tasks[task_id]["owner_agent"], {}).get("runtime")
        if runtime in {"codex_thread", "codex_subagent"} and task_id not in native_binding_tasks:
            errors.append(f"{task_id}: native owner requires binding before dispatch")
    for task_id, names in task_event_names.items():
        if names.intersection(NATIVE_EVENTS) and task_id not in native_binding_tasks:
            errors.append(f"{task_id}: native event requires a native binding")

    operation_types_by_task: dict[str, set[str]] = {}
    operation_keys: set[str] = set()
    operation_count = 0
    for path in sorted((native_dir / "operations").glob("*.yaml")):
        values = add_protocol_error(
            errors,
            scalar_map,
            path.read_text(encoding="utf-8"),
            source=str(path),
        )
        if values is None:
            continue
        required = (
            "protocol_version",
            "kind",
            "run_id",
            "task_id",
            "agent_id",
            "operation_key",
            "operation_type",
            "tool_name",
            "status",
            "requested_at",
            "result_ref",
            "result_sha256",
        )
        missing = [key for key in required if key not in values]
        if missing:
            errors.append(f"{path.name}: missing operation keys {', '.join(missing)}")
            continue
        operation_count += 1
        task_id = values["task_id"]
        operation_type = values["operation_type"]
        operation_types_by_task.setdefault(task_id, set()).add(operation_type)
        if (
            values["protocol_version"] != PROTOCOL_VERSION
            or values["kind"] != "codex_native_operation"
            or values["run_id"] != run_id
            or task_id not in tasks
            or values["agent_id"] != tasks[task_id]["owner_agent"]
        ):
            errors.append(f"{path.name}: native operation ownership or protocol mismatch")
        if operation_type not in NATIVE_OPERATION_TYPES or values["status"] not in NATIVE_OPERATION_STATUSES:
            errors.append(f"{path.name}: invalid operation type or status")
        if values["operation_key"] in operation_keys:
            errors.append(f"{path.name}: duplicate operation_key")
        operation_keys.add(values["operation_key"])
        if not valid_iso8601(values["requested_at"]):
            errors.append(f"{path.name}: requested_at requires timezone")
        if values["status"] == "succeeded" and operation_type not in {"wait", "read"}:
            if values.get("completed_at") in {None, "null"} or not valid_iso8601(
                values.get("completed_at", "")
            ):
                errors.append(f"{path.name}: succeeded operation requires completed_at")
            result_ref = resolve_reference(
                values["result_ref"],
                run_dir,
                project_root,
                allowed_roots,
            )
            if result_ref is None or values["result_sha256"] != sha256(result_ref):
                errors.append(f"{path.name}: succeeded side-effect operation needs hashed result")
            if operation_type == "handoff" and (
                values.get("native_operation_id") in {None, "null"}
                or values.get("revision") in {None, "null"}
            ):
                errors.append(f"{path.name}: succeeded handoff requires operation id and revision")
    for task_id, names in task_event_names.items():
        for event_name in names.intersection(NATIVE_EVENTS):
            required_types = NATIVE_EVENT_OPERATION_TYPES[event_name]
            if not operation_types_by_task.get(task_id, set()).intersection(required_types):
                errors.append(f"{task_id}: {event_name} lacks matching native operation")
    for task_id in operation_types_by_task:
        if task_id not in native_binding_tasks:
            errors.append(f"{task_id}: native operation requires a native binding")

    delegation_tasks: set[str] = set()
    delegation_count = 0
    for path in sorted((run_dir / "delegations").glob("*.yaml")):
        values = add_protocol_error(
            errors,
            scalar_map,
            path.read_text(encoding="utf-8"),
            source=str(path),
        )
        if values is None:
            continue
        required = (
            "protocol_version",
            "kind",
            "run_id",
            "task_id",
            "agent_id",
            "parent_agent_id",
            "delegated_by",
            "runtime",
            "delegation_depth",
            "task_path",
            "task_sha256",
            "status",
            "max_duration_seconds",
            "max_attempts",
            "created_at",
            "updated_at",
            "result_ref",
            "closed_at",
        )
        missing = [key for key in required if key not in values]
        if missing:
            errors.append(f"{path.name}: missing delegation keys {', '.join(missing)}")
            continue
        delegation_count += 1
        task_id = values["task_id"]
        if task_id in delegation_tasks:
            errors.append(f"{task_id}: duplicate delegation binding")
        delegation_tasks.add(task_id)
        agent = agents.get(values["agent_id"], {})
        task = tasks.get(task_id)
        if (
            values["protocol_version"] != PROTOCOL_VERSION
            or values["kind"] != "document_subagent_binding"
            or values["runtime"] != "document_subagent"
            or values["run_id"] != run_id
            or not task
            or task["owner_agent"] != values["agent_id"]
            or agent.get("runtime") != "document_subagent"
            or values["parent_agent_id"] != agent.get("parent_agent_id")
            or values["delegated_by"] != values["parent_agent_id"]
        ):
            errors.append(f"{path.name}: delegation ownership or protocol mismatch")
        if task and task.get("reviewer_agent") != values.get("parent_agent_id"):
            errors.append(f"{path.name}: parent must review delegated task")
        if values["status"] not in DOCUMENT_SUBAGENT_BINDING_STATUSES:
            errors.append(f"{path.name}: invalid delegation status")
        task_path_value = Path(values["task_path"])
        if not task_path_value.is_absolute():
            task_path_value = run_dir / task_path_value
        if not task_path_value.is_file() or (
            task and (
                task_path_value.resolve() != task_paths[task_id]
                or values["task_sha256"] != sha256(task_paths[task_id])
            )
        ):
            errors.append(f"{path.name}: delegation task reference mismatch")
        try:
            depth = int(values["delegation_depth"])
            duration = int(values["max_duration_seconds"])
            attempts = int(values["max_attempts"])
            if depth < 1 or depth > max_depth or duration < 1 or attempts < 1:
                raise ValueError
        except ValueError:
            errors.append(f"{path.name}: invalid delegation numeric policy")
        for field in ("created_at", "updated_at"):
            if not valid_iso8601(values[field]):
                errors.append(f"{path.name}: {field} requires timezone")
        required_event = {
            "ready": "DOCUMENT_SUBAGENT_DELEGATED",
            "running": "LEASE_ACQUIRED",
            "result_received": "DOCUMENT_SUBAGENT_RESULT_RECEIVED",
            "failed": "DOCUMENT_SUBAGENT_FAILED",
            "closed": "DOCUMENT_SUBAGENT_CLOSED",
        }.get(values["status"])
        if required_event and required_event not in task_event_names.get(task_id, set()):
            errors.append(f"{path.name}: status lacks {required_event}")
        if values["status"] in {"result_received", "closed"}:
            result_path = Path(values["result_ref"])
            if not result_path.is_absolute():
                result_path = run_dir / result_path
            result_path = result_path.resolve()
            result_values_for_binding = result_values_by_path.get(result_path)
            if (
                not result_path.is_file()
                or not result_values_for_binding
                or result_values_for_binding.get("task_id") != task_id
                or result_values_for_binding.get("agent_id") != values["agent_id"]
                or result_path
                not in event_payloads.get(
                    (task_id, "DOCUMENT_SUBAGENT_RESULT_RECEIVED"),
                    [],
                )
            ):
                errors.append(
                    f"{path.name}: result_ref must be the event-bound child owner result attempt"
                )
        if values["status"] == "closed" and not valid_iso8601(values["closed_at"]):
            errors.append(f"{path.name}: closed binding requires closed_at")
    for task_id in ready_tasks:
        runtime = agents.get(tasks[task_id]["owner_agent"], {}).get("runtime")
        if runtime == "document_subagent" and task_id not in delegation_tasks:
            errors.append(f"{task_id}: document_subagent requires delegation binding")
    for task_id, names in task_event_names.items():
        if names.intersection(DOCUMENT_SUBAGENT_EVENTS) and task_id not in delegation_tasks:
            errors.append(f"{task_id}: document subagent event lacks binding")

    files_to_scan = [
        path
        for root in (bus_root / "protocol.yaml", bus_root / "project.yaml", run_dir)
        for path in ([root] if root.is_file() else root.rglob("*"))
        if path.is_file() and path.name not in {".sequence.lock", ".init.lock"}
    ]
    for path in files_to_scan:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if (
            PRIVATE_KEY_MARKER.search(content)
            or BEARER_VALUE.search(content)
            or URL_CREDENTIAL.search(content)
            or TOKEN_SHAPES.search(content)
        ):
            errors.append(f"{path}: possible inline credential material")
        for match in SECRET_ASSIGNMENT.finditer(content):
            if not SAFE_SECRET.fullmatch(match.group(2)):
                errors.append(f"{path}: possible inline secret; store a reference only")

    phase = args.phase
    if phase == "auto":
        if release_tasks or manifest.get("status") == "release_ready":
            phase = "release"
        elif completed_tasks or manifest.get("status") in {"completed", "archived"}:
            phase = "completion"
        elif ready_tasks:
            phase = "dispatch"
        else:
            phase = "structure"
    if phase in {"completion", "release"} and not tasks:
        errors.append(f"{phase} validation requires at least one task")
    if phase == "dispatch" and not ready_tasks:
        errors.append("dispatch validation requires at least one TASK_READY event")
    if phase == "completion" and not task_states:
        errors.append("completion validation requires event-derived task states")
    if phase == "completion" and any(
        value not in TERMINAL_TASK_STATUSES for value in task_states.values()
    ):
        errors.append("completion validation requires every task to be terminal")
    if phase == "release" and not release_tasks:
        errors.append("release validation requires at least one RELEASE_READY event")
    if phase == "release" and any(
        value not in {"completed", "release_ready", "cancelled", "superseded"}
        for value in task_states.values()
    ):
        errors.append("release validation has unresolved or failed tasks")
    if not tasks:
        warnings.append("run is initialized but contains no task graph")
    if not records:
        warnings.append("run has no events and has not been dispatched")

    for warning in warnings:
        print(f"WARN: {warning}")
    for error in errors:
        print(f"ERROR: {error}")
    print(
        f"Validated {run_dir} [{phase}]: {len(tasks)} tasks, {len(records)} events, "
        f"{len(ack_tasks)} ACKs, {len(result_values)} results, {binding_count} native bindings, "
        f"{operation_count} native operations, {delegation_count} document delegations, "
        f"{len(errors)} errors, {len(warnings)} warnings"
    )
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
