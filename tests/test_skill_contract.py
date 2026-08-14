from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class SkillContractTests(unittest.TestCase):
    def test_description_is_trigger_only_and_routing_table_exists(self) -> None:
        text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        match = re.match(r"---\n(?P<frontmatter>.*?)\n---\n", text, re.DOTALL)
        if match is None:
            self.fail("SKILL.md frontmatter is missing")
        frontmatter = match.group("frontmatter")
        description = next(
            line.split(":", 1)[1].strip()
            for line in frontmatter.splitlines()
            if line.startswith("description:")
        )
        self.assertTrue(description.startswith("Use when"))
        self.assertLessEqual(len(description), 500)
        self.assertIn("## 能力路由", text)
        self.assertIn("长期 Agent 层", text)
        self.assertIn("Protocol v3 Run", text)
        self.assertIn("长期层 + Run 层", text)

    def test_direct_and_coordinated_storage_boundary_is_explicit(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for text in (skill, readme):
            self.assertIn("Direct", text)
            self.assertIn("Coordinated", text)
            self.assertIn("Governance Home", text)
            self.assertIn("默认不创建治理资料", text)
            self.assertIn("不自动创建或修改目标项目的 `AGENTS.md`", text)
            self.assertIn("网站构建、启动、测试、部署和线上运行对治理资料零依赖", text)
        self.assertLessEqual(len(skill.splitlines()), 500)
        self.assertNotIn("协同数据目录 | `.multi-agent-collaboration/`", readme)

    def test_generic_router_requires_evidence_to_upgrade_direct(self) -> None:
        skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        modes = (ROOT / "references" / "modes-and-gates.md").read_text(encoding="utf-8")
        catalog = (ROOT / "references" / "agent-catalog.md").read_text(encoding="utf-8")
        for text in (skill, modes):
            self.assertIn("Direct 是默认路径", text)
            self.assertIn("升级必须有结构化证据", text)
            self.assertIn("Reviewed", text)
            self.assertIn("Integration Policy", text)
        self.assertIn("Integration Owner", catalog)
        self.assertIn("不新增长期角色", catalog)
        self.assertNotIn("A12", skill)


if __name__ == "__main__":
    unittest.main()
