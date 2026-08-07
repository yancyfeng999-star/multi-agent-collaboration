from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
BRIDGE = SCRIPTS / "archive_run_to_agents.py"


class RunMemoryBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        (self.project / "src").mkdir()
        self.command("init_project_agents.py", "--project-root", str(self.project),
                 "--project-id", "bridge", "--project-name", "Bridge",
                 "--agents", "A01-coordinator,A02-worker", "--governance", "light",
                 "--user-confirmed")
        initialized = self.command(
            "init_run.py", "--project-root", str(self.project), "--governance", "light",
            "--transport", "document_bus", "--objective", "bridge lifecycle",
            "--run-id", "RUN-BRIDGE", "--versioning-mode", "not_applicable",
            "--versioning-reason", "Fixture has no versioned deliverable", "--user-confirmed",
        )
        self.run_dir = Path(initialized.stdout.strip())
        self.command("manage_run.py", "add-agent", "--run-dir", str(self.run_dir),
                 "--agent-id", "worker", "--runtime", "document", "--role", "Worker",
                 "--readable-path", str(self.project), "--writable-path", str(self.project / "src"))
        runtime = json.loads(self.command(
            "record_agent_runtime.py", "--project-root", str(self.project),
            "--agent-id", "A02-worker", "--model", "gpt-5.6-sol",
            "--provider", "custom:rootflowgpt", "--platform", "hermes",
            "--profile", "default", "--workspace", str(self.project),
            "--runtime-kind", "document",
        ).stdout)
        self.runtime_profile = (
            self.project / ".multi-agent-collaboration" / "agents" / "A02-worker"
            / "runtime" / runtime["path"]
        )
        self.runtime_profile_ref = f"runtime/{runtime['path']}"
        task_result = self.command(
            "manage_run.py", "create-task", "--run-dir", str(self.run_dir),
            "--task-id", "TASK-001", "--title", "Bridge task", "--objective", "Finish it",
            "--owner-agent", "worker", "--owned-path", str(self.project / "src"),
            "--acceptance", "Archived", "--verification", "Validate run",
        )
        self.task = Path(task_result.stdout.strip())
        self.emit("TASK_READY", "coordinator", "worker", self.task)
        self.emit("TASK_DISPATCHED", "coordinator", "worker", self.task)
        ack = Path(self.command("manage_run.py", "write-ack", "--run-dir", str(self.run_dir),
                            "--task-id", "TASK-001", "--agent-id", "worker",
                            "--idempotency-key", "ACK-1").stdout.strip())
        self.emit("ACK", "worker", "coordinator", ack)
        lease = Path(self.command("manage_run.py", "write-lease", "--run-dir", str(self.run_dir),
                              "--task-id", "TASK-001", "--agent-id", "worker",
                              "--lease-id", "LEASE-001").stdout.strip())
        self.emit("LEASE_ACQUIRED", "coordinator", "worker", lease)
        self.artifact = self.project / "src" / "report.txt"
        self.artifact.write_text("verified output\n", encoding="utf-8")
        self.evidence = Path(self.command(
            "manage_run.py", "record-evidence", "--run-dir", str(self.run_dir),
            "--evidence-id", "VERIFY-001", "--kind", "verification", "--status", "passed",
            "--task-id", "TASK-001", "--agent-id", "worker", "--summary", "Verified",
            "--artifact-ref", str(self.artifact),
        ).stdout.strip())
        self.result = Path(self.command(
            "manage_run.py", "write-result", "--run-dir", str(self.run_dir),
            "--task-id", "TASK-001", "--agent-id", "worker", "--status", "completed",
            "--outcome", "Done", "--verification-status", "passed",
            "--verification-ref", str(self.evidence), "--risk-summary", "None",
            "--rollback-plan", "Remove report", "--changed-file", str(self.artifact),
            "--handoff-to", "coordinator",
        ).stdout.strip())
        self.activity = self.record_activity()
        self.emit("HANDOFF_READY", "worker", "coordinator", self.result)
        self.emit("TASK_COMPLETED", "coordinator", "worker", self.result)
        validation = self.command("validate_run.py", str(self.run_dir), "--phase", "completion")
        self.assertIn("0 errors", validation.stdout)

    def command(self, script: str, *args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(["python3", str(SCRIPTS / script), *args], capture_output=True, text=True)
        if ok and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def emit(self, event: str, sender: str, recipient: str, payload: Path) -> None:
        self.command("emit_event.py", "--run-dir", str(self.run_dir), "--task-id", "TASK-001",
                 "--event", event, "--from-agent", sender, "--to-agent", recipient,
                 "--summary", event, "--payload-file", str(payload))

    def record_activity(self) -> Path:
        profile = json.loads(self.runtime_profile.read_text(encoding="utf-8"))
        payload = {
            "schema_version": 1, "record_kind": "attempt_finished",
            "recorded_at": "2026-08-06T16:00:00Z", "run_id": "RUN-BRIDGE",
            "task_id": "TASK-001", "attempt_id": "ATTEMPT-001",
            "agent_id": "A02-worker", "session_id": "SESSION-BRIDGE",
            "parent_agent_id": "coordinator",
            "runtime_profile": {
                "runtime": "document", "provider": profile["provider"]["value"],
                "model": profile["model"]["value"], "profile_name": profile["profile"]["value"],
                "node_id": "local", "host_fingerprint": None,
                "native_binding_ref": self.runtime_profile_ref,
                "native_binding_sha256": hashlib.sha256(self.runtime_profile.read_bytes()).hexdigest(),
            },
            "status": {"attempt_status": "completed", "task_status_observed": "completed",
                       "outcome": "success", "reason_code": None, "summary": "Run task completed"},
            "tool_summary": None, "verification": None, "artifacts": [], "evidence_refs": [],
            "usage": {"input_tokens": None, "output_tokens": None, "cached_input_tokens": None,
                      "reasoning_tokens": None, "total_tokens": None, "cost_minor_units": None,
                      "currency": None, "usage_source": "unavailable", "source_ref": None,
                      "source_sha256": None, "reported_at": None},
            "source": {"source_kind": "result", "source_ref": self.result.resolve().relative_to(self.project.resolve()).as_posix(),
                       "source_sha256": hashlib.sha256(self.result.read_bytes()).hexdigest(),
                       "source_event_id": None, "correlation_id": "RUN-BRIDGE:TASK-001:ATTEMPT-001",
                       "causation_id": None},
            "supersedes_record_sha256": None,
        }
        input_path = Path(self.temp.name) / "activity.json"
        input_path.write_text(json.dumps(payload), encoding="utf-8")
        pointer = json.loads(self.command("record_agent_activity.py", "--project-root", str(self.project),
                                         "--agent-id", "A02-worker", "--input", str(input_path)).stdout)
        return (self.runtime_profile.parents[2] / "activity" / "RUN-BRIDGE" / "TASK-001"
                / "ATTEMPT-001" / pointer["path"])

    def bridge(self, *extra: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
        return self.command("archive_run_to_agents.py", "--run-dir", str(self.run_dir),
                        "--agent-map", "worker=A02-worker", *extra, ok=ok)

    def run_fingerprint(self) -> dict[str, str]:
        result = {}
        for path in sorted(self.run_dir.rglob("*")):
            if path.is_file() and path.name != ".sequence.lock":
                result[path.relative_to(self.run_dir).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
        return result

    def test_real_lifecycle_bridges_frozen_sources_and_manifest_without_mutating_run(self) -> None:
        before = self.run_fingerprint()
        dry = self.bridge("--dry-run")
        plan = json.loads(dry.stdout)
        self.assertTrue(plan["dry_run"])
        self.assertFalse((self.project / ".multi-agent-collaboration" / "bridges").exists())
        self.assertEqual(before, self.run_fingerprint())

        completed = self.bridge()
        manifest_path = Path(completed.stdout.strip())
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["schema_version"], "1.1")
        self.assertEqual(manifest["run_id"], "RUN-BRIDGE")
        self.assertEqual(manifest["event_sequence"], 6)
        self.assertEqual(manifest["source_run_path"], str(self.run_dir))
        self.assertEqual(before, self.run_fingerprint())

        agent = self.project / ".multi-agent-collaboration" / "agents" / "A02-worker"
        task_copy = agent / "tasks" / "TASK-BRIDGE--001.md"
        handoff_copy = agent / "handoffs" / "TASK-BRIDGE--001--ATTEMPT-001.md"
        bundle = agent / "artifacts" / "RUN-BRIDGE--TASK-001--bundle.json"
        evidence_copy = agent / "artifacts" / "RUN-BRIDGE--TASK-001--evidence--VERIFY-001.yaml"
        self.assertIn('doc_type: "task"', task_copy.read_text(encoding="utf-8"))
        self.assertIn('doc_type: "handoff"', handoff_copy.read_text(encoding="utf-8"))
        task_meta = self.frontmatter(task_copy)
        handoff_meta = self.frontmatter(handoff_copy)
        self.assertEqual(task_meta["schema_version"], "1.0")
        self.assertEqual(handoff_meta["schema_version"], "1.1")
        profile_hash = hashlib.sha256(self.runtime_profile.read_bytes()).hexdigest()
        activity_hash = hashlib.sha256(self.activity.read_bytes()).hexdigest()
        self.assertEqual(handoff_meta["runtime_profile_id"], "RP-000001")
        self.assertEqual(handoff_meta["runtime_profile_sha256"], profile_hash)
        self.assertEqual(
            handoff_meta["activity_record_path"],
            self.activity.relative_to(self.project / ".multi-agent-collaboration").as_posix(),
        )
        self.assertEqual(handoff_meta["activity_record_sha256"], activity_hash)
        self.assertNotIn("record_kind", handoff_meta)
        self.assertEqual(evidence_copy.read_bytes(), self.evidence.read_bytes())
        metadata = json.loads(bundle.read_text(encoding="utf-8"))
        records = {item["kind"]: item for item in metadata["records"]}
        self.assertEqual(records["artifact"]["source_path"], str(self.artifact.resolve()))
        self.assertEqual(records["artifact"]["source_sha256"], hashlib.sha256(self.artifact.read_bytes()).hexdigest())
        self.assertTrue(all("source_path" in item and "source_sha256" in item for item in metadata["records"]))
        task_entry = manifest["tasks"][0]
        self.assertEqual(len(manifest["tasks"]), len(list((self.run_dir / "tasks").glob("*.md"))))
        self.assertEqual(task_entry["runtime_profile_path"], str(self.runtime_profile.resolve()))
        self.assertEqual(task_entry["runtime_profile_sha256"], profile_hash)
        self.assertEqual(task_entry["activity_record_path"], str(self.activity.resolve()))
        self.assertEqual(task_entry["activity_record_sha256"], activity_hash)
        self.assertEqual(task_entry["result_source_path"], str(self.result.resolve()))
        self.assertEqual(task_entry["result_source_sha256"], hashlib.sha256(self.result.read_bytes()).hexdigest())
        self.command("rebuild_index.py", "--project-root", str(self.project))
        validated = self.command("validate_agents.py", "--project-root", str(self.project))
        self.assertIn("PASS", validated.stdout)

    def test_second_run_is_idempotent_and_archive_tampering_fails_closed(self) -> None:
        manifest = Path(self.bridge().stdout.strip())
        agent = self.project / ".multi-agent-collaboration" / "agents" / "A02-worker"
        tracked = [manifest, agent / "tasks" / "TASK-BRIDGE--001.md",
                   agent / "handoffs" / "TASK-BRIDGE--001--ATTEMPT-001.md",
                   agent / "artifacts" / "RUN-BRIDGE--TASK-001--bundle.json"]
        mtimes = {path: path.stat().st_mtime_ns for path in tracked}
        second = self.bridge()
        self.assertEqual(Path(second.stdout.strip()), manifest)
        self.assertEqual(mtimes, {path: path.stat().st_mtime_ns for path in tracked})

        task_copy = agent / "tasks" / "TASK-BRIDGE--001.md"
        task_copy.write_text(task_copy.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
        failed = self.bridge(ok=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("conflict", failed.stderr.lower())
        self.assertTrue(task_copy.read_text(encoding="utf-8").endswith("tampered\n"))

        task_copy.write_text(task_copy.read_text(encoding="utf-8")[:-9], encoding="utf-8")
        bridge_manifest = json.loads(manifest.read_text(encoding="utf-8"))
        bridge_manifest["tasks"] = []
        manifest.write_text(json.dumps(bridge_manifest, indent=2) + "\n", encoding="utf-8")
        drifted = self.bridge(ok=False)
        self.assertNotEqual(drifted.returncode, 0)
        self.assertIn("conflict", drifted.stderr.lower())
        self.assertEqual(json.loads(manifest.read_text(encoding="utf-8"))["tasks"], [])

    def test_activity_tampering_is_rejected_before_any_bridge_write(self) -> None:
        activity = json.loads(self.activity.read_text(encoding="utf-8"))
        activity["status"]["summary"] = "tampered activity"
        self.activity.write_text(json.dumps(activity, indent=2) + "\n", encoding="utf-8")
        failed = self.bridge(ok=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("activity", failed.stderr.lower())
        self.assertFalse((self.project / ".multi-agent-collaboration" / "bridges").exists())

    def test_source_tampering_is_rejected_before_any_bridge_write(self) -> None:
        self.artifact.write_text("tampered source\n", encoding="utf-8")
        failed = self.bridge(ok=False)
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn("validation failed", failed.stderr.lower())
        self.assertFalse((self.project / ".multi-agent-collaboration" / "bridges").exists())

    @staticmethod
    def frontmatter(path: Path) -> dict:
        text = path.read_text(encoding="utf-8")
        end = text.index("\n---\n", 4)
        return {
            key.strip(): json.loads(value.strip())
            for key, value in (line.split(":", 1) for line in text[4:end].splitlines())
        }


if __name__ == "__main__":
    unittest.main()
