# Codex 原生通信协议

## 目录

1. 适用范围
2. Runtime 类型
3. 工具发现与能力表
4. 创建和复用
5. Worktree 异步就绪
6. 消息投递与 ACK
7. 等待、读取和结果落盘
8. Handoff
9. 生命周期管理
10. 文档事件映射
11. 失败恢复
12. 完整执行清单

## 1. 适用范围

本协议描述 Codex runtime 如何使用原生任务通信，同时保留通用文档总线。原生工具名和
返回字段可能随 Codex 版本变化；每次运行先发现当前可用工具并读取其 schema，不凭记忆
构造参数，不伪造 project、thread、host、cursor、operation 或 pending ID。

Python 脚本只维护文档协议，不能直接调用仅存在于 Codex runtime 的线程工具。原生调用
由执行本 Skill 的 Codex Coordinator 完成。

## 2. Runtime 类型

| runtime | 用途 | 是否出现在侧边栏 | 文档要求 |
| --- | --- | --- | --- |
| `codex_thread` | 用户明确要求创建或管理独立 Codex 任务 | 是 | 完整双写 |
| `codex_subagent` | 当前任务内部的有界并行子任务 | 否 | 纳入正式 run 时完整双写 |
| `document` | 任意通用智能体 | 不适用 | 只用文档 |
| `document_subagent` | 通用智能体创建的正式受管子代理 | 不适用 | 文档任务 + delegation binding |

不要用 `create_thread` 代替普通内部子任务。只有用户明确要求新建、后台或独立 Codex
任务时才创建用户拥有的 thread。内部并行仅用于有界、互不冲突且有可衡量收益的工作；
能由现有智能体顺序完成的能力不为方便而拆分。
`document_subagent` 不使用 Codex 原生线程工具，其规则见
[document-subagent-protocol.md](document-subagent-protocol.md)。

## 3. 工具发现与能力表

每次调用前通过工具发现确认实际名称和 schema。当前能力按语义映射：

| 能力 | 原生工具 | 关键规则 |
| --- | --- | --- |
| 列出项目 | `list_projects` | `create_thread` 前选择真实 `projectId` |
| 列出任务 | `list_threads` | 查重、复用、恢复 pending 或失联任务 |
| 创建任务 | `create_thread` | 必须有用户明确授权 |
| Fork | `fork_thread` | 只复制已完成历史，不复制正在生成的当前 turn |
| 发送消息 | `send_message_to_thread` | 默认继承目标任务模型和思考等级 |
| 读取任务 | `read_thread` | 用 cursor 分页；只在需要时包含工具输出 |
| 等待任务 | `wait_threads` | 可用时按 hostId/cursor 有界等待，不高频轮询 |
| Handoff | `handoff_thread` | 不能移动调用线程自身；运行中目标会被中断 |
| Handoff 状态 | `get_handoff_status` | 使用 revision 和 30–60 秒等待，逐步退避 |
| 重命名 | `set_thread_title` | 使用稳定的角色/任务名称 |
| 固定 | `set_thread_pinned` | 只固定活跃协调任务 |
| 归档 | `set_thread_archived` | 文档归档完成后再归档原生任务 |

若某项工具未暴露，使用文档总线降级；不要声称原生操作成功。

内部 sub-agent 使用当前 runtime 暴露的多 Agent 工具：

| 能力 | 语义 |
| --- | --- |
| spawn | 创建有界子任务，记录真实 agent id |
| send | 向同一 sub-agent 补充或纠正任务 |
| wait | 有界等待一个或多个 sub-agent |
| close | 完成后关闭，释放并发额度 |
| resume | 确需继续时恢复已关闭 sub-agent |

只有用户或项目规则已允许委派时才能 spawn。立即阻塞主路径的工作留在 Coordinator；
适合并行且写入范围互斥的 sidecar 才交给 sub-agent。使用
[codex-subagent-binding.yaml.template](../assets/codex-subagent-binding.yaml.template)
记录 opaque agent id，不把它写成 thread id。

## 4. 创建和复用

执行顺序：

1. 文档任务冻结并产生 `TASK_READY`。
2. 有项目时用 `list_projects` 选择与绝对项目根目录匹配的 project；无项目时明确使用
   projectless target 和独立输出目录。
3. 用 `list_threads` 搜索已有 agent 名称、task id 和项目。
4. 只有职责、项目、上下文、owned paths 都兼容时才复用。
5. 否则根据授权调用 `create_thread` 或 `fork_thread`。
6. 将真实返回值写入
   [codex-thread-binding.yaml.template](../assets/codex-thread-binding.yaml.template)。
7. 生成原生生命周期事件并写 `TASK_DISPATCHED` 后再发送任务消息。

写任务默认使用独立 worktree。只有明确串行、同目录协作且不会重叠写入时才能使用 local
或 same-directory。创建时不主动覆盖模型；用户明确指定模型时才传 model。

Fork 运行中的任务不会复制正在执行的 turn。需要最新要求时，等待该 turn 完成后再 fork，
或 fork 后用 `send_message_to_thread` 补发完整任务。

## 5. Worktree 异步就绪

`create_thread` 或 `fork_thread` 可能只返回 `pendingWorktreeId`、`clientThreadId` 或其他
待处理标识。所有标识均视为 opaque：

- 原样写入 `pending_id`，不得转换、截断或推导 thread id。
- binding 状态设为 `provisioning`。
- 没有真实 `thread_id` 前不得发送消息、读取或 handoff。
- 使用原始工具返回、`list_threads` 或当前版本提供的状态能力确认真实 thread。
- 无法解析时保持 `waiting_external` 并向用户展示 pending ID，不重复创建替代任务。
- 超时后先查重，再决定重试；防止产生两个写入同一 owned path 的任务。

worktree 就绪后记录 `thread_id`、`host_id`、`worktree_path`、branch 和 base commit；工具
没有返回的字段保持 `null`，不得猜测。

## 6. 消息投递与 ACK

发送前必须满足：

- `TASK_READY` 存在且 hash 可验证。
- thread binding 状态为 `ready`。
- owned paths 没有冲突锁。
- 依赖已完成。
- 用户和风险门禁已满足。

原生消息必须包含 run id、task id、agent id、目标、owned/forbidden paths、依赖、验收、
验证命令、任务绝对路径、task hash、结果格式和禁止操作。

`send_message_to_thread` 成功只表示消息已接收，不等于任务完成。Coordinator 随即：

1. 写 `THREAD_MESSAGE_SENT`。
2. 代理写 owner ACK 文档。
3. 以该精确 ACK 文档为 payload 写 `ACK`。
4. 在首次观察到执行中状态后创建不可变 lease，并以其为 payload 写
   `LEASE_ACQUIRED`；续约使用新的 lease 文件和 `LEASE_RENEWED`。

发送失败时不写 ACK。使用同一个文档 idempotency key 重试；先查目标任务是否已经收到，
避免重复 turn。

## 7. 等待、读取和结果落盘

优先使用有界等待：

- `wait_threads` 可用时，传真实 hostId 和最近 cursor；`timeoutMs: 0` 只用于即时快照。
- 一次可等待多个目标；收到第一个完成、阻塞或需用户输入的目标后处理事件。
- cursor 必须原样保存到 binding，避免重复消费同一结果。
- `read_thread` 的分页 cursor 与 `wait_threads` 的进度 cursor 分开保存为
  `last_read_cursor` 和 `last_wait_cursor`，禁止互换。
- 不对没有变化的状态反复播报。
- 每次新的 `THREAD_PROGRESS` 使用独立且稳定的 `event-key`；同一次等待重试复用该 key。

需要上下文时使用 `read_thread`。默认只读最近 turn；仅在诊断失败时设置
`includeOutputs: true`，并限制单项输出长度。分页 cursor 原样保存。

收到结果后严格双写：

1. 校验 thread id、task id 和任务 hash。
2. 将原生结果转换为 owner 的 `<task-id>-result-<attempt-id>.md`，其中
   `attempt_id` 与当前 ACK/lease 一致。
3. 以该精确 result 为 payload 写 `THREAD_RESULT_RECEIVED`。
4. 按结果写 `HANDOFF_READY`、`BLOCKED`、`WAITING_USER_APPROVAL` 或 `TASK_FAILED`。
5. 更新 binding cursor、status、commit 和 evidence。
6. 再调用 Reviewer、QA 或下游。

聊天中的“完成”不能替代 result、diff/commit 和验证证据。

## 8. Handoff

Handoff 只用于另一个 Codex thread 及其 Git 状态在当前 checkout 与 Codex worktree 之间
移动。限制：

- 不能 handoff 当前调用线程自身。
- 运行中的目标会先被中断。
- cloud handoff 不支持时不得模拟成功。
- 生产、数据库或发布上下文仍受人工门禁约束。

流程：

1. 确认目标 thread、Git 状态、owned paths 和无并行写入。
2. 写 `THREAD_HANDOFF_STARTED` 和 operation 文档。
3. 调用 `handoff_thread`，保存真实 `operationId` 和 revision。
4. 调用 `get_handoff_status`，首次检查后使用 30–60 秒等待并退避。
5. 成功后更新 binding 的 environment/worktree/branch。
6. 失败或超时写 `THREAD_HANDOFF_FAILED`，保留原状态并停止下游。

## 9. 生命周期管理

- 创建后用 `set_thread_title` 设置 `<agent-id> | <task-id> | <short-title>`。
- 只固定 Coordinator 和当前需要用户关注的任务。
- 用户改变需求时，通过 `send_message_to_thread` 发送完整修订；任务内容变化则创建新的
  revision task id，不原地改写冻结任务。
- 用户直接在子任务中追加要求时，Coordinator 恢复时必须写 decision 和新事件。
- 任务失联时先 `list_threads`，再 `read_thread`；不要直接再创建一个写任务。
- 文档 summary、result、事件和 commit 完整后才归档。
- 调用 `set_thread_archived` 后写 `THREAD_ARCHIVED`；归档失败不影响文档事实，但需记录。
- `create_thread` 成功后按 Codex 客户端要求返回 created-thread 指令；worktree 尚在排队时
  使用真实 client/pending ID，不能提前宣告 thread 已就绪。

## 10. 文档事件映射

| 原生动作 | 文档事件 |
| --- | --- |
| 请求创建/fork | `THREAD_CREATE_REQUESTED` |
| 返回 pending ID | `THREAD_PROVISIONING` |
| 获得真实 thread id | `THREAD_READY` |
| 消息发送成功 | `THREAD_MESSAGE_SENT` |
| 观察到运行 | `THREAD_RUNNING` + `LEASE_ACQUIRED` |
| 收到新进度 | `THREAD_PROGRESS` |
| 收到最终结果 | `THREAD_RESULT_RECEIVED` |
| 用户输入请求 | `WAITING_USER_APPROVAL` |
| 原生失败/失联 | `THREAD_FAILED` 或 `BLOCKED` |
| 开始 handoff | `THREAD_HANDOFF_STARTED` |
| handoff 成功 | `THREAD_HANDOFF_COMPLETED` |
| handoff 失败 | `THREAD_HANDOFF_FAILED` |
| 归档成功 | `THREAD_ARCHIVED` |
| sub-agent 创建 | `SUBAGENT_SPAWNED` |
| sub-agent 消息 | `SUBAGENT_MESSAGE_SENT` |
| sub-agent 结果 | `SUBAGENT_RESULT_RECEIVED` |
| sub-agent 失败 | `SUBAGENT_FAILED` |
| sub-agent 关闭 | `SUBAGENT_CLOSED` |

原生事件是传输证据，不替代任务状态事件。比如 `THREAD_RESULT_RECEIVED` 后仍必须根据结果
写 `HANDOFF_READY` 或 `TASK_FAILED`。

每个原生事件必须能关联 Run 内的 operation 记录。create/fork/spawn/send/handoff/
rename/pin/archive/close/resume 成功时，operation 必须保存 `result_ref` 和
`result_sha256`；只有聊天或事件而没有 operation 证据时验证失败。

## 11. 失败恢复

| 故障 | 恢复 |
| --- | --- |
| 创建返回 pending | 查重并等待真实 thread；不重复创建 |
| 消息发送超时 | 查 `read_thread` 是否已收到，再用同一 key 重试 |
| wait 超时 | 保持 lease；读取一次状态，随后退避 |
| cursor 丢失 | 从 binding 的最后 cursor 恢复；必要时读取最近 turn 去重 |
| 线程需用户输入 | 写 `WAITING_USER_APPROVAL`，停止自动链 |
| 线程失败 | 保存错误摘要，写 `THREAD_FAILED`，按风险决定重试或 dead letter |
| Coordinator 中断 | 读取 binding、operations、events，再对照 `list_threads` |
| worktree 冲突 | 停止目标任务，释放前不得创建第二写任务 |
| handoff 中断 | 用 operationId/revision 恢复状态查询 |
| 结果与 Git 不一致 | 以实际 Git/diff 为准，退回 Owner |

资金、发布、删除、migration 等副作用状态不确定时禁止自动重试。

## 12. 完整执行清单

- 已获得用户创建独立 Codex 任务的明确许可。
- 已冻结任务并写 `TASK_READY`。
- 已发现当前工具 schema。
- 已选择真实 project，完成 thread 查重。
- 已选择 local/worktree/fork，并记录 pending 或 thread id。
- 已建立 binding 和 operation 文档。
- Registry 来自当前 Run 的 `agents.yaml`，没有复用其他 Run 的 thread/agent id。
- `codex_thread` 与 `codex_subagent` 使用了正确的独立 binding。
- 已发送完整消息并写代理 ACK/lease。
- 已用 cursor/hostId 有界等待。
- 已把结果、commit、验证和风险落盘。
- 已完成 Review/QA/人工门禁。
- 已处理 handoff 或明确不需要。
- 已生成 summary 后归档原生任务。
