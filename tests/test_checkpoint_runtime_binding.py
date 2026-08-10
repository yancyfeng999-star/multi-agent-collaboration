from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.governance_test_support import governance_project, governance_root


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "create_checkpoint.py"
SCHEMA = ROOT / "assets" / "schemas" / "checkpoint.schema.json"
HEADINGS = [
    "长期使命", "当前总目标", "当前任务与状态", "已确认需求", "关键决策及原因",
    "已完成事项", "修改文件", "命令与真实结果", "失败尝试和踩坑", "未解决事项",
    "风险与假设", "下一步", "恢复时必须读取", "可按需读取的原文",
]


def canonical_hash(value: dict) -> str:
    unhashed = dict(value)
    unhashed.pop("record_hash", None)
    payload = json.dumps(unhashed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def frontmatter(path: Path) -> dict:
    block = path.read_text(encoding="utf-8").split("---\n", 2)[1]
    result = {}
    for line in block.splitlines():
        key, raw = line.split(":", 1)
        raw = raw.strip()
        result[key] = json.loads(raw) if raw.startswith(('"', "[", "{")) or raw in {"null", "true", "false"} else raw
    return result


class CheckpointRuntimeBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.governance = governance_root(self.temp.name)
        self.bus = governance_project(self.temp.name, self.project)
        self.agent = self.bus / "agents" / "A01-worker"
        (self.agent / "conversations/archive/2026-08").mkdir(parents=True)
        (self.agent / "conversations/checkpoints").mkdir(parents=True)
        (self.agent / "runtime/profiles").mkdir(parents=True)
        self.summary = self.project / "summary.md"
        self.summary.write_text("\n\n".join(f"## {heading}\n\n内容" for heading in HEADINGS), encoding="utf-8")
        self.profile_2, self.hash_2 = self.make_profile("RP-000002", "session-2", "model-2", "provider-2")
        self.profile_1, self.hash_1 = self.make_profile("RP-000001", "session-1", "model-1", "provider-1")
        self.archive_2 = self.make_archive("b.md", "body-b", "RP-000002", self.hash_2, "session-2", "model-2", "provider-2")
        self.archive_1 = self.make_archive("a.md", "body-a", "RP-000001", self.hash_1, "session-1", "model-1", "provider-1")

    def make_profile(self, profile_id: str, session: str, model: str, provider: str) -> tuple[Path, str]:
        profile = {
            "runtime_profile_id": profile_id, "agent_id": "A01-worker",
            "session": {"status": "known", "value": session},
            "model": {"status": "known", "value": model},
            "provider": {"status": "known", "value": provider},
        }
        digest = canonical_hash(profile)
        profile["record_hash"] = {"algorithm": "sha256", "canonicalization": "jcs-rfc8785", "value": digest}
        path = self.agent / "runtime/profiles" / f"{profile_id}.json"
        path.write_text(json.dumps(profile), encoding="utf-8")
        return path, digest

    def make_archive(self, name: str, body: str, profile_id: str, profile_hash: str,
                     session: str, model: str, provider: str) -> Path:
        digest = hashlib.sha256(body.encode()).hexdigest()
        path = self.agent / "conversations/archive/2026-08" / name
        path.write_text(
            '---\nschema_version: "1.0"\ndoc_type: "conversation_archive"\n'
            'agent_id: "A01-worker"\n'
            f'session_id: {json.dumps(session)}\n'
            f'runtime_profile_id: "{profile_id}"\n'
            f'runtime_profile_sha256: "{profile_hash}"\n'
            'actual_model_status: "known"\n'
            f'actual_model: {json.dumps(model)}\n'
            'actual_provider_status: "known"\n'
            f'actual_provider: {json.dumps(provider)}\n'
            f'content_sha256: "{digest}"\n---\n\n# 完整对话归档\n\n{body}\n',
            encoding="utf-8",
        )
        return path

    def run_checkpoint(self, *archives: Path) -> subprocess.CompletedProcess[str]:
        command = ["python3", str(SCRIPT), "--project-root", str(self.project), "--agent-id", "A01-worker",
                   "--governance-root", str(self.governance), "--summary-file", str(self.summary)]
        for archive in archives:
            command += ["--source-archive", str(archive)]
        return subprocess.run(command, capture_output=True, text=True)

    def assert_failed_without_publication(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.agent / "conversations/checkpoints/CP-0001.md").exists())
        self.assertFalse((self.agent / "conversations/CURRENT_CONTEXT.md").exists())

    def test_collects_deduplicated_runtime_profile_paths_and_hashes_in_stable_order(self) -> None:
        duplicate = self.make_archive("c.md", "body-c", "RP-000001", self.hash_1,
                                      "session-1", "model-1", "provider-1")
        result = self.run_checkpoint(self.archive_2, duplicate, self.archive_1)
        self.assertEqual(result.returncode, 0, result.stderr)
        meta = frontmatter(Path(result.stdout.strip()))
        paths = [
            "agents/A01-worker/runtime/profiles/RP-000001.json",
            "agents/A01-worker/runtime/profiles/RP-000002.json",
        ]
        self.assertEqual(meta["source_runtime_profiles"], paths)
        self.assertEqual(meta["source_runtime_profile_hashes"], {paths[0]: self.hash_1, paths[1]: self.hash_2})

    def test_rejects_missing_profile_and_declared_hash_mismatch(self) -> None:
        self.profile_1.unlink()
        result = self.run_checkpoint(self.archive_1)
        self.assert_failed_without_publication(result)
        self.assertIn("runtime profile does not exist", result.stderr.lower())

        self.profile_1, self.hash_1 = self.make_profile("RP-000001", "session-1", "model-1", "provider-1")
        bad = self.make_archive("bad-hash.md", "bad", "RP-000001", "0" * 64,
                                "session-1", "model-1", "provider-1")
        result = self.run_checkpoint(bad)
        self.assert_failed_without_publication(result)
        self.assertIn("runtime profile hash", result.stderr.lower())

    def test_rejects_profile_content_hash_agent_and_session_mismatch(self) -> None:
        profile = json.loads(self.profile_1.read_text(encoding="utf-8"))
        profile["model"]["value"] = "tampered"
        self.profile_1.write_text(json.dumps(profile), encoding="utf-8")
        result = self.run_checkpoint(self.archive_1)
        self.assert_failed_without_publication(result)
        self.assertIn("runtime profile hash", result.stderr.lower())

        self.profile_1, self.hash_1 = self.make_profile("RP-000001", "session-1", "model-1", "provider-1")
        profile = json.loads(self.profile_1.read_text(encoding="utf-8"))
        profile["agent_id"] = "A02-other"
        digest = canonical_hash(profile)
        profile["record_hash"]["value"] = digest
        self.profile_1.write_text(json.dumps(profile), encoding="utf-8")
        bad_agent = self.make_archive("bad-agent.md", "bad-agent", "RP-000001", digest,
                                      "session-1", "model-1", "provider-1")
        result = self.run_checkpoint(bad_agent)
        self.assert_failed_without_publication(result)
        self.assertIn("agent", result.stderr.lower())

        self.profile_1, self.hash_1 = self.make_profile("RP-000001", "session-1", "model-1", "provider-1")
        bad_session = self.make_archive("bad-session.md", "bad-session", "RP-000001", self.hash_1,
                                        "wrong-session", "model-1", "provider-1")
        result = self.run_checkpoint(bad_session)
        self.assert_failed_without_publication(result)
        self.assertIn("session", result.stderr.lower())

    def test_rejects_archive_model_provider_redundancy_mismatch(self) -> None:
        for field, model, provider in (("model", "wrong", "provider-1"), ("provider", "model-1", "wrong")):
            with self.subTest(field=field):
                archive = self.make_archive(f"bad-{field}.md", field, "RP-000001", self.hash_1,
                                            "session-1", model, provider)
                result = self.run_checkpoint(archive)
                self.assert_failed_without_publication(result)
                self.assertIn(field, result.stderr.lower())

    def test_failure_preserves_historical_checkpoint_and_current_context(self) -> None:
        first = self.run_checkpoint(self.archive_1)
        self.assertEqual(first.returncode, 0, first.stderr)
        checkpoint = Path(first.stdout.strip())
        checkpoint_before = checkpoint.read_bytes()
        current = self.agent / "conversations/CURRENT_CONTEXT.md"
        current_before = current.read_bytes()
        self.profile_1.unlink()
        failed = self.run_checkpoint(self.archive_1)
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(checkpoint.read_bytes(), checkpoint_before)
        self.assertEqual(current.read_bytes(), current_before)
        self.assertFalse((checkpoint.parent / "CP-0002.md").exists())

    def test_schema_requires_runtime_profile_bindings(self) -> None:
        schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertIn("source_runtime_profiles", schema["required"])
        self.assertIn("source_runtime_profile_hashes", schema["required"])
        self.assertTrue(schema["properties"]["source_runtime_profiles"]["uniqueItems"])
        self.assertEqual(schema["properties"]["source_runtime_profile_hashes"]["additionalProperties"]["pattern"],
                         "^[a-f0-9]{64}$")


if __name__ == "__main__":
    unittest.main()
