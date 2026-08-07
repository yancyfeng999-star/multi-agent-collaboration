from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"


class PersistentAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.command(
            "init_project_agents.py", "--project-root", str(self.project),
            "--project-id", "fixture", "--project-name", "Fixture",
            "--agents", "A01-coordinator,A02-worker", "--governance", "standard",
            "--user-confirmed",
        )

    def command(self, script: str, *args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(["python3", str(SCRIPTS / script), *args], capture_output=True, text=True)
        if ok and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def test_end_to_end_archive_checkpoint_index_resume(self) -> None:
        bus = self.project / ".multi-agent-collaboration"
        self.assertTrue((self.project / "AGENTS.md").is_file())
        self.assertTrue((bus / "schemas" / "checkpoint.schema.json").is_file())
        self.assertTrue((bus / "agents" / "A01-coordinator" / "CHECKLIST.md").is_file())
        team = json.loads((bus / "TEAM.yaml").read_text(encoding="utf-8"))
        self.assertEqual(team["project_id"], "fixture")
        self.assertEqual([agent["agent_id"] for agent in team["agents"]], ["A01-coordinator", "A02-worker"])
        session_schema = json.loads((bus / "schemas" / "session-map.schema.json").read_text(encoding="utf-8"))
        session_map = json.loads((bus / "agents" / "A01-coordinator" / "conversations" / "SESSION_MAP.json").read_text(encoding="utf-8"))
        self.assertIsNone(session_map["active"])
        self.command(
            "bind_session.py", "--project-root", str(self.project), "--agent-id", "A01-coordinator",
            "--platform", "hermes", "--session-id", "session-1",
            "--model", "fixture-model", "--provider", "fixture-provider",
        )
        session_map = json.loads((bus / "agents" / "A01-coordinator" / "conversations" / "SESSION_MAP.json").read_text(encoding="utf-8"))
        self.assertEqual(
            set(session_schema["properties"]["active"]["required"]) - set(session_map["active"]),
            set(),
        )
        source = self.project / "conversation.md"
        source.write_text("user: token=abc123456789\nassistant: 完成真实检查", encoding="utf-8")
        task = bus / "agents" / "A01-coordinator" / "tasks" / "TASK-0001.md"
        task.write_text(
            '---\nschema_version: "1.0"\ndoc_type: "task"\ntask_id: "TASK-0001"\n'
            'title: "Fixture task"\nowner: "A01-coordinator"\ngoal: "Exercise persistent flow"\n'
            'dependencies: []\nallowed_writes: []\n---\n\n# Fixture task\n',
            encoding="utf-8",
        )
        synced = self.command(
            "sync_conversation.py", "--project-root", str(self.project), "--agent-id", "A01-coordinator",
            "--source-file", str(source), "--platform", "hermes", "--session-id", "session-1",
            "--task-id", "TASK-0001", "--message-start", "1", "--message-end", "2",
        )
        archive = Path(synced.stdout.strip())
        self.assertIn("[REDACTED]", archive.read_text(encoding="utf-8"))
        summary = self.project / "summary.md"
        headings = [
            "长期使命", "当前总目标", "当前任务与状态", "已确认需求", "关键决策及原因",
            "已完成事项", "修改文件", "命令与真实结果", "失败尝试和踩坑", "未解决事项",
            "风险与假设", "下一步", "恢复时必须读取", "可按需读取的原文",
        ]
        summary.write_text("\n\n".join(f"## {h}\n\n无" for h in headings), encoding="utf-8")
        checkpoint = self.command(
            "create_checkpoint.py", "--project-root", str(self.project), "--agent-id", "A01-coordinator",
            "--summary-file", str(summary), "--source-archive", str(archive), "--task-id", "TASK-0001",
        )
        self.assertTrue(Path(checkpoint.stdout.strip()).is_file())
        checkpoint_schema = json.loads((bus / "schemas" / "checkpoint.schema.json").read_text(encoding="utf-8"))
        checkpoint_meta = self.frontmatter_json(Path(checkpoint.stdout.strip()))
        self.assertEqual(set(checkpoint_schema["required"]) - set(checkpoint_meta), set())
        self.assertIsNone(checkpoint_meta["previous_checkpoint"])
        self.command("rebuild_index.py", "--project-root", str(self.project))
        self.assertTrue((bus / "index.jsonl").is_file())
        resume = self.command("resume_brief.py", "--project-root", str(self.project), "--agent-id", "A01-coordinator")
        self.assertTrue(Path(resume.stdout.strip()).is_file())
        validation = self.command("validate_agents.py", "--project-root", str(self.project))
        self.assertIn("PASS", validation.stdout)

    @staticmethod
    def frontmatter_json(path: Path) -> dict:
        text = path.read_text(encoding="utf-8")
        block = text.split("---\n", 2)[1]
        result = {}
        for line in block.splitlines():
            key, raw = line.split(":", 1)
            raw = raw.strip()
            result[key] = json.loads(raw) if raw.startswith(('"', '[', '{')) or raw in {"null", "true", "false"} else raw
        return result

    def test_validation_rejects_invalid_team_yaml_and_tampered_checkpoint(self) -> None:
        bus = self.project / ".multi-agent-collaboration"
        team_path = bus / "TEAM.yaml"
        original = team_path.read_text(encoding="utf-8")
        team_path.write_text("---\nschema_version: 1.0\n---\n# not a machine-readable registry\n", encoding="utf-8")
        result = self.command("validate_agents.py", "--project-root", str(self.project), ok=False)
        self.assertNotEqual(result.returncode, 0)
        team_path.write_text(original, encoding="utf-8")

        self.command(
            "bind_session.py", "--project-root", str(self.project), "--agent-id", "A01-coordinator",
            "--platform", "hermes", "--session-id", "session-tamper",
            "--model", "fixture-model", "--provider", "fixture-provider",
        )
        source = self.project / "conversation.md"
        source.write_text("safe conversation", encoding="utf-8")
        archive = Path(self.command(
            "sync_conversation.py", "--project-root", str(self.project), "--agent-id", "A01-coordinator",
            "--source-file", str(source), "--platform", "hermes", "--session-id", "session-tamper",
            "--task-id", "UNSCOPED", "--message-start", "1", "--message-end", "1",
        ).stdout.strip())
        summary = self.project / "summary.md"
        headings = [
            "长期使命", "当前总目标", "当前任务与状态", "已确认需求", "关键决策及原因",
            "已完成事项", "修改文件", "命令与真实结果", "失败尝试和踩坑", "未解决事项",
            "风险与假设", "下一步", "恢复时必须读取", "可按需读取的原文",
        ]
        summary.write_text("\n\n".join(f"## {h}\n\n无" for h in headings), encoding="utf-8")
        checkpoint = Path(self.command(
            "create_checkpoint.py", "--project-root", str(self.project), "--agent-id", "A01-coordinator",
            "--summary-file", str(summary), "--source-archive", str(archive), "--task-id", "TASK-001",
        ).stdout.strip())
        checkpoint.write_text(checkpoint.read_text(encoding="utf-8") + "\n篡改内容\n", encoding="utf-8")
        result = self.command("validate_agents.py", "--project-root", str(self.project), ok=False)
        self.assertIn("content_sha256 mismatch", result.stdout)

    def test_checkpoint_requires_synced_archive(self) -> None:
        summary = self.project / "summary.md"
        summary.write_text("# incomplete", encoding="utf-8")
        result = self.command(
            "create_checkpoint.py", "--project-root", str(self.project), "--agent-id", "A01-coordinator",
            "--summary-file", str(summary), "--source-archive", str(self.project / "missing.md"), ok=False,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_parallel_identity_and_workspace_fail_closed(self) -> None:
        bad_project = Path(self.temp.name) / "bad"
        bad_project.mkdir()
        result = self.command(
            "init_project_agents.py", "--project-root", str(bad_project), "--project-id", "bad",
            "--project-name", "Bad", "--agents", "coordinator", "--user-confirmed", ok=False,
        )
        self.assertNotEqual(result.returncode, 0)
        result = self.command(
            "bind_session.py", "--project-root", str(self.project), "--agent-id", "A01-coordinator",
            "--platform", "hermes", "--session-id", "session-1", "--workspace", "/tmp", ok=False,
        )
        self.assertNotEqual(result.returncode, 0)

    def test_partial_initialization_never_publishes_team_success_marker(self) -> None:
        partial = Path(self.temp.name) / "partial"
        partial.mkdir()
        conflict = partial / ".multi-agent-collaboration" / "schemas"
        conflict.parent.mkdir()
        conflict.write_text("blocks schema directory", encoding="utf-8")
        result = self.command(
            "init_project_agents.py", "--project-root", str(partial), "--project-id", "partial",
            "--project-name", "Partial", "--agents", "A01-coordinator,A02-worker",
            "--user-confirmed", ok=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((partial / ".multi-agent-collaboration/TEAM.yaml").exists())

        legacy = Path(self.temp.name) / "legacy-partial"
        bus = legacy / ".multi-agent-collaboration"
        bus.mkdir(parents=True)
        (bus / "TEAM.yaml").write_text("{}\n", encoding="utf-8")
        retry = self.command(
            "init_project_agents.py", "--project-root", str(legacy), "--project-id", "legacy",
            "--project-name", "Legacy", "--agents", "A01-coordinator,A02-worker",
            "--user-confirmed", ok=False,
        )
        self.assertNotEqual(retry.returncode, 0)
        self.assertIn("不完整初始化", retry.stderr)


if __name__ == "__main__":
    unittest.main()
