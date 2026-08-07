from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from runtime_metadata import DetectionRejected, detect_runtime_metadata  # noqa: E402


class GetOnlyEnvironment:
    """Synthetic environment that fails if the detector attempts enumeration."""

    def __init__(self, values: dict[str, str]) -> None:
        self.values = values
        self.read_keys: list[str] = []

    def get(self, key: str, default=None):
        self.read_keys.append(key)
        return self.values.get(key, default)

    def __iter__(self):
        raise AssertionError("environment must not be iterated")

    def keys(self):
        raise AssertionError("environment keys must not be enumerated")

    def items(self):
        raise AssertionError("environment items must not be enumerated")


class RuntimeMetadataTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def detect(self, **kwargs):
        return detect_runtime_metadata(
            project_root=self.root,
            environ=kwargs.pop("environ", GetOnlyEnvironment({})),
            **kwargs,
        )

    def test_explicit_metadata_wins_over_matching_lower_priority_evidence(self) -> None:
        result = self.detect(
            explicit={"platform": "hermes", "session_id": "s-explicit", "profile": "work", "workspace": str(self.workspace)},
            trusted_context={"platform": "hermes", "session_id": "s-explicit", "profile": "work", "workspace": str(self.workspace)},
            environ=GetOnlyEnvironment({"HERMES_SESSION_ID": "s-explicit", "HERMES_PROFILE": "work"}),
        )
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["source"], {"platform": "explicit", "session_id": "explicit", "profile": "explicit", "workspace": "explicit"})
        self.assertEqual(result["confidence"], 1.0)
        self.assertEqual(result["conflicts"], [])

    def test_trusted_context_is_used_before_allowlisted_environment(self) -> None:
        result = self.detect(
            trusted_context={"platform": "codex", "session_id": "codex-1", "profile": "team", "workspace": str(self.workspace)},
            environ=GetOnlyEnvironment({"CODEX_SESSION_ID": "codex-1", "CODEX_PROFILE": "team", "CODEX_WORKSPACE": str(self.workspace)}),
        )
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["platform"], "codex")
        self.assertEqual(result["source"]["session_id"], "trusted_context")
        self.assertEqual(result["confidence"], .95)

    def test_verified_session_map_precedes_environment(self) -> None:
        result = self.detect(
            agent_id="A01-coordinator",
            session_map={"agent_id": "A01-coordinator", "active": {"platform": "hermes", "session_id": "mapped", "profile": "mapped-profile", "workspace": str(self.workspace)}},
            environ=GetOnlyEnvironment({"HERMES_SESSION_ID": "mapped", "HERMES_PROFILE": "mapped-profile", "HERMES_WORKSPACE": str(self.workspace)}),
        )
        self.assertEqual(result["status"], "resolved")
        self.assertEqual(result["source"]["session_id"], "session_map")
        self.assertEqual(result["confidence"], .9)

    def test_detector_only_reads_static_allowlist_and_ignores_other_keys(self) -> None:
        environment = GetOnlyEnvironment({
            "HERMES_SESSION_ID": "safe-session", "HERMES_PROFILE": "safe", "HERMES_WORKSPACE": str(self.workspace),
            "OPENAI_API_KEY": "sk-this-value-must-never-be-read", "UNRELATED_SAFE_KEY": "ignored",
        })
        result = self.detect(environ=environment)
        self.assertEqual(result["platform"], "hermes")
        self.assertEqual(result["status"], "insufficient")
        self.assertNotIn("OPENAI_API_KEY", environment.read_keys)
        self.assertNotIn("UNRELATED_SAFE_KEY", environment.read_keys)
        self.assertFalse(result["security"]["environment_snapshot_taken"])

    def test_multiple_platform_signals_are_ambiguous_and_candidates_are_retained_safely(self) -> None:
        result = self.detect(environ=GetOnlyEnvironment({"HERMES_SESSION_ID": "hermes-id", "CODEX_SESSION_ID": "codex-id"}))
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["platform"])
        conflict = next(item for item in result["conflicts"] if item["code"] == "MULTI_PLATFORM_STRONG_SIGNAL")
        self.assertEqual([item["platform"] for item in conflict["candidates"]], ["hermes", "codex"])
        self.assertNotIn("hermes-id", repr(result))
        self.assertNotIn("codex-id", repr(result))

    def test_explicit_value_conflicting_with_active_map_is_not_silently_overwritten(self) -> None:
        result = self.detect(
            agent_id="A01-coordinator",
            explicit={"platform": "hermes", "session_id": "new-session", "profile": "work", "workspace": str(self.workspace)},
            session_map={"agent_id": "A01-coordinator", "active": {"platform": "hermes", "session_id": "active-session", "profile": "work", "workspace": str(self.workspace)}},
        )
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["session_id"])
        self.assertTrue(any(item["code"] == "EXPLICIT_CONTRADICTS_ACTIVE_BINDING" for item in result["conflicts"]))
        self.assertNotIn("new-session", repr(result["conflicts"]))
        self.assertNotIn("active-session", repr(result["conflicts"]))

    def test_different_strong_actual_candidates_are_preserved_as_a_conflict(self) -> None:
        result = self.detect(
            explicit={"platform": "hermes", "session_id": "explicit-session", "profile": "work", "workspace": str(self.workspace)},
            trusted_context={"platform": "hermes", "session_id": "trusted-session", "profile": "work", "workspace": str(self.workspace)},
        )
        self.assertEqual(result["status"], "ambiguous")
        self.assertIsNone(result["session_id"])
        conflict = next(item for item in result["conflicts"] if item["code"] == "SESSION_ID_MISMATCH")
        self.assertEqual([item["source"] for item in conflict["candidates"]], ["explicit", "trusted_context"])
        self.assertNotIn("explicit-session", repr(conflict))
        self.assertNotIn("trusted-session", repr(conflict))

    def test_declared_defaults_are_low_confidence_candidates_not_actual_proof(self) -> None:
        result = self.detect(declared_defaults={"platform": "claude_code", "profile": "default"})
        self.assertEqual(result["platform"], "claude-code")
        self.assertEqual(result["source"]["platform"], "default")
        self.assertEqual(result["confidence"], 0.0)
        self.assertEqual(result["status"], "insufficient")
        self.assertIsNone(result["session_id"])

    def test_workspace_is_resolved_and_must_stay_within_allowed_roots(self) -> None:
        nested = self.workspace / "nested"
        nested.mkdir()
        result = self.detect(explicit={"platform": "other", "session_id": "manual-1", "profile": "safe", "workspace": str(nested / "..")})
        self.assertEqual(result["workspace"], str(self.workspace))
        outside = Path(self.temp.name).parent.resolve()
        with self.assertRaises(DetectionRejected) as caught:
            self.detect(explicit={"platform": "other", "session_id": "manual-1", "profile": "safe", "workspace": str(outside)})
        self.assertEqual(caught.exception.code, "WORKSPACE_OUTSIDE_ALLOWED_ROOTS")

    def test_secret_shaped_allowlisted_value_is_rejected_without_echo(self) -> None:
        secret = "eyJabcdefghijk.abcdefghijk.abcdefghijk"
        with self.assertRaises(DetectionRejected) as caught:
            self.detect(environ=GetOnlyEnvironment({"HERMES_SESSION_ID": secret}))
        self.assertEqual(caught.exception.code, "SENSITIVE_INPUT_REJECTED")
        self.assertNotIn(secret, str(caught.exception))

    def test_schema_unknown_trusted_field_and_agent_mismatch_fail_closed(self) -> None:
        with self.assertRaises(DetectionRejected) as caught:
            self.detect(trusted_context={"platform": "hermes", "session_id": "s", "profile": "p", "workspace": str(self.workspace), "extra": "not-allowed"})
        self.assertEqual(caught.exception.code, "SOURCE_SCHEMA_INVALID")
        result = self.detect(
            agent_id="A01-coordinator",
            session_map={"agent_id": "A02-worker", "active": {"platform": "hermes", "session_id": "s", "profile": "p", "workspace": str(self.workspace)}},
        )
        self.assertEqual(result["status"], "ambiguous")
        self.assertTrue(any(item["code"] == "AGENT_ID_MISMATCH" for item in result["conflicts"]))


if __name__ == "__main__":
    unittest.main()
