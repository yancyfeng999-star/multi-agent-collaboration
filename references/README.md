# References 导航

本目录保存协议的规范性说明。执行时先读 [SKILL.md](../SKILL.md)，先选择 Direct 或 Coordinated，再按当前 transport、governance 和任务类型选择必要文档，不需要一次加载全部内容。Direct 默认不创建治理资料，通常无需读取 Run/存储细节。

| 文档 | 何时读取 | 主要内容 |
| --- | --- | --- |
| [agent-catalog.md](agent-catalog.md) | 用户选择 Agent 或项目需要展示角色 | 角色卡字段、人工启动表单、稳定身份真源、默认边界和高级治理切换 |
| [interview-and-planning.md](interview-and-planning.md) | 每次新 Run | 访谈、只读扫描、最小智能体编制、DAG 和用户确认 |
| [document-protocol.md](document-protocol.md) | 初始化或恢复 Run | 协议 v3 目录、状态机、事件、不可变证据和恢复 |
| [modes-and-gates.md](modes-and-gates.md) | 选择治理模式或快车道 | Light、Standard、Strict、execution profile、dispatch policy、claim 和发布门禁 |
| [version-governance.md](version-governance.md) | 判断项目交付版本 | 集中式版本合同、Release Train、RC 和版本重评 |
| [adapters.md](adapters.md) | 选择 transport | Codex 原生、文档总线和 hybrid 双写 |
| [codex-native-protocol.md](codex-native-protocol.md) | 使用 Codex 任务或子代理 | 原生工具发现、消息、等待、handoff 和恢复 |
| [document-subagent-protocol.md](document-subagent-protocol.md) | 通用智能体需要受管子代理 | 父子权限、binding、结果审查和关闭 |
| [storage-protocol.md](storage-protocol.md) | 初始化 Coordinated Agent/Run 或迁移 legacy 资料 | 外部 Governance Home、binding、写入所有权与安全迁移 |
| [checkpoint-protocol.md](checkpoint-protocol.md) | 上下文压缩 | 三层上下文模型、检查点格式、触发条件、压缩规则 |
| [cross-platform-resume.md](cross-platform-resume.md) | 跨平台恢复 | 恢复流程、漂移处理、平台适配器、最小恢复提示词 |
| [run-memory-bridge.md](run-memory-bridge.md) | Run 完成后沉淀长期记忆 | Run→Agent 只读桥接、哈希、幂等与边界 |
| [project-finalization.md](project-finalization.md) | 阶段 checkpoint 或项目收口 | 项目级 checkpoint、最终报告与审计清单 |
| [agent-lifecycle.md](agent-lifecycle.md) | 长期角色变化或存储升级 | 添加、暂停、退役、修复、迁移和回滚 |
| [coordinator-runtime.md](coordinator-runtime.md) | 自动推进 ready wave | 有界 tick、唤醒适配、超时建议与安全回退 |
| [runtime-metadata.md](runtime-metadata.md) | 绑定会话或记录运行资料 | actual/declared 证据边界、unknown/conflict、usage、Secret 与收口顺序 |

维护约定：

- 规范性行为变化必须同步脚本、模板、测试、`SKILL.md` 和根 `README.md`。
- 新规则优先加入现有文档；只有形成独立治理边界时才新增 reference。
- 文档中的事件、字段和 CLI 参数必须与当前脚本实际实现一致。
- v1/v2 仅作为迁移历史描述；当前可写协议是 v3。
