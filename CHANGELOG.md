# Change Log

本文件记录 Skill 协议和用户可见行为变化。项目业务版本由各 Run 的版本合同治理，不在
这里记录。

## Skill 1.3.0 — 2026-08-09

### 用户入口与 Agent 目录

- 新增 `agents.html` 作为默认用户入口，展示最小必要 Agent 的稳定角色、能力、适用场景和
  禁用边界。
- 支持用户填写项目根目录、目标、允许修改范围和验收标准后，手动复制单 Agent 启动指令。
- 明确目录不读取 Run、不显示当前任务或运行状态、不自动创建线程/Agent，也不自动编排。
- 将 Protocol v3 的并行、交接、审计、高风险和版本治理保留为用户明确选择的高级模式。

### 角色合同与版本边界

- 补充项目 Agent 目录的角色合同说明，稳定身份继续以 `TEAM.yaml`、`AGENT_PROFILE.json`
  和 `ROLE.md` 为真源。
- `AGENT_PROFILE.json` 增加无运行状态的 `catalog` 投影 Schema；初始化和新增 Agent 时提供
  角色使命、能力、适用/禁用场景及启动骨架默认值。
- Skill 版本升级为 `1.3.0`；Protocol 版本仍为 `3`，两者独立治理。

## Skill 1.2.0 — 2026-08-06

### 运行资料操作规范

- 明确运行资料采用自动探测优先、缺失字段再显式补充，不扫描或快照全量环境。
- 明确 `observed_actual` 与 `declared_default` 的证据边界；declared 不得覆盖或冒充 actual。
- 规范 `unknown`、`not_collected`、`conflict` 的使用，禁止字符串占位、静默冲突裁决和伪造完整值。
- Token/费用只接受带来源和 hash 的 provider/runtime/billing 回执；无真实数据时不估算。
- 补充 secret 禁区、双重敏感信息扫描和错误/冲突不回显原值要求。
- 将长期项目收口顺序明确为 `Bridge → PCP → index → validator → finalize`。
- 明确 Hermes/Codex 远程 adapter 依赖显式 bridge 与真实 active session 映射；bridge 成功不等于 ACK、执行或完成，缺失时回退 document package。

### 长期协作闭环

- 新增 Run→长期 Agent 的只读、哈希绑定和幂等桥接。
- 新增项目级不可变 checkpoint、最终收口报告、审计 manifest 与 artifact index。
- 新增长期 Agent 生命周期管理、半初始化修复和事务式存储迁移。
- 新增有界协调器 tick、真实 document invocation package、Hermes/Codex 显式桥接与安全回退。

### 安全与完整性

- 强化 Bearer/JWT/AWS/数据库 URI/URL query/Slack/Google/Cookie 等敏感信息脱敏和 fail-closed 扫描。
- 会话同步支持稳定消息 ID 的增量、去重、连续性、缺口审计和游标原子更新。
- archive、Agent checkpoint 与项目 checkpoint 绑定来源哈希；并发写入使用跨平台锁。
- 长期验证器执行 Schema 子集、任务 DAG/owner/写入冲突、handoff/evidence/artifact 和索引门禁。
- 索引改为确定性重建；恢复包可检测 Git、路径、引用和哈希漂移。

### 质量

- 新增安全、增量同步、checkpoint、索引、恢复、桥接、项目收口、生命周期、协调器和 Skill 契约回归测试。
- 最终独立审查补强协调器投递顺序、项目 checkpoint 回滚、最终审计来源漂移与完整索引校验。
- 修复 Run→长期层桥接 Schema 不兼容、部分初始化误报成功、跨 Agent checkpoint 污染、同步路径标识符穿越和多 Run 审批串扰。
- `validate_agents.py` 现验证项目 PCP Schema/正文/来源哈希/链/最新指针；finalize 强制 bridge → PCP → rebuild index 闭环。
- 独立最终审查发现并修复两个 P1：Session Map 发布失败后遗留孤立 Runtime Profile；Hermes declared default 被错误提升为 observed actual。
- Runtime Profile、索引、当前指针与 Session Map 现共享事务回滚边界；注入 Session Map 写失败后调用前字节状态完整恢复，重试复用原 Runtime 序号。
- config-only 会话绑定不再发布高置信 actual；缺少可信 model/provider 时返回结构化 `RUNTIME_METADATA_REQUIRED`。
- 最终本机回归：两轮 `pytest` 均为 `197 passed, 79 subtests`，`unittest` 为 `197 tests OK`，24/24 CLI help、8/8 Schema、compileall 和 `git diff --check` 全部通过。

## Skill 1.1.0 — 2026-08-06

### Agent 身份持久化

- 新增 Agent 目录结构（`agents/<id>/`），包含 ROLE.md、SYSTEM_PROMPT.md、conversations/ 等
- 新增 TEAM.yaml 团队清单
- 新增 PROTOCOL.md 协同协议说明
- 新增 CURRENT_PROJECT_CONTEXT.md 项目级上下文
- 新增 DECISIONS.md 决策记录
- 新增 INDEX.md 项目级索引

### 对话归档与检查点

- 新增三层上下文模型：完整原文、历史检查点、当前上下文
- 新增 CHECKPOINT.md 检查点模板
- 新增 HANDOFF.md 标准化交接模板
- 新增 SESSION_MAP.json 平台会话映射
- 新增 checkpoint-protocol.md 检查点协议

### 跨平台恢复

- 新增 cross-platform-resume.md 跨平台恢复协议
- 新增恢复流程、漂移处理、平台适配器
- 新增最小恢复提示词

### 存储协议

- 新增 storage-protocol.md 存储协议
- 新增目录结构、文件职责、写入所有权、索引策略

### 新增脚本

- `init_project_agents.py` - 初始化项目 Agent 结构和可移植 schemas/templates
- `bind_session.py` - 绑定平台会话到 Agent
- `sync_conversation.py` - 默认脱敏并归档平台可见对话
- `create_checkpoint.py` - 创建链式不可变上下文检查点
- `rebuild_index.py` - 重建项目与 Agent 索引
- `resume_brief.py` - 生成跨平台最小恢复包
- `validate_agents.py` - fail-closed 验证持久化结构、引用和敏感信息

### 新增模板

- Agent 模板：ROLE.md、SYSTEM_PROMPT.md、CHECKLIST.md
- 对话模板：CHECKPOINT.md、HANDOFF.md、CURRENT_CONTEXT.md、SESSION_MAP.json
- 项目模板：TEAM.yaml、PROTOCOL.md、CURRENT_PROJECT_CONTEXT.md、DECISIONS.md

### 新增 Schema

- task.schema.json - 任务定义 Schema
- handoff.schema.json - 交接文档 Schema
- checkpoint.schema.json - 检查点 Schema
- session-map.schema.json - 会话映射 Schema

## Skill 1.0.0 — 2026-08-03

首个正式 Skill 版本。版本权威源为根目录 `VERSION`，Skill 版本与文档通信协议版本独立
演进。本版本包含稳定的 Protocol v3 实现。

### Protocol v3

- 中文名统一为"多智能体协同"，英文名统一为 "Multi-Agent Collaboration"。
- Skill ID 和调用名统一为 `multi-agent-collaboration`。
- 协同数据目录统一为 `.multi-agent-collaboration/`。
- 增加显式 `tracked` / `not_applicable` 项目版本判断。
- 增加集中式版本合同、Release Train、RC 和版本源漂移校验。
- 项目版本治理归 Coordinator，不增加 Version Agent。
- 引入"最小必要智能体"：Reviewer 与 QA 可合并，未参与实现的 Coordinator 可兼任；
  Standard / Strict 继续禁止 Owner 自审。
- 修正子代理返工规则和 `QA_PASSED` 下游路由。
- 增加协议模板、发布校验和完整回归测试。
- 以 MIT License 开源发布，并补充公开安装与贡献说明。

#### 兼容性

v1/v2 Run 保留为只读历史，不自动补写 v3 的版本事实。需要继续执行时，应在用户确认后
创建 v3 后继 Run，不得原地伪造或迁移历史事件。
