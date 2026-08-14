# 通用文档通信协议 v3

本协议只用于 Coordinated。Direct 默认不创建 Run 或任何协议文件。Coordinated Run 位于项目外 Governance Home，目标项目的构建与运行不依赖该目录。

## 协同消息压缩

文档总线保留 Protocol v3 事件的完整审计，同时只向其它 Agent/用户传播四类有决策价值的摘要：
`STARTED`、`BLOCKED`、`CANDIDATE_READY`、`INTEGRATED`。普通 `PROGRESS`、命令日志和重复提醒不
触发新的协同消息。`BLOCKED` 必须带 blocker code、观察到的证据、scope impact、安全默认动作和
建议处置；scope impact 只能列受影响任务/候选，不得把局部冲突升级成全局停摆。

## 1. 事实来源

优先级：

1. 实际项目文件、Git commit 和外部系统可验证状态。
2. 通过结构、hash、权限和状态转换校验的事件。
3. 由事件 reducer 生成的 `state.yaml`。
4. 原生线程消息。
5. 聊天描述。

`manifest.yaml` 保存 Run 策略、任务索引、治理引用和派生 Run 状态；`state.yaml` 只能由
reducer 生成。任务文件冻结后 `status` 永远为 `draft`，不得用任务 frontmatter 表示运行
状态。

协议文件使用 UTF-8、LF 和受限 YAML 1.2。字符串应加双引号，字符串列表和 map 使用 JSON
行内语法。禁止 BOM、CRLF、重复 key、anchors、aliases、自定义 tags 和隐式日期。

## 2. Run 隔离和目录

```text
<governance-home>/projects/<project-key>/
├── project-binding.yaml
├── protocol.yaml
├── project.yaml
├── current-run
└── runs/
    └── RUN-.../
        ├── agents.yaml
        ├── manifest.yaml
        ├── state.yaml
        ├── next-action.md
        ├── summary.md
        ├── tasks/
        ├── inbox/<agent-id>/
        ├── outbox/<agent-id>/
        ├── events/
        ├── decisions/
        ├── artifacts/
        ├── evidence/
        ├── locks/
        ├── operations/        # Native/Document wake operation facts
        ├── claims/
        │   ├── tasks/
        │   └── threads/
        ├── executors/
        │   ├── EXEC-*.yaml
        │   └── releases/
        ├── config/
        ├── dead-letter/
        ├── delegations/
        ├── native/
        │   ├── threads/
        │   └── operations/
        ├── versions/
        │   ├── version-contract.yaml
        │   └── candidates/
        └── archive/
```

`project-binding.yaml` 将治理项目绑定到真实项目根；`protocol.yaml` 和 `project.yaml` 是治理项目级固定身份。Agent Registry 必须位于 Run 内，不能
从上一个 Run 继承角色、路径、thread id、agent id 或权限。`current-run` 只是便利指针，
不是权限或恢复事实。

## 3. 写入所有权

| 路径 | 唯一写入者 |
| --- | --- |
| `protocol.yaml`、`project.yaml` | 初始化者 |
| Run 的 `agents.yaml`、`manifest.yaml` | Coordinator / `manage_run.py` |
| `state.yaml` | 事件 reducer |
| `tasks/`、`inbox/`、`decisions/`、`evidence/` | Coordinator / `manage_run.py` |
| `outbox/<agent-id>/` | 对应 Agent；Codex 结果可由 Coordinator 代理 |
| `events/`、`locks/`、`delegations/`、`native/` | Coordinator |
| `claims/tasks/`、`claims/threads/` | 具备相应 claim 能力的 Agent，在独立 claim 锁内追加不可变 claim/release 记录 |
| `executors/`、`executors/releases/` | Executor pool，在独立 executor 锁内追加短期 binding/release；不写长期 TEAM |

原始 claim 文件不得覆盖。持有者主动让出时，使用 `agent_claim.py release-task` 或
`release-thread` 在对应 `releases/` 子目录追加 release 记录；release 只改变后续 operational
owner 解析，不伪造任务完成，也不自动把任务状态改回 `ready`。
| `versions/version-contract.yaml`、`versions/candidates/` | Coordinator |
| `summary.md`、`archive/` | Coordinator |

任务、ACK、lease、result、Review、QA、人工许可和 dead letter 一旦作为事件 payload
写入，就成为不可变文档。修改内容必须创建新的 revision 文件和新事件，不能原地改写。
manifest 中所有 `*_ref` 同时保存对应 `*_ref_sha256`；任务中预声明的 human gate 同时写入
`human_gate_hashes`；evidence 的 `artifact_refs` 必须由 `artifact_hashes` 完整覆盖。因此
dispatch/release 使用的配置证据和底层验证产物都不能被静默替换。

## 4. 身份、路径和权限

协议版本固定为 `3`。要求唯一的 `run_id`、`task_id`、`event_id`、`idempotency_key`、
`correlation_id`、有时区的 ISO 8601 时间和 SHA-256。

`project.yaml.allowed_roots` 是项目级最大访问范围。所有 Agent readable/writable/forbidden
路径、任务 owned paths、锁资源和证据引用都必须经过 canonical resolve：

- 相对路径基于 `project_root`。
- 解析 `..` 和现有 symlink。
- 使用真正的父子路径关系，禁止字符串前缀判断。
- 每个任务的 owned paths 必须位于 Owner writable paths 内且不落入 forbidden paths。
- 任务必须继承 Owner 的所有 forbidden paths。
- 子代理权限必须是父智能体权限的真子集或相同范围，不能扩大。

存在 owned path 重叠的任务必须通过 DAG 依赖或显式 `parent_task_id` 关系串行；没有任何
序列化关系时验证失败。`assignment_mode:
claimable` 的任务使用 `owner_agent: pool` 和 `eligible_agents`，owned paths 必须同时处于
所有 eligible Agent 的 writable scope；抢占后 effective Owner 才解析为 claimant。

### 项目交付版本

每个 Run 必须明确使用 `tracked` 或 `not_applicable`，不能省略判断。版本治理由
Coordinator 集中负责，不为版本识别、RC 编号或版本重评增加独立 Agent。

`versions/version-contract.yaml` 在初始化时冻结，并由 manifest 保存路径和 SHA-256。
每个任务必须携带与合同一致的 `release_train_id`、`delivery_version` 和
`version_contract_sha256`。`tracked` Run 在 dispatch 前校验版本源仍等于冻结基线；
漂移时停止调度。

候选版本由 `manage_run.py record-release-candidate` 顺序创建为 `RC-001`、`RC-002`，
文件不可覆盖。Release 必须至少存在一个候选版本，最新候选 commit 必须和 Release
任务结果一致，版本权威源必须包含预留目标版本。

## 5. 状态机

运行状态只由事件重放：

```text
TASK_READY              none → ready
TASK_DISPATCHED         ready → dispatched
ACK                     ready/dispatched → acknowledged
LEASE_ACQUIRED          acknowledged → running
LEASE_RENEWED           running → running
HANDOFF_READY           running → handoff_ready
REVIEW_STARTED          handoff_ready → reviewing
CHANGES_REQUESTED       handoff_ready/reviewing → changes_requested
REVIEW_APPROVED         handoff_ready/reviewing → qa_running
QA_FAILED               qa_running → qa_failed
QA_PASSED               qa_running → qa_passed
RELEASE_READY           qa_passed → release_ready
BLOCKED                 active → blocked
WAITING_USER_APPROVAL   active → waiting_user_approval
APPROVAL_GRANTED        waiting_user_approval → ready
APPROVAL_REJECTED       waiting_user_approval → cancelled
RETRY_SCHEDULED         blocked/failed → waiting_external
TASK_RESUMED            blocked/waiting_external/changes_requested/qa_failed → ready
TASK_COMPLETED          allowed state → completed
TASK_FAILED             nonterminal → failed
TASK_CANCELLED          nonterminal → cancelled
TASK_SUPERSEDED         nonterminal → superseded
TASK_EXPIRED            nonterminal → expired
DEAD_LETTERED           non-completed → dead_letter
```

传输事件和子代理生命周期事件不改变任务状态，但必须发生在已经 `TASK_READY` 的任务上。
Review/QA 要求的实现修复如果不改变冻结目标、范围、验收和门禁，可通过 `TASK_RESUMED`
回到 `ready`，并使用新的 `attempt_id` 重新投递。若任务内容、owned paths、风险、验收或
门禁发生变化，则必须使用新 task id，例如 `TASK-001-R1`；原任务保持
`changes_requested`/`qa_failed` 后转为 `superseded`，不得修改冻结任务。

`emit_event.py` 在 sequence 锁内先重放并验证下一转换，再写事件，最后重建完整
`state.yaml` 和 manifest Run 状态。恢复时使用 `manage_run.py rebuild-state`。

## 6. Payload 和不可变证据

以下事件强制提供精确文件和 SHA-256：

| 事件 | payload |
| --- | --- |
| `TASK_READY`、`TASK_DISPATCHED` | 精确的 `tasks/<task-id>.md` |
| `ACK` | 精确的 `<task>-ack-<attempt>.yaml` |
| `LEASE_ACQUIRED`、`LEASE_RENEWED` | 精确的 `<task>-lease-<attempt>-<lease>.yaml` |
| `HANDOFF_READY`、`TASK_COMPLETED`、`TASK_FAILED` | 精确的 `<task>-result-<attempt>.md` |
| `CHANGES_REQUESTED`、`REVIEW_APPROVED` | Run 内 review evidence |
| `QA_FAILED`、`QA_PASSED` | Run 内 QA evidence |
| `WAITING_USER_APPROVAL`、`APPROVAL_*`、`RELEASE_READY` | Run 内 human gate |
| `DEAD_LETTERED` | Run 内 dead-letter |
| 原生/文档子代理结果事件 | 精确的 Owner result |

验证器同时核对路径、hash、kind、task id、Owner 和事件语义。存在一个任意“hash 正确”的
其他文件不能替代任务或结果。

Coordinator 是原生工具事件、重试、终止、`RELEASE_READY` 和 `TASK_COMPLETED` 的唯一
事件写入者。`TASK_READY`/固定任务的 `TASK_DISPATCHED` 可以由拥有 `task_publish` 的父任务
Agent 在 `hybrid`/`self_service` 策略下写入；任务池只能先写 `TASK_READY`，claim 后由具备
`task_claim` 的 claimant 写 `TASK_DISPATCHED`。`HANDOFF_READY` 必须由 Owner 发给 Reviewer，`REVIEW_APPROVED` 必须由
Reviewer 发给 QA；进入 Release 的任务必须声明已注册的 `release_agent`，并由
`RELEASE_READY` 精确投递给它。

## 7. 投递、幂等和 lease

- 同一次有副作用投递复用相同 idempotency key。
- 不同 payload 默认使用 payload hash 派生 occurrence key。
- `THREAD_PROGRESS`、`SUBAGENT_MESSAGE_SENT` 和 `RETRY_SCHEDULED` 等可重复无 payload
  事件必须显式传稳定 `--event-key`。lease renewal 使用新的不可变 lease payload，其 hash
  自动成为 occurrence key。
- 每次执行尝试使用新的 `attempt_id`；同一尝试内的传输重投继续复用原 idempotency key。
  任务内容不变时 task id 不变，只有 ACK、lease 和 result 产生新的尝试文件。
- ACK 必须先落盘并作为 `ACK` payload；没有当前尝试的 ACK 不能建立 lease 或提交结果。
- 在 capability pool 中，ACK、lease、result 可带 `executor_id`；验证器会检查它与 task、
  principal Agent 和 attempt 的 immutable binding 一致。旧回执缺少该字段时按 legacy principal
  兼容读取。
- lease 由 `manage_run.py write-lease` 创建，且 `attempt_id` 必须匹配当前 ACK；获取和到期
  时间必须有时区且顺序有效。
  running 任务没有有效未过期 lease 时验证失败并进入恢复。
- `TASK_FAILED` 后通过 `RETRY_SCHEDULED → TASK_RESUMED → TASK_DISPATCHED` 开始下一
  尝试。新 ACK 的 `attempt_id` 不得复用；lease/result 必须绑定该 ACK。
- lease 超时后先检查副作用，再决定重投；生产、资金、删除、migration 和发布状态不明时
  禁止自动重试。
- ACK/lease 超时由 `recover_timeout.py` 记录 `blocked_by`、side-effect state 和下一动作；
  不因为 `fast` profile 自动伪造失败、重试或 dead-letter。
- ACK 尝试数和重试调度都受 manifest `max_attempts` 限制。耗尽后使用
  `manage_run.py write-dead-letter`；`attempts` 必须等于事件中实际 ACK 尝试数并达到该
  上限，`failed_event_id` 必须指向本任务的真实失败事件。随后以该文件为 payload 写
  `DEAD_LETTERED`。
- `TASK_COMPLETED` 必须引用最新 `HANDOFF_READY` 的同一结果；Review、QA 和 Release
  只消费事件绑定的最终尝试，不按文件名猜测“最新结果”。

## 8. DAG、并行和锁

manifest 的任务索引必须与 `tasks/` 完全一致。依赖必须存在、无环，且只有依赖任务
`completed` 后才能 `TASK_READY`。

`max_parallel` 限制 dispatched、acknowledged、running、reviewing 和 qa_running 的并发
数量。Emergency 的 `executor_policy=capability_pool` 允许同一 principal 在额度内绑定多个
executor，但不按角色设置隐含单实例队列。锁通过 `manage_run.py lock` 管理：

- path 资源必须位于任务 owned paths。
- logical 资源使用 `logical:<name>`。
- Owner 必须等于任务 Owner。
- 获取和到期时间必须有效。
- 父子路径锁视为冲突。
- logical/environment/workspace/release lane 冲突也必须串行；不同 worktree 的同类型任务可以并行。
- 终止任务不能保留活动锁。
- release 前必须释放全部活动锁；历史锁移动到 `archive/locks/`。

任务发布、任务 claim、thread claim 使用独立串行锁。claim 记录写入后不可覆盖；第二个
claimant 必须等待 claim lease 到期并完成副作用检查。thread claim 还必须绑定 platform 和
精确 project workspace。

## 9. 治理与人工门禁

初始化必须显式传 `--user-confirmed`，并生成 Run 内用户确认记录。Standard 和 Strict 的
任务进入 `TASK_READY` 前必须声明注册过的 Reviewer 和 QA。Reviewer 与 QA 可以是同一个
质量智能体，但必须与任务 Owner 独立。

Strict 在 dispatch 前强制：

- change id。
- registry、Git 状态、环境影响、回滚和安全审查引用。
- 当前非 detached Git branch 必须匹配 manifest，且除文档总线外工作区真实干净。
- 每个风险 flag 对应的已批准 human gate，且批准时间不晚于 `TASK_READY`。

Standard/Strict completion 强制 Review、QA、通过的验证、风险与回滚说明以及 commit 或
明确未提交原因。`verification_status: passed` 必须引用 Run 内 status 为 passed 的
verification evidence。Strict 强制 implementation commit 可从 manifest 的本地
`git_branch` 到达，且每个 result `changed_file` 都实际包含在该 commit 中。

Release 强制目标环境、发布许可、干净工作区证据、`RELEASE_READY` 的批准 gate；Strict
还实时检查除文档总线外的 Git 工作区是否干净。所有 release task 必须引用实际存在且
与 changed files 一致的 implementation commit；`local_only` 任务不能发布。

## 10. 原生操作和受管子代理

每个 Codex thread/subagent 都必须有 Run 内 binding。每个原生副作用操作必须有 operation
记录；成功的 create/fork/spawn/send/handoff/rename/pin/archive/close/resume 必须引用
hashed result。原生事件必须能找到语义匹配的 operation，不能只有事件没有调用证据。

`document_subagent` 必须有独立 Registry、task、inbox/outbox 和 delegation binding，父
智能体先审查结果。binding 与任务文件必须精确路径和 hash 一致。

## 11. 恢复、验证和归档

恢复顺序：

```text
protocol.yaml
→ project.yaml
→ manifest.yaml
→ agents.yaml
→ tasks/
→ events/
→ manage_run.py rebuild-state
→ inbox/outbox
→ decisions/evidence
→ locks/dead-letter
→ delegations/native
→ Git 与外部事实
→ next-action.md
```

验证阶段：

```bash
python3 scripts/validate_run.py <run-dir> --phase structure
python3 scripts/validate_run.py <run-dir> --phase dispatch
python3 scripts/validate_run.py <run-dir> --phase completion
python3 scripts/validate_run.py <run-dir> --phase release
```

默认 `auto` 根据真实状态选阶段。归档使用 `manage_run.py archive-run`，只有完成验证、无
活动锁且 Run 终止后才能生成归档 marker。

验证器是本地结构、权限、状态、常见泄密和治理门禁，不替代完整 DLP、隐私合规、安全
审计或对外部系统结果的人工确认。
