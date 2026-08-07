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


if __name__ == "__main__":
    unittest.main()
