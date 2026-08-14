from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

from integration_policy import PolicyNotConfigured, load_integration_policy  # noqa: E402
from protocol_lib import ProtocolError  # noqa: E402


def _write_policy(root: Path, body: str) -> Path:
    path = root.parent / "integration-policy.yaml"
    path.write_text(body, encoding="utf-8")
    return path


class IntegrationPolicyTests(unittest.TestCase):
    def test_missing_policy_is_an_explicit_read_only_blocker(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            with self.assertRaises(PolicyNotConfigured):
                load_integration_policy(root / "missing.yaml", root)

    def test_manual_policy_defaults_are_normalized_without_core_branch_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            policy = _write_policy(
                root,
                '\n'.join(
                    (
                        'schema_version: "1.0"',
                        'canonical_branch: "trunk"',
                        'working_branch: "integration"',
                        "",
                    )
                ),
            )
            loaded = load_integration_policy(policy, root)
            self.assertEqual(loaded["canonical_branch"], "trunk")
            self.assertEqual(loaded["working_branch"], "integration")
            self.assertEqual(loaded["candidate_submit_mode"], "manual")
            self.assertEqual(loaded["candidate_submit_command"], [])
            self.assertEqual(loaded["high_conflict_paths"], [])
            self.assertFalse(loaded["release_freeze_supported"])
            self.assertTrue(loaded["policy_sha256"])

    def test_same_branches_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            policy = _write_policy(
                root,
                '\n'.join(
                    (
                        'schema_version: "1.0"',
                        'canonical_branch: "main"',
                        'working_branch: "main"',
                        "",
                    )
                ),
            )
            with self.assertRaises(ProtocolError):
                load_integration_policy(policy, root)

    def test_relative_conflict_paths_are_required(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            policy = _write_policy(
                root,
                '\n'.join(
                    (
                        'schema_version: "1.0"',
                        'canonical_branch: "main"',
                        'working_branch: "update"',
                        'high_conflict_paths: ["src", "docs/README.md"]',
                        "",
                    )
                ),
            )
            loaded = load_integration_policy(policy, root)
            self.assertEqual(loaded["high_conflict_paths"], ["src", "docs/README.md"])

            bad = _write_policy(
                root,
                '\n'.join(
                    (
                        'schema_version: "1.0"',
                        'canonical_branch: "main"',
                        'working_branch: "update"',
                        'high_conflict_paths: ["../secrets"]',
                        "",
                    )
                ),
            )
            with self.assertRaises(ProtocolError):
                load_integration_policy(bad, root)

    def test_commands_are_argv_only_and_cannot_escape_or_invoke_a_shell(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            for command in ("/usr/bin/git", "git;echo", "git", "../bin/tool"):
                policy = _write_policy(
                    root,
                    '\n'.join(
                        (
                            'schema_version: "1.0"',
                            'canonical_branch: "main"',
                            'working_branch: "update"',
                            f'candidate_submit_command: ["{command}"]',
                            "",
                        )
                    ),
                )
                if command == "git":
                    self.assertEqual(load_integration_policy(policy, root)["candidate_submit_command"], ["git"])
                else:
                    with self.assertRaises(ProtocolError):
                        load_integration_policy(policy, root)

    def test_authorized_auto_requires_a_submit_command(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            policy = _write_policy(
                root,
                '\n'.join(
                    (
                        'schema_version: "1.0"',
                        'canonical_branch: "main"',
                        'working_branch: "update"',
                        'candidate_submit_mode: "authorized_auto"',
                        "",
                    )
                ),
            )
            with self.assertRaises(ProtocolError):
                load_integration_policy(policy, root)

    def test_unknown_fields_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            policy = _write_policy(
                root,
                '\n'.join(
                    (
                        'schema_version: "1.0"',
                        'canonical_branch: "main"',
                        'working_branch: "update"',
                        'project_name: "must-not-be-runtime-policy"',
                        "",
                    )
                ),
            )
            with self.assertRaises(ProtocolError):
                load_integration_policy(policy, root)

    def test_cli_is_read_only_and_returns_normalized_json(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            policy = _write_policy(
                root,
                '\n'.join(
                    (
                        'schema_version: "1.0"',
                        'canonical_branch: "main"',
                        'working_branch: "update"',
                        "",
                    )
                ),
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "integration_policy.py"),
                    "--policy",
                    str(policy),
                    "--project-root",
                    str(root),
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            payload = json.loads(result.stdout)
            self.assertEqual(payload["canonical_branch"], "main")
            self.assertEqual(payload["working_branch"], "update")
            self.assertFalse(payload["write_performed"])


if __name__ == "__main__":
    unittest.main()
