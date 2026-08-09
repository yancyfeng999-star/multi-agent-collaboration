from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def dispatch(run_dir: Path, operation: dict[str, Any], *, dry_run: bool = False) -> dict[str, Any]:
    """Materialize a self-contained, immutable invocation package in the owner's inbox."""
    owner = operation["agent_id"]
    target = run_dir / "inbox" / owner / f'{operation["operation_id"]}.json'
    package = {
        "protocol_version": 3,
        "kind": "document_invocation",
        "operation_id": operation["operation_id"],
        "run_id": operation["run_id"],
        "task_id": operation["task_id"],
        "agent_id": owner,
        "workspace": operation["workspace"],
        "task_path": operation["task_path"],
        "task_sha256": operation["task_sha256"],
        "claim_id": operation.get("claim_id"),
        "owned_paths": operation["owned_paths"],
        "forbidden_paths": operation["forbidden_paths"],
        "instruction": (
            "Verify task_id and task_sha256, work only in workspace and owned_paths, "
            "write an immutable ACK and result to your run-local outbox; do not edit global events/state."
        ),
    }
    if dry_run:
        return {"adapter": "document", "status": "planned", "package_path": str(target)}
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if target.exists():
        if target.read_text(encoding="utf-8") != encoded:
            raise ValueError(f"immutable document invocation collision: {target}")
    else:
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.write_text(encoded, encoding="utf-8")
        os.replace(temporary, target)
    return {"adapter": "document", "status": "message_sent", "delivery_status": "message_sent", "package_path": str(target)}
