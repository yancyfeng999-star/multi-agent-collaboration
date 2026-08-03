# Change Log

本文件记录 Skill 协议和用户可见行为变化。项目业务版本由各 Run 的版本合同治理，不在
这里记录。

## Skill 1.0.0 — 2026-08-03

首个正式 Skill 版本。版本权威源为根目录 `VERSION`，Skill 版本与文档通信协议版本独立
演进。本版本包含稳定的 Protocol v3 实现。

### Protocol v3

- 中文名统一为“多智能体协同”，英文名统一为 “Multi-Agent Collaboration”。
- Skill ID 和调用名统一为 `multi-agent-collaboration`。
- 协同数据目录统一为 `.multi-agent-collaboration/`。
- 增加显式 `tracked` / `not_applicable` 项目版本判断。
- 增加集中式版本合同、Release Train、RC 和版本源漂移校验。
- 项目版本治理归 Coordinator，不增加 Version Agent。
- 引入“最小必要智能体”：Reviewer 与 QA 可合并，未参与实现的 Coordinator 可兼任；
  Standard / Strict 继续禁止 Owner 自审。
- 修正子代理返工规则和 `QA_PASSED` 下游路由。
- 增加协议模板、发布校验和完整回归测试。
- 以 MIT License 开源发布，并补充公开安装与贡献说明。

#### 兼容性

v1/v2 Run 保留为只读历史，不自动补写 v3 的版本事实。需要继续执行时，应在用户确认后
创建 v3 后继 Run，不得原地伪造或迁移历史事件。
