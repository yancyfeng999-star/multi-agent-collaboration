#!/usr/bin/env python3
"""Run one bounded protocol-v3 coordination tick (never a daemon by default)."""
from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from protocol_lib import event_records, frontmatter, json_string_list, parse_agent_profiles, paths_overlap, replay_task_states, scalar_map
from preflight_lib import run_preflight
from wake_agent import wake_agent

ACTIVE = {"dispatched", "acknowledged", "running", "handoff_ready", "reviewing", "qa_running", "release_ready"}
TERMINAL = {"completed", "failed", "cancelled", "superseded", "expired", "dead_letter"}


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _active_locks(run_dir: Path, now: datetime) -> list[dict[str, str]]:
    locks = []
    for path in sorted((run_dir / "locks").glob("*.yaml")):
        values = scalar_map(path.read_text(encoding="utf-8"), source=str(path))
        try:
            if _time(values["lease_expires_at"]) > now:
                locks.append(values)
        except (KeyError, ValueError):
            locks.append(values)  # malformed/undated locks fail closed
    return locks


def _attempt_count(run_dir: Path, task_id: str, records: list[tuple[Path, dict[str, str]]]) -> int:
    attempts = set()
    for _, event in records:
        if event.get("task_id") == task_id and event.get("event") == "ACK":
            attempts.add(event.get("idempotency_key", "ACK"))
    for path in (run_dir / "outbox").glob(f"*/{task_id}-ack-*.yaml"):
        values = scalar_map(path.read_text(encoding="utf-8"), source=str(path))
        attempts.add(values.get("attempt_id", path.name))
    return len(attempts)


def _timeouts(run_dir: Path, manifest: dict[str, str], records: list[tuple[Path, dict[str, str]]], states: dict[str, str], now: datetime) -> list[dict[str, Any]]:
    advice = []
    by_task: dict[str, list[dict[str, str]]] = {}
    for _, event in records:
        by_task.setdefault(event.get("task_id", ""), []).append(event)
    for task_id, state in states.items():
        events = by_task.get(task_id, [])
        attempts = _attempt_count(run_dir, task_id, records)
        reason = None
        if state == "dispatched":
            dispatches = [item for item in events if item.get("event") == "TASK_DISPATCHED"]
            if dispatches and (now - _time(dispatches[-1]["created_at"])).total_seconds() > int(manifest["ack_timeout_seconds"]):
                reason = "ack_timeout"
                attempts = max(1, attempts)
        elif state == "running":
            leases = [item for item in events if item.get("event") in {"LEASE_ACQUIRED", "LEASE_RENEWED"}]
            if leases:
                payload = Path(leases[-1].get("payload_path", ""))
                if payload.is_file():
                    values = scalar_map(payload.read_text(encoding="utf-8"), source=str(payload))
                    if _time(values["lease_expires_at"]) <= now:
                        reason = "lease_timeout"
                        attempts = max(attempts, int(values.get("attempt_id", "ATTEMPT-001").rsplit("-", 1)[-1]))
        if reason:
            recommendation = "dead_letter" if attempts >= int(manifest["max_attempts"]) else "retry"
            advice.append({
                "task_id": task_id,
                "reason": reason,
                "attempts": attempts,
                "recommendation": recommendation,
                "blocked_by": reason,
                "next_action": "inspect_side_effects_then_recover_timeout",
                "safe_cli": "write-dead-letter requires a real failure event; coordinator does not fabricate one",
            })
    return advice


def _emit(run_dir: Path, task_id: str, event: str, target: str, task_path: Path) -> None:
    command = ["python3", str(Path(__file__).with_name("emit_event.py")), "--run-dir", str(run_dir), "--task-id", task_id, "--event", event, "--from-agent", "coordinator", "--to-agent", target, "--summary", f"coordinator {event.lower()}", "--payload-file", str(task_path), "--idempotency-key", f"coordinator:{task_id}:{event}:v1"]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())


def tick(run_dir: str | Path, *, dry_run: bool = False, emit_events: bool = True, now: datetime | None = None) -> dict[str, Any]:
    if not emit_events and not dry_run:
        raise ValueError("--no-emit-events is preview-only; combine it with --dry-run")
    run_dir = Path(run_dir).expanduser().resolve()
    now = now or datetime.now().astimezone()
    manifest = scalar_map((run_dir / "manifest.yaml").read_text(encoding="utf-8"), source="manifest")
    if manifest.get("protocol_version") != "3" or manifest.get("status") in {"archived", "completed", "cancelled", "superseded"}:
        return {"run_id": manifest.get("run_id"), "bounded": True, "ready_set": [], "dispatches": [], "timeouts": [], "reason": "run_not_active"}
    agents = parse_agent_profiles((run_dir / "agents.yaml").read_text(encoding="utf-8"), source="agents")
    records = event_records(run_dir / "events")
    states, errors = replay_task_states(records, manifest.get("governance", ""))
    if errors:
        raise ValueError("invalid event history: " + "; ".join(errors))
    preflight_report: dict[str, Any] | None = None
    # Non-central runs must pass the complete read-only preflight before any
    # dispatch.  Do not short-circuit on a missing scope file: the preflight
    # itself must report that gap and keep the coordinator from waking agents.
    if manifest.get("preflight_required") == "true" and manifest.get("dispatch_policy", "central") != "central":
        preflight_report = run_preflight(run_dir)
        if not preflight_report.get("ready"):
            return {
                "run_id": manifest["run_id"],
                "bounded": True,
                "dry_run": dry_run,
                "ready_set": [],
                "dispatches": [],
                "blocked_conflicts": [],
                "timeouts": [],
                "preflight": preflight_report,
                "reason": "preflight_blocked",
            }
    tasks: dict[str, tuple[Path, dict[str, str]]] = {}
    for path in sorted((run_dir / "tasks").glob("*.md")):
        values = frontmatter(path); tasks[values["task_id"]] = (path, values)
    active_count = sum(state in ACTIVE for state in states.values())
    capacity = max(0, int(manifest["max_parallel"]) - active_count)
    candidates = []
    for task_id in json_string_list(manifest.get("tasks", "[]"), field="tasks", source="manifest"):
        if task_id not in tasks or states.get(task_id) in ACTIVE | TERMINAL | {"waiting_user_approval", "blocked"}:
            continue
        _, task = tasks[task_id]
        deps = json_string_list(task.get("dependencies", "[]"), field="dependencies", source=task_id)
        if all(states.get(dep) == "completed" for dep in deps):
            candidates.append(task_id)
    selected: list[str] = []
    blocked: list[dict[str, str]] = []
    project = scalar_map((run_dir.parent.parent / "project.yaml").read_text(encoding="utf-8"), source="project")
    root = Path(project["project_root"]).resolve()
    locks = _active_locks(run_dir, now)
    for task_id in candidates:
        if len(selected) >= capacity:
            break
        _, task = tasks[task_id]
        owned = json_string_list(task.get("owned_paths", "[]"), field="owned_paths", source=task_id)
        conflict = next((other for other in selected if any(paths_overlap(a, b, root) for a in owned for b in json_string_list(tasks[other][1].get("owned_paths", "[]"), field="owned_paths", source=other))), None)
        lock = next((item for item in locks if item.get("owner_task") != task_id and any(paths_overlap(path, item.get("resource", "logical:invalid"), root) for path in owned) and not item.get("resource", "").startswith("logical:")), None)
        if conflict:
            blocked.append({"task_id": task_id, "reason": f"owned_path_conflict:{conflict}"})
        elif lock:
            blocked.append({"task_id": task_id, "reason": f"lock_conflict:{lock.get('lock_id', 'unknown')}"})
        else:
            selected.append(task_id)
    dispatches = []
    for task_id in selected:
        task_path, task = tasks[task_id]; owner = task["owner_agent"]
        if task.get("assignment_mode", "fixed") == "claimable":
            if emit_events and task_id not in states and not dry_run:
                _emit(run_dir, task_id, "TASK_READY", "coordinator", task_path)
            dispatches.append({
                "task_id": task_id,
                "agent_id": "pool",
                "status": "awaiting_claim",
                "next_action": "eligible_agent_claim_task",
            })
            continue
        result = wake_agent(run_dir, task_id, owner, dry_run=dry_run)
        if emit_events and task_id not in states and not dry_run:
            _emit(run_dir, task_id, "TASK_READY", owner, task_path)
            _emit(run_dir, task_id, "TASK_DISPATCHED", owner, task_path)
        dispatches.append({"task_id": task_id, "agent_id": owner, **result})
    return {"run_id": manifest["run_id"], "bounded": True, "dry_run": dry_run, "ready_set": candidates, "dispatches": dispatches, "blocked_conflicts": blocked, "timeouts": _timeouts(run_dir, manifest, records, states, now), "preflight": preflight_report}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--run-dir", required=True)
    parser.add_argument("--once", action="store_true", help="Run exactly one tick (also the default)")
    parser.add_argument("--dry-run", action="store_true"); parser.add_argument("--no-emit-events", action="store_true")
    args = parser.parse_args(); print(json.dumps(tick(args.run_dir, dry_run=args.dry_run, emit_events=not args.no_emit_events), ensure_ascii=False, indent=2)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
