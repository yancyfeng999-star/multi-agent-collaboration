from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from pathlib import Path

from tests.governance_test_support import governance_project

SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / "scripts"))

from coordinator import tick  # noqa: E402
from wake_agent import wake_agent  # noqa: E402


class CoordinatorRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        self.bus = governance_project(self.temp.name, self.root)
        self.run_dir = self.bus / "runs" / "RUN-1"
        for name in ("tasks", "events", "locks", "operations", "inbox/a", "inbox/b", "outbox/a", "outbox/b"):
            (self.run_dir / name).mkdir(parents=True, exist_ok=True)
        (self.bus / "project.yaml").write_text(
            f'protocol_version: 3\nproject_root: {json.dumps(str(self.root))}\nallowed_roots: {json.dumps([str(self.root)])}\n',
            encoding="utf-8",
        )
        (self.run_dir / "manifest.yaml").write_text(
            '\n'.join((
                'protocol_version: 3', 'run_id: "RUN-1"', 'status: "active"',
                'governance: "light"', 'transport: "document_bus"', 'max_parallel: 2',
                'ack_timeout_seconds: 30', 'lease_seconds: 60', 'max_attempts: 2',
                'tasks: ["T1", "T2", "T3"]', '',
            )), encoding="utf-8"
        )
        self._agents("document", "document")
        self._task("T1", "a", [], [str(self.root / "one")])
        self._task("T2", "b", [], [str(self.root / "two")])
        self._task("T3", "a", ["T1"], [str(self.root / "three")])

    def _agents(self, runtime_a: str, runtime_b: str) -> None:
        def block(agent: str, runtime: str) -> str:
            return '\n'.join((
                f'  - agent_id: "{agent}"', f'    runtime: "{runtime}"', f'    role: "{agent}"',
                '    status: "ready"', '    parent_agent_id: null', '    delegation_depth: 0',
                f'    readable_paths: [{json.dumps(str(self.root))}]',
                f'    writable_paths: [{json.dumps(str(self.root))}]', '    forbidden_paths: []',
                '    thread_id: null', f'    inbox: "inbox/{agent}"', f'    outbox: "outbox/{agent}"',
                '    current_task: null', '    handoff_to: "coordinator"',
            ))
        (self.run_dir / "agents.yaml").write_text(
            'protocol_version: 3\nrun_id: "RUN-1"\nagents:\n' + block("a", runtime_a) + '\n' + block("b", runtime_b) + '\n',
            encoding="utf-8",
        )

    def _task(self, task_id: str, owner: str, deps: list[str], owned: list[str]) -> None:
        (self.run_dir / "tasks" / f"{task_id}.md").write_text(
            '\n'.join((
                '---', 'protocol_version: 3', 'run_id: "RUN-1"', f'task_id: "{task_id}"',
                f'owner_agent: "{owner}"', f'dependencies: {json.dumps(deps)}',
                f'owned_paths: {json.dumps(owned)}', 'forbidden_paths: []',
                f'idempotency_key: "RUN-1:{task_id}:v1"', '---', '', f'# {task_id}', '',
            )), encoding="utf-8",
        )

    def _event(self, sequence: int, event: str, task: str, when: datetime, payload: Path | None = None) -> None:
        fields = [
            'protocol_version: 3', f'sequence: {sequence}', f'event_id: "E{sequence}"',
            f'run_id: "RUN-1"', f'task_id: "{task}"', f'event: "{event}"',
            'from_agent: "coordinator"', 'to_agent: "a"', f'created_at: "{when.isoformat()}"',
            f'payload_path: {json.dumps(str(payload)) if payload else "null"}',
            'payload_sha256: null', f'idempotency_key: "E{sequence}"',
        ]
        (self.run_dir / "events" / f"{sequence:06d}-{event.lower()}.yaml").write_text('\n'.join(fields) + '\n', encoding="utf-8")

    def test_ready_wave_obeys_dependencies_and_max_parallel(self) -> None:
        report = tick(self.run_dir, dry_run=True, emit_events=False)
        self.assertEqual([item["task_id"] for item in report["dispatches"]], ["T1", "T2"])
        self.assertNotIn("T3", report["ready_set"])
        self.assertEqual(len(list((self.run_dir / "inbox").glob("*/*.json"))), 0)

    def test_conflicting_owned_paths_are_not_dispatched(self) -> None:
        self._task("T2", "b", [], [str(self.root / "one" / "child")])
        report = tick(self.run_dir, dry_run=True, emit_events=False)
        self.assertEqual([item["task_id"] for item in report["dispatches"]], ["T1"])
        self.assertTrue(any(item["task_id"] == "T2" and "owned_path_conflict" in item["reason"] for item in report["blocked_conflicts"]))

    def test_active_path_lock_blocks_dispatch(self) -> None:
        (self.run_dir / "locks" / "held.yaml").write_text(
            '\n'.join((
                'protocol_version: 3', 'kind: "lock"', 'run_id: "RUN-1"', 'lock_id: "held"',
                f'resource: {json.dumps(str(self.root / "two"))}', 'owner_task: "OTHER"',
                'owner_agent: "other"', f'acquired_at: "{datetime.now(timezone.utc).isoformat()}"',
                f'lease_expires_at: "{(datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()}"', '',
            )), encoding="utf-8"
        )
        report = tick(self.run_dir, dry_run=True, emit_events=False)
        self.assertEqual([item["task_id"] for item in report["dispatches"]], ["T1"])
        self.assertTrue(any(item["task_id"] == "T2" and "lock_conflict" in item["reason"] for item in report["blocked_conflicts"]))

    def test_unsupported_codex_falls_back_to_real_document_package(self) -> None:
        self._agents("codex_thread", "document")
        result = wake_agent(self.run_dir, "T1", "a", requested_adapter="codex")
        self.assertEqual(result["status"], "fallback_document")
        self.assertEqual(result["unsupported_adapter"], "codex")
        self.assertTrue(Path(result["package_path"]).is_file())
        self.assertNotEqual(result["status"], "woken")

    def test_restart_is_idempotent(self) -> None:
        with patch("coordinator._emit"):
            first = tick(self.run_dir, dry_run=False, emit_events=True)
        operation_count = len(list((self.run_dir / "operations").glob("*.json")))
        package_count = len(list((self.run_dir / "inbox").glob("*/*.json")))
        states = [(1, "TASK_READY"), (2, "TASK_DISPATCHED"), (3, "TASK_READY"), (4, "TASK_DISPATCHED")]
        for sequence, event in states[:2]:
            self._event(sequence, event, "T1", datetime.now(timezone.utc))
        for sequence, event in states[2:]:
            self._event(sequence, event, "T2", datetime.now(timezone.utc))
        second = tick(self.run_dir, dry_run=False, emit_events=True)
        self.assertEqual(second["dispatches"], [])
        self.assertEqual(operation_count, 2)
        self.assertEqual(package_count, 2)

    def test_no_emit_events_cannot_perform_real_delivery(self) -> None:
        with self.assertRaisesRegex(ValueError, "preview-only"):
            tick(self.run_dir, dry_run=False, emit_events=False)

    def test_wake_failure_never_declares_task_dispatched(self) -> None:
        emitted: list[tuple[str, str]] = []

        def record_emit(run_dir, task_id, event, owner, task_path):
            emitted.append((task_id, event))

        with patch("coordinator._emit", side_effect=record_emit), patch(
            "coordinator.wake_agent", side_effect=RuntimeError("inbox write failed")
        ):
            with self.assertRaisesRegex(RuntimeError, "inbox write failed"):
                tick(self.run_dir, dry_run=False, emit_events=True)

        self.assertEqual(emitted, [])

    def test_ack_and_lease_timeouts_produce_retry_and_dead_letter_advice(self) -> None:
        old = datetime.now(timezone.utc) - timedelta(minutes=10)
        self._event(1, "TASK_READY", "T1", old)
        self._event(2, "TASK_DISPATCHED", "T1", old)
        lease = self.run_dir / "outbox" / "b" / "T2-lease-ATTEMPT-002-L2.yaml"
        lease.write_text(
            '\n'.join((
                'protocol_version: 3', 'kind: "lease"', 'run_id: "RUN-1"', 'task_id: "T2"',
                'agent_id: "b"', 'attempt_id: "ATTEMPT-002"', 'lease_id: "L2"',
                f'acquired_at: "{old.isoformat()}"', f'lease_expires_at: "{old.isoformat()}"', '',
            )), encoding="utf-8"
        )
        self._event(3, "TASK_READY", "T2", old)
        self._event(4, "TASK_DISPATCHED", "T2", old)
        self._event(5, "ACK", "T2", old)
        self._event(6, "LEASE_ACQUIRED", "T2", old, lease)
        report = tick(self.run_dir, dry_run=True, emit_events=False)
        advice = {item["task_id"]: item["recommendation"] for item in report["timeouts"]}
        self.assertEqual(advice["T1"], "retry")
        self.assertEqual(advice["T2"], "dead_letter")

    def test_external_wake_rejects_session_identity_or_workspace_mismatch(self) -> None:
        self._agents("codex_thread", "document")
        mapping = self.root / "SESSION_MAP.json"
        mapping.write_text(json.dumps({"agent_id": "wrong", "active": {"platform": "codex", "session_id": "S", "workspace": str(self.root)}}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "identity"):
            wake_agent(self.run_dir, "T1", "a", requested_adapter="codex", session_map=mapping, codex_command=["/bin/true"])


if __name__ == "__main__":
    unittest.main()
