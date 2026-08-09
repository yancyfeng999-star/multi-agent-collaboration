# v1.3.0 合并架构与数据边界

本文描述 Multi-Agent Collaboration v1.3.0 的**当前实现结构**。规范入口是
[SKILL.md](../SKILL.md)；字段和操作细节分别以 [references/](../references/README.md)、
[assets/](../assets/README.md) 和 [scripts/](../scripts/README.md) 为准。

## 0. 用户入口层

`agents.html` 是默认的用户入口，负责展示稳定角色、适用场景和启动边界。用户填写项目、
目标、范围和验收标准后自行复制启动指令；页面不读取 Run、不显示当前任务或运行状态，也
不创建线程、增加 Agent 或自动编排。需要并行、正式证据、跨会话恢复或高风险治理时，才
进入下方 Protocol v3 Run / 长期 Agent 层。

项目专属角色以 `TEAM.yaml`、`AGENT_PROFILE.json` 和 `ROLE.md` 为真源；Profile 的 `catalog`
只保存稳定目录投影，不保存 session、model、provider、lease 或其他运行事实。

## 1. 两层事实模型

### Protocol v3 Run 层

负责单次正式执行的事实：

- 冻结任务和依赖；
- 不可变事件；
- ACK、lease、锁、attempt、result 和 evidence；
- Review、QA、人工门禁、版本合同和 Release Candidate；
- Run 状态重放与 completion/release 验证。

### 长期 Agent 层

负责跨 Run、跨会话的连续性：

- 稳定 Agent 身份和角色版本；
- Session Runtime Profile；
- Task Attempt Activity Ledger；
- 对话 archive、checkpoint、handoff 和恢复包；
- Run→Agent bridge、项目 checkpoint、索引和最终审计包。

两层同时使用时，**Run 层是执行状态真源**。长期层只沉淀通过校验的引用和快照，不创建第二套
任务状态机。

## 2. 项目目录

```text
project-root/
├── AGENTS.md
└── .multi-agent-collaboration/
    ├── protocol.yaml
    ├── project.yaml
    ├── current-run
    ├── TEAM.yaml
    ├── PROTOCOL.md
    ├── CURRENT_PROJECT_CONTEXT.md
    ├── DECISIONS.md
    ├── INDEX.md
    ├── index.jsonl
    ├── schemas/
    ├── templates/
    ├── agents/
    │   └── <agent-id>/
    │       ├── AGENT_PROFILE.json
    │       ├── ROLE.md
    │       ├── SYSTEM_PROMPT.md
    │       ├── CHECKLIST.md
    │       ├── runtime/
    │       │   ├── profiles/RP-NNNNNN.json
    │       │   ├── RUNTIME_INDEX.jsonl
    │       │   ├── CURRENT_RUNTIME.json
    │       │   └── logs/YYYY/MM/DD/ACTIVITY-NNNNNN.json
    │       ├── conversations/
    │       │   ├── SESSION_MAP.json
    │       │   ├── archive/
    │       │   ├── checkpoints/
    │       │   ├── CURRENT_CONTEXT.md
    │       │   └── INDEX.md
    │       ├── tasks/
    │       ├── handoffs/
    │       └── artifacts/
    ├── project-checkpoints/
    ├── bridges/
    ├── finalization/
    └── runs/<run-id>/
        ├── agents.yaml
        ├── manifest.yaml
        ├── state.yaml
        ├── tasks/
        ├── events/
        ├── inbox/
        ├── outbox/
        ├── evidence/
        ├── locks/
        ├── dead-letter/
        ├── delegations/
        ├── native/
        ├── versions/
        └── archive/
```

实际目录会按需延迟创建。例如 `runtime/profiles/` 只在首次拥有可信 actual runtime evidence 时
创建，避免空目录或 declared policy 被误认为已观测运行事实。

## 3. 运行资料三层结构

| 层 | 权威文件 | 保存内容 |
| --- | --- | --- |
| 长期身份 | `AGENT_PROFILE.json` | 稳定 Agent ID、角色版本、ROLE hash、declared runtime policy |
| 单次运行 | `RP-NNNNNN.json` | actual model/provider/platform/session/workspace、来源、状态、冲突和 hash chain |
| 单次尝试 | `ACTIVITY-NNNNNN.json` | Run/Task/Attempt、Runtime 引用、状态、证据和真实 usage receipt |

`declared_default` 不能证明本次实际运行。actual 缺失、历史未采集和可信来源冲突分别使用
`unknown`、`not_collected` 和 `conflict`；不得用默认值、字符串占位或估算数据补齐。

## 4. 事务、不变性与安全

- Runtime Profile、Activity、archive、checkpoint、handoff、PCP 和 final audit 均不可静默覆盖。
- 连续记录使用顺序 ID、前驱 hash 和 append-only index。
- Session Map 与新 Runtime Profile 共享事务回滚边界；Session 发布失败不会留下孤立 Profile。
- 同目录写入使用临时文件、fsync 和原子替换；并发写使用跨平台排他锁。
- 迁移和 repair 使用 dry-run、plan hash、备份、apply 门禁和故障回滚。
- 路径必须限制在项目/Agent 根目录内，拒绝绝对路径污染、`..` 和 symlink escape。
- 只逐键读取固定环境 allowlist，禁止枚举或保存完整环境。
- Token 和费用只接受 provider response、runtime meter 或 billing export 的真实回执，不估算。
- API key、token、Authorization、Cookie、密码、私钥和 Secret-shaped 值写前写后均 fail-closed。

## 5. Run 到长期层的收口顺序

```text
Protocol v3 completion gate
→ archive_run_to_agents.py        # Bridge
→ create_project_checkpoint.py    # PCP
→ rebuild_index.py                # deterministic index
→ validate_agents.py              # schema/hash/reference/secret closure
→ finalize_project.py             # immutable final audit bundle
```

任何一步失败都必须修复事实或引用后重试，不能让 `finalize` 自动补齐前置资料。可变指针
`CURRENT_PROJECT_CONTEXT.md` 不进入历史 PCP 的永久 source hash。

## 6. 恢复顺序

1. 确认项目根目录和 Agent ID；
2. 读取 `PROTOCOL.md`、`TEAM.yaml`、`AGENT_PROFILE.json` 和 `ROLE.md`；
3. 读取 `CURRENT_CONTEXT.md` 与最新 checkpoint；
4. 校验 Runtime Profile、Activity 和引用 hash；
5. 对比实际 Git、文件、session 和 runtime，记录 drift；
6. 读取当前 Run 的真实 state/event/evidence；
7. 汇报恢复结果后再继续执行。

平台 Session ID 只是恢复线索，项目目录中的哈希绑定资料才是可移植长期真源。

## 7. 适配器边界

Hermes/Codex adapter 只调用操作者显式配置的外部 bridge。必须先验证 stable Agent identity、平台、
真实 active session 和精确 workspace。bridge 退出 0 只表示投递命令成功，不表示 ACK、运行或完成。
不支持或校验失败时，系统写入真实 document invocation package 并报告 `fallback_document`。

## 8. 验证入口

```bash
python3 scripts/validate_run.py "<run-dir>"
python3 scripts/validate_agents.py --project-root "<project-root>"
python3 -m pytest -q
```

完整发布门禁见 [tests/README.md](../tests/README.md)，运行资料规范见
[runtime-metadata.md](../references/runtime-metadata.md)。
