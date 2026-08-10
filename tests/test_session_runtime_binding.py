from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.governance_test_support import governance_root


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import bind_session  # noqa: E402


class SessionRuntimeBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.governance = governance_root(self.temp.name)
        self.command(
            "init_project_agents.py", "--project-root", str(self.project),
            "--project-id", "fixture", "--project-name", "Fixture",
            "--agents", "A01-worker", "--user-confirmed",
            "--governance-root", str(self.governance),
        )

    @property
    def agent(self) -> Path:
        return self.governance / "projects" / "fixture" / "agents" / "A01-worker"

    @property
    def mapping_path(self) -> Path:
        return self.agent / "conversations" / "SESSION_MAP.json"

    @property
    def runtime(self) -> Path:
        return self.agent / "runtime"

    def command(
        self, script: str, *args: str, ok: bool = True,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        clean_env = os.environ.copy()
        clean_env["HOME"] = self.temp.name
        for key in (
            "HERMES_MODEL", "HERMES_PROVIDER", "CODEX_MODEL", "CODEX_PROVIDER",
            "CLAUDE_MODEL", "CLAUDE_PROVIDER", "AGENT_MODEL", "AGENT_PROVIDER",
        ):
            clean_env.pop(key, None)
        if env:
            clean_env.update(env)
        result = subprocess.run(
            ["python3", str(SCRIPTS / script), *args],
            capture_output=True, text=True, env=clean_env,
        )
        if ok and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def bind(self, session_id: str, *extra: str, ok: bool = True, env: dict[str, str] | None = None):
        return self.command(
            "bind_session.py", "--project-root", str(self.project),
            "--agent-id", "A01-worker", "--platform", "hermes",
            "--session-id", session_id, "--governance-root", str(self.governance),
            *extra, ok=ok, env=env,
        )

    def test_binding_detects_records_and_references_runtime_profile(self) -> None:
        self.bind("session-1", "--model", "hermes-4", "--provider", "nous")

        mapping = json.loads(self.mapping_path.read_text(encoding="utf-8"))
        active = mapping["active"]
        self.assertEqual(mapping["schema_version"], "1.1")
        self.assertEqual(active["runtime_profile_id"], "RP-000001")
        profile = json.loads((self.runtime / "profiles/RP-000001.json").read_text(encoding="utf-8"))
        self.assertEqual(active["runtime_profile_sha256"], profile["record_hash"]["value"])
        self.assertEqual(profile["model"]["value"], "hermes-4")
        self.assertEqual(profile["provider"]["value"], "nous")
        self.assertEqual(profile["session"]["value"], "session-1")
        self.assertEqual(profile["runtime_kind"]["value"], "hermes-thread")

    def test_rebinding_preserves_old_runtime_reference_and_sets_ended_at(self) -> None:
        self.bind("session-1", "--model", "hermes-4", "--provider", "nous")
        first = json.loads(self.mapping_path.read_text(encoding="utf-8"))["active"]
        self.bind("session-2", "--model", "hermes-5", "--provider", "nous")

        mapping = json.loads(self.mapping_path.read_text(encoding="utf-8"))
        old = mapping["history"][0]
        self.assertEqual(mapping["active"]["runtime_profile_id"], "RP-000002")
        self.assertEqual(old["runtime_profile_id"], first["runtime_profile_id"])
        self.assertEqual(old["runtime_profile_sha256"], first["runtime_profile_sha256"])
        self.assertEqual(old["superseded_by"], "session-2")
        self.assertTrue(old["ended_at"])

    def test_unknown_model_provider_returns_structured_requirement_without_partial_publish(self) -> None:
        before = self.mapping_path.read_bytes()
        result = self.bind("session-unknown", ok=False)

        self.assertNotEqual(result.returncode, 0)
        requirement = json.loads(result.stderr)
        self.assertEqual(requirement["code"], "RUNTIME_METADATA_REQUIRED")
        self.assertEqual(requirement["status"], "explicit_input_required")
        self.assertEqual(requirement["fields"], ["model", "provider"])
        self.assertEqual(self.mapping_path.read_bytes(), before)
        self.assertFalse((self.runtime / "profiles").exists())

    def test_conflicting_probe_requires_explicit_resolution_without_partial_publish(self) -> None:
        result = self.bind(
            "session-conflict", "--model", "hermes-4", "--provider", "nous", ok=False,
            env={"HERMES_MODEL": "other-model"},
        )

        requirement = json.loads(result.stderr)
        self.assertEqual(requirement["code"], "RUNTIME_METADATA_REQUIRED")
        self.assertEqual(requirement["reason"], "conflict")
        self.assertEqual(requirement["fields"], ["model"])
        self.assertFalse((self.runtime / "profiles").exists())
        self.assertIsNone(json.loads(self.mapping_path.read_text(encoding="utf-8"))["active"])

    def test_existing_workspace_and_duplicate_session_gates_run_before_profile_publication(self) -> None:
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        bad = self.bind(
            "outside", "--model", "hermes-4", "--provider", "nous",
            "--workspace", str(outside), ok=False,
        )
        self.assertNotEqual(bad.returncode, 0)
        self.assertFalse((self.runtime / "profiles").exists())

        self.bind("session-1", "--model", "hermes-4", "--provider", "nous")
        duplicate = self.bind("session-1", "--model", "hermes-5", "--provider", "nous", ok=False)
        self.assertNotEqual(duplicate.returncode, 0)
        profiles = sorted((self.runtime / "profiles").glob("RP-*.json"))
        self.assertEqual([path.name for path in profiles], ["RP-000001.json"])

    def test_session_map_publish_failure_rolls_back_runtime_ledger_and_retry_reuses_first_id(self) -> None:
        before = self.mapping_path.read_bytes()
        argv = [
            "bind_session.py", "--project-root", str(self.project),
            "--agent-id", "A01-worker", "--platform", "hermes",
            "--session-id", "session-transaction", "--model", "hermes-4",
            "--provider", "nous", "--governance-root", str(self.governance),
        ]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(
            bind_session, "write_json", side_effect=OSError("injected session map publish failure")
        ):
            self.assertEqual(bind_session.main(), 1)

        self.assertEqual(self.mapping_path.read_bytes(), before)
        self.assertFalse((self.runtime / "profiles/RP-000001.json").exists())
        self.assertFalse((self.runtime / "RUNTIME_INDEX.jsonl").exists())
        self.assertFalse((self.runtime / "CURRENT_RUNTIME.json").exists())

        self.bind("session-transaction", "--model", "hermes-4", "--provider", "nous")
        self.assertTrue((self.runtime / "profiles/RP-000001.json").is_file())
        self.assertFalse((self.runtime / "profiles/RP-000002.json").exists())

    def test_hermes_declared_defaults_do_not_satisfy_actual_runtime_identity(self) -> None:
        config = Path(self.temp.name) / ".hermes" / "config.yaml"
        config.parent.mkdir(parents=True)
        config.write_text(
            "model:\n  default: declared-model\n  provider: declared-provider\n",
            encoding="utf-8",
        )

        result = self.bind("session-declared-only", ok=False)
        self.assertEqual(result.returncode, 2)
        requirement = json.loads(result.stderr)
        self.assertEqual(requirement["code"], "RUNTIME_METADATA_REQUIRED")
        self.assertEqual(requirement["reason"], "unknown")
        self.assertEqual(requirement["fields"], ["model", "provider"])
        self.assertFalse((self.runtime / "profiles").exists())


if __name__ == "__main__":
    unittest.main()
