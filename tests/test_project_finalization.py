from __future__ import annotations

import importlib.util
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
import sys
sys.path.insert(0, str(SCRIPTS))


def load_script(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ProjectFinalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.governance = Path(self.temp.name) / "governance"
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(["git", "-C", str(self.project), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.project), "config", "user.name", "Fixture"], check=True)
        (self.project / ".seed").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.project), "add", ".seed"], check=True)
        subprocess.run(["git", "-C", str(self.project), "commit", "-qm", "fixture"], check=True)
        result = subprocess.run(
            ["python3", str(SCRIPTS / "init_project_agents.py"), "--project-root", str(self.project),
             "--project-id", "fixture", "--project-name", "Fixture", "--agents", "A01-coordinator,A02-worker",
             "--governance", "standard", "--governance-root", str(self.governance), "--user-confirmed"], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.bus = self.governance / "projects" / "fixture"
        self.make_run("RUN-001", "completed")

    def make_run(self, run_id: str, status: str) -> Path:
        run = self.bus / "runs" / run_id
        for directory in ("tasks", "outbox/A02-worker", "evidence", "decisions", "events"):
            (run / directory).mkdir(parents=True, exist_ok=True)
        (run / "manifest.yaml").write_text(
            f'protocol_version: 3\nrun_id: "{run_id}"\nstatus: "{status}"\ngovernance: "standard"\ntasks: ["TASK-1"]\n', encoding="utf-8")
        (run / "state.yaml").write_text(
            f'protocol_version: 3\nrun_id: "{run_id}"\nstatus: "{status}"\ntask_states: {{"TASK-1": "completed"}}\n', encoding="utf-8")
        (run / "summary.md").write_text("# Run Summary\n\nAll accepted.\n", encoding="utf-8")
        (run / "tasks/TASK-1.md").write_text("---\ntask_id: \"TASK-1\"\nowner_agent: \"A02-worker\"\n---\n# Task\n", encoding="utf-8")
        (run / "outbox/A02-worker/TASK-1-result-ATTEMPT-001.md").write_text(
            "---\ntask_id: \"TASK-1\"\nagent_id: \"A02-worker\"\nstatus: \"completed\"\nverification_status: \"passed\"\nrisk_summary: \"none\"\nhandoff_to: \"coordinator\"\n---\n# Outcome\nDone\n", encoding="utf-8")
        (run / "evidence/VERIFY.yaml").write_text('kind: "verification"\nstatus: "passed"\nsummary: "tests passed"\n', encoding="utf-8")
        (run / "decisions/GATE.yaml").write_text('kind: "human_gate"\nstatus: "approved"\nsummary: "approved"\n', encoding="utf-8")
        return run

    def seed_runtime_audit_sources(self) -> dict[str, Path]:
        agent = self.bus / "agents/A02-worker"
        profile = agent / "runtime/profiles/RP-000001.json"
        profile.parent.mkdir(parents=True, exist_ok=True)
        profile_payload: dict[str, object] = {
            "schema_version": "1.0", "doc_type": "runtime_profile",
            "runtime_profile_id": "RP-000001", "agent_id": "A02-worker",
            "model": {"status": "known", "value": "fixture-model"},
            "provider": {"status": "known", "value": "fixture-provider"},
        }
        profile_digest = hashlib.sha256(json.dumps(
            profile_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        profile_payload["record_hash"] = {
            "algorithm": "sha256", "canonicalization": "jcs-rfc8785", "value": profile_digest,
        }
        profile.write_text(json.dumps(profile_payload), encoding="utf-8")
        activity = agent / "activity/RUN-001/TASK-1/ATTEMPT-001/ACTIVITY-000001.json"
        activity.parent.mkdir(parents=True, exist_ok=True)
        activity.write_text(json.dumps({
            "record_kind": "attempt_finished", "agent_id": "A02-worker",
            "run_id": "RUN-001", "task_id": "TASK-1", "attempt_id": "ATTEMPT-001",
            "runtime_profile": {
                "native_binding_ref": "agents/A02-worker/runtime/profiles/RP-000001.json",
                "native_binding_sha256": hashlib.sha256(profile.read_bytes()).hexdigest(),
            },
        }), encoding="utf-8")
        return {"runtime": profile, "activity": activity, "agent_profile": agent / "AGENT_PROFILE.json"}

    def close_persistent_layer(self, *run_ids: str) -> None:
        for run_id in run_ids:
            run = self.bus / "runs" / run_id
            bridge = self.bus / "bridges" / f"{run_id}.json"
            bridge.parent.mkdir(exist_ok=True)
            bridge.write_text(json.dumps({
                "schema_version": "1.0", "doc_type": "run_memory_bridge", "run_id": run_id,
                "source_run_path": str(run.resolve()),
                "source_manifest_sha256": hashlib.sha256((run / "manifest.yaml").read_bytes()).hexdigest(),
                "source_state_sha256": hashlib.sha256((run / "state.yaml").read_bytes()).hexdigest(),
                "tasks": [{"source_task_id": "TASK-1"}],
            }), encoding="utf-8")
        module = load_script("create_project_checkpoint")
        module.create_project_checkpoint(
            self.project, list(run_ids), governance_root=self.governance, project_id="fixture",
        )
        rebuilt = subprocess.run(
            ["python3", str(SCRIPTS / "rebuild_index.py"), "--project-root", str(self.project),
             "--governance-root", str(self.governance), "--project-id", "fixture"],
            capture_output=True, text=True,
        )
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr)

    def test_checkpoint_is_immutable_chained_and_hash_binds_sources(self) -> None:
        module = load_script("create_project_checkpoint")
        preview = module.create_project_checkpoint(self.project, ["RUN-001"], dry_run=True, governance_root=self.governance, project_id="fixture")
        self.assertEqual(preview["checkpoint_id"], "PCP-0001")
        self.assertFalse((self.bus / "project-checkpoints").exists())

        first = module.create_project_checkpoint(self.project, ["RUN-001"], governance_root=self.governance, project_id="fixture")
        second = module.create_project_checkpoint(self.project, ["RUN-001"], governance_root=self.governance, project_id="fixture")
        self.assertEqual(first["checkpoint_id"], "PCP-0001")
        self.assertEqual(second["checkpoint_id"], "PCP-0002")
        first_doc = (self.bus / first["path"]).read_text(encoding="utf-8")
        second_doc = (self.bus / second["path"]).read_text(encoding="utf-8")
        self.assertIn('previous_checkpoint: null', first_doc)
        self.assertIn('previous_checkpoint: "PCP-0001"', second_doc)
        self.assertIn("source_hashes:", first_doc)
        current = (self.bus / "CURRENT_PROJECT_CONTEXT.md").read_text(encoding="utf-8")
        self.assertIn("project-checkpoints/PCP-0002.md", current)
        with self.assertRaises(FileExistsError):
            module._write_immutable(self.bus / first["path"], "tamper")
        subprocess.run(["python3", str(SCRIPTS / "rebuild_index.py"), "--project-root", str(self.project), "--governance-root", str(self.governance), "--project-id", "fixture"], check=True, capture_output=True, text=True)
        valid = subprocess.run(["python3", str(SCRIPTS / "validate_agents.py"), "--project-root", str(self.project), "--governance-root", str(self.governance), "--project-id", "fixture"], capture_output=True, text=True)
        self.assertEqual(valid.returncode, 0, valid.stdout + valid.stderr)

        latest = self.bus / second["path"]
        latest.chmod(0o600)
        latest.write_text(latest.read_text(encoding="utf-8") + "TAMPERED\n", encoding="utf-8")
        invalid = subprocess.run(["python3", str(SCRIPTS / "validate_agents.py"), "--project-root", str(self.project), "--governance-root", str(self.governance), "--project-id", "fixture"], capture_output=True, text=True)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn("content_sha256 mismatch", invalid.stdout)

    def test_finalize_fail_closed_then_writes_idempotent_audit_bundle(self) -> None:
        self.close_persistent_layer("RUN-001")
        module = load_script("finalize_project")
        validators = lambda root, runs: []
        preview = module.finalize_project(self.project, ["RUN-001"], dry_run=True, validator_runner=validators, governance_root=self.governance, project_id="fixture")
        self.assertEqual(preview["status"], "ready")
        self.assertFalse((self.bus / "PROJECT_FINAL_REPORT.md").exists())

        result = module.finalize_project(self.project, ["RUN-001"], validator_runner=validators, governance_root=self.governance, project_id="fixture")
        self.assertEqual(result["status"], "finalized")
        report = self.bus / "PROJECT_FINAL_REPORT.md"
        manifest = self.bus / "AUDIT_MANIFEST.json"
        index = self.bus / "ARTIFACT_INDEX.jsonl"
        for path in (report, manifest, index):
            self.assertTrue(path.is_file())
        report_text = report.read_text(encoding="utf-8")
        for heading in ("Tasks", "Agents", "Runs", "Decisions", "Handoffs", "Evidence", "Risks", "Unresolved Items", "Approvals and Risk Acceptance"):
            self.assertIn(f"## {heading}", report_text)
        audit = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(audit["project_id"], "fixture")
        self.assertEqual(audit["runs"], ["RUN-001"])
        self.assertTrue(audit["source_hashes"])
        before = {p.name: p.read_bytes() for p in (report, manifest, index)}
        again = module.finalize_project(self.project, ["RUN-001"], validator_runner=validators, governance_root=self.governance, project_id="fixture")
        self.assertEqual(again["status"], "already_finalized")
        self.assertEqual(before, {p.name: p.read_bytes() for p in (report, manifest, index)})

        (self.bus / "runs/RUN-001/state.yaml").write_text(
            'protocol_version: 3\nrun_id: "RUN-001"\nstatus: "active"\ntask_states: {"TASK-1": "running"}\n', encoding="utf-8")
        for path in (report, manifest, index):
            path.unlink()
        with self.assertRaisesRegex(ValueError, "terminal"):
            module.finalize_project(self.project, ["RUN-001"], validator_runner=validators, governance_root=self.governance, project_id="fixture")

    def test_validator_failure_and_unapproved_gate_are_fail_closed(self) -> None:
        self.close_persistent_layer("RUN-001")
        module = load_script("finalize_project")
        with self.assertRaisesRegex(ValueError, "validator"):
            module.finalize_project(self.project, ["RUN-001"], validator_runner=lambda root, runs: ["persistent validator failed"], governance_root=self.governance, project_id="fixture")
        gate = self.bus / "runs/RUN-001/decisions/GATE.yaml"
        gate.write_text('kind: "human_gate"\nstatus: "pending"\nsummary: "waiting"\n', encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "gate"):
            module.finalize_project(self.project, ["RUN-001"], validator_runner=lambda root, runs: [], governance_root=self.governance, project_id="fixture")

    def test_release_approval_is_scoped_per_run(self) -> None:
        second = self.make_run("RUN-002", "completed")
        for decision in (second / "decisions").glob("*"):
            decision.unlink()
        (second / "events/000001-release_ready.yaml").write_text("event: RELEASE_READY\n", encoding="utf-8")
        module = load_script("finalize_project")
        with self.assertRaisesRegex(ValueError, "release gate missing approval: RUN-002"):
            module.finalize_project(self.project, ["RUN-001", "RUN-002"], validator_runner=lambda root, runs: [], governance_root=self.governance, project_id="fixture")

    def test_existing_audit_rejects_source_drift(self) -> None:
        self.close_persistent_layer("RUN-001")
        module = load_script("finalize_project")
        validators = lambda root, runs: []
        module.finalize_project(self.project, ["RUN-001"], validator_runner=validators, governance_root=self.governance, project_id="fixture")
        (self.bus / "runs/RUN-001/summary.md").write_text("tampered after finalization\n", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "source hash mismatch"):
            module.finalize_project(self.project, ["RUN-001"], validator_runner=validators, governance_root=self.governance, project_id="fixture")

    def test_existing_audit_rejects_empty_artifact_index(self) -> None:
        self.close_persistent_layer("RUN-001")
        module = load_script("finalize_project")
        validators = lambda root, runs: []
        module.finalize_project(self.project, ["RUN-001"], validator_runner=validators, governance_root=self.governance, project_id="fixture")
        (self.bus / "ARTIFACT_INDEX.jsonl").write_text("", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "record set"):
            module.finalize_project(self.project, ["RUN-001"], validator_runner=validators, governance_root=self.governance, project_id="fixture")

    def test_final_audit_includes_runtime_activity_profile_pcp_bridge_and_index_hashes(self) -> None:
        self.close_persistent_layer("RUN-001")
        runtime_sources = self.seed_runtime_audit_sources()
        rebuilt = subprocess.run(
            ["python3", str(SCRIPTS / "rebuild_index.py"), "--project-root", str(self.project),
             "--governance-root", str(self.governance), "--project-id", "fixture"],
            capture_output=True, text=True,
        )
        self.assertEqual(rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr)
        module = load_script("finalize_project")
        module.finalize_project(self.project, ["RUN-001"], validator_runner=lambda root, runs: [], governance_root=self.governance, project_id="fixture")

        audit = json.loads((self.bus / "AUDIT_MANIFEST.json").read_text(encoding="utf-8"))
        index_records = {
            record["path"]: record
            for record in map(json.loads, (self.bus / "ARTIFACT_INDEX.jsonl").read_text(encoding="utf-8").splitlines())
        }
        required = {
            runtime_sources["runtime"].relative_to(self.bus).as_posix(),
            runtime_sources["activity"].relative_to(self.bus).as_posix(),
            runtime_sources["agent_profile"].relative_to(self.bus).as_posix(),
            "project-checkpoints/PCP-0001.md",
            "bridges/RUN-001.json",
            "index.jsonl",
        }
        self.assertTrue(required <= audit["source_hashes"].keys())
        for relative in required:
            expected = hashlib.sha256((self.bus / relative).read_bytes()).hexdigest()
            self.assertEqual(audit["source_hashes"][relative], expected)
            self.assertEqual(index_records[relative]["sha256"], expected)

    def test_existing_audit_rejects_each_runtime_audit_source_drift(self) -> None:
        source_keys = ("runtime", "activity", "agent_profile", "pcp", "bridge", "index")
        for source_key in source_keys:
            with self.subTest(source=source_key):
                # A fresh fixture keeps each drift assertion independent.
                self.setUp()
                self.close_persistent_layer("RUN-001")
                runtime_sources = self.seed_runtime_audit_sources()
                rebuilt = subprocess.run(
                    ["python3", str(SCRIPTS / "rebuild_index.py"), "--project-root", str(self.project),
                     "--governance-root", str(self.governance), "--project-id", "fixture"],
                    capture_output=True, text=True,
                )
                self.assertEqual(rebuilt.returncode, 0, rebuilt.stdout + rebuilt.stderr)
                sources = {
                    **runtime_sources,
                    "pcp": self.bus / "project-checkpoints/PCP-0001.md",
                    "bridge": self.bus / "bridges/RUN-001.json",
                    "index": self.bus / "index.jsonl",
                }
                module = load_script("finalize_project")
                module.finalize_project(self.project, ["RUN-001"], validator_runner=lambda root, runs: [], governance_root=self.governance, project_id="fixture")
                source = sources[source_key]
                source.chmod(0o600)
                source.write_bytes(source.read_bytes() + b"\nDRIFT\n")
                with self.assertRaisesRegex(ValueError, "source hash mismatch"):
                    module.finalize_project(self.project, ["RUN-001"], validator_runner=lambda root, runs: [], governance_root=self.governance, project_id="fixture")

    def test_project_checkpoint_rolls_back_if_context_publish_fails(self) -> None:
        module = load_script("create_project_checkpoint")
        original_atomic = module._atomic

        def fail_context(path, content):
            if path.name == "CURRENT_PROJECT_CONTEXT.md":
                raise OSError("context publish failed")
            original_atomic(path, content)

        from unittest.mock import patch
        with patch.object(module, "_atomic", side_effect=fail_context):
            with self.assertRaisesRegex(OSError, "context publish failed"):
                module.create_project_checkpoint(self.project, ["RUN-001"], governance_root=self.governance, project_id="fixture")
        self.assertFalse((self.bus / "project-checkpoints/PCP-0001.md").exists())

    def test_finalization_bundle_rolls_back_on_mid_write_failure(self) -> None:
        self.close_persistent_layer("RUN-001")
        module = load_script("finalize_project")
        original_atomic = module._atomic
        calls = 0

        def fail_second(path, content):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("disk full")
            original_atomic(path, content)

        from unittest.mock import patch
        with patch.object(module, "_atomic", side_effect=fail_second):
            with self.assertRaisesRegex(OSError, "disk full"):
                module.finalize_project(self.project, ["RUN-001"], validator_runner=lambda root, runs: [], governance_root=self.governance, project_id="fixture")
        for name in module.FINAL_FILES:
            self.assertFalse((self.bus / name).exists())


if __name__ == "__main__":
    unittest.main()
