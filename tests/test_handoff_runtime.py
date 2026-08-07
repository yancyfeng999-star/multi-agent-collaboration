from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "assets/schemas/handoff.schema.json"
TEMPLATE_PATH = ROOT / "assets/templates/conversation/HANDOFF.md"
HASH = "a" * 64


def schema_errors(value: Any, schema: dict[str, Any], root: dict[str, Any], where: str = "$") -> list[str]:
    """Focused Draft-07 evaluator for the Handoff runtime contract."""
    if "$ref" in schema:
        target: Any = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return schema_errors(value, target, root, where)

    errors: list[str] = []
    for branch in schema.get("allOf", []):
        errors.extend(schema_errors(value, branch, root, where))
    if "oneOf" in schema:
        matches = sum(not schema_errors(value, branch, root, where) for branch in schema["oneOf"])
        if matches != 1:
            errors.append(f"{where}: oneOf matched {matches} branches")
    if "anyOf" in schema and not any(not schema_errors(value, branch, root, where) for branch in schema["anyOf"]):
        errors.append(f"{where}: no anyOf branch matched")
    if "not" in schema and not schema_errors(value, schema["not"], root, where):
        errors.append(f"{where}: forbidden by not")
    if "if" in schema and not schema_errors(value, schema["if"], root, where):
        errors.extend(schema_errors(value, schema.get("then", {}), root, where))
    elif "else" in schema:
        errors.extend(schema_errors(value, schema["else"], root, where))

    allowed = schema.get("type")
    if allowed is not None:
        names = allowed if isinstance(allowed, list) else [allowed]
        checks = {
            "object": lambda item: isinstance(item, dict),
            "array": lambda item: isinstance(item, list),
            "string": lambda item: isinstance(item, str),
            "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
            "null": lambda item: item is None,
        }
        if not any(checks[name](value) for name in names):
            return errors + [f"{where}: wrong type"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: wrong const")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: outside enum")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{where}: shorter than minLength")
        if "pattern" in schema and re.search(schema["pattern"], value) is None:
            errors.append(f"{where}: pattern mismatch")
    if isinstance(value, int) and not isinstance(value, bool) and value < schema.get("minimum", value):
        errors.append(f"{where}: below minimum")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{where}: missing {key}")
        if schema.get("additionalProperties") is False:
            for key in value.keys() - properties.keys():
                errors.append(f"{where}: unknown {key}")
        for key, item in value.items():
            if key in properties:
                errors.extend(schema_errors(item, properties[key], root, f"{where}.{key}"))
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, item in enumerate(value):
            errors.extend(schema_errors(item, schema["items"], root, f"{where}[{index}]"))
    return errors


def current_handoff() -> dict[str, Any]:
    return {
        "schema_version": "1.1",
        "doc_type": "handoff",
        "task_id": "TASK-017",
        "agent_id": "A17-worker",
        "status": "completed",
        "summary": "Extended the Handoff runtime contract.",
        "created_at": "2026-08-06T16:00:00Z",
        "runtime_profile_id": "RP-000001",
        "runtime_profile_sha256": HASH,
        "activity_record_path": "agents/A17-worker/activity/RUN-001/TASK-017/ATTEMPT-002/2026/08/06/ACTIVITY-000001.json",
        "activity_record_sha256": "b" * 64,
        "actual_model_status": "known",
        "actual_model": "gpt-5.6-sol",
        "actual_provider_status": "known",
        "actual_provider": "custom:rootflowgpt",
        "usage_summary": {
            "usage_source": "provider_response",
            "input_tokens": 120,
            "output_tokens": 30,
            "cached_input_tokens": 40,
            "reasoning_tokens": 10,
            "total_tokens": 200,
            "cost_minor_units": 25,
            "currency": "USD",
            "source_ref": "agents/A17-worker/activity/RUN-001/TASK-017/ATTEMPT-002/2026/08/06/ACTIVITY-000001.json",
            "source_sha256": "b" * 64,
        },
    }


class HandoffRuntimeContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def assertValid(self, document: dict[str, Any]) -> None:
        self.assertEqual(schema_errors(document, self.schema, self.schema), [])

    def assertInvalid(self, document: dict[str, Any]) -> None:
        self.assertTrue(schema_errors(document, self.schema, self.schema))

    def test_current_version_requires_complete_runtime_and_activity_bindings(self) -> None:
        self.assertValid(current_handoff())
        for pair in (
            ("runtime_profile_id", "runtime_profile_sha256"),
            ("activity_record_path", "activity_record_sha256"),
        ):
            for field in pair:
                with self.subTest(field=field):
                    invalid = current_handoff()
                    del invalid[field]
                    self.assertInvalid(invalid)

    def test_runtime_ids_hashes_and_activity_paths_are_strict(self) -> None:
        mutations = {
            "runtime_profile_id": "RP-1",
            "runtime_profile_sha256": "A" * 64,
            "activity_record_sha256": "short",
            "activity_record_path": "../outside/ACTIVITY-000001.json",
        }
        for field, value in mutations.items():
            with self.subTest(field=field):
                invalid = current_handoff()
                invalid[field] = value
                self.assertInvalid(invalid)
        for path in (
            "/absolute/activity/ACTIVITY-000001.json",
            "agents/A17-worker/runtime/profiles/RP-000001.json",
            "https://example.test/ACTIVITY-000001.json",
        ):
            with self.subTest(path=path):
                invalid = current_handoff()
                invalid["activity_record_path"] = path
                self.assertInvalid(invalid)

    def test_actual_model_and_provider_statuses_control_values(self) -> None:
        for prefix in ("model", "provider"):
            for status in ("unknown", "not_collected", "conflict"):
                with self.subTest(prefix=prefix, status=status):
                    valid = current_handoff()
                    valid[f"actual_{prefix}_status"] = status
                    valid[f"actual_{prefix}"] = None
                    self.assertValid(valid)
                    invalid = copy.deepcopy(valid)
                    invalid[f"actual_{prefix}"] = "declared-default"
                    self.assertInvalid(invalid)
            invalid = current_handoff()
            invalid[f"actual_{prefix}_status"] = "known"
            invalid[f"actual_{prefix}"] = None
            self.assertInvalid(invalid)

    def test_usage_summary_requires_evidence_for_values_and_nulls_when_unavailable(self) -> None:
        for field in ("source_ref", "source_sha256"):
            invalid = current_handoff()
            del invalid["usage_summary"][field]
            self.assertInvalid(invalid)
        unavailable = current_handoff()
        unavailable["usage_summary"] = {
            "usage_source": "unavailable",
            "input_tokens": None,
            "output_tokens": None,
            "cached_input_tokens": None,
            "reasoning_tokens": None,
            "total_tokens": None,
            "cost_minor_units": None,
            "currency": None,
            "source_ref": None,
            "source_sha256": None,
        }
        self.assertValid(unavailable)
        unavailable["usage_summary"]["total_tokens"] = 0
        self.assertInvalid(unavailable)

    def test_legacy_handoff_is_explicit_and_never_accepts_partial_runtime_fields(self) -> None:
        legacy = {
            "schema_version": "1.0", "doc_type": "handoff", "task_id": "TASK-017",
            "agent_id": "A17-worker", "status": "completed", "summary": "legacy handoff",
            "created_at": "2026-08-06T16:00:00Z",
        }
        self.assertValid(legacy)
        for field, value in (
            ("runtime_profile_id", "RP-000001"),
            ("activity_record_path", current_handoff()["activity_record_path"]),
            ("actual_model_status", "unknown"),
            ("usage_summary", current_handoff()["usage_summary"]),
        ):
            with self.subTest(field=field):
                invalid = copy.deepcopy(legacy)
                invalid[field] = value
                self.assertInvalid(invalid)

    def test_template_declares_current_runtime_contract_without_fake_defaults(self) -> None:
        template = TEMPLATE_PATH.read_text(encoding="utf-8")
        self.assertIn('schema_version: "1.1"', template)
        for placeholder in (
            "{runtime_profile_id}", "{runtime_profile_sha256}",
            "{activity_record_path}", "{activity_record_sha256}",
            "{actual_model_status}", "{actual_model}",
            "{actual_provider_status}", "{actual_provider}", "{usage_summary}",
        ):
            self.assertIn(placeholder, template)
        self.assertNotRegex(template, r"actual_(?:model|provider):\s*(?:default|unknown)\b")
        self.assertNotRegex(template, r"(?:input|output|total)_tokens:\s*0\b")


if __name__ == "__main__":
    unittest.main()
