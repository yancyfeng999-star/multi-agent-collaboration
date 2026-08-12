# 通信适配器

## 目录

1. 共同约束
2. Codex 原生适配器
3. 通用文档适配器
4. 混合模式
5. 双写一致性

## 1. 共同约束

所有适配器使用同一任务、事件、状态和结果协议。文档通信始终存在；适配器只决定如何通知执行者。

通信顺序固定为：

```text
persist task
→ emit TASK_READY
→ reducer updates state
→ emit TASK_DISPATCHED
→ notify runtime
→ receive result
→ persist result
→ emit result event with exact result payload
→ reducer updates state
→ notify downstream
```

任务池/自助派发使用受控串行顺序：

```text
persist parent-scoped task
→ authorized publisher emits TASK_READY
→ eligible Agent claims task under task-claim lock
→ claimant emits TASK_DISPATCHED
→ notify claimant runtime
→ ACK / lease / result
```

任务 claim 会把 pooled task 的 effective Owner 绑定到 claimant；没有有效 claim 时不得写
ACK、lease 或 result。thread claim 是另一把锁，只记录 thread、platform、session 线索和
精确 workspace，不改变任务状态机。

## 2. Codex 原生适配器

在 Codex 中发现可用线程工具后使用原生适配器。具体工具、pending worktree、cursor、
handoff 和生命周期规则以 [codex-native-protocol.md](codex-native-protocol.md) 为准。

常用能力：

- 列出项目和线程。
- 创建或 fork 线程。
- 向现有线程发送消息并在后台运行。
- 读取或等待线程状态。
- 在线程与 worktree 间 handoff。
- 重命名、固定或归档线程。

规则：

- 创建新线程前必须获得用户明确授权。
- 优先复用职责一致且上下文有效的线程。
- 不给两个写线程分配同一 owned path。
- 发送消息时包含完整任务，不要求目标线程先读 inbox。
- 同时附带 task path、hash 和结果格式，便于追溯。
- 目标结果必须镜像到 outbox 和事件文档。
- 每个原生任务必须有 thread binding；每个有副作用的原生调用必须有 operation 记录。
- 成功的有副作用 operation 必须引用 result_ref 和 result_sha256；原生事件必须能匹配到
  对应 operation。
- 不高频轮询；使用等待能力或合理间隔读取。
- 用户直接向线程追加新要求时，Coordinator 下一次恢复必须把要求补入事件链。

Codex 消息模板：

```text
Run: <run_id>
Task: <task_id>
Role: <agent_id>
Objective: ...
Owned paths: ...
Forbidden paths: ...
Dependencies satisfied: ...
Required output: ...
Verification: ...
Task document: <absolute path>
Task SHA-256: ...
Write no project communication state directly; return a structured result to Coordinator.
```

## 3. 通用文档适配器

通用智能体不需要专用 API。

Coordinator：

- 写任务到目标 inbox。
- 写 `next-action.md`，给出可复制命令。
- 等待目标 outbox。
- 校验结果引用的 task id、`TASK_READY.payload_sha256` 和协议版本。
- 持久化事件并调度下游。

通用智能体：

- 读取指定任务，不扫描其他 inbox。
- 校验 task id、`TASK_READY.payload_sha256` 和禁止项。
- 写 ACK。
- 执行后写自己的 result。
- 不改 `state.yaml`、其他 Agent 的文件或全局事件序号。
- 不直接调度下游。
- 具备 `task_publish` 时，只能在已声明父任务和冻结 scope 内发布；具备 `task_claim` 时，
  只能抢占 eligible 的 `owner_agent: pool` 任务。两项能力都不允许跳过 Reviewer/QA、人工
  门禁或 Release。

通用智能体使用子代理时，内部透明调用仍由主智能体负责；需要独立任务、写路径、恢复或
验收的子代理必须登记为 `document_subagent`，使用独立 inbox/outbox 和 delegation
binding。完整规则见 [document-subagent-protocol.md](document-subagent-protocol.md)。

调用命令模板：

```text
读取 <absolute-task-path> 和对应 TASK_READY 事件，校验 task_id 和 payload_sha256，按任务边界执行。
先在指定 outbox 写 ACK；完成后按 result 模板写结果。
不得修改 state.yaml、其他智能体 inbox/outbox 或未授权路径。
```

如果没有自动执行器，必须向用户展示这条命令并把任务保持为 `dispatched` 或 `waiting_external`，不能报告为 `running` 或 `completed`。任务池没有 claimant 时保持
`ready`，不创建伪 Owner。

## 4. 混合模式

Agent Registry 为每个最终智能体指定 runtime。一个智能体可承担多个不冲突角色，不因
runtime 配置需要而拆分 Agent：

```yaml
agents:
  - agent_id: backend-owner
    runtime: codex_thread
    thread_id: null
  - agent_id: security-review
    runtime: document
    invoke_template: "读取 {task_file} 并写入 {result_file}"
  - agent_id: evidence-child
    runtime: document_subagent
    parent_agent_id: security-review
    delegation_depth: 1
```

Codex Owner 完成后可以把结果文档投递给通用 Review；通用 Review 写回后，Coordinator 可直接唤醒 Codex Owner 修复。

不要让 runtime 差异改变任务状态和证据要求。

## 5. 双写一致性

文档写入成功后才能发送原生消息。

原生结果收到后：

1. 校验 task id。
2. 写 result 临时文件。
3. 原子重命名。
4. 生成 hash。
5. 以该精确 result 为 payload 写事件。
6. reducer 更新 state。
7. 调度下游。

如果文档写入失败，不触发下游。如果原生消息发送失败，文档仍保留 `TASK_READY`，可重试或切换为通用文档适配器。

所有 Agent Registry 都读取当前 `runs/<run-id>/agents.yaml`，禁止读取项目总线根目录或
其他 Run 的 Registry。
