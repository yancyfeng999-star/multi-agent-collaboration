from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "scripts" / "init_project_agents.py"
TEAM_TEMPLATE = ROOT / "assets" / "templates" / "project" / "TEAM.yaml"


class RuntimeInitializationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()

    def run_init(self, *, env: dict[str, str] | None = None, ok: bool = True) -> subprocess.CompletedProcess[str]:
        command = [
            "python3", str(INIT), "--project-root", str(self.project),
            "--project-id", "runtime-fixture", "--project-name", "Runtime Fixture",
            "--agents", "A01-coordinator,A02-worker", "--user-confirmed",
        ]
        result = subprocess.run(command, capture_output=True, text=True, env={**os.environ, **(env or {})})
        if ok and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def test_initializes_agent_profile_and_empty_runtime_log_structure(self) -> None:
        self.run_init()
        bus = self.project / ".multi-agent-collaboration"
        for agent_id in ("A01-coordinator", "A02-worker"):
            agent = bus / "agents" / agent_id
            profile = json.loads((agent / "AGENT_PROFILE.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["agent_id"], agent_id)
            self.assertEqual(profile["role"]["path"], f"agents/{agent_id}/ROLE.md")
            self.assertNotEqual(profile["role"]["sha256"], "0" * 64)
            self.assertIn("summary", profile["catalog"])
            self.assertTrue(profile["catalog"]["capabilities"])
            self.assertNotIn("current_task", json.dumps(profile["catalog"]))
            self.assertTrue((agent / "runtime" / "logs").is_dir())
            self.assertFalse((agent / "runtime" / "profiles").exists())
            self.assertEqual(list((agent / "runtime" / "logs").iterdir()), [])

    def test_team_declares_policy_without_claiming_actual_runtime(self) -> None:
        self.run_init()
        team = json.loads((self.project / ".multi-agent-collaboration" / "TEAM.yaml").read_text(encoding="utf-8"))
        policy = team["declared_model_policy"]
        self.assertEqual(policy["policy_kind"], "declared_default")
        self.assertEqual(policy["preferred_models"], [])
        self.assertIsNone(policy["preferred_provider"])
        serialized = json.dumps(team, sort_keys=True)
        for forbidden in ("actual_model", "actual_provider", "actual_runtime", "observed_actual"):
            self.assertNotIn(forbidden, serialized)
        template = json.loads(TEAM_TEMPLATE.read_text(encoding="utf-8"))
        self.assertEqual(template["declared_model_policy"]["policy_kind"], "declared_default")

    def test_failed_transaction_leaves_no_completion_marker_and_retry_succeeds(self) -> None:
        failed = self.run_init(env={"AGENT_INIT_FAIL_AFTER": "A01-coordinator"}, ok=False)
        self.assertNotEqual(failed.returncode, 0)
        bus = self.project / ".multi-agent-collaboration"
        self.assertFalse((bus / "TEAM.yaml").exists())
        self.assertFalse(bus.exists(), "fresh initialization must publish as one transaction")
        self.assertFalse((self.project / "AGENTS.md").exists())

        retry = self.run_init()
        self.assertEqual(retry.returncode, 0)
        self.assertIn("初始化完成", retry.stdout)
        self.assertTrue((bus / "TEAM.yaml").is_file())
        self.assertTrue((bus / "agents" / "A02-worker" / "AGENT_PROFILE.json").is_file())


if __name__ == "__main__":
    unittest.main()
