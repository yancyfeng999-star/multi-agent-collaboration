#!/usr/bin/env python3
"""Manage a small FIFO resource request queue without granting partial bundles."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from project_memory_lib import exclusive_lock
from claim_lib import effective_owner
from preflight_lib import _load_context
from protocol_lib import atomic_write, quote, scalar_map


def _queue_dir(run_dir: Path, *, create: bool = True) -> Path:
    path = run_dir / "locks" / "queue"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def request(run_dir_value: str | Path, task_id: str, agent_id: str, resources: list[str], step_id: str, queue_key: str | None = None, *, dry_run: bool = False) -> dict[str, object]:
    if not resources:
        raise ValueError("resource request must contain at least one resource")
    run_dir = Path(run_dir_value).expanduser().resolve()
    context = _load_context(run_dir)
    if task_id not in context["tasks"]:
        raise ValueError(f"task does not exist: {task_id}")
    if agent_id not in context["agents"]:
        raise ValueError(f"agent is not registered: {agent_id}")
    task = context["tasks"][task_id][1]
    if effective_owner(run_dir, task) not in {agent_id, "pool"} and agent_id != "coordinator":
        raise ValueError("resource request agent must be the effective task owner or Coordinator")
    if not queue_key:
        # Make the queue key optional for callers that already provide the
        # logical resources.  Resolve it from the declared resource bundle so
        # the request still joins the correct FIFO without a second lookup by
        # the worker.
        try:
            declared_steps = json.loads(task.get("resource_steps", "[]"))
        except json.JSONDecodeError as exc:
            raise ValueError("task resource_steps is invalid") from exc
        requested = set(resources)
        for declared in declared_steps if isinstance(declared_steps, list) else []:
            if not isinstance(declared, dict) or not declared.get("queue_key"):
                continue
            declared_resources = declared.get("resources", [])
            if isinstance(declared_resources, list) and requested.issubset({str(item) for item in declared_resources}):
                queue_key = str(declared["queue_key"])
                break
    manifest = context["manifest"]
    queue_dir = _queue_dir(run_dir, create=not dry_run)
    request_id = "REQ-" + hashlib.sha256(f"{task_id}\0{agent_id}\0{step_id}\0{queue_key or ''}\0{'|'.join(sorted(resources))}".encode()).hexdigest()[:20]
    path = queue_dir / f"{request_id}.yaml"
    created = datetime.now().astimezone().isoformat(timespec="microseconds")
    content = "\n".join(
        (
            "protocol_version: 3",
            'kind: "resource_request"',
            f"request_id: {quote(request_id)}",
            f"run_id: {quote(manifest.get('run_id', run_dir.name))}",
            f"task_id: {quote(task_id)}",
            f"agent_id: {quote(agent_id)}",
            f"step_id: {quote(step_id)}",
            f"queue_key: {quote(queue_key) if queue_key else 'null'}",
            f"resources: {json.dumps(sorted(set(resources)), ensure_ascii=False)}",
            f"created_at: {quote(created)}",
            'status: "queued"',
            "",
        )
    )
    if not dry_run:
        with exclusive_lock(run_dir / "locks" / ".resource-queue.lock"):
            if not path.exists():
                atomic_write(path, content)
    entries: list[tuple[datetime, str]] = []
    grant_dir = queue_dir / "grants"
    granted: set[str] = set()
    for grant_path in sorted(grant_dir.glob("*.yaml")) if grant_dir.is_dir() else []:
        try:
            grant = scalar_map(grant_path.read_text(encoding="utf-8"), source=str(grant_path))
        except (OSError, ValueError):
            continue
        if grant.get("status") == "granted" and grant.get("request_id"):
            granted.add(grant["request_id"])
    if request_id in granted:
        return {
            "ready": True,
            "request_id": request_id,
            "request_path": str(path),
            "queue_position": 0,
            "resources": sorted(set(resources)),
            "status": "granted",
            "dry_run": dry_run,
        }
    if queue_dir.exists():
        for item in queue_dir.glob("*.yaml"):
            try:
                values = scalar_map(item.read_text(encoding="utf-8"), source=str(item))
                created_at = datetime.fromisoformat(values["created_at"].replace("Z", "+00:00"))
            except (OSError, KeyError, ValueError):
                continue
            if values.get("status") == "queued" and values.get("request_id") not in granted:
                entries.append((created_at, item.name))
    if path.name not in {name for _, name in entries}:
        entries.append((datetime.fromisoformat(created.replace("Z", "+00:00")), path.name))
    entries.sort(key=lambda item: (item[0], item[1]))
    position = [name for _, name in entries].index(path.name) + 1
    return {"ready": True, "request_id": request_id, "request_path": str(path), "queue_position": position, "resources": sorted(set(resources)), "dry_run": dry_run}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    ask = subparsers.add_parser("request")
    ask.add_argument("--run-dir", required=True); ask.add_argument("--task-id", required=True); ask.add_argument("--agent-id", required=True)
    ask.add_argument("--step-id", required=True); ask.add_argument("--queue-key"); ask.add_argument("--resource", action="append", required=True); ask.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = request(args.run_dir, args.task_id, args.agent_id, args.resource, args.step_id, args.queue_key, dry_run=args.dry_run)
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
