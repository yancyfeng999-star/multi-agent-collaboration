from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.governance_test_support import governance_project, governance_root


ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def canonical_hash(profile: dict) -> str:
    unhashed = dict(profile)
    unhashed.pop("record_hash", None)
    payload = json.dumps(unhashed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ArchiveRuntimeBindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.governance = governance_root(self.temp.name)
        self.bus = governance_project(self.temp.name, self.project)
        (self.agent / "conversations").mkdir(parents=True)
        (self.agent / "runtime" / "profiles").mkdir(parents=True)
        profile = {
            "runtime_profile_id": "RP-000001", "agent_id": "A01-worker",
            "model": {"status": "known", "value": "hermes-4"},
            "provider": {"status": "known", "value": "nous"},
            "session": {"status": "known", "value": "session-1"},
        }
        digest = canonical_hash(profile)
        profile["record_hash"] = {"algorithm": "sha256", "canonicalization": "jcs-rfc8785", "value": digest}
        (self.agent / "runtime" / "profiles" / "RP-000001.json").write_text(json.dumps(profile), encoding="utf-8")
        self.mapping_path.write_text(json.dumps({
            "schema_version": "1.1", "agent_id": "A01-worker", "history": [],
            "active": {"platform": "hermes", "session_id": "session-1", "last_synced_message_id": 0,
                       "runtime_profile_id": "RP-000001", "runtime_profile_sha256": digest},
        }), encoding="utf-8")
        self.source = self.project / "conversation.json"
        self.source.write_text(json.dumps([{"id": 1, "role": "user", "content": "safe"}]), encoding="utf-8")

    @property
    def agent(self) -> Path:
        return self.bus / "agents" / "A01-worker"

    @property
    def mapping_path(self) -> Path:
        return self.agent / "conversations" / "SESSION_MAP.json"

    @property
    def profile_path(self) -> Path:
        active = json.loads(self.mapping_path.read_text(encoding="utf-8"))["active"]
        return self.agent / "runtime" / "profiles" / f"{active['runtime_profile_id']}.json"

    def command(self, script: str, *args: str, ok: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(["python3", str(SCRIPTS / script), *args], capture_output=True, text=True)
        if ok and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def sync(self, *, ok: bool = True) -> subprocess.CompletedProcess[str]:
        return self.command("sync_conversation.py", "--project-root", str(self.project), "--agent-id", "A01-worker",
                            "--source-file", str(self.source), "--platform", "hermes",
                            "--session-id", "session-1", "--governance-root", str(self.governance), ok=ok)

    def mapping(self) -> dict:
        return json.loads(self.mapping_path.read_text(encoding="utf-8"))

    def write_mapping(self, mapping: dict) -> None:
        self.mapping_path.write_text(json.dumps(mapping), encoding="utf-8")

    def rewrite_profile(self, mutate) -> None:
        profile = json.loads(self.profile_path.read_text(encoding="utf-8"))
        mutate(profile)
        digest = canonical_hash(profile)
        profile["record_hash"] = {"algorithm": "sha256", "canonicalization": "jcs-rfc8785", "value": digest}
        self.profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        mapping = self.mapping()
        mapping["active"]["runtime_profile_sha256"] = digest
        self.write_mapping(mapping)

    def test_archive_frontmatter_binds_profile_hash_and_known_actual_runtime(self) -> None:
        archive = Path(self.sync().stdout.strip()).read_text(encoding="utf-8")
        self.assertIn('runtime_profile_id: "RP-000001"', archive)
        self.assertRegex(archive, r'runtime_profile_sha256: "[a-f0-9]{64}"')
        self.assertIn('actual_model: "hermes-4"', archive)
        self.assertIn('actual_provider: "nous"', archive)
        self.assertIn('actual_model_status: "known"', archive)
        self.assertIn('actual_provider_status: "known"', archive)

    def test_archive_marks_unknown_and_conflict_without_inventing_values(self) -> None:
        def mutate(profile: dict) -> None:
            profile["model"] = {
                "status": "unknown", "value": None, "confidence": "none", "selected_source_ids": [],
                "conflict_candidate_ids": [], "unknown_reason_code": "U001_NOT_EXPOSED",
                "resolution_note": "actual runtime value was not exposed",
            }
            profile["provider"] = {
                "status": "conflict", "value": None, "confidence": "none", "selected_source_ids": [],
                "conflict_candidate_ids": ["CND-001", "CND-002"], "unknown_reason_code": None,
                "resolution_note": "actual observations disagree",
            }

        self.rewrite_profile(mutate)
        archive = Path(self.sync().stdout.strip()).read_text(encoding="utf-8")
        self.assertIn('actual_model_status: "unknown"', archive)
        self.assertIn("actual_model: null", archive)
        self.assertIn('actual_provider_status: "conflict"', archive)
        self.assertIn("actual_provider: null", archive)
        self.assertNotIn('actual_model: "unknown"', archive)
        self.assertNotIn('actual_provider: "conflict"', archive)

    def test_rejects_missing_active_runtime_reference_or_profile(self) -> None:
        mapping = self.mapping()
        del mapping["active"]["runtime_profile_id"]
        self.write_mapping(mapping)
        self.assertIn("runtime profile", self.sync(ok=False).stderr.lower())

    def test_rejects_missing_runtime_profile_file(self) -> None:
        self.profile_path.unlink()
        self.assertIn("runtime profile", self.sync(ok=False).stderr.lower())

    def test_rejects_profile_hash_agent_and_session_mismatch(self) -> None:
        mapping = self.mapping()
        mapping["active"]["runtime_profile_sha256"] = "0" * 64
        self.write_mapping(mapping)
        self.assertIn("hash", self.sync(ok=False).stderr.lower())

    def test_rejects_profile_agent_mismatch(self) -> None:
        self.rewrite_profile(lambda profile: profile.__setitem__("agent_id", "A02-wrong"))
        self.assertIn("agent", self.sync(ok=False).stderr.lower())

    def test_rejects_profile_session_mismatch(self) -> None:
        self.rewrite_profile(lambda profile: profile["session"].__setitem__("value", "wrong-session"))
        self.assertIn("session", self.sync(ok=False).stderr.lower())


if __name__ == "__main__":
    unittest.main()
