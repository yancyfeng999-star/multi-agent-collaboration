# 通用智能体子代理协议

## 1. 适用范围

本协议处理 runtime 为 `document` 的通用智能体在执行任务时使用子代理的情况。它不替代
Codex 原生 `codex_subagent` 协议，也不假设通用智能体一定具有可编程的子代理 API。

每次委派前必须先选择透明模式或受管模式。不能在任务完成后再补登记，把未受控的内部调用
追认成受管子代理。

## 2. 两种模式

### 2.1 透明子代理

透明子代理是父智能体内部的执行工具：

- 不进入全局 Agent Registry。
- 不拥有独立全局 `task_id`、inbox、outbox、ACK 或 lease。
- 不直接写项目通信目录和全局事件。
- 不直接联系 Coordinator、Reviewer、QA 或 Release。
- 父智能体是唯一 Owner，对输入、权限、结果、失败和重试负责。
- 父智能体最终 result 必须说明使用过内部子代理以及哪些结论未经独立验证。

适合搜索、分类、摘要、局部只读分析等低风险内部工作。只要子代理需要独立写路径、独立
验收、跨运行恢复或全局追踪，就必须使用受管模式。

### 2.2 受管子代理

受管子代理是正式执行节点：

- runtime 为 `document_subagent`。
- 在当前 Run 的 `agents.yaml` 中有独立 `agent_id` 和 `parent_agent_id`。
- 有独立任务、inbox、outbox、ACK、lease 和 result。
- 在 `delegations/` 中有独立 binding。
- 任务 Owner 是子代理，Reviewer 必须是父智能体。
- 子代理结果先交父智能体审查；父智能体不能用聊天自述代替结果证据。
- Coordinator 仍是任务、事件、全局状态和下游路由的唯一写入者。

## 3. 权限继承

受管子代理不能扩大父智能体权限：

- `readable_paths` 必须位于父智能体可读范围内。
- `writable_paths` 必须位于父智能体可写范围内。
- 必须继承父智能体的全部 `forbidden_paths`，可以增加禁止路径。
- 任务 `owned_paths` 必须同时位于子代理和父智能体可写范围内。
- 不继承父智能体的凭据、生产授权、发布许可或人工门禁批准。
- 父智能体无权执行的操作，子代理同样无权执行。

默认 `max_document_delegation_depth: 1`。父智能体深度为 `0`，其受管子代理深度为 `1`。
只有 manifest 明确提高上限且经过用户确认，受管子代理才能继续创建下一层；任何层级的
权限都必须逐级缩小。

## 4. Registry 字段

父智能体至少包含：

```yaml
agent_id: "research-owner"
runtime: "document"
parent_agent_id: null
delegation_depth: 0
```

受管子代理至少包含：

```yaml
agent_id: "research-child-1"
runtime: "document_subagent"
parent_agent_id: "research-owner"
delegation_depth: 1
```

父子双方都必须声明 `readable_paths`、`writable_paths`、`forbidden_paths`、inbox 和
outbox。禁止复用父智能体的 inbox/outbox。

## 5. Binding

使用
[document-subagent-binding.yaml.template](../assets/document-subagent-binding.yaml.template)
写入：

```text
delegations/<task-id>-<agent-id>.yaml
```

binding 必须记录父子身份、委派深度、任务路径/hash、最大执行时间、最大重试次数、状态、
结果引用和关闭时间。binding 由 Coordinator 写；通用父子智能体不得直接修改。

状态：

```text
requested
ready
running
blocked
result_received
failed
closed
```

`result_received` 只表示 Coordinator 已验证并保存子代理结果，不表示父智能体已经接受，
也不表示 Review、QA 或任务完成。

## 6. 委派顺序

严格顺序：

1. 父智能体向 Coordinator 提出委派请求和理由。
2. Coordinator 验证深度、权限子集、并行额度、路径冲突和风险门禁。
3. Coordinator 登记子代理并冻结独立任务。
4. 计算任务 SHA-256，写 `TASK_READY`。
5. 写 delegation binding。
6. 写 `DOCUMENT_SUBAGENT_DELEGATED`。
7. 将任务投递到子代理 inbox。
8. 子代理写 ACK；Coordinator 以该精确 ACK 为 payload 写 `ACK`。
9. 首次观察到执行后，Coordinator 创建不可变 lease，并以其为 payload 写
   `LEASE_ACQUIRED`。
10. 子代理只向自己的 outbox 写 result。
11. Coordinator 校验 result、hash 和边界，以 result 为事件 payload 写
    `DOCUMENT_SUBAGENT_RESULT_RECEIVED`。
12. Coordinator 以同一精确 result 为 payload 再写 `HANDOFF_READY`，把父智能体作为
    Reviewer 唤醒。
13. 父智能体通过正常 `REVIEW_APPROVED` 或 `CHANGES_REQUESTED` 返回决定。
14. 下游 Review/QA/Release 仍由 Coordinator 按任务图调度。

没有外部执行器时，任务保持 `waiting_external`，不得报告为 running。

## 7. 生命周期事件

| 事件 | 含义 |
| --- | --- |
| `DOCUMENT_SUBAGENT_DELEGATED` | binding 已建立，任务可以投递 |
| `DOCUMENT_SUBAGENT_RESULT_RECEIVED` | 子代理结果已落盘并通过结构校验 |
| `DOCUMENT_SUBAGENT_FAILED` | 子代理或委派执行失败 |
| `DOCUMENT_SUBAGENT_CLOSED` | 委派已关闭，不再允许继续写入 |

这些事件是委派传输事实，不替代 `ACK`、`LEASE_ACQUIRED`、`HANDOFF_READY`、
`REVIEW_APPROVED`、`QA_PASSED` 或 `TASK_COMPLETED`。

## 8. 失败、重试和关闭

- 超时后先检查 outbox、lease 和副作用状态，再决定重投。
- 同一尝试内的传输重投使用同一个 idempotency key；失败后的新执行尝试使用新的
  `attempt_id`，并创建独立 ACK、lease 和 result 文件。
- 任务内容变化必须创建修订任务和新 binding。
- 父智能体失败或关闭时，Coordinator 必须枚举其所有未关闭 binding，停止投递并逐一关闭。
- 子代理结果被父智能体拒绝时，先写 `CHANGES_REQUESTED`。目标、范围、owned paths、
  风险、验收标准和门禁均未变化时，在同一冻结任务和 binding 上写 `TASK_RESUMED`，
  使用新的 `attempt_id`、ACK、lease 和 result 返工；任一冻结内容或边界变化时，创建
  修订任务和新 binding，并将原任务标记为 superseded。两种情况都不得原地改写冻结任务。
- 副作用状态未知时不得自动重试生产、资金、删除、migration 或发布任务。
- 关闭后写 `DOCUMENT_SUBAGENT_CLOSED`；历史 binding、事件和结果不得删除。

## 9. 验收清单

- 已明确选择透明或受管模式。
- 受管子代理已登记父子关系和委派深度。
- 子代理权限已验证为父智能体权限子集。
- 子代理使用独立 task、inbox、outbox 和 binding。
- `TASK_READY`、binding、delegation 事件顺序正确。
- ACK、lease、result 和 task hash 一致。
- 父智能体已先审查子代理结果。
- 子代理没有直接调用 Reviewer、QA 或 Release。
- 失败、重试和关闭状态可恢复。
