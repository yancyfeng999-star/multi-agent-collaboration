from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from conflict_model import find_conflict  # noqa: E402


class ParallelWorktreePolicyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        (self.project / "src").mkdir()
        (self.project / "worktree-a").mkdir()
        (self.project / "worktree-b").mkdir()

    def task(self, task_id: str, path: str, workspace: Path, policy: str = "isolated_writer") -> dict[str, str]:
        return {
            "task_id": task_id,
            "owned_paths": f'["{self.project / path}"]',
            "logical_resources": "[]",
            "environment_resources": "[]",
            "workspace": str(workspace),
            "workspace_policy": policy,
            "release_lane": "none",
        }

    def test_independent_worktrees_allow_parallel_writers(self) -> None:
        active = self.task("TASK-A", "src/a", self.project / "worktree-a")
        candidate = self.task("TASK-B", "src/b", self.project / "worktree-b")
        self.assertIsNone(find_conflict(candidate, [active], self.project))

    def test_same_worktree_rejects_parallel_writers(self) -> None:
        active = self.task("TASK-A", "src/a", self.project / "worktree-a")
        candidate = self.task("TASK-B", "src/b", self.project / "worktree-a")
        self.assertEqual(find_conflict(candidate, [active], self.project), "workspace_conflict:TASK-A")

    def test_shared_read_only_worktree_is_allowed(self) -> None:
        active = self.task("TASK-A", "src/a", self.project / "worktree-a", "shared_read_only")
        candidate = self.task("TASK-B", "src/b", self.project / "worktree-a", "shared_read_only")
        self.assertIsNone(find_conflict(candidate, [active], self.project))


if __name__ == "__main__":
    unittest.main()
