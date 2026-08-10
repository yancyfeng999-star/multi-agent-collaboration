#!/usr/bin/env python3
"""Initialize the durable document bus for a multi-agent run."""

from __future__ import annotations

import argparse
from project_memory_lib import exclusive_lock
import json
import os
import re
import secrets
import subprocess
from datetime import datetime
from pathlib import Path

from governance_paths import resolve_governance_project, write_project_binding
from protocol_lib import (
    PROTOCOL_VERSION,
    ProtocolError,
    render_state,
    scalar_map,
    sha256,
)


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def slug(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-").lower()
    return normalized or "project"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument(
        "--coordination-mode",
        choices=("direct", "coordinated"),
        default="direct",
        help="Direct uses no persistent Run; coordinated stores governance outside the project",
    )
    parser.add_argument("--governance-root")
    parser.add_argument("--project-id")
    parser.add_argument("--project-name")
    parser.add_argument("--governance", choices=("light", "standard", "strict"), required=True)
    parser.add_argument(
        "--execution-profile",
        choices=("fast", "normal"),
        default="normal",
        help="Latency preference; fast never lowers governance gates",
    )
    parser.add_argument(
        "--dispatch-policy",
        choices=("auto", "central", "hybrid", "self_service"),
        default="auto",
        help="Who may publish scoped tasks; auto selects a safe mode default",
    )
    parser.add_argument(
        "--transport",
        choices=("codex_native", "document_bus", "hybrid"),
        required=True,
    )
    parser.add_argument("--objective", required=True)
    parser.add_argument("--max-parallel", type=int, default=2)
    parser.add_argument("--max-document-delegation-depth", type=int, default=1)
    parser.add_argument("--ack-timeout-seconds", type=int, default=300)
    parser.add_argument("--lease-seconds", type=int, default=1800)
    parser.add_argument("--max-attempts", type=int, default=3)
    parser.add_argument("--run-id")
    parser.add_argument(
        "--versioning-mode",
        choices=("tracked", "not_applicable"),
        required=True,
        help="Explicit project delivery-version assessment for this run",
    )
    parser.add_argument(
        "--version-scheme",
        choices=("semver", "calendar", "registry_managed", "custom"),
        default="custom",
    )
    parser.add_argument("--baseline-version")
    parser.add_argument("--target-version")
    parser.add_argument("--version-source")
    parser.add_argument("--version-policy-ref")
    parser.add_argument("--release-train-id")
    parser.add_argument(
        "--versioning-reason",
        required=True,
        help="Why project version governance is tracked or not applicable",
    )
    parser.add_argument(
        "--user-confirmed",
        action="store_true",
        help="Confirm that the user approved the task graph and execution boundary",
    )
    parser.add_argument(
        "--confirmation-summary",
        default="User approved the documented task graph and execution boundary",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    project_root = Path(args.project_root).expanduser().resolve()
    if not project_root.is_dir():
        raise SystemExit(f"Project root does not exist: {project_root}")
    if args.coordination_mode == "direct":
        raise SystemExit(
            "Direct mode does not create a Run; work in the current task and use init_run only with --coordination-mode coordinated"
        )
    project_id = args.project_id or slug(project_root.name)
    project_name = args.project_name or project_root.name
    try:
        governance_paths = resolve_governance_project(
            project_root,
            project_id,
            args.governance_root,
            require_existing=False,
        )
    except ProtocolError as exc:
        raise SystemExit(str(exc)) from exc
    numeric_policies = {
        "--max-parallel": args.max_parallel,
        "--max-document-delegation-depth": args.max_document_delegation_depth,
        "--ack-timeout-seconds": args.ack_timeout_seconds,
        "--lease-seconds": args.lease_seconds,
        "--max-attempts": args.max_attempts,
    }
    for option, value in numeric_policies.items():
        if value < 1:
            raise SystemExit(f"{option} must be at least 1")
    if not args.user_confirmed:
        raise SystemExit(
            "--user-confirmed is required; initialize only after explicit user confirmation"
        )
    if not args.versioning_reason.strip():
        raise SystemExit("--versioning-reason must not be empty")
    dispatch_policy = args.dispatch_policy
    if dispatch_policy == "auto":
        dispatch_policy = "central" if args.governance == "strict" else "hybrid"
    if args.governance == "strict" and args.execution_profile == "fast":
        raise SystemExit("strict governance cannot use the fast execution profile")
    if args.governance == "strict" and dispatch_policy != "central":
        raise SystemExit("strict governance requires central dispatch policy")

    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    run_id = args.run_id or f"RUN-{timestamp}-{secrets.token_hex(3)}"
    if not re.fullmatch(r"RUN-[A-Za-z0-9._-]+", run_id):
        raise SystemExit("run id must start with RUN- and contain only letters, digits, dot, underscore, or dash")
    release_train_id = args.release_train_id or f"REL-{run_id.removeprefix('RUN-')}"
    if not re.fullmatch(r"REL-[A-Za-z0-9._-]+", release_train_id):
        raise SystemExit(
            "release train id must start with REL- and contain only letters, digits, dot, underscore, or dash"
        )
    version_source: Path | None = None
    version_policy_ref: Path | None = None
    if args.versioning_mode == "tracked":
        missing = [
            option
            for option, value in (
                ("--baseline-version", args.baseline_version),
                ("--target-version", args.target_version),
                ("--version-source", args.version_source),
            )
            if not value
        ]
        if missing:
            raise SystemExit(
                "tracked versioning requires " + ", ".join(missing)
            )
        if args.baseline_version == args.target_version:
            raise SystemExit("tracked versioning requires target version to differ from baseline")
        version_source = Path(args.version_source).expanduser()
        if not version_source.is_absolute():
            version_source = project_root / version_source
        version_source = version_source.resolve()
        try:
            version_source.relative_to(project_root)
        except ValueError as exc:
            raise SystemExit("--version-source must stay inside project_root") from exc
        if not version_source.is_file():
            raise SystemExit(f"version source does not exist: {version_source}")
        try:
            version_source_text = version_source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise SystemExit("version source must be a readable UTF-8 text file") from exc
        if args.baseline_version not in version_source_text:
            raise SystemExit(
                "baseline version was not found in the declared version source"
            )
    elif any((args.baseline_version, args.target_version, args.version_source)):
        raise SystemExit(
            "not_applicable versioning must not declare baseline, target, or version source"
        )
    if args.version_policy_ref:
        version_policy_ref = Path(args.version_policy_ref).expanduser()
        if not version_policy_ref.is_absolute():
            version_policy_ref = project_root / version_policy_ref
        version_policy_ref = version_policy_ref.resolve()
        try:
            version_policy_ref.relative_to(project_root)
        except ValueError as exc:
            raise SystemExit("--version-policy-ref must stay inside project_root") from exc
        if not version_policy_ref.is_file():
            raise SystemExit(f"version policy does not exist: {version_policy_ref}")

    try:
        binding_file = write_project_binding(governance_paths, project_name)
    except ProtocolError as exc:
        raise SystemExit(str(exc)) from exc
    bus_root = governance_paths.project_dir
    init_lock = exclusive_lock(bus_root / ".init.lock")
    init_lock.__enter__()
    run_dir = bus_root / "runs" / run_id
    if run_dir.exists():
        raise SystemExit(f"Run already exists: {run_dir}")
    project_file = bus_root / "project.yaml"
    if project_file.exists():
        try:
            project_values = scalar_map(
                project_file.read_text(encoding="utf-8"),
                source=str(project_file),
            )
            allowed_roots = json.loads(project_values.get("allowed_roots", "[]"))
        except (ProtocolError, json.JSONDecodeError) as exc:
            raise SystemExit(str(exc)) from exc
        if project_values.get("project_root") != str(project_root):
            raise SystemExit(f"Existing document bus belongs to another project: {project_file}")
        if project_values.get("protocol_version") != PROTOCOL_VERSION:
            raise SystemExit(f"Existing project metadata uses an unsupported protocol: {project_file}")
        if (
            not isinstance(allowed_roots, list)
            or not all(isinstance(item, str) for item in allowed_roots)
            or str(project_root) not in {
                str(Path(item).expanduser().resolve()) for item in allowed_roots
            }
            or project_values.get("coordinator") != "coordinator"
            or project_values.get("secrets_policy") != "references_only"
        ):
            raise SystemExit(f"Existing project metadata is incompatible: {project_file}")
    protocol_file = bus_root / "protocol.yaml"
    if protocol_file.exists():
        try:
            protocol_values = scalar_map(
                protocol_file.read_text(encoding="utf-8"),
                source=str(protocol_file),
            )
        except ProtocolError as exc:
            raise SystemExit(str(exc)) from exc
        expected_protocol = {
            "protocol_version": PROTOCOL_VERSION,
            "name": "multi-agent-collaboration-document-bus",
            "delivery": "at_least_once",
            "canonical_record": "documents",
            "secrets_policy": "references_only",
        }
        if any(protocol_values.get(key) != value for key, value in expected_protocol.items()):
            raise SystemExit(f"Existing document bus protocol is incompatible: {protocol_file}")
    legacy_agents = bus_root / "agents.yaml"
    if legacy_agents.exists():
        raise SystemExit(
            f"Legacy shared Agent Registry must be migrated explicitly before v3: {legacy_agents}"
        )

    created_at = now_iso()
    directories = (
        "tasks",
        "inbox/coordinator",
        "outbox/coordinator",
        "events",
        "decisions",
        "artifacts",
        "evidence",
        "locks",
        "operations",
        "dead-letter",
        "delegations",
        "claims/tasks",
        "claims/threads",
        "native/threads",
        "native/operations",
        "versions/candidates",
        "config",
        "archive",
    )
    for directory in directories:
        (run_dir / directory).mkdir(parents=True, exist_ok=False)

    if not protocol_file.exists():
        atomic_write(
            protocol_file,
            "\n".join(
                (
                    f"protocol_version: {PROTOCOL_VERSION}",
                    'name: "multi-agent-collaboration-document-bus"',
                    'delivery: "at_least_once"',
                    'canonical_record: "documents"',
                    'secrets_policy: "references_only"',
                    "",
                )
            ),
        )

    if not project_file.exists():
        atomic_write(
            project_file,
            "\n".join(
                (
                    f"protocol_version: {PROTOCOL_VERSION}",
                    f"project_id: {quote(project_id)}",
                    f"project_root: {quote(str(project_root))}",
                    f"allowed_roots: {json.dumps([str(project_root)], ensure_ascii=False)}",
                    f"project_binding_ref: {quote(str(binding_file))}",
                    f"created_at: {quote(created_at)}",
                    'coordinator: "coordinator"',
                    'secrets_policy: "references_only"',
                    "",
                )
            ),
        )
    agents_file = run_dir / "agents.yaml"
    atomic_write(
        agents_file,
        "\n".join(
            (
                f"protocol_version: {PROTOCOL_VERSION}",
                f"run_id: {quote(run_id)}",
                "agents:",
                '  - agent_id: "coordinator"',
                f"    runtime: {quote('codex_thread' if args.transport != 'document_bus' else 'document')}",
                '    role: "Coordinator"',
                '    status: "ready"',
                "    parent_agent_id: null",
                "    delegation_depth: 0",
                "    readable_paths:",
                f"      - {quote(str(project_root))}",
                "    writable_paths: []",
                "    forbidden_paths: []",
                '    capabilities: ["task_publish", "task_claim", "thread_claim"]',
                "    thread_id: null",
                '    inbox: "inbox/coordinator"',
                '    outbox: "outbox/coordinator"',
                "    current_task: null",
                "    handoff_to: null",
                "",
            )
        ),
    )

    confirmation_file = run_dir / "decisions" / "GATE-USER-CONFIRMATION.yaml"
    atomic_write(
        confirmation_file,
        "\n".join(
            (
                f"protocol_version: {PROTOCOL_VERSION}",
                'kind: "human_gate"',
                f"run_id: {quote(run_id)}",
                'gate_id: "GATE-USER-CONFIRMATION"',
                "task_id: null",
                'scope: "run_initialization"',
                'status: "approved"',
                'approved_by: "user"',
                f"approved_at: {quote(created_at)}",
                f"summary: {quote(args.confirmation_summary)}",
                "",
            )
        ),
    )

    baseline_commit = "null"
    if args.versioning_mode == "tracked":
        git_head = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
        )
        if git_head.returncode == 0 and git_head.stdout.strip():
            baseline_commit = quote(git_head.stdout.strip())
    version_contract_file = run_dir / "versions" / "version-contract.yaml"
    atomic_write(
        version_contract_file,
        "\n".join(
            (
                f"protocol_version: {PROTOCOL_VERSION}",
                'kind: "version_contract"',
                'contract_version: "1"',
                f"run_id: {quote(run_id)}",
                f"release_train_id: {quote(release_train_id)}",
                f"versioning_mode: {quote(args.versioning_mode)}",
                f"version_scheme: {quote(args.version_scheme) if args.versioning_mode == 'tracked' else 'null'}",
                f"baseline_version: {quote(args.baseline_version) if args.baseline_version else 'null'}",
                f"baseline_commit: {baseline_commit}",
                f"target_version: {quote(args.target_version) if args.target_version else 'null'}",
                f"version_source_ref: {quote(str(version_source)) if version_source else 'null'}",
                f"version_source_sha256: {quote(sha256(version_source)) if version_source else 'null'}",
                f"version_policy_ref: {quote(str(version_policy_ref)) if version_policy_ref else 'null'}",
                f"version_policy_sha256: {quote(sha256(version_policy_ref)) if version_policy_ref else 'null'}",
                'owner_agent: "coordinator"',
                f"reason: {quote(args.versioning_reason.strip())}",
                f"created_at: {quote(created_at)}",
                "",
            )
        ),
    )

    retry_policy_file = run_dir / "config" / "retry-policy.yaml"
    atomic_write(
        retry_policy_file,
        "\n".join(
            (
                f"protocol_version: {PROTOCOL_VERSION}",
                'kind: "retry_policy"',
                f"run_id: {quote(run_id)}",
                'ack_timeout_seconds: 600',
                'progress_timeout_seconds: 900',
                'result_timeout_seconds: 600',
                'max_attempts_light: 2',
                'max_attempts_standard: 2',
                'max_attempts_strict: 1',
                'owner_noop_action: "blocked_then_reassign"',
                'auto_retry_light: true',
                'auto_retry_standard: false',
                'auto_retry_strict: false',
                'immutable_events: true',
                f"created_at: {quote(created_at)}",
                "",
            )
        ),
    )

    atomic_write(
        run_dir / "manifest.yaml",
        "\n".join(
            (
                f"protocol_version: {PROTOCOL_VERSION}",
                f"run_id: {quote(run_id)}",
                f"objective: {quote(args.objective)}",
                'status: "initializing"',
                'coordination_mode: "coordinated"',
                'governance_storage_schema: "1.0"',
                f"governance: {quote(args.governance)}",
                f"execution_profile: {quote(args.execution_profile)}",
                f"dispatch_policy: {quote(dispatch_policy)}",
                "preflight_required: true",
                f"transport: {quote(args.transport)}",
                f"max_parallel: {args.max_parallel}",
                f"max_document_delegation_depth: {args.max_document_delegation_depth}",
                'delivery: "at_least_once"',
                f"ack_timeout_seconds: {args.ack_timeout_seconds}",
                f"lease_seconds: {args.lease_seconds}",
                f"max_attempts: {args.max_attempts}",
                f"created_at: {quote(created_at)}",
                f"user_confirmation_ref: {quote(str(confirmation_file))}",
                f"user_confirmation_ref_sha256: {quote(sha256(confirmation_file))}",
                f"versioning_mode: {quote(args.versioning_mode)}",
                f"release_train_id: {quote(release_train_id)}",
                f"baseline_version: {quote(args.baseline_version) if args.baseline_version else 'null'}",
                f"target_version: {quote(args.target_version) if args.target_version else 'null'}",
                f"version_contract_ref: {quote(str(version_contract_file))}",
                f"version_contract_ref_sha256: {quote(sha256(version_contract_file))}",
                f"retry_policy_ref: {quote(str(retry_policy_file))}",
                f"retry_policy_ref_sha256: {quote(sha256(retry_policy_file))}",
                "scope_freeze_ref: null",
                "scope_freeze_ref_sha256: null",
                'self_service_parent_scope: "task_owner_or_declared_collaborator"',
                "release_candidates: []",
                "change_id: null",
                "registry_ref: null",
                "registry_ref_sha256: null",
                "git_branch: null",
                "git_status_ref: null",
                "git_status_ref_sha256: null",
                "environment_impact_ref: null",
                "environment_impact_ref_sha256: null",
                "rollback_ref: null",
                "rollback_ref_sha256: null",
                "security_review_ref: null",
                "security_review_ref_sha256: null",
                "release_environment: null",
                "release_authorization_ref: null",
                "release_authorization_ref_sha256: null",
                "clean_worktree_ref: null",
                "clean_worktree_ref_sha256: null",
                "tasks: []",
                'human_gates: ["GATE-USER-CONFIRMATION"]',
                "",
            )
        ),
    )
    atomic_write(
        run_dir / "state.yaml",
        render_state(run_id, {}, 0, created_at),
    )
    atomic_write(
        run_dir / "next-action.md",
        "\n".join(
            (
                "# Next Action",
                "",
                f"- Run: `{run_id}`",
                "- Current status: `initializing`",
                "- Ready task: `none`",
                "- Target agent: `coordinator`",
                f"- Transport: `{args.transport}`",
                f"- Version governance: `{args.versioning_mode}`",
                f"- Release train: `{release_train_id}`",
                f"- Delivery version: `{args.target_version or 'not_applicable'}`",
                "- Action: define the run-local agents and create the approved task graph",
                "- Blocking gate: `none`",
                "",
            )
        ),
    )
    atomic_write(
        run_dir / "summary.md",
        "\n".join(
            (
                "# Run Summary",
                "",
                f"- Run: `{run_id}`",
                f"- Objective: {args.objective}",
                "- Status: initializing",
                f"- Version governance: {args.versioning_mode}",
                f"- Release train: {release_train_id}",
                f"- Delivery version: {args.target_version or 'not_applicable'}",
                "- Completed tasks: none",
                "- Remaining tasks: task graph not yet approved",
                "- Evidence: none",
                "- Residual risks: execution has not started",
                "",
            )
        ),
    )
    atomic_write(bus_root / "current-run", f"{run_id}\n")
    init_lock.__exit__(None, None, None)
    print(run_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
