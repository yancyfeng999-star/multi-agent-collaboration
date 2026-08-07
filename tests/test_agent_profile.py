import copy
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from validate_agents import schema_errors  # noqa: E402

SCHEMA_PATH = ROOT / "assets" / "schemas" / "agent-profile.schema.json"
TEMPLATE_PATH = ROOT / "assets" / "templates" / "agent" / "AGENT_PROFILE.json"


class AgentProfileSchemaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        cls.template = json.loads(TEMPLATE_PATH.read_text(encoding="utf-8"))

    def assert_invalid(self, mutation) -> None:
        document = copy.deepcopy(self.template)
        mutation(document)
        self.assertTrue(schema_errors(document, self.schema))

    def test_template_is_a_valid_strict_long_lived_identity_profile(self) -> None:
        self.assertEqual(schema_errors(self.template, self.schema), [])
        self.assertEqual(self.template["schema_version"], "1.0")
        self.assertEqual(self.template["doc_type"], "agent_profile")
        self.assertNotIn("runtime", self.template)
        self.assertNotIn("actual_model", json.dumps(self.template))
        self.assertFalse(self.schema["additionalProperties"])

    def test_agent_id_and_role_reference_hash_are_strict(self) -> None:
        self.assert_invalid(lambda d: d.__setitem__("agent_id", "coordinator"))
        self.assert_invalid(lambda d: d["role"].__setitem__("path", "/tmp/ROLE.md"))
        self.assert_invalid(lambda d: d["role"].__setitem__("sha256", "not-a-hash"))

    def test_declared_model_policy_cannot_claim_runtime_facts(self) -> None:
        self.assert_invalid(lambda d: d["declared_model_policy"].__setitem__("actual_model", "gpt-x"))
        self.assert_invalid(lambda d: d.__setitem__("provider", "example-provider"))
        self.assert_invalid(lambda d: d.__setitem__("session_id", "runtime-session"))

    def test_lifecycle_contract_is_strict_and_state_aware(self) -> None:
        lifecycle = self.schema["properties"]["lifecycle"]
        self.assertEqual(lifecycle["properties"]["status"]["enum"], ["active", "paused", "retired"])
        self.assertIn("allOf", lifecycle)
        self.assert_invalid(lambda d: d["lifecycle"].__setitem__("created_at", "2026-08-06"))

    def test_unknown_fields_and_secret_bearing_fields_are_rejected(self) -> None:
        self.assert_invalid(lambda d: d.__setitem__("notes", "unversioned extension"))
        self.assert_invalid(lambda d: d["declared_model_policy"].__setitem__("api_key", "secret"))
        self.assert_invalid(lambda d: d["metadata"].__setitem__("token", "secret"))


if __name__ == "__main__":
    unittest.main()
