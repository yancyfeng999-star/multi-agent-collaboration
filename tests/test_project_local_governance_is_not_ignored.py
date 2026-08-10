from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import emit_event  # noqa: E402
import freeze_scope  # noqa: E402
import preflight_lib  # noqa: E402
import validate_run  # noqa: E402


class ProjectLocalGovernanceDirtyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(["git", "-C", str(self.project), "config", "user.email", "fixture@example.invalid"], check=True)
        subprocess.run(["git", "-C", str(self.project), "config", "user.name", "Fixture"], check=True)
        (self.project / "tracked.txt").write_text("fixture\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.project), "add", "tracked.txt"], check=True)
        subprocess.run(["git", "-C", str(self.project), "commit", "-qm", "fixture"], check=True)
        legacy = self.project / ".multi-agent-collaboration"
        legacy.mkdir()
        (legacy / "unexpected.txt").write_text("must count as project dirt\n", encoding="utf-8")

    def test_project_local_governance_is_not_a_clean_worktree_exception(self) -> None:
        self.assertFalse(emit_event.git_worktree_clean(self.project))
        self.assertFalse(validate_run.git_worktree_clean(self.project))
        self.assertIn(".multi-agent-collaboration/", freeze_scope._dirty(self.project))
        status, paths = preflight_lib._git_status(self.project)
        self.assertEqual(status, "dirty")
        self.assertIn(".multi-agent-collaboration/", paths)


if __name__ == "__main__":
    unittest.main()
