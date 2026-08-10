from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class AgentCatalogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = (ROOT / "agents.html").read_text(encoding="utf-8")
        cls.skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
        cls.readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_catalog_has_manual_start_form_for_minimum_project_context(self) -> None:
        for marker in (
            "多智能体协同",
            "Multi-Agent Collaboration",
            'id="launch-dialog"',
            'id="project-root"',
            'id="objective"',
            'id="allowed-scope"',
            'id="acceptance"',
            "生成启动指令",
            "projectRoot",
            "acceptance",
        ):
            self.assertIn(marker, self.html)

    def test_catalog_explicitly_avoids_live_state_and_orchestration(self) -> None:
        self.assertIn("不读取项目 Run", self.html)
        self.assertIn("不自动分配任务", self.html)
        self.assertNotIn("current_task", self.html)
        self.assertNotIn("task_states", self.html)
        self.assertNotIn("并行槽位", self.html)
        self.assertIn("Direct", self.html)
        self.assertIn("Governance Home", self.html)
        self.assertIn("不写入目标项目", self.html)

    def test_skill_and_readme_define_manual_catalog_as_default(self) -> None:
        for text in (self.skill, self.readme):
            self.assertIn("Agent 目录", text)
            self.assertIn("人工启动", text)
            self.assertIn("高级治理", text)

    def test_docs_explain_independent_skill_protocol_and_project_versions(self) -> None:
        for text in (self.skill, self.readme):
            self.assertIn("版本边界", text)
            self.assertIn("Skill 版本", text)
            self.assertIn("Protocol 版本", text)
            self.assertIn("项目业务版本", text)
        self.assertIn("唯一版本权威源", self.skill)
        self.assertIn("1.4.1", self.readme)


if __name__ == "__main__":
    unittest.main()
