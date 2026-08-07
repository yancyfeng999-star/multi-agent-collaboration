from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
RESUME = SKILL / "scripts" / "resume_brief.py"
INIT = SKILL / "scripts" / "init_project_agents.py"


class ResumeDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.command(INIT, "--project-root", self.project, "--project-id", "fixture",
                 "--project-name", "Fixture", "--agents", "A01-worker", "--user-confirmed")
        self.bus = self.project / ".multi-agent-collaboration"
        self.agent = self.bus / "agents" / "A01-worker"

    def command(self, program: Path | str, *args: object, ok: bool = True) -> subprocess.CompletedProcess[str]:
        command = (["python3", str(program)] if isinstance(program, Path) else [program])
        command.extend(str(value) for value in args)
        result = subprocess.run(command, capture_output=True, text=True)
        if ok and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    @staticmethod
    def frontmatter(path: Path) -> dict:
        block = path.read_text(encoding="utf-8").split("---\n", 2)[1]
        values = {}
        for line in block.splitlines():
            key, raw = line.split(":", 1)
            raw = raw.strip()
            values[key] = json.loads(raw) if raw.startswith(('"', "[", "{")) or raw in {"null", "true", "false"} else raw
        return values

    def resume(self, *extra: str, ok: bool = True) -> tuple[subprocess.CompletedProcess[str], Path | None]:
        result = self.command(RESUME, "--project-root", self.project, "--agent-id", "A01-worker", *extra, ok=ok)
        return result, Path(result.stdout.strip()) if result.returncode == 0 else None

    def write_doc(self, path: Path, **meta: object) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["---", *(f"{key}: {json.dumps(value)}" for key, value in meta.items()), "---", "body", ""]
        path.write_text("\n".join(lines), encoding="utf-8")

    def test_active_task_and_created_at_select_documents_not_filename_order(self) -> None:
        tasks = self.agent / "tasks"
        handoffs = self.agent / "handoffs"
        self.write_doc(tasks / "ZZZ-old.md", task_id="TASK-OLD", created_at="2026-01-01T00:00:00Z")
        active = tasks / "AAA-active.md"
        self.write_doc(active, task_id="TASK-ACTIVE", created_at="2025-01-01T00:00:00Z")
        self.write_doc(handoffs / "ZZZ-old.md", created_at="2024-01-01T00:00:00Z")
        newest_handoff = handoffs / "AAA-new.md"
        self.write_doc(newest_handoff, created_at="2026-02-01T00:00:00Z")
        current = self.agent / "conversations" / "CURRENT_CONTEXT.md"
        current.write_text(current.read_text(encoding="utf-8").replace('active_task: null', 'active_task: "tasks/AAA-active.md"'), encoding="utf-8")

        _, output = self.resume()
        self.assertIsNotNone(output)
        assert output is not None
        text = output.read_text(encoding="utf-8")
        self.assertIn(active.relative_to(self.project).as_posix(), text)
        self.assertNotIn("ZZZ-old.md`\n", text)
        self.assertIn(newest_handoff.relative_to(self.project).as_posix(), text)

    def test_run_state_selects_newest_created_active_task_when_context_has_none(self) -> None:
        runs = self.bus / "runs"
        old_task = runs / "RUN-Z" / "tasks" / "TASK-OLD.md"
        new_task = runs / "RUN-A" / "tasks" / "TASK-NEW.md"
        self.write_doc(old_task, task_id="TASK-OLD", owner_agent="A01-worker", created_at="2024-01-01T00:00:00Z")
        self.write_doc(new_task, task_id="TASK-NEW", owner_agent="A01-worker", created_at="2026-01-01T00:00:00Z")
        for task, state in ((old_task, "running"), (new_task, "dispatched")):
            state_path = task.parents[1] / "state.yaml"
            state_path.write_text(f'task_states: {{"{self.frontmatter(task)["task_id"]}": "{state}"}}\n', encoding="utf-8")

        _, output = self.resume()
        self.assertIsNotNone(output)
        assert output is not None
        self.assertIn(new_task.relative_to(self.project).as_posix(), output.read_text(encoding="utf-8"))

    def test_detects_git_missing_reference_hash_and_project_path_drift(self) -> None:
        self.command("git", "init", self.project)
        self.command("git", "-C", self.project, "config", "user.email", "test@example.com")
        self.command("git", "-C", self.project, "config", "user.name", "Test")
        tracked = self.project / "tracked.txt"
        tracked.write_text("before\n", encoding="utf-8")
        reference = self.project / "reference.txt"
        reference.write_text("before\n", encoding="utf-8")
        self.command("git", "-C", self.project, "add", ".")
        self.command("git", "-C", self.project, "commit", "-m", "baseline")
        head = self.command("git", "-C", self.project, "rev-parse", "HEAD").stdout.strip()
        checkpoint = self.agent / "conversations" / "checkpoints" / "CP-drift.md"
        self.write_doc(
            checkpoint,
            checkpoint_id="CP-drift",
            created_at="2026-01-01T00:00:00Z",
            git_head=head,
            dirty_files=[],
            project_root="/moved/project",
            referenced_files=["reference.txt", "missing.txt"],
            reference_hashes={"reference.txt": hashlib.sha256(b"before\n").hexdigest()},
        )
        current = self.agent / "conversations" / "CURRENT_CONTEXT.md"
        current.write_text(current.read_text(encoding="utf-8").replace('latest_checkpoint: null', 'latest_checkpoint: "conversations/checkpoints/CP-drift.md"'), encoding="utf-8")
        tracked.write_text("dirty\n", encoding="utf-8")
        reference.write_text("changed\n", encoding="utf-8")

        _, output = self.resume("--detect-drift")
        self.assertIsNotNone(output)
        assert output is not None
        drift = self.frontmatter(output)["drift"]
        self.assertTrue(drift["detected"])
        self.assertEqual(drift["git"]["expected_head"], head)
        self.assertIn("tracked.txt", drift["git"]["actual_dirty_files"])
        self.assertIn("missing.txt", drift["missing_references"])
        self.assertEqual(drift["hash_mismatches"][0]["path"], "reference.txt")
        self.assertFalse(drift["project_path"]["matches"])

        failed, _ = self.resume("--fail-on-drift", ok=False)
        self.assertEqual(failed.returncode, 2)

    def test_session_map_can_be_missing_and_output_is_confined(self) -> None:
        (self.agent / "conversations" / "SESSION_MAP.json").unlink()
        safe = Path(self.temp.name) / "exports"
        safe.mkdir()
        allowed = safe / "brief.md"
        _, output = self.resume("--output", str(allowed), "--safe-output-dir", str(safe))
        self.assertEqual(output, allowed.resolve())
        self.assertTrue(allowed.is_file())

        escaped = Path(self.temp.name) / "outside.md"
        result, _ = self.resume("--output", str(escaped), ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("outside project root", result.stderr)


if __name__ == "__main__":
    unittest.main()
