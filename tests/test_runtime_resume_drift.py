from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.governance_test_support import governance_root


SKILL = Path(__file__).resolve().parents[1]
RESUME = SKILL / "scripts" / "resume_brief.py"
INIT = SKILL / "scripts" / "init_project_agents.py"


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class RuntimeResumeDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.governance = governance_root(self.temp.name)
        result = subprocess.run([
            "python3", str(INIT), "--project-root", str(self.project), "--project-id", "fixture",
            "--project-name", "Fixture", "--agents", "A01-worker", "--user-confirmed",
            "--governance-root", str(self.governance),
        ], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.agent = self.governance / "projects" / "fixture" / "agents" / "A01-worker"

    def write_runtime(self, *, model_status: str = "known") -> Path:
        def resolved(value: str | None) -> dict:
            return {"status": "known" if value else "unknown", "value": value}

        profile = {
            "runtime_profile_id": "RP-000001", "agent_id": "A01-worker",
            "capture_status": {"code": "S000", "name": "complete"},
            "captured_at": "2026-08-06T16:00:00Z",
            "model": resolved("gpt-old" if model_status == "known" else None),
            "provider": resolved("provider-old"), "platform": resolved("hermes"),
            "session": resolved("session-old"), "profile": resolved("default"),
        }
        profile["record_hash"] = {"algorithm": "sha256", "canonicalization": "jcs-rfc8785", "value": digest(profile)}
        runtime = self.agent / "runtime"
        path = runtime / "profiles" / "RP-000001.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(profile) + "\n", encoding="utf-8")
        pointer = {"runtime_profile_id": "RP-000001", "record_hash": profile["record_hash"]["value"], "path": "profiles/RP-000001.json"}
        (runtime / "CURRENT_RUNTIME.json").write_text(json.dumps(pointer) + "\n", encoding="utf-8")
        return path

    def write_activity(self) -> None:
        receipt = self.agent / "receipts" / "usage.json"
        receipt.parent.mkdir(parents=True)
        receipt.write_text('{"input_tokens":12,"output_tokens":3}\n', encoding="utf-8")
        record = {
            "activity_id": "ACTIVITY-000001", "recorded_at": "2026-08-06T16:01:00Z",
            "record_kind": "attempt_progress", "run_id": "RUN-1", "task_id": "TASK-1",
            "attempt_id": "ATTEMPT-1", "status": {"attempt_status": "running", "summary": "working"},
            "usage": {"usage_source": "provider_response", "source_ref": "receipts/usage.json",
                      "source_sha256": hashlib.sha256(receipt.read_bytes()).hexdigest(), "total_tokens": 15},
            "record_sha256": None,
        }
        record["record_sha256"] = digest({key: value for key, value in record.items() if key != "record_sha256"})
        ledger = self.agent / "activity" / "RUN-1" / "TASK-1" / "ATTEMPT-1"
        path = ledger / "2026" / "08" / "06" / "ACTIVITY-000001.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        (ledger / "CURRENT.json").write_text(json.dumps({"activity_id": record["activity_id"], "record_sha256": record["record_sha256"], "path": "2026/08/06/ACTIVITY-000001.json"}) + "\n", encoding="utf-8")

    def resume(self, env: dict[str, str]) -> tuple[subprocess.CompletedProcess[str], dict, str]:
        clean_env = {key: value for key, value in os.environ.items() if not key.startswith(("HERMES_", "CODEX_", "CLAUDE_CODE_"))}
        result = subprocess.run([
            "python3", str(RESUME), "--project-root", str(self.project), "--agent-id", "A01-worker", "--detect-drift",
            "--governance-root", str(self.governance),
        ], capture_output=True, text=True, env={**clean_env, **env})
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        output = Path(result.stdout.strip())
        text = output.read_text(encoding="utf-8")
        block = text.split("---\n", 2)[1]
        frontmatter = {key: json.loads(raw.strip()) for key, raw in (line.split(":", 1) for line in block.splitlines())}
        return result, frontmatter, text

    def test_verified_runtime_activity_and_usage_are_shown_and_known_changes_drift(self) -> None:
        self.write_runtime()
        self.write_activity()
        _result, meta, text = self.resume({
            "HERMES_MODEL": "gpt-new", "HERMES_PROVIDER": "provider-new",
            "HERMES_SESSION_ID": "session-new", "HERMES_PROFILE": "default",
        })
        runtime = meta["runtime"]
        self.assertTrue(runtime["profile_verified"])
        self.assertEqual(runtime["runtime_profile_id"], "RP-000001")
        self.assertEqual(runtime["actual"]["provider"], "provider-new")
        self.assertEqual(runtime["actual"]["status"], "insufficient")
        self.assertEqual(runtime["actual"]["platform"], "hermes")
        self.assertEqual(runtime["actual"]["session"], "session-new")
        self.assertEqual(set(runtime["drift_fields"]), {"model", "provider", "session"})
        self.assertTrue(meta["drift"]["detected"])
        self.assertEqual(meta["recent_activity"]["activity_id"], "ACTIVITY-000001")
        self.assertTrue(meta["recent_activity"]["verified"])
        self.assertEqual(meta["recent_activity"]["usage_source"], "provider_response")
        self.assertTrue(meta["recent_activity"]["usage_source_verified"])
        self.assertIn("## Runtime Profile", text)
        self.assertIn("## 最近 Activity 与 Usage", text)

    def test_unknown_actual_does_not_count_as_change(self) -> None:
        self.write_runtime()
        _result, meta, _text = self.resume({"HERMES_PROFILE": "default"})
        self.assertIsNone(meta["runtime"]["actual"]["model"])
        self.assertNotIn("model", meta["runtime"]["drift_fields"])
        self.assertEqual(meta["runtime"]["comparison"]["model"], "unknown")

    def test_unverified_hashes_and_secret_shaped_values_are_not_published(self) -> None:
        profile_path = self.write_runtime()
        profile = json.loads(profile_path.read_text())
        secret = "sk-abcdefghijklmnopqrstuvwxyz1234567890"
        profile["provider"]["value"] = secret
        profile_path.write_text(json.dumps(profile) + "\n", encoding="utf-8")
        _result, meta, text = self.resume({"HERMES_PROVIDER": secret})
        self.assertFalse(meta["runtime"]["profile_verified"])
        self.assertIsNone(meta["runtime"]["stored"]["provider"])
        self.assertIsNone(meta["runtime"]["actual"]["provider"])
        self.assertNotIn(secret, text)
        self.assertEqual(meta["runtime"]["drift_fields"], [])


if __name__ == "__main__":
    unittest.main()
