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
    spec = importlib.util.spec_from_file_location("create_project_checkpoint_runtime_view", SCRIPTS / "create_project_checkpoint.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_hash(value: dict) -> str:
    unhashed = dict(value)
    unhashed.pop("record_hash", None)
    payload = json.dumps(unhashed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def resolved(value: str) -> dict:
    return {
        "status": "known", "value": value, "confidence": "high",
        "selected_source_ids": ["SRC-001"], "conflict_candidate_ids": [],
        "unknown_reason_code": None, "resolution_note": "fixture",
    }


def unresolved(status: str, reason: str | None = "U001_NOT_EXPOSED") -> dict:
    return {
        "status": status, "value": None, "confidence": "none",
        "selected_source_ids": [],
        "conflict_candidate_ids": ["CND-001", "CND-002"] if status == "conflict" else [],
        "unknown_reason_code": None if status == "conflict" else reason,
        "resolution_note": "fixture",
    }


class ProjectRuntimeViewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(["git", "-C", str(self.project), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.project), "config", "user.name", "Fixture"], check=True)
        (self.project / ".seed").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.project), "add", ".seed"], check=True)
        subprocess.run(["git", "-C", str(self.project), "commit", "-qm", "fixture"], check=True)
        result = subprocess.run([
            sys.executable, str(SCRIPTS / "init_project_agents.py"),
            "--project-root", str(self.project), "--project-id", "fixture", "--project-name", "Fixture",
            "--agents", "A02-worker,A01-coordinator", "--governance", "standard", "--user-confirmed",
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.bus = self.project / ".multi-agent-collaboration"
        run = self.bus / "runs/RUN-001"
        run.mkdir(parents=True)
        (run / "manifest.yaml").write_text('run_id: "RUN-001"\n', encoding="utf-8")
        (run / "state.yaml").write_text('status: "active"\ntask_states: {}\n', encoding="utf-8")
        (run / "summary.md").write_text("# summary\n", encoding="utf-8")

    def profile(self, agent_id: str, number: int, **fields: dict) -> Path:
        profile_id = f"RP-{number:06d}"
        profile = {
            "schema_version": "1.0", "doc_type": "runtime_profile",
            "runtime_profile_id": profile_id, "agent_id": agent_id,
            "capture_status": {"code": "S002", "name": "conflicted"},
            "model": fields["model"], "provider": fields["provider"],
            "platform": fields["platform"], "session": fields["session"],
            # Candidate details may contain sensitive observations and must never be rendered.
            "candidates": [{"normalized_value": "SECRET-MUST-NOT-LEAK"}],
        }
        profile["record_hash"] = {
            "algorithm": "sha256", "canonicalization": "jcs-rfc8785", "value": canonical_hash(profile),
        }
        path = self.bus / f"agents/{agent_id}/runtime/profiles/{profile_id}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(profile), encoding="utf-8")
        return path

    def activity(self, agent_id: str, name: str, profile: Path, recorded_at: str, attempt_status: str) -> None:
        path = self.bus / f"agents/{agent_id}/activity/RUN-001/TASK-2/ATTEMPT-002/{name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "run_id": "RUN-001", "task_id": "TASK-2", "attempt_id": "ATTEMPT-002", "agent_id": agent_id,
            "recorded_at": recorded_at, "status": {"attempt_status": attempt_status},
            "runtime_profile": {"native_binding_ref": profile.relative_to(self.bus / f"agents/{agent_id}").as_posix()},
        }), encoding="utf-8")

    def test_current_context_renders_latest_actual_runtime_assignment_in_stable_agent_order(self) -> None:
        older = self.profile(
            "A02-worker", 1, model=resolved("old-model"), provider=resolved("old-provider"),
            platform=resolved("codex"), session=resolved("old-session"),
        )
        latest = self.profile(
            "A02-worker", 2, model=unresolved("conflict"), provider=unresolved("unknown"),
            platform=resolved("hermes"), session=resolved("session-2"),
        )
        self.activity("A02-worker", "ACTIVITY-000002", latest, "2026-08-06T17:30:00Z", "running")
        self.activity("A02-worker", "ACTIVITY-000001", older, "2026-08-06T16:00:00Z", "completed")

        load_module().create_project_checkpoint(self.project, ["RUN-001"])
        context = (self.bus / "CURRENT_PROJECT_CONTEXT.md").read_text(encoding="utf-8")

        heading = "| Agent | Actual model (status) | Actual provider (status) | Runtime profile ID | Platform / session | 最近 activity | Activity 状态 | Agent 状态 |"
        self.assertIn(heading, context)
        row_a01 = "| A01-coordinator | unknown | unknown | unknown | unknown / unknown | unknown | unknown | active |"
        row_a02 = "| A02-worker | conflict | unknown | RP-000002 | hermes (known) / session-2 (known) | 2026-08-06T17:30:00Z | running | active |"
        self.assertIn(row_a01, context)
        self.assertIn(row_a02, context)
        self.assertLess(context.index(row_a01), context.index(row_a02))
        self.assertNotIn("old-model", context)
        self.assertNotIn("SECRET-MUST-NOT-LEAK", context)


if __name__ == "__main__":
    unittest.main()
