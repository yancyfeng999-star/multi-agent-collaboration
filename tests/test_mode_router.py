from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from mode_router import select_mode  # noqa: E402


class ModeRouterTests(unittest.TestCase):
    def test_direct_is_default_for_one_writer(self) -> None:
        decision = select_mode({"independent_writers": 1})
        self.assertEqual(decision["mode"], "direct")
        self.assertEqual(decision["required_roles"], ["owner"])
        self.assertEqual(decision["persistence_level"], "none")
        self.assertEqual(decision["upgrade_reasons"], [])

    def test_single_emergency_bug_uses_direct_hotfix(self) -> None:
        decision = select_mode({"independent_writers": 1, "emergency": True})
        self.assertEqual(decision["mode"], "direct_hotfix")
        self.assertNotIn("coordinator", decision["required_roles"])
        self.assertNotIn("release", decision["required_roles"])
        self.assertEqual(decision["persistence_level"], "none")

    def test_independent_writers_upgrade_to_coordinated(self) -> None:
        decision = select_mode({"independent_writers": 2})
        self.assertEqual(decision["mode"], "coordinated")
        self.assertEqual(decision["required_roles"], ["coordinator", "owner"])
        self.assertEqual(decision["upgrade_reasons"], ["multiple_independent_writers"])
        self.assertEqual(decision["persistence_level"], "run")

    def test_quality_risk_uses_one_quality_role(self) -> None:
        decision = select_mode({"requires_independent_quality": True})
        self.assertEqual(decision["mode"], "reviewed")
        self.assertEqual(decision["required_roles"], ["owner", "quality"])
        self.assertEqual(decision["persistence_level"], "candidate")

    def test_real_release_takes_precedence(self) -> None:
        decision = select_mode(
            {
                "independent_writers": 3,
                "emergency": True,
                "requests_real_release": True,
            }
        )
        self.assertEqual(decision["mode"], "release")
        self.assertEqual(decision["required_roles"], ["integration_owner", "release"])
        self.assertEqual(decision["persistence_level"], "release_record")
        self.assertIn("real_release_requested", decision["upgrade_reasons"])

    def test_cli_is_read_only_and_returns_json(self) -> None:
        result = subprocess.run(
            [
                "python3",
                str(ROOT / "scripts" / "mode_router.py"),
                "--independent-writers",
                "2",
                "--emergency",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "coordinated_emergency")

    def test_invalid_writer_count_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "independent_writers"):
            select_mode({"independent_writers": 0})


if __name__ == "__main__":
    unittest.main()
