from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from preflight_lib import run_preflight  # noqa: E402


SKILL_DIR = Path(__file__).resolve().parents[1]
INIT = SKILL_DIR / "scripts" / "init_run.py"
MANAGE = SKILL_DIR / "scripts" / "manage_run.py"
DISPATCH = SKILL_DIR / "scripts" / "agent_dispatch.py"
VALIDATE = SKILL_DIR / "scripts" / "validate_run.py"


class EmergencyFlowTests(unittest.TestCase):
    def test_init_emergency_run_defaults_to_task_scope_and_executor_pool(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            governance = root / "governance"
            project.mkdir()
            command = [
                "python3",
                str(INIT),
                "--project-root", str(project),
                "--coordination-mode", "coordinated",
                "--governance-root", str(governance),
                "--project-id", "emergency-fixture",
                "--project-name", "Emergency Fixture",
                "--governance", "light",
                "--execution-profile", "emergency",
                "--dispatch-policy", "hybrid",
                "--transport", "document_bus",
                "--objective", "Emergency flow test",
                "--max-parallel", "2",
                "--versioning-mode", "not_applicable",
                "--versioning-reason", "The fixture has no release artifact",
                "--run-id", "RUN-EMERGENCY",
                "--user-confirmed",
            ]
            result = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            run_dir = Path(result.stdout.strip())
            manifest = (run_dir / "manifest.yaml").read_text(encoding="utf-8")
            self.assertIn('execution_profile: "emergency"', manifest)
            self.assertIn('preflight_scope: "task"', manifest)
            self.assertIn('executor_policy: "capability_pool"', manifest)
            self.assertIn("executor_scale_authorized: false", manifest)
            self.assertTrue((run_dir / "executors").is_dir())

    def test_emergency_light_preflight_does_not_require_run_scope_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            governance = root / "governance"
            project.mkdir()
            init = subprocess.run(
                [
                    "python3", str(INIT), "--project-root", str(project),
                    "--coordination-mode", "coordinated", "--governance-root", str(governance),
                    "--project-id", "emergency-preflight", "--project-name", "Emergency Preflight",
                    "--governance", "light", "--execution-profile", "emergency",
                    "--dispatch-policy", "hybrid", "--transport", "document_bus",
                    "--objective", "Emergency preflight", "--versioning-mode", "not_applicable",
                    "--versioning-reason", "No release artifact", "--run-id", "RUN-EMERGENCY-PREFLIGHT",
                    "--user-confirmed",
                ], capture_output=True, text=True,
            )
            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            run_dir = Path(init.stdout.strip())
            report = run_preflight(run_dir, ["TASK-MISSING"])
            self.assertNotIn("scope_freeze_ref", {item["field"] for item in report["missing"]})

    def test_emergency_self_service_can_publish_with_parent_scope_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            governance = root / "governance"
            (project / "src").mkdir(parents=True)
            (project / "src" / "child").mkdir()
            init = subprocess.run(
                [
                    "python3", str(INIT), "--project-root", str(project),
                    "--coordination-mode", "coordinated", "--governance-root", str(governance),
                    "--project-id", "emergency-self-service", "--project-name", "Emergency Self Service",
                    "--governance", "light", "--execution-profile", "emergency",
                    "--dispatch-policy", "self_service", "--transport", "document_bus",
                    "--objective", "Emergency self service", "--versioning-mode", "not_applicable",
                    "--versioning-reason", "No release artifact", "--run-id", "RUN-EMERGENCY-SELF",
                    "--user-confirmed",
                ], capture_output=True, text=True,
            )
            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            run_dir = Path(init.stdout.strip())
            def command(*args: object) -> subprocess.CompletedProcess[str]:
                result = subprocess.run(["python3", *map(str, args)], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                return result
            command(
                MANAGE, "add-agent", "--run-dir", run_dir, "--agent-id", "worker",
                "--runtime", "document", "--role", "owner", "--readable-path", project,
                "--writable-path", project, "--capability", "task_publish",
            )
            command(
                MANAGE, "create-task", "--run-dir", run_dir, "--task-id", "TASK-PARENT",
                "--title", "parent", "--objective", "parent", "--owner-agent", "worker",
                "--owned-path", project / "src",
            )
            published = command(
                DISPATCH, "publish", "--run-dir", run_dir, "--publisher-agent", "worker",
                "--parent-task", "TASK-PARENT", "--task-id", "TASK-CHILD",
                "--title", "child", "--objective", "child", "--owner-agent", "worker",
                "--owned-path", project / "src" / "child",
            )
            payload = json.loads(published.stdout)
            self.assertTrue(payload["ready"])
            self.assertTrue(payload["dispatch"])
            validation = subprocess.run(
                ["python3", str(VALIDATE), str(run_dir), "--phase", "structure"],
                capture_output=True, text=True,
            )
            self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)

    def test_task_creation_persists_capability_and_conflict_fields(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            governance = root / "governance"
            (project / "src").mkdir(parents=True)
            init = subprocess.run(
                [
                    "python3", str(INIT), "--project-root", str(project),
                    "--coordination-mode", "coordinated", "--governance-root", str(governance),
                    "--project-id", "task-fields", "--project-name", "Task Fields",
                    "--governance", "light", "--execution-profile", "emergency",
                    "--dispatch-policy", "hybrid", "--transport", "document_bus",
                    "--objective", "Task fields", "--max-parallel", "2",
                    "--versioning-mode", "not_applicable", "--versioning-reason", "No release artifact",
                    "--run-id", "RUN-TASK-FIELDS", "--user-confirmed",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(init.returncode, 0, init.stdout + init.stderr)
            run_dir = Path(init.stdout.strip())
            add_agent = subprocess.run(
                [
                    "python3", str(MANAGE), "add-agent", "--run-dir", str(run_dir),
                    "--agent-id", "owner", "--runtime", "document", "--role", "owner",
                    "--readable-path", str(project), "--writable-path", str(project),
                    "--capability", "frontend",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(add_agent.returncode, 0, add_agent.stdout + add_agent.stderr)
            created = subprocess.run(
                [
                    "python3", str(MANAGE), "create-task", "--run-dir", str(run_dir),
                    "--task-id", "TASK-FIELDS", "--title", "fields", "--objective", "fields",
                    "--owner-agent", "owner", "--owned-path", str(project / "src"),
                    "--role-ref", "owner", "--required-capability", "frontend",
                    "--logical-resource", "logical:db/schema", "--workspace", str(project),
                    "--workspace-policy", "shared_no_git_mutation", "--release-lane", "none",
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(created.returncode, 0, created.stdout + created.stderr)
            task = Path(created.stdout.strip()).read_text(encoding="utf-8")
            self.assertIn('role_ref: "owner"', task)
            self.assertIn('required_capabilities: ["frontend"]', task)
            self.assertIn('logical_resources: ["logical:db/schema"]', task)
            self.assertIn('workspace_policy: "shared_no_git_mutation"', task)


if __name__ == "__main__":
    unittest.main()
