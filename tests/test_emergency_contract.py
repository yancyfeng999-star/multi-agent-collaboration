from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from preflight_lib import validate_execution_profile  # noqa: E402


class EmergencyContractTests(unittest.TestCase):
    def test_emergency_profile_is_valid_for_all_governance_levels(self) -> None:
        for governance in ("light", "standard", "strict"):
            validate_execution_profile(governance, "emergency")

    def test_manifest_template_declares_task_scoped_emergency_defaults(self) -> None:
        manifest = (SKILL_DIR / "assets" / "manifest.yaml.template").read_text(encoding="utf-8")
        self.assertIn('execution_profile: "<emergency|fast|normal>"', manifest)
        self.assertIn('preflight_scope: "task"', manifest)
        self.assertIn('executor_policy: "capability_pool"', manifest)
        self.assertIn("executor_scale_authorized", manifest)

    def test_task_template_declares_capability_and_conflict_fields(self) -> None:
        task = (SKILL_DIR / "assets" / "task.md.template").read_text(encoding="utf-8")
        for field in (
            "role_ref",
            "required_capabilities",
            "logical_resources",
            "environment_resources",
            "workspace_policy",
            "release_lane",
        ):
            self.assertIn(f"{field}:", task)

    def test_executor_binding_schema_has_stable_principal_and_ephemeral_executor(self) -> None:
        schema = json.loads(
            (SKILL_DIR / "assets" / "schemas" / "executor-binding.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.0")
        self.assertIn("principal_agent_id", schema["required"])
        self.assertIn("executor_id", schema["required"])
        self.assertIn("task_id", schema["required"])

    def test_preflight_result_schema_accepts_emergency_and_task_scope(self) -> None:
        schema = json.loads(
            (SKILL_DIR / "assets" / "schemas" / "preflight-result.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(schema["properties"]["schema_version"]["const"], "1.1")
        self.assertIn("emergency", schema["properties"]["execution_profile"]["enum"])
        self.assertIn("blocked_tasks", schema["properties"])


if __name__ == "__main__":
    unittest.main()
