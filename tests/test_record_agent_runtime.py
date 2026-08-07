from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(ROOT / "tests"))

from test_runtime_profiles import validate  # noqa: E402


def canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


class RecordAgentRuntimeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name) / "project"
        self.agent = self.project / ".multi-agent-collaboration" / "agents" / "A02-worker"
        self.agent.mkdir(parents=True)

    def command(self, *extra: str, ok: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        command = [
            sys.executable, str(SCRIPTS / "record_agent_runtime.py"),
            "--project-root", str(self.project), "--agent-id", "A02-worker",
            *extra,
        ]
        result = subprocess.run(command, capture_output=True, text=True, env=env)
        if ok and result.returncode:
            self.fail(result.stdout + result.stderr)
        return result

    def observed_args(self, session: str = "session-1") -> tuple[str, ...]:
        return (
            "--model", "gpt-5.6-sol", "--provider", "custom-rootflowgpt",
            "--platform", "hermes", "--session-id", session,
            "--profile", "default", "--workspace", str(self.project),
            "--runtime-kind", "hermes-thread",
        )

    @property
    def runtime(self) -> Path:
        return self.agent / "runtime"

    def load_profile(self, profile_id: str) -> dict:
        return json.loads((self.runtime / "profiles" / f"{profile_id}.json").read_text(encoding="utf-8"))

    def test_cli_publishes_immutable_profile_pointer_index_and_hash_chain(self) -> None:
        first = json.loads(self.command(*self.observed_args()).stdout)
        second = json.loads(self.command(*self.observed_args("session-2")).stdout)
        self.assertEqual((first["runtime_profile_id"], second["runtime_profile_id"]), ("RP-000001", "RP-000002"))

        one = self.load_profile("RP-000001")
        two = self.load_profile("RP-000002")
        schema = json.loads((ROOT / "assets/schemas/runtime-profile.schema.json").read_text(encoding="utf-8"))
        self.assertEqual(validate(one, schema), [])
        self.assertEqual(validate(two, schema), [])
        self.assertEqual(one["previous_profile"], None)
        self.assertEqual(two["previous_profile"], {
            "runtime_profile_id": "RP-000001", "record_hash": one["record_hash"]["value"],
        })
        for profile in (one, two):
            unhashed = dict(profile)
            recorded = unhashed.pop("record_hash")["value"]
            self.assertEqual(recorded, hashlib.sha256(canonical(unhashed).encode()).hexdigest())

        current = json.loads((self.runtime / "CURRENT_RUNTIME.json").read_text(encoding="utf-8"))
        self.assertEqual(current, {
            "runtime_profile_id": "RP-000002",
            "record_hash": two["record_hash"]["value"],
            "path": "profiles/RP-000002.json",
        })
        index = [json.loads(line) for line in (self.runtime / "RUNTIME_INDEX.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertEqual([item["runtime_profile_id"] for item in index], ["RP-000001", "RP-000002"])
        self.assertEqual(index[-1]["record_hash"], two["record_hash"]["value"])
        self.assertEqual(self.load_profile("RP-000001"), one, "a later capture must not mutate history")

    def test_model_provider_unknown_and_conflicting_observations_are_not_guessed(self) -> None:
        unknown = self.load_profile(json.loads(self.command(
            "--platform", "hermes", "--session-id", "s-unknown", "--profile", "default",
            "--workspace", str(self.project), "--runtime-kind", "hermes-thread",
        ).stdout)["runtime_profile_id"])
        for field in ("model", "provider"):
            self.assertEqual(unknown[field]["status"], "unknown")
            self.assertIsNone(unknown[field]["value"])
            self.assertEqual(unknown[field]["unknown_reason_code"], "U001_NOT_EXPOSED")
        self.assertEqual(unknown["capture_status"], {"code": "S001", "name": "partial"})

        environment = os.environ.copy()
        environment.update({"HERMES_MODEL": "different-model", "HERMES_PROVIDER": "different-provider"})
        conflicted = self.load_profile(json.loads(self.command(*self.observed_args("s-conflict"), env=environment).stdout)["runtime_profile_id"])
        for field in ("model", "provider"):
            self.assertEqual(conflicted[field]["status"], "conflict")
            self.assertIsNone(conflicted[field]["value"])
            self.assertEqual(len(conflicted[field]["conflict_candidate_ids"]), 2)
        self.assertEqual(conflicted["capture_status"], {"code": "S002", "name": "conflicted"})

    def test_secret_scan_runs_before_and_after_serialization_without_publishing(self) -> None:
        secret = "sk-abcdefghijklmnopqrstuv"
        rejected = self.command(*self.observed_args(), "--model", secret, ok=False)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertNotIn(secret, rejected.stdout + rejected.stderr)
        self.assertFalse(self.runtime.exists())

    def test_parallel_processes_allocate_continuous_unique_ids(self) -> None:
        def run(index: int) -> subprocess.CompletedProcess[str]:
            return self.command(*self.observed_args(f"parallel-{index}"))

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(run, range(12)))
        ids = sorted(json.loads(result.stdout)["runtime_profile_id"] for result in results)
        self.assertEqual(ids, [f"RP-{index:06d}" for index in range(1, 13)])
        files = sorted(path.stem for path in (self.runtime / "profiles").glob("RP-*.json"))
        self.assertEqual(files, ids)
        index_ids = [json.loads(line)["runtime_profile_id"] for line in (self.runtime / "RUNTIME_INDEX.jsonl").read_text().splitlines()]
        self.assertEqual(index_ids, ids)

    def test_publish_failure_rolls_back_profile_index_and_pointer(self) -> None:
        import record_agent_runtime as module

        first = module.record_agent_runtime(
            project_root=self.project, agent_id="A02-worker",
            observed={
                "model": "model-one", "provider": "provider-one", "platform": "hermes",
                "session": "session-one", "profile": "default", "workspace": str(self.project),
                "runtime_kind": "hermes-thread",
            }, environ={},
        )
        before = {
            path.relative_to(self.runtime).as_posix(): path.read_bytes()
            for path in self.runtime.rglob("*") if path.is_file() and path.name != ".runtime.lock"
        }
        real_replace = module.os.replace
        calls = 0

        def fail_during_publish(source, destination):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected publish failure")
            return real_replace(source, destination)

        with mock.patch.object(module.os, "replace", side_effect=fail_during_publish):
            with self.assertRaises(OSError):
                module.record_agent_runtime(
                    project_root=self.project, agent_id="A02-worker",
                    observed={
                        "model": "model-two", "provider": "provider-two", "platform": "hermes",
                        "session": "session-two", "profile": "default", "workspace": str(self.project),
                        "runtime_kind": "hermes-thread",
                    }, environ={},
                )
        after = {
            path.relative_to(self.runtime).as_posix(): path.read_bytes()
            for path in self.runtime.rglob("*") if path.is_file() and path.name != ".runtime.lock"
        }
        self.assertEqual(after, before)
        self.assertEqual(first["runtime_profile_id"], "RP-000001")
        self.assertFalse((self.runtime / "profiles/RP-000002.json").exists())


if __name__ == "__main__":
    unittest.main()
