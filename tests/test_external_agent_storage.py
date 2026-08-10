from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "scripts/init_project_agents.py"
VALIDATE = ROOT / "scripts/validate_agents.py"


class ExternalAgentStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "website"
        self.project.mkdir()
        (self.project / "index.html").write_text("website\n", encoding="utf-8")
        self.governance = self.root / "governance"

    def command(self, script: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(script), *arguments],
            capture_output=True,
            text=True,
        )

    def test_agent_identity_and_handoff_storage_stays_outside_project(self) -> None:
        before = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*")
            if path.is_file()
        }
        result = self.command(
            INIT,
            "--project-root",
            str(self.project),
            "--governance-root",
            str(self.governance),
            "--project-id",
            "website",
            "--project-name",
            "Website",
            "--agents",
            "A01-coordinator,A02-owner",
            "--governance",
            "standard",
            "--user-confirmed",
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        governance_project = self.governance.resolve() / "projects/website"
        self.assertTrue((governance_project / "TEAM.yaml").is_file())
        self.assertTrue((governance_project / "agents/A02-owner/ROLE.md").is_file())
        self.assertTrue((governance_project / "agents/A02-owner/AGENT_PROFILE.json").is_file())
        self.assertTrue((governance_project / "agents/A02-owner/handoffs").is_dir())
        after = {
            path.relative_to(self.project).as_posix(): path.read_bytes()
            for path in self.project.rglob("*")
            if path.is_file()
        }
        self.assertEqual(after, before)
        self.assertFalse((self.project / ".multi-agent-collaboration").exists())
        self.assertFalse((self.project / "AGENTS.md").exists())

        validation = self.command(
            VALIDATE,
            "--project-root",
            str(self.project),
            "--governance-root",
            str(self.governance),
            "--project-id",
            "website",
        )
        self.assertEqual(validation.returncode, 0, validation.stdout + validation.stderr)


if __name__ == "__main__":
    unittest.main()
