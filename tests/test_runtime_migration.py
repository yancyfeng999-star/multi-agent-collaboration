from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

from test_runtime_profiles import validate  # noqa: E402
from tests.governance_test_support import governance_project, governance_root  # noqa: E402


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class RuntimeMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "跨平台 project"
        self.project.mkdir()
        self.governance = governance_root(self.temp.name)
        self.bus = governance_project(self.temp.name, self.project)
        self.agent = self.bus / "agents" / "A02-worker"
        (self.agent / "conversations" / "archive").mkdir(parents=True)
        (self.agent / "checkpoints").mkdir()
        (self.agent / "AGENT_PROFILE.json").write_text(json.dumps({
            "schema_version": "1.0", "agent_id": "A02-worker",
            "declared_model_policy": {
                "preferred_models": ["declared-is-not-observed"],
                "preferred_provider": "declared-provider",
                "runtime_kind": "declared-runtime",
            },
        }), encoding="utf-8")
        (self.agent / "conversations" / "SESSION_MAP.json").write_text(json.dumps({
            "schema_version": "0.9",
            "active": {"platform": "hermes", "session_id": "legacy-session"},
        }), encoding="utf-8")
        self.history = self.agent / "conversations" / "archive" / "old.md"
        self.history.write_text("immutable history\n", encoding="utf-8")
        self.checkpoint = self.agent / "checkpoints" / "CP-0001.md"
        self.checkpoint.write_text("immutable checkpoint\n", encoding="utf-8")

    @property
    def runtime(self) -> Path:
        return self.agent / "runtime"

    def command(self, *extra: str, ok: bool = True, env: dict[str, str] | None = None):
        result = subprocess.run([
            sys.executable, str(SCRIPTS / "migrate_agent_runtime.py"),
            "--project-root", str(self.project), "--governance-root", str(self.governance), *extra,
        ], capture_output=True, text=True, env=env)
        if ok and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def plan(self) -> dict:
        return json.loads(self.command("--dry-run").stdout)

    def apply(self, plan: dict, *, ok: bool = True, env: dict[str, str] | None = None):
        return self.command("--apply", "--plan-hash", plan["plan_hash"], ok=ok, env=env)

    def test_dry_run_is_stable_and_has_zero_project_side_effects(self) -> None:
        before = {p.relative_to(self.project).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.project.rglob("*") if p.is_file()}
        first = self.plan()
        second = self.plan()
        after = {p.relative_to(self.project).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in self.project.rglob("*") if p.is_file()}
        self.assertEqual(first, second)
        self.assertEqual(before, after)
        self.assertEqual(first["classification"], "legacy-recognized")
        self.assertEqual(first["operations"], [{"agent_id": "A02-worker", "action": "create_runtime_profile"}])
        unhashed = dict(first)
        plan_hash = unhashed.pop("plan_hash")
        self.assertEqual(plan_hash, hashlib.sha256(canonical(unhashed).encode()).hexdigest())
        self.assertFalse((self.bus / ".runtime-migration.lock").exists())
        self.assertFalse((self.bus / "migrations").exists())

    def test_apply_uses_bound_plan_and_does_not_invent_unreported_runtime_facts(self) -> None:
        plan = self.plan()
        result = json.loads(self.apply(plan).stdout)
        profile = json.loads((self.runtime / "profiles" / "RP-000001.json").read_text(encoding="utf-8"))
        self.assertEqual(result["status"], "COMMITTED")
        self.assertEqual(profile["capture_status"], {"code": "S004", "name": "legacy_imported"})
        self.assertEqual(profile["platform"]["status"], "known")
        self.assertEqual(profile["platform"]["value"], "hermes")
        self.assertEqual(profile["session"]["value"], "legacy-session")
        for field in ("model", "provider", "profile", "workspace", "runtime_kind"):
            self.assertEqual(profile[field]["status"], "not_collected")
            self.assertIsNone(profile[field]["value"])
            self.assertEqual(profile[field]["unknown_reason_code"], "U008_LEGACY_NOT_COLLECTED")
        serialized = json.dumps(profile)
        self.assertNotIn("declared-is-not-observed", serialized)
        self.assertNotIn("declared-provider", serialized)
        self.assertTrue(any(source["claim_kind"] == "legacy_import" for source in profile["sources"]))
        schema = json.loads((ROOT / "assets/schemas/runtime-profile.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(profile, schema), [])
        unhashed = dict(profile)
        record_hash = unhashed.pop("record_hash")["value"]
        self.assertEqual(record_hash, hashlib.sha256(canonical(unhashed).encode()).hexdigest())
        self.assertEqual(self.history.read_text(), "immutable history\n")
        self.assertEqual(self.checkpoint.read_text(), "immutable checkpoint\n")

    def test_source_drift_rejects_apply_and_partial_target_fails_closed(self) -> None:
        plan = self.plan()
        session_map = self.agent / "conversations" / "SESSION_MAP.json"
        session_map.write_text(session_map.read_text() + "\n", encoding="utf-8")
        drifted = self.apply(plan, ok=False)
        self.assertEqual(drifted.returncode, 3)
        self.assertIn("SOURCE_DRIFT", drifted.stderr)
        self.assertFalse(self.runtime.exists())

        (self.runtime / "profiles").mkdir(parents=True)
        (self.runtime / "profiles" / "RP-000001.json").write_text("{}\n")
        partial = self.command("--dry-run", ok=False)
        self.assertEqual(partial.returncode, 2)
        self.assertIn("PARTIAL_TARGET", partial.stderr)

    def test_success_is_idempotent_without_content_or_mtime_changes(self) -> None:
        self.apply(self.plan())
        before = {p.relative_to(self.runtime).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                  for p in self.runtime.rglob("*") if p.is_file()}
        no_op_plan = self.plan()
        self.assertEqual(no_op_plan["classification"], "current")
        self.assertEqual(no_op_plan["operations"], [])
        result = json.loads(self.apply(no_op_plan).stdout)
        after = {p.relative_to(self.runtime).as_posix(): (p.read_bytes(), p.stat().st_mtime_ns)
                 for p in self.runtime.rglob("*") if p.is_file()}
        self.assertEqual(result["status"], "NO_OP_CURRENT")
        self.assertEqual(before, after)

    def test_transaction_rolls_back_all_agents_and_preserves_history(self) -> None:
        second = self.bus / "agents" / "A03-reviewer"
        (second / "conversations").mkdir(parents=True)
        (second / "conversations" / "SESSION_MAP.json").write_text(json.dumps({
            "schema_version": "0.9", "active": {"platform": "codex", "session_id": "s2"},
        }), encoding="utf-8")
        plan = self.plan()
        environment = os.environ.copy()
        environment["RUNTIME_MIGRATION_FAIL_AFTER"] = "after_replace_2"
        failed = self.apply(plan, ok=False, env=environment)
        self.assertEqual(failed.returncode, 4)
        self.assertIn("ROLLED_BACK", failed.stderr)
        self.assertFalse((self.agent / "runtime").exists())
        self.assertFalse((second / "runtime").exists())
        self.assertEqual(self.history.read_text(), "immutable history\n")
        self.assertFalse((self.bus / "migrations").exists())

    def test_rejects_symlink_escape_and_emits_only_posix_relative_paths(self) -> None:
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = self.agent / "runtime"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("symlinks unavailable")
        rejected = self.command("--dry-run", ok=False)
        self.assertEqual(rejected.returncode, 2)
        self.assertIn("PATH_ESCAPE", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
