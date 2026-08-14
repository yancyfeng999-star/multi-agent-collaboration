# Scripts 使用说明

脚本实现协议 v3 的外置开发治理文档总线。Direct 不需要运行初始化脚本；Coordinated 命令必须使用项目外 Governance Home。

## 核心脚本

| 脚本 | 职责 |
| --- | --- |
| `init_run.py` | 用户确认 Coordinated 后在 Governance Home 初始化 Run、版本合同和门禁；Direct 拒绝创建 Run |
| `manage_run.py` | 创建 Agent/任务/证据/RC，管理 ACK、lease、锁、恢复和归档 |
| `emit_event.py` | 校验并原子写入事件，然后重建派生状态 |
| `validate_run.py` | 对 structure、dispatch、completion 或 release 执行 fail-closed 校验 |
| `protocol_lib.py` | YAML 子集、路径、hash、状态机和运行文档的共享实现 |

## Agent 管理脚本

| 脚本 | 职责 |
| --- | --- |
| `init_project_agents.py` | 在 Governance Home 初始化长期 Agent 身份、会话、handoff 及 schemas/templates，不写目标项目 |
| `bind_session.py` | 绑定平台会话到 Agent，保留历史映射 |
| `sync_conversation.py` | 导入会话导出、默认脱敏并写入不可变 archive |
| `create_checkpoint.py` | 从已同步 archive 创建链式不可变检查点 |
| `rebuild_index.py` | 重建项目 INDEX、Agent INDEX 和 `index.jsonl` |
| `resume_brief.py` | 生成跨平台最小恢复包 |
| `validate_agents.py` | fail-closed 验证身份、archive、检查点链、索引和敏感信息 |
| `archive_run_to_agents.py` | 将通过 completion 门禁的 Run 事实幂等沉淀到长期 Agent 层 |
| `create_project_checkpoint.py` | 创建链式不可变项目级 checkpoint 并更新项目上下文 |
| `finalize_project.py` | 通过 Run 与长期层门禁后生成项目最终报告和审计清单 |
| `manage_project_agents.py` | 添加、更新、暂停、恢复、退役和修复长期 Agent |
| `migrate_project_agents.py` | 以备份、校验和回滚迁移长期存储版本 |
| `migrate_governance_storage.py` | 将旧的项目内治理目录逐文件校验并复制到外部 Governance Home，不删除源目录 |
| `coordinator.py` | 执行有界单 tick 的 ready-wave、超时与投递协调 |
| `executor_pool.py` | 按 role/capability/worktree/容量分配 Run 内短期 executor，并追加 release 记录 |
| `preflight_run.py` | 一次性只读检查任务图、范围、锁、治理和派发准备度 |
| `completion_preflight.py` | 一次性只读检查 Owner 结果、验证、Review/QA、commit 和收口缺口 |
| `freeze_scope.py` | 冻结请求路径、禁止路径和目标环境，生成不可变 scope hash |
| `agent_dispatch.py` | 允许有 `task_publish` 的工作 Agent 在父任务范围内发布子任务 |
| `agent_claim.py` | 串行抢占任务池或 Codex/Hermes/document thread，并绑定有效 Owner |
| `recover_timeout.py` | 记录 ACK/lease 超时恢复决策，避免无证据自动重投 |
| `resource_queue.py` | 为共享资源写 FIFO 请求，避免多个 Agent 同时争用高冲突资源 |
| `build_candidate_index.py` | 只读汇总完成/发布整备候选的版本、commit 和证据事实 |
| `integration_policy.py` | 只读加载并校验项目可选的集成适配策略；缺失策略保持只读，不创建分支或调用命令 |
| `integration_lane.py` | 只读评估候选并在显式确认、单一锁和 Git 预检下串行集成一个候选 |
| `integration_lib.py` | 候选合同、Git commit/diff、资源冲突和 release freeze 事实的共享只读库 |
| `evidence_layers.py` | 只读校验 canonical freeze，生成缺失即 `not_verified` 的通用证据分层 |
| `finalize_worktree.py` | 审计并在显式确认下安全移除已注册、干净且候选 commit 有其它 ref 保存的临时 worktree |
| `message_contract.py` | 校验并压缩 STARTED/BLOCKED/CANDIDATE_READY/INTEGRATED 摘要，不改变 Protocol v3 事件 |
| `migrate_run_optimization.py` | 为旧 Run 增补执行 profile、任务级 Preflight、executor policy 和 retry policy，支持 dry-run/apply/rollback |
| `wake_agent.py` | 验证身份/会话后调用适配器，失败时安全回退 document bus |
| `runtime_metadata.py` | 按安全来源优先级探测 actual runtime metadata，并处理未知与冲突 |
| `record_agent_runtime.py` | 原子发布不可变 Runtime Profile、哈希链、索引和当前指针 |
| `record_agent_activity.py` | 记录 Task Attempt Activity、真实 usage 引用和不可变哈希链 |
| `migrate_agent_runtime.py` | dry-run/plan-bound 迁移 legacy Agent 运行资料，支持回滚与幂等 |
| `project_memory_lib.py` | 长期层共享路径、锁、原子写、hash、frontmatter 和 Secret 防护库 |

## 推荐顺序

### 初始化项目 Agent

```text
只读扫描与用户确认
→ init_project_agents.py（创建 Agent 结构）
→ 编辑 ROLE.md 和 SYSTEM_PROMPT.md
→ bind_session.py（绑定平台会话）
→ init_run.py（创建第一个 Run）
```

### 执行 Run

```text
init_run.py
→ manage_run.py add-agent
→ manage_run.py create-task
→ emit_event.py TASK_READY
→ emit_event.py TASK_DISPATCHED
→ ACK / lease / result / Review / QA
→ validate_run.py
→ archive_run_to_agents.py（沉淀 Run 事实）
→ create_project_checkpoint.py（阶段性收口）
→ manage_run.py archive-run
```

### Emergency、快车道与自助派发

初始化时显式选择执行配置；`emergency` 可用于 Light/Standard/Strict，`fast` 可用于 Light/Standard，`strict` 必须使用 `normal` 或 `emergency`。Emergency 默认使用任务级 Preflight 和 capability pool：

Light/Standard 的低风险、本地可逆 Emergency 可以先依赖父任务 owned scope，不必先建立完整
Run scope freeze；Strict Emergency 和高风险动作仍必须先冻结 scope 并补齐对应门禁。

```bash
python3 scripts/init_run.py --project-root "<project-root>" \
  --coordination-mode coordinated --governance-root "<governance-home>" \
  --project-id "<project-id>" \
  --governance standard --execution-profile emergency --dispatch-policy self_service \
  --executor-policy capability_pool --executor-scale-authorized \
  --transport document_bus --objective "<objective>" \
  --versioning-mode not_applicable --versioning-reason "<reason>" --user-confirmed
python3 scripts/freeze_scope.py --run-dir "<run-dir>" \
  --requested-path "src" --target-environment local
python3 scripts/preflight_run.py --run-dir "<run-dir>"
```

父任务 Owner 或声明协作者可发布固定 Owner 子任务：

```bash
python3 scripts/agent_dispatch.py publish --run-dir "<run-dir>" \
  --publisher-agent worker --parent-task TASK-PARENT --task-id TASK-CHILD \
  --title "Child" --objective "..." --owner-agent worker --owned-path src/child \
  --role-ref frontend --required-capability frontend \
  --workspace "<project-root>/worktrees/TASK-CHILD" \
  --workspace-policy isolated_writer --release-lane none
```

任务池必须声明 `--owner-agent pool --assignment-mode claimable --eligible-agent <agent>`。
Coordinator 或父 Agent 只写 `TASK_READY`；eligible Agent 抢到后，脚本在 task-claim 锁内
写不可变 claim、`TASK_DISPATCHED` 和自己的唤醒包：

```bash
python3 scripts/agent_claim.py claim-task --run-dir "<run-dir>" \
  --task-id TASK-POOL --agent-id worker
python3 scripts/agent_claim.py claim-thread --run-dir "<run-dir>" \
  --task-id TASK-POOL --agent-id worker --thread-id THREAD-1 \
  --platform codex --session-id "<active-session>" --workspace "<executor-workspace>" \
  --executor-id "<executor-id>"
# 主动让出时追加不可变 release 记录（不会伪造完成或自动重置任务）
python3 scripts/agent_claim.py release-task --run-dir "<run-dir>" \
  --claim-ref "<claim-path>" --agent-id worker --reason "handoff complete"
python3 scripts/agent_claim.py release-thread --run-dir "<run-dir>" \
  --claim-ref "<thread-claim-path>" --agent-id worker --reason "thread released"
```

第二个抢占者不会覆盖第一个 claim，而是返回持有者、lease 到期时间、`blocked_by` 和下一
动作。任务 claim 解析为后续 ACK/lease/result 的有效 Owner；线程 claim 只绑定 thread、
platform 和精确 workspace。

若任务使用 capability pool，`write-ack`、`write-lease` 和 `write-result` 应传同一个
`--executor-id`；脚本与验证器会检查 task、principal、attempt 和 binding 一致。结果完成后
自动追加 executor release；lease 到期由下一次调度或分配 tick 追加不可变 expiry 记录。

完成前和超时恢复：

```bash
python3 scripts/completion_preflight.py --run-dir "<run-dir>" --task-id TASK-001
python3 scripts/recover_timeout.py --run-dir "<run-dir>" --task-id TASK-001 \
  --action block --side-effect-state unknown
python3 scripts/build_candidate_index.py --run-dir "<run-dir>"
```

这些脚本默认只读或写不可变证据，不自动制造 ACK、result、Review、QA、重试或发布许可。

### 版本化发布

```text
manage_run.py record-release-candidate
→ RELEASE_READY
→ validate_run.py --phase release
```

## 获取精确参数

```bash
python3 scripts/init_run.py --help
python3 scripts/manage_run.py --help
python3 scripts/manage_run.py <subcommand> --help
python3 scripts/emit_event.py --help
python3 scripts/validate_run.py --help
python3 scripts/init_project_agents.py --help
python3 scripts/bind_session.py --help
python3 scripts/sync_conversation.py --help
python3 scripts/create_checkpoint.py --help
python3 scripts/rebuild_index.py --help
python3 scripts/resume_brief.py --help
python3 scripts/validate_agents.py --help
python3 scripts/archive_run_to_agents.py --help
python3 scripts/create_project_checkpoint.py --help
python3 scripts/finalize_project.py --help
python3 scripts/manage_project_agents.py --help
python3 scripts/migrate_project_agents.py --help
python3 scripts/migrate_governance_storage.py --help
python3 scripts/coordinator.py --help
python3 scripts/preflight_run.py --help
python3 scripts/completion_preflight.py --help
python3 scripts/freeze_scope.py --help
python3 scripts/agent_dispatch.py --help
python3 scripts/agent_claim.py --help
python3 scripts/recover_timeout.py --help
python3 scripts/resource_queue.py --help
python3 scripts/build_candidate_index.py --help
python3 scripts/integration_policy.py --help
python3 scripts/integration_lane.py --help
python3 scripts/evidence_layers.py --help
python3 scripts/finalize_worktree.py --help
python3 scripts/migrate_run_optimization.py --help
python3 scripts/wake_agent.py --help
python3 scripts/runtime_metadata.py --help
python3 scripts/record_agent_runtime.py --help
python3 scripts/record_agent_activity.py --help
python3 scripts/migrate_agent_runtime.py --help
```

资源步骤在任务的 `resource_steps` 中声明 `step_id`、资源集合和可选 `queue_key`。调用
`resource_queue.py request` 时可以省略 `--queue-key`，脚本会按资源集合从任务声明推断；
真正取得 bundle 仍须用 `manage_run.py lock acquire --step-id ... --queue-key ...` 按 FIFO
校验；成功后会在 `locks/queue/grants/` 留下绑定 request/lock 的 grant 事实，不能只写入
排队请求就视为已获得资源。

`bind_session.py` 的 model/provider 必须来自本次运行的实际证据。默认配置只能作为 declared
policy；如果 actual 缺失或冲突，命令返回 `RUNTIME_METADATA_REQUIRED`，不会留下孤立 Runtime
Profile。精确定义见 [runtime-metadata.md](../references/runtime-metadata.md)。

## 维护约定

- 不绕过 `protocol_lib.py` 的 canonical path、SHA-256 和原子写入能力。
- 新事件必须同时更新状态机、actor/payload 校验、模板、规范和测试。
- 新 manifest/task 字段必须同步初始化器、管理器、事件器和验证器。
- 行为不兼容时递增协议版本，不静默解释旧 Run。
- 生成的 `__pycache__` 和 `.test-tmp` 不进入交付目录。
