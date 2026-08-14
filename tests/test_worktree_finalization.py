from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Callable


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

from finalize_worktree import audit_worktree, cleanup_worktree  # noqa: E402
from protocol_lib import ProtocolError  # noqa: E402


def git(root: Path, *args: str) -> str:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True)
    return result.stdout.strip()


class WorktreeFinalizationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        git(self.root, "init", "-q")
        git(self.root, "config", "user.name", "Finalizer Test")
        git(self.root, "config", "user.email", "finalizer@example.test")
        (self.root / "README.md").write_text("base\n", encoding="utf-8")
        git(self.root, "add", "README.md")
        git(self.root, "commit", "-q", "-m", "base")
        git(self.root, "branch", "-M", "main")
        self.worktree = Path(self.temp.name) / "worktree"
        git(self.root, "worktree", "add", "-q", "-b", "candidate", str(self.worktree), "main")
        (self.worktree / "candidate.txt").write_text("candidate\n", encoding="utf-8")
        git(self.worktree, "add", "candidate.txt")
        git(self.worktree, "commit", "-q", "-m", "candidate")
        self.candidate_commit = git(self.worktree, "rev-parse", "HEAD")

    def tearDown(self) -> None:
        self.temp.cleanup()

    def no_process(self, _path: Path) -> list[str]:
        return []

    def test_clean_registered_worktree_with_preserved_commit_is_ready(self) -> None:
        result = audit_worktree(
            self.root,
            self.worktree,
            candidate_commit=self.candidate_commit,
            process_checker=self.no_process,
        )
        self.assertTrue(result["ready"])
        self.assertEqual(result["blockers"], [])
        self.assertTrue(result["candidate_preserved"])
        self.assertFalse(result["write_performed"])

    def test_dirty_merge_and_process_or_release_state_block_cleanup(self) -> None:
        (self.worktree / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        result = audit_worktree(
            self.root,
            self.worktree,
            candidate_commit=self.candidate_commit,
            process_checker=lambda _path: ["pid=1 active-worker"],
            release_active=True,
        )
        self.assertFalse(result["ready"])
        self.assertIn("worktree_dirty", result["blockers"])
        self.assertIn("active_processes", result["blockers"])
        self.assertIn("release_or_freeze_active", result["blockers"])

        merge_head = Path(git(self.worktree, "rev-parse", "--git-path", "MERGE_HEAD"))
        if not merge_head.is_absolute():
            merge_head = self.worktree / merge_head
        merge_head.parent.mkdir(parents=True, exist_ok=True)
        merge_head.write_text(self.candidate_commit + "\n", encoding="utf-8")
        result = audit_worktree(
            self.root,
            self.worktree,
            candidate_commit=self.candidate_commit,
            process_checker=self.no_process,
        )
        self.assertIn("merge_in_progress", result["blockers"])

    def test_root_symlink_and_unregistered_targets_are_rejected(self) -> None:
        with self.assertRaises(ProtocolError):
            audit_worktree(self.root, self.root, candidate_commit=self.candidate_commit, process_checker=self.no_process)
        link = Path(self.temp.name) / "worktree-link"
        link.symlink_to(self.worktree, target_is_directory=True)
        result = audit_worktree(self.root, link, candidate_commit=self.candidate_commit, process_checker=self.no_process)
        self.assertFalse(result["ready"])
        self.assertIn("symlink_target", result["blockers"])
        other = Path(self.temp.name) / "other"
        other.mkdir()
        result = audit_worktree(self.root, other, candidate_commit=self.candidate_commit, process_checker=self.no_process)
        self.assertIn("worktree_not_registered", result["blockers"])

    def test_cleanup_requires_confirmation_and_removes_only_ready_worktree(self) -> None:
        with self.assertRaises(ProtocolError):
            cleanup_worktree(
                self.root,
                self.worktree,
                candidate_commit=self.candidate_commit,
                process_checker=self.no_process,
                user_confirmed=False,
            )
        result = cleanup_worktree(
            self.root,
            self.worktree,
            candidate_commit=self.candidate_commit,
            process_checker=self.no_process,
            user_confirmed=True,
        )
        self.assertTrue(result["removed"])
        self.assertFalse(self.worktree.exists())


if __name__ == "__main__":
    unittest.main()
