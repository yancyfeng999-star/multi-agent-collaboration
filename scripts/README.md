# Scripts 使用说明

脚本实现协议 v3 的本地文档总线。所有命令只应在用户确认的项目范围内执行。

## 核心脚本

| 脚本 | 职责 |
| --- | --- |
| `init_run.py` | 用户确认后初始化项目总线、Run、版本合同和初始门禁 |
| `manage_run.py` | 创建 Agent/任务/证据/RC，管理 ACK、lease、锁、恢复和归档 |
| `emit_event.py` | 校验并原子写入事件，然后重建派生状态 |
| `validate_run.py` | 对 structure、dispatch、completion 或 release 执行 fail-closed 校验 |
| `protocol_lib.py` | YAML 子集、路径、hash、状态机和运行文档的共享实现 |

## Agent 管理脚本

| 脚本 | 职责 |
| --- | --- |
| `init_project_agents.py` | 初始化项目 Agent 结构、身份文件及项目内 schemas/templates |
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
| `coordinator.py` | 执行有界单 tick 的 ready-wave、超时与投递协调 |
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
python3 scripts/coordinator.py --help
python3 scripts/wake_agent.py --help
python3 scripts/runtime_metadata.py --help
python3 scripts/record_agent_runtime.py --help
python3 scripts/record_agent_activity.py --help
python3 scripts/migrate_agent_runtime.py --help
```

`bind_session.py` 的 model/provider 必须来自本次运行的实际证据。默认配置只能作为 declared
policy；如果 actual 缺失或冲突，命令返回 `RUNTIME_METADATA_REQUIRED`，不会留下孤立 Runtime
Profile。精确定义见 [runtime-metadata.md](../references/runtime-metadata.md)。

## 维护约定

- 不绕过 `protocol_lib.py` 的 canonical path、SHA-256 和原子写入能力。
- 新事件必须同时更新状态机、actor/payload 校验、模板、规范和测试。
- 新 manifest/task 字段必须同步初始化器、管理器、事件器和验证器。
- 行为不兼容时递增协议版本，不静默解释旧 Run。
- 生成的 `__pycache__` 和 `.test-tmp` 不进入交付目录。
