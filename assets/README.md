# Assets 模板说明

本目录保存协议 v3 文档模板。模板用于理解字段和生成外部文档；正式 Run 应优先通过
`scripts/` 中的命令创建，避免手工遗漏 hash、身份或状态约束。

## Run 与任务

| 模板 | 用途 |
| --- | --- |
| `project-binding.yaml.template` | 外部治理空间与目标项目的稳定绑定；不会复制到目标项目 |
| `project.yaml.template` | 项目根、允许范围和 Coordinator 身份 |
| `agents.yaml.template` | Run 内 Agent Registry |
| `manifest.yaml.template` | Run 策略、治理、版本合同引用和任务索引 |
| `task.md.template` | 冻结任务、角色、路径、版本绑定和验收条件 |
| `executor-binding.yaml.template` | Run 内短期执行实例、稳定 principal、worktree 和 lease |
| `next-action.md.template` | 可恢复的下一步操作 |
| `result.md.template` | Owner 的不可变执行结果 |

## 可靠性与治理

| 模板 | 用途 |
| --- | --- |
| `event.yaml.template` | 不可变事件 |
| `ack.yaml.template` | 当前 attempt 的接收确认 |
| `lease.yaml.template` | 执行租约及续约 |
| `lock.yaml.template` | 路径或逻辑资源锁 |
| `claims/tasks/` | 任务池的不可变 task claim/release（由 `agent_claim.py` 写入） |
| `claims/threads/` | thread 的不可变 claim/release，绑定 platform、session 线索和 workspace |
| `executors/` | 同角色短期执行实例 binding；不进入长期 TEAM 或 `agents.html` |
| `config/retry-policy.yaml` | fast/normal 的超时、重试、side-effect 和不可变事件策略 |
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

## 项目集成适配（可选）

| 模板 | 用途 |
| --- | --- |
| `integration-policy.yaml.template` | 由调用方提供的 canonical/working 分支、候选提交权限、冲突路径提示和冻结能力；不是项目运行时配置 |

`integration-policy.yaml` 必须由项目适配层或外部 Governance Home 提供。Skill 核心不猜测
分支名称，也不会因为模板存在就创建分支、调用命令或修改目标项目。未配置策略时，只能输出
Direct/Reviewed 路由、候选建议和只读阻塞原因。候选命令使用 JSON argv 数组，默认提交模式是
`manual`；只有策略明确声明 `authorized_auto` 并提供安全 argv 时，才允许后续集成器考虑自动提交。

## Agent 模板（新增）

| 模板 | 用途 |
| --- | --- |
| `templates/agent/ROLE.md` | Agent 岗位章程 |
| `templates/agent/SYSTEM_PROMPT.md` | Agent 恢复提示词 |
| `templates/agent/CHECKLIST.md` | Agent 检查清单 |
| `templates/agent/AGENT_PROFILE.json` | 长期 Agent 身份、角色版本、declared runtime policy 和稳定目录投影 |

## 对话模板（新增）

| 模板 | 用途 |
| --- | --- |
| `templates/conversation/CHECKPOINT.md` | 上下文检查点 |
| `templates/conversation/HANDOFF.md` | 标准化交接 |
| `templates/conversation/CURRENT_CONTEXT.md` | Agent 当前上下文 |
| `templates/conversation/SESSION_MAP.json` | 平台会话映射 |

## 项目模板（新增）

| 模板 | 用途 |
| --- | --- |
| `templates/project/TEAM.yaml` | 团队清单 |
| `templates/project/PROTOCOL.md` | 协同协议说明 |
| `templates/project/CURRENT_PROJECT_CONTEXT.md` | 项目级上下文 |
| `templates/project/DECISIONS.md` | 决策记录 |
| `templates/project/PROJECT_CHECKPOINT.md` | 项目级不可变检查点 |

## 输出模板（新增）

| 模板 | 用途 |
| --- | --- |
| `preflight-result.json.template` | dispatch/completion 一次性只读缺口报告示例 |

## Schema 定义（新增）

| Schema | 用途 |
| --- | --- |
| `schemas/task.schema.json` | 任务定义 Schema |
| `schemas/handoff.schema.json` | 交接文档 Schema |
| `schemas/checkpoint.schema.json` | 检查点 Schema |
| `schemas/session-map.schema.json` | 会话映射 Schema |
| `schemas/project-checkpoint.schema.json` | 项目级检查点 Schema |
| `schemas/agent-profile.schema.json` | 长期 Agent Profile Schema（含无运行状态的目录投影） |
| `schemas/runtime-profile.schema.json` | 单次 Session 不可变 Runtime Profile Schema |
| `schemas/agent-activity.schema.json` | Task Attempt Activity 与真实 usage 引用 Schema |
| `schemas/preflight-result.schema.json` | dispatch/completion 只读缺口报告 Schema |
| `schemas/executor-binding.schema.json` | Run 内 executor binding 与 worktree policy Schema |
| `schemas/candidate-summary.schema.json` | 完成/发布整备候选索引 Schema |
| `schemas/integration-policy.schema.json` | 可选项目集成适配策略 Schema；不包含任何具体项目、环境或 Agent 编号 |

模板中的 `<placeholder>` 不是有效运行数据。不要复制模板后声称任务、审批、验证或发布已经
发生。
