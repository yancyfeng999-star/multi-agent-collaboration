from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.protocol_lib import ProtocolError


class GovernancePathsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.project = self.root / "website"
        self.project.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def load_module(self):
        from scripts import governance_paths

        return governance_paths

    def test_rejects_governance_root_equal_to_or_inside_project(self) -> None:
        module = self.load_module()
        for governance_root in (self.project, self.project / ".governance"):
            with self.subTest(governance_root=governance_root):
                with self.assertRaisesRegex(ProtocolError, "outside the target project"):
                    module.resolve_governance_project(
                        self.project,
                        "website",
                        governance_root,
                        require_existing=False,
                    )

    def test_resolves_stable_external_project_paths_without_writing_project(self) -> None:
        module = self.load_module()
        governance_root = self.root / "governance"
        before = sorted(path.relative_to(self.project) for path in self.project.rglob("*"))

        paths = module.resolve_governance_project(
            self.project,
            "Website Alpha",
            governance_root,
            require_existing=False,
        )

        self.assertEqual(paths.project_root, self.project.resolve())
        self.assertEqual(paths.governance_root, governance_root.resolve())
        self.assertEqual(paths.project_id, "Website Alpha")
        self.assertEqual(paths.project_key, "website-alpha")
        self.assertEqual(paths.project_dir, governance_root.resolve() / "projects/website-alpha")
        self.assertEqual(paths.agents_dir, paths.project_dir / "agents")
        self.assertEqual(paths.runs_dir, paths.project_dir / "runs")
        self.assertEqual(before, sorted(path.relative_to(self.project) for path in self.project.rglob("*")))

    def test_writes_and_loads_project_binding_in_governance_home(self) -> None:
        module = self.load_module()
        governance_root = self.root / "governance"
        paths = module.resolve_governance_project(
            self.project,
            "website",
            governance_root,
            require_existing=False,
        )

        binding_path = module.write_project_binding(paths, "Website")
        binding = module.load_project_binding(paths.project_dir)

        self.assertEqual(binding_path, paths.project_dir / "project-binding.yaml")
        self.assertEqual(binding["storage_schema"], "1.0")
        self.assertEqual(binding["project_id"], "website")
        self.assertEqual(binding["project_name"], "Website")
        self.assertEqual(binding["project_root"], str(self.project.resolve()))
        self.assertEqual(binding["allowed_roots"], [str(self.project.resolve())])
        self.assertFalse((self.project / ".multi-agent-collaboration").exists())
        self.assertFalse((self.project / "AGENTS.md").exists())

    def test_project_binding_schema_is_valid_json(self) -> None:
        schema = Path(__file__).parents[1] / "assets/schemas/project-binding.schema.json"
        parsed = json.loads(schema.read_text(encoding="utf-8"))
        self.assertEqual(parsed["properties"]["storage_schema"]["const"], "1.0")

    def test_legacy_project_store_requires_explicit_read_only_opt_in(self) -> None:
        import sys

        scripts = Path(__file__).parents[1] / "scripts"
        sys.path.insert(0, str(scripts))
        self.addCleanup(lambda: sys.path.remove(str(scripts)))
        from project_memory_lib import bus_root

        legacy = self.project / ".multi-agent-collaboration"
        legacy.mkdir()
        with self.assertRaisesRegex(Exception, "external governance binding"):
            bus_root(self.project)
        self.assertEqual(bus_root(self.project, allow_legacy=True), legacy.resolve())


if __name__ == "__main__":
    unittest.main()
