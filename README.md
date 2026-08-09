# 多智能体协同 | Multi-Agent Collaboration

## 名称 | Name

| 项目 | 内容 |
| --- | --- |
| 中文名称 | 多智能体协同 |
| English Name | Multi-Agent Collaboration |
| Skill ID | `multi-agent-collaboration` |
| Skill Version | `1.3.0` |
| Protocol Version | `3` |
| GitHub | [yancyfeng999-star/multi-agent-collaboration](https://github.com/yancyfeng999-star/multi-agent-collaboration) |
| License | [MIT](LICENSE) |
| 调用方式 | `$multi-agent-collaboration` |
| 协同数据目录 | `.multi-agent-collaboration/` |

## 快速导航 | Quick Navigation

| 入口 | 用途 |
| --- | --- |
| [agents.html](agents.html) | 用户选择 Agent、填写最小上下文并复制启动指令的默认入口 |
| [SKILL.md](SKILL.md) | Codex 执行本 Skill 时必须遵循的主协议 |
| [references/README.md](references/README.md) | 按场景选择详细规范 |
| [scripts/README.md](scripts/README.md) | 初始化、管理、事件和校验命令 |
| [assets/README.md](assets/README.md) | 协议模板及字段用途 |
| [tests/README.md](tests/README.md) | 测试范围和运行方法 |
| [CHANGELOG.md](CHANGELOG.md) | 协议及用户可见行为变更 |
| [docs/MERGED_STRUCTURE.md](docs/MERGED_STRUCTURE.md) | 两层架构、Agent 目录、运行资料与收口数据流 |

当前 Skill 正式版本为 `1.3.0`，唯一版本权威源是 [VERSION](VERSION)；当前可写协议为
v3。Skill 版本与协议版本独立递增。`SKILL.md` 是规范入口，根 README 用于介绍和导航；
两者冲突时应先修正文档与实现，不以 README 放宽协议门禁。

### 版本边界

| 对象 | 当前值/权威源 | 变化条件 |
| --- | --- | --- |
| Skill 版本 | `1.3.0` / [VERSION](VERSION) | 用户入口、文档、脚本、Schema、模板或默认行为变化 |
| Protocol 版本 | `3` / `scripts/protocol_lib.py` | Run、任务、事件、状态机、证据或恢复语义不兼容变化 |
| 项目业务版本 | 目标项目版本文件或 `version-contract` | 目标项目交付范围、兼容性或正式发布内容变化 |

更新本 Skill 不会自动改变目标项目业务版本。发布 Skill 时同步更新 VERSION、CHANGELOG、
中英文说明、入口和测试；只有目标项目进入 `tracked` Run 后，才按项目自己的版本规则治理
业务版本。

## 中文说明

### 定位

多智能体协同是一个面向通用项目的 Agent 角色目录与按需协同 Skill。默认由用户从 Agent
目录中选择一个角色、填写最小项目上下文并人工启动（手动启动）；当用户明确需要并行、接力、审计或高
风险治理时，再切换到 Protocol v3 的可验证文档协作流程。

它不默认展示项目状态或自动编排，但仍保留任务分工、权限隔离、并行调度、结果交接、冲突
控制、失败恢复、质量验证、版本治理和人工门禁能力。

### 用户默认入口

打开 [agents.html](agents.html)，选择 Coordinator、Owner、Quality 或按需使用 Release，
填写项目根目录、目标、允许修改范围和验收标准，然后复制启动指令到 Codex。该页面不读取
Run、不显示当前任务、不统计运行状态，也不自动创建线程或 Agent。

页面只是用户界面；项目稳定 Agent 身份仍以项目自己的 `TEAM.yaml`、`AGENT_PROFILE.json`
和 `ROLE.md` 为准。完整启动边界见 [agent-catalog.md](references/agent-catalog.md)。

### 核心能力

- 根据项目结构动态设计角色和有向无环任务图。
- 识别可并行任务与必须串行的高冲突任务。
- 为每个 Agent 限定可读、可写和禁止访问的路径。
- 支持 Codex 原生任务、当前任务内子代理、文档型智能体及混合协作。
- 使用不可变任务、事件、ACK、lease、result 和 evidence 建立可审计记录。
- 支持 Review、QA、重试、死信、恢复、归档和下游任务接力。
- 对生产、数据库、资金、权限、密钥、删除和发布操作设置人工门禁。
- 在总控中断后，通过文档和事件状态恢复协同现场。
- 把进入同一交付物的任务绑定到同一项目版本合同和 Release Train。
- 自动探测并不可变记录实际运行资料，明确区分 actual observation 与 declared default。

### 高级治理模式

只有用户明确要求多 Agent 并行、跨会话恢复、正式 Review/QA、版本合同、生产/数据库/权限
操作或发布审计时，才启用 Protocol v3 Run 或长期 Agent 层。高级层保留状态、事件、证据、
锁、恢复和版本门禁，但不改变 Agent 目录的默认人工选择体验。

### 最小必要智能体

功能、角色和 Agent 不是一一对应关系。Skill 默认把不冲突的能力合并给现有智能体：

- Coordinator 同时承担架构、路由、集成、版本治理和最终收口。
- Standard / Strict 使用独立于 Owner 的质量能力，但 Reviewer 与 QA 默认合并；
  Coordinator 未参与实现时可以直接兼任。
- Platform、Data、UI、Security、Operations 和 Release 是按需能力，不自动创建 Agent。
- 只有权限隔离、自审冲突、owned paths 冲突、独立环境、项目强制要求，或有可衡量并行
  收益时才新增智能体。

功能增加、分工方便或组织图清晰都不是新增智能体的理由。规划阶段必须说明每个新增 Agent
为什么不能继续与现有 Agent 合并。

### 三种治理模式

| 模式 | 适用场景 | 主要要求 |
| --- | --- | --- |
| `light` | 研究、方案和低风险文档 | 保存任务、事件、结果和总结 |
| `standard` | 常规代码修改 | 增加 owned paths、Git、Review、QA 和验证证据 |
| `strict` | 生产、数据库、资金、权限和发布 | 增加正式审批、安全审查、回滚和发布门禁 |

### 通信架构

Skill 始终以文档协议作为持久化通信底座，并按执行环境选择实时适配器：

- `codex_native`：全部执行者都是 Codex 任务。
- `document_bus`：通过 inbox、outbox 和可复制命令连接通用智能体。
- `hybrid`：Codex 与通用智能体共同协作。

原生消息用于实时唤醒和调度，文档用于恢复、验证和审计。即使实时消息不可用，执行者仍应
能够仅根据任务文档和结果文档完成接力。

### 集中式版本治理

版本治理不会拆分成更多 Agent。Coordinator 统一负责版本识别、目标版本合同、任务绑定、
RC 编号和版本重评；普通 Owner、Reviewer 和 QA 保持原职责。只有项目本来需要发布时，
现有 Release 角色才负责最终版本落盘和发布。

每个 Run 必须明确选择：

- `tracked`：代码、数据库、API、配置、构建、部署、发布或多 Agent 汇入同一正式交付物。
- `not_applicable`：只读分析、调研或不进入正式交付物的草稿，并写明理由。

`tracked` Run 会冻结基线版本、目标版本、版本权威源和 SHA-256，并把所有任务绑定到同一
`release_train_id`。任务返工增加 attempt，重新集成增加 RC；只有范围或兼容性变化才
重新评估项目正式版本。

### 默认单 Agent 使用

1. 打开 [agents.html](agents.html) 选择角色。
2. 填写项目根目录、目标、允许修改范围和验收标准。
3. 复制启动指令并粘贴到 Codex。
4. Agent 先只读检查；需要多 Agent、正式证据或高风险治理时再请求切换模式。

页面不会显示当前任务、运行状态或任务编排，也不会自动创建线程或 Agent。

### 高级治理工作流

1. 读取目标项目及其本地约束，不立即修改。
2. 确认目标、范围、验收标准、治理模式和并行限制。
3. 设计角色、owned paths、任务依赖和并行批次。
4. 经用户确认后初始化 `.multi-agent-collaboration/` 文档总线。
5. 先写任务文档和事件，再通知目标智能体。
6. 收集 ACK、lease、结果、Review、QA 和审批证据。
7. 根据事件状态机调度返工、下游任务、发布整备或人工处理。
8. 验证完成条件，生成总结并将运行记录只读归档。

### 运行资料与项目收口

运行资料采用“自动探测优先、缺失显式补充”：先使用获准运行上下文、平台/桥接证据、已验证
会话映射和固定环境 allowlist，再对缺字段请求 CLI/人工输入。实际观测值（actual）与 Registry、
项目配置或默认模型声明（declared）分开保存；declared 不能证明本次会话实际使用，也不能覆盖
actual。无法确认、历史未采集、可信来源冲突分别记录为 `unknown`、`not_collected`、`conflict`，
不得伪造值或静默选择冲突候选。

Token 和费用只记录 provider response、runtime meter 或 billing export 的真实回执；Skill 不按
文本长度、模型价目或持续时间估算。无回执时保持 `null`/`unavailable`。运行资料禁止保存全量
环境、prompt、原始命令/输出、密钥、token、Cookie、Authorization、私钥和带 query 的 URL；
敏感输入、输出或字段命中即 fail-closed，错误消息不回显秘密。

长期项目收口顺序固定为：`Bridge → PCP → index → validator → finalize`。Bridge 把完成 Run
哈希绑定到长期 Agent；PCP 创建项目 checkpoint；index 确定性重建；validator 校验结构、引用、
hash 和门禁；最后 finalize 才生成最终报告与审计包。

Hermes/Codex 远程 adapter 不是自动会话发现器。只有显式配置外部 bridge 且会话映射匹配
Agent、平台、真实 active session 和精确 workspace 时才尝试唤醒；bridge 退出 0 不等于远端
ACK、运行或完成。缺少真实 bridge/session 或投递失败时只写 document invocation package 并
报告 fallback，不宣称远端已唤醒。

### 使用方式

安装到 Codex：

```bash
git clone https://github.com/yancyfeng999-star/multi-agent-collaboration.git \
  ~/.codex/skills/multi-agent-collaboration
```

在 Codex 中调用：

```text
$multi-agent-collaboration
```

也可以直接描述需求，例如：

```text
使用多智能体协同分析这个项目，先设计角色和并行任务图，确认后再执行。
```

初始化运行：

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

验证运行：

```bash
python3 <skill-dir>/scripts/validate_run.py \
  "<project-root>/.multi-agent-collaboration/runs/<run-id>"
```

### 重要边界

- 未经用户明确确认，不创建多个 Codex 任务。
- 不把任务文档已经创建误报为目标智能体已经完成。
- 不允许智能体绕过 Coordinator 自行调用下游角色。
- 不允许子代理获得超过父智能体的权限。
- 不以聊天陈述代替文件、Git、测试或外部系统证据。
- 不因使用本 Skill 自动获得生产、发布、删除或其他高风险操作权限。
- 不把 declared 默认值写成 actual，不估算 Token/费用，不采集或持久化 secret。

## English Description

### Purpose

Multi-Agent Collaboration is an Agent role catalog and on-demand collaboration skill for
general-purpose projects. Its default entry is a human-facing `agents.html` directory: the user
chooses one known role, supplies the minimum project context, and manually starts that Agent. When
the user explicitly needs parallel work, handoffs, audit, or high-risk release governance, the
Skill switches to the verifiable Protocol v3 workflow.

The default page does not read Run data, show current tasks or runtime status, create threads, add
Agents, or orchestrate work automatically. The advanced layer still governs task ownership,
permission boundaries, parallel scheduling, handoffs, conflict control, failure recovery, quality
assurance, version contracts, and human approval gates.

### Version Boundaries

| Object | Current value / source of truth | Changes when |
| --- | --- | --- |
| Skill version | `1.3.0` / `VERSION` | The user entry, docs, scripts, schemas, templates, or default behavior changes |
| Protocol version | `3` / `scripts/protocol_lib.py` | Run, task, event, state-machine, evidence, or recovery semantics change incompatibly |
| Project business version | The target project's version source or `version-contract` | The target project's delivery scope, compatibility, or formal release changes |

Updating this Skill does not automatically change a target project's business version. A Skill
release updates its version authority, changelog, bilingual docs, entry page, and tests; project
version governance begins only inside a `tracked` Run under the target project's own rules.

### Core Capabilities

- Designs dynamic roles and a directed acyclic task graph from the actual project structure.
- Separates safely parallel work from high-conflict work that must remain sequential.
- Defines readable, writable, and forbidden paths for every agent.
- Supports Codex tasks, in-task subagents, document-based agents, and hybrid collaboration.
- Maintains auditable immutable tasks, events, acknowledgements, leases, results, and evidence.
- Supports review, QA, retries, dead letters, recovery, archival, and downstream handoffs.
- Requires human approval for production, database, financial, permission, secret, deletion, and
  release operations.
- Restores coordination state from documents and events after an interruption.
- Binds all tasks entering one deliverable to the same project version contract and release train.
- Captures immutable Runtime Profiles and Task Attempt Activity while separating observed actual
  runtime facts from declared defaults.

### Minimum Necessary Agents

Features, roles, and agents do not have a one-to-one relationship. Non-conflicting capabilities are
assigned to existing agents by default:

- The Coordinator also owns architecture, routing, integration, version governance, and closure.
- Standard and Strict use quality assurance independent from the Owner, while Reviewer and QA are
  combined by default; the Coordinator may fill that role when it did not implement the task.
- Platform, Data, UI, Security, Operations, and Release are optional capabilities, not automatic
  agent identities.
- A new agent requires permission isolation, a self-review conflict, owned-path conflict, a separate
  runtime or environment, a project mandate, or measurable parallel benefit.

Feature growth, convenient delegation, or a tidy organization chart is not sufficient justification
for another agent. Every proposed agent must state why its responsibilities cannot be merged into an
existing one.

### Governance Modes

| Mode | Intended Use | Main Requirements |
| --- | --- | --- |
| `light` | Research, planning, and low-risk documents | Tasks, events, results, and summary |
| `standard` | Normal code changes | Owned paths, Git evidence, review, QA, and validation |
| `strict` | Production, databases, finance, permissions, and releases | Formal approvals, security review, rollback, and release gates |

### Default Manual Agent Use

1. Open [`agents.html`](agents.html) and choose a role card.
2. Fill in the project root, objective, allowed modification scope, and acceptance criteria.
3. Copy the generated launch instruction and paste it into Codex yourself.
4. Let the Agent begin with a read-only inspection; switch to advanced governance only when the
   task actually needs multiple Agents, formal evidence, or high-risk controls.

The catalog is a launch surface, not a Run console. It intentionally does not display current work,
runtime state, task graphs, or automatic orchestration. Stable project identity comes from the
project's `TEAM.yaml`, `AGENT_PROFILE.json`, and `ROLE.md`; see
[`references/agent-catalog.md`](references/agent-catalog.md) for the contract.

### Communication Architecture

The document protocol is always the durable communication layer. A real-time adapter is selected for
the execution environment:

- `codex_native`: all executors are Codex tasks.
- `document_bus`: general agents communicate through inboxes, outboxes, and copyable commands.
- `hybrid`: Codex and general agents collaborate in the same run.

Native messages provide real-time wake-up and dispatch. Documents provide recovery, validation, and
auditability. Executors should still be able to continue from task and result documents when native
messaging is unavailable.

### Centralized Version Governance

Version governance does not introduce additional agents. The Coordinator owns version discovery,
the target-version contract, task binding, RC numbering, and version reassessment. Owners,
Reviewers, and QA agents keep their existing responsibilities. The existing Release role writes the
final version and performs release work only when the project already requires a release.

Every Run explicitly selects:

- `tracked` for code, database, API, configuration, build, deployment, release, or any formal
  deliverable assembled by multiple agents.
- `not_applicable` for read-only analysis, research, or drafts that do not enter a formal
  deliverable, with a recorded reason.

A tracked Run freezes the baseline version, target version, authoritative version source, and its
SHA-256. All tasks share one `release_train_id`. Rework increments a task attempt, reintegration
increments the RC number, and only scope or compatibility changes trigger reassessment of the
project version.

### Advanced Governance Workflow

1. Read the target project and its local instructions before making changes.
2. Confirm the objective, scope, acceptance criteria, governance mode, and concurrency limits.
3. Design roles, owned paths, dependencies, and parallel batches.
4. Initialize the `.multi-agent-collaboration/` document bus after user confirmation.
5. Persist the task and event before notifying an executor.
6. Collect acknowledgements, leases, results, review, QA, and approval evidence.
7. Route rework, downstream tasks, release preparation, or human intervention through the event
   state machine.
8. Validate completion, generate a summary, and archive the run as read-only history.

### Runtime Metadata and Project Closure

Runtime capture prefers approved automatic evidence and requests explicit values only when actual
model/provider data is missing or conflicted. Project or Hermes defaults are declared policy, not
proof of what a session used. Unknown, legacy-not-collected, and conflicting facts remain explicit;
the Skill does not synthesize placeholders or silently choose a conflict candidate.

Token counts and cost are persisted only from a provider response, runtime meter, or billing export.
Without a real receipt they remain `null`/`unavailable`. Full environment snapshots, prompts, raw
credentials, authorization headers, cookies, private keys, and secret-shaped values are rejected.

Long-term closure follows `Bridge → PCP → index → validator → finalize`. Each stage validates paths,
ownership, references, and SHA-256 bindings before the next stage. A Session Map publication failure
rolls back the corresponding Runtime Profile transaction, so retries do not leave orphan profiles.

Hermes/Codex adapters require an explicitly configured external bridge plus a matching stable agent,
platform, active session, and exact workspace. Bridge exit code zero proves delivery only—not ACK,
execution, or completion. Unsupported or failed delivery falls back to a real document invocation
package without claiming that the remote agent was awakened.

### Usage

Install for Codex:

```bash
git clone https://github.com/yancyfeng999-star/multi-agent-collaboration.git \
  ~/.codex/skills/multi-agent-collaboration
```

Invoke the Skill in Codex:

```text
$multi-agent-collaboration
```

Or use a natural-language request:

```text
Use Multi-Agent Collaboration to analyze this project. Design the roles and parallel task graph
first, then wait for confirmation before execution.
```

### Safety Boundaries

- Do not create multiple Codex tasks without explicit user confirmation.
- Do not report a persisted task as completed execution.
- Agents may not bypass the Coordinator to invoke downstream roles.
- A subagent may never receive broader permissions than its parent.
- Chat claims do not replace file, Git, test, or external-system evidence.
- This Skill does not grant production, release, deletion, or other high-risk authority.
- Declared defaults must not be persisted as observed actual runtime facts; token/cost estimates and
  secrets are prohibited.

## 目录结构 | Repository Layout

```text
multi-agent-collaboration/
├── SKILL.md                 # Codex 主协议 / normative skill entry
├── README.md                # 中英文介绍与导航 / bilingual overview
├── agents.html              # 用户选择 Agent 并手动复制启动指令的入口
├── CHANGELOG.md             # 协议变更 / protocol changes
├── docs/                    # 当前架构说明 / current architecture
├── agents/
│   └── openai.yaml          # Skill 展示和默认提示
├── references/              # 规范性详细协议
├── assets/                  # 协议模板与 JSON Schema
├── scripts/                 # 初始化、管理、事件和校验实现
└── tests/                   # 协议 v3 回归测试
```

维护时保持职责边界：

- 行为规范写入 `SKILL.md` 和对应 reference。
- 用户介绍、快速开始和目录导航写入根 `README.md`。
- 字段示例放入 `assets/`，不要复制进多个规范文件形成漂移。
- 可执行约束必须在 `scripts/` 中 fail-closed，并在 `tests/` 中覆盖。
- 功能增长不自动增加 Agent；先合并不冲突能力，再说明无法合并的边界。


### Agent 身份持久化

多智能体项目需要长期稳定的 Agent 身份，而不是每次运行都重新定义。每个长期 Agent 必须有：

- **ROLE.md** - 岗位章程，保存稳定岗位信息
- **SYSTEM_PROMPT.md** - 恢复提示词，用于新平台恢复 Agent 身份
- **conversations/** - 对话归档和检查点
- **SESSION_MAP.json** - 平台会话映射

初始化 Agent 结构：

```bash
python3 scripts/init_project_agents.py \
  --project-root "<project-root>" \
  --project-id "<project-id>" \
  --project-name "<project-name>" \
  --agents "A01-coordinator,A02-frontend,A03-backend" \
  --governance standard \
  --user-confirmed
```

### 对话归档与检查点

三层上下文模型：

1. **完整原文** - 用于审计和深度恢复，不能被摘要替代
2. **历史检查点** - 上下文压缩后的不可变快照
3. **当前上下文** - 只保留当前有效信息

检查点触发条件：
- 一个任务完成
- 对话即将进行平台原生压缩
- 切换问题域
- Agent 即将交接给另一个 Agent
- 累计消息或 token 超过配置阈值

### 跨平台恢复

核心原则：
- 项目目录是唯一可移植的长期真源
- 平台会话 ID 只是恢复线索，不是长期上下文的唯一来源
- 任何支持读取项目文件的 Agent 都应能恢复工作

恢复流程：
1. 确认项目根目录
2. 读取协议和团队清单
3. 确认自己的 Agent ID
4. 读取角色和恢复提示词
5. 读取当前上下文和最新检查点
6. 检查实际文件状态
7. 汇报恢复结果

### 绑定平台会话

```bash
python3 scripts/bind_session.py \
  --project-root "<project-root>" \
  --agent-id "A01-coordinator" \
  --platform hermes \
  --session-id "session-xxx" \
  --model "<observed-model>" \
  --provider "<observed-provider>" \
  --profile default
```

模型和 Provider 必须是本次会话的实际运行证据，不能使用项目或 Hermes 默认配置冒充 actual。
证据缺失或冲突时，绑定会 fail-closed，并保持 Session Map 与 Runtime Ledger 原样不变。

### 验证 Agent 结构

```bash
python3 scripts/validate_agents.py \
  --project-root "<project-root>"
```


## 进一步文档 | Further Documentation

- [Skill 主协议 | Main Skill Protocol](SKILL.md)
- [详细规范导航 | Reference Index](references/README.md)
- [文档通信协议 | Document Protocol](references/document-protocol.md)
- [Codex 原生协议 | Codex Native Protocol](references/codex-native-protocol.md)
- [访谈与任务规划 | Interview and Planning](references/interview-and-planning.md)
- [治理模式与门禁 | Governance Modes and Gates](references/modes-and-gates.md)
- [集中式版本治理 | Centralized Version Governance](references/version-governance.md)
- [运行资料采集 | Runtime Metadata](references/runtime-metadata.md)
- [文档子代理协议 | Document Subagent Protocol](references/document-subagent-protocol.md)
- [脚本说明 | Scripts Guide](scripts/README.md)
- [模板说明 | Assets Guide](assets/README.md)
- [测试说明 | Tests Guide](tests/README.md)
- [变更记录 | Change Log](CHANGELOG.md)
- [贡献指南 | Contributing](CONTRIBUTING.md)
- [MIT License](LICENSE)
