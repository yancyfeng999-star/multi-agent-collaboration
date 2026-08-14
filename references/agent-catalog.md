# Agent 目录与人工启动

## 定位

`agents.html` 是面向用户的 Agent 角色目录和 Direct 启动入口。它帮助用户理解角色、选择角色、
填写最小项目上下文并复制启动指令；它不是 Run 控制台，不显示当前任务、运行状态、并行槽位
或任务图，也不自动创建线程、增加 Agent 或调度下游。

页面不是事实源，也不写入目标项目或 Governance Home。Coordinated 中的稳定身份、角色边界和历史以外部 Governance Home 的 `TEAM.yaml`、
`agents/<agent-id>/AGENT_PROFILE.json` 与 `ROLE.md` 为准。
使用 `init_project_agents.py` 或 `manage_project_agents.py add` 初始化时，会为 Profile 写入
无运行状态的目录投影；项目负责人应在首次使用前把通用默认值校准为项目事实。

## 默认用户流程

```text
打开 agents.html
→ 选择角色
→ 填写项目、目标、范围和验收标准
→ 复制启动指令
→ 用户自行粘贴到 Codex 并决定是否继续
```

页面启动不等于 Agent 已经运行。启动指令被复制后，执行者仍必须读取项目内约束，确认真实
范围，并按用户授权执行。

## 角色卡字段

每张通用或项目专属角色卡应说明：

- `agent_id`：稳定身份或通用角色标识；不能把示例 ID 当成项目现状。
- `display_name`：用户可读名称。
- `role`：职责名称。
- `summary`：一句话长期使命。
- `capabilities`：能力标签；能力不等于新增 Agent。
- `suitable_for`：适合的目标或问题。
- `avoid_when`：不适合使用的场景。
- `launch_prompt`：只包含角色边界和安全约束的启动骨架。
- `tier`：`core`、`optional` 或项目自定义的 `custom`。

Profile 的 `catalog` 对象由 `assets/schemas/agent-profile.schema.json` 校验。它只描述稳定
角色，不得写入当前任务、Run、会话、模型、Provider、lease 或任何运行状态。

不能把当前任务、当前 Run、会话 ID、模型、Provider、lease 或临时状态写进默认角色卡。

本版本的可选协同能力也写在 `capabilities` 中，而不是新增角色：

- `task_publish`：在父任务和冻结 scope 内发布子任务。
- `task_claim`：抢占 `owner_agent: pool` 且自己在 `eligible_agents` 中的任务。
- `thread_claim`：在精确 workspace 内串行抢占一个 Codex/Hermes/document thread。

这些是运行时权限，不是页面状态。`agents.html` 只解释“谁适合做什么”和人工启动入口，
不显示某个项目当前有多少 claim、线程或活跃任务。

## 启动表单的最小上下文

页面收集：

1. 项目根目录或 projectless 的 `coordination/output` 目录，可为空。
2. 用户目标，必填。
3. 允许修改范围，可写“只读”。
4. 验收标准，可留空，由 Agent 继续询问。

启动骨架必须默认声明：

- 先只读读取项目内指令和约束。
- 不自动创建线程或任务图。
- 不自动增加 Agent。
- 不扩大用户给出的写入范围。
- Direct 不创建 Agent、Run、handoff、candidate、checkpoint 或任何治理资料。
- 发布、生产、数据库、密钥、删除和回滚需要再次明确授权。
- 结果必须报告真实修改、验证、风险和未完成项。

## 项目专属目录

Coordinated Governance Home 有 `TEAM.yaml` 时，可以将稳定角色字段投影到临时用户视图。投影只包含身份、
职责、能力和调用说明，不包含执行状态。Governance Home 没有长期 Agent 资料时，继续使用通用角色卡，
不得凭空生成项目 Agent 数量或职责。

## 紧急任务路由

- 单一、低风险、可逆的紧急 Bug：使用 Direct Hotfix，当前 Agent 直接读取、修复和定向验证，不创建 Run。
- 两个以上真正独立的紧急任务：用户确认后使用 Coordinated Emergency；任务按冲突指纹分流，同一稳定角色可以在不同任务上拥有多个 Run 内短期 `executor_id`。
- executor 不是新的长期 Agent，不进入 TEAM、Profile 或此页面；它只绑定一个 task attempt、一个 workspace 和一段 lease，结束时追加 release/expiry 记录。
- 共享文件、数据库 schema、版本源、生产环境或 release lane 的任务必须串行；并行额度只由 Run 的 `max_parallel` 和已授权的 `executor_scale_authorized` 控制。

## 何时进入高级治理

用户明确要求以下任一项时，才从人工启动模式切换到 Protocol v3 Run 或长期 Agent 层：

- 多 Agent 并行或正式任务接力。
- 跨会话、跨平台恢复。
- 代码交付需要 Review、QA、证据或变更记录。
- 生产、数据库、资金、权限、密钥、删除、部署或发布。
- 需要版本合同、Release Train、回滚或审计闭环。

切换后仍遵循 `SKILL.md` 的最小 Agent 原则；页面本身不负责调度。

## Integration Owner 能力

Integration Owner 是 Coordinator 可按需承担的单写入能力，不新增长期角色。它只消费已验证
候选，依据项目 Integration Policy 串行推进 working/canonical branch，并记录精确 commit
可达性。普通 Owner 可以并行产生互不冲突的候选，但不能竞争移动 canonical branch。

Release 仍是独立的真实发布能力；Integration Owner 不因此获得生产、凭据或部署权限。

## 版本边界

- 角色目录、启动表单和用户入口变化属于 Skill 用户可见变化，应更新 Skill 版本和
  `CHANGELOG.md`。
- 任务、事件、状态机、锁、证据或 Run 语义变化才需要重新评估 Protocol 版本。
- 角色页面变化不会自动增加目标项目的业务版本。
- `TEAM.yaml`、Agent Profile、Run Registry 和实际项目文件的冲突，不能由页面静默裁决；
  进入高级治理时应按事实优先级处理并记录冲突。
