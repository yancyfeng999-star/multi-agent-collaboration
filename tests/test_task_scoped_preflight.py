from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from coordinator import tick  # noqa: E402
from protocol_lib import sha256  # noqa: E402


class TaskScopedPreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.project = root / "project"
        self.project.mkdir()
        self.run_dir = root / "governance" / "runs" / "RUN-1"
        for name in ("tasks", "events", "locks", "operations", "inbox/b", "outbox/b"):
            (self.run_dir / name).mkdir(parents=True, exist_ok=True)
        (self.run_dir.parent.parent / "project.yaml").write_text(
            "\n".join(
                (
                    "protocol_version: 3",
                    f"project_root: {json.dumps(str(self.project))}",
                    f"allowed_roots: {json.dumps([str(self.project)])}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        scope = self.run_dir / "scope.yaml"
        scope.write_text(
            "\n".join(
                (
                    "scope_id: SCOPE-1",
                    f"requested_paths: {json.dumps([str(self.project)])}",
                    "forbidden_paths: []",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.run_dir / "manifest.yaml").write_text(
            "\n".join(
                (
                    'protocol_version: 3',
                    'run_id: "RUN-1"',
                    'status: "active"',
                    'governance: "light"',
                    'execution_profile: "emergency"',
                    'dispatch_policy: "hybrid"',
                    'preflight_required: true',
                    f'scope_freeze_ref: {json.dumps(str(scope))}',
                    f'scope_freeze_ref_sha256: {json.dumps(sha256(scope))}',
                    'transport: "document_bus"',
                    'max_parallel: 2',
                    'ack_timeout_seconds: 30',
                    'lease_seconds: 60',
                    'max_attempts: 2',
                    'versioning_mode: "not_applicable"',
                    'tasks: ["TASK-BLOCKED", "TASK-READY"]',
                    '',
                )
            ),
            encoding="utf-8",
        )
        (self.run_dir / "agents.yaml").write_text(
            "\n".join(
                (
                    'protocol_version: 3',
                    'run_id: "RUN-1"',
                    'agents:',
                    '  - agent_id: "b"',
                    '    runtime: "document"',
                    '    role: "owner"',
                    '    status: "ready"',
                    '    parent_agent_id: null',
                    '    delegation_depth: 0',
                    f'    readable_paths: [{json.dumps(str(self.project))}]',
                    f'    writable_paths: [{json.dumps(str(self.project))}]',
                    '    forbidden_paths: []',
                    '    thread_id: null',
                    '    inbox: "inbox/b"',
                    '    outbox: "outbox/b"',
                    '    current_task: null',
                    '    handoff_to: "coordinator"',
                    '',
                )
            ),
            encoding="utf-8",
        )
        self._task("TASK-BLOCKED", "missing", "blocked")
        self._task("TASK-READY", "b", "ready")

    def _task(self, task_id: str, owner: str, directory: str) -> None:
        path = self.project / directory
        path.mkdir()
        (self.run_dir / "tasks" / f"{task_id}.md").write_text(
            "\n".join(
                (
                    "---",
                    'protocol_version: 3',
                    'run_id: "RUN-1"',
                    f'task_id: "{task_id}"',
                    f'owner_agent: "{owner}"',
                    'assignment_mode: "fixed"',
                    f'owned_paths: [{json.dumps(str(path))}]',
                    'forbidden_paths: []',
                    'dependencies: []',
                    'reviewer_agent: null',
                    'qa_agent: null',
                    '---',
                    f"# {task_id}",
                    "",
                )
            ),
            encoding="utf-8",
        )

    def test_unready_task_does_not_block_independent_ready_task(self) -> None:
        report = tick(self.run_dir, dry_run=True, emit_events=False)

        self.assertEqual([item["task_id"] for item in report["dispatches"]], ["TASK-READY"])
        self.assertTrue(any(item["task_id"] == "TASK-BLOCKED" for item in report["blocked_tasks"]))
        self.assertNotEqual(report.get("reason"), "preflight_blocked")


if __name__ == "__main__":
    unittest.main()
