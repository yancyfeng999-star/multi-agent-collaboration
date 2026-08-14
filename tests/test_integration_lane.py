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

from integration_lane import evaluate_candidate, integrate_candidate  # noqa: E402
from protocol_lib import ProtocolError  # noqa: E402


def git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def write_policy(root: Path) -> Path:
    path = root.parent / "integration-policy.yaml"
    path.write_text(
        "\n".join(
            (
                'schema_version: "1.0"',
                'canonical_branch: "main"',
                'working_branch: "update"',
                'high_conflict_paths: ["src/shared"]',
                'integration_method: "merge_preserve_candidate"',
                "",
            )
        ),
        encoding="utf-8",
    )
    return path


def candidate(
    path: Path,
    *,
    candidate_id: str,
    baseline: str,
    commit: str,
    changed_paths: list[str],
    workspace: str = "workspace-a",
    dependencies: list[str] | None = None,
    logical_resources: list[str] | None = None,
    environment_resources: list[str] | None = None,
    version_source: str | None = None,
    migration_order: int = 0,
    release_lane: str = "none",
    quality_required: bool = False,
    quality_status: str = "not_required",
    status: str = "ready",
) -> Path:
    value = {
        "schema_version": "1.0",
        "candidate_id": candidate_id,
        "baseline_commit": baseline,
        "candidate_commit": commit,
        "changed_paths": changed_paths,
        "verification": [
            {
                "command": ["git", "diff", "--check"],
                "status": "passed",
                "completed_at": "2026-08-14T10:00:00+08:00",
            }
        ],
        "risk_flags": [],
        "owner": "worker",
        "status": status,
        "dependencies": dependencies or [],
        "logical_resources": logical_resources or [],
        "environment_resources": environment_resources or [],
        "workspace": workspace,
        "version_source": version_source,
        "migration_order": migration_order,
        "release_lane": release_lane,
        "quality_required": quality_required,
        "quality_status": quality_status,
    }
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


class IntegrationLaneTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / "project"
        self.root.mkdir()
        git(self.root, "init", "-q")
        git(self.root, "config", "user.name", "Integration Test")
        git(self.root, "config", "user.email", "integration@example.test")
        (self.root / "README.md").write_text("base\n", encoding="utf-8")
        git(self.root, "add", "README.md")
        git(self.root, "commit", "-q", "-m", "base")
        git(self.root, "branch", "-M", "main")
        git(self.root, "checkout", "-q", "-b", "update")
        self.base = git(self.root, "rev-parse", "HEAD")
        self.policy = write_policy(self.root)
        self.candidate_dir = self.root.parent / "candidates"
        self.candidate_dir.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _commit_candidate(self, name: str, relative: str, content: str) -> str:
        git(self.root, "checkout", "-q", "-b", name)
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        git(self.root, "add", relative)
        git(self.root, "commit", "-q", "-m", name)
        return git(self.root, "rev-parse", "HEAD")

    def test_evaluate_fails_closed_when_git_paths_do_not_match_candidate(self) -> None:
        commit = self._commit_candidate("candidate-a", "src/a.txt", "a\n")
        record = candidate(
            self.candidate_dir / "A.json",
            candidate_id="A",
            baseline=self.base,
            commit=commit,
            changed_paths=["src/not-real.txt"],
        )
        result = evaluate_candidate(record, self.root, self.policy)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("changed_paths_mismatch", result["blockers"])

    def test_quality_and_dependency_block_only_the_unready_candidate(self) -> None:
        commit = self._commit_candidate("candidate-a", "src/a.txt", "a\n")
        record = candidate(
            self.candidate_dir / "A.json",
            candidate_id="A",
            baseline=self.base,
            commit=commit,
            changed_paths=["src/a.txt"],
            dependencies=["external-review"],
            quality_required=True,
            quality_status="unknown",
        )
        result = evaluate_candidate(record, self.root, self.policy)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("dependencies_not_proven", result["blockers"])
        self.assertIn("quality_not_passed", result["blockers"])

    def test_disjoint_candidates_can_be_ready_and_shared_paths_conflict(self) -> None:
        commit_a = self._commit_candidate("candidate-a", "src/a.txt", "a\n")
        record_a = candidate(
            self.candidate_dir / "A.json",
            candidate_id="A",
            baseline=self.base,
            commit=commit_a,
            changed_paths=["src/a.txt"],
        )
        git(self.root, "checkout", "-q", "update")
        commit_b = self._commit_candidate("candidate-b", "src/b.txt", "b\n")
        record_b = candidate(
            self.candidate_dir / "B.json",
            candidate_id="B",
            baseline=self.base,
            commit=commit_b,
            changed_paths=["src/b.txt"],
            workspace="workspace-b",
        )
        ready_a = evaluate_candidate(record_a, self.root, self.policy, against_candidates=[record_b])
        ready_b = evaluate_candidate(record_b, self.root, self.policy, against_candidates=[record_a])
        self.assertEqual(ready_a["status"], "ready")
        self.assertEqual(ready_b["status"], "ready")

        conflict = candidate(
            self.candidate_dir / "C.json",
            candidate_id="C",
            baseline=self.base,
            commit=commit_b,
            changed_paths=["src/a.txt"],
        )
        conflicted = evaluate_candidate(record_a, self.root, self.policy, against_candidates=[conflict])
        self.assertEqual(conflicted["status"], "conflicted")
        self.assertIn("changed_paths", conflicted["conflicts"][0]["dimensions"])

    def test_evaluate_does_not_move_any_ref(self) -> None:
        commit = self._commit_candidate("candidate-a", "src/a.txt", "a\n")
        record = candidate(
            self.candidate_dir / "A.json",
            candidate_id="A",
            baseline=self.base,
            commit=commit,
            changed_paths=["src/a.txt"],
        )
        before = git(self.root, "rev-parse", "refs/heads/update")
        result = evaluate_candidate(record, self.root, self.policy)
        after = git(self.root, "rev-parse", "refs/heads/update")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(before, after)
        self.assertFalse(result["write_performed"])

    def test_integrate_requires_confirmation_and_preserves_candidate_reachability(self) -> None:
        commit = self._commit_candidate("candidate-a", "src/a.txt", "a\n")
        record = candidate(
            self.candidate_dir / "A.json",
            candidate_id="A",
            baseline=self.base,
            commit=commit,
            changed_paths=["src/a.txt"],
        )
        with self.assertRaises(ProtocolError):
            integrate_candidate(record, self.root, self.policy, target="working", user_confirmed=False)

        result = integrate_candidate(record, self.root, self.policy, target="working", user_confirmed=True)
        self.assertEqual(result["status"], "integrated")
        self.assertEqual(result["candidate_commit"], commit)
        self.assertTrue(result["candidate_reachable"])
        self.assertTrue(result["integrated_commit"])
        self.assertEqual(git(self.root, "rev-parse", "refs/heads/update"), result["integrated_commit"])
        self.assertEqual(git(self.root, "merge-base", "--is-ancestor", commit, "refs/heads/update"), "")
        self.assertEqual(result["coordination_message"]["kind"], "INTEGRATED")

    def test_integrate_requires_policy_and_rejects_stale_target(self) -> None:
        commit = self._commit_candidate("candidate-a", "src/a.txt", "a\n")
        record = candidate(
            self.candidate_dir / "A.json",
            candidate_id="A",
            baseline=self.base,
            commit=commit,
            changed_paths=["src/a.txt"],
        )
        with self.assertRaises(ProtocolError):
            integrate_candidate(record, self.root, self.root.parent / "missing.yaml", target="working", user_confirmed=True)
        git(self.root, "checkout", "-q", "update")
        (self.root / "other.txt").write_text("other\n", encoding="utf-8")
        git(self.root, "add", "other.txt")
        git(self.root, "commit", "-q", "-m", "advance")
        with self.assertRaises(ProtocolError):
            integrate_candidate(record, self.root, self.policy, target="working", user_confirmed=True)

    def test_merge_policy_keeps_both_target_and_candidate_history_reachable(self) -> None:
        commit = self._commit_candidate("candidate-a", "src/a.txt", "a\n")
        record = candidate(
            self.candidate_dir / "A.json",
            candidate_id="A",
            baseline=self.base,
            commit=commit,
            changed_paths=["src/a.txt"],
            workspace="workspace-a",
        )
        git(self.root, "checkout", "-q", "update")
        (self.root / "src").mkdir(exist_ok=True)
        (self.root / "src" / "target.txt").write_text("target\n", encoding="utf-8")
        git(self.root, "add", "src/target.txt")
        git(self.root, "commit", "-q", "-m", "target")
        target_commit = git(self.root, "rev-parse", "HEAD")
        git(self.root, "checkout", "-q", "candidate-a")
        result = integrate_candidate(record, self.root, self.policy, target="working", user_confirmed=True)
        self.assertEqual(result["status"], "integrated")
        self.assertNotEqual(result["integrated_commit"], target_commit)
        self.assertTrue(isinstance(result["integrated_commit"], str))
        self.assertTrue(git(self.root, "merge-base", "--is-ancestor", commit, result["integrated_commit"]) == "")
        self.assertTrue(git(self.root, "merge-base", "--is-ancestor", target_commit, result["integrated_commit"]) == "")


if __name__ == "__main__":
    unittest.main()
