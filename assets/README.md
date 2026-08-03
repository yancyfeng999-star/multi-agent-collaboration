# Assets 模板说明

本目录保存协议 v3 文档模板。模板用于理解字段和生成外部文档；正式 Run 应优先通过
`scripts/` 中的命令创建，避免手工遗漏 hash、身份或状态约束。

## Run 与任务

| 模板 | 用途 |
| --- | --- |
| `project.yaml.template` | 项目根、允许范围和 Coordinator 身份 |
| `agents.yaml.template` | Run 内 Agent Registry |
| `manifest.yaml.template` | Run 策略、治理、版本合同引用和任务索引 |
| `task.md.template` | 冻结任务、角色、路径、版本绑定和验收条件 |
| `next-action.md.template` | 可恢复的下一步操作 |
| `result.md.template` | Owner 的不可变执行结果 |

## 可靠性与治理

| 模板 | 用途 |
| --- | --- |
| `event.yaml.template` | 不可变事件 |
| `ack.yaml.template` | 当前 attempt 的接收确认 |
| `lease.yaml.template` | 执行租约及续约 |
| `lock.yaml.template` | 路径或逻辑资源锁 |
| `dead-letter.yaml.template` | 重试耗尽后的失败记录 |
| `evidence.yaml.template` | Review、QA、安全、发布或验证证据 |
| `human-gate.yaml.template` | 用户批准、拒绝或待决门禁 |

## 原生与子代理绑定

| 模板 | 用途 |
| --- | --- |
| `codex-thread-binding.yaml.template` | Codex 独立任务绑定 |
| `codex-subagent-binding.yaml.template` | 当前任务内 Codex 子代理绑定 |
| `codex-operation.yaml.template` | 原生工具操作记录 |
| `document-subagent-binding.yaml.template` | 通用受管子代理 binding |

## 项目版本

| 模板 | 用途 |
| --- | --- |
| `version-contract.yaml.template` | Coordinator 冻结的项目交付版本合同 |
| `release-candidate.yaml.template` | 顺序递增且不可覆盖的 RC |

模板中的 `<placeholder>` 不是有效运行数据。不要复制模板后声称任务、审批、验证或发布已经
发生。
