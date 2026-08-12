from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def load_module():
    spec = importlib.util.spec_from_file_location("create_project_checkpoint_runtime", SCRIPTS / "create_project_checkpoint.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_hash(value: dict) -> str:
    unhashed = dict(value)
    unhashed.pop("record_hash", None)
    return hashlib.sha256(json.dumps(unhashed, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def frontmatter(path: Path) -> dict:
    block = path.read_text(encoding="utf-8").split("---\n", 2)[1]
    result = {}
    for line in block.splitlines():
        key, raw = line.split(":", 1)
        raw = raw.strip()
        result[key] = json.loads(raw) if raw.startswith(('"', "[", "{")) or raw in {"null", "true", "false"} else raw
    return result


def resolved(value: str) -> dict:
    return {"status": "known", "value": value, "confidence": "high", "selected_source_ids": ["SRC-001"],
            "conflict_candidate_ids": [], "unknown_reason_code": None, "resolution_note": "fixture"}


class ProjectCheckpointRuntimeTests(unittest.TestCase):
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
        result = subprocess.run([
            sys.executable, str(SCRIPTS / "init_project_agents.py"), "--project-root", str(self.project),
            "--project-id", "fixture", "--project-name", "Fixture", "--agents", "A02-worker,A01-coordinator",
            "--governance", "standard", "--governance-root", str(self.governance), "--user-confirmed",
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.bus = self.governance / "projects" / "fixture"
        run = self.bus / "runs/RUN-001"
        run.mkdir(parents=True)
        (run / "manifest.yaml").write_text('run_id: "RUN-001"\n', encoding="utf-8")
        (run / "state.yaml").write_text('status: "active"\ntask_states: {"TASK-2": "running"}\n', encoding="utf-8")
        (run / "summary.md").write_text("# summary\n", encoding="utf-8")

    def profile(self, agent_id: str, number: int, model: str, provider: str) -> tuple[Path, str]:
        profile_id = f"RP-{number:06d}"
        profile = {
            "schema_version": "1.0", "doc_type": "runtime_profile", "runtime_profile_id": profile_id,
            "agent_id": agent_id, "model": resolved(model), "provider": resolved(provider),
        }
        digest = canonical_hash(profile)
        profile["record_hash"] = {"algorithm": "sha256", "canonicalization": "jcs-rfc8785", "value": digest}
        path = self.bus / f"agents/{agent_id}/runtime/profiles/{profile_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(profile), encoding="utf-8")
        return path, digest

    def activity(self, agent_id: str, name: str, profile: Path) -> Path:
        record = {
            "run_id": "RUN-001", "task_id": "TASK-2", "attempt_id": "ATTEMPT-002", "agent_id": agent_id,
            "runtime_profile": {"native_binding_ref": profile.relative_to(self.bus / f"agents/{agent_id}").as_posix()},
        }
        path = self.bus / f"agents/{agent_id}/activity/RUN-001/TASK-2/ATTEMPT-002/2026/08/06/{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record), encoding="utf-8")
        return path

    def handoff(self, agent_id: str, name: str, *, run_id: str = "RUN-001", declared_agent: str | None = None) -> Path:
        path = self.bus / f"agents/{agent_id}/handoffs/{name}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f'---\nagent_id: {json.dumps(declared_agent or agent_id)}\n'
            f'activity_record_path: {json.dumps(f"agents/{agent_id}/activity/{run_id}/TASK-2/ATTEMPT-002/2026/08/06/ACTIVITY-000001.json")}\n'
            '---\n# Handoff\n',
            encoding="utf-8",
        )
        return path

    def test_snapshots_stage_profiles_actual_runtime_and_evidence_in_stable_deduplicated_order(self) -> None:
        profile_2, hash_2 = self.profile("A02-worker", 2, "model-2", "provider-2")
        profile_1, hash_1 = self.profile("A02-worker", 1, "model-1", "provider-1")
        activity_b = self.activity("A02-worker", "ACTIVITY-000002", profile_2)
        activity_a = self.activity("A02-worker", "ACTIVITY-000001", profile_1)
        # A duplicate activity binding must not duplicate the runtime profile snapshot.
        activity_c = self.activity("A02-worker", "ACTIVITY-000003", profile_1)
        handoff_b = self.handoff("A02-worker", "HO-0002")
        handoff_a = self.handoff("A02-worker", "HO-0001")
        self.handoff("A02-worker", "OLD", run_id="RUN-OLD")

        result = load_module().create_project_checkpoint(
            self.project, ["RUN-001"], governance_root=self.governance, project_id="fixture",
        )
        meta = frontmatter(self.bus / result["path"])
        snapshots = meta["agent_runtime_snapshots"]
        self.assertEqual([item["agent_id"] for item in snapshots], ["A01-coordinator", "A02-worker"])
        worker = snapshots[1]
        refs = [f"agents/A02-worker/runtime/profiles/RP-{number:06d}.json" for number in (1, 2)]
        self.assertEqual(worker["runtime_profiles"], [
            {"ref": refs[0], "sha256": hash_1, "actual_model": {"status": "known", "value": "model-1"},
             "actual_provider": {"status": "known", "value": "provider-1"}},
            {"ref": refs[1], "sha256": hash_2, "actual_model": {"status": "known", "value": "model-2"},
             "actual_provider": {"status": "known", "value": "provider-2"}},
        ])
        expected_activities = sorted((p.relative_to(self.bus).as_posix(), file_hash(p)) for p in (activity_a, activity_b, activity_c))
        self.assertEqual(worker["activity_refs"], [{"ref": ref, "sha256": digest} for ref, digest in expected_activities])
        expected_handoffs = sorted((p.relative_to(self.bus).as_posix(), file_hash(p)) for p in (handoff_a, handoff_b))
        self.assertEqual(worker["handoff_refs"], [{"ref": ref, "sha256": digest} for ref, digest in expected_handoffs])
        self.assertNotIn("CURRENT_PROJECT_CONTEXT.md", meta["source_hashes"])
        for item in worker["activity_refs"] + worker["handoff_refs"]:
            self.assertEqual(meta["source_hashes"][item["ref"]], item["sha256"])

    def test_rejects_cross_agent_runtime_profile_and_handoff_ownership(self) -> None:
        foreign, _ = self.profile("A01-coordinator", 1, "model", "provider")
        activity = self.bus / "agents/A02-worker/activity/RUN-001/TASK-2/ATTEMPT-002/record.json"
        activity.parent.mkdir(parents=True, exist_ok=True)
        activity.write_text(json.dumps({
            "run_id": "RUN-001", "task_id": "TASK-2", "attempt_id": "ATTEMPT-002", "agent_id": "A02-worker",
            "runtime_profile": {"native_binding_ref": "../A01-coordinator/runtime/profiles/RP-000001.json"},
        }), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "runtime profile.*outside|runtime profile.*belong"):
            load_module().create_project_checkpoint(
                self.project, ["RUN-001"], governance_root=self.governance, project_id="fixture",
            )
        activity.unlink()
        self.handoff("A02-worker", "BAD", declared_agent="A01-coordinator")
        with self.assertRaisesRegex(ValueError, "handoff.*agent"):
            load_module().create_project_checkpoint(
                self.project, ["RUN-001"], governance_root=self.governance, project_id="fixture",
            )

    def test_rejects_runtime_profile_identity_or_record_hash_mismatch(self) -> None:
        profile, _ = self.profile("A02-worker", 1, "model", "provider")
        self.activity("A02-worker", "ACTIVITY-000001", profile)
        payload = json.loads(profile.read_text(encoding="utf-8"))
        payload["model"] = resolved("tampered")
        profile.write_text(json.dumps(payload), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "runtime profile hash"):
            load_module().create_project_checkpoint(
                self.project, ["RUN-001"], governance_root=self.governance, project_id="fixture",
            )

    def test_schema_requires_agent_runtime_snapshot_contract(self) -> None:
        schema = json.loads((ROOT / "assets/schemas/project-checkpoint.schema.json").read_text(encoding="utf-8"))
        self.assertIn("agent_runtime_snapshots", schema["required"])
        snapshots = schema["properties"]["agent_runtime_snapshots"]
        self.assertTrue(snapshots["uniqueItems"])
        required = schema["$defs"]["agentRuntimeSnapshot"]["required"]
        self.assertEqual(required, ["agent_id", "runtime_profiles", "activity_refs", "handoff_refs"])
        profile = schema["$defs"]["runtimeProfileSnapshot"]
        self.assertEqual(profile["required"], ["ref", "sha256", "actual_model", "actual_provider"])


if __name__ == "__main__":
    unittest.main()
