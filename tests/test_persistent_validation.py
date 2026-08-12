from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"


def frontmatter(meta: dict, body: str = "") -> str:
    lines = ["---"]
    for key, value in meta.items():
        lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
    return "\n".join(lines) + "\n---\n\n" + body


class PersistentValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.governance = Path(self.temp.name) / "governance"
        self.command("init_project_agents.py", "--project-root", str(self.project), "--project-id", "fixture",
                 "--project-name", "Fixture", "--agents", "A01-coordinator,A02-worker", "--user-confirmed")
        self.bus = self.governance / "projects" / "fixture"

    def command(self, script: str, *args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
        arguments = list(args)
        if "--governance-root" not in arguments:
            arguments.extend(["--governance-root", str(self.governance)])
        result = subprocess.run(["python3", str(SCRIPTS / script), *arguments], text=True, capture_output=True)
        if ok and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def validate(self, needle: str | None = None) -> subprocess.CompletedProcess[str]:
        result = self.command("validate_agents.py", "--project-root", str(self.project), ok=False)
        if needle is not None:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn(needle, result.stdout)
        return result

    def write_task(self, agent: str, task_id: str, *, owner: str | None = None,
                   dependencies: list[str] | None = None, writes: list[str] | None = None) -> Path:
        path = self.bus / "agents" / agent / "tasks" / f"{task_id}.md"
        path.write_text(frontmatter({
            "schema_version": "1.0", "doc_type": "task", "task_id": task_id,
            "title": task_id, "owner": owner or agent, "goal": "goal",
            "dependencies": dependencies or [], "allowed_writes": writes or [],
        }, "# Task\n"), encoding="utf-8")
        return path

    def test_builtin_schema_rejects_unknown_property_enum_pattern_and_bad_iso_time(self) -> None:
        mapping_path = self.bus / "agents/A01-coordinator/conversations/SESSION_MAP.json"
        mapping = json.loads(mapping_path.read_text(encoding="utf-8"))
        mapping["active"] = {
            "platform": "invalid", "session_id": "s", "profile": "default",
            "workspace": str(self.project), "started_at": "yesterday",
            "last_synced_message_id": 0, "last_synced_at": "2026-08-06T12:00:00Z",
            "unexpected": True,
        }
        mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
        result = self.validate("schema violation")
        self.assertIn("enum", result.stdout)
        self.assertIn("date-time", result.stdout)
        self.assertIn("additional property", result.stdout)

        self.write_task("A01-coordinator", "bad id")
        self.validate("pattern")

    def test_archive_body_hash_source_hash_and_checkpoint_source_hashes_are_verified(self) -> None:
        agent = self.bus / "agents/A01-coordinator"
        archive = agent / "conversations/archive/2026-08/a.md"
        archive.parent.mkdir(parents=True, exist_ok=True)
        body = "# 完整对话归档\n\nhello"
        digest = hashlib.sha256("hello".encode()).hexdigest()
        archive.write_text(frontmatter({
            "schema_version": "1.0", "doc_type": "conversation_archive", "agent_id": "A01-coordinator",
            "task_ids": ["TASK-1"], "platform": "hermes", "session_id": "s",
            "exported_at": "2026-08-06T12:00:00Z", "source_message_range": "1-1",
            "redacted": True, "redaction_count": 0, "content_sha256": digest,
            "source_sha256": "not-a-sha256",
        }, body), encoding="utf-8")
        self.validate("source hash")

        text = archive.read_text(encoding="utf-8").replace('source_sha256: "not-a-sha256"',
                                                            'source_sha256: "' + digest + '"')
        archive.write_text(text, encoding="utf-8")
        cp_body = "summary"
        checkpoint = agent / "conversations/checkpoints/CP-0001.md"
        checkpoint.write_text(frontmatter({
            "schema_version": "1.0", "doc_type": "checkpoint", "checkpoint_id": "CP-0001",
            "agent_id": "A01-coordinator", "task_ids": ["TASK-1"], "created_at": "2026-08-06T12:01:00Z",
            "previous_checkpoint": None, "source_archives": [archive.relative_to(self.bus).as_posix()],
            "source_archive_hashes": {archive.relative_to(self.bus).as_posix(): "f" * 64},
            "content_sha256": hashlib.sha256(cp_body.encode()).hexdigest(),
        }, "# 上下文检查点\n\n" + cp_body), encoding="utf-8")
        self.validate("source_archive_hashes")

    def test_task_registry_enforces_identity_owner_dag_and_parallel_write_isolation(self) -> None:
        self.write_task("A01-coordinator", "TASK-A", dependencies=["TASK-B"], writes=["src/**"])
        self.write_task("A02-worker", "TASK-B", dependencies=["TASK-A"], writes=["src/file.py"])
        result = self.validate("dependency cycle")
        self.assertIn("allowed_writes overlap", result.stdout)

        task = self.bus / "agents/A02-worker/tasks/TASK-B.md"
        task.write_text(task.read_text(encoding="utf-8").replace('"A02-worker"', '"A99-ghost"'), encoding="utf-8")
        result = self.validate("owner is not declared in TEAM.yaml")
        self.assertIn("task owner/directory mismatch", result.stdout)

        duplicate = self.bus / "agents/A02-worker/tasks/duplicate.md"
        duplicate.write_text((self.bus / "agents/A01-coordinator/tasks/TASK-A.md").read_text(encoding="utf-8"), encoding="utf-8")
        self.validate("duplicate task_id")

    def test_handoff_requires_matching_task_owner_and_hashed_existing_evidence(self) -> None:
        self.write_task("A02-worker", "TASK-DONE")
        handoff = self.bus / "agents/A01-coordinator/handoffs/TASK-DONE.md"
        handoff.write_text(frontmatter({
            "schema_version": "1.0", "doc_type": "handoff", "task_id": "TASK-DONE",
            "agent_id": "A01-coordinator", "status": "completed", "summary": "done",
            "created_at": "2026-08-06T12:00:00Z", "acceptance_evidence": [], "artifacts": [],
        }, "# handoff"), encoding="utf-8")
        result = self.validate("handoff agent_id does not match task owner")
        self.assertIn("completed handoff requires", result.stdout)

        evidence = self.bus / "agents/A02-worker/artifacts/evidence.txt"
        evidence.write_text("passed", encoding="utf-8")
        bad_hash = "0" * 64
        good_meta = {
            "schema_version": "1.0", "doc_type": "handoff", "task_id": "TASK-DONE",
            "agent_id": "A02-worker", "status": "completed", "summary": "done",
            "created_at": "2026-08-06T12:00:00Z",
            "acceptance_evidence": [{"path": evidence.relative_to(self.bus).as_posix(), "sha256": bad_hash}],
            "artifacts": [],
        }
        handoff.unlink()
        handoff = self.bus / "agents/A02-worker/handoffs/TASK-DONE.md"
        handoff.write_text(frontmatter(good_meta, "# handoff"), encoding="utf-8")
        self.validate("evidence hash mismatch")

    def test_current_context_and_indexes_must_be_complete_and_rebuildable(self) -> None:
        agent = self.bus / "agents/A01-coordinator"
        archive = agent / "conversations/archive/a.md"
        body = "hello"
        archive.write_text(frontmatter({
            "doc_type": "conversation_archive", "agent_id": "A01-coordinator",
            "content_sha256": hashlib.sha256(body.encode()).hexdigest(),
        }, body), encoding="utf-8")
        for number in (1, 2):
            cp_body = f"cp{number}"
            (agent / f"conversations/checkpoints/CP-{number:04d}.md").write_text(frontmatter({
                "checkpoint_id": f"CP-{number:04d}", "agent_id": "A01-coordinator",
                "previous_checkpoint": None if number == 1 else "CP-0001",
                "source_archives": [archive.relative_to(self.bus).as_posix()],
                "content_sha256": hashlib.sha256(cp_body.encode()).hexdigest(),
            }, "# 上下文检查点\n\n" + cp_body), encoding="utf-8")
        current = agent / "conversations/CURRENT_CONTEXT.md"
        current.write_text(frontmatter({"latest_checkpoint": "conversations/checkpoints/CP-0001.md"}), encoding="utf-8")
        self.command("rebuild_index.py", "--project-root", str(self.project))
        with (self.bus / "index.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"path": "missing.md"}) + "\n")
        result = self.validate("latest_checkpoint")
        self.assertIn("dangling path", result.stdout)
        self.assertIn("index is not rebuildable", result.stdout)


if __name__ == "__main__":
    unittest.main()
