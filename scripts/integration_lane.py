#!/usr/bin/env python3
"""Evaluate candidates independently and integrate one candidate serially."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, Iterable

from integration_lib import (
    branch_checked_out,
    branch_commit,
    changed_paths,
    common_git_dir,
    conflict_dimensions,
    git,
    git_output,
    is_ancestor,
    load_candidate,
    read_freeze,
    resolve_commit,
    worktree_clean,
)
from integration_policy import load_integration_policy
from project_memory_lib import exclusive_lock
from protocol_lib import ProtocolError


def _candidate_value(value: str | Path | dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    return load_candidate(value)


def _verification_blockers(candidate: dict[str, Any]) -> list[str]:
    return [
        "verification_not_passed"
        for record in candidate["verification"]
        if record["status"] != "passed"
    ][:1]


def evaluate_candidate(
    candidate_value: str | Path | dict[str, Any],
    project_root: str | Path,
    policy_path: str | Path,
    *,
    against_candidates: Iterable[str | Path | dict[str, Any]] | None = None,
    satisfied_dependencies: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Read-only candidate evaluation; no ref, worktree or lock is changed."""

    root = Path(project_root).expanduser().resolve()
    policy = load_integration_policy(policy_path, root)
    candidate = _candidate_value(candidate_value)
    blockers: list[str] = []
    conflicts: list[dict[str, Any]] = []
    try:
        baseline = resolve_commit(root, candidate["baseline_commit"])
        candidate_commit = resolve_commit(root, candidate["candidate_commit"])
    except ProtocolError as error:
        blockers.append("commit_unavailable")
        baseline = candidate["baseline_commit"]
        candidate_commit = candidate["candidate_commit"]
        commit_error = str(error)
    else:
        commit_error = None
        if not is_ancestor(root, baseline, candidate_commit):
            blockers.append("baseline_not_ancestor")
        actual_paths = changed_paths(root, baseline, candidate_commit)
        if sorted(candidate["changed_paths"]) != actual_paths:
            blockers.append("changed_paths_mismatch")

    if commit_error:
        blockers.append(commit_error)
    if candidate["status"] not in {"ready", "submitted"}:
        blockers.append("candidate_not_ready")
    blockers.extend(_verification_blockers(candidate))
    if candidate["quality_required"] and candidate["quality_status"] != "passed":
        blockers.append("quality_not_passed")
    if candidate["version_source"] and policy["version_authority"] and candidate["version_source"] != policy["version_authority"]:
        blockers.append("version_authority_mismatch")
    satisfied = set(satisfied_dependencies or [])
    unresolved = set(candidate["dependencies"]) - satisfied
    if unresolved:
        blockers.append("dependencies_not_proven")

    for other_value in against_candidates or []:
        other = _candidate_value(other_value)
        if other["candidate_id"] == candidate["candidate_id"]:
            raise ProtocolError(f"candidate id is duplicated: {candidate['candidate_id']}")
        dimensions = conflict_dimensions(candidate, other, policy["high_conflict_paths"])
        if dimensions:
            conflicts.append({"candidate_id": other["candidate_id"], "dimensions": sorted(set(dimensions))})

    if blockers:
        status = "blocked"
    elif conflicts:
        status = "conflicted"
    else:
        status = "ready"
    return {
        "status": status,
        "candidate_id": candidate["candidate_id"],
        "baseline_commit": candidate["baseline_commit"],
        "candidate_commit": candidate["candidate_commit"],
        "resolved_baseline": baseline,
        "resolved_candidate": candidate_commit,
        "blockers": list(dict.fromkeys(blockers)),
        "conflicts": conflicts,
        "candidate_reachable": False,
        "read_only": True,
        "write_performed": False,
    }


def _target_branch(policy: dict[str, Any], target: str) -> str:
    if target == "working":
        return policy["working_branch"]
    if target == "canonical":
        return policy["canonical_branch"]
    raise ProtocolError("target must be working or canonical")


def _ensure_freeze_allows(freeze_path: str | Path | None, target: str, branch: str) -> None:
    freeze = read_freeze(freeze_path)
    if not freeze or not bool(freeze.get("active", False)):
        return
    frozen_branch = freeze.get("canonical_branch") or freeze.get("branch")
    if target == "canonical" and (frozen_branch in {None, branch}):
        raise ProtocolError("release freeze is active; canonical branch movement is blocked")


def _merge_tree(root: Path, target_commit: str, candidate_commit: str) -> str:
    result = git(root, "merge-tree", "--write-tree", target_commit, candidate_commit)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ProtocolError(f"candidate merge preflight failed: {detail}")
    tree = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
    if not tree or len(tree) != 40 or any(char not in "0123456789abcdef" for char in tree.lower()):
        raise ProtocolError("candidate merge preflight did not return a valid tree")
    return tree


def _commit_merge(root: Path, tree: str, target_commit: str, candidate_commit: str, candidate_id: str) -> str:
    result = git(
        root,
        "commit-tree",
        tree,
        "-p",
        target_commit,
        "-p",
        candidate_commit,
        "-m",
        f"Integrate candidate {candidate_id}",
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ProtocolError(f"cannot create integration commit: {detail}")
    return result.stdout.strip()


def integrate_candidate(
    candidate_value: str | Path | dict[str, Any],
    project_root: str | Path,
    policy_path: str | Path,
    *,
    target: str,
    user_confirmed: bool,
    against_candidates: Iterable[str | Path | dict[str, Any]] | None = None,
    satisfied_dependencies: Iterable[str] | None = None,
    release_freeze_path: str | Path | None = None,
) -> dict[str, Any]:
    """Serially update one branch after read-only evaluation and confirmation."""

    if not user_confirmed:
        raise ProtocolError("integration requires explicit user confirmation")
    root = Path(project_root).expanduser().resolve()
    policy = load_integration_policy(policy_path, root)
    candidate = _candidate_value(candidate_value)
    branch = _target_branch(policy, target)
    _ensure_freeze_allows(release_freeze_path, target, branch)
    if not worktree_clean(root):
        raise ProtocolError("integration requires a clean current worktree")
    if branch_checked_out(root, branch):
        raise ProtocolError(f"target branch is checked out in a worktree: {branch}")

    evaluation = evaluate_candidate(
        candidate,
        root,
        policy_path,
        against_candidates=against_candidates,
        satisfied_dependencies=satisfied_dependencies,
    )
    if evaluation["status"] != "ready":
        raise ProtocolError(
            f"candidate is not ready for integration: {evaluation['status']} "
            f"blockers={evaluation['blockers']} conflicts={evaluation['conflicts']}"
        )

    common_dir = common_git_dir(root)
    lock_path = common_dir / "multi-agent-collaboration.integration.lock"
    with exclusive_lock(lock_path):
        target_commit = branch_commit(root, branch)
        resolved_candidate = evaluation["resolved_candidate"]
        resolved_baseline = evaluation["resolved_baseline"]
        if is_ancestor(root, resolved_candidate, target_commit):
            return {
                "status": "already_integrated",
                "target": target,
                "target_branch": branch,
                "previous_commit": target_commit,
                "integrated_commit": target_commit,
                "candidate_commit": candidate["candidate_commit"],
                "candidate_reachable": True,
                "coordination_message": {
                    "kind": "INTEGRATED",
                    "main_hash": target_commit,
                    "candidate_status": "already_integrated",
                    "remaining_work": [],
                },
                "write_performed": False,
            }
        if not is_ancestor(root, resolved_baseline, target_commit):
            raise ProtocolError("target branch changed or diverged from candidate baseline; re-evaluate candidate")

        method = policy["integration_method"]
        if method == "fast_forward_only" and not is_ancestor(root, target_commit, resolved_candidate):
            raise ProtocolError("fast_forward_only policy rejects a non-descendant candidate")
        if is_ancestor(root, target_commit, resolved_candidate):
            integrated = resolved_candidate
        else:
            tree = _merge_tree(root, target_commit, resolved_candidate)
            integrated = _commit_merge(root, tree, target_commit, resolved_candidate, candidate["candidate_id"])
        ref = f"refs/heads/{branch}"
        update = git(root, "update-ref", ref, integrated, target_commit)
        if update.returncode:
            detail = update.stderr.strip() or update.stdout.strip() or f"exit {update.returncode}"
            raise ProtocolError(f"target ref changed during integration: {detail}")
        reachable = is_ancestor(root, resolved_candidate, integrated)
        if not reachable:
            raise ProtocolError("integration completed without candidate reachability proof")
        return {
            "status": "integrated",
            "target": target,
            "target_branch": branch,
            "previous_commit": target_commit,
            "integrated_commit": integrated,
            "candidate_commit": candidate["candidate_commit"],
            "candidate_reachable": reachable,
            "coordination_message": {
                "kind": "INTEGRATED",
                "main_hash": integrated,
                "candidate_status": "integrated",
                "remaining_work": [],
            },
            "write_performed": True,
        }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("evaluate", "integrate"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--project-root", required=True)
        sub.add_argument("--policy", required=True)
        sub.add_argument("--candidate", required=True)
        sub.add_argument("--against-candidate", action="append", default=[])
        sub.add_argument("--satisfied-dependency", action="append", default=[])
        if name == "integrate":
            sub.add_argument("--target", choices=("working", "canonical"), required=True)
            sub.add_argument("--release-freeze")
            sub.add_argument("--user-confirmed", action="store_true")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    try:
        if args.command == "evaluate":
            result = evaluate_candidate(
                args.candidate,
                args.project_root,
                args.policy,
                against_candidates=args.against_candidate,
                satisfied_dependencies=args.satisfied_dependency,
            )
        else:
            result = integrate_candidate(
                args.candidate,
                args.project_root,
                args.policy,
                target=args.target,
                user_confirmed=args.user_confirmed,
                against_candidates=args.against_candidate,
                satisfied_dependencies=args.satisfied_dependency,
                release_freeze_path=args.release_freeze,
            )
    except (OSError, ProtocolError) as error:
        parser.error(str(error))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
