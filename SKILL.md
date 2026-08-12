---
name: multi-agent-collaboration
description: Use when a user wants to understand or manually start an Agent role, or when a project needs multiple agents, parallel ownership, governed handoffs, review and QA, cross-session recovery, or auditable release coordination.
---

# 多智能体协同（Multi-Agent Collaboration）

- 中文名称：多智能体协同
- 英文名称：Multi-Agent Collaboration
- Skill ID：`multi-agent-collaboration`
- Skill 版本：`2.1.0`（唯一版本权威源：`VERSION`）
- Protocol 版本：`3`
- Governance Storage Schema：`1.1`（兼容读取 `1.0`）
- 调用：`$multi-agent-collaboration`

## 定位

本 Skill 有两条路径：

- **Direct**：默认路径。一个 Agent 在当前任务中完成明确目标，默认不创建治理资料。
- **Coordinated**：只在确实需要多 Agent、受管交接、独立质量门禁、跨会话恢复或审计时启用，资料保存在项目外 Governance Home。

本 Skill 是开发治理能力，不是目标网站的运行组件。网站构建、启动、测试、部署和线上运行对治理资料零依赖。不自动创建或修改目标项目的 `AGENTS.md`。

紧急 Bug 优先走最短安全路径：单一、低风险、可逆修复使用 **Direct Hotfix**；只有存在两个以上真正独立的工作单元、需要并行写入或需要可审计接力时，才使用 **Coordinated Emergency**。Emergency 缩短的是等待和资料范围，不降低生产、数据库、支付、权限、密钥、部署、删除或回滚门禁。

## 强制边界

1. 先读项目、项目级指令、Git 状态、测试和版本规则，再决定是否写入。
2. Direct 不创建 Agent、Run、handoff、checkpoint、candidate index 或项目内治理目录。
3. Coordinated 的治理根必须在目标项目之外；默认为 `~/.codex/governance/multi-agent-collaboration`。
4. 只有项目业务源码、测试、构建配置、版本权威源和必要交付文档可位于目标项目。
5. 旧的项目内 `.multi-agent-collaboration/` 只读兼容；只能经显式 dry-run/apply 迁移，不自动删除。
6. 不为新功能自动增加长期 Agent。能交给同一 Agent 且权限、写入范围、独立审查不冲突的能力必须合并；在用户授权的 `max_parallel` 内，可以为不同任务建立 Run 内短期执行实例。
7. Owner 不能审查自己的实现。Reviewer 与 QA 默认合并为一个独立 Quality 能力。
8. 未经用户明确授权，不创建外部任务、不发布、不部署、不执行高风险操作。

## 默认用户入口：Agent 目录与人工启动

用户可打开 [agents.html](agents.html)，了解 Coordinator、Owner、Quality 和按需 Release，填写项目根目录、目标、范围和验收标准，然后人工启动。

该页面：

- 不读取项目 Run，不显示实时任务或 Agent 状态。
- 不自动分配任务、不创建线程、不增加 Agent。
- 生成的是 Direct 启动指令，不写入目标项目或 Governance Home。
- 只是 Skill 自带的静态角色目录，不是任何项目的 Agent 事实源。

角色说明见 [agent-catalog.md](references/agent-catalog.md)。

## 能力路由

| 场景 | 路径 | 持久资料 |
| --- | --- | --- |
| 单一、明确、低风险任务 | Direct | 无 |
| 单一紧急 Bug、需要立即修复 | Direct Hotfix | 无；部署前单独请求授权 |
| 普通网站更新，一个 Owner 可完成 | Direct | 无 |
| 多个独立调查，结果可由当前任务收集 | Direct + 临时子 Agent | 不建长期 Agent 层 |
| 需要多 Agent 并行写入、ACK/lease、锁、交接或独立质量门禁 | Coordinated + Protocol v3 Run | Governance Home 内 Run |
| 多天、稳定身份、跨会话恢复 | Coordinated + 长期 Agent 层 | Governance Home 内 Agents |
| 长期项目的正式执行波次 | 长期层 + Run 层 | 两者同属一个外部治理项目 |
| 生产、数据库、资金、权限、删除、发布 | Coordinated + Strict | 正式证据与人工门禁 |
| 多个相互独立的紧急 Bug | Coordinated Emergency | 任务级 Preflight、短期执行实例和冲突指纹 |

事实分工：

- **长期 Agent 层**保存稳定身份、会话映射、原文归档、checkpoint、handoff 和恢复资料。
- **Protocol v3 Run**保存冻结任务、事件、ACK/lease、锁、result、Review、QA、证据和发布门禁。
- **长期层 + Run 层**中，Run 是执行状态真源；长期层只通过校验后的 Bridge 沉淀结果。
- **Candidate Index**只是派生视图，不授予发布权限，也不是项目构建输入。

## Direct 工作流

1. 读取用户已给定的项目根、项目指令、Git 状态、目标、范围和验收标准。
2. 只询问会改变实现方向的缺失信息。
3. 一个 Agent 完成实现和相称验证；必要的独立只读子任务可在当前任务内使用临时子 Agent。
4. 报告真实文件、验证、风险和未验证项。
5. 不运行 `init_project_agents.py` 或 `init_run.py`，不创建治理记录。

任务中途如果出现真实的并行写入、自审冲突、跨会话恢复或正式门禁需求，明确说明原因后才升级为 Coordinated。

## Coordinated 最小编制

Coordinated 是按需启用的高级治理路径，不是普通项目更新的默认前置。

先合并能力，再确定 Agent 数量：

- Coordinator：目标、范围、任务图、冲突串行、结果集成、版本治理和最终收口。
- Owner：在唯一 owned paths 范围内实现一个可验证目标。
- Quality：独立 Review + QA；仅在 Owner 不能自审或项目门禁需要时创建。
- Release：只在项目已有正式发布流程时按需启用，不为版本治理单独新建 Agent。

只有权限隔离、owned paths 冲突、独立运行环境、项目强制独立审查，或有可衡量并行收益时才增加 Agent。

## Governance Home

默认布局：

```text
~/.codex/governance/multi-agent-collaboration/
└── projects/<project-key>/
    ├── project-binding.yaml
    ├── TEAM.yaml
    ├── agents/<agent-id>/
    ├── runs/<run-id>/
    ├── bridges/
    ├── project-checkpoints/
    └── migrations/
```

`project-binding.yaml` 将治理项目绑定到真实 `project_root`。治理根与项目根任意一方位于另一方内都必须 fail-closed。详见 [storage-protocol.md](references/storage-protocol.md)。

## Coordinated 初始化

只有用户确认后才执行。如果仅需一次 Run，不必先创建长期 Agent 层。

### 可选：长期 Agent 层

```bash
python3 <skill-dir>/scripts/init_project_agents.py \
  --project-root "<project-root>" \
  --governance-root "<governance-home>" \
  --project-id "<project-id>" \
  --project-name "<project-name>" \
  --agents "A01-coordinator,A02-owner,A03-quality" \
  --governance standard \
  --user-confirmed
```

### Protocol v3 Run

```bash
python3 <skill-dir>/scripts/init_run.py \
  --coordination-mode coordinated \
  --project-root "<project-root>" \
  --governance-root "<governance-home>" \
  --project-id "<project-id>" \
  --project-name "<project-name>" \
  --governance standard \
  --execution-profile normal \
  --dispatch-policy hybrid \
  --transport hybrid \
  --objective "<objective>" \
  --versioning-mode tracked \
  --versioning-reason "<reason>" \
  --user-confirmed
```

Direct 调用 `init_run.py` 会被拒绝，因为 Direct 不需要 Run。

## 治理、时效与自助协同

- `light`：调研、方案、低风险文档。
- `standard`：代码修改，保留 owned paths、Git、独立质量和验证证据。
- `strict`：生产、数据库、资金、权限、密钥、删除和发布，保留安全审查、人工门禁和回滚。

`execution_profile=emergency` 面向时效优先的修复 Run：默认 `preflight_scope=task` 和 `executor_policy=capability_pool`，某个任务缺资料只进入 `blocked_tasks`，不阻塞无关任务。`fast` 仍可用于 Light/Standard，通过一次 preflight 和一次 completion preflight 减少重复唤醒；不降低路径、Secret、证据或人工门禁。Strict 不可使用 fast，但可以使用 emergency。

Light/Standard Emergency 的低风险、本地可逆任务不因缺少完整 Run 级 `scope_freeze_ref` 而停摆；任务级路径、能力、资源、验收和真实验证仍不可省略。Strict Emergency 仍保留 scope freeze 与全部高风险门禁。

`dispatch_policy`：

- `central`：只有 Coordinator 发布。
- `hybrid`：工作 Agent 可在已冻结父任务范围内发布子任务。
- `self_service`：eligible Agent 还可以串行 claim 任务或线程。

工作 Agent 不必唤醒主架构 Agent 才能发布父任务内的明确子任务，但必须满足父任务 hash、owned/forbidden paths、冻结范围、冲突指纹和发布锁。同一任务、线程或高冲突资源必须串行；claim 不能覆盖未过期持有者，也不能扩大权限或发布资格。

### Run 内短期执行实例

`principal_agent_id` 是稳定权限主体，`executor_id` 是一次 task attempt 的短期执行实例。两个无冲突任务可以绑定同一个 principal 的不同 executor，在不同 worktree 中并行；同一任务、同一写 worktree、重叠路径、logical/environment resource 和 release lane 仍然串行。执行实例只写入 Run 的 `executors/`，结束时追加 release/expiry 记录，不进入长期 TEAM 或 `agents.html`。Native 新实例只有在 `executor_scale_authorized=true` 且不超过容量时才允许创建。

详见 [modes-and-gates.md](references/modes-and-gates.md)和 [document-protocol.md](references/document-protocol.md)。

## 版本边界

| 对象 | 权威源 | 何时变化 |
| --- | --- | --- |
| Skill 版本 | `VERSION` | Skill 用户可见能力、默认行为、文档、脚本或 Schema 变化 |
| Protocol 版本 | `scripts/protocol_lib.py` 和协议文档 | 任务、事件、状态机、证据或恢复语义不兼容变化 |
| Governance Storage Schema | binding 与 storage schema | 外部治理布局或绑定契约不兼容变化 |
| 项目业务版本 | 目标项目的唯一版本权威源 | 项目交付范围、兼容性或正式发布内容变化 |

版本治理不新增 Version Agent。Coordinator 负责识别权威源、冻结版本合同、绑定任务、管理 RC 和重评；只有真正进入项目发布时，现有 Release 能力才落盘版本与执行发布。Direct 也要按项目自身规则判断是否更新业务版本，但不因此创建治理 Run。

详见 [version-governance.md](references/version-governance.md)。

## Emergency、冲突与可靠性

Coordinated 保持 Protocol v3 语义：

- 每个任务只有一个 Owner，冻结后状态只由不可变事件重放得出。
- 先写任务文档和 `TASK_READY`，再 `TASK_DISPATCHED` 并唤醒执行者。
- ACK、lease、result、Review、QA、人工许可和 dead letter 必须是可校验 payload。
- 同一高冲突文件、schema、全局样式、注册表、锁文件、版本源和发布账本必须串行。
- Coordinator 和工作 Agent 都使用 dependency/path/logical/workspace/environment/release 六维冲突指纹；无关同类型任务不再按角色排全局队列。
- `blocked_tasks` 表示任务级门禁缺口，`deferred_tasks` 表示容量或冲突等待，`run_level_blockers` 只保留 manifest、Protocol、项目根失效等全局故障。
- 超时先检查副作用；不伪造失败，不自动重投可能已产生副作用的任务。
- 不覆盖用户或其他任务的未提交改动。

## 收口、Bridge 与 Candidate

1. `validate_run.py --phase completion` 通过后，才可将 Run 事实 Bridge 到长期 Agent 层。
2. Bridge 从 binding 读取真实项目根，分别校验治理源和项目产物，不修改 Run。
3. Project checkpoint、final report、audit manifest 和 artifact index 只写 Governance Home。
4. `build_candidate_index.py` 始终只读；它汇总 commit、handoff、Review/QA 和阻塞，不授予发布权限。
5. 目标项目不得在构建或运行时读取上述文件。

详见 [run-memory-bridge.md](references/run-memory-bridge.md)和 [project-finalization.md](references/project-finalization.md)。

## 旧资料迁移

```bash
python3 <skill-dir>/scripts/migrate_governance_storage.py \
  --project-root "<project-root>" \
  --project-id "<project-id>" \
  --project-name "<project-name>" \
  --governance-root "<governance-home>" \
  --dry-run
```

审查文件清单、目标和 SHA-256 后，才将 `--dry-run` 换为 `--apply`。迁移器使用外部 staging、逐文件验证和原子发布；不删除或改写旧目录，拒绝 symlink、特殊文件和目标冲突。

## 恢复与跨平台

恢复时先从 project binding 确认项目身份，再读 TEAM、ROLE、CURRENT_CONTEXT、最新 checkpoint、handoff 和 Run state。平台会话只是加速层，不是唯一真源。不从私有会话库或未授权路径猜测事实。

详见 [cross-platform-resume.md](references/cross-platform-resume.md)。

## 完成前验证

- Direct：验证项目改动和测试，并确认项目中没有新治理资料。
- Coordinated：同时验证 binding、Agent 层、Run 层、hash、路径范围、Review/QA 与人工门禁。
- Emergency capability pool：另外验证 executor binding、worktree policy、claim/thread/operation 的 `executor_id` 一致性，以及 release/expiry 记录。
- 不把“已创建任务文档”说成“Agent 已执行”，不把本地验证说成远程或生产验收。

脚本索引见 [scripts/README.md](scripts/README.md)，详细规范索引见 [references/README.md](references/README.md)。
