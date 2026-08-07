from __future__ import annotations

import copy
import json
import re
import unittest
from pathlib import Path
from typing import Any


SKILL = Path(__file__).resolve().parents[1]
SCHEMA_PATH = SKILL / "assets/schemas/session-map.schema.json"
TEMPLATE_PATH = SKILL / "assets/templates/conversation/SESSION_MAP.json"
HASH = "a" * 64


def schema_errors(value: Any, schema: dict[str, Any], root: dict[str, Any], where: str = "$") -> list[str]:
    """Focused Draft-07 evaluator covering the invariants used by session-map."""
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
    if isinstance(value, dict):
        if len(value) < schema.get("minProperties", 0):
            errors.append(f"{where}: fewer than minProperties")
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


def binding(*, history: bool = False) -> dict[str, Any]:
    value: dict[str, Any] = {
        "platform": "hermes",
        "session_id": "session-1",
        "profile": "default",
        "workspace": "/tmp/project",
        "started_at": "2026-08-06T12:00:00Z",
        "last_synced_message_id": 0,
        "last_synced_at": "2026-08-06T12:00:00Z",
        "runtime_profile_id": "RP-000001",
        "runtime_profile_sha256": HASH,
    }
    if history:
        value.update({"ended_at": "2026-08-06T13:00:00Z", "superseded_by": "session-2"})
    return value


class SessionRuntimeSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def assertValid(self, value: dict[str, Any]) -> None:
        self.assertEqual(schema_errors(value, self.schema, self.schema), [])

    def assertInvalid(self, value: dict[str, Any]) -> None:
        self.assertTrue(schema_errors(value, self.schema, self.schema))

    def test_current_schema_requires_complete_runtime_binding_in_active_and_history(self) -> None:
        document = {"schema_version": "1.1", "agent_id": "A01-coordinator", "active": binding(), "history": [binding(history=True)]}
        self.assertValid(document)
        for location in ("active", "history"):
            for field in ("runtime_profile_id", "runtime_profile_sha256"):
                with self.subTest(location=location, field=field):
                    invalid = copy.deepcopy(document)
                    target = invalid["active"] if location == "active" else invalid["history"][0]
                    del target[field]
                    self.assertInvalid(invalid)

    def test_runtime_binding_rejects_bad_id_hash_and_unknown_properties(self) -> None:
        document = {"schema_version": "1.1", "agent_id": "A01-coordinator", "active": binding(), "history": []}
        for field, bad in (("runtime_profile_id", "RP-1"), ("runtime_profile_sha256", "A" * 64)):
            with self.subTest(field=field):
                invalid = copy.deepcopy(document)
                invalid["active"][field] = bad
                self.assertInvalid(invalid)
        invalid = copy.deepcopy(document)
        invalid["active"]["runtime_profile_path"] = "profiles/RP-000001.json"
        self.assertInvalid(invalid)

    def test_legacy_schema_accepts_unbound_records_but_never_partial_bindings(self) -> None:
        legacy_active = binding()
        legacy_history = binding(history=True)
        for target in (legacy_active, legacy_history):
            del target["runtime_profile_id"]
            del target["runtime_profile_sha256"]
        legacy = {"schema_version": "1.0", "agent_id": "A01-coordinator", "active": legacy_active, "history": [legacy_history]}
        self.assertValid(legacy)
        partial = copy.deepcopy(legacy)
        partial["active"]["runtime_profile_id"] = "RP-000001"
        self.assertInvalid(partial)

    def test_template_is_current_and_binds_runtime_profile_without_empty_placeholders(self) -> None:
        rendered = TEMPLATE_PATH.read_text(encoding="utf-8")
        replacements = {
            "{agent_id}": "A01-coordinator", "{platform}": "hermes", "{session_id}": "session-1",
            "{profile}": "default", "{workspace}": "/tmp/project",
            "{started_at}": "2026-08-06T12:00:00Z", "{last_synced_message_id}": "0",
            "{last_synced_at}": "2026-08-06T12:00:00Z", "{runtime_profile_id}": "RP-000001",
            "{runtime_profile_sha256}": HASH,
        }
        for placeholder, value in replacements.items():
            rendered = rendered.replace(placeholder, value)
        document = json.loads(rendered)
        self.assertEqual(document["schema_version"], "1.1")
        self.assertEqual(document["active"]["runtime_profile_id"], "RP-000001")
        self.assertEqual(document["active"]["runtime_profile_sha256"], HASH)
        self.assertValid(document)

    def test_focused_evaluator_really_enforces_oneof_and_minproperties(self) -> None:
        root = {"oneOf": [{"type": "object", "minProperties": 1}, {"type": "null"}]}
        self.assertTrue(schema_errors({}, root, root))
        self.assertEqual(schema_errors(None, root, root), [])


if __name__ == "__main__":
    unittest.main()
