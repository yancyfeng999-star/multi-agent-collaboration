from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "scripts/init_run.py"


class ExternalRunStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "website"
        self.project.mkdir()
        self.governance = self.root / "governance"

    def run_init(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "python3",
                str(INIT),
                "--project-root",
                str(self.project),
                "--project-id",
                "website",
                "--project-name",
                "Website",
                "--governance-root",
                str(self.governance),
                "--governance",
                "light",
                "--transport",
                "document_bus",
                "--objective",
                "external run fixture",
                "--run-id",
                "RUN-EXTERNAL",
                "--versioning-mode",
                "not_applicable",
                "--versioning-reason",
                "Fixture has no project release",
                "--user-confirmed",
                *extra,
            ],
            capture_output=True,
            text=True,
        )

    def test_direct_mode_is_default_and_never_creates_a_run(self) -> None:
        result = self.run_init()

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Direct mode does not create a Run", result.stderr)
        self.assertFalse(self.governance.exists())
        self.assertFalse((self.project / ".multi-agent-collaboration").exists())
        self.assertFalse((self.project / "AGENTS.md").exists())

    def test_coordinated_run_is_created_only_in_external_governance_home(self) -> None:
        result = self.run_init("--coordination-mode", "coordinated")

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        run_dir = Path(result.stdout.strip())
        self.assertEqual(
            run_dir,
            self.governance.resolve() / "projects/website/runs/RUN-EXTERNAL",
        )
        self.assertTrue((run_dir / "manifest.yaml").is_file())
        self.assertTrue((self.governance / "projects/website/project-binding.yaml").is_file())
        self.assertFalse((self.project / ".multi-agent-collaboration").exists())
        self.assertFalse((self.project / "AGENTS.md").exists())


if __name__ == "__main__":
    unittest.main()
