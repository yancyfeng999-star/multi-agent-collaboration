from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from tests.governance_test_support import governance_project, governance_root


SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts" / "create_checkpoint.py"
HEADINGS = [
    "长期使命", "当前总目标", "当前任务与状态", "已确认需求", "关键决策及原因",
    "已完成事项", "修改文件", "命令与真实结果", "失败尝试和踩坑", "未解决事项",
    "风险与假设", "下一步", "恢复时必须读取", "可按需读取的原文",
]


def frontmatter(path: Path) -> dict:
    block = path.read_text(encoding="utf-8").split("---\n", 2)[1]
    result = {}
    for line in block.splitlines():
        key, raw = line.split(":", 1)
        raw = raw.strip()
        result[key] = json.loads(raw) if raw.startswith(('"', "[", "{")) or raw in {"null", "true", "false"} else raw
    return result


class CheckpointIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.governance = governance_root(self.temp.name)
        self.bus = governance_project(self.temp.name, self.project)
        self.agent = self.bus / "agents" / "A01-test"
        (self.agent / "conversations" / "archive" / "2026-08").mkdir(parents=True)
        (self.agent / "conversations" / "checkpoints").mkdir(parents=True)
        (self.agent / "runtime" / "profiles").mkdir(parents=True)
        self.summary = self.project / "summary.md"
        values = {
            "当前任务与状态": "实现 checkpoint 完整性；状态：进行中",
            "关键决策及原因": "使用 Agent 级 flock，避免编号竞争",
            "修改文件": "scripts/create_checkpoint.py",
            "命令与真实结果": "unittest: PASS",
            "未解决事项": "补充发布说明",
            "风险与假设": "仅支持 POSIX flock",
            "下一步": "运行全量测试",
        }
        self.summary.write_text(
            "\n\n".join(f"## {heading}\n\n{values.get(heading, '无')}" for heading in HEADINGS),
            encoding="utf-8",
        )
        profile = {
            "runtime_profile_id": "RP-000001", "agent_id": "A01-test",
            "session": {"status": "known", "value": "session-1"},
            "model": {"status": "known", "value": "hermes-4"},
            "provider": {"status": "known", "value": "nous"},
        }
        payload = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.runtime_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        profile["record_hash"] = {
            "algorithm": "sha256", "canonicalization": "jcs-rfc8785", "value": self.runtime_hash,
        }
        (self.agent / "runtime" / "profiles" / "RP-000001.json").write_text(json.dumps(profile), encoding="utf-8")
        self.archive = self.make_archive("archive.md", "conversation body")

    def make_archive(self, name: str, body: str) -> Path:
        digest = hashlib.sha256(body.encode()).hexdigest()
        path = self.agent / "conversations" / "archive" / "2026-08" / name
        path.write_text(
            '---\nschema_version: "1.0"\ndoc_type: "conversation_archive"\n'
            'agent_id: "A01-test"\n'
            'session_id: "session-1"\nruntime_profile_id: "RP-000001"\n'
            f'runtime_profile_sha256: "{self.runtime_hash}"\n'
            'actual_model_status: "known"\nactual_model: "hermes-4"\n'
            'actual_provider_status: "known"\nactual_provider: "nous"\n'
            f'content_sha256: "{digest}"\n---\n\n# 完整对话归档\n\n{body}\n',
            encoding="utf-8",
        )
        return path

    def run_checkpoint(self, archive: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(SCRIPT), "--project-root", str(self.project), "--agent-id", "A01-test",
             "--governance-root", str(self.governance),
             "--summary-file", str(self.summary), "--source-archive", str(archive or self.archive),
             "--task-id", "TASK-1"],
            capture_output=True, text=True,
        )

    def test_concurrent_creation_serializes_number_write_and_current_update(self) -> None:
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.run_checkpoint(), range(8)))
        self.assertTrue(all(result.returncode == 0 for result in results), [r.stderr for r in results])
        paths = sorted((self.agent / "conversations" / "checkpoints").glob("CP-*.md"))
        self.assertEqual([p.stem for p in paths], [f"CP-{n:04d}" for n in range(1, 9)])
        for index, path in enumerate(paths):
            self.assertEqual(frontmatter(path)["previous_checkpoint"], None if index == 0 else paths[index - 1].stem)
        current = frontmatter(self.agent / "conversations" / "CURRENT_CONTEXT.md")
        self.assertEqual(current["latest_checkpoint"], "conversations/checkpoints/CP-0008.md")

    def test_checkpoint_binds_archive_hash_and_rejects_tampered_archive(self) -> None:
        first = self.run_checkpoint()
        self.assertEqual(first.returncode, 0, first.stderr)
        meta = frontmatter(Path(first.stdout.strip()))
        relative = self.archive.relative_to(self.bus).as_posix()
        self.assertEqual(meta["source_archive_hashes"], {relative: hashlib.sha256(b"conversation body").hexdigest()})

        self.archive.write_text(self.archive.read_text(encoding="utf-8") + "tampered", encoding="utf-8")
        second = self.run_checkpoint()
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("content_sha256 mismatch", second.stderr)
        self.assertFalse((self.agent / "conversations" / "checkpoints" / "CP-0002.md").exists())
        self.assertEqual(frontmatter(self.agent / "conversations" / "CURRENT_CONTEXT.md")["latest_checkpoint"],
                         "conversations/checkpoints/CP-0001.md")

    def test_current_context_contains_structured_summary_and_chain_supports_three_levels(self) -> None:
        for _ in range(3):
            result = self.run_checkpoint()
            self.assertEqual(result.returncode, 0, result.stderr)
        cp3 = frontmatter(self.agent / "conversations" / "checkpoints" / "CP-0003.md")
        self.assertEqual(cp3["previous_checkpoint"], "CP-0002")
        current = (self.agent / "conversations" / "CURRENT_CONTEXT.md").read_text(encoding="utf-8")
        for heading, content in [
            ("当前任务", "实现 checkpoint 完整性"), ("决策", "Agent 级 flock"),
            ("待完成", "补充发布说明"), ("关键文件", "create_checkpoint.py"),
            ("验证", "unittest: PASS"), ("风险", "POSIX flock"), ("下一步", "运行全量测试"),
        ]:
            self.assertIn(f"## {heading}", current)
            self.assertIn(content, current)

    def test_current_update_failure_rolls_back_checkpoint_and_temp_files(self) -> None:
        current = self.agent / "conversations" / "CURRENT_CONTEXT.md"
        current.mkdir()
        result = self.run_checkpoint()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(list((self.agent / "conversations" / "checkpoints").glob("CP-*.md")), [])
        leftovers = list(self.agent.rglob("*.tmp")) + list(self.agent.rglob(".*.tmp"))
        self.assertEqual(leftovers, [])

    def test_schema_requires_archive_hash_map(self) -> None:
        schema = json.loads((SKILL / "assets" / "schemas" / "checkpoint.schema.json").read_text(encoding="utf-8"))
        self.assertIn("source_archive_hashes", schema["required"])
        prop = schema["properties"]["source_archive_hashes"]
        self.assertEqual(prop["type"], "object")
        self.assertEqual(prop["additionalProperties"]["pattern"], "^[a-f0-9]{64}$")

    def test_rejects_archive_from_another_agent(self) -> None:
        other = self.bus / "agents/A02-other/conversations/archive/2026-08"
        other.mkdir(parents=True)
        body = "other agent conversation"
        archive = other / "other.md"
        archive.write_text(
            '---\nschema_version: "1.0"\ndoc_type: "conversation_archive"\nagent_id: "A02-other"\n'
            f'content_sha256: "{hashlib.sha256(body.encode()).hexdigest()}"\n---\n\n{body}\n', encoding="utf-8"
        )
        result = self.run_checkpoint(archive)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not belong", result.stderr)


if __name__ == "__main__":
    unittest.main()
