from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


SKILL = Path(__file__).resolve().parents[1]
SCRIPTS = SKILL / "scripts"
sys.path.insert(0, str(SCRIPTS))

from message_contract import compact_messages, validate_message  # noqa: E402
from protocol_lib import ProtocolError  # noqa: E402


class AgentMessageCompactionTests(unittest.TestCase):
    def test_ordinary_progress_is_not_a_coordination_message(self) -> None:
        self.assertEqual(
            compact_messages(
                [
                    {"kind": "PROGRESS", "task_id": "TASK-1", "summary": "still working"},
                    {"kind": "LOG", "task_id": "TASK-1", "summary": "ran a command"},
                ]
            ),
            [],
        )

    def test_blocked_requires_evidence_scope_and_safe_disposition(self) -> None:
        with self.assertRaises(ProtocolError):
            validate_message({"kind": "BLOCKED", "task_id": "TASK-1", "blocker_code": "dirty"})
        message = validate_message(
            {
                "kind": "BLOCKED",
                "task_id": "TASK-1",
                "blocker_code": "dirty_worktree",
                "observed": "status evidence ref",
                "scope_impact": ["TASK-1"],
                "safe_default": "do_not_integrate",
                "recommended_disposition": "inspect_worktree",
            }
        )
        self.assertEqual(message["scope_impact"], ["TASK-1"])

    def test_deduplication_keeps_one_message_per_subject_and_kind(self) -> None:
        started = {
            "kind": "STARTED",
            "task_id": "TASK-1",
            "owner": "worker",
            "paths": ["src/a"],
            "baseline": "abc1234",
        }
        blocked = {
            "kind": "BLOCKED",
            "task_id": "TASK-1",
            "blocker_code": "conflict",
            "observed": "conflict evidence",
            "scope_impact": ["TASK-1"],
            "safe_default": "pause_task",
            "recommended_disposition": "re-evaluate",
        }
        result = compact_messages([started, started, blocked, blocked])
        self.assertEqual(len(result), 2)
        self.assertEqual([item["kind"] for item in result], ["STARTED", "BLOCKED"])

    def test_candidate_ready_and_integrated_contracts_are_independently_valid(self) -> None:
        candidate = validate_message(
            {
                "kind": "CANDIDATE_READY",
                "candidate_id": "C-1",
                "commit": "a" * 40,
                "paths": ["src/a"],
                "checks": ["unit tests passed"],
            }
        )
        integrated = validate_message(
            {
                "kind": "INTEGRATED",
                "main_hash": "b" * 40,
                "candidate_status": "integrated",
                "remaining_work": [],
            }
        )
        self.assertEqual(candidate["candidate_id"], "C-1")
        self.assertEqual(integrated["remaining_work"], [])


if __name__ == "__main__":
    unittest.main()
