from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
INIT = SCRIPTS / "init_run.py"
MANAGE = SCRIPTS / "manage_run.py"


class RunActivityIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        (self.project / "src").mkdir()
        self.run_dir = Path(self.command(
            INIT, "--project-root", self.project, "--governance", "light",
            "--transport", "document_bus", "--objective", "activity integration",
            "--run-id", "RUN-ACTIVITY", "--versioning-mode", "not_applicable",
            "--versioning-reason", "test fixture", "--user-confirmed",
        ).stdout.strip())
        self.command(
            MANAGE, "add-agent", "--run-dir", self.run_dir, "--agent-id", "A02-owner",
            "--runtime", "document", "--role", "worker", "--readable-path", self.project,
            "--writable-path", self.project / "src",
        )
        self.command(
            MANAGE, "create-task", "--run-dir", self.run_dir, "--task-id", "TASK-001",
            "--title", "task", "--objective", "work", "--owner-agent", "A02-owner",
            "--owned-path", self.project / "src",
        )
        self.agent = self.project / ".multi-agent-collaboration" / "agents" / "A02-owner"
        (self.agent / "runtime" / "profiles").mkdir(parents=True)
        self.profile = self.agent / "runtime" / "profiles" / "RP-000001.json"
        self.profile.write_text(json.dumps({
            "runtime_profile_id": "RP-000001",
            "model": {"status": "known", "value": "gpt-5.6-sol"},
            "provider": {"status": "known", "value": "custom-rootflowgpt"},
            "profile": {"status": "known", "value": "default"},
            "runtime_kind": {"status": "known", "value": "document"},
        }) + "\n", encoding="utf-8")
        self.bridge = [
            "--activity-project-root", str(self.project),
            "--activity-session-id", "SESSION-016-A2",
            "--activity-runtime-profile-ref", "runtime/profiles/RP-000001.json",
        ]

    def command(self, script: Path, *args: object, ok: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(script), *map(str, args)], capture_output=True, text=True,
        )
        if ok and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def records(self) -> list[dict]:
        ledger = self.agent / "activity" / "RUN-ACTIVITY" / "TASK-001" / "ATTEMPT-002"
        index = [json.loads(line) for line in (ledger / "INDEX.jsonl").read_text().splitlines()]
        return [json.loads((ledger / item["path"]).read_text()) for item in index]

    def test_ack_lease_result_and_evidence_are_bridged_without_replacing_v3_sources(self) -> None:
        ack = Path(self.command(
            MANAGE, "write-ack", "--run-dir", self.run_dir, "--task-id", "TASK-001",
            "--agent-id", "A02-owner", "--attempt-id", "ATTEMPT-002",
            "--idempotency-key", "RUN-ACTIVITY:TASK-001:ATTEMPT-002:ack:v1", *self.bridge,
        ).stdout.strip())
        lease = Path(self.command(
            MANAGE, "write-lease", "--run-dir", self.run_dir, "--task-id", "TASK-001",
            "--agent-id", "A02-owner", "--attempt-id", "ATTEMPT-002", "--lease-id", "LEASE-002",
            *self.bridge,
        ).stdout.strip())
        verification = self.run_dir / "verification.txt"
        verification.write_text("passed\n", encoding="utf-8")
        result = Path(self.command(
            MANAGE, "write-result", "--run-dir", self.run_dir, "--task-id", "TASK-001",
            "--agent-id", "A02-owner", "--attempt-id", "ATTEMPT-002", "--status", "completed",
            "--outcome", "done", "--verification-status", "passed",
            "--verification-ref", verification, "--risk-summary", "none",
            "--rollback-plan", "none", *self.bridge,
        ).stdout.strip())
        evidence = Path(self.command(
            MANAGE, "record-evidence", "--run-dir", self.run_dir, "--evidence-id", "QA-002",
            "--kind", "qa", "--status", "passed", "--task-id", "TASK-001",
            "--agent-id", "A02-owner", "--attempt-id", "ATTEMPT-002", "--summary", "qa passed",
            "--artifact-ref", verification, *self.bridge,
        ).stdout.strip())

        self.assertTrue(all(path.is_file() for path in (ack, lease, result, evidence)))
        records = self.records()
        self.assertEqual([r["record_kind"] for r in records], [
            "attempt_started", "status_transition", "attempt_finished", "artifact_evidence",
        ])
        expected_hash = hashlib.sha256(self.profile.read_bytes()).hexdigest()
        for record, source in zip(records, (ack, lease, result, evidence), strict=True):
            self.assertEqual(record["attempt_id"], "ATTEMPT-002")
            self.assertEqual(record["runtime_profile"]["native_binding_sha256"], expected_hash)
            self.assertEqual(record["source"]["source_sha256"], hashlib.sha256(source.read_bytes()).hexdigest())
            self.assertIn("ATTEMPT-002", record["source"]["correlation_id"])
        self.assertEqual([r["sequence"] for r in records], [1, 2, 3, 4])
        self.assertEqual(len({r["idempotency_key"] for r in records}), 4)

    def test_activity_failure_rolls_back_result_and_evidence_sources(self) -> None:
        broken_bridge = [
            "--activity-project-root", str(self.project),
            "--activity-session-id", "SESSION-016-A2",
            "--activity-runtime-profile-ref", "runtime/profiles/MISSING.json",
        ]
        result = self.command(
            MANAGE, "write-result", "--run-dir", self.run_dir, "--task-id", "TASK-001",
            "--agent-id", "A02-owner", "--attempt-id", "ATTEMPT-002", "--status", "completed",
            "--outcome", "done", "--verification-status", "not_run", "--risk-summary", "none",
            "--rollback-plan", "none", *broken_bridge, ok=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.run_dir / "outbox" / "A02-owner" / "TASK-001-result-ATTEMPT-002.md").exists())

        evidence = self.command(
            MANAGE, "record-evidence", "--run-dir", self.run_dir, "--evidence-id", "QA-FAIL",
            "--kind", "qa", "--status", "passed", "--task-id", "TASK-001",
            "--agent-id", "A02-owner", "--attempt-id", "ATTEMPT-002", "--summary", "qa passed",
            *broken_bridge, ok=False,
        )
        self.assertNotEqual(evidence.returncode, 0)
        self.assertFalse((self.run_dir / "evidence" / "QA-FAIL.yaml").exists())


if __name__ == "__main__":
    unittest.main()
