# References 导航

本目录保存协议的规范性说明。执行时先读 [SKILL.md](../SKILL.md)，再按当前 transport、
governance 和任务类型选择必要文档，不需要一次加载全部内容。

| 文档 | 何时读取 | 主要内容 |
| --- | --- | --- |
| [interview-and-planning.md](interview-and-planning.md) | 每次新 Run | 访谈、只读扫描、最小智能体编制、DAG 和用户确认 |
| [document-protocol.md](document-protocol.md) | 初始化或恢复 Run | 协议 v3 目录、状态机、事件、不可变证据和恢复 |
| [modes-and-gates.md](modes-and-gates.md) | 选择治理模式 | Light、Standard、Strict、人工门禁和发布门禁 |
| [version-governance.md](version-governance.md) | 判断项目交付版本 | 集中式版本合同、Release Train、RC 和版本重评 |
| [adapters.md](adapters.md) | 选择 transport | Codex 原生、文档总线和 hybrid 双写 |
| [codex-native-protocol.md](codex-native-protocol.md) | 使用 Codex 任务或子代理 | 原生工具发现、消息、等待、handoff 和恢复 |
| [document-subagent-protocol.md](document-subagent-protocol.md) | 通用智能体需要受管子代理 | 父子权限、binding、结果审查和关闭 |

维护约定：

- 规范性行为变化必须同步脚本、模板、测试、`SKILL.md` 和根 `README.md`。
- 新规则优先加入现有文档；只有形成独立治理边界时才新增 reference。
- 文档中的事件、字段和 CLI 参数必须与当前脚本实际实现一致。
- v1/v2 仅作为迁移历史描述；当前可写协议是 v3。
