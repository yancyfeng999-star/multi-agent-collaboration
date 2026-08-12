from __future__ import annotations

import json
from pathlib import Path


def governance_root(temporary_root: str | Path) -> Path:
    return Path(temporary_root) / "governance"


def governance_project(
    temporary_root: str | Path,
    project_root: str | Path,
    *,
    project_id: str = "fixture",
    project_name: str = "Fixture",
) -> Path:
    """Create the smallest valid external binding for hand-built test stores."""
    project = Path(project_root).resolve()
    root = governance_root(temporary_root)
    bus = root / "projects" / project_id
    bus.mkdir(parents=True, exist_ok=True)
    binding = {
        "storage_schema": "1.0",
        "project_id": project_id,
        "project_name": project_name,
        "project_root": str(project),
        "project_key": project_id,
        "allowed_roots": [str(project)],
        "created_at": "2026-08-10T00:00:00+08:00",
    }
    (bus / "project-binding.yaml").write_text(
        json.dumps(binding, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return bus
