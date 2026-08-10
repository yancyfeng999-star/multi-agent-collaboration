from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.governance_test_support import governance_project, governance_root


ROOT = Path(__file__).resolve().parents[1]
REBUILD = ROOT / "scripts" / "rebuild_index.py"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class RuntimeIndexingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "project"
        self.project.mkdir()
        self.governance = governance_root(self.temporary.name)
        self.bus = governance_project(self.temporary.name, self.project)

    def write_json(self, relative: str, value: dict) -> Path:
        path = self.bus / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, sort_keys=True) + "\n", encoding="utf-8")
        return path

    def write_md(self, relative: str, value: dict) -> Path:
        path = self.bus / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["---", *(f"{key}: {json.dumps(item)}" for key, item in value.items()), "---", ""]
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def run_rebuild(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(REBUILD), "--project-root", str(self.project),
             "--governance-root", str(self.governance)],
            text=True,
            capture_output=True,
        )

    def fixture(self) -> dict[str, Path]:
        profile = self.write_json(
            "agents/A02-worker/runtime/RP-000001.json",
            {"doc_type": "runtime_profile", "runtime_profile_id": "RP-000001", "agent_id": "A02-worker"},
        )
        agent_profile = self.write_json(
            "agents/A02-worker/AGENT_PROFILE.json",
            {"doc_type": "agent_profile", "agent_id": "A02-worker", "lifecycle": {"status": "active"}},
        )
        task = self.write_md(
            "agents/A02-worker/tasks/TASK-LONG-001.md",
            {"doc_type": "task", "task_id": "TASK-LONG-001", "owner": "A02-worker", "status": "active"},
        )
        source = self.write_md(
            "agents/A02-worker/handoffs/HANDOFF-001.md",
            {"doc_type": "handoff", "task_id": "TASK-LONG-001", "agent_id": "A02-worker"},
        )
        activity = self.write_json(
            "agents/A02-worker/activity/RUN-001/TASK-LONG-001/ATTEMPT-001/ACTIVITY-000001.json",
            {
                "record_kind": "attempt_finished",
                "agent_id": "A02-worker",
                "run_id": "RUN-001",
                "task_id": "TASK-LONG-001",
                "runtime_profile": {
                    "native_binding_ref": "agents/A02-worker/runtime/RP-000001.json",
                    "native_binding_sha256": digest(profile),
                },
                "source": {
                    "source_ref": "agents/A02-worker/handoffs/HANDOFF-001.md",
                    "source_sha256": digest(source),
                },
            },
        )
        checkpoint = self.write_json(
            "project-checkpoints/PCP-0001.json",
            {
                "doc_type": "project_checkpoint",
                "associated_runs": ["RUN-001"],
                "agent_runtime_snapshots": [{
                    "agent_id": "A02-worker",
                    "runtime_profiles": [{"ref": "agents/A02-worker/runtime/RP-000001.json", "sha256": digest(profile)}],
                    "activity_refs": [{"ref": activity.relative_to(self.bus).as_posix(), "sha256": digest(activity)}],
                    "handoff_refs": [{"ref": source.relative_to(self.bus).as_posix(), "sha256": digest(source)}],
                }],
            },
        )
        bridge = self.write_json(
            "bridges/RUN-001/bridge.json",
            {
                "doc_type": "run_memory_bridge",
                "run_id": "RUN-001",
                "tasks": [{
                    "task_id": "TASK-LONG-001",
                    "agent_id": "A02-worker",
                    "runtime_profile_path": profile.relative_to(self.bus).as_posix(),
                    "runtime_profile_sha256": digest(profile),
                    "activity_record_path": activity.relative_to(self.bus).as_posix(),
                    "activity_record_sha256": digest(activity),
                }],
            },
        )
        return {"profile": profile, "agent_profile": agent_profile, "task": task, "source": source,
                "activity": activity, "checkpoint": checkpoint, "bridge": bridge}

    def records(self) -> list[dict]:
        return [json.loads(line) for line in (self.bus / "index.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_indexes_runtime_documents_relationships_owner_and_is_idempotent(self) -> None:
        paths = self.fixture()
        first = self.run_rebuild()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = (self.bus / "index.jsonl").read_bytes()
        second = self.run_rebuild()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(before, (self.bus / "index.jsonl").read_bytes())

        records = self.records()
        self.assertEqual([r["path"] for r in records], sorted(r["path"] for r in records))
        by_type = {r["doc_type"]: r for r in records}
        self.assertTrue({"runtime-profile", "activity", "agent-profile", "project-checkpoint", "bridge"} <= set(by_type))
        for record in records:
            self.assertEqual(record["hash"], digest(self.bus / record["path"]))
            self.assertEqual(
                {"path", "hash", "doc_type", "agent", "owner", "task", "run", "runtime", "activity"} - record.keys(),
                set(),
            )
        self.assertEqual(by_type["runtime-profile"]["agent"], ["A02-worker"])
        task_record = next(r for r in records if r["path"] == paths["task"].relative_to(self.bus).as_posix())
        self.assertEqual(task_record["owner"], ["A02-worker"])
        self.assertEqual(task_record["agent"], ["A02-worker"])
        self.assertEqual(by_type["activity"]["run"], ["RUN-001"])
        self.assertEqual(by_type["activity"]["runtime"], ["agents/A02-worker/runtime/RP-000001.json"])
        self.assertEqual(by_type["project-checkpoint"]["activity"], [paths["activity"].relative_to(self.bus).as_posix()])
        self.assertEqual(by_type["bridge"]["runtime"], [paths["profile"].relative_to(self.bus).as_posix()])
        self.assertEqual(by_type["bridge"]["activity"], [paths["activity"].relative_to(self.bus).as_posix()])

    def assert_fail_closed(self, mutate, expected: str) -> None:
        paths = self.fixture()
        self.assertEqual(self.run_rebuild().returncode, 0)
        old = (self.bus / "index.jsonl").read_bytes()
        mutate(paths)
        failed = self.run_rebuild()
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(expected, failed.stderr.lower())
        self.assertEqual(old, (self.bus / "index.jsonl").read_bytes())

    def test_tampered_hashed_runtime_reference_fails_closed(self) -> None:
        self.assert_fail_closed(
            lambda paths: paths["profile"].write_text(
                json.dumps({"doc_type": "runtime_profile", "runtime_profile_id": "RP-000001", "agent_id": "A02-worker", "tampered": True}) + "\n",
                encoding="utf-8",
            ),
            "hash mismatch",
        )

    def test_dangling_activity_reference_fails_closed(self) -> None:
        self.assert_fail_closed(lambda paths: paths["activity"].unlink(), "missing referenced path")


if __name__ == "__main__":
    unittest.main()
