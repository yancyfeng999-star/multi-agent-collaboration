from __future__ import annotations

import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
INIT = SKILL_DIR / "scripts" / "init_run.py"
MANAGE = SKILL_DIR / "scripts" / "manage_run.py"
EMIT = SKILL_DIR / "scripts" / "emit_event.py"
VALIDATE = SKILL_DIR / "scripts" / "validate_run.py"


class ProtocolV3Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        (self.project / "src").mkdir()
        (self.project / "src" / "module").mkdir()
        (self.project / "outside").mkdir()
        self.run_dir = self.initialize("RUN-TEST", "light")

    def command(
        self,
        *arguments: str,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = list(arguments) if arguments and arguments[0] == "git" else ["python3", *arguments]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
        )
        if check and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def initialize(
        self,
        run_id: str,
        governance: str,
        *,
        max_attempts: int = 3,
        tracked_version: bool = False,
    ) -> Path:
        arguments = [
            str(INIT),
            "--project-root",
            str(self.project),
            "--governance",
            governance,
            "--transport",
            "document_bus",
            "--objective",
            "protocol v3 test",
            "--max-parallel",
            "2",
            "--max-attempts",
            str(max_attempts),
            "--run-id",
            run_id,
            "--versioning-mode",
            "tracked" if tracked_version else "not_applicable",
            "--versioning-reason",
            (
                "Test release produces a versioned deliverable"
                if tracked_version
                else "Protocol fixture has no versioned deliverable"
            ),
            "--user-confirmed",
        ]
        if tracked_version:
            arguments.extend(
                (
                    "--version-scheme",
                    "semver",
                    "--baseline-version",
                    "1.0.0",
                    "--target-version",
                    "1.0.1",
                    "--version-source",
                    str(self.project / "VERSION"),
                )
            )
        result = self.command(*arguments)
        return Path(result.stdout.strip())

    def add_agent(
        self,
        agent_id: str,
        *,
        runtime: str = "document",
        writable: Path | None = None,
        parent: str | None = None,
        depth: int = 0,
        role: str | None = None,
        run_dir: Path | None = None,
        forbidden: Path | None = None,
    ) -> None:
        run_dir = run_dir or self.run_dir
        arguments = [
            str(MANAGE),
            "add-agent",
            "--run-dir",
            str(run_dir),
            "--agent-id",
            agent_id,
            "--runtime",
            runtime,
            "--role",
            role or agent_id,
            "--readable-path",
            str(self.project),
            "--delegation-depth",
            str(depth),
        ]
        if writable:
            arguments.extend(("--writable-path", str(writable)))
        if parent:
            arguments.extend(("--parent-agent-id", parent))
        if forbidden:
            arguments.extend(("--forbidden-path", str(forbidden)))
        self.command(*arguments)

    def create_task(
        self,
        *,
        task_id: str = "TASK-001",
        owner: str = "owner",
        owned: Path | None = None,
        reviewer: str | None = None,
        qa: str | None = None,
        release: str | None = None,
        risk: str | None = None,
        gate: str | None = None,
        dependency: str | None = None,
        run_dir: Path | None = None,
    ) -> Path:
        run_dir = run_dir or self.run_dir
        arguments = [
            str(MANAGE),
            "create-task",
            "--run-dir",
            str(run_dir),
            "--task-id",
            task_id,
            "--title",
            task_id,
            "--objective",
            "Complete the test task",
            "--owner-agent",
            owner,
            "--owned-path",
            str(owned or self.project / "src"),
            "--acceptance",
            "Result is persisted",
            "--verification",
            "Run validator",
        ]
        if reviewer:
            arguments.extend(("--reviewer-agent", reviewer))
        if qa:
            arguments.extend(("--qa-agent", qa))
        if release:
            arguments.extend(("--release-agent", release))
        if risk:
            arguments.extend(("--risk-flag", risk))
        if gate:
            arguments.extend(("--human-gate", gate))
        if dependency:
            arguments.extend(("--dependency", dependency))
        result = self.command(*arguments)
        return Path(result.stdout.strip())

    def emit(
        self,
        event: str,
        *,
        task_id: str = "TASK-001",
        from_agent: str = "coordinator",
        to_agent: str = "owner",
        payload: Path | None = None,
        event_key: str | None = None,
        run_dir: Path | None = None,
        check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        run_dir = run_dir or self.run_dir
        arguments = [
            str(EMIT),
            "--run-dir",
            str(run_dir),
            "--task-id",
            task_id,
            "--event",
            event,
            "--from-agent",
            from_agent,
            "--to-agent",
            to_agent,
            "--summary",
            event,
        ]
        if payload:
            arguments.extend(("--payload-file", str(payload)))
        if event_key:
            arguments.extend(("--event-key", event_key))
        return self.command(*arguments, check=check)

    def validate(
        self,
        *,
        run_dir: Path | None = None,
        phase: str = "auto",
    ) -> subprocess.CompletedProcess[str]:
        return self.command(
            str(VALIDATE),
            str(run_dir or self.run_dir),
            "--phase",
            phase,
            check=False,
        )

    def prepare_owner_task(self) -> Path:
        self.add_agent("owner", writable=self.project / "src")
        return self.create_task()

    def prepare_running_task(self) -> tuple[Path, Path]:
        task = self.prepare_owner_task()
        self.emit("TASK_READY", payload=task)
        self.emit("TASK_DISPATCHED", payload=task)
        ack_result = self.command(
            str(MANAGE),
            "write-ack",
            "--run-dir",
            str(self.run_dir),
            "--task-id",
            "TASK-001",
            "--agent-id",
            "owner",
            "--idempotency-key",
            "ACK-1",
        )
        ack = Path(ack_result.stdout.strip())
        self.emit("ACK", from_agent="owner", to_agent="coordinator", payload=ack)
        lease = self.write_lease("LEASE-001")
        self.emit("LEASE_ACQUIRED", payload=lease)
        return task, ack

    def write_lease(
        self,
        lease_id: str,
        *,
        run_dir: Path | None = None,
        agent_id: str = "owner",
        attempt_id: str = "ATTEMPT-001",
    ) -> Path:
        run_dir = run_dir or self.run_dir
        return Path(
            self.command(
                str(MANAGE),
                "write-lease",
                "--run-dir",
                str(run_dir),
                "--task-id",
                "TASK-001",
                "--agent-id",
                agent_id,
                "--lease-id",
                lease_id,
                "--attempt-id",
                attempt_id,
            ).stdout.strip()
        )

    def complete_light_task(self) -> Path:
        self.prepare_running_task()
        result = Path(
            self.command(
                str(MANAGE),
                "write-result",
                "--run-dir",
                str(self.run_dir),
                "--task-id",
                "TASK-001",
                "--agent-id",
                "owner",
                "--status",
                "completed",
                "--outcome",
                "Done",
                "--verification-status",
                "passed",
                "--risk-summary",
                "No residual risk",
                "--rollback-plan",
                "No project mutation",
            ).stdout.strip()
        )
        self.emit(
            "HANDOFF_READY",
            from_agent="owner",
            to_agent="coordinator",
            payload=result,
        )
        self.emit("TASK_COMPLETED", payload=result)
        return result

    def test_initialization_requires_explicit_confirmation(self) -> None:
        result = self.command(
            str(INIT),
            "--project-root",
            str(self.project),
            "--governance",
            "light",
            "--transport",
            "document_bus",
            "--objective",
            "must fail",
            "--run-id",
            "RUN-NO-CONFIRM",
            "--versioning-mode",
            "not_applicable",
            "--versioning-reason",
            "No versioned deliverable",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--user-confirmed is required", result.stderr)

    def test_initialization_requires_explicit_versioning_assessment(self) -> None:
        result = self.command(
            str(INIT),
            "--project-root",
            str(self.project),
            "--governance",
            "light",
            "--transport",
            "document_bus",
            "--objective",
            "must assess versioning",
            "--versioning-reason",
            "Assessment is intentionally missing",
            "--run-id",
            "RUN-NO-VERSION-ASSESSMENT",
            "--user-confirmed",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("--versioning-mode", result.stderr)

    def test_tracked_version_contract_binds_tasks_and_candidates(self) -> None:
        (self.project / "VERSION").write_text("1.0.0\n", encoding="utf-8")
        tracked = self.initialize(
            "RUN-VERSIONED",
            "light",
            tracked_version=True,
        )
        self.add_agent(
            "versioned-owner",
            writable=self.project / "src",
            run_dir=tracked,
        )
        task = self.create_task(
            owner="versioned-owner",
            run_dir=tracked,
        )
        task_text = task.read_text(encoding="utf-8")
        self.assertIn('release_train_id: "REL-VERSIONED"', task_text)
        self.assertIn('delivery_version: "1.0.1"', task_text)
        first = Path(
            self.command(
                str(MANAGE),
                "record-release-candidate",
                "--run-dir",
                str(tracked),
                "--summary",
                "First candidate",
            ).stdout.strip()
        )
        second = Path(
            self.command(
                str(MANAGE),
                "record-release-candidate",
                "--run-dir",
                str(tracked),
                "--summary",
                "Second candidate",
            ).stdout.strip()
        )
        self.assertEqual(first.name, "RC-001.yaml")
        self.assertEqual(second.name, "RC-002.yaml")
        self.assertIn(
            'candidate_version: "1.0.1-rc.2"',
            second.read_text(encoding="utf-8"),
        )
        validation = self.validate(run_dir=tracked, phase="structure")
        self.assertEqual(validation.returncode, 0, validation.stdout)

    def test_tracked_baseline_must_match_declared_version_source(self) -> None:
        version_file = self.project / "VERSION"
        version_file.write_text("2.0.0\n", encoding="utf-8")
        result = self.command(
            str(INIT),
            "--project-root",
            str(self.project),
            "--governance",
            "light",
            "--transport",
            "document_bus",
            "--objective",
            "reject invented baseline",
            "--run-id",
            "RUN-WRONG-BASELINE",
            "--versioning-mode",
            "tracked",
            "--version-scheme",
            "semver",
            "--baseline-version",
            "1.0.0",
            "--target-version",
            "1.0.1",
            "--version-source",
            str(version_file),
            "--versioning-reason",
            "Versioned fixture",
            "--user-confirmed",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("baseline version was not found", result.stderr)

    def test_version_contract_tampering_blocks_dispatch(self) -> None:
        task = self.prepare_owner_task()
        contract = self.run_dir / "versions" / "version-contract.yaml"
        contract.write_text(
            contract.read_text(encoding="utf-8").replace(
                "Protocol fixture has no versioned deliverable",
                "Silently changed version decision",
            ),
            encoding="utf-8",
        )
        result = self.emit("TASK_READY", payload=task, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("version contract", result.stderr)

    def test_tracked_version_source_drift_blocks_dispatch(self) -> None:
        version_file = self.project / "VERSION"
        version_file.write_text("1.0.0\n", encoding="utf-8")
        tracked = self.initialize(
            "RUN-VERSION-DRIFT",
            "light",
            tracked_version=True,
        )
        self.add_agent(
            "versioned-owner",
            writable=self.project / "src",
            run_dir=tracked,
        )
        task = self.create_task(
            owner="versioned-owner",
            run_dir=tracked,
        )
        version_file.write_text("unexpected\n", encoding="utf-8")
        result = self.emit(
            "TASK_READY",
            task_id="TASK-001",
            to_agent="versioned-owner",
            payload=task,
            run_dir=tracked,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reserved baseline", result.stderr)

    def test_unversioned_run_rejects_release_candidate(self) -> None:
        result = self.command(
            str(MANAGE),
            "record-release-candidate",
            "--run-dir",
            str(self.run_dir),
            "--summary",
            "Must not exist",
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("tracked versioning", result.stderr)

    def test_standard_coordinator_can_combine_reviewer_and_qa(self) -> None:
        standard = self.initialize("RUN-COMBINED-QUALITY", "standard")
        self.add_agent(
            "owner",
            writable=self.project / "src",
            run_dir=standard,
        )
        task = self.create_task(
            owner="owner",
            reviewer="coordinator",
            qa="coordinator",
            run_dir=standard,
        )
        emitted = self.emit(
            "TASK_READY",
            to_agent="owner",
            payload=task,
            run_dir=standard,
        )
        self.assertEqual(emitted.returncode, 0)
        validation = self.validate(run_dir=standard, phase="dispatch")
        self.assertEqual(validation.returncode, 0, validation.stdout)

    def test_standard_rejects_owner_self_review(self) -> None:
        standard = self.initialize("RUN-SELF-REVIEW", "standard")
        self.add_agent(
            "owner",
            writable=self.project / "src",
            run_dir=standard,
        )
        task = self.create_task(
            owner="owner",
            reviewer="owner",
            qa="owner",
            run_dir=standard,
        )
        result = self.emit(
            "TASK_READY",
            to_agent="owner",
            payload=task,
            run_dir=standard,
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("independent from Owner", result.stderr)

    def test_agent_registry_is_isolated_per_run(self) -> None:
        self.add_agent("run-one-owner", writable=self.project / "src")
        second = self.initialize("RUN-SECOND", "light")
        first_agents = (self.run_dir / "agents.yaml").read_text(encoding="utf-8")
        second_agents = (second / "agents.yaml").read_text(encoding="utf-8")
        self.assertIn("run-one-owner", first_agents)
        self.assertNotIn("run-one-owner", second_agents)
        self.assertFalse((self.project / ".multi-agent-collaboration" / "agents.yaml").exists())

    def test_valid_light_lifecycle_reduces_and_archives(self) -> None:
        self.complete_light_task()
        validation = self.validate(phase="completion")
        self.assertEqual(validation.returncode, 0, validation.stdout)
        state = (self.run_dir / "state.yaml").read_text(encoding="utf-8")
        self.assertIn('"TASK-001": "completed"', state)
        archived = self.command(
            str(MANAGE),
            "archive-run",
            "--run-dir",
            str(self.run_dir),
        )
        self.assertTrue(Path(archived.stdout.strip()).is_file())
        self.assertEqual(self.validate().returncode, 0)
        rejected = self.command(
            str(MANAGE),
            "record-evidence",
            "--run-dir",
            str(self.run_dir),
            "--evidence-id",
            "POST-ARCHIVE",
            "--kind",
            "verification",
            "--status",
            "passed",
            "--agent-id",
            "coordinator",
            "--summary",
            "Must be rejected",
            check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("read-only", rejected.stderr)

    def test_task_ready_rejects_unrelated_payload(self) -> None:
        self.prepare_owner_task()
        unrelated = self.run_dir / "artifacts" / "unrelated.txt"
        unrelated.write_text("not the task", encoding="utf-8")
        result = self.emit("TASK_READY", payload=unrelated, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exact frozen task", result.stderr)

    def test_all_agent_owned_paths_are_enforced(self) -> None:
        task = self.prepare_owner_task()
        content = task.read_text(encoding="utf-8").replace(
            str(self.project / "src"),
            str(self.project / "outside"),
        )
        task.write_text(content, encoding="utf-8")
        validation = self.validate(phase="structure")
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("owned path exceeds owner scope", validation.stdout)
        event = self.emit("TASK_READY", payload=task, check=False)
        self.assertNotEqual(event.returncode, 0)

    def test_completed_empty_run_is_rejected(self) -> None:
        manifest = self.run_dir / "manifest.yaml"
        state = self.run_dir / "state.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                'status: "initializing"',
                'status: "completed"',
            ),
            encoding="utf-8",
        )
        state.write_text(
            state.read_text(encoding="utf-8").replace(
                'status: "initializing"',
                'status: "completed"',
            ),
            encoding="utf-8",
        )
        validation = self.validate(phase="completion")
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("requires at least one task", validation.stdout)

    def test_invalid_numeric_policy_fails_without_validator_crash(self) -> None:
        manifest = self.run_dir / "manifest.yaml"
        manifest.write_text(
            manifest.read_text(encoding="utf-8").replace(
                "max_attempts: 3",
                'max_attempts: "invalid"',
            ),
            encoding="utf-8",
        )
        validation = self.validate(phase="structure")
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("numeric policies", validation.stdout)
        self.assertNotIn("Traceback", validation.stderr)

    def test_user_approval_has_a_legal_resume_path(self) -> None:
        task = self.prepare_owner_task()
        self.emit("TASK_READY", payload=task)
        pending = Path(
            self.command(
                str(MANAGE),
                "record-gate",
                "--run-dir",
                str(self.run_dir),
                "--gate-id",
                "GATE-QUESTION",
                "--scope",
                "business_rule",
                "--status",
                "pending",
                "--task-id",
                "TASK-001",
                "--summary",
                "Need user decision",
            ).stdout.strip()
        )
        self.emit("WAITING_USER_APPROVAL", payload=pending)
        unconfirmed = self.command(
            str(MANAGE),
            "record-gate",
            "--run-dir",
            str(self.run_dir),
            "--gate-id",
            "GATE-UNCONFIRMED",
            "--scope",
            "business_rule",
            "--status",
            "approved",
            "--task-id",
            "TASK-001",
            "--summary",
            "Must fail",
            check=False,
        )
        self.assertNotEqual(unconfirmed.returncode, 0)
        self.assertIn("--human-confirmed", unconfirmed.stderr)
        approved = Path(
            self.command(
                str(MANAGE),
                "record-gate",
                "--run-dir",
                str(self.run_dir),
                "--gate-id",
                "GATE-QUESTION-APPROVED",
                "--scope",
                "business_rule",
                "--status",
                "approved",
                "--human-confirmed",
                "--task-id",
                "TASK-001",
                "--summary",
                "User approved",
            ).stdout.strip()
        )
        self.emit("APPROVAL_GRANTED", payload=approved)
        state = (self.run_dir / "state.yaml").read_text(encoding="utf-8")
        self.assertIn('"TASK-001": "ready"', state)
        self.assertEqual(self.validate(phase="dispatch").returncode, 0)

    def test_review_changes_can_resume_without_mutating_frozen_task(self) -> None:
        standard = self.initialize("RUN-REVIEW-RESUME", "standard")
        self.add_agent("owner", writable=self.project / "src", run_dir=standard)
        self.add_agent("reviewer", run_dir=standard)
        self.add_agent("qa", run_dir=standard)
        task = self.create_task(
            owner="owner",
            reviewer="reviewer",
            qa="qa",
            run_dir=standard,
        )
        frozen_hash = hashlib.sha256(task.read_bytes()).hexdigest()
        self.emit("TASK_READY", payload=task, run_dir=standard)
        self.emit("TASK_DISPATCHED", payload=task, run_dir=standard)
        ack = Path(
            self.command(
                str(MANAGE),
                "write-ack",
                "--run-dir",
                str(standard),
                "--task-id",
                "TASK-001",
                "--agent-id",
                "owner",
                "--idempotency-key",
                "ACK-REVIEW-001",
            ).stdout.strip()
        )
        self.emit(
            "ACK",
            from_agent="owner",
            to_agent="coordinator",
            payload=ack,
            run_dir=standard,
        )
        lease = self.write_lease("LEASE-REVIEW-001", run_dir=standard)
        self.emit("LEASE_ACQUIRED", payload=lease, run_dir=standard)
        result = Path(
            self.command(
                str(MANAGE),
                "write-result",
                "--run-dir",
                str(standard),
                "--task-id",
                "TASK-001",
                "--agent-id",
                "owner",
                "--status",
                "completed",
                "--outcome",
                "Ready for review",
                "--uncommitted-reason",
                "Review fixture",
                "--verification-status",
                "not_run",
                "--risk-summary",
                "Pending review",
                "--rollback-plan",
                "Discard fixture",
            ).stdout.strip()
        )
        self.emit(
            "HANDOFF_READY",
            from_agent="owner",
            to_agent="reviewer",
            payload=result,
            run_dir=standard,
        )
        self.emit(
            "REVIEW_STARTED",
            from_agent="reviewer",
            to_agent="owner",
            run_dir=standard,
        )
        changes = Path(
            self.command(
                str(MANAGE),
                "record-evidence",
                "--run-dir",
                str(standard),
                "--evidence-id",
                "REVIEW-CHANGES",
                "--kind",
                "review",
                "--status",
                "changes_requested",
                "--task-id",
                "TASK-001",
                "--agent-id",
                "reviewer",
                "--summary",
                "Fix within existing scope",
            ).stdout.strip()
        )
        self.emit(
            "CHANGES_REQUESTED",
            from_agent="reviewer",
            to_agent="owner",
            payload=changes,
            run_dir=standard,
        )
        self.emit("TASK_RESUMED", run_dir=standard)
        state = (standard / "state.yaml").read_text(encoding="utf-8")
        self.assertIn('"TASK-001": "ready"', state)
        self.assertEqual(hashlib.sha256(task.read_bytes()).hexdigest(), frozen_hash)
        validation = self.validate(run_dir=standard, phase="dispatch")
        self.assertEqual(validation.returncode, 0, validation.stdout)

    def test_repeatable_event_requires_occurrence_key(self) -> None:
        self.prepare_running_task()
        missing = self.emit("THREAD_PROGRESS", check=False)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("repeatable", missing.stderr)
        renewed = self.write_lease("LEASE-002")
        self.emit("LEASE_RENEWED", payload=renewed)
        self.assertEqual(self.validate().returncode, 0)

    def test_retry_uses_immutable_attempt_documents(self) -> None:
        task = self.prepare_owner_task()
        self.emit("TASK_READY", payload=task)
        self.emit("TASK_DISPATCHED", payload=task)
        ack_one = Path(
            self.command(
                str(MANAGE),
                "write-ack",
                "--run-dir",
                str(self.run_dir),
                "--task-id",
                "TASK-001",
                "--agent-id",
                "owner",
                "--attempt-id",
                "ATTEMPT-001",
                "--idempotency-key",
                "ACK-ATTEMPT-001",
            ).stdout.strip()
        )
        self.emit("ACK", from_agent="owner", to_agent="coordinator", payload=ack_one)
        lease_one = self.write_lease("LEASE-001", attempt_id="ATTEMPT-001")
        self.emit("LEASE_ACQUIRED", payload=lease_one)
        failed_result = Path(
            self.command(
                str(MANAGE),
                "write-result",
                "--run-dir",
                str(self.run_dir),
                "--task-id",
                "TASK-001",
                "--agent-id",
                "owner",
                "--attempt-id",
                "ATTEMPT-001",
                "--status",
                "failed",
                "--outcome",
                "Transient failure",
                "--verification-status",
                "failed",
                "--risk-summary",
                "No side effect",
                "--rollback-plan",
                "No rollback required",
            ).stdout.strip()
        )
        self.emit("TASK_FAILED", payload=failed_result)
        self.emit("RETRY_SCHEDULED", event_key="ATTEMPT-002")
        self.emit("TASK_RESUMED")
        self.emit("TASK_DISPATCHED", payload=task)
        ack_two = Path(
            self.command(
                str(MANAGE),
                "write-ack",
                "--run-dir",
                str(self.run_dir),
                "--task-id",
                "TASK-001",
                "--agent-id",
                "owner",
                "--attempt-id",
                "ATTEMPT-002",
                "--idempotency-key",
                "ACK-ATTEMPT-002",
            ).stdout.strip()
        )
        self.emit("ACK", from_agent="owner", to_agent="coordinator", payload=ack_two)
        lease_two = self.write_lease("LEASE-002", attempt_id="ATTEMPT-002")
        self.emit("LEASE_ACQUIRED", payload=lease_two)
        completed_result = Path(
            self.command(
                str(MANAGE),
                "write-result",
                "--run-dir",
                str(self.run_dir),
                "--task-id",
                "TASK-001",
                "--agent-id",
                "owner",
                "--attempt-id",
                "ATTEMPT-002",
                "--status",
                "completed",
                "--outcome",
                "Retry succeeded",
                "--verification-status",
                "passed",
                "--risk-summary",
                "No residual risk",
                "--rollback-plan",
                "No project mutation",
            ).stdout.strip()
        )
        self.emit(
            "HANDOFF_READY",
            from_agent="owner",
            to_agent="coordinator",
            payload=completed_result,
        )
        self.emit("TASK_COMPLETED", payload=completed_result)
        validation = self.validate(phase="completion")
        self.assertEqual(validation.returncode, 0, validation.stdout)
        self.assertNotEqual(failed_result, completed_result)
        self.assertTrue(failed_result.is_file())
        self.assertTrue(completed_result.is_file())

    def test_retry_is_rejected_after_max_attempts(self) -> None:
        capped = self.initialize("RUN-CAPPED", "light", max_attempts=1)
        self.add_agent("owner", writable=self.project / "src", run_dir=capped)
        task = self.create_task(run_dir=capped)
        self.emit("TASK_READY", payload=task, run_dir=capped)
        self.emit("TASK_DISPATCHED", payload=task, run_dir=capped)
        ack = Path(
            self.command(
                str(MANAGE),
                "write-ack",
                "--run-dir",
                str(capped),
                "--task-id",
                "TASK-001",
                "--agent-id",
                "owner",
                "--attempt-id",
                "ATTEMPT-001",
                "--idempotency-key",
                "ACK-CAPPED",
            ).stdout.strip()
        )
        self.emit(
            "ACK",
            from_agent="owner",
            to_agent="coordinator",
            payload=ack,
            run_dir=capped,
        )
        lease = self.write_lease(
            "LEASE-CAPPED",
            run_dir=capped,
            attempt_id="ATTEMPT-001",
        )
        self.emit("LEASE_ACQUIRED", payload=lease, run_dir=capped)
        failed_result = Path(
            self.command(
                str(MANAGE),
                "write-result",
                "--run-dir",
                str(capped),
                "--task-id",
                "TASK-001",
                "--agent-id",
                "owner",
                "--attempt-id",
                "ATTEMPT-001",
                "--status",
                "failed",
                "--outcome",
                "Final failed attempt",
                "--verification-status",
                "failed",
                "--risk-summary",
                "No side effect",
                "--rollback-plan",
                "No rollback required",
            ).stdout.strip()
        )
        failed_event = self.emit("TASK_FAILED", payload=failed_result, run_dir=capped)
        rejected = self.emit(
            "RETRY_SCHEDULED",
            event_key="ATTEMPT-002",
            run_dir=capped,
            check=False,
        )
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("dead-letter", rejected.stderr)
        failed_event_path = Path(failed_event.stdout.strip())
        failed_event_id = next(
            line.split(":", 1)[1].strip().strip('"')
            for line in failed_event_path.read_text(encoding="utf-8").splitlines()
            if line.startswith("event_id:")
        )
        dead_letter = Path(
            self.command(
                str(MANAGE),
                "write-dead-letter",
                "--run-dir",
                str(capped),
                "--task-id",
                "TASK-001",
                "--failed-event-id",
                failed_event_id,
                "--attempts",
                "1",
                "--reason",
                "Retry budget exhausted",
                "--side-effect-state",
                "none",
            ).stdout.strip()
        )
        self.emit("DEAD_LETTERED", payload=dead_letter, run_dir=capped)
        validation = self.validate(run_dir=capped, phase="structure")
        self.assertEqual(validation.returncode, 0, validation.stdout)

    def test_result_tampering_breaks_event_hash(self) -> None:
        result = self.complete_light_task()
        result.write_text(
            result.read_text(encoding="utf-8") + "\nTampered\n",
            encoding="utf-8",
        )
        validation = self.validate(phase="completion")
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("payload hash mismatch", validation.stdout)

    def test_evidence_artifact_tampering_is_detected(self) -> None:
        artifact = self.run_dir / "artifacts" / "verification.txt"
        artifact.write_text("passed\n", encoding="utf-8")
        self.command(
            str(MANAGE),
            "record-evidence",
            "--run-dir",
            str(self.run_dir),
            "--evidence-id",
            "EVIDENCE-WITH-ARTIFACT",
            "--kind",
            "verification",
            "--status",
            "passed",
            "--agent-id",
            "coordinator",
            "--summary",
            "Artifact is hashed",
            "--artifact-ref",
            str(artifact),
        )
        artifact.write_text("modified\n", encoding="utf-8")
        validation = self.validate(phase="structure")
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("artifact hash mismatch", validation.stdout)

    def test_canonical_path_blocks_parent_escape(self) -> None:
        self.add_agent("owner", writable=self.project / "src")
        result = self.command(
            str(MANAGE),
            "create-task",
            "--run-dir",
            str(self.run_dir),
            "--task-id",
            "TASK-ESCAPE",
            "--title",
            "Escape",
            "--objective",
            "Must fail",
            "--owner-agent",
            "owner",
            "--owned-path",
            str(self.project / "src" / ".." / "outside"),
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("exceeds owner writable scope", result.stderr)

    def test_lock_detects_parent_child_resource_conflict(self) -> None:
        self.add_agent("owner", writable=self.project / "src")
        self.create_task(task_id="TASK-ONE", owned=self.project / "src")
        self.create_task(
            task_id="TASK-TWO",
            owned=self.project / "src" / "module",
            dependency="TASK-ONE",
        )
        self.command(
            str(MANAGE),
            "lock",
            "--run-dir",
            str(self.run_dir),
            "acquire",
            "--lock-id",
            "LOCK-ONE",
            "--task-id",
            "TASK-ONE",
            "--agent-id",
            "owner",
            "--resource",
            str(self.project / "src"),
        )
        second = self.command(
            str(MANAGE),
            "lock",
            "--run-dir",
            str(self.run_dir),
            "acquire",
            "--lock-id",
            "LOCK-TWO",
            "--task-id",
            "TASK-TWO",
            "--agent-id",
            "owner",
            "--resource",
            str(self.project / "src" / "module"),
            check=False,
        )
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("conflicts with active lock", second.stderr)

    def test_strict_dispatch_requires_and_accepts_real_gate_evidence(self) -> None:
        self.command("git", "-C", str(self.project), "init")
        self.command("git", "-C", str(self.project), "config", "user.email", "test@example.com")
        self.command("git", "-C", str(self.project), "config", "user.name", "Protocol Test")
        self.command("git", "-C", str(self.project), "commit", "--allow-empty", "-m", "initial")
        branch = self.command(
            "git",
            "-C",
            str(self.project),
            "branch",
            "--show-current",
        ).stdout.strip()
        strict = self.initialize("RUN-STRICT", "strict")
        self.add_agent("owner", writable=self.project / "src", run_dir=strict)
        self.add_agent("reviewer", run_dir=strict)
        self.add_agent("qa", run_dir=strict)
        gate = Path(
            self.command(
                str(MANAGE),
                "record-gate",
                "--run-dir",
                str(strict),
                "--gate-id",
                "GATE-MIGRATION",
                "--scope",
                "migration",
                "--status",
                "approved",
                "--human-confirmed",
                "--summary",
                "Migration approved",
            ).stdout.strip()
        )
        task = self.create_task(
            owner="owner",
            reviewer="reviewer",
            qa="qa",
            risk="migration",
            gate="GATE-MIGRATION",
            run_dir=strict,
        )
        blocked = self.emit(
            "TASK_READY",
            payload=task,
            run_dir=strict,
            check=False,
        )
        self.assertNotEqual(blocked.returncode, 0)
        self.assertIn("change_id", blocked.stderr)
        refs: dict[str, Path] = {}
        for kind, status in (
            ("git_status", "clean"),
            ("environment_impact", "reviewed"),
            ("rollback", "ready"),
            ("security", "approved"),
        ):
            refs[kind] = Path(
                self.command(
                    str(MANAGE),
                    "record-evidence",
                    "--run-dir",
                    str(strict),
                    "--evidence-id",
                    f"EVIDENCE-{kind.upper()}",
                    "--kind",
                    kind,
                    "--status",
                    status,
                    "--agent-id",
                    "reviewer",
                    "--summary",
                    kind,
                ).stdout.strip()
            )
        registry = strict / "artifacts" / "registry.yaml"
        registry.write_text("change: registered\n", encoding="utf-8")
        self.command(
            str(MANAGE),
            "configure-run",
            "--run-dir",
            str(strict),
            "--set",
            "change_id=CHG-001",
            "--set",
            f"git_branch={branch}",
            "--set",
            f"registry_ref={registry}",
            "--set",
            f"git_status_ref={refs['git_status']}",
            "--set",
            f"environment_impact_ref={refs['environment_impact']}",
            "--set",
            f"rollback_ref={refs['rollback']}",
            "--set",
            f"security_review_ref={refs['security']}",
        )
        self.emit("TASK_READY", payload=task, run_dir=strict)
        validation = self.validate(run_dir=strict, phase="dispatch")
        self.assertEqual(validation.returncode, 0, validation.stdout)
        self.assertTrue(gate.is_file())

    def test_standard_completion_requires_and_accepts_review_qa_evidence(self) -> None:
        standard = self.initialize("RUN-STANDARD", "standard")
        self.add_agent("owner", writable=self.project / "src", run_dir=standard)
        self.add_agent("reviewer", run_dir=standard)
        self.add_agent("qa", run_dir=standard)
        task = self.create_task(
            owner="owner",
            reviewer="reviewer",
            qa="qa",
            run_dir=standard,
        )
        self.emit("TASK_READY", payload=task, run_dir=standard)
        self.emit("TASK_DISPATCHED", payload=task, run_dir=standard)
        ack = Path(
            self.command(
                str(MANAGE),
                "write-ack",
                "--run-dir",
                str(standard),
                "--task-id",
                "TASK-001",
                "--agent-id",
                "owner",
                "--idempotency-key",
                "ACK-STANDARD",
            ).stdout.strip()
        )
        self.emit(
            "ACK",
            from_agent="owner",
            to_agent="coordinator",
            payload=ack,
            run_dir=standard,
        )
        standard_lease = self.write_lease("LEASE-STANDARD", run_dir=standard)
        self.emit("LEASE_ACQUIRED", payload=standard_lease, run_dir=standard)
        verification = Path(
            self.command(
                str(MANAGE),
                "record-evidence",
                "--run-dir",
                str(standard),
                "--evidence-id",
                "VERIFY-001",
                "--kind",
                "verification",
                "--status",
                "passed",
                "--task-id",
                "TASK-001",
                "--agent-id",
                "owner",
                "--summary",
                "Verification passed",
            ).stdout.strip()
        )
        result = Path(
            self.command(
                str(MANAGE),
                "write-result",
                "--run-dir",
                str(standard),
                "--task-id",
                "TASK-001",
                "--agent-id",
                "owner",
                "--status",
                "completed",
                "--outcome",
                "Implemented",
                "--uncommitted-reason",
                "Protocol fixture has no project commit",
                "--verification-status",
                "passed",
                "--verification-ref",
                str(verification),
                "--risk-summary",
                "Low",
                "--rollback-plan",
                "Discard fixture",
            ).stdout.strip()
        )
        self.emit(
            "HANDOFF_READY",
            from_agent="owner",
            to_agent="reviewer",
            payload=result,
            run_dir=standard,
        )
        self.emit(
            "REVIEW_STARTED",
            from_agent="reviewer",
            to_agent="owner",
            run_dir=standard,
        )
        review = Path(
            self.command(
                str(MANAGE),
                "record-evidence",
                "--run-dir",
                str(standard),
                "--evidence-id",
                "REVIEW-001",
                "--kind",
                "review",
                "--status",
                "approved",
                "--task-id",
                "TASK-001",
                "--agent-id",
                "reviewer",
                "--summary",
                "Review passed",
            ).stdout.strip()
        )
        self.emit(
            "REVIEW_APPROVED",
            from_agent="reviewer",
            to_agent="qa",
            payload=review,
            run_dir=standard,
        )
        qa = Path(
            self.command(
                str(MANAGE),
                "record-evidence",
                "--run-dir",
                str(standard),
                "--evidence-id",
                "QA-001",
                "--kind",
                "qa",
                "--status",
                "passed",
                "--task-id",
                "TASK-001",
                "--agent-id",
                "qa",
                "--summary",
                "QA passed",
            ).stdout.strip()
        )
        self.emit(
            "QA_PASSED",
            from_agent="qa",
            to_agent="coordinator",
            payload=qa,
            run_dir=standard,
        )
        blocked_completion = self.emit(
            "TASK_COMPLETED",
            payload=result,
            run_dir=standard,
            check=False,
        )
        self.assertNotEqual(blocked_completion.returncode, 0)
        self.assertIn("git_branch", blocked_completion.stderr)
        git_status = Path(
            self.command(
                str(MANAGE),
                "record-evidence",
                "--run-dir",
                str(standard),
                "--evidence-id",
                "GIT-STATUS-STANDARD",
                "--kind",
                "git_status",
                "--status",
                "not_applicable",
                "--agent-id",
                "reviewer",
                "--summary",
                "Fixture has no Git worktree requirement",
            ).stdout.strip()
        )
        self.command(
            str(MANAGE),
            "configure-run",
            "--run-dir",
            str(standard),
            "--set",
            "git_branch=not-applicable",
            "--set",
            f"git_status_ref={git_status}",
        )
        self.emit("TASK_COMPLETED", payload=result, run_dir=standard)
        validation = self.validate(run_dir=standard, phase="completion")
        self.assertEqual(validation.returncode, 0, validation.stdout)

    def test_strict_release_closed_loop_with_real_commit(self) -> None:
        self.command("git", "-C", str(self.project), "init")
        self.command("git", "-C", str(self.project), "config", "user.email", "test@example.com")
        self.command("git", "-C", str(self.project), "config", "user.name", "Protocol Test")
        tracked = self.project / "src" / "release.txt"
        tracked.write_text("before\n", encoding="utf-8")
        version_file = self.project / "VERSION"
        version_file.write_text("1.0.0\n", encoding="utf-8")
        self.command("git", "-C", str(self.project), "add", "src/release.txt", "VERSION")
        self.command("git", "-C", str(self.project), "commit", "-m", "initial")
        branch = self.command(
            "git",
            "-C",
            str(self.project),
            "branch",
            "--show-current",
        ).stdout.strip()

        strict = self.initialize(
            "RUN-STRICT-RELEASE",
            "strict",
            tracked_version=True,
        )
        self.add_agent("owner", writable=self.project / "src", run_dir=strict)
        self.add_agent("reviewer", run_dir=strict)
        self.add_agent("qa", run_dir=strict)
        self.add_agent("release", run_dir=strict)
        release_gate = Path(
            self.command(
                str(MANAGE),
                "record-gate",
                "--run-dir",
                str(strict),
                "--gate-id",
                "GATE-RELEASE",
                "--scope",
                "release",
                "--status",
                "approved",
                "--human-confirmed",
                "--summary",
                "Release approved",
            ).stdout.strip()
        )
        evidence_refs: dict[str, Path] = {}
        for kind, status in (
            ("git_status", "clean"),
            ("environment_impact", "reviewed"),
            ("rollback", "ready"),
            ("security", "approved"),
        ):
            evidence_refs[kind] = Path(
                self.command(
                    str(MANAGE),
                    "record-evidence",
                    "--run-dir",
                    str(strict),
                    "--evidence-id",
                    f"PRE-{kind.upper()}",
                    "--kind",
                    kind,
                    "--status",
                    status,
                    "--agent-id",
                    "reviewer",
                    "--summary",
                    kind,
                ).stdout.strip()
            )
        registry = strict / "artifacts" / "registry.yaml"
        registry.write_text("change: CHG-RELEASE\n", encoding="utf-8")
        self.command(
            str(MANAGE),
            "configure-run",
            "--run-dir",
            str(strict),
            "--set",
            "change_id=CHG-RELEASE",
            "--set",
            f"git_branch={branch}",
            "--set",
            f"registry_ref={registry}",
            "--set",
            f"git_status_ref={evidence_refs['git_status']}",
            "--set",
            f"environment_impact_ref={evidence_refs['environment_impact']}",
            "--set",
            f"rollback_ref={evidence_refs['rollback']}",
            "--set",
            f"security_review_ref={evidence_refs['security']}",
        )
        task = self.create_task(
            owner="owner",
            reviewer="reviewer",
            qa="qa",
            release="release",
            risk="release",
            gate="GATE-RELEASE",
            run_dir=strict,
        )
        self.emit("TASK_READY", payload=task, run_dir=strict)
        self.emit("TASK_DISPATCHED", payload=task, run_dir=strict)
        ack = Path(
            self.command(
                str(MANAGE),
                "write-ack",
                "--run-dir",
                str(strict),
                "--task-id",
                "TASK-001",
                "--agent-id",
                "owner",
                "--idempotency-key",
                "ACK-STRICT",
            ).stdout.strip()
        )
        self.emit(
            "ACK",
            from_agent="owner",
            to_agent="coordinator",
            payload=ack,
            run_dir=strict,
        )
        strict_lease = self.write_lease("LEASE-STRICT", run_dir=strict)
        self.emit("LEASE_ACQUIRED", payload=strict_lease, run_dir=strict)
        tracked.write_text("after\n", encoding="utf-8")
        version_file.write_text("1.0.1\n", encoding="utf-8")
        self.command("git", "-C", str(self.project), "add", "src/release.txt", "VERSION")
        self.command("git", "-C", str(self.project), "commit", "-m", "release change")
        commit = self.command(
            "git",
            "-C",
            str(self.project),
            "rev-parse",
            "HEAD",
        ).stdout.strip()
        verification = Path(
            self.command(
                str(MANAGE),
                "record-evidence",
                "--run-dir",
                str(strict),
                "--evidence-id",
                "VERIFY-STRICT",
                "--kind",
                "verification",
                "--status",
                "passed",
                "--task-id",
                "TASK-001",
                "--agent-id",
                "owner",
                "--summary",
                "Strict verification passed",
            ).stdout.strip()
        )
        result = Path(
            self.command(
                str(MANAGE),
                "write-result",
                "--run-dir",
                str(strict),
                "--task-id",
                "TASK-001",
                "--agent-id",
                "owner",
                "--status",
                "completed",
                "--outcome",
                "Release candidate complete",
                "--changed-file",
                str(tracked),
                "--implementation-commit",
                commit,
                "--verification-status",
                "passed",
                "--verification-ref",
                str(verification),
                "--risk-summary",
                "Reviewed",
                "--rollback-plan",
                "Revert the implementation commit",
            ).stdout.strip()
        )
        self.emit(
            "HANDOFF_READY",
            from_agent="owner",
            to_agent="reviewer",
            payload=result,
            run_dir=strict,
        )
        review = Path(
            self.command(
                str(MANAGE),
                "record-evidence",
                "--run-dir",
                str(strict),
                "--evidence-id",
                "REVIEW-STRICT",
                "--kind",
                "review",
                "--status",
                "approved",
                "--task-id",
                "TASK-001",
                "--agent-id",
                "reviewer",
                "--summary",
                "Strict review passed",
            ).stdout.strip()
        )
        self.emit(
            "REVIEW_APPROVED",
            from_agent="reviewer",
            to_agent="qa",
            payload=review,
            run_dir=strict,
        )
        qa = Path(
            self.command(
                str(MANAGE),
                "record-evidence",
                "--run-dir",
                str(strict),
                "--evidence-id",
                "QA-STRICT",
                "--kind",
                "qa",
                "--status",
                "passed",
                "--task-id",
                "TASK-001",
                "--agent-id",
                "qa",
                "--summary",
                "Strict QA passed",
            ).stdout.strip()
        )
        self.emit(
            "QA_PASSED",
            from_agent="qa",
            to_agent="coordinator",
            payload=qa,
            run_dir=strict,
        )
        clean = Path(
            self.command(
                str(MANAGE),
                "record-evidence",
                "--run-dir",
                str(strict),
                "--evidence-id",
                "CLEAN-RELEASE",
                "--kind",
                "git_status",
                "--status",
                "clean",
                "--agent-id",
                "reviewer",
                "--summary",
                "Release worktree clean",
            ).stdout.strip()
        )
        self.command(
            str(MANAGE),
            "configure-run",
            "--run-dir",
            str(strict),
            "--set",
            "release_environment=test",
            "--set",
            f"release_authorization_ref={release_gate}",
            "--set",
            f"clean_worktree_ref={clean}",
        )
        self.command(
            str(MANAGE),
            "record-release-candidate",
            "--run-dir",
            str(strict),
            "--summary",
            "Integrated release candidate",
            "--implementation-commit",
            commit,
        )
        self.emit(
            "RELEASE_READY",
            to_agent="release",
            payload=release_gate,
            run_dir=strict,
        )
        self.emit("TASK_COMPLETED", payload=result, run_dir=strict)
        validation = self.validate(run_dir=strict, phase="release")
        self.assertEqual(validation.returncode, 0, validation.stdout)

    def test_native_event_requires_operation_record(self) -> None:
        self.add_agent(
            "native-owner",
            runtime="codex_thread",
            writable=self.project / "src",
        )
        task = self.create_task(owner="native-owner")
        binding = self.run_dir / "native" / "threads" / "TASK-001.yaml"
        task_hash = hashlib.sha256(task.read_bytes()).hexdigest()
        binding.write_text(
            f"""protocol_version: 3
kind: "codex_thread_binding"
run_id: "RUN-TEST"
task_id: "TASK-001"
agent_id: "native-owner"
runtime: "codex_thread"
project_id: "project"
thread_id: null
pending_id: null
host_id: null
environment: "local"
worktree_path: null
branch: null
base_commit: null
implementation_commit: null
task_path: "{task}"
task_sha256: "{task_hash}"
status: "requested"
last_read_cursor: null
last_wait_cursor: null
last_native_operation: null
created_at: "2026-08-03T20:00:00+08:00"
updated_at: "2026-08-03T20:00:00+08:00"
""",
            encoding="utf-8",
        )
        self.emit("TASK_READY", to_agent="native-owner", payload=task)
        self.emit(
            "THREAD_CREATE_REQUESTED",
            to_agent="native-owner",
        )
        validation = self.validate(phase="dispatch")
        self.assertNotEqual(validation.returncode, 0)
        self.assertIn("lacks matching native operation", validation.stdout)

    def test_valid_managed_document_subagent_binding(self) -> None:
        self.add_agent("parent", writable=self.project / "src")
        self.add_agent(
            "child",
            runtime="document_subagent",
            writable=self.project / "src" / "module",
            parent="parent",
            depth=1,
        )
        task = self.create_task(
            owner="child",
            owned=self.project / "src" / "module",
            reviewer="parent",
        )
        task_hash = hashlib.sha256(task.read_bytes()).hexdigest()
        binding = self.run_dir / "delegations" / "TASK-001-child.yaml"
        binding.write_text(
            f"""protocol_version: 3
kind: "document_subagent_binding"
run_id: "RUN-TEST"
task_id: "TASK-001"
agent_id: "child"
parent_agent_id: "parent"
delegated_by: "parent"
runtime: "document_subagent"
delegation_depth: 1
task_path: "{task}"
task_sha256: "{task_hash}"
status: "ready"
max_duration_seconds: 1800
max_attempts: 3
created_at: "2026-08-03T20:00:00+08:00"
updated_at: "2026-08-03T20:00:00+08:00"
result_ref: null
closed_at: null
""",
            encoding="utf-8",
        )
        self.emit("TASK_READY", to_agent="child", payload=task)
        self.emit("DOCUMENT_SUBAGENT_DELEGATED", to_agent="child")
        validation = self.validate(phase="dispatch")
        self.assertEqual(validation.returncode, 0, validation.stdout)


if __name__ == "__main__":
    unittest.main()
