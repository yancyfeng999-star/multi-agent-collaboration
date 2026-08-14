#!/usr/bin/env python3
"""Audit and safely close a temporary Git worktree without destructive force flags."""

from __future__ import annotations

import argparse
import os
import subprocess
from pathlib import Path
from typing import Callable, Any

from evidence_layers import freeze_active, load_release_freeze
from integration_lib import common_git_dir, git, resolve_commit
from protocol_lib import ProtocolError


ProcessChecker = Callable[[Path], list[str]]


def _registered_worktrees(root: Path) -> list[dict[str, Any]]:
    result = git(root, "worktree", "list", "--porcelain")
    if result.returncode:
        raise ProtocolError(f"cannot inspect registered worktrees: {result.stderr.strip()}")
    records: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in result.stdout.splitlines():
        if line.startswith("worktree "):
            if current:
                records.append(current)
            current = {"path": line.split(" ", 1)[1], "locked": False}
        elif current is not None and line.startswith("branch "):
            current["branch"] = line.split(" ", 1)[1]
        elif current is not None and line == "locked":
            current["locked"] = True
    if current:
        records.append(current)
    return records


def _default_process_checker(worktree: Path) -> list[str]:
    result = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=False)
    if result.returncode:
        raise ProtocolError(f"cannot inspect active processes: {result.stderr.strip()}")
    needle = str(worktree)
    findings: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) == 2 and parts[0].isdigit() and int(parts[0]) != os.getpid() and needle in parts[1]:
            findings.append(line.strip())
    return findings


def _safe_target(root: Path, raw_target: str | Path) -> tuple[Path, list[str]]:
    input_path = Path(raw_target).expanduser()
    if input_path.is_symlink():
        return input_path, ["symlink_target"]
    target = input_path.resolve()
    blockers: list[str] = []
    if target in {Path("/"), Path.home(), root}:
        raise ProtocolError("worktree target cannot be project root, home or filesystem root")
    try:
        target.relative_to(root)
    except ValueError:
        pass
    else:
        raise ProtocolError("worktree target cannot be inside the project root")
    if not target.exists():
        blockers.append("worktree_missing")
    return target, blockers


def _merge_head_path(worktree: Path) -> Path:
    result = git(worktree, "rev-parse", "--git-path", "MERGE_HEAD")
    if result.returncode:
        raise ProtocolError(f"cannot inspect MERGE_HEAD: {result.stderr.strip()}")
    path = Path(result.stdout.strip())
    return path if path.is_absolute() else worktree / path


def _candidate_preserved(root: Path, commit: str) -> bool:
    try:
        resolved = resolve_commit(root, commit)
    except ProtocolError:
        return False
    refs = git(root, "for-each-ref", "--contains", resolved, "refs/heads", "refs/tags", "refs/remotes")
    return refs.returncode == 0 and bool(refs.stdout.strip())


def audit_worktree(
    project_root: str | Path,
    worktree_path: str | Path,
    *,
    candidate_commit: str | None,
    process_checker: ProcessChecker | None = None,
    release_active: bool = False,
    release_freeze_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return an audit without removing a worktree or changing any ref."""

    root = Path(project_root).expanduser().resolve()
    if not root.is_dir():
        raise ProtocolError(f"project_root must be an existing directory: {root}")
    target, blockers = _safe_target(root, worktree_path)
    result: dict[str, Any] = {
        "project_root": str(root),
        "worktree": str(target),
        "registered": False,
        "worktree_clean": False,
        "merge_state_clear": False,
        "candidate_preserved": False,
        "active_processes": [],
        "release_active": bool(release_active),
        "blockers": list(blockers),
        "read_only": True,
        "write_performed": False,
    }
    if blockers:
        result["ready"] = False
        return result

    records = _registered_worktrees(root)
    matching = [record for record in records if Path(str(record["path"])).expanduser().resolve() == target]
    if not matching:
        result["blockers"].append("worktree_not_registered")
        result["ready"] = False
        return result
    result["registered"] = True
    if matching[0].get("locked"):
        result["blockers"].append("worktree_locked")
    try:
        if common_git_dir(root) != common_git_dir(target):
            result["blockers"].append("git_common_dir_mismatch")
    except ProtocolError:
        result["blockers"].append("git_common_dir_unknown")

    status = git(target, "status", "--porcelain", "--untracked-files=all")
    if status.returncode or status.stdout.strip():
        result["blockers"].append("worktree_dirty")
    else:
        result["worktree_clean"] = True
    try:
        merge_head = _merge_head_path(target)
        if merge_head.exists() and merge_head.read_text(encoding="utf-8").strip():
            result["blockers"].append("merge_in_progress")
        else:
            result["merge_state_clear"] = True
    except (OSError, ProtocolError):
        result["blockers"].append("merge_state_unknown")

    checker = process_checker or _default_process_checker
    try:
        result["active_processes"] = checker(target)
        if result["active_processes"]:
            result["blockers"].append("active_processes")
    except Exception as error:  # fail closed when process state cannot be proven
        result["blockers"].append("process_state_unknown")
        result["process_error"] = str(error)

    if release_freeze_path is not None:
        try:
            freeze = load_release_freeze(release_freeze_path, root)
            result["release_active"] = freeze_active(freeze)
        except (OSError, ProtocolError) as error:
            result["blockers"].append("release_state_unknown")
            result["release_error"] = str(error)
    if result["release_active"]:
        result["blockers"].append("release_or_freeze_active")

    if not candidate_commit:
        result["blockers"].append("candidate_commit_missing")
    elif not _candidate_preserved(root, candidate_commit):
        result["blockers"].append("candidate_commit_not_preserved")
    else:
        result["candidate_preserved"] = True
    result["blockers"] = list(dict.fromkeys(result["blockers"]))
    result["ready"] = not result["blockers"]
    return result


def cleanup_worktree(
    project_root: str | Path,
    worktree_path: str | Path,
    *,
    candidate_commit: str | None,
    process_checker: ProcessChecker | None = None,
    release_active: bool = False,
    release_freeze_path: str | Path | None = None,
    user_confirmed: bool,
) -> dict[str, Any]:
    if not user_confirmed:
        raise ProtocolError("worktree cleanup requires explicit user confirmation")
    audit = audit_worktree(
        project_root,
        worktree_path,
        candidate_commit=candidate_commit,
        process_checker=process_checker,
        release_active=release_active,
        release_freeze_path=release_freeze_path,
    )
    if not audit["ready"]:
        raise ProtocolError("worktree cleanup blocked: " + ", ".join(audit["blockers"]))
    root = Path(project_root).expanduser().resolve()
    target = Path(worktree_path).expanduser().resolve()
    removed = git(root, "worktree", "remove", str(target))
    if removed.returncode:
        detail = removed.stderr.strip() or removed.stdout.strip() or f"exit {removed.returncode}"
        raise ProtocolError(f"git worktree remove failed: {detail}")
    if target.exists():
        raise ProtocolError("git worktree remove completed but target still exists")
    return {
        **audit,
        "removed": True,
        "write_performed": True,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    for name in ("audit", "cleanup"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--project-root", required=True)
        sub.add_argument("--worktree", required=True)
        sub.add_argument("--candidate-commit")
        sub.add_argument("--release-freeze")
        sub.add_argument("--release-active", action="store_true")
        if name == "cleanup":
            sub.add_argument("--user-confirmed", action="store_true")
    return parser


def main() -> int:
    parser = _parser()
    args = parser.parse_args()
    try:
        if args.command == "audit":
            result = audit_worktree(
                args.project_root,
                args.worktree,
                candidate_commit=args.candidate_commit,
                release_active=args.release_active,
                release_freeze_path=args.release_freeze,
            )
        else:
            result = cleanup_worktree(
                args.project_root,
                args.worktree,
                candidate_commit=args.candidate_commit,
                release_active=args.release_active,
                release_freeze_path=args.release_freeze,
                user_confirmed=args.user_confirmed,
            )
    except (OSError, ProtocolError) as error:
        parser.error(str(error))
    import json
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ready", True) else 2


if __name__ == "__main__":
    raise SystemExit(main())
