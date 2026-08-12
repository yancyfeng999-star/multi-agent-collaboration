# Change Log

本文件记录 Skill 协议和用户可见行为变化。项目业务版本由各 Run 的版本合同治理，不在
这里记录。

## Skill 2.1.0 — 2026-08-10

### Emergency 时效与任务级治理

- 新增 Direct Hotfix 与 Coordinated Emergency 路由：单一低风险紧急 Bug 不创建 Run；多个
  独立任务才启用任务级 Preflight。
- Emergency 的普通缺口进入 `blocked_tasks`，容量/冲突等待进入 `deferred_tasks` 或
  `resource_waits`，只有真实 Run 级故障才进入 `run_level_blockers`；旧 Run 缺字段仍按旧的
  run-scoped/fixed 默认行为读取。
- 冲突模型统一覆盖 dependency、owned path、logical resource、workspace、environment 和
  release lane；无冲突的同类型任务不再因同一角色忙碌而排队。

### Run 内短期执行实例

- 增加 `executor_pool.py`、Executor Binding Schema `1.0` 和 `executors/` Run-local 目录；
  `principal_agent_id` 保持稳定权限主体，`executor_id` 只绑定一个 task attempt。
- 同一 principal 可以在独立 worktree 上拥有多个短期 executor；同一任务、写 worktree、
  重叠资源和发布通道仍严格串行。Native 新实例需要 `executor_scale_authorized`，释放采用
  不可变 `executors/releases/` 记录。
- `executor_id` 贯穿 task/thread claim、wake operation、Document invocation package 和
  结构验证；工作 Agent 自助发布固定子任务时也使用相同 pool 和冲突校验。

### 兼容与版本

- Protocol v3 保持不变；Preflight Result Schema 升为 `1.1`；Governance Storage Schema 升为
  `1.1`，兼容读取 `1.0` binding/Run。
- 迁移工具为旧 Run 增补 `preflight_scope=run`、`executor_policy=fixed` 等可选字段，支持
  dry-run/apply/rollback，不自动改变旧 Run 行为。
- Skill 版本升级不修改任何目标项目业务版本；版本合同和发布通道仍由 Coordinator/现有
  Release 能力集中治理，不新增 Version Agent 或长期角色。

## Skill 2.0.0 — 2026-08-10

### 开发治理侧车

- 新增 `Direct` / `Coordinated` 双模式：Direct 为默认且不创建 Run、Agent、handoff、
  candidate index 或任何治理文件；只在用户明确要求多 Agent 协作时进入 Coordinated。
- Coordinated 的默认真源改为项目外 `~/.codex/governance/multi-agent-collaboration/`，
  通过 Storage Schema `1.0` 的项目绑定关联真实项目根目录。
- Agent 角色、Run、session/runtime、archive、checkpoint、handoff、bridge、PCP、
  finalization 和 candidate index 全部外置；不再自动创建或修改目标项目 `AGENTS.md`。
- 网站和应用的构建、启动、测试、部署与线上运行对治理资料零依赖；
  项目内旧治理目录不再被 Git 门禁忽略。

### 迁移与兼容

- 新增 `migrate_governance_storage.py` 的 dry-run/apply 事务迁移：复制前生成清单与
  SHA-256，staging 中逐文件校验后原子发布，源目录不删除、不改写。
- 保留 Protocol v3 与旧项目内资料的只读兼容；Skill 大版本升级不改项目业务
  版本，也不授予发布权限。
- 用户入口 `agents.html` 仍只是静态角色目录与手动启动器，不读 Run、不显示
  运行状态、不自动编排。

## Skill 1.4.1 — 2026-08-10

### 发布一致性补丁

- 统一 `VERSION`、中英文 README、`SKILL.md`、Agent 入口、OpenAI metadata、架构说明和
  测试断言为 Skill `1.4.1`。
- Protocol 仍为 v3；本补丁不改变任务、事件、claim、恢复或项目业务版本语义。
- 保留 1.4.0 的快车道、自助派发、串行任务/线程 claim、范围冻结、超时恢复和候选索引能力，
  旧的固定 Owner v3 Run 继续兼容。

## Skill 1.4.0 — 2026-08-10

### 时效与门禁

- 增加 `execution_profile: fast|normal` 与 `dispatch_policy: central|hybrid|self_service`，
  将“减少等待”与治理强度分开记录；Strict 禁止 fast。
- 增加 `freeze_scope.py`、`preflight_run.py` 和 `completion_preflight.py`，把范围、任务图、
  锁、版本、结果和收口缺口汇总为一次性只读报告，不伪造事件或发布许可。
- 增加 `recover_timeout.py` 和 run-local `retry-policy.yaml`；超时先记录 side-effect state、
  `blocked_by` 与下一动作，禁止无证据自动重试。

### 受控自助协同

- 工作 Agent 获得可审计的 `task_publish` 能力，可在父任务 owned paths/冻结 scope 内发布
  子任务；发布锁保证任务文档与事件串行落盘。
- 增加 `assignment_mode: claimable`、`owner_agent: pool` 和 `eligible_agents`；
  `agent_claim.py` 使用独立 task-claim 锁完成串行抢占，并把有效 Owner 接入 ACK、lease、
  result、事件和唤醒适配器。
- 增加独立 thread claim 锁，绑定 thread、platform、session 线索和精确 workspace；冲突
  返回持有者和下一动作，不覆盖旧 claim。
- Claim 持有者可用 `release-task`/`release-thread` 追加不可变让出记录；释放不会伪造完成或
  自动重置任务，后续仍须按 timeout/recovery 处理。
- 增加共享资源 FIFO 请求、资源步骤与 bundle lock 校验；未取得资源只阻塞对应步骤，已取得
  lock 的步骤可以继续。
- `central`/Strict 仍由 Coordinator 独占派发、人工许可、重试/dead-letter、完成和发布事件；
  不为新能力增加新的 Agent。

### 版本与验证

- Skill 版本升级为 `1.4.0`；Protocol 版本保持 `3`，旧固定 Owner v3 Run 继续兼容。
- 增加 preflight/candidate Schema、候选索引和旧 Run 显式迁移脚本；迁移不改项目业务版本，
  不自动授予发布权限。
- Native/Document wake operation 与 invocation package 统一记录 task、claim、hash、workspace
  和真实 `message_sent`/fallback 事实；验证器同步校验 Scope Freeze、Claim、release 和操作文件。
- 新增快车道、自助发布、任务/线程 claim、范围冻结和完成前检查回归测试。

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
