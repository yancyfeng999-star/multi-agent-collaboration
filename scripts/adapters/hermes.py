from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Sequence


def dispatch(operation: dict[str, Any], command: Sequence[str] | None) -> dict[str, Any]:
    """Invoke only an explicitly configured Hermes API/CLI bridge."""
    if not command:
        return {"adapter": "hermes", "status": "unsupported", "reason": "no explicit Hermes CLI/API command configured"}
    result = subprocess.run([*command, operation["task_path"]], cwd=operation["workspace"], capture_output=True, text=True)
    if result.returncode:
        return {"adapter": "hermes", "status": "failed", "returncode": result.returncode, "stderr": result.stderr.strip()}
    return {"adapter": "hermes", "status": "message_sent", "stdout": result.stdout.strip()}
