from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
INIT = SKILL_DIR / "scripts" / "init_run.py"
MANAGE = SKILL_DIR / "scripts" / "manage_run.py"
PREFLIGHT = SKILL_DIR / "scripts" / "preflight_run.py"
COMPLETE_PREFLIGHT = SKILL_DIR / "scripts" / "completion_preflight.py"
FREEZE = SKILL_DIR / "scripts" / "freeze_scope.py"
DISPATCH = SKILL_DIR / "scripts" / "agent_dispatch.py"
CLAIM = SKILL_DIR / "scripts" / "agent_claim.py"
MIGRATE = SKILL_DIR / "scripts" / "migrate_run_optimization.py"
QUEUE = SKILL_DIR / "scripts" / "resource_queue.py"
TIMEOUT = SKILL_DIR / "scripts" / "recover_timeout.py"
CANDIDATE = SKILL_DIR / "scripts" / "build_candidate_index.py"
VALIDATE = SKILL_DIR / "scripts" / "validate_run.py"
COORDINATOR = SKILL_DIR / "scripts" / "coordinator.py"


class OptimizationFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.project = Path(self.tmp.name) / "project"
        (self.project / "src").mkdir(parents=True)
        self.governance = Path(self.tmp.name) / "governance"
        result = self.command(
            INIT,
            "--project-root", self.project,
            "--coordination-mode", "coordinated",
            "--governance-root", self.governance,
            "--project-id", "optimization-fixture",
            "--governance", "light",
            "--transport", "document_bus",
            "--objective", "fast-lane fixture",
            "--versioning-mode", "not_applicable",
            "--versioning-reason", "fixture has no delivery version",
            "--execution-profile", "fast",
            "--dispatch-policy", "hybrid",
            "--run-id", "RUN-OPT",
            "--user-confirmed",
        )
        self.run_dir = Path(result.stdout.strip())
        self.worker_root = self.project / "src"
        self.command(
            MANAGE, "add-agent", "--run-dir", self.run_dir,
            "--agent-id", "worker", "--runtime", "document", "--role", "Owner",
            "--readable-path", self.project, "--writable-path", self.worker_root,
            "--capability", "task_publish", "--capability", "task_claim", "--capability", "thread_claim",
        )

    def command(self, *args: object, check: bool = True) -> subprocess.CompletedProcess[str]:
        command = ["python", *[str(value) for value in args]]
        result = subprocess.run(command, capture_output=True, text=True)
        if check and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def create_task(self, task_id: str, owner: str, owned: Path, **extra: object) -> None:
        arguments: list[object] = [
            MANAGE, "create-task", "--run-dir", self.run_dir,
            "--task-id", task_id, "--title", task_id, "--objective", "fixture task",
            "--owner-agent", owner, "--owned-path", owned,
        ]
        for key, option in (("assignment_mode", "--assignment-mode"), ("published_by", "--published-by"), ("parent_task", "--parent-task")):
            if key in extra:
                arguments.extend((option, extra[key]))
        for resource_step in extra.get("resource_step", []):
            arguments.extend(("--resource-step", json.dumps(resource_step, ensure_ascii=False)))
        for eligible in extra.get("eligible_agent", []):
            arguments.extend(("--eligible-agent", eligible))
        self.command(*arguments)

    def test_init_writes_execution_policy_and_claim_directories(self) -> None:
        manifest = (self.run_dir / "manifest.yaml").read_text(encoding="utf-8")
        self.assertIn('execution_profile: "fast"', manifest)
        self.assertIn('dispatch_policy: "hybrid"', manifest)
        self.assertTrue((self.run_dir / "config" / "retry-policy.yaml").is_file())
        self.assertTrue((self.run_dir / "claims" / "tasks").is_dir())
        self.assertIn('capabilities: ["task_publish", "task_claim", "thread_claim"]', (self.run_dir / "agents.yaml").read_text(encoding="utf-8"))

    def test_preflight_reports_missing_task_graph_once(self) -> None:
        result = self.command(PREFLIGHT, "--run-dir", self.run_dir, "--dry-run", check=False)
        self.assertEqual(result.returncode, 2)
        report = json.loads(result.stdout)
        self.assertFalse(report["ready"])
        self.assertEqual(report["missing"][0]["field"], "tasks")
        self.assertEqual(report["estimated_handoffs"], 0)

    def test_scope_freeze_and_light_preflight(self) -> None:
        self.create_task("TASK-PARENT", "worker", self.project / "src" / "parent")
        frozen = self.command(FREEZE, "--run-dir", self.run_dir, "--requested-path", "src", "--target-environment", "local")
        self.assertIn("scope_path", json.loads(frozen.stdout))
        result = self.command(PREFLIGHT, "--run-dir", self.run_dir, "--task-id", "TASK-PARENT", check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        report = json.loads(result.stdout)
        self.assertTrue(report["ready"])
        self.assertEqual(report["missing"], [])

    def test_non_central_coordinator_cannot_bypass_missing_scope_freeze(self) -> None:
        self.create_task("TASK-SCOPE", "worker", self.project / "src" / "scope")
        result = self.command(COORDINATOR, "--run-dir", self.run_dir, "--dry-run")
        report = json.loads(result.stdout)
        self.assertEqual(report["reason"], "preflight_blocked")
        self.assertEqual(report["dispatches"], [])
        self.assertEqual(report["preflight"]["next_action"], "resolve_preflight")

    def test_self_service_publishes_without_coordinator_wake(self) -> None:
        self.create_task("TASK-PARENT", "worker", self.project / "src" / "parent")
        self.command(FREEZE, "--run-dir", self.run_dir, "--requested-path", "src", "--target-environment", "local")
        result = self.command(
            DISPATCH, "publish", "--run-dir", self.run_dir,
            "--publisher-agent", "worker", "--parent-task", "TASK-PARENT",
            "--task-id", "TASK-CHILD", "--title", "child", "--objective", "child work",
            "--owner-agent", "worker", "--owned-path", self.project / "src" / "child",
        )
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ready"])
        self.assertTrue((self.run_dir / "tasks" / "TASK-CHILD.md").is_file())
        events = "\n".join(path.read_text(encoding="utf-8") for path in (self.run_dir / "events").glob("*.yaml"))
        self.assertIn('from_agent: "worker"', events)
        self.assertNotIn('from_agent: "coordinator"', events)

    def test_task_and_thread_claims_are_serialized(self) -> None:
        manifest_path = self.run_dir / "manifest.yaml"
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8").replace('dispatch_policy: "hybrid"', 'dispatch_policy: "self_service"'),
            encoding="utf-8",
        )
        self.command(FREEZE, "--run-dir", self.run_dir, "--requested-path", "src", "--target-environment", "local")
        self.create_task(
            "TASK-POOL", "pool", self.project / "src" / "claim",
            assignment_mode="claimable", eligible_agent=["worker"],
        )
        first = self.command(CLAIM, "claim-task", "--run-dir", self.run_dir, "--task-id", "TASK-POOL", "--agent-id", "worker")
        first_payload = json.loads(first.stdout)
        self.assertTrue(first_payload["ready"])
        second = self.command(CLAIM, "claim-task", "--run-dir", self.run_dir, "--task-id", "TASK-POOL", "--agent-id", "worker", check=False)
        self.assertEqual(second.returncode, 2)
        self.assertTrue(json.loads(second.stdout)["conflict"])
        validation = self.command(VALIDATE, self.run_dir, "--phase", "structure", check=False)
        self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)
        events = "\n".join(path.read_text(encoding="utf-8") for path in (self.run_dir / "events").glob("*.yaml"))
        self.assertIn('event: "TASK_DISPATCHED"', events)
        thread = self.command(
            CLAIM, "claim-thread", "--run-dir", self.run_dir, "--task-id", "TASK-POOL",
            "--agent-id", "worker", "--thread-id", "THREAD-1", "--platform", "document",
            "--workspace", self.project,
        )
        thread_payload = json.loads(thread.stdout)
        self.assertTrue(thread_payload["ready"])
        released_thread = self.command(
            CLAIM, "release-thread", "--run-dir", self.run_dir,
            "--claim-ref", thread_payload["claim_path"], "--agent-id", "worker", "--reason", "handoff complete",
        )
        self.assertTrue(json.loads(released_thread.stdout)["ready"])
        released_task = self.command(
            CLAIM, "release-task", "--run-dir", self.run_dir,
            "--claim-ref", first_payload["claim_path"], "--agent-id", "worker", "--reason", "yield to recovery",
        )
        self.assertTrue(json.loads(released_task.stdout)["ready"])
        released_validation = self.command(VALIDATE, self.run_dir, "--phase", "structure", check=False)
        self.assertEqual(released_validation.returncode, 0, released_validation.stdout + released_validation.stderr)

    def test_completion_preflight_reports_owner_result_gap(self) -> None:
        self.create_task("TASK-PARENT", "worker", self.project / "src" / "parent")
        result = self.command(COMPLETE_PREFLIGHT, "--run-dir", self.run_dir, "--task-id", "TASK-PARENT", check=False)
        self.assertEqual(result.returncode, 2)
        report = json.loads(result.stdout)
        self.assertFalse(report["ready"])
        self.assertEqual(report["missing"][0]["field"], "result")

    def test_migration_is_dry_run_apply_and_recoverable(self) -> None:
        manifest_path = self.run_dir / "manifest.yaml"
        original = manifest_path.read_text(encoding="utf-8")
        legacy = "\n".join(
            line
            for line in original.splitlines()
            if not line.startswith((
                "execution_profile:", "dispatch_policy:", "preflight_required:",
                "scope_freeze_ref:", "scope_freeze_ref_sha256:",
                "self_service_parent_scope:", "retry_policy_ref:", "retry_policy_ref_sha256:",
            ))
        ) + "\n"
        manifest_path.write_text(legacy, encoding="utf-8")
        dry = self.command(MIGRATE, "--run-dir", self.run_dir, "--dry-run")
        self.assertTrue(json.loads(dry.stdout)["would_add"])
        self.assertFalse((self.run_dir / "config" / "manifest.before-optimization.yaml").exists())
        applied = self.command(MIGRATE, "--run-dir", self.run_dir, "--apply")
        self.assertTrue(json.loads(applied.stdout)["backup_path"])
        rolled = self.command(MIGRATE, "--run-dir", self.run_dir, "--rollback")
        self.assertTrue(json.loads(rolled.stdout)["rollback"])
        self.assertEqual(manifest_path.read_text(encoding="utf-8"), legacy)

    def test_resource_queue_is_owner_bound_and_fifo(self) -> None:
        self.create_task(
            "TASK-QUEUE",
            "worker",
            self.project / "src" / "queue",
            resource_step=[{"step_id": "shared", "resources": ["logical:shared"], "required": True, "queue_key": "shared"}],
        )
        first = self.command(
            QUEUE, "request", "--run-dir", self.run_dir, "--task-id", "TASK-QUEUE",
            "--agent-id", "worker", "--step-id", "step-1", "--resource", "logical:shared",
        )
        second = self.command(
            QUEUE, "request", "--run-dir", self.run_dir, "--task-id", "TASK-QUEUE",
            "--agent-id", "worker", "--step-id", "step-2", "--resource", "logical:shared",
        )
        self.assertEqual(json.loads(first.stdout)["queue_position"], 1)
        self.assertEqual(json.loads(second.stdout)["queue_position"], 2)
        preflight = self.command(PREFLIGHT, "--run-dir", self.run_dir, "--task-id", "TASK-QUEUE", check=False)
        report = json.loads(preflight.stdout)
        self.assertEqual(preflight.returncode, 2)
        self.assertEqual(report["next_action"], "wait_for_queue_grant")
        self.command(FREEZE, "--run-dir", self.run_dir, "--requested-path", "src", "--target-environment", "local")
        self.command(
            MANAGE, "lock", "--run-dir", self.run_dir, "acquire", "--lock-id", "LOCK-QUEUE",
            "--task-id", "TASK-QUEUE", "--agent-id", "worker", "--resource", "logical:shared",
            "--step-id", "step-1", "--queue-key", "shared",
        )
        granted = self.command(PREFLIGHT, "--run-dir", self.run_dir, "--task-id", "TASK-QUEUE")
        self.assertTrue(json.loads(granted.stdout)["ready"])
        self.command(MANAGE, "lock", "--run-dir", self.run_dir, "release", "--lock-id", "LOCK-QUEUE")
        self.command(
            MANAGE, "lock", "--run-dir", self.run_dir, "acquire", "--lock-id", "LOCK-QUEUE-2",
            "--task-id", "TASK-QUEUE", "--agent-id", "worker", "--resource", "logical:shared",
            "--step-id", "step-2", "--queue-key", "shared",
        )

    def test_timeout_recovery_blocks_without_fabricating_failure(self) -> None:
        self.create_task("TASK-TIMEOUT", "worker", self.project / "src" / "timeout")
        ready = self.command(
            SKILL_DIR / "scripts" / "emit_event.py", "--run-dir", self.run_dir,
            "--task-id", "TASK-TIMEOUT", "--event", "TASK_READY", "--from-agent", "coordinator",
            "--to-agent", "worker", "--summary", "ready", "--payload-file", self.run_dir / "tasks" / "TASK-TIMEOUT.md",
        )
        self.assertEqual(ready.returncode, 0, ready.stdout + ready.stderr)
        dispatched = self.command(
            SKILL_DIR / "scripts" / "emit_event.py", "--run-dir", self.run_dir,
            "--task-id", "TASK-TIMEOUT", "--event", "TASK_DISPATCHED", "--from-agent", "coordinator",
            "--to-agent", "worker", "--summary", "dispatch", "--payload-file", self.run_dir / "tasks" / "TASK-TIMEOUT.md",
        )
        self.assertEqual(dispatched.returncode, 0, dispatched.stdout + dispatched.stderr)
        manifest_path = self.run_dir / "manifest.yaml"
        manifest_path.write_text(
            manifest_path.read_text(encoding="utf-8").replace("ack_timeout_seconds: 300", "ack_timeout_seconds: 0"),
            encoding="utf-8",
        )
        recovered = self.command(
            TIMEOUT, "--run-dir", self.run_dir, "--task-id", "TASK-TIMEOUT",
            "--action", "block", "--side-effect-state", "none",
        )
        payload = json.loads(recovered.stdout)
        self.assertTrue(payload["ready"])
        self.assertTrue(Path(payload["evidence_path"]).is_file())

    def test_candidate_index_is_read_only_and_explicitly_non_authorizing(self) -> None:
        before = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }
        result = self.command(CANDIDATE, "--run-dir", self.run_dir)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["release_authority"], "project_release_adapter")
        self.assertTrue(payload["dry_run"])
        after = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*") if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertFalse(any(path.name.startswith("candidate") for path in self.project.rglob("*")))


if __name__ == "__main__":
    unittest.main()
