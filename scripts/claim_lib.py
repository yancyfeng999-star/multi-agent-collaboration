"""Shared read-only helpers for claimable task ownership."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from protocol_lib import scalar_map, sha256


def _parse_time(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def task_claims(run_dir: str | Path, task_id: str) -> list[dict[str, str]]:
    """Return valid claim records in acquisition order, newest first.

    Claim files are immutable.  An expired claim remains useful for historical
    event ownership; operational callers decide whether the newest record is
    still eligible for a fresh claim.
    """

    directory = Path(run_dir).expanduser().resolve() / "claims" / "tasks"
    records: list[dict[str, str]] = []
    if not directory.is_dir():
        return records
    for path in sorted(directory.glob("*.yaml")):
        try:
            values = scalar_map(path.read_text(encoding="utf-8"), source=str(path))
        except (OSError, ValueError):
            continue
        if values.get("task_id") != task_id or values.get("status") != "active":
            continue
        acquired = _parse_time(values.get("lease_acquired_at", ""))
        if acquired is None:
            continue
        records.append({**values, "_path": str(path)})
    records.sort(key=lambda value: _parse_time(value.get("lease_acquired_at", "")) or datetime.min, reverse=True)
    return records


def _released_claims(directory: Path) -> dict[str, datetime]:
    """Read immutable release records without mutating the original claim."""

    released: dict[str, datetime] = {}
    release_dir = directory / "releases"
    if not release_dir.is_dir():
        return released
    for path in sorted(release_dir.glob("*.yaml")):
        try:
            values = scalar_map(path.read_text(encoding="utf-8"), source=str(path))
            when = _parse_time(values.get("released_at", ""))
            claim_id = values.get("claim_id", "")
            original_path = directory / f"{claim_id}.yaml"
            original = scalar_map(original_path.read_text(encoding="utf-8"), source=str(original_path))
        except (OSError, ValueError):
            continue
        if (
            claim_id
            and when is not None
            and values.get("status") == "released"
            and original.get("status") == "active"
            and values.get("claim_sha256") == sha256(original_path)
            and values.get("released_by") == original.get("claimer_agent")
        ):
            released[claim_id] = min(when, released.get(claim_id, when))
    return released


def latest_task_claim(run_dir: str | Path, task_id: str, *, at: datetime | None = None) -> dict[str, str] | None:
    """Return the latest claim at or before an event timestamp."""

    directory = Path(run_dir).expanduser().resolve() / "claims" / "tasks"
    released = _released_claims(directory)
    for record in task_claims(run_dir, task_id):
        acquired = _parse_time(record.get("lease_acquired_at", ""))
        release_at = released.get(record.get("claim_id", ""))
        # Claim/release timestamps are intentionally second-granular in the
        # document protocol.  Treat equality as historical tie: an event
        # written in the same second may precede the release in the sequence
        # lock, so only a strictly earlier release invalidates ownership at
        # that event time.
        if at is not None and release_at is not None and release_at < at:
            continue
        if at is None or (acquired is not None and acquired <= at):
            return record
    return None


def active_task_claim(run_dir: str | Path, task_id: str, *, now: datetime | None = None) -> dict[str, str] | None:
    """Return the current non-expired claim for operational ownership."""

    now = now or datetime.now().astimezone()
    released = _released_claims(Path(run_dir).expanduser().resolve() / "claims" / "tasks")
    for record in task_claims(run_dir, task_id):
        expires = _parse_time(record.get("lease_expires_at", ""))
        if record.get("claim_id") in released:
            continue
        if expires is not None and expires > now:
            return record
    return None


def effective_owner(
    run_dir: str | Path,
    task: dict[str, Any],
    *,
    at: datetime | None = None,
    operational: bool = True,
) -> str:
    """Resolve a fixed owner or the latest/current claimant for a task."""

    owner = str(task.get("owner_agent", ""))
    if task.get("assignment_mode", "fixed") != "claimable" or owner != "pool":
        return owner
    claim = active_task_claim(run_dir, str(task.get("task_id", ""))) if operational and at is None else latest_task_claim(run_dir, str(task.get("task_id", "")), at=at)
    return str(claim.get("claimer_agent")) if claim else "pool"
