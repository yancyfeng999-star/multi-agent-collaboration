from __future__ import annotations

import json
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
REBUILD = SKILL_DIR / "scripts" / "rebuild_index.py"


class RebuildIndexTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.project = Path(self.temporary.name) / "project"
        self.bus = self.project / ".multi-agent-collaboration"
        self.bus.mkdir(parents=True)

    def write_doc(self, relative: str, fields: dict[str, object], body: str = "") -> Path:
        path = self.bus / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["---"]
        for key, value in fields.items():
            lines.append(f"{key}: {json.dumps(value, ensure_ascii=False)}")
        lines.extend(("---", "", body))
        path.write_text("\n".join(lines), encoding="utf-8")
        return path

    def run_rebuild(self) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["python3", str(REBUILD), "--project-root", str(self.project)],
            text=True,
            capture_output=True,
        )

    def valid_fixture(self) -> None:
        (self.project / "deliverables").mkdir()
        (self.project / "deliverables" / "result.txt").write_text("ok\n", encoding="utf-8")
        self.write_doc(
            "runs/R/tasks/TASK-002.md",
            {
                "doc_type": "task",
                "task_id": "TASK-002",
                "owner_agent": "A02-worker",
                "status": "completed",
                "risk_flags": ["release"],
                "keywords": ["beta", "alpha"],
                "owned_paths": ["deliverables/result.txt"],
                "verification": ["pytest"],
                "deliverables": ["deliverables/result.txt"],
            },
        )
        self.write_doc(
            "runs/R/tasks/TASK-001.md",
            {
                "doc_type": "task",
                "task_id": "TASK-001",
                "owner_agent": "A01-worker",
                "status": "handoff_ready",
                "risk_flags": [],
                "keywords": ["index"],
                "related_files": ["deliverables/result.txt"],
                "verification": ["python -m unittest"],
                "deliverable": "deliverables/result.txt",
            },
        )
        self.write_doc(
            "runs/R/outbox/A01-worker/TASK-001-result.md",
            {
                "kind": "result",
                "task_id": "TASK-001",
                "agent_id": "A01-worker",
                "status": "completed",
                "changed_files": ["deliverables/result.txt"],
                "verification_status": "passed",
                "verification_refs": ["runs/R/evidence/VERIFY-1.yaml"],
                "handoff_to": "coordinator",
            },
        )
        evidence = self.bus / "runs/R/evidence/VERIFY-1.yaml"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text(
            '\n'.join((
                'kind: "verification"',
                'evidence_id: "VERIFY-1"',
                'task_id: "TASK-001"',
                'agent_id: "A01-worker"',
                'status: "passed"',
                'artifact_refs: ["deliverables/result.txt"]',
                'summary: "tests passed"',
                '',
            )),
            encoding="utf-8",
        )
        self.write_doc(
            "agents/A01-worker/conversations/checkpoints/CP-0001.md",
            {"doc_type": "checkpoint", "agent_id": "A01-worker", "task_ids": ["TASK-001"], "status": "active"},
        )
        self.write_doc(
            "agents/A01-worker/conversations/archive/2026-01.md",
            {"doc_type": "archive", "agent_id": "A01-worker", "task_ids": ["TASK-001"], "keywords": ["history"]},
        )
        decision = self.bus / "runs/R/decisions/DEC-1.json"
        decision.parent.mkdir(parents=True, exist_ok=True)
        decision.write_text(json.dumps({"kind": "decision", "task_id": "TASK-001", "agent_id": "coordinator", "status": "approved"}), encoding="utf-8")
        artifact = self.bus / "runs/R/artifacts/build.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps({"kind": "artifact", "task_id": "TASK-002", "agent_id": "A02-worker", "deliverable": "deliverables/result.txt"}), encoding="utf-8")

    def records(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in (self.bus / "index.jsonl").read_text(encoding="utf-8").splitlines()]

    def test_builds_complete_stable_consistent_index_without_rewriting_unchanged_output(self) -> None:
        self.valid_fixture()
        first = self.run_rebuild()
        self.assertEqual(first.returncode, 0, first.stderr)
        json_before = (self.bus / "index.jsonl").read_bytes()
        md_before = (self.bus / "INDEX.md").read_bytes()
        mtimes_before = ((self.bus / "index.jsonl").stat().st_mtime_ns, (self.bus / "INDEX.md").stat().st_mtime_ns)
        time.sleep(0.02)
        second = self.run_rebuild()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json_before, (self.bus / "index.jsonl").read_bytes())
        self.assertEqual(md_before, (self.bus / "INDEX.md").read_bytes())
        self.assertEqual(mtimes_before, ((self.bus / "index.jsonl").stat().st_mtime_ns, (self.bus / "INDEX.md").stat().st_mtime_ns))

        tampered = (self.bus / "INDEX.md").read_text(encoding="utf-8").replace("# 项目索引", "# 损坏索引")
        (self.bus / "INDEX.md").write_text(tampered, encoding="utf-8")
        repaired = self.run_rebuild()
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        self.assertIn("# 项目索引", (self.bus / "INDEX.md").read_text(encoding="utf-8"))
        self.assertNotIn("# 损坏索引", (self.bus / "INDEX.md").read_text(encoding="utf-8"))

        records = self.records()
        record_paths = [str(record["path"]) for record in records]
        self.assertEqual(record_paths, sorted(record_paths))
        self.assertEqual({record["kind"] for record in records}, {"archive", "checkpoint", "task", "handoff", "decision", "evidence", "artifact"})
        required = {"path", "kind", "agents", "tasks", "status", "risk", "keywords", "related_files", "verification", "deliverable", "sha256"}
        self.assertTrue(all(required <= record.keys() for record in records))
        self.assertTrue(all(len(str(record["sha256"])) == 64 for record in records))
        index_md = (self.bus / "INDEX.md").read_text(encoding="utf-8")
        for record in records:
            self.assertEqual(index_md.count(f"`{record['path']}`"), 1)
            self.assertIn(str(record["sha256"]), index_md)

    def assert_fail_closed(self, expected: str, mutate) -> None:
        self.valid_fixture()
        self.assertEqual(self.run_rebuild().returncode, 0)
        old_json = (self.bus / "index.jsonl").read_bytes()
        old_md = (self.bus / "INDEX.md").read_bytes()
        mutate()
        failed = self.run_rebuild()
        self.assertNotEqual(failed.returncode, 0)
        self.assertIn(expected, failed.stderr)
        self.assertEqual(old_json, (self.bus / "index.jsonl").read_bytes())
        self.assertEqual(old_md, (self.bus / "INDEX.md").read_bytes())

    def test_duplicate_task_id_fails_closed(self) -> None:
        self.assert_fail_closed(
            "duplicate task ID",
            lambda: self.write_doc("runs/R/tasks/copy.md", {"doc_type": "task", "task_id": "TASK-001", "owner_agent": "A01-worker"}),
        )

    def test_dangling_task_reference_fails_closed(self) -> None:
        self.assert_fail_closed(
            "dangling task reference",
            lambda: self.write_doc("runs/R/decisions/bad.md", {"doc_type": "decision", "task_id": "TASK-404"}),
        )

    def test_missing_related_path_fails_closed(self) -> None:
        def mutate() -> None:
            path = self.bus / "runs/R/tasks/TASK-001.md"
            path.write_text(path.read_text(encoding="utf-8").replace('"deliverables/result.txt"', '"missing.txt"'), encoding="utf-8")
        self.assert_fail_closed("missing referenced path", mutate)

    def test_task_handoff_agent_mismatch_fails_closed(self) -> None:
        def mutate() -> None:
            path = self.bus / "runs/R/outbox/A01-worker/TASK-001-result.md"
            path.write_text(path.read_text(encoding="utf-8").replace('agent_id: "A01-worker"', 'agent_id: "A99-other"'), encoding="utf-8")
        self.assert_fail_closed("task-handoff mismatch", mutate)


if __name__ == "__main__":
    unittest.main()
