#!/usr/bin/env python3
"""Create an immutable, hash-bound project checkpoint and refresh its pointer."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from project_memory_lib import bus_root, exclusive_lock, project_root as resolve_project_root

PCP_RE = re.compile(r"PCP-(\d{4})\.md$")


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_profile_hash(profile: dict[str, Any]) -> str:
    unhashed = dict(profile)
    unhashed.pop("record_hash", None)
    payload = json.dumps(unhashed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _json_yaml(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid machine-readable file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"expected object: {path}")
    return value


def _flat(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#") or ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        value = value.strip()
        try:
            result[key.strip()] = json.loads(value)
        except json.JSONDecodeError:
            result[key.strip()] = value
    return result


def _frontmatter(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        return {}
    end = text.find("\n---\n", 4)
    # Parse in memory using the same flat scalar rules.
    result: dict[str, Any] = {}
    for raw in text[4:end].splitlines():
        if ":" not in raw:
            continue
        key, value = raw.split(":", 1)
        try:
            result[key.strip()] = json.loads(value.strip())
        except json.JSONDecodeError:
            result[key.strip()] = value.strip()
    return result


def _atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _write_immutable(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
    try:
        os.write(descriptor, content.encode("utf-8"))
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _relative(path: Path, bus: Path) -> str:
    try:
        return path.resolve().relative_to(bus.resolve()).as_posix()
    except ValueError as exc:
        raise ValueError(f"source outside project store: {path}") from exc


def _owned_relative(path: Path, owner: Path, bus: Path, kind: str) -> str:
    try:
        path.resolve().relative_to(owner.resolve())
    except ValueError as exc:
        raise ValueError(f"{kind} does not belong to Agent: {path}") from exc
    return _relative(path, bus)


def _runtime_profile(agent: Path, bus: Path, agent_id: str, raw_ref: Any) -> tuple[dict[str, Any], Path]:
    if not isinstance(raw_ref, str) or not raw_ref:
        raise ValueError("activity runtime profile reference is missing")
    profile_path = (agent / raw_ref).resolve()
    profiles = (agent / "runtime" / "profiles").resolve()
    try:
        profile_path.relative_to(profiles)
    except ValueError as exc:
        raise ValueError(f"runtime profile does not belong to Agent: {raw_ref}") from exc
    if not profile_path.is_file():
        raise ValueError(f"runtime profile does not exist: {raw_ref}")
    profile = _json_yaml(profile_path)
    if profile.get("agent_id") != agent_id or profile.get("runtime_profile_id") != profile_path.stem:
        raise ValueError(f"runtime profile identity mismatch: {raw_ref}")
    record_hash = profile.get("record_hash")
    stored_hash = record_hash.get("value") if isinstance(record_hash, dict) else None
    if stored_hash != _canonical_profile_hash(profile):
        raise ValueError(f"runtime profile hash mismatch: {raw_ref}")
    return profile, profile_path


def _agent_runtime_snapshot(
    agent: Path, bus: Path, agent_id: str, selected_runs: set[str]
) -> tuple[dict[str, Any], list[Path], dict[str, Any]]:
    activity_paths: list[Path] = []
    profile_by_ref: dict[str, dict[str, Any]] = {}
    latest_runtime_view: dict[str, Any] | None = None
    for path in sorted((agent / "activity").glob("**/*.json")):
        activity = _json_yaml(path)
        if activity.get("run_id") not in selected_runs:
            continue
        if activity.get("agent_id") != agent_id:
            raise ValueError(f"activity agent mismatch: {path}")
        runtime = activity.get("runtime_profile")
        raw_ref = runtime.get("native_binding_ref") if isinstance(runtime, dict) else None
        profile, profile_path = _runtime_profile(agent, bus, agent_id, raw_ref)
        relative = _owned_relative(profile_path, agent, bus, "runtime profile")
        profile_by_ref[relative] = {
            "ref": relative,
            "sha256": profile["record_hash"]["value"],
            "actual_model": {key: profile["model"].get(key) for key in ("status", "value")},
            "actual_provider": {key: profile["provider"].get(key) for key in ("status", "value")},
        }
        recorded_at = activity.get("recorded_at")
        activity_status = activity.get("status")
        if isinstance(activity_status, dict):
            activity_status = activity_status.get("attempt_status")
        candidate = {
            "agent_id": agent_id,
            "actual_model": {key: profile.get("model", {}).get(key) for key in ("status", "value")},
            "actual_provider": {key: profile.get("provider", {}).get(key) for key in ("status", "value")},
            "runtime_profile_id": profile.get("runtime_profile_id"),
            "platform": {key: profile.get("platform", {}).get(key) for key in ("status", "value")},
            "session": {key: profile.get("session", {}).get(key) for key in ("status", "value")},
            "latest_activity_at": recorded_at if isinstance(recorded_at, str) and recorded_at else None,
            "activity_status": activity_status if isinstance(activity_status, str) and activity_status else None,
            "_activity_ref": _owned_relative(path, agent, bus, "Agent evidence"),
        }
        candidate_key = (candidate["latest_activity_at"] or "", candidate["_activity_ref"])
        current_key = (
            latest_runtime_view["latest_activity_at"] or "",
            latest_runtime_view["_activity_ref"],
        ) if latest_runtime_view else None
        if current_key is None or candidate_key > current_key:
            latest_runtime_view = candidate
        activity_paths.append(path)

    handoff_paths: list[Path] = []
    for path in sorted((agent / "handoffs").glob("*.md")):
        meta = _frontmatter(path)
        activity_ref = meta.get("activity_record_path")
        activity_run = None
        if isinstance(activity_ref, str):
            parts = Path(activity_ref).parts
            activity_run = parts[parts.index("activity") + 1] if "activity" in parts and len(parts) > parts.index("activity") + 1 else None
        if meta.get("run_id") not in selected_runs and activity_run not in selected_runs:
            continue
        if meta.get("agent_id") != agent_id:
            raise ValueError(f"handoff agent mismatch: {path}")
        handoff_paths.append(path)

    def references(paths: Iterable[Path]) -> list[dict[str, str]]:
        by_ref = {_owned_relative(path, agent, bus, "Agent evidence"): _hash(path) for path in paths}
        return [{"ref": ref, "sha256": digest} for ref, digest in sorted(by_ref.items())]

    snapshot = {
        "agent_id": agent_id,
        "runtime_profiles": [profile_by_ref[ref] for ref in sorted(profile_by_ref)],
        "activity_refs": references(activity_paths),
        "handoff_refs": references(handoff_paths),
    }
    runtime_view = latest_runtime_view or {
        "agent_id": agent_id,
        "actual_model": {"status": "unknown", "value": None},
        "actual_provider": {"status": "unknown", "value": None},
        "runtime_profile_id": None,
        "platform": {"status": "unknown", "value": None},
        "session": {"status": "unknown", "value": None},
        "latest_activity_at": None,
        "activity_status": None,
    }
    runtime_view.pop("_activity_ref", None)
    return snapshot, [*profile_by_ref_paths(profile_by_ref, bus), *activity_paths, *handoff_paths], runtime_view


def profile_by_ref_paths(profiles: dict[str, dict[str, Any]], bus: Path) -> list[Path]:
    return [bus / ref for ref in profiles]


def _git(root: Path) -> dict[str, Any]:
    def run(*args: str) -> str:
        result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)
        if result.returncode:
            raise ValueError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
        return result.stdout.strip()
    return {"branch": run("branch", "--show-current") or "DETACHED", "head": run("rev-parse", "HEAD"), "status": run("status", "--porcelain=v1")}


def _collect(root: Path, bus: Path, run_ids: Iterable[str] | None) -> tuple[Path, dict[str, Any], list[Path], list[dict[str, Any]]]:
    team_path = bus / "TEAM.yaml"
    decisions = bus / "DECISIONS.md"
    current = bus / "CURRENT_PROJECT_CONTEXT.md"
    for required in (team_path, decisions, current):
        if not required.is_file():
            raise ValueError(f"missing required project source: {required}")
    team = _json_yaml(team_path)
    # CURRENT_PROJECT_CONTEXT is the mutable pointer published after this checkpoint;
    # hashing it into the checkpoint would make every later PCP invalidate history.
    sources = [team_path, decisions]
    agents: list[dict[str, Any]] = []
    for record in team.get("agents", []):
        agent_id = record.get("agent_id")
        agent = bus / "agents" / str(agent_id)
        context = agent / "conversations" / "CURRENT_CONTEXT.md"
        if not context.is_file():
            raise ValueError(f"missing Agent context: {context}")
        sources.append(context)
        meta = _frontmatter(context)
        latest_ref = meta.get("latest_checkpoint")
        latest_path = agent / str(latest_ref) if latest_ref else None
        if latest_path:
            if not latest_path.is_file():
                raise ValueError(f"dangling Agent checkpoint: {latest_path}")
            sources.append(latest_path)
        handoffs = sorted((agent / "handoffs").glob("*.md"))
        if handoffs:
            sources.append(handoffs[-1])
        agents.append({"agent_id": agent_id, "status": record.get("status"), "active_task": meta.get("active_task"),
                       "latest_checkpoint": _relative(latest_path, bus) if latest_path else None,
                       "latest_handoff": _relative(handoffs[-1], bus) if handoffs else None})
    selected = list(run_ids or [])
    if not selected:
        selected = sorted(path.name for path in (bus / "runs").glob("RUN-*") if path.is_dir())
    if len(selected) != len(set(selected)):
        raise ValueError("duplicate run id")
    runs: list[dict[str, Any]] = []
    for run_id in selected:
        if not re.fullmatch(r"RUN-[A-Za-z0-9._-]+", run_id):
            raise ValueError(f"invalid run id: {run_id}")
        run = bus / "runs" / run_id
        manifest, state, summary = run / "manifest.yaml", run / "state.yaml", run / "summary.md"
        for source in (manifest, state, summary):
            if not source.is_file():
                raise ValueError(f"missing run source: {source}")
            sources.append(source)
        runs.append({"run_id": run_id, "status": _flat(state).get("status"), "task_states": _flat(state).get("task_states", {}),
                     "summary": _relative(summary, bus)})
    return bus, team, sorted(set(sources)), runs


def _next(directory: Path) -> tuple[str, str | None]:
    found = sorted((int(match.group(1)), path) for path in directory.glob("PCP-*.md") if (match := PCP_RE.fullmatch(path.name)))
    number = found[-1][0] + 1 if found else 1
    if found and [item[0] for item in found] != list(range(1, found[-1][0] + 1)):
        raise ValueError("project checkpoint chain has a sequence gap")
    return f"PCP-{number:04d}", f"PCP-{found[-1][0]:04d}" if found else None


def _display_actual(value: Any) -> str:
    if not isinstance(value, dict):
        return "unknown"
    status = value.get("status")
    actual = value.get("value")
    if status == "known" and isinstance(actual, str) and actual:
        return f"{actual} (known)"
    return status if isinstance(status, str) and status else "unknown"


def _render_context(project_id: str, now: str, checkpoint_ref: str, agents: list[dict[str, Any]], runs: list[dict[str, Any]], runtime_views: list[dict[str, Any]]) -> str:
    rows = [f"| {a['agent_id']} | {a['active_task'] or '-'} | {a['status'] or 'unknown'} | {a['latest_checkpoint'] or '-'} | {a['latest_handoff'] or '-'} |" for a in agents]
    status_by_agent = {a["agent_id"]: a.get("status") for a in agents}
    runtime_rows = [
        f"| {view['agent_id']} | {_display_actual(view['actual_model'])} | {_display_actual(view['actual_provider'])} | "
        f"{view['runtime_profile_id'] or 'unknown'} | {_display_actual(view['platform'])} / {_display_actual(view['session'])} | "
        f"{view['latest_activity_at'] or 'unknown'} | {view['activity_status'] or 'unknown'} | "
        f"{status_by_agent.get(view['agent_id']) or 'unknown'} |"
        for view in sorted(runtime_views, key=lambda item: item["agent_id"])
    ]
    run_rows = [f"- `{r['run_id']}`: `{r['status']}`; tasks={json.dumps(r['task_states'], ensure_ascii=False, sort_keys=True)}" for r in runs]
    return "\n".join(["---", 'schema_version: "1.0"', "doc_type: current_project_context", f"project_id: {json.dumps(project_id, ensure_ascii=False)}",
        f"updated_at: {json.dumps(now)}", f"latest_project_checkpoint: {json.dumps(checkpoint_ref)}", "---", "", "# 当前项目上下文", "",
        "## 模型分配表", "", "| Agent | Actual model (status) | Actual provider (status) | Runtime profile ID | Platform / session | 最近 activity | Activity 状态 | Agent 状态 |", "|---|---|---|---|---|---|---|---|", *runtime_rows, "",
        "## Agent 状态", "", "| Agent | 当前任务 | 状态 | 最新检查点 | 最近交接 |", "|---|---|---|---|---|", *rows, "", "## 关联 Run", "", *(run_rows or ["- none"]), "", "## 恢复入口", "", f"- `{checkpoint_ref}`", ""])


def create_project_checkpoint(
    project_root: str | Path,
    run_ids: Iterable[str] | None = None,
    *,
    dry_run: bool = False,
    governance_root: str | Path | None = None,
    project_id: str | None = None,
) -> dict[str, Any]:
    root = resolve_project_root(str(project_root))
    governance_bus = bus_root(root, governance_root=governance_root, project_id=project_id)
    bus, team, sources, runs = _collect(root, governance_bus, run_ids)
    selected_runs = {record["run_id"] for record in runs}
    agents = []
    runtime_snapshots: list[dict[str, Any]] = []
    runtime_views: list[dict[str, Any]] = []
    for record in team["agents"]:
        context = bus / "agents" / record["agent_id"] / "conversations" / "CURRENT_CONTEXT.md"
        meta = _frontmatter(context)
        handoffs = sorted((context.parents[1] / "handoffs").glob("*.md"))
        agents.append({"agent_id": record["agent_id"], "status": record.get("status"), "active_task": meta.get("active_task"),
                       "latest_checkpoint": meta.get("latest_checkpoint"), "latest_handoff": _relative(handoffs[-1], bus) if handoffs else None})
        snapshot, runtime_sources, runtime_view = _agent_runtime_snapshot(context.parents[1], bus, record["agent_id"], selected_runs)
        runtime_snapshots.append(snapshot)
        runtime_views.append(runtime_view)
        sources.extend(runtime_sources)
    runtime_snapshots.sort(key=lambda item: item["agent_id"])
    sources = sorted(set(sources))
    git = _git(root)
    checkpoint_dir = bus / "project-checkpoints"
    with exclusive_lock(bus / ".project-checkpoint.lock"):
        checkpoint_id, previous = _next(checkpoint_dir)
        now = _now()
        source_hashes = {_relative(path, bus): _hash(path) for path in sources}
        body = "\n".join(["# 项目检查点", "", "## Agents", "", *[f"- `{a['agent_id']}` status={a['status']}; task={a['active_task'] or 'none'}; checkpoint={a['latest_checkpoint'] or 'none'}; handoff={a['latest_handoff'] or 'none'}" for a in agents], "", "## Runs", "", *[f"- `{r['run_id']}` status={r['status']}; task_states={json.dumps(r['task_states'], ensure_ascii=False, sort_keys=True)}; summary=`{r['summary']}`" for r in runs], "", "## Decisions", "", "- `DECISIONS.md` (hash-bound below)", "", "## Git", "", f"- branch: `{git['branch']}`", f"- head: `{git['head']}`", "- status:", "```", git["status"] or "clean", "```", ""])
        content_hash = hashlib.sha256(body.strip().encode("utf-8")).hexdigest()
        meta = ["---", 'schema_version: "1.0"', "doc_type: project_checkpoint", f"project_id: {json.dumps(team['project_id'], ensure_ascii=False)}", f"checkpoint_id: {json.dumps(checkpoint_id)}", f"created_at: {json.dumps(now)}", f"previous_checkpoint: {json.dumps(previous) if previous else 'null'}", f"associated_runs: {json.dumps([r['run_id'] for r in runs], ensure_ascii=False)}", f"agent_runtime_snapshots: {json.dumps(runtime_snapshots, ensure_ascii=False, sort_keys=True)}", f"source_hashes: {json.dumps(source_hashes, ensure_ascii=False, sort_keys=True)}", f"git_snapshot_sha256: {json.dumps(hashlib.sha256(json.dumps(git, sort_keys=True).encode()).hexdigest())}", f"content_sha256: {json.dumps(content_hash)}", "---", ""]
        path = checkpoint_dir / f"{checkpoint_id}.md"
        result = {"checkpoint_id": checkpoint_id, "previous_checkpoint": previous, "path": _relative(path, bus), "source_hashes": source_hashes, "dry_run": dry_run}
        if dry_run:
            return result
        _write_immutable(path, "\n".join(meta) + body)
        try:
            _atomic(bus / "CURRENT_PROJECT_CONTEXT.md", _render_context(team["project_id"], now, _relative(path, bus), agents, runs, runtime_views))
        except OSError:
            path.unlink(missing_ok=True)
            raise
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project-root", required=True)
    parser.add_argument("--governance-root")
    parser.add_argument("--project-id")
    parser.add_argument("--run-id", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(create_project_checkpoint(
            args.project_root, args.run_id, dry_run=args.dry_run,
            governance_root=args.governance_root, project_id=args.project_id,
        ), ensure_ascii=False, sort_keys=True))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
