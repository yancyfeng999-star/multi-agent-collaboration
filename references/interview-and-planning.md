# 访谈、扫描和任务规划

## 目录

1. 访谈原则
2. 项目扫描
3. 角色设计
4. 任务图
5. 并行与串行
6. 用户确认

## 1. 访谈原则

从用户已提供的信息中预填答案，只问缺失项。一次优先询问 1–3 个高价值问题，避免把所有字段变成问卷。用户说“紧急修 Bug”时，先判断是否单一、低风险、可逆：是则直接走 Direct Hotfix，不创建治理资料；只有多个独立工作单元才提出 Coordinated Emergency。

必须明确：

- 项目根目录和是否允许访问；projectless 任务使用用户指定的 coordination/output 目录。
- coordination mode：默认 `direct`；只在有多 Agent、受管交接、独立质量、恢复或审计需求时选择 `coordinated`。
- 目标、非目标、交付物和完成定义。
- 只读、规划、实施、测试或发布边界。
- 禁止的路径、数据、远端和命令。
- 必须通过的测试或人工验收。
- Coordinated 才需要选择 `light`、`standard`、`strict`。
- execution profile：单一紧急 Run 用 `emergency`（任务级门禁），普通时效优先用 `fast`，完整证据链用 `normal`（Strict 不能 fast，但可以 emergency）。
- dispatch policy：`central`、`hybrid` 或 `self_service`，以及哪些现有 Agent 具备
  `task_publish`、`task_claim`、`thread_claim`。
- 最大并行数量。
- 若使用 Emergency capability pool，是否一次授权 `executor_scale_authorized`，以及每个任务的
  `role_ref`、`required_capabilities`、workspace/worktree 和 `workspace_policy`。
- 是否明确授权创建 Codex 线程。
- 是否存在通用智能体，以及由谁触发它读取文档。
- 通用智能体是否会使用子代理，以及透明子代理还是需要正式追踪的受管子代理。

工作 Agent 自助发布或抢占不等于创建线程。只要使用已有 Agent/runtime，并且任务/线程 claim
的 workspace、路径和锁已声明，就不需要为“派发方便”额外增加 Agent；首次创建新的 Codex
线程仍需要用户明确确认。

用户没有明确授权时，不创建线程、不修改项目、不运行发布。

Direct 不询问与当前任务无关的完整治理问卷，不创建 Agent、Run、handoff、candidate 或 checkpoint。Coordinated 启用前必须确认 Governance Home 在项目外。

## 2. 项目扫描

优先读取目标项目自己的指令和已有标准，不从当前打开的其他项目继承任何路径、角色或
发布规则，也不从框架经验猜项目结构。

扫描：

- Git 根、分支、dirty files、worktree。
- README、AGENTS、CONTRIBUTING、架构、运行和发布文档。
- 语言、框架、包管理器、测试和构建命令。
- source、API、database、migration、worker、queue、storage、deployment。
- 已有 owner、handoff、change registry 和 release gate。
- 项目版本权威源、当前版本、版本规则，以及本次工作是否进入正式交付物。
- 高冲突文件和未提交文件。

输出事实清单，并把不一致标记为：

- 事实已实现。
- 文档先行。
- 代码未实现。
- 迁移中。
- 无法验证。

## 3. 角色设计

角色按任务动态识别，但角色只是能力标签，不自动对应新的 Agent。先把所有职责放入
Coordinator 和现有 Owner，再识别真正不能合并的边界。

默认最小编制：

- Coordinator：架构、Registry、Routing、集成、版本治理和收口。
- 一个或多个 Owner：仅按互不重叠的 owned paths 或真实专业边界拆分。
- Quality：Standard / Strict 中独立于 Owner；默认同时承担 Reviewer 和 QA。Coordinator
  未参与任务实现且具备验证能力时直接兼任，不另建 Agent。
- Release：仅在存在实际发布任务且不能由 Coordinator 安全承担时增加或复用。

Platform、Data、UI、Security、Operations 等能力优先附加给现有智能体。只有满足下列
至少一项才能新增 Agent：

- 权限必须隔离。
- 同一智能体承担两项职责会形成自审或其他利益冲突。
- owned paths 或资源锁要求独立所有权。
- 必须使用不同 runtime、凭据或运行环境。
- 项目规范明确要求独立角色。
- 并行执行能产生可衡量收益，且任务不存在共享高冲突文件。

不得因为功能增多、命名方便、组织图整齐或可以并行就自动拆分 Agent。

对每个最终 Agent 必须定义：

- `agent_id`
- runtime
- parent_agent_id 和 delegation_depth
- owner paths
- forbidden paths
- dependencies
- reviewer
- QA
- handoff_to
- expected outputs
- verification
- risk flags
- 不能继续与哪个现有 Agent 合并，以及原因

## 4. 任务图

每个任务只有一个 Owner。把跨模块需求拆成 DAG，不用“大家一起改”。

任务池可以把声明 Owner 写成 `pool`，但必须列出 `eligible_agents`、共同 writable scope 和
claim lease。claim 前任务保持 ready，claim 后才进入 claimant 的 dispatched/running 生命周期。

示例：

```text
architecture
  ├─ shared-service
  ├─ product-ui
  └─ migration
       ↓
integration
  ↓
security-review
  ↓
qa
  ↓
release-readiness
```

任务必须说明：

- 输入依赖。
- 可开始条件。
- 完成条件。
- 失败退回对象。
- 是否需要人工许可。
- `logical_resources`、`environment_resources` 和 `release_lane`；这些字段决定无关同类型任务
  能否并行，不按角色名称排全局队列。

## 5. 并行与串行

可以并行：

- 不同目录的只读扫描。
- 不重叠 owned paths 的实现。
- 独立测试和文档任务。
- 同一稳定 principal 的不同 executor，只要路径、逻辑资源、环境、worktree 和发布通道均无冲突。

必须串行：

- 同一文件或同一数据库 schema。
- 全局 CSS、产品注册表、包锁、发布账本。
- 同一资金、积分、支付事务。
- migration 与依赖其 schema 的实现。
- release、rollback 和生产数据操作。

存在不明确的重叠时，默认串行；缺少 writer workspace 的新任务视为共享项目根，必须先补充隔离
worktree 才能并行。

自助并不改变串行规则：任务发布、任务 claim、thread claim 各使用独立锁；同一 owned path、
同一 logical resource 或同一 thread 仍不可并发。抢占只是选择唯一执行者，不是获得额外并发量。

## 6. 用户确认

创建线程前展示：

| 线程 | runtime | 任务 | owned paths | 依赖 | 风险 | 验证 |
| --- | --- | --- | --- | --- | --- | --- |

同时说明：

- 并行批次。
- 串行锁。
- 文档通信目录。
- 是否创建 worktree。
- 哪些节点会再次询问用户。

只有用户明确确认后才进入 dispatch。

初始化时使用 `--user-confirmed` 把本次确认写入 Run 内不可变 gate。该 flag 只允许在当前
对话已经获得明确确认后传入，不能由 Coordinator 自行推断。后续扩大范围必须创建新的
human gate，不能改写初始确认。
