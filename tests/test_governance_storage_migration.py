from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/migrate_governance_storage.py"


class GovernanceStorageMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "website"
        self.project.mkdir()
        (self.project / "index.html").write_text("website\n", encoding="utf-8")
        self.legacy = self.project / ".multi-agent-collaboration"
        (self.legacy / "agents/A01-coordinator/handoffs").mkdir(parents=True)
        (self.legacy / "TEAM.yaml").write_bytes(b'{"project_id":"website"}\n')
        (self.legacy / "agents/A01-coordinator/ROLE.md").write_bytes(b"role\n")
        (self.legacy / "agents/A01-coordinator/handoffs/TASK-1.md").write_bytes(b"handoff\n")
        self.governance = self.root / "governance"
        self.target = self.governance / "projects/website"

    def command(self, mode: str, *, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3", str(SCRIPT), "--project-root", str(self.project),
                "--project-id", "website", "--project-name", "Website",
                "--governance-root", str(self.governance), mode,
            ],
            capture_output=True,
            text=True,
            env={**os.environ, **(env or {})},
        )

    def source_inventory(self) -> dict[str, str]:
        return {
            path.relative_to(self.legacy).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in self.legacy.rglob("*") if path.is_file()
        }

    def test_dry_run_has_no_side_effects(self) -> None:
        before = self.source_inventory()
        result = self.command("--dry-run")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        plan = json.loads(result.stdout)
        self.assertTrue(plan["dry_run"])
        self.assertEqual(plan["source"], str(self.legacy.resolve()))
        self.assertEqual(plan["target"], str(self.target.resolve()))
        self.assertEqual({item["path"]: item["sha256"] for item in plan["files"]}, before)
        self.assertFalse(self.governance.exists())
        self.assertEqual(self.source_inventory(), before)

    def test_apply_copies_original_bytes_writes_manifest_and_rolls_back_failure(self) -> None:
        before = self.source_inventory()
        failed = self.command("--apply", env={"GOVERNANCE_MIGRATION_FAIL_AFTER": "copy"})
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.source_inventory(), before)

        result = self.command("--apply")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        payload = json.loads(result.stdout)
        manifest = Path(payload["manifest"])
        self.assertTrue(manifest.is_file())
        self.assertTrue((self.target / "project-binding.yaml").is_file())
        for relative, digest in before.items():
            copied = self.target / relative
            self.assertTrue(copied.is_file())
            self.assertEqual(hashlib.sha256(copied.read_bytes()).hexdigest(), digest)
        recorded = json.loads(manifest.read_text(encoding="utf-8"))
        self.assertEqual(recorded["source_inventory_sha256"], payload["source_inventory_sha256"])
        self.assertEqual({item["path"]: item["sha256"] for item in recorded["files"]}, before)
        self.assertEqual(self.source_inventory(), before, "legacy source must never be deleted or rewritten")

    def test_symlink_and_existing_target_are_rejected(self) -> None:
        outside = self.root / "outside.txt"
        outside.write_text("outside\n", encoding="utf-8")
        (self.legacy / "escape").symlink_to(outside)
        rejected = self.command("--dry-run")
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("symlink", rejected.stderr.lower())
        (self.legacy / "escape").unlink()

        self.target.mkdir(parents=True)
        conflict = self.command("--apply")
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn("target", conflict.stderr.lower())


if __name__ == "__main__":
    unittest.main()
