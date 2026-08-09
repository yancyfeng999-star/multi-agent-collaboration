---
name: multi-agent-collaboration
description: Use when a project needs an Agent role catalog or manual launch, or needs multiple agents, parallel task ownership, cross-session continuity, project-local audit trails, governed review/QA, or recovery across agent platforms.
---

# 多智能体协同（Multi-Agent Collaboration）

- 中文名称：多智能体协同
- 英文名称：Multi-Agent Collaboration
- Skill ID：`multi-agent-collaboration`
- Skill 版本：`1.3.0`（唯一版本权威源：`VERSION`）
- Protocol 版本：`3`
- 调用方式：`$multi-agent-collaboration`

## 全局适用范围

这是安装在用户级 Skill 目录中的通用能力，不属于当前打开的项目。每次调用都重新识别
目标项目、项目级指令和治理规则，不继承上一次项目的角色、路径、服务器、环境名、发布
账本或产品边界。

- 可用于任意代码库、文档项目、研究任务、内容生产、运维计划或 projectless 任务。
- 有项目时，以目标项目自己的 AGENTS、README、架构和发布规范为约束。
- 无项目时，由用户指定一个 coordination/output 目录保存文档总线。
- 项目专属角色和标准只写入该次 run，不写回全局 Skill。

## Skill 与项目的版本边界

三个版本对象必须分开记录，不得用一个版本号代替另一个：

| 对象 | 权威源 | 何时变化 |
| --- | --- | --- |
| **Skill 版本** | 根目录 `VERSION` | 用户入口、文档、脚本、Schema、模板或默认行为发生用户可见变化 |
| **Protocol 版本** | `scripts/protocol_lib.py` 与协议文档 | Run、任务、事件、状态机、证据或恢复语义发生不兼容变化 |
| **项目业务版本** | 目标项目自己的版本文件或 `version-contract` | 目标项目交付范围、兼容性或发布内容发生变化 |

本 Skill 当前为 Skill `1.3.0`、Protocol `3`。更新 Skill 不会自动修改目标项目业务版本；
只有进入 `tracked` Run 并满足目标项目自己的版本规则时，才治理项目业务版本。发布本 Skill
时必须同步 `VERSION`、`CHANGELOG.md`、中英文 README、`SKILL.md`、相关测试和用户入口，
先完成验证，再推送并通过代码审查合并。

## 默认用户入口：Agent 目录与人工启动

用户从 [agents.html](agents.html) 选择一个角色、填写项目根目录/目标/范围/验收标准并复制
启动指令。页面只做角色说明和人工启动，不读取 Run 状态、不显示当前任务、不创建线程、不
自动增加 Agent，也不自动编排任务。

默认启动模式只使用一个用户选定的 Agent：

1. Agent 先只读读取项目内的指令、README、约束和实际状态。
2. Agent 复述目标、范围、验收标准和缺失信息。
3. 只有用户授权的范围才允许修改；projectless 任务使用指定的 coordination/output 目录。
4. 需要多个 Agent、正式证据或高风险操作时，才切换到下方的 Protocol v3 高级治理 Run 或长期 Agent 层。

页面的角色卡不是项目事实源。项目稳定身份和职责以项目自己的 `TEAM.yaml`、
`AGENT_PROFILE.json` 和 `ROLE.md` 为准；页面不能凭空推断项目 Agent 数量或当前状态。
初始化项目 Agent 时，`AGENT_PROFILE.json` 应包含稳定的 `catalog` 投影（使命、能力、适用场景、
禁用场景和启动骨架）；这些字段用于项目专属目录，不得包含运行状态。
完整字段和启动骨架见 [agent-catalog.md](references/agent-catalog.md)。

## 能力路由

先判断任务需要哪一层，不要默认把单 Agent 使用升级为编排，也不要让长期项目绕过 Run 门禁。

| 场景 | 选用能力 | 说明 |
| --- | --- | --- |
| 用户从目录选择一个 Agent，处理单一、明确、低风险目标 | 人工启动模式 | 不创建 Run、线程或任务图；Agent 仍须先读项目并遵守授权边界 |
| 一次性、低风险、独立调查 | 简单并行或单个 Protocol v3 Run | 结束后可归档，不必创建长期身份 |
| 单轮但高风险、需要 Review/QA/证据 | Protocol v3 Run | 使用任务、事件、锁、Review、QA 与收口门禁 |
| 多天、多阶段、稳定角色或跨会话恢复 | 长期 Agent 层 | 建立 TEAM、身份、会话归档、checkpoint 与恢复包 |
| 长期项目中的正式执行波次 | 长期层 + Run 层 | Run 负责执行事实；长期层负责跨 Run 身份、记忆与项目收口 |
| 临时子 Agent | 父 Agent + 必要的 Run 记录 | 临时角色不自动升级为长期 Agent，结果由父 Agent 归档 |

事实分工固定：

- **长期 Agent 层**保存稳定身份、完整原文、历史 checkpoint、当前上下文和跨平台恢复资料。
- **Protocol v3 Run**保存冻结任务、状态事件、ACK/lease、锁、Review、QA、证据和发布门禁。
- **长期层 + Run 层**同时使用时，Run 是执行状态真源；长期层只通过受验证的桥接结果沉淀，不另造第二套任务状态机。
- **人工启动模式**只负责用户选定的单 Agent 使用；它不是第三套持久化状态机。进入正式交付、
  并行或高风险操作后，必须切换到 Run/长期层的事实模型。

## 核心原则

把文档协议作为唯一持久化通信底座，把 Codex 原生线程通信作为实时加速层。

- 每个任务都必须有文档，即使目标是 Codex 线程。
- Codex 可直接收到完整消息并自动运行，不要求先轮询或读取 inbox。
- Codex 的任务、结果、状态和交接仍必须镜像到文档。
- 通用智能体只依赖任务文档和结果文档也必须能完成接力。
- 通用智能体内部透明子代理由父智能体负责；正式受管子代理必须进入文档协议。
- 文档、原生消息和 Git 事实冲突时，以最新有效事件、实际文件和 Git commit 为准。
- 未经用户明确确认，不创建线程、不扩大写入范围、不执行线上或高风险操作。

## 第一步必须询问

从 HTML 启动表单或当前上下文提取已知答案，只询问缺失的高价值信息。单 Agent 人工启动不
要求用户先回答完整治理问卷；以下字段是需要进入正式 Run 或执行写入前的最小确认：

1. 目标项目根目录；无项目时询问 coordination/output 目录。
2. 最终目标和交付物。
3. 允许修改与禁止修改的范围。
4. 验收标准和必须运行的检查。
5. 治理模式：`light`、`standard` 或 `strict`。
6. 项目版本治理判断：`tracked` 或 `not_applicable`，以及判断理由。
7. 若为 `tracked`：版本权威源、当前版本、目标版本和版本规则。
8. 是否明确允许创建多个 Codex 线程。
9. 最大并行线程数，以及是否包含通用智能体（仅在用户明确要求多 Agent 时）。

不要重复询问用户已经明确的信息。用户确认多 Agent 方案之前，只允许只读扫描和规划；
单 Agent 人工启动本身不创建线程，也不触发线程确认门禁。

详细访谈、扫描和任务图规则见 [interview-and-planning.md](references/interview-and-planning.md)。

## 选择通信适配器

文档通信始终启用，再选择加速方式：

| transport | 使用条件 | 执行方式 |
| --- | --- | --- |
| `codex_native` | 所有执行者都是 Codex 线程 | 文档双写 + Codex 原生线程消息 |
| `document_bus` | 只使用通用智能体 | 写 inbox，生成可复制调用命令，读取 outbox |
| `hybrid` | Codex 与通用智能体混合 | 按 Agent Registry 为每个角色选择适配器 |

通用适配规则见 [adapters.md](references/adapters.md)。执行 Codex 原生任务前必须读取
[codex-native-protocol.md](references/codex-native-protocol.md)。

## 选择治理模式

- `light`：研究、方案、低风险文档；保留任务、事件、结果和总结。
- `standard`：代码修改；增加 Git、owned paths、Review、QA 和验证证据。
- `strict`：生产、数据库、资金、权限、密钥、发布；增加变更编号、正式 handoff、registry、安全审查、人工门禁和回滚。

模式字段、必填证据和人工门禁见 [modes-and-gates.md](references/modes-and-gates.md)。

## 项目交付版本治理

版本治理集中在现有角色，不新增独立 Version Agent：

- Coordinator 负责识别版本权威源、冻结版本合同、绑定任务、编号 RC 和触发版本重评。
- 普通 Owner、Reviewer 和 QA 沿用原职责，不各自修改或决定项目版本。
- 只有项目本来需要发布时，现有 Release 角色才负责最终版本落盘和发布；否则由
  Coordinator 收口。

只读分析、调研和不进入正式交付物的草稿可使用 `not_applicable`，但必须写明理由。
代码、数据库、API、配置、构建、部署、发布，以及多个 Agent 汇入同一交付物的工作应使用
`tracked`。Skill 先读取项目已有版本规则和权威源，不擅自发明版本体系。

`tracked` Run 必须冻结基线版本、基线 commit、目标版本、版本源及其 hash，并为所有任务
写入相同 `release_train_id`、`delivery_version` 和版本合同 hash。版本源在 dispatch 前
漂移时 fail-closed。Coordinator 使用 `record-release-candidate` 创建不可变的
`<target>-rc.N`；正式 Release 必须引用真实候选 commit，且版本源已写入目标版本。

任务 attempt、RC 编号和项目正式版本相互独立。返工只增加 attempt；重新集成增加 RC；
只有交付范围或兼容性变化才重新评估目标项目版本。完整规则见
[version-governance.md](references/version-governance.md)。


## Agent 身份持久化

多智能体项目需要长期稳定的 Agent 身份，而不是每次运行都重新定义。

### 核心概念

1. **项目 Agent** - 拥有稳定身份、职责和历史的长期协作角色
2. **临时子 Agent** - 一次性的、边界明确的调查或执行任务
3. **总控 Agent** - 负责维护总目标、拆解任务、分配并行波次

### Agent 目录结构

每个长期 Agent 必须有独立目录：

```text
.multi-agent-collaboration/agents/<agent-id>/
├── ROLE.md                    # 岗位章程
├── SYSTEM_PROMPT.md           # 恢复提示词
├── CHECKLIST.md               # 检查清单
├── conversations/
│   ├── CURRENT_CONTEXT.md     # 当前上下文
│   ├── SESSION_MAP.json       # 平台会话映射
│   ├── INDEX.md               # 对话索引
│   ├── archive/               # 完整对话归档
│   └── checkpoints/           # 压缩检查点
├── tasks/                     # 任务文档
├── handoffs/                  # 交接文档
└── artifacts/                 # 证据产物
```

### 初始化 Agent 结构

```bash
python3 <skill-dir>/scripts/init_project_agents.py \
  --project-root "<project-root>" \
  --project-id "<project-id>" \
  --project-name "<project-name>" \
  --agents "A01-coordinator,A02-frontend,A03-backend" \
  --governance standard \
  --user-confirmed
```

详细规范见 [storage-protocol.md](references/storage-protocol.md)。

### 运行资料采集

长期 Agent 的 model、provider、platform、session、profile、workspace 和 runtime kind 必须
按 [runtime-metadata.md](references/runtime-metadata.md) 记录为可追溯运行资料：

- **自动探测优先，缺失才显式补充**：先读取获准的运行上下文、平台/桥接证据、已验证
  `SESSION_MAP.json` 和固定 allowlist；只有缺字段或冲突需要裁决时，才请求 CLI 参数或人工输入。
- **actual 与 declared 分开**：实际观测值使用 `observed_actual`；项目配置、Registry 或默认模型
  只是 `declared_default`，不能证明本次会话实际使用了该值，也不能覆盖 actual。
- **不编造完整性**：无法确认写 `unknown`；历史版本未采集写 `not_collected`；多个可信 actual
  不一致写 `conflict` 并保留候选来源，禁止静默择一。三者都不能写成字符串 `"unknown"`。
- **Token/费用不估算**：只接受 provider response、runtime meter 或 billing export 的真实回执并
  绑定来源/hash；无回执时保持 `null` 和 `usage_source: unavailable`。
- **secret 禁区**：不读取全量环境快照，不持久化 prompt、原始命令/输出、Authorization、Cookie、
  API key、访问/刷新 token、私钥或带 query 的 URL。命中敏感字段或高置信秘密时 fail-closed，
  错误和冲突记录也不得回显原值。

运行资料快照和 activity 账本是审计证据，不是 Run 状态真源；变更时追加不可变记录，不原地
改写旧记录。

## 对话归档与检查点

### 三层上下文模型

1. **完整原文** - 用于审计和深度恢复，不能被摘要替代
2. **历史检查点** - 上下文压缩后的不可变快照
3. **当前上下文** - 只保留当前有效信息

### 检查点触发条件

满足任一条件时创建新检查点：
- 一个任务完成
- 对话即将进行平台原生压缩
- 切换问题域
- Agent 即将交接给另一个 Agent
- 累计消息或 token 超过配置阈值
- 出现关键架构决策

### 压缩硬规则

- 先同步完整原文，再生成检查点
- 检查点不得覆盖原文
- 新检查点不得覆盖旧检查点
- 当前上下文必须指向最新检查点
- 摘要中的结论必须能回溯到原文或文件证据
- 不能把计划写成已完成
- 不能丢失失败尝试和未解决事项

详细规范见 [checkpoint-protocol.md](references/checkpoint-protocol.md)。

## 跨平台恢复

### 核心原则

- 项目目录是唯一可移植的长期真源
- 平台会话 ID 只是恢复线索，不是长期上下文的唯一来源
- 任何支持读取项目文件的 Agent 都应能恢复工作

### 恢复流程

1. 确认项目根目录
2. 读取 .multi-agent-collaboration/PROTOCOL.md
3. 读取 .multi-agent-collaboration/TEAM.yaml
4. 确认自己的 Agent ID
5. 读取自己的 ROLE.md、SYSTEM_PROMPT.md
6. 读取 conversations/CURRENT_CONTEXT.md
7. 读取最新 checkpoint
8. 读取当前任务及上一次交接
9. 检查实际文件、Git 状态和运行环境
10. 汇报恢复结果后再继续工作

### 漂移处理

若项目文件状态与 checkpoint 不一致：
- 以实际文件和真实系统状态为事实
- 不直接覆盖
- 记录漂移
- 查阅后续原文和 Git 历史
- 交由总控判断是否更新上下文

详细规范见 [cross-platform-resume.md](references/cross-platform-resume.md)。

## 绑定平台会话

```bash
python3 <skill-dir>/scripts/bind_session.py \
  --project-root "<project-root>" \
  --agent-id "A01-coordinator" \
  --platform hermes \
  --session-id "session-xxx" \
  --model "<observed-model>" \
  --provider "<observed-provider>" \
  --profile default
```

`--model` 和 `--provider` 必须来自本次会话的显式输入或可信运行证据。Hermes 配置中的默认值
只属于 declared policy，不能代替 actual；actual 不足或冲突时命令返回
`RUNTIME_METADATA_REQUIRED`，且不会发布 Runtime Profile 或部分 Session 绑定。

## 验证 Agent 结构

```bash
python3 <skill-dir>/scripts/validate_agents.py \
  --project-root "<project-root>"
```

## 长期项目闭环命令

Run 执行完成后严格按 `Bridge → PCP → index → validator → finalize` 沉淀和收口；不得跳步，
也不得把 finalize 当作自动补齐前置资料的命令：

```bash
python3 <skill-dir>/scripts/archive_run_to_agents.py --run-dir "<run-dir>" --agent-map "<run-agent>=<long-term-agent>"
python3 <skill-dir>/scripts/create_project_checkpoint.py --project-root "<project-root>" --run-id "<run-id>"
python3 <skill-dir>/scripts/rebuild_index.py --project-root "<project-root>"
python3 <skill-dir>/scripts/validate_agents.py --project-root "<project-root>"
python3 <skill-dir>/scripts/finalize_project.py --project-root "<project-root>" --run-id "<run-id>"
```

长期 Agent 状态或存储版本变化时使用：

```bash
python3 <skill-dir>/scripts/manage_project_agents.py --help
python3 <skill-dir>/scripts/migrate_project_agents.py --project-root "<project-root>" --dry-run
```

需要自动推进一个有界波次时，运行一次 coordinator tick。Hermes/Codex 远程 adapter 只有在
显式配置外部 CLI/API bridge，且 `SESSION_MAP.json` 同时匹配 Agent、平台、真实 active session
和精确 workspace 时，才允许尝试唤醒；退出码 0 只证明 bridge 命令成功，不证明远端任务已
ACK、运行或完成。未配置、校验失败或投递失败时必须回退到真实 document invocation package，
并报告 `fallback_document`，不能伪报已唤醒：

```bash
python3 <skill-dir>/scripts/coordinator.py --run-dir "<run-dir>" --once
```

协调器只自动派发 ready wave、验证冲突并报告 ACK/lease 超时；Review、QA、失败重试和 release 仍必须由真实 evidence/event 驱动，不能由协调器伪造。`--no-emit-events` 只能与 `--dry-run` 一起用于预览。

详细边界见 [runtime-metadata.md](references/runtime-metadata.md)、[run-memory-bridge.md](references/run-memory-bridge.md)、[project-finalization.md](references/project-finalization.md)、[agent-lifecycle.md](references/agent-lifecycle.md) 和 [coordinator-runtime.md](references/coordinator-runtime.md)。

## 工作流

### 1. 只读扫描

在任何写入前：

- 确认项目根目录、Git 状态和项目级指令。
- 阅读项目已有的 README、AGENTS、架构、协作和发布文档。
- 扫描模块、测试、构建命令、数据库、部署和高冲突文件。
- 识别现有 owner、已有线程和未提交改动。
- 标记事实、计划、未实现和迁移中内容，不把计划写成完成状态。

### 2. 设计角色和任务图

先设计职责，再把不冲突的职责合并到最少的智能体中。角色是能力，不等于必须创建一个新
Agent：

- 每个任务只有一个主 Owner。
- Coordinator 默认同时承担架构、路由、集成、版本治理和最终收口。
- Standard / Strict 保留独立质量检查，但 Reviewer 与 QA 默认由同一个质量智能体承担；
  质量智能体不能同时是该任务 Owner。Coordinator 未参与任务实现且具备验证能力时，可
  直接承担这个质量角色。
- Release、Security、Data、UI 等都是按需能力；现有智能体具备能力且权限不冲突时直接
  合并，不为角色名称单独创建智能体。
- 只有权限隔离、职责冲突、owned paths 冲突、独立运行环境、项目强制独立审查，或确有
  可衡量并行收益时才新增智能体。
- “方便分工”“角色看起来清晰”或“功能增加了”都不是新增智能体的充分理由。
- 明确 reviewer、QA、release、collaborating agents 和 handoff_to。
- 生成有向无环依赖图。
- 不重叠的 owned paths 可并行。
- 同一高冲突文件、schema、全局样式、注册表、锁文件和发布账本必须串行。
- 涉及共享能力时，由共享 owner 处理，产品 Agent 只提出需求。

向用户展示最小智能体编制、每个新增智能体不可合并的理由、目标、owned paths、依赖、
风险、验证和并行批次。等待明确确认。

### 3. 初始化文档通信

用户确认后运行：

```bash
python3 <skill-dir>/scripts/init_run.py \
  --project-root "<project-root>" \
  --governance standard \
  --transport hybrid \
  --objective "<objective>" \
  --max-parallel 4 \
  --max-attempts 3 \
  --ack-timeout-seconds 300 \
  --lease-seconds 1800 \
  --versioning-mode tracked \
  --version-scheme semver \
  --baseline-version "<current-version>" \
  --target-version "<target-version>" \
  --version-source "<version-source-file>" \
  --versioning-reason "<why-this-run-is-versioned>" \
  --user-confirmed
```

默认在项目根目录创建 `.multi-agent-collaboration/`。不得覆盖现有 run。`project.yaml` 只保存项目
身份和固定根范围；每个 Run 都在自己的 `runs/<run-id>/agents.yaml` 保存 Registry，禁止
继承或复用其他 Run 的角色、权限和原生标识。项目已有正式 handoff/registry 时，保留并
通过当前 Run 的引用适配，不另造冲突账本。

并行数、文档子代理深度、ACK 超时、lease 时长和最大尝试数都必须在初始化时明确或采用
可见默认值；初始化器拒绝零值和负值。

协议 v1/v2 的旧 Run 不会被静默复用或自动升级；初始化器会 fail-closed。保留旧 Run
为只读历史，并在明确迁移决策后建立新的 v3 文档总线。

完整目录、状态机、事件和可靠投递规则见 [document-protocol.md](references/document-protocol.md)。

### 4. 建立 Agent Registry 和任务文档

为每个角色登记：

- `agent_id`
- runtime：`codex_thread`、`codex_subagent`、`document` 或 `document_subagent`
- 职责和能力
- 可读/可写/禁止路径
- 父智能体、委派深度和权限继承
- thread id 或 inbox/outbox
- 当前任务、依赖、handoff_to
- 调用模板

优先使用 `scripts/manage_run.py add-agent` 和 `create-task`，避免手工编辑协议文件。
模板见 [agents.yaml.template](assets/agents.yaml.template) 和
[task.md.template](assets/task.md.template)。任务文档必须有唯一 `task_id` 和
`idempotency_key`，冻结后 `status` 永远保持 `draft`；实际状态只由事件重放得出。
ACK、lease、资源锁和死信分别使用 [ack.yaml.template](assets/ack.yaml.template)、
[lease.yaml.template](assets/lease.yaml.template)、
[lock.yaml.template](assets/lock.yaml.template) 和
[dead-letter.yaml.template](assets/dead-letter.yaml.template)。
Codex 原生对象分别使用
[codex-thread-binding.yaml.template](assets/codex-thread-binding.yaml.template)、
[codex-subagent-binding.yaml.template](assets/codex-subagent-binding.yaml.template) 和
[codex-operation.yaml.template](assets/codex-operation.yaml.template)。
通用受管子代理使用
[document-subagent-binding.yaml.template](assets/document-subagent-binding.yaml.template)。
项目交付版本使用
[version-contract.yaml.template](assets/version-contract.yaml.template) 和
[release-candidate.yaml.template](assets/release-candidate.yaml.template)。

### 5. 文档先写，再调度

严格使用以下顺序：

1. 写任务文档。
2. 计算任务内容 SHA-256。
3. 使用 `emit_event.py` 写 `TASK_READY`；payload 必须是该 task id 对应的精确任务文件。
4. 事件工具在同一 sequence 锁内校验转换并由 reducer 重建 `state.yaml`。
5. 写 `TASK_DISPATCHED` 后再通过选定适配器通知目标智能体。

不得先唤醒智能体再补任务文档。

### 6. Codex 原生调度

只有用户明确批准创建线程后才能调用线程创建工具。

先区分 runtime：

- 用户要求独立、后台或侧边栏任务时使用 `codex_thread`。
- 当前任务内有界并行使用 `codex_subagent`，不创建用户拥有的侧边栏任务。
- 两者进入正式 run 时都必须文档双写。

优先流程：

1. 发现当前可用工具及 schema，不凭记忆构造参数。
2. 列出可用项目和任务，选择正确项目并查重复用。
3. 为并行写任务使用独立 worktree；只读任务可使用合适的现有上下文。
4. 创建或复用任务，原样记录 `thread_id`、`pending_id`、host、cursor 和 environment。
5. pending worktree 没有真实 thread id 前不发送消息，也不重复创建。
6. 写 binding 和 operation 文档。
7. 发送完整任务内容、任务路径、hash、禁止项和交付格式。
8. 原生消息成功后由 Coordinator 代理写 ACK；观察运行后写 lease。
9. 使用 wait/read 有界等待，保存 cursor，避免高频轮询。
10. 结果先持久化到 outbox/events/state，再触发下游。
11. handoff、标题、固定和归档都写对应原生事件。

完整工具映射、pending、handoff、恢复和归档规则见
[codex-native-protocol.md](references/codex-native-protocol.md)。

### 7. 通用智能体调度

通用智能体通过文档通信：

1. Coordinator 写目标 inbox。
2. 在 `next-action.md` 生成明确调用命令。
3. 智能体校验 `task_id`、协议版本和 `TASK_READY.payload_sha256`。
4. 智能体写 `ACK`，再执行。
5. 智能体只写自己的 outbox，不改全局 state。
6. Coordinator 读取结果、验证后决定下一步。

没有外部执行器时，不声称通用智能体已自动运行；只提供下一条可复制调用命令。

通用智能体需要子代理时必须先选择：

- **透明子代理**：只作为父智能体内部工具，不进入 Registry，不拥有独立全局任务；父智能体
  对其权限、结果和失败负责。
- **受管子代理**：使用 `document_subagent` runtime，拥有独立 `agent_id`、任务、inbox、
  outbox、ACK、lease、result 和 delegation binding；结果必须先交父智能体审查。

子代理权限只能是父智能体权限的子集，不能绕过父智能体直接调用 Reviewer、QA 或
Release。默认最多一层委派。完整规则见
[document-subagent-protocol.md](references/document-subagent-protocol.md)。

### 8. 事件驱动路由

按事件自动选择目标：

| 事件 | 下一步 |
| --- | --- |
| `HANDOFF_READY` | 调用 Reviewer |
| `CHANGES_REQUESTED` | 范围不变时 `TASK_RESUMED` 后以新 attempt 退回 Owner；范围变化则建修订任务 |
| `REVIEW_APPROVED` | 调用 QA |
| `QA_FAILED` | 范围不变时 `TASK_RESUMED` 后以新 attempt 退回 Owner，再重新 Review |
| `QA_PASSED` | 由 Coordinator 按任务图进入 `TASK_COMPLETED`、`RELEASE_READY` 或下游任务 |
| `BLOCKED` | 根据 `blocked_by` 调用对应角色或询问用户 |
| `WAITING_USER_APPROVAL` | 停止自动链路并询问用户 |
| `APPROVAL_GRANTED` | 回到 `ready`，重新检查依赖、锁和权限后投递 |
| `APPROVAL_REJECTED` | 取消任务 |
| `TASK_RESUMED` | blocked / waiting_external / changes_requested / qa_failed 回到 `ready` |
| `RELEASE_READY` | 仅在授权和门禁满足时交给 Release |
| `TASK_COMPLETED` | 更新依赖并调度新就绪任务 |
| `DOCUMENT_SUBAGENT_RESULT_RECEIVED` | 交给父智能体审查，不直接进入 QA 或 Release |
| `DOCUMENT_SUBAGENT_FAILED` | 通知父智能体和 Coordinator，停止该委派链 |

禁止智能体自行绕过 Coordinator 直接调用下游。

### 9. 可靠性和冲突控制

- 采用至少一次投递，所有有副作用操作必须幂等。
- 可重复事件必须提供稳定的 `event-key`；同一尝试内的传输重投复用同一个 key。
- 每次执行尝试使用新的 `attempt_id`。ACK、lease 和 result 分别写成
  `<task>-ack-<attempt>.yaml`、`<task>-lease-<attempt>-<lease>.yaml` 和
  `<task>-result-<attempt>.md`，旧尝试永不覆盖。
- 目标收到任务后写当前尝试 ACK；Coordinator 再用 `manage_run.py write-lease` 创建同一
  `attempt_id` 的不可变 lease，并作为 `LEASE_ACQUIRED` / `LEASE_RENEWED` 的 payload。
- 失败后只有 `RETRY_SCHEDULED → TASK_RESUMED → TASK_DISPATCHED` 才能开始新尝试；达到
  `max_attempts` 后禁止继续重试，必须进入 `dead-letter/`。
- 一个事件一个文件，禁止多人追加同一文件。
- Coordinator 独占 `state.yaml`、inbox 和全局事件序号。
- 每个智能体只写自己的 outbox。
- 高冲突路径使用锁；锁未释放时任务保持等待。
- 写入临时文件后原子重命名。
- ACK、result、Review、QA、人工许可和 dead letter 必须作为对应事件的 hashed payload，
  事件产生后不得原地修改。
- 不覆盖用户或其他线程的未提交改动。

### 10. Review、QA 和人工门禁

- Reviewer 依据任务文档、diff/commit、结果和证据审查，不以聊天自述为事实。
- QA 依据验收标准验证，不自动放宽标准。
- 生产、数据库、资金、权限扩大、密钥、删除、发布和回滚必须经过人工门禁。
- `local_only`、blocked、forbidden 或证据不完整的任务不得进入发布。

### 11. 恢复和收尾

总控中断后按以下顺序恢复：

```text
protocol.yaml
→ project.yaml
→ manifest.yaml
→ agents.yaml
→ state.yaml
→ events/
→ tasks/
→ inbox/outbox
→ delegations/
→ native/threads
→ native/operations
→ next-action.md
```

完成时：

- 验证所有必需任务、Review、QA 和人工许可。
- 生成 `summary.md`，列出完成项、commit、验证、风险和未完成项。
- 清理临时锁，不删除历史事件。
- 把 run 标记为只读归档。
- 不把临时通信文件误当成产品发布状态。

## 校验

初始化后运行：

```bash
python3 <skill-dir>/scripts/validate_run.py \
  "<project-root>/.multi-agent-collaboration/runs/<run-id>"
```

验证器默认根据状态自动选择 `structure`、`dispatch`、`completion` 或 `release` 阶段，也可
显式传 `--phase`。`completion` 和 `release` 采用 fail-closed：缺少任务、权限、hash、
Review、QA、人工许可、锁、commit 或治理证据时返回失败。

创建 Agent、任务、门禁、证据、ACK、result、锁、状态恢复和归档统一使用：

```bash
python3 <skill-dir>/scripts/manage_run.py --help
```

创建事件时优先使用：

```bash
python3 <skill-dir>/scripts/emit_event.py \
  --run-dir "<run-dir>" \
  --task-id TASK-001 \
  --event TASK_READY \
  --from-agent coordinator \
  --to-agent owner \
  --summary "Task is ready" \
  --payload-file "<task-file>"
```

校验失败时先修复协议和文档，不继续自动调度。

## 最终回复

向用户清楚报告：

- 使用的 transport 和 governance。
- 已创建或复用的线程/通用智能体。
- 当前任务图和状态。
- 哪些任务自动继续，哪些等待用户或通用智能体。
- commit、handoff、测试和证据。
- 阻塞、风险和下一条动作。

不要把“已写任务文档”表述成“目标智能体已经执行完成”。
