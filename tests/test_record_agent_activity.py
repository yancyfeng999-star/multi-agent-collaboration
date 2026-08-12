from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.governance_test_support import governance_project, governance_root
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

from test_agent_activity import canonical_record_hash, find_secret_field, schema_errors  # noqa: E402


class RecordAgentActivityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.governance = governance_root(self.temp.name)
        self.bus = governance_project(self.temp.name, self.project)
        self.agent = self.bus / "agents" / "A02-worker"
        (self.agent / "runtime" / "profiles").mkdir(parents=True)
        self.binding = self.agent / "runtime" / "profiles" / "RP-000001.json"
        self.binding.write_text('{"runtime_profile_id":"RP-000001"}\n', encoding="utf-8")
        self.schema = json.loads((ROOT / "assets/schemas/agent-activity.schema.json").read_text(encoding="utf-8"))

    def payload(self, *, summary: str = "started", session_id: str = "SESSION-001") -> dict:
        import hashlib

        return {
            "schema_version": 1,
            "record_kind": "attempt_started",
            "recorded_at": "2026-08-06T16:00:00.000Z",
            "run_id": "RUN-123",
            "task_id": "TASK-015",
            "attempt_id": "ATTEMPT-002",
            "agent_id": "A02-worker",
            "session_id": session_id,
            "parent_agent_id": "coordinator",
            "runtime_profile": {
                "runtime": "codex_subagent", "provider": "custom-rootflowgpt", "model": "gpt-5.6-sol",
                "profile_name": "default", "node_id": "local", "host_fingerprint": None,
                "native_binding_ref": "runtime/profiles/RP-000001.json",
                "native_binding_sha256": hashlib.sha256(self.binding.read_bytes()).hexdigest(),
            },
            "status": {"attempt_status": "running", "task_status_observed": "running", "outcome": None, "reason_code": None, "summary": summary},
            "tool_summary": None, "verification": None, "artifacts": [], "evidence_refs": [],
            "usage": {
                "input_tokens": None, "output_tokens": None, "cached_input_tokens": None,
                "reasoning_tokens": None, "total_tokens": None, "cost_minor_units": None,
                "currency": None, "usage_source": "unavailable", "source_ref": None,
                "source_sha256": None, "reported_at": None,
            },
            "source": {
                "source_kind": "ack", "source_ref": "acks/TASK-015/ACK-002.yaml",
                "source_sha256": "2" * 64, "source_event_id": "EVENT-001",
                "correlation_id": "RUN-123:TASK-015:ATTEMPT-002", "causation_id": None,
            },
            "supersedes_record_sha256": None,
        }

    @property
    def ledger(self) -> Path:
        return self.agent / "activity" / "RUN-123" / "TASK-015" / "ATTEMPT-002"

    def command(self, payload: dict, *, ok: bool = True) -> subprocess.CompletedProcess[str]:
        input_path = Path(self.temp.name) / f"input-{id(payload)}-{payload.get('session_id', 'x')}.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        result = subprocess.run([
            sys.executable, str(SCRIPTS / "record_agent_activity.py"),
            "--project-root", str(self.project), "--governance-root", str(self.governance),
            "--agent-id", "A02-worker", "--input", str(input_path),
        ], capture_output=True, text=True)
        if ok and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def test_cli_writes_date_partitioned_immutable_chain_and_index(self) -> None:
        first_pointer = json.loads(self.command(self.payload()).stdout)
        second_pointer = json.loads(self.command(self.payload(summary="working", session_id="SESSION-002")).stdout)
        self.assertEqual(first_pointer["activity_id"], "ACTIVITY-000001")
        self.assertEqual(second_pointer["activity_id"], "ACTIVITY-000002")
        self.assertEqual(first_pointer["path"], "2026/08/06/ACTIVITY-000001.json")

        first = json.loads((self.ledger / first_pointer["path"]).read_text(encoding="utf-8"))
        second = json.loads((self.ledger / second_pointer["path"]).read_text(encoding="utf-8"))
        self.assertEqual(schema_errors(first, self.schema, self.schema), [])
        self.assertEqual(schema_errors(second, self.schema, self.schema), [])
        self.assertEqual((first["sequence"], second["sequence"]), (1, 2))
        self.assertIsNone(first["previous_record_sha256"])
        self.assertEqual(second["previous_record_sha256"], first["record_sha256"])
        self.assertEqual(first["record_sha256"], canonical_record_hash(first))
        self.assertEqual(second["record_sha256"], canonical_record_hash(second))
        index = [json.loads(line) for line in (self.ledger / "INDEX.jsonl").read_text().splitlines()]
        self.assertEqual([item["activity_id"] for item in index], ["ACTIVITY-000001", "ACTIVITY-000002"])
        self.assertEqual(index[-1]["record_sha256"], second["record_sha256"])
        self.assertEqual(json.loads((self.ledger / "CURRENT.json").read_text()), second_pointer)

    def test_requires_matching_run_task_attempt_agent_session_and_existing_rp_binding(self) -> None:
        for field in ("run_id", "task_id", "attempt_id", "session_id", "runtime_profile"):
            with self.subTest(field=field):
                payload = self.payload()
                del payload[field]
                self.assertNotEqual(self.command(payload, ok=False).returncode, 0)
        payload = self.payload()
        payload["agent_id"] = "A99-other"
        self.assertNotEqual(self.command(payload, ok=False).returncode, 0)
        payload = self.payload()
        payload["runtime_profile"]["native_binding_ref"] = "runtime/profiles/RP-999999.json"
        self.assertNotEqual(self.command(payload, ok=False).returncode, 0)
        self.assertFalse(self.ledger.exists())

    def test_usage_is_real_receipt_backed_or_all_null_with_explicit_source(self) -> None:
        invalid = self.payload()
        invalid["usage"]["input_tokens"] = 12
        self.assertNotEqual(self.command(invalid, ok=False).returncode, 0)

        receipt = self.agent / "receipts" / "usage.json"
        receipt.parent.mkdir()
        receipt.write_text('{"input_tokens":12,"output_tokens":3}\n', encoding="utf-8")
        import hashlib
        valid = self.payload()
        valid["usage"].update({
            "input_tokens": 12, "output_tokens": 3, "total_tokens": 15,
            "usage_source": "provider_response", "source_ref": "receipts/usage.json",
            "source_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(),
            "reported_at": "2026-08-06T16:00:01Z",
        })
        record = json.loads((self.ledger / json.loads(self.command(valid).stdout)["path"]).read_text())
        self.assertEqual(record["usage"]["input_tokens"], 12)

    def test_parallel_processes_allocate_continuous_unique_records(self) -> None:
        def run(index: int) -> subprocess.CompletedProcess[str]:
            return self.command(self.payload(summary=f"parallel {index}", session_id=f"SESSION-{index:03d}"))

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, range(12)))
        ids = sorted(json.loads(result.stdout)["activity_id"] for result in results)
        self.assertEqual(ids, [f"ACTIVITY-{index:06d}" for index in range(1, 13)])
        index = [json.loads(line) for line in (self.ledger / "INDEX.jsonl").read_text().splitlines()]
        self.assertEqual([item["sequence"] for item in index], list(range(1, 13)))
        records = [json.loads((self.ledger / item["path"]).read_text()) for item in index]
        self.assertEqual(len({record["record_sha256"] for record in records}), 12)
        for position, record in enumerate(records):
            expected = None if position == 0 else records[position - 1]["record_sha256"]
            self.assertEqual(record["previous_record_sha256"], expected)

    def test_secret_scan_rejects_before_publish_without_echoing_secret(self) -> None:
        secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
        rejected = self.command(self.payload(summary=secret), ok=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn(secret, rejected.stdout + rejected.stderr)
        self.assertFalse(self.ledger.exists())

    def test_publish_failure_rolls_back_record_index_and_pointer(self) -> None:
        import record_agent_activity as module

        first = module.record_agent_activity(
            project_root=self.project, governance_root=self.governance,
            agent_id="A02-worker", payload=self.payload(),
        )
        before = {path.relative_to(self.ledger).as_posix(): path.read_bytes() for path in self.ledger.rglob("*") if path.is_file() and path.name != ".activity.lock"}
        real_replace = module.os.replace
        calls = 0

        def fail_during_publish(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected publish failure")
            return real_replace(source, destination)

        with mock.patch.object(module.os, "replace", side_effect=fail_during_publish):
            with self.assertRaises(OSError):
                module.record_agent_activity(
                    project_root=self.project, governance_root=self.governance,
                    agent_id="A02-worker", payload=self.payload(summary="second", session_id="SESSION-002"),
                )
        after = {path.relative_to(self.ledger).as_posix(): path.read_bytes() for path in self.ledger.rglob("*") if path.is_file() and path.name != ".activity.lock"}
        self.assertEqual(after, before)
        self.assertEqual(first["activity_id"], "ACTIVITY-000001")
        self.assertFalse(any(self.ledger.rglob("ACTIVITY-000002.json")))


if __name__ == "__main__":
    unittest.main()
