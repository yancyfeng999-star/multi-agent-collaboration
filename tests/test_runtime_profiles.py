from __future__ import annotations

import copy
import json
import re
import unittest
from datetime import datetime
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "assets/schemas/runtime-profile.schema.json"
CORE_FIELDS = ("model", "provider", "platform", "session", "profile", "workspace", "runtime_kind")
FORBIDDEN_KEY = re.compile(
    r"(?i)(password|passwd|pwd|secret|token|cookie|authorization|api[_-]?key|"
    r"private[_-]?key|access[_-]?key|refresh[_-]?token|client[_-]?secret|"
    r"credential|bearer|database[_-]?url|payment)"
)


def iso(value: str) -> datetime:
    return datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)


def schema_errors(value: Any, schema: dict[str, Any], root: dict[str, Any], where: str = "$") -> list[str]:
    """Small, fail-closed evaluator for the schema keywords used by this fixture."""
    if "$ref" in schema:
        target: Any = root
        for part in schema["$ref"].removeprefix("#/").split("/"):
            target = target[part.replace("~1", "/").replace("~0", "~")]
        return schema_errors(value, target, root, where)
    errors: list[str] = []
    checks = {
        "object": lambda v: isinstance(v, dict), "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str), "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool), "null": lambda v: v is None,
    }
    allowed = schema.get("type")
    if allowed is not None:
        names = allowed if isinstance(allowed, list) else [allowed]
        if not any(checks[name](value) for name in names):
            return [f"{where}: type"]
    if "const" in schema and value != schema["const"]:
        errors.append(f"{where}: const")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{where}: enum")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            errors.append(f"{where}: minLength")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{where}: maxLength")
        if "pattern" in schema and re.fullmatch(schema["pattern"], value) is None:
            errors.append(f"{where}: pattern")
        if schema.get("format") == "date-time":
            try:
                parsed = iso(value)
                if parsed.utcoffset() is None:
                    raise ValueError
            except (TypeError, ValueError):
                errors.append(f"{where}: date-time")
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            errors.append(f"{where}: minItems")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{where}: maxItems")
        if schema.get("uniqueItems") and len({json.dumps(v, sort_keys=True) for v in value}) != len(value):
            errors.append(f"{where}: uniqueItems")
        if isinstance(schema.get("items"), dict):
            for index, item in enumerate(value):
                errors += schema_errors(item, schema["items"], root, f"{where}[{index}]")
    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{where}: required {key}")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value.keys() - props.keys():
                errors.append(f"{where}: additionalProperties {key}")
        for key, child in value.items():
            if key in props:
                errors += schema_errors(child, props[key], root, f"{where}.{key}")
    for subschema in schema.get("allOf", []):
        errors += schema_errors(value, subschema, root, where)
    if "oneOf" in schema:
        matches = sum(not schema_errors(value, option, root, where) for option in schema["oneOf"])
        if matches != 1:
            errors.append(f"{where}: oneOf")
    return errors


def semantic_errors(profile: dict[str, Any]) -> list[str]:
    errors: list[str] = []

    def walk(value: Any, where: str = "$") -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if FORBIDDEN_KEY.search(key):
                    errors.append(f"{where}.{key}: forbidden secret field")
                walk(child, f"{where}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{where}[{index}]")
    walk(profile)

    try:
        if iso(profile["capture_started_at"]) > iso(profile["captured_at"]):
            errors.append("capture_started_at is after captured_at")
    except (KeyError, TypeError, ValueError):
        pass

    sources = {item.get("source_id"): item for item in profile.get("sources", [])}
    candidates = {item.get("candidate_id"): item for item in profile.get("candidates", [])}
    if len(sources) != len(profile.get("sources", [])):
        errors.append("duplicate source_id")
    if len(candidates) != len(profile.get("candidates", [])):
        errors.append("duplicate candidate_id")
    expected_sources = [f"SRC-{i:03d}" for i in range(1, len(sources) + 1)]
    if list(sources) != expected_sources:
        errors.append("source ids are not continuous")
    expected_candidates = [f"CND-{i:03d}" for i in range(1, len(candidates) + 1)]
    if list(candidates) != expected_candidates:
        errors.append("candidate ids are not continuous")
    for candidate in candidates.values():
        for source_id in candidate.get("source_ids", []):
            if source_id not in sources:
                errors.append("candidate references unknown source")
        kinds = {sources[s]["claim_kind"] for s in candidate.get("source_ids", []) if s in sources}
        if kinds and candidate.get("claim_kind") not in kinds:
            errors.append("candidate claim_kind disagrees with source")

    for field in CORE_FIELDS:
        resolved = profile.get(field, {})
        status = resolved.get("status")
        selected_sources = resolved.get("selected_source_ids", [])
        conflict_ids = resolved.get("conflict_candidate_ids", [])
        for source_id in selected_sources:
            if source_id not in sources:
                errors.append(f"{field} references unknown source")
        if status == "known":
            actual_sources = [sources[s] for s in selected_sources if s in sources]
            if not any(source["claim_kind"] == "observed_actual" for source in actual_sources):
                errors.append(f"{field} known without observed_actual source")
            selected = [c for c in candidates.values() if c.get("field") == field and c.get("selected")]
            if len(selected) != 1 or selected[0].get("normalized_value") != resolved.get("value"):
                errors.append(f"{field} known candidate selection mismatch")
        elif status == "conflict":
            values = {candidates[c]["normalized_value"] for c in conflict_ids if c in candidates}
            if len(values) < 2 or len(values) != len(conflict_ids):
                errors.append(f"{field} conflict must reference distinct candidates")
        if status != "known" and any(c.get("field") == field and c.get("selected") for c in candidates.values()):
            errors.append(f"{field} non-known value has selected candidate")

    statuses = [profile.get(field, {}).get("status") for field in CORE_FIELDS]
    expected = profile.get("capture_status", {})
    if "conflict" in statuses:
        wanted = ("S002", "conflicted")
    elif all(status in {"unknown", "not_collected"} for status in statuses):
        wanted = ("S003", "unresolved")
    elif all(status == "known" for status in statuses):
        wanted = ("S000", "complete")
    else:
        wanted = ("S001", "partial")
    if (expected.get("code"), expected.get("name")) not in {wanted, ("S004", "legacy_imported")}:
        errors.append("capture_status does not match resolved fields")
    return errors


def validate(profile: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    return schema_errors(profile, schema, schema) + semantic_errors(profile)


def resolved_known(value: str, source: str = "SRC-001", confidence: str = "medium") -> dict[str, Any]:
    return {"status": "known", "value": value, "confidence": confidence,
            "selected_source_ids": [source], "conflict_candidate_ids": [],
            "unknown_reason_code": None, "resolution_note": "structured runtime observation"}


def resolved_unknown(reason: str = "U004_INSUFFICIENT_EVIDENCE") -> dict[str, Any]:
    return {"status": "unknown", "value": None, "confidence": "none", "selected_source_ids": [],
            "conflict_candidate_ids": [], "unknown_reason_code": reason,
            "resolution_note": "actual value was not sufficiently evidenced"}


def valid_profile() -> dict[str, Any]:
    values = {
        "model": "hermes-model", "provider": "nous", "platform": "hermes",
        "session": "session-123", "profile": "default", "workspace": "/approved/project",
        "runtime_kind": "hermes-thread",
    }
    sources = [{
        "source_id": "SRC-001", "source_type": "runtime_context", "claim_kind": "observed_actual",
        "locator": "process_context:runtime", "observed_at": "2026-08-06T07:46:00Z",
        "freshness": "live", "trust": "strong", "probe_status": "success",
        "evidence_hash": "a" * 64, "error_code": None,
    }]
    candidates = [{
        "candidate_id": f"CND-{index:03d}", "field": field, "normalized_value": value,
        "source_ids": ["SRC-001"], "claim_kind": "observed_actual", "confidence": "medium", "selected": True,
    } for index, (field, value) in enumerate(values.items(), 1)]
    return {
        "schema_version": "1.0", "doc_type": "runtime_profile", "runtime_profile_id": "RP-000001",
        "agent_id": "A01-coordinator", "capture_status": {"code": "S000", "name": "complete"},
        "capture_started_at": "2026-08-06T07:46:00Z", "captured_at": "2026-08-06T07:46:01Z",
        **{field: resolved_known(value) for field, value in values.items()},
        "sources": sources, "candidates": candidates, "declared_defaults": [],
        "config_fingerprint": {"status": "known", "algorithm": "sha256", "canonicalization": "jcs-rfc8785",
                               "scope_version": "runtime-config-v1", "included_fields": ["platform"],
                               "value": "b" * 64, "unknown_reason_code": None},
        "previous_profile": None,
        "record_hash": {"algorithm": "sha256", "canonicalization": "jcs-rfc8785", "value": "c" * 64},
    }


class RuntimeProfileSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))

    def assertValid(self, profile: dict[str, Any]) -> None:
        self.assertEqual(validate(profile, self.schema), [])

    def assertInvalid(self, profile: dict[str, Any], needle: str) -> None:
        errors = validate(profile, self.schema)
        self.assertTrue(any(needle in error for error in errors), errors)

    def test_accepts_strict_complete_profile(self) -> None:
        self.assertValid(valid_profile())

    def test_rejects_extra_properties_and_secret_fields_at_any_depth(self) -> None:
        profile = valid_profile()
        profile["unexpected"] = True
        self.assertInvalid(profile, "additionalProperties")
        profile = valid_profile()
        profile["sources"][0]["api_token"] = "redacted"
        self.assertInvalid(profile, "forbidden secret field")

    def test_rejects_bad_ids_dates_hashes_and_reversed_capture_window(self) -> None:
        mutations = [
            ("runtime_profile_id", "RP-1", "pattern"), ("agent_id", "agent", "pattern"),
            ("captured_at", "2026-08-06", "date-time"),
        ]
        for key, value, needle in mutations:
            with self.subTest(key=key):
                profile = valid_profile(); profile[key] = value
                self.assertInvalid(profile, needle)
        profile = valid_profile(); profile["record_hash"]["value"] = "ABC"
        self.assertInvalid(profile, "pattern")
        profile = valid_profile(); profile["capture_started_at"] = "2026-08-06T07:47:00Z"
        self.assertInvalid(profile, "after captured_at")

    def test_enforces_known_unknown_not_collected_and_null_semantics(self) -> None:
        profile = valid_profile(); profile["model"] = resolved_unknown(); profile["capture_status"] = {"code": "S001", "name": "partial"}
        profile["candidates"][0]["selected"] = False
        self.assertValid(profile)
        for bad in ("", "unknown"):
            with self.subTest(value=bad):
                candidate = copy.deepcopy(profile); candidate["model"]["value"] = bad
                self.assertInvalid(candidate, "oneOf")
        candidate = copy.deepcopy(profile); candidate["model"]["unknown_reason_code"] = None
        self.assertInvalid(candidate, "oneOf")
        candidate = copy.deepcopy(profile); candidate["model"]["status"] = "not_collected"
        candidate["model"]["unknown_reason_code"] = "U008_LEGACY_NOT_COLLECTED"
        self.assertValid(candidate)

    def test_default_is_valid_only_as_an_evidenced_profile_name(self) -> None:
        self.assertEqual(valid_profile()["profile"]["value"], "default")
        self.assertValid(valid_profile())
        profile = valid_profile(); profile["model"]["value"] = "default"
        self.assertInvalid(profile, "candidate selection mismatch")

    def test_declared_default_cannot_prove_actual_known(self) -> None:
        profile = valid_profile()
        profile["sources"][0]["claim_kind"] = "declared_default"
        for candidate in profile["candidates"]:
            candidate["claim_kind"] = "declared_default"
        self.assertInvalid(profile, "known without observed_actual source")

    def test_validates_source_references_confidence_and_conflicts(self) -> None:
        profile = valid_profile(); profile["model"]["selected_source_ids"] = ["SRC-999"]
        self.assertInvalid(profile, "unknown source")
        profile = valid_profile(); profile["model"]["confidence"] = "none"
        self.assertInvalid(profile, "oneOf")

        profile = valid_profile()
        profile["sources"].append({**profile["sources"][0], "source_id": "SRC-002", "locator": "platform_api:model"})
        profile["candidates"].append({**profile["candidates"][0], "candidate_id": "CND-008",
                                      "normalized_value": "other-model", "source_ids": ["SRC-002"], "selected": False})
        profile["model"] = {"status": "conflict", "value": None, "confidence": "none", "selected_source_ids": [],
                            "conflict_candidate_ids": ["CND-001", "CND-008"], "unknown_reason_code": None,
                            "resolution_note": "two strong actual observations disagree"}
        profile["candidates"][0]["selected"] = False
        profile["capture_status"] = {"code": "S002", "name": "conflicted"}
        self.assertValid(profile)
        profile["candidates"][-1]["normalized_value"] = profile["candidates"][0]["normalized_value"]
        self.assertInvalid(profile, "distinct candidates")

    def test_capture_status_code_name_and_effective_states_must_agree(self) -> None:
        profile = valid_profile(); profile["capture_status"] = {"code": "S001", "name": "complete"}
        self.assertInvalid(profile, "oneOf")
        profile = valid_profile(); profile["capture_status"] = {"code": "S001", "name": "partial"}
        self.assertInvalid(profile, "does not match resolved fields")


if __name__ == "__main__":
    unittest.main()
