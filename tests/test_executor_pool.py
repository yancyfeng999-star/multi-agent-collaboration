from __future__ import annotations

import json
import hashlib
import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from executor_pool import allocate_executor, expire_stale_executors, release_executor, validate_binding  # noqa: E402
from agent_claim import claim_task, claim_thread  # noqa: E402
from wake_agent import wake_agent  # noqa: E402
from coordinator import tick  # noqa: E402


class ExecutorPoolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.project = root / "project"
        self.project.mkdir()
        (self.project / "worktree-a").mkdir()
        (self.project / "worktree-b").mkdir()
        self.run_dir = root / "governance" / "runs" / "RUN-1"
        for name in ("tasks", "events", "locks", "executors"):
            (self.run_dir / name).mkdir(parents=True, exist_ok=True)
        version_contract = self.run_dir / "versions" / "version-contract.yaml"
        version_contract.parent.mkdir(parents=True, exist_ok=True)
        version_contract.write_text(
            "\n".join(
                (
                    'run_id: "RUN-1"',
                    'release_train_id: "REL-RUN-1"',
                    'versioning_mode: "not_applicable"',
                    'baseline_version: null',
                    'target_version: null',
                    '',
                )
            ),
            encoding="utf-8",
        )
        confirmation = self.run_dir / "decisions" / "user-confirmation.yaml"
        confirmation.parent.mkdir(parents=True, exist_ok=True)
        confirmation.write_text(
            "\n".join(
                (
                    'kind: "human_gate"',
                    'scope: "run_initialization"',
                    'status: "approved"',
                    '',
                )
            ),
            encoding="utf-8",
        )
        (self.run_dir.parent.parent / "project.yaml").write_text(
            "\n".join(
                (
                    "protocol_version: 3",
                    f"project_root: {json.dumps(str(self.project))}",
                    f"allowed_roots: {json.dumps([str(self.project)])}",
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.run_dir / "manifest.yaml").write_text(
            "\n".join(
                (
                    'protocol_version: 3',
                    'run_id: "RUN-1"',
                    'status: "active"',
                    'governance: "light"',
                    'execution_profile: "emergency"',
                    'executor_policy: "capability_pool"',
                    'executor_scale_authorized: true',
                    'dispatch_policy: "self_service"',
                    'preflight_required: false',
                    'max_parallel: 2',
                    'max_attempts: 2',
                    'ack_timeout_seconds: 30',
                    'lease_seconds: 60',
                    'max_instances_per_role: {"owner": 2}',
                    'release_train_id: "REL-RUN-1"',
                    'baseline_version: null',
                    'target_version: null',
                    'versioning_mode: "not_applicable"',
                    f'version_contract_ref: {json.dumps(str(version_contract))}',
                    f'version_contract_ref_sha256: {json.dumps(hashlib.sha256(version_contract.read_bytes()).hexdigest())}',
                    f'user_confirmation_ref: {json.dumps(str(confirmation))}',
                    f'user_confirmation_ref_sha256: {json.dumps(hashlib.sha256(confirmation.read_bytes()).hexdigest())}',
                    'tasks: ["TASK-POOL"]',
                    '',
                )
            ),
            encoding="utf-8",
        )
        (self.run_dir / "agents.yaml").write_text(
            "\n".join(
                (
                    'protocol_version: 3',
                    'run_id: "RUN-1"',
                    'agents:',
                    '  - agent_id: "owner"',
                    '    runtime: "codex_thread"',
                    '    role: "owner"',
                    '    capabilities: ["frontend", "task_claim", "thread_claim", "task_publish"]',
                    '    status: "ready"',
                    '    parent_agent_id: null',
                    '    delegation_depth: 0',
                    f'    readable_paths: [{json.dumps(str(self.project))}]',
                    f'    writable_paths: [{json.dumps(str(self.project))}]',
                    '    forbidden_paths: []',
                    '    thread_id: null',
                    '    inbox: "inbox/owner"',
                    '    outbox: "outbox/owner"',
                    '    current_task: null',
                    '    handoff_to: "coordinator"',
                    '  - agent_id: "coordinator"',
                    '    runtime: "document"',
                    '    role: "coordinator"',
                    '    capabilities: ["task_publish"]',
                    '    status: "ready"',
                    '    parent_agent_id: null',
                    '    delegation_depth: 0',
                    f'    readable_paths: [{json.dumps(str(self.project))}]',
                    f'    writable_paths: [{json.dumps(str(self.project))}]',
                    '    forbidden_paths: []',
                    '    thread_id: null',
                    '    inbox: "inbox/coordinator"',
                    '    outbox: "outbox/coordinator"',
                    '    current_task: null',
                    '    handoff_to: "coordinator"',
                    '',
                )
            ),
            encoding="utf-8",
        )
        (self.run_dir / "tasks" / "TASK-A.md").write_text(
            "\n".join(
                (
                    "---",
                    'protocol_version: 3',
                    'run_id: "RUN-1"',
                    'task_id: "TASK-A"',
                    'status: "draft"',
                    'owner_agent: "owner"',
                    'assignment_mode: "fixed"',
                    'workspace_policy: "shared_no_git_mutation"',
                    f'workspace: {json.dumps(str(self.project))}',
                    f'owned_paths: [{json.dumps(str(self.project / "worktree-a"))}]',
                    'forbidden_paths: []',
                    '---',
                    '# TASK-A',
                    "",
                )
            ),
            encoding="utf-8",
        )
        (self.run_dir / "tasks" / "TASK-B.md").write_text(
            (self.run_dir / "tasks" / "TASK-A.md").read_text(encoding="utf-8")
            .replace('task_id: "TASK-A"', 'task_id: "TASK-B"')
            .replace(str(self.project / "worktree-a"), str(self.project / "worktree-b")),
            encoding="utf-8",
        )
        (self.run_dir / "tasks" / "TASK-POOL.md").write_text(
            "\n".join(
                (
                    "---",
                    'protocol_version: 3',
                    'run_id: "RUN-1"',
                    'task_id: "TASK-POOL"',
                    'status: "draft"',
                    'owner_agent: "pool"',
                    'assignment_mode: "claimable"',
                    'eligible_agents: ["owner"]',
                    'published_by: "owner"',
                    'release_train_id: "REL-RUN-1"',
                    'delivery_version: null',
                    f'version_contract_sha256: {json.dumps(hashlib.sha256((self.run_dir / "versions" / "version-contract.yaml").read_bytes()).hexdigest())}',
                    f'owned_paths: [{json.dumps(str(self.project / "worktree-b"))}]',
                    'forbidden_paths: []',
                    'dependencies: []',
                    '---',
                    '# TASK-POOL',
                    "",
                )
            ),
            encoding="utf-8",
        )

    def test_independent_tasks_get_distinct_same_role_executor_bindings(self) -> None:
        first = allocate_executor(
            self.run_dir,
            task_id="TASK-A",
            principal_agent_id="owner",
            role_ref="owner",
            required_capabilities=["frontend"],
            runtime="codex_thread",
            workspace=self.project / "worktree-a",
        )
        second = allocate_executor(
            self.run_dir,
            task_id="TASK-B",
            principal_agent_id="owner",
            role_ref="owner",
            required_capabilities=["frontend"],
            runtime="codex_thread",
            workspace=self.project / "worktree-b",
        )

        self.assertNotEqual(first["executor_id"], second["executor_id"])
        self.assertEqual(first["principal_agent_id"], second["principal_agent_id"])
        self.assertEqual(len(list((self.run_dir / "executors").glob("*.yaml"))), 2)

    def test_same_task_reuses_one_active_executor_binding(self) -> None:
        first = allocate_executor(
            self.run_dir,
            task_id="TASK-A",
            principal_agent_id="owner",
            role_ref="owner",
            required_capabilities=["frontend"],
            runtime="codex_thread",
            workspace=self.project / "worktree-a",
        )
        second = allocate_executor(
            self.run_dir,
            task_id="TASK-A",
            principal_agent_id="owner",
            role_ref="owner",
            required_capabilities=["frontend"],
            runtime="codex_thread",
            workspace=self.project / "worktree-a",
        )

        self.assertEqual(first["executor_id"], second["executor_id"])
        self.assertEqual(len(list((self.run_dir / "executors").glob("*.yaml"))), 1)

    def test_two_writer_executors_cannot_share_one_worktree(self) -> None:
        allocate_executor(
            self.run_dir,
            task_id="TASK-A",
            principal_agent_id="owner",
            role_ref="owner",
            required_capabilities=["frontend"],
            runtime="codex_thread",
            workspace=self.project / "worktree-a",
        )
        with self.assertRaisesRegex(ValueError, "workspace"):
            allocate_executor(
                self.run_dir,
                task_id="TASK-B",
                principal_agent_id="owner",
                role_ref="owner",
                required_capabilities=["frontend"],
                runtime="codex_thread",
                workspace=self.project / "worktree-a",
            )

    def test_executor_release_is_append_only_and_frees_capacity(self) -> None:
        binding = allocate_executor(
            self.run_dir,
            task_id="TASK-A",
            principal_agent_id="owner",
            role_ref="owner",
            required_capabilities=["frontend"],
            runtime="codex_thread",
            workspace=self.project / "worktree-a",
        )
        released = release_executor(self.run_dir, binding["executor_id"], "owner", "handoff complete")
        self.assertTrue(Path(released["release_path"]).is_file())
        self.assertTrue(Path(binding["binding_path"]).is_file())
        next_binding = allocate_executor(
            self.run_dir,
            task_id="TASK-B",
            principal_agent_id="owner",
            role_ref="owner",
            required_capabilities=["frontend"],
            runtime="codex_thread",
            workspace=self.project / "worktree-a",
        )
        self.assertNotEqual(binding["executor_id"], next_binding["executor_id"])

    def test_executor_release_is_idempotent_for_claim_then_result_cleanup(self) -> None:
        binding = allocate_executor(
            self.run_dir,
            task_id="TASK-A",
            principal_agent_id="owner",
            role_ref="owner",
            required_capabilities=["frontend"],
            runtime="codex_thread",
            workspace=self.project / "worktree-a",
        )
        first = release_executor(self.run_dir, binding["executor_id"], "owner", "attempt result: completed")
        second = release_executor(self.run_dir, binding["executor_id"], "owner", "claim release: handoff")
        self.assertFalse(first["already_released"])
        self.assertTrue(second["already_released"])
        self.assertEqual(first["release_path"], second["release_path"])

    def test_expired_executor_gets_immutable_expiry_record_and_frees_capacity(self) -> None:
        binding = allocate_executor(
            self.run_dir,
            task_id="TASK-A",
            principal_agent_id="owner",
            role_ref="owner",
            required_capabilities=["frontend"],
            runtime="codex_thread",
            workspace=self.project / "worktree-a",
        )
        binding_path = Path(binding["binding_path"])
        binding_path.write_text(
            binding_path.read_text(encoding="utf-8").replace(
                f'lease_expires_at: "{binding["lease_expires_at"]}"',
                'lease_expires_at: "2000-01-01T00:00:00+00:00"',
            ),
            encoding="utf-8",
        )
        expired = expire_stale_executors(self.run_dir)
        self.assertEqual(len(expired), 1)
        release = Path(expired[0]["release_path"])
        self.assertIn('kind: "executor_expiry"', release.read_text(encoding="utf-8"))
        next_binding = allocate_executor(
            self.run_dir,
            task_id="TASK-B",
            principal_agent_id="owner",
            role_ref="owner",
            required_capabilities=["frontend"],
            runtime="codex_thread",
            workspace=self.project / "worktree-a",
        )
        self.assertNotEqual(binding["executor_id"], next_binding["executor_id"])

    def test_executor_binding_validation_rejects_workspace_escape(self) -> None:
        binding = allocate_executor(
            self.run_dir,
            task_id="TASK-A",
            principal_agent_id="owner",
            role_ref="owner",
            required_capabilities=["frontend"],
            runtime="codex_thread",
            workspace=self.project / "worktree-a",
        )
        self.assertEqual(validate_binding(binding, self.project), [])
        tampered = {**binding, "workspace": str(Path(self.temp.name).parent)}
        self.assertTrue(validate_binding(tampered, self.project))

    def test_new_native_executor_requires_scale_authorization(self) -> None:
        manifest = self.run_dir / "manifest.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace("executor_scale_authorized: true", "executor_scale_authorized: false"),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(ValueError, "scale-up authorization"):
            allocate_executor(
                self.run_dir,
                task_id="TASK-A",
                principal_agent_id="owner",
                role_ref="owner",
                required_capabilities=["frontend"],
                runtime="codex_thread",
                workspace=self.project / "worktree-a",
            )

    def test_wake_operation_and_document_package_bind_executor(self) -> None:
        binding = allocate_executor(
            self.run_dir,
            task_id="TASK-A",
            principal_agent_id="owner",
            role_ref="owner",
            required_capabilities=["frontend"],
            runtime="codex_thread",
            workspace=self.project / "worktree-a",
        )
        result = wake_agent(
            self.run_dir,
            "TASK-A",
            "owner",
            executor_id=binding["executor_id"],
            requested_adapter="document",
        )

        operation = json.loads(Path(result["operation_path"]).read_text(encoding="utf-8"))
        package = json.loads(Path(result["package_path"]).read_text(encoding="utf-8"))
        self.assertEqual(operation["executor_id"], binding["executor_id"])
        self.assertEqual(package["executor_id"], binding["executor_id"])
        self.assertEqual(package["workspace"], str((self.project / "worktree-a").resolve()))

    def test_task_and_thread_claims_bind_the_selected_executor(self) -> None:
        binding = allocate_executor(
            self.run_dir,
            task_id="TASK-POOL",
            principal_agent_id="owner",
            role_ref="owner",
            required_capabilities=["frontend"],
            runtime="document",
            workspace=self.project / "worktree-b",
        )
        task_claim = claim_task(
            self.run_dir,
            "TASK-POOL",
            "owner",
            60,
            executor_id=binding["executor_id"],
        )
        self.assertEqual(task_claim["executor_id"], binding["executor_id"])
        thread_claim = claim_thread(
            self.run_dir,
            "TASK-POOL",
            "owner",
            "THREAD-POOL",
            "document",
            self.project / "worktree-b",
            60,
            executor_id=binding["executor_id"],
        )
        self.assertEqual(thread_claim["executor_id"], binding["executor_id"])

    def test_capability_pool_claim_allocates_executor_when_caller_omits_id(self) -> None:
        task_claim = claim_task(
            self.run_dir,
            "TASK-POOL",
            "owner",
            60,
        )

        self.assertTrue(task_claim["executor_id"])
        self.assertTrue(Path(task_claim["executor_id"] and task_claim["claim_path"]).is_file())
        thread_claim = claim_thread(
            self.run_dir,
            "TASK-POOL",
            "owner",
            "THREAD-AUTO",
            "document",
            self.project,
            60,
        )
        self.assertEqual(thread_claim["executor_id"], task_claim["executor_id"])

    def test_coordinator_dispatches_independent_same_role_tasks_to_distinct_instances(self) -> None:
        manifest = self.run_dir / "manifest.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace('tasks: ["TASK-POOL"]', 'tasks: ["TASK-A", "TASK-B"]'),
            encoding="utf-8",
        )
        report = tick(self.run_dir, dry_run=True, emit_events=False)

        dispatches = {item["task_id"]: item for item in report["dispatches"]}
        self.assertEqual(set(dispatches), {"TASK-A", "TASK-B"})
        self.assertNotEqual(dispatches["TASK-A"]["executor_id"], dispatches["TASK-B"]["executor_id"])


if __name__ == "__main__":
    unittest.main()
