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

from evidence_layers import (  # noqa: E402
    build_evidence_layers,
    canonical_movement_gate,
    load_release_freeze,
)
from integration_lane import integrate_candidate  # noqa: E402
from protocol_lib import ProtocolError  # noqa: E402


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


class ReleaseFreezeTests(unittest.TestCase):
    def test_active_freeze_is_readable_and_blocks_canonical_movement(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "project"
            root.mkdir()
            git(root, "init", "-q")
            git(root, "config", "user.name", "Freeze Test")
            git(root, "config", "user.email", "freeze@example.test")
            (root / "README.md").write_text("base\n", encoding="utf-8")
            git(root, "add", "README.md")
            git(root, "commit", "-q", "-m", "base")
            commit = git(root, "rev-parse", "HEAD")
            freeze = Path(temp) / "freeze.yaml"
            freeze.write_text(
                "\n".join(
                    (
                        'schema_version: "1.0"',
                        'freeze_id: "FREEZE-001"',
                        'active: true',
                        'canonical_branch: "main"',
                        f'canonical_commit: "{commit}"',
                        'scope_paths: ["src"]',
                        'reason: "hold canonical movement"',
                        'created_at: "2026-08-14T10:00:00+08:00"',
                        'expires_at: "2099-08-14T10:00:00+08:00"',
                        "",
                    )
                ),
                encoding="utf-8",
            )
            loaded = load_release_freeze(freeze, root)
            self.assertTrue(loaded["active"])
            gate = canonical_movement_gate(loaded, "main", commit, requested_scope_paths=["unrelated"])
            self.assertFalse(gate["allowed"])
            self.assertIn("release_freeze_active", gate["blockers"])

    def test_canonical_commit_mismatch_cannot_be_bypassed_by_scope_change(self) -> None:
        freeze = {
            "active": True,
            "canonical_branch": "main",
            "canonical_commit": "a" * 40,
            "scope_paths": ["src"],
            "expires_at": "2099-08-14T10:00:00+08:00",
        }
        gate = canonical_movement_gate(freeze, "main", "b" * 40, requested_scope_paths=["docs"])
        self.assertFalse(gate["allowed"])
        self.assertIn("canonical_commit_mismatch", gate["blockers"])
        self.assertIn("release_freeze_active", gate["blockers"])


class EvidenceLayerTests(unittest.TestCase):
    def test_missing_layers_are_not_verified_and_deployment_acceptance_are_independent(self) -> None:
        value = build_evidence_layers(
            "candidate-a",
            "a" * 40,
            deployments=[{"environment": "staging", "status": "blocked_unknown", "evidence_ref": "deploy-1"}],
            external_acceptance=[{"acceptor": "customer", "status": "not_verified"}],
        )
        self.assertEqual(value["local"]["status"], "not_verified")
        self.assertEqual(value["candidate"]["status"], "not_verified")
        self.assertEqual(value["quality"]["status"], "not_verified")
        self.assertEqual(value["canonical"]["status"], "not_verified")
        self.assertEqual(value["deployments"][0]["status"], "blocked_unknown")
        self.assertEqual(value["external_acceptance"][0]["status"], "not_verified")

    def test_evidence_cli_is_read_only(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            result = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "evidence_layers.py"),
                    "--candidate-id",
                    "candidate-a",
                    "--candidate-commit",
                    "a" * 40,
                ],
                capture_output=True,
                text=True,
                check=True,
                cwd=temp,
            )
            value = json.loads(result.stdout)
            self.assertEqual(value["candidate_id"], "candidate-a")
            self.assertFalse(value["write_performed"])


if __name__ == "__main__":
    unittest.main()
