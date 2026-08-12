#!/usr/bin/env python3
"""Record a bounded timeout block without fabricating an owner failure."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from project_memory_lib import exclusive_lock
from claim_lib import effective_owner
from preflight_lib import _load_context
from protocol_lib import atomic_write, event_records, quote, scalar_map, sha256


def _expired_lease(run_dir: Path, task_id: str, now: datetime) -> str | None:
    for event_path, values in reversed(event_records(run_dir / "events")):
        if values.get("task_id") != task_id or values.get("event") not in {"LEASE_ACQUIRED", "LEASE_RENEWED"}:
            continue
        payload_value = values.get("payload_path", "null")
        if payload_value == "null":
            continue
        payload = Path(payload_value).expanduser()
        if not payload.is_absolute():
            payload = run_dir / payload
        if not payload.is_file():
            continue
        lease = scalar_map(payload.read_text(encoding="utf-8"), source=str(payload))
        expiry = lease.get("lease_expires_at", "")
        try:
            if datetime.fromisoformat(expiry.replace("Z", "+00:00")) <= now:
                return expiry
        except ValueError:
            return expiry
        return None
    return None


def recover_timeout(run_dir_value: str | Path, task_id: str, action: str, side_effect_state: str, *, dry_run: bool = False) -> dict[str, object]:
    context = _load_context(run_dir_value)
    run_dir = context["run_dir"]
    state = context["states"].get(task_id)
    if state not in {"dispatched", "acknowledged", "running"}:
        raise ValueError(f"task is not timeout-recoverable: {task_id}:{state or 'none'}")
    now = datetime.now().astimezone()
    expiry = _expired_lease(run_dir, task_id, now) if state in {"acknowledged", "running"} else None
    if state == "running" and expiry is None:
        raise ValueError("running task has no expired lease")
    if state == "acknowledged" and expiry is None:
        raise ValueError("acknowledged task has no expired lease")
    if state == "dispatched":
        dispatches = [
            values
            for _, values in context["records"]
            if values.get("task_id") == task_id and values.get("event") == "TASK_DISPATCHED"
        ]
        if not dispatches:
            raise ValueError("dispatched task has no dispatch event")
        try:
            dispatched_at = datetime.fromisoformat(dispatches[-1]["created_at"].replace("Z", "+00:00"))
        except (KeyError, ValueError) as exc:
            raise ValueError("dispatch event has invalid timestamp") from exc
        if (now - dispatched_at).total_seconds() <= int(context["manifest"].get("ack_timeout_seconds", "0")):
            raise ValueError("ACK timeout has not expired")
    if action == "retry" and side_effect_state != "none":
        raise ValueError("retry requires side-effect-state none")
    if action == "retry":
        return {
            "ready": False,
            "task_id": task_id,
            "action": "retry",
            "blocked_by": "owner_failure_evidence_required",
            "next_action": "write_real_failure_then_schedule_retry",
            "reason": "Coordinator will not fabricate TASK_FAILED or retry evidence",
        }
    owner = effective_owner(run_dir, context["tasks"][task_id][1])
    if owner == "pool":
        raise ValueError("claimable task must have a claimant before timeout recovery")
    evidence_path = run_dir / "evidence" / f"TIMEOUT-{task_id}-{now.strftime('%Y%m%d%H%M%S')}.yaml"
    content = "\n".join(
        (
            "protocol_version: 3",
            'kind: "timeout"',
            f"run_id: {quote(context['manifest']['run_id'])}",
            f"task_id: {quote(task_id)}",
            f"evidence_id: {quote(evidence_path.stem)}",
            'status: "blocked"',
            f"observed_state: {quote(state)}",
            f"lease_expires_at: {quote(expiry or 'null')}",
            f"side_effect_state: {quote(side_effect_state)}",
            f"observed_at: {quote(now.isoformat(timespec='seconds'))}",
            'summary: "bounded timeout recovery block; no owner failure was fabricated"',
            "",
        )
    )
    if dry_run:
        return {"ready": True, "dry_run": True, "task_id": task_id, "action": action, "evidence_path": str(evidence_path), "owner_agent": owner}
    with exclusive_lock(run_dir / "locks" / ".timeout-recovery.lock"):
        if evidence_path.exists():
            raise ValueError(f"timeout evidence already exists: {evidence_path}")
        atomic_write(evidence_path, content)
        event_command = [
            sys.executable,
            str(Path(__file__).with_name("emit_event.py")),
            "--run-dir", str(run_dir),
            "--task-id", task_id,
            "--event", "BLOCKED",
            "--from-agent", "coordinator",
            "--to-agent", owner,
            "--summary", "bounded timeout recovery block",
            "--payload-file", str(evidence_path),
            "--idempotency-key", f"{context['manifest']['run_id']}:{task_id}:timeout-block:{evidence_path.stem}",
        ]
        emitted = subprocess.run(event_command, capture_output=True, text=True)
        if emitted.returncode:
            evidence_path.unlink(missing_ok=True)
            raise RuntimeError(emitted.stderr.strip() or emitted.stdout.strip())
    return {"ready": True, "dry_run": False, "task_id": task_id, "action": "block", "evidence_path": str(evidence_path), "evidence_sha256": sha256(evidence_path), "owner_agent": owner}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--task-id", required=True)
    parser.add_argument("--action", choices=("block", "retry"), default="block")
    parser.add_argument("--side-effect-state", choices=("none", "unknown", "confirmed"), required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = recover_timeout(
        args.run_dir,
        args.task_id,
        args.action,
        args.side_effect_state,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("ready") else 2


if __name__ == "__main__":
    raise SystemExit(main())
