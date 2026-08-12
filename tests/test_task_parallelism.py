from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL_DIR / "scripts"))

from conflict_model import find_conflict  # noqa: E402


class TaskParallelismTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        (self.project / "src").mkdir()
        (self.project / "worktrees").mkdir()

    def task(self, task_id: str, path: str, **fields: str) -> dict[str, str]:
        values = {
            "task_id": task_id,
            "owned_paths": f'["{self.project / path}"]',
            "logical_resources": "[]",
            "environment_resources": "[]",
            "workspace": str(self.project / "worktrees" / task_id),
            "workspace_policy": "isolated_writer",
            "release_lane": "none",
        }
        values.update(fields)
        return values

    def test_explicit_writer_without_workspace_uses_shared_project_root(self) -> None:
        active = self.task("TASK-A", "src/a", workspace="", workspace_policy="isolated_writer")
        candidate = self.task("TASK-B", "src/b", workspace="", workspace_policy="isolated_writer")
        self.assertEqual(find_conflict(candidate, [active], self.project), "workspace_conflict:TASK-A")

    def test_same_capability_and_disjoint_paths_are_parallel(self) -> None:
        active = self.task("TASK-A", "src/a")
        candidate = self.task("TASK-B", "src/b")

        self.assertIsNone(find_conflict(candidate, [active], self.project))

    def test_same_logical_resource_is_serialized_even_when_paths_differ(self) -> None:
        active = self.task("TASK-A", "src/a", logical_resources='["logical:database/schema"]')
        candidate = self.task("TASK-B", "src/b", logical_resources='["logical:database/schema"]')

        self.assertEqual(find_conflict(candidate, [active], self.project), "logical_resource_conflict:TASK-A")

    def test_same_writer_workspace_is_serialized_but_shared_readers_can_run(self) -> None:
        active = self.task("TASK-A", "src/a", workspace=str(self.project / "worktree"))
        candidate = self.task("TASK-B", "src/b", workspace=str(self.project / "worktree"))
        self.assertEqual(find_conflict(candidate, [active], self.project), "workspace_conflict:TASK-A")

        reader = self.task(
            "TASK-C",
            "src/c",
            workspace=str(self.project / "worktree"),
            workspace_policy="shared_read_only",
        )
        reader_active = self.task(
            "TASK-D",
            "src/d",
            workspace=str(self.project / "worktree"),
            workspace_policy="shared_read_only",
        )
        self.assertIsNone(find_conflict(reader, [reader_active], self.project))

    def test_release_lane_is_single_writer(self) -> None:
        active = self.task("TASK-A", "src/a", release_lane="release:main")
        candidate = self.task("TASK-B", "src/b", release_lane="release:main")

        self.assertEqual(find_conflict(candidate, [active], self.project), "release_lane_conflict:TASK-A")


if __name__ == "__main__":
    unittest.main()
