from __future__ import annotations

import copy
import hashlib
import json
import re
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "assets" / "schemas" / "agent-activity.schema.json"
HASH_RE = re.compile(r"^[a-f0-9]{64}$")
SECRET_KEY_RE = re.compile(
    r"(?i)(api[_-]?key|access[_-]?key|secret|token|password|passwd|pwd|credential|"
    r"authorization|cookie|private[_-]?key|client[_-]?secret|refresh[_-]?token|"
    r"session[_-]?token|bearer|signature|webhook)"
)
SECRET_VALUE_RE = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+\S+|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|"
    r"(?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?)://[^\s/:]+:[^\s/@]+@|"
    r"https?://[^\s?#]+[?&](?:api[_-]?key|token|password|signature)=[^&\s]+|"
    r"eyJ[A-Za-z0-9_-]{8,}\.eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}|"
    r"AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})"
)


def canonical_record_hash(record: dict[str, Any]) -> str:
    value = copy.deepcopy(record)
    value["record_sha256"] = None
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def schema_errors(value: Any, schema: dict[str, Any], root: dict[str, Any], where: str = "$") -> list[str]:
    """Small stdlib validator for the Draft-07 subset used by this schema."""
    if "$ref" in schema:
        target: Any = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return schema_errors(value, target, root, where)
    errors: list[str] = []
    for branch in schema.get("allOf", []):
        errors.extend(schema_errors(value, branch, root, where))
    if "if" in schema and not schema_errors(value, schema["if"], root, where):
        errors.extend(schema_errors(value, schema.get("then", {}), root, where))
    if "not" in schema and not schema_errors(value, schema["not"], root, where):
        errors.append(f"{where}: forbidden by not")
    allowed = schema.get("type")
    if allowed is not None:
        allowed = allowed if isinstance(allowed, list) else [allowed]
        checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "null": lambda item: item is None,
        }
        if not any(checks[kind](value) for kind in allowed):
            return [f"{where}: expected {allowed}"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: expected const {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: invalid enum")
    if isinstance(value, str):
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{where}: pattern mismatch")
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{where}: too short")
    if isinstance(value, int) and not isinstance(value, bool) and value < schema.get("minimum", value):
        errors.append(f"{where}: below minimum")
    if isinstance(value, list):
        for index, item in enumerate(value):
            errors.extend(schema_errors(item, schema.get("items", {}), root, f"{where}[{index}]"))
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{where}: missing {key}")
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value.keys() - properties.keys():
                errors.append(f"{where}: unknown {key}")
        for key, item in value.items():
            if key in properties:
                errors.extend(schema_errors(item, properties[key], root, f"{where}.{key}"))
    return errors


def find_secret_field(value: Any, path: str = "$") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            if SECRET_KEY_RE.search(key) and key not in {"input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens", "total_tokens"}:
                return f"{path}.{key}"
            finding = find_secret_field(item, f"{path}.{key}")
            if finding:
                return finding
    elif isinstance(value, list):
        for index, item in enumerate(value):
            finding = find_secret_field(item, f"{path}[{index}]")
            if finding:
                return finding
    elif isinstance(value, str) and SECRET_VALUE_RE.search(value):
        return path
    return None


def valid_record(record: dict[str, Any], schema: dict[str, Any]) -> bool:
    return not schema_errors(record, schema, schema) and find_secret_field(record) is None and record["record_sha256"] == canonical_record_hash(record)


def valid_chain(records: list[dict[str, Any]], schema: dict[str, Any]) -> bool:
    previous = None
    identity = None
    seen_activity: set[str] = set()
    for sequence, record in enumerate(records, start=1):
        current_identity = tuple(record[key] for key in ("run_id", "task_id", "attempt_id", "agent_id"))
        if identity is None:
            identity = current_identity
        if current_identity != identity or record["sequence"] != sequence:
            return False
        if record["previous_record_sha256"] != previous or record["activity_id"] in seen_activity:
            return False
        if not valid_record(record, schema):
            return False
        seen_activity.add(record["activity_id"])
        previous = record["record_sha256"]
    return True


def fixture() -> dict[str, Any]:
    record: dict[str, Any] = {
        "schema_version": 1,
        "record_kind": "attempt_started",
        "activity_id": "ACTIVITY-001",
        "sequence": 1,
        "recorded_at": "2026-08-06T16:00:00.000Z",
        "run_id": "RUN-123",
        "task_id": "TASK-001",
        "attempt_id": "ATTEMPT-001",
        "agent_id": "worker",
        "session_id": "SESSION-001",
        "parent_agent_id": None,
        "runtime_profile": {
            "runtime": "codex_subagent", "provider": "openai", "model": "gpt-5",
            "profile_name": "default", "node_id": "local", "host_fingerprint": None,
            "native_binding_ref": "native/threads/THREAD-001.yaml",
            "native_binding_sha256": "1" * 64,
        },
        "status": {"attempt_status": "running", "task_status_observed": "running", "outcome": None, "reason_code": None, "summary": "started"},
        "tool_summary": None,
        "verification": None,
        "artifacts": [],
        "evidence_refs": [],
        "usage": {
            "input_tokens": None, "output_tokens": None, "cached_input_tokens": None,
            "reasoning_tokens": None, "total_tokens": None, "cost_minor_units": None,
            "currency": None, "usage_source": "unavailable", "source_ref": None,
            "source_sha256": None, "reported_at": None,
        },
        "source": {
            "source_kind": "ack", "source_ref": "acks/TASK-001/ACK-001.yaml",
            "source_sha256": "2" * 64, "source_event_id": "EVENT-001",
            "correlation_id": "RUN-123:TASK-001", "causation_id": None,
        },
        "idempotency_key": "RUN-123:TASK-001:ATTEMPT-001:worker:ACTIVITY-001:v1",
        "supersedes_record_sha256": None,
        "previous_record_sha256": None,
        "record_sha256": None,
    }
    record["record_sha256"] = canonical_record_hash(record)
    return record


class AgentActivitySchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def assertValid(self, record: dict[str, Any]) -> None:
        self.assertEqual(schema_errors(record, self.schema, self.schema), [])

    def test_accepts_complete_activity_record(self) -> None:
        record = fixture()
        self.assertValid(record)
        self.assertTrue(valid_record(record, self.schema))

    def test_usage_requires_source_and_rejects_negative_or_float_values(self) -> None:
        for field in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_tokens", "total_tokens", "cost_minor_units"):
            for invalid in (-1, 1.5):
                with self.subTest(field=field, invalid=invalid):
                    record = fixture()
                    record["usage"][field] = invalid
                    self.assertTrue(schema_errors(record, self.schema, self.schema))
        record = fixture()
        del record["usage"]["usage_source"]
        self.assertTrue(schema_errors(record, self.schema, self.schema))

    def test_usage_source_controls_receipt_and_unknown_values(self) -> None:
        for source in ("provider_response", "runtime_meter", "billing_export"):
            record = fixture()
            record["usage"]["usage_source"] = source
            self.assertTrue(schema_errors(record, self.schema, self.schema), source)
            record["usage"].update({"source_ref": "receipts/usage.json", "source_sha256": "3" * 64, "reported_at": "2026-08-06T16:01:00Z"})
            self.assertValid(record)
        for source in ("unavailable", "none_required"):
            record = fixture()
            record["usage"]["usage_source"] = source
            record["usage"]["input_tokens"] = 0
            self.assertTrue(schema_errors(record, self.schema, self.schema), source)
        record = fixture()
        record["usage"]["usage_source"] = "estimated"
        self.assertTrue(schema_errors(record, self.schema, self.schema))

    def test_requires_run_task_attempt_agent_session_and_runtime_binding(self) -> None:
        for field in ("run_id", "task_id", "attempt_id", "agent_id", "session_id", "runtime_profile"):
            with self.subTest(field=field):
                record = fixture()
                del record[field]
                self.assertTrue(schema_errors(record, self.schema, self.schema))
        record = fixture()
        del record["runtime_profile"]["native_binding_sha256"]
        self.assertTrue(schema_errors(record, self.schema, self.schema))

    def test_hashes_are_lowercase_sha256_and_chain_is_strict(self) -> None:
        first = fixture()
        second = fixture()
        second.update({"activity_id": "ACTIVITY-002", "sequence": 2, "previous_record_sha256": first["record_sha256"], "idempotency_key": "RUN-123:TASK-001:ATTEMPT-001:worker:ACTIVITY-002:v1"})
        second["record_sha256"] = canonical_record_hash(second)
        self.assertTrue(valid_chain([first, second], self.schema))

        tampered = copy.deepcopy(second)
        tampered["status"]["summary"] = "tampered"
        self.assertFalse(valid_chain([first, tampered], self.schema))
        wrong_link = copy.deepcopy(second)
        wrong_link["previous_record_sha256"] = "f" * 64
        wrong_link["record_sha256"] = canonical_record_hash(wrong_link)
        self.assertFalse(valid_chain([first, wrong_link], self.schema))
        uppercase = fixture()
        uppercase["source"]["source_sha256"] = "A" * 64
        self.assertTrue(schema_errors(uppercase, self.schema, self.schema))

    def test_rejects_secret_fields_recursively_unknown_properties_and_secret_values(self) -> None:
        for path, key in (("root", "api_key"), ("runtime", "authorization"), ("usage", "refresh_token")):
            with self.subTest(path=path, key=key):
                record = fixture()
                target = record if path == "root" else record["runtime_profile" if path == "runtime" else "usage"]
                target[key] = "must-not-persist"
                self.assertTrue(schema_errors(record, self.schema, self.schema))
                self.assertIsNotNone(find_secret_field(record))
        for secret in (
            "Authorization: Bearer bearer-secret-value-123456",
            "postgresql://app:database-password@example.test/app",
            "-----BEGIN PRIVATE KEY-----",
        ):
            with self.subTest(secret_class=secret.split()[0]):
                record = fixture()
                record["status"]["summary"] = secret
                self.assertIsNotNone(find_secret_field(record))
                record["record_sha256"] = canonical_record_hash(record)
                self.assertFalse(valid_record(record, self.schema))


if __name__ == "__main__":
    unittest.main()
