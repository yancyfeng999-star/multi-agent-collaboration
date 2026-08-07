from __future__ import annotations

import subprocess
from typing import Any, Sequence


def dispatch(operation: dict[str, Any], command: Sequence[str] | None) -> dict[str, Any]:
    """Invoke only an explicitly configured Codex API/CLI bridge."""
    if not command:
        return {"adapter": "codex", "status": "unsupported", "reason": "no explicit Codex CLI/API command configured"}
    result = subprocess.run([*command, operation["task_path"]], cwd=operation["workspace"], capture_output=True, text=True)
    if result.returncode:
        return {"adapter": "codex", "status": "failed", "returncode": result.returncode, "stderr": result.stderr.strip()}
    return {"adapter": "codex", "status": "woken", "stdout": result.stdout.strip()}
