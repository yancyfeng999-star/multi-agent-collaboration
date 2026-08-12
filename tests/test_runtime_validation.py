from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from tests.governance_test_support import governance_root

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("validate_agents", SCRIPTS / "validate_agents.py")
assert SPEC and SPEC.loader
VALIDATOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(VALIDATOR)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_hash(record: dict, field: str) -> str:
    value = json.loads(json.dumps(record))
    if field == "record_sha256":
        value[field] = None
    else:
        value.pop(field)
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


class StdlibSchemaFallbackTests(unittest.TestCase):
    def test_combinators_conditionals_and_constraints_are_not_skipped(self) -> None:
        cases = [
            (1, {"oneOf": [{"type": "number"}, {"minimum": 0}]}, "oneOf"),
            (1, {"allOf": [{"minimum": 2}]}, "minimum"),
            (1, {"anyOf": [{"type": "string"}, {"minimum": 2}]}, "anyOf"),
            ("secret", {"not": {"const": "secret"}}, "not"),
            ({"kind": "x"}, {"if": {"properties": {"kind": {"const": "x"}}}, "then": {"required": ["value"]}, "else": {"required": ["other"]}}, "value"),
            ({"kind": "y"}, {"if": {"properties": {"kind": {"const": "x"}}}, "then": {"required": ["value"]}, "else": {"required": ["other"]}}, "other"),
            ({"a": 1}, {"type": "object", "minProperties": 2}, "minProperties"),
            ([1, 1], {"type": "array", "uniqueItems": True}, "unique"),
            ("not-a-time", {"type": "string", "format": "date-time"}, "date-time"),
            (-1, {"type": "integer", "minimum": 0}, "minimum"),
        ]
        for value, schema, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, "\n".join(VALIDATOR.schema_errors(value, schema)))


class RuntimeValidationGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.project.mkdir()
        self.governance = governance_root(self.temp.name)
        self.command("init_project_agents.py", "--project-root", self.project, "--project-id", "fixture",
                     "--project-name", "Fixture", "--agents", "A01-coordinator,A02-worker", "--user-confirmed",
                     "--governance-root", self.governance)
        self.bus = self.governance / "projects" / "fixture"

    def command(self, script: str, *args: object, ok: bool = True) -> subprocess.CompletedProcess[str]:
        result = subprocess.run([sys.executable, str(SCRIPTS / script), *map(str, args)], text=True, capture_output=True)
        if ok and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def validate(self, needle: str | None = None) -> subprocess.CompletedProcess[str]:
        result = self.command("validate_agents.py", "--project-root", self.project,
                              "--governance-root", self.governance, ok=False)
        if needle:
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn(needle.lower(), result.stdout.lower())
        return result

    def bind(self, agent: str = "A02-worker", session: str = "SESSION-001") -> Path:
        self.command("bind_session.py", "--project-root", self.project, "--agent-id", agent,
                     "--platform", "hermes", "--session-id", session, "--model", "model-one",
                     "--provider", "provider-one", "--governance-root", self.governance)
        current = json.loads((self.bus / "agents" / agent / "runtime/CURRENT_RUNTIME.json").read_text())
        return self.bus / "agents" / agent / "runtime" / current["path"]

    def test_all_new_schemas_agent_profile_runtime_and_activity_are_applied(self) -> None:
        profile = self.bus / "agents/A02-worker/AGENT_PROFILE.json"
        data = json.loads(profile.read_text())
        data["lifecycle"]["status"] = "retired"
        profile.write_text(json.dumps(data) + "\n")
        self.validate("schema violation")

        data["lifecycle"].update(retired_at="2026-08-06T12:00:00Z", retirement_reason="done")
        profile.write_text(json.dumps(data) + "\n")
        runtime = self.bind()
        runtime_data = json.loads(runtime.read_text())
        runtime_data["capture_status"] = {"code": "S000", "name": "partial"}
        runtime.write_text(json.dumps(runtime_data) + "\n")
        self.validate("oneOf")

    def test_runtime_profile_sequence_hash_chain_session_binding_and_orphan_gate(self) -> None:
        first = self.bind(session="SESSION-001")
        second = self.bind(session="SESSION-002")
        first.rename(first.with_name("RP-000003.json"))
        self.validate("runtime profile sequence")

        first = first.with_name("RP-000003.json")
        first.rename(first.with_name("RP-000001.json"))
        value = json.loads(second.read_text())
        value["previous_profile"]["record_hash"] = "0" * 64
        second.write_text(json.dumps(value) + "\n")
        self.validate("previous_profile")

        mapping = self.bus / "agents/A02-worker/conversations/SESSION_MAP.json"
        mapped = json.loads(mapping.read_text())
        mapped["active"]["runtime_profile_sha256"] = "f" * 64
        mapping.write_text(json.dumps(mapped) + "\n")
        self.validate("runtime_profile_sha256")

        self.command("record_agent_runtime.py", "--project-root", self.project, "--agent-id", "A01-coordinator",
                     "--model", "m", "--provider", "p", "--platform", "hermes", "--session-id", "ORPHAN",
                     "--profile", "default", "--workspace", self.project, "--runtime-kind", "hermes-thread",
                     "--governance-root", self.governance)
        self.validate("orphan runtime profile")

    def test_activity_chain_attribution_references_usage_and_secret_scanning(self) -> None:
        runtime = self.bind()
        task = self.bus / "agents/A02-worker/tasks/TASK-001.md"
        task.write_text("---\nschema_version: \"1.0\"\ndoc_type: \"task\"\ntask_id: \"TASK-001\"\ntitle: \"task\"\nowner: \"A02-worker\"\ngoal: \"goal\"\ndependencies: []\nallowed_writes: []\n---\n")
        source = self.bus / "agents/A02-worker/artifacts/source.json"
        source.write_text("{}\n")
        payload = {
            "schema_version": 1, "record_kind": "attempt_started", "recorded_at": "2026-08-06T12:00:00Z",
            "run_id": "RUN-001", "task_id": "TASK-001", "attempt_id": "ATTEMPT-001", "agent_id": "A02-worker",
            "session_id": "SESSION-001", "parent_agent_id": None,
            "runtime_profile": {"runtime": "document", "provider": "p", "model": "m", "profile_name": "default",
                "node_id": None, "host_fingerprint": None, "native_binding_ref": runtime.relative_to(self.bus / "agents/A02-worker").as_posix(), "native_binding_sha256": sha(runtime)},
            "status": {"attempt_status": "running", "task_status_observed": "running", "outcome": None, "reason_code": None, "summary": "started"},
            "tool_summary": None, "verification": None, "artifacts": [], "evidence_refs": [],
            "usage": {"input_tokens": None, "output_tokens": None, "cached_input_tokens": None, "reasoning_tokens": None,
                "total_tokens": None, "cost_minor_units": None, "currency": None, "usage_source": "unavailable",
                "source_ref": None, "source_sha256": None, "reported_at": None},
            "source": {"source_kind": "native_operation", "source_ref": "artifacts/source.json", "source_sha256": sha(source),
                "source_event_id": None, "correlation_id": "RUN-001:TASK-001:ATTEMPT-001", "causation_id": None},
            "supersedes_record_sha256": None,
        }
        input_path = Path(self.temp.name) / "activity.json"
        input_path.write_text(json.dumps(payload))
        pointer = json.loads(self.command("record_agent_activity.py", "--project-root", self.project,
                            "--governance-root", self.governance, "--agent-id", "A02-worker",
                            "--input", input_path).stdout)
        ledger = self.bus / "agents/A02-worker/activity/RUN-001/TASK-001/ATTEMPT-001"
        record_path = ledger / pointer["path"]
        record = json.loads(record_path.read_text())
        record["usage"]["input_tokens"] = 1
        record["record_sha256"] = canonical_hash(record, "record_sha256")
        record_path.write_text(json.dumps(record) + "\n")
        self.validate("usage")

        record["usage"]["input_tokens"] = None
        record["status"]["summary"] = "sk-abcdefghijklmnop"
        record["record_sha256"] = canonical_hash(record, "record_sha256")
        record_path.write_text(json.dumps(record) + "\n")
        self.validate("credential")

    def test_index_bridge_checkpoint_and_agent_profile_hashed_references_are_verified(self) -> None:
        runtime = self.bind()
        role = self.bus / "agents/A02-worker/ROLE.md"
        agent_profile = self.bus / "agents/A02-worker/AGENT_PROFILE.json"
        profile = json.loads(agent_profile.read_text())
        profile["role"]["sha256"] = "0" * 64
        agent_profile.write_text(json.dumps(profile) + "\n")
        self.validate("role hash")

        profile["role"]["sha256"] = sha(role)
        agent_profile.write_text(json.dumps(profile) + "\n")
        bridge = self.bus / "bridges/RUN-001/bridge.json"
        bridge.parent.mkdir(parents=True)
        bridge.write_text(json.dumps({"doc_type": "run_memory_bridge", "run_id": "RUN-001", "tasks": [{
            "task_id": "TASK-MISSING", "agent_id": "A02-worker", "runtime_profile_path": runtime.relative_to(self.bus).as_posix(),
            "runtime_profile_sha256": "0" * 64, "activity_record_path": "missing.json", "activity_record_sha256": "0" * 64}]}) + "\n")
        self.validate("bridge")

        bridge.unlink()
        self.command("rebuild_index.py", "--project-root", self.project,
                     "--governance-root", self.governance)
        index = self.bus / "index.jsonl"
        records = [json.loads(line) for line in index.read_text().splitlines()]
        records[0]["hash"] = "0" * 64
        index.write_text("\n".join(json.dumps(item) for item in records) + "\n")
        self.validate("index hash mismatch")


if __name__ == "__main__":
    unittest.main()
