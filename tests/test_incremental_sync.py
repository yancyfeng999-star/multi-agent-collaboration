from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"


class IncrementalConversationSyncTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.command("init_project_agents.py", "--project-root", str(self.project), "--project-id", "fixture",
                     "--project-name", "Fixture", "--agents", "A01-worker", "--user-confirmed")
        self.command("bind_session.py", "--project-root", str(self.project), "--agent-id", "A01-worker",
                     "--platform", "hermes", "--session-id", "session-1",
                     "--model", "fixture-model", "--provider", "fixture-provider")
        self.source = self.project / "conversation.json"

    @property
    def agent(self) -> Path:
        return self.project / ".multi-agent-collaboration" / "agents" / "A01-worker"

    @property
    def mapping_path(self) -> Path:
        return self.agent / "conversations" / "SESSION_MAP.json"

    def command(self, script: str, *args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(["python3", str(SCRIPTS / script), *args], capture_output=True, text=True)
        if ok and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def sync(self, *extra: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
        return self.command("sync_conversation.py", "--project-root", str(self.project), "--agent-id", "A01-worker",
                        "--source-file", str(self.source), "--platform", "hermes", "--session-id", "session-1",
                        *extra, ok=ok)

    def write_messages(self, *messages: tuple[int, str]) -> None:
        self.source.write_text(json.dumps([
            {"id": message_id, "role": "user", "content": content} for message_id, content in messages
        ]), encoding="utf-8")

    def active(self) -> dict:
        return json.loads(self.mapping_path.read_text(encoding="utf-8"))["active"]

    def archives(self) -> list[Path]:
        return list((self.agent / "conversations" / "archive").glob("*/*.md"))

    def test_full_json_export_is_incremental_and_duplicate_sync_is_noop(self) -> None:
        self.write_messages((1, "one"), (2, "two"))
        first = self.sync()
        first_archive = Path(first.stdout.strip())
        self.assertEqual(self.active()["last_synced_message_id"], 2)
        self.assertEqual(len(self.archives()), 1)

        duplicate = self.sync()
        self.assertEqual(Path(duplicate.stdout.strip()), first_archive)
        self.assertEqual(len(self.archives()), 1)

        self.write_messages((1, "one"), (2, "two"), (3, "three"))
        second_archive = Path(self.sync().stdout.strip())
        body = second_archive.read_text(encoding="utf-8")
        self.assertIn("three", body)
        self.assertNotIn("\none\n", body)
        self.assertEqual(self.active()["last_synced_message_id"], 3)
        self.assertIn("source_file_sha256", body)
        self.assertIn("normalized_body_sha256", body)
        self.assertIn("message_count: 1", body)

    def test_rejects_backward_overlap_mismatch_and_gap_unless_allowed(self) -> None:
        self.write_messages((1, "one"), (2, "two"))
        self.sync()

        self.write_messages((1, "one"))
        self.assertIn("backward", self.sync(ok=False).stderr.lower())
        self.write_messages((1, "changed"), (2, "two"), (3, "three"))
        self.assertIn("overlap", self.sync(ok=False).stderr.lower())
        self.write_messages((1, "one"), (2, "two"), (4, "four"))
        self.assertIn("gap", self.sync(ok=False).stderr.lower())
        allowed_archive = Path(self.sync("--allow-gap").stdout.strip())
        self.assertIn("gap_allowed: true", allowed_archive.read_text(encoding="utf-8"))
        self.assertEqual(self.active()["sync_gaps"][-1]["missing_message_ids"], [3])
        self.assertEqual(self.active()["last_synced_message_id"], 4)

    def test_text_requires_explicit_contiguous_range(self) -> None:
        self.source = self.project / "conversation.md"
        self.source.write_text("first", encoding="utf-8")
        self.assertIn("message-start", self.sync(ok=False).stderr)
        self.sync("--message-start", "1", "--message-end", "1")
        self.source.write_text("third", encoding="utf-8")
        self.assertIn("gap", self.sync("--message-start", "3", "--message-end", "3", ok=False).stderr.lower())
        self.sync("--message-start", "3", "--message-end", "3", "--allow-gap")
        self.assertEqual(self.active()["last_synced_message_id"], 3)

    def test_archive_failure_does_not_advance_cursor(self) -> None:
        import importlib.util
        import sys

        sys.path.insert(0, str(SCRIPTS))
        self.addCleanup(lambda: sys.path.remove(str(SCRIPTS)))
        spec = importlib.util.spec_from_file_location("sync_conversation_under_test", SCRIPTS / "sync_conversation.py")
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        self.write_messages((1, "one"))
        args = module.parser().parse_args([
            "--project-root", str(self.project), "--agent-id", "A01-worker", "--source-file", str(self.source),
            "--platform", "hermes", "--session-id", "session-1",
        ])
        with mock.patch.object(module, "atomic_write", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                module.sync(args)
        self.assertEqual(self.active()["last_synced_message_id"], 0)
        self.assertEqual(self.archives(), [])

    def test_session_map_failure_removes_archive_and_does_not_advance_cursor(self) -> None:
        import importlib.util
        import sys

        sys.path.insert(0, str(SCRIPTS))
        self.addCleanup(lambda: sys.path.remove(str(SCRIPTS)))
        spec = importlib.util.spec_from_file_location("sync_conversation_map_failure", SCRIPTS / "sync_conversation.py")
        assert spec is not None
        module = importlib.util.module_from_spec(spec)
        assert spec.loader
        spec.loader.exec_module(module)
        self.write_messages((1, "one"))
        args = module.parser().parse_args([
            "--project-root", str(self.project), "--agent-id", "A01-worker", "--source-file", str(self.source),
            "--platform", "hermes", "--session-id", "session-1",
        ])
        real_atomic_write = module.atomic_write

        def fail_mapping(path: Path, content: str) -> None:
            if path.name == "SESSION_MAP.json":
                raise OSError("mapping write failed")
            real_atomic_write(path, content)

        with mock.patch.object(module, "atomic_write", side_effect=fail_mapping):
            with self.assertRaises(OSError):
                module.sync(args)
        self.assertEqual(self.active()["last_synced_message_id"], 0)
        self.assertEqual(self.archives(), [])

    def test_no_redact_requires_confirmation_reason_and_still_blocks_secret(self) -> None:
        self.write_messages((1, "safe"))
        self.assertIn("reason", self.sync("--no-redact", ok=False).stderr.lower())
        self.assertIn("confirm", self.sync("--no-redact", "--no-redact-reason", "public transcript", ok=False).stderr.lower())
        self.sync("--no-redact", "--no-redact-reason", "public transcript", "--confirm-no-redact")

        # A fresh session is needed because message 1 is already synced.
        mapping = json.loads(self.mapping_path.read_text(encoding="utf-8"))
        mapping["active"]["last_synced_message_id"] = 0
        mapping["active"].pop("synced_message_hashes", None)
        self.mapping_path.write_text(json.dumps(mapping), encoding="utf-8")
        self.write_messages((1, "api_key=sk-abcdefghijklmnopqrstuvwxyz"))
        result = self.sync("--no-redact", "--no-redact-reason", "debug fixture", "--confirm-no-redact", ok=False)
        self.assertIn("secret", result.stderr.lower())

    def test_path_like_task_id_is_rejected_before_archive_write(self) -> None:
        self.write_messages((1, "safe"))
        result = self.sync("--task-id", "../../outside", ok=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid --task-id", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(self.archives(), [])


if __name__ == "__main__":
    unittest.main()
