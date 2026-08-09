from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"


class AgentLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.command("init_project_agents.py", "--project-root", str(self.project),
                 "--project-id", "fixture", "--project-name", "Fixture",
                 "--agents", "A01-coordinator,A02-worker", "--user-confirmed")
        self.bus = self.project / ".multi-agent-collaboration"

    def command(self, script: str, *args: str, ok: bool = True, env: dict[str, str] | None = None):
        merged = os.environ.copy()
        if env:
            merged.update(env)
        result = subprocess.run(["python3", str(SCRIPTS / script), *args], text=True,
                                capture_output=True, env=merged)
        if ok and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def manage(self, action: str, *args: str, ok: bool = True, env: dict[str, str] | None = None):
        return self.command("manage_project_agents.py", action, "--project-root", str(self.project), *args, ok=ok, env=env)

    def team(self):
        return json.loads((self.bus / "TEAM.yaml").read_text())

    def test_add_update_preserves_stable_id_and_role_history(self):
        self.manage("add", "--agent-id", "A03-qa", "--role-name", "quality", "--domain", "tests")
        self.manage("update", "--agent-id", "A03-qa", "--role-name", "release-quality", "--domain", "release")
        record = next(a for a in self.team()["agents"] if a["agent_id"] == "A03-qa")
        self.assertEqual(record["role_name"], "release-quality")
        self.assertEqual(record["role_history"][-1]["role_name"], "quality")
        self.assertTrue((self.bus / record["role_file"]).is_file())
        self.assertTrue((self.bus / record["agent_profile_file"]).is_file())
        profile = json.loads((self.bus / record["agent_profile_file"]).read_text())
        self.assertEqual(profile["role"]["role_id"], "release-quality")
        self.assertEqual(profile["catalog"]["tier"], "custom")
        rejected = self.manage("update", "--agent-id", "A03-qa", "--new-agent-id", "A04-qa", ok=False)
        self.assertIn("immutable", (rejected.stdout + rejected.stderr).lower())

    def test_pause_resume_and_retire_never_delete_archive_or_checkpoints(self):
        agent = self.bus / "agents" / "A02-worker"
        archive = agent / "conversations" / "archive" / "history.md"
        checkpoint = agent / "conversations" / "checkpoints" / "CP-0001.md"
        archive.write_text("history")
        checkpoint.write_text("checkpoint")
        self.manage("pause", "--agent-id", "A02-worker", "--reason", "maintenance")
        self.assertEqual(self.team()["agents"][1]["status"], "paused")
        self.manage("resume", "--agent-id", "A02-worker")
        self.assertEqual(self.team()["agents"][1]["status"], "active")
        self.manage("retire", "--agent-id", "A02-worker", "--reason", "reorganization")
        self.assertEqual(self.team()["agents"][1]["status"], "retired")
        self.assertTrue(archive.is_file())
        self.assertTrue(checkpoint.is_file())
        self.assertTrue(agent.is_dir())

    def test_repair_half_initialized_agent_without_overwriting_history(self):
        agent = self.bus / "agents" / "A02-worker"
        role = agent / "ROLE.md"
        role.write_text("immutable custom role history\n")
        (agent / "SYSTEM_PROMPT.md").unlink()
        (agent / "conversations" / "SESSION_MAP.json").unlink()
        shutil_target = agent / "conversations" / "checkpoints"
        shutil_target.rmdir()
        plan = json.loads(self.manage("repair", "--agent-id", "A02-worker").stdout)
        self.manage("repair", "--agent-id", "A02-worker", "--apply", "--plan-hash", plan["plan_hash"])
        self.assertEqual(role.read_text(), "immutable custom role history\n")
        self.assertTrue((agent / "SYSTEM_PROMPT.md").is_file())
        self.assertTrue((agent / "conversations" / "SESSION_MAP.json").is_file())
        self.assertTrue(shutil_target.is_dir())

    def test_repair_runtime_activity_is_dry_run_then_applies_transactionally(self):
        agent = self.bus / "agents" / "A02-worker"
        runtime_profile = agent / "runtime" / "profiles" / "RP-000001.json"
        runtime_profile.parent.mkdir(parents=True)
        runtime_profile.write_text('{"immutable":"runtime"}\n')
        activity_record = agent / "activity" / "RUN-1" / "TASK-1" / "ATTEMPT-1" / "2026" / "08" / "06" / "ACTIVITY-000001.json"
        activity_record.parent.mkdir(parents=True)
        activity_record.write_text('{"immutable":"activity"}\n')
        orphan_runtime = agent / "runtime" / "orphan.tmp"
        orphan_runtime.write_text("partial")
        before = {path: path.read_bytes() for path in (runtime_profile, activity_record)}

        dry = self.manage("repair", "--agent-id", "A02-worker")
        plan = json.loads(dry.stdout)
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["plan_hash"], hashlib.sha256(json.dumps(plan["operations"], sort_keys=True, separators=(",", ":")).encode()).hexdigest())
        self.assertTrue(any(item["path"] == "runtime/CURRENT_RUNTIME.json" for item in plan["operations"]))
        self.assertTrue(any(item["path"] == "activity/RUN-1/TASK-1/ATTEMPT-1/INDEX.jsonl" for item in plan["operations"]))
        self.assertTrue(any(item["action"] == "quarantine" and item["path"] == "runtime/orphan.tmp" for item in plan["operations"]))
        self.assertFalse((agent / "runtime" / "CURRENT_RUNTIME.json").exists())

        applied = self.manage("repair", "--agent-id", "A02-worker", "--apply", "--plan-hash", plan["plan_hash"])
        result = json.loads(applied.stdout)
        self.assertFalse(result["dry_run"])
        self.assertTrue((self.bus / result["backup"]).is_file())
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertTrue((agent / "runtime" / "CURRENT_RUNTIME.json").is_file())
        self.assertTrue((agent / "activity" / "RUN-1" / "TASK-1" / "ATTEMPT-1" / "INDEX.jsonl").is_file())
        self.assertFalse(orphan_runtime.exists())

    def test_repair_rejects_stale_plan_and_rolls_back_failed_apply(self):
        agent = self.bus / "agents" / "A02-worker"
        (agent / "runtime" / "profiles").mkdir(parents=True)
        (agent / "runtime" / "profiles" / "RP-000001.json").write_text('{"runtime_profile_id":"RP-000001"}\n')
        plan = json.loads(self.manage("repair", "--agent-id", "A02-worker").stdout)
        stale = self.manage("repair", "--agent-id", "A02-worker", "--apply", "--plan-hash", "0" * 64, ok=False)
        self.assertIn("plan hash", stale.stderr.lower())
        before = {path.relative_to(agent).as_posix(): path.read_bytes() for path in agent.rglob("*") if path.is_file()}
        failed = self.manage("repair", "--agent-id", "A02-worker", "--apply", "--plan-hash", plan["plan_hash"],
                             ok=False, env={"AGENT_REPAIR_FAIL_AFTER": "1"})
        self.assertIn("rolled back", failed.stderr.lower())
        after = {path.relative_to(agent).as_posix(): path.read_bytes() for path in agent.rglob("*") if path.is_file() and ".repair-" not in path.as_posix()}
        self.assertEqual(before, after)

    def test_archive_and_retire_preserve_runtime_activity_checkpoint_hash_chains(self):
        agent = self.bus / "agents" / "A02-worker"
        paths = {
            agent / "runtime" / "profiles" / "RP-000001.json": b"runtime-profile\n",
            agent / "runtime" / "RUNTIME_INDEX.jsonl": b"runtime-index\n",
            agent / "activity" / "RUN-1" / "TASK-1" / "ATTEMPT-1" / "INDEX.jsonl": b"activity-index\n",
            agent / "activity" / "RUN-1" / "TASK-1" / "ATTEMPT-1" / "ACTIVITY-000001.json": b"activity-record\n",
            agent / "conversations" / "checkpoints" / "CP-0001.md": b"checkpoint\n",
        }
        for path, content in paths.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        hashes = {path: hashlib.sha256(content).hexdigest() for path, content in paths.items()}
        self.manage("archive", "--agent-id", "A02-worker", "--reason", "handover")
        self.assertEqual(self.team()["agents"][1]["status"], "retired")
        for path, digest in hashes.items():
            self.assertTrue(path.is_file())
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        repeated = self.manage("retire", "--agent-id", "A02-worker", "--reason", "already archived")
        for path, digest in hashes.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)

    def test_migration_dry_run_idempotence_and_manifest(self):
        before = hashlib.sha256((self.bus / "TEAM.yaml").read_bytes()).hexdigest()
        dry = self.command("migrate_project_agents.py", "--project-root", str(self.project), "--dry-run")
        self.assertIn("dry-run", dry.stdout.lower())
        self.assertEqual(before, hashlib.sha256((self.bus / "TEAM.yaml").read_bytes()).hexdigest())
        self.assertFalse((self.bus / "STORAGE.json").exists())
        first = self.command("migrate_project_agents.py", "--project-root", str(self.project))
        storage = json.loads((self.bus / "STORAGE.json").read_text())
        self.assertEqual(storage["schema_version"], "1.1")
        manifest = Path(first.stdout.strip().splitlines()[-1])
        self.assertTrue(manifest.is_file())
        second = self.command("migrate_project_agents.py", "--project-root", str(self.project))
        self.assertIn("already", second.stdout.lower())
        self.assertEqual(storage, json.loads((self.bus / "STORAGE.json").read_text()))

    def test_migration_failure_rolls_back_and_preserves_history(self):
        agent = self.bus / "agents" / "A01-coordinator" / "conversations"
        archive = agent / "archive" / "keep.md"
        checkpoint = agent / "checkpoints" / "keep.md"
        archive.write_text("archive")
        checkpoint.write_text("checkpoint")
        before = (self.bus / "TEAM.yaml").read_bytes()
        failed = self.command("migrate_project_agents.py", "--project-root", str(self.project),
                          ok=False, env={"AGENT_MIGRATION_FAIL_AFTER": "1"})
        self.assertIn("rolled back", (failed.stdout + failed.stderr).lower())
        self.assertEqual(before, (self.bus / "TEAM.yaml").read_bytes())
        self.assertFalse((self.bus / "STORAGE.json").exists())
        self.assertEqual(archive.read_text(), "archive")
        self.assertEqual(checkpoint.read_text(), "checkpoint")


if __name__ == "__main__":
    unittest.main()
