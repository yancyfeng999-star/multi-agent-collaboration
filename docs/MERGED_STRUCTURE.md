# Direct/Coordinated 架构与数据边界

本文描述 Multi-Agent Collaboration 的当前实现结构。规范入口是 [SKILL.md](../SKILL.md)；字段与操作细节分别以 [references/](../references/README.md)、[assets/](../assets/README.md) 和 [scripts/](../scripts/README.md) 为准。

## 1. 用户入口与路由

`agents.html` 是静态 Agent 角色目录，默认生成 Direct 人工启动指令。它不读取 Run、不显示实时状态、不创建 Agent/线程、不自动编排、不写入项目或 Governance Home。

```text
用户目标
├── 单 Agent 可完成 → Direct → 只修改项目业务交付
└── 多 Agent/交接/门禁/恢复 → Coordinated → 外部 Governance Home
```

Direct 默认不创建治理资料。Coordinated 只在有真实需求时启用，不作为网站更新的默认前置。

## 2. 物理分离

### 目标项目

```text
project-root/
├── 业务源码
├── 测试
├── 构建/启动/部署配置
├── 项目自身版本权威源
└── 必要交付文档
```

Skill 不自动创建或修改目标项目 `AGENTS.md`。网站构建、测试、部署和线上运行对 Governance Home 零依赖。

### Governance Home

```text
~/.codex/governance/multi-agent-collaboration/
└── projects/<project-key>/
    ├── project-binding.yaml
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
    ├── agents/<agent-id>/
    │   ├── AGENT_PROFILE.json
    │   ├── ROLE.md
    │   ├── SYSTEM_PROMPT.md
    │   ├── CHECKLIST.md
    │   ├── runtime/
    │   ├── activity/
    │   ├── conversations/
    │   ├── tasks/
    │   ├── handoffs/
    │   └── artifacts/
    ├── runs/<run-id>/
    │   ├── agents.yaml
    │   ├── manifest.yaml
    │   ├── state.yaml
    │   ├── tasks/
    │   ├── events/
    │   ├── inbox/
    │   ├── outbox/
    │   ├── evidence/
    │   ├── locks/
    │   ├── claims/
    │   ├── dead-letter/
    │   ├── delegations/
    │   ├── native/
    │   ├── versions/
    │   └── archive/
    ├── bridges/
    ├── project-checkpoints/
    └── migrations/
```

binding 是治理层到真实项目的唯一身份入口。治理根和项目根不能嵌套。

## 3. 两层事实模型

### Protocol v3 Run 层

负责单次正式执行：冻结任务、不可变事件、ACK/lease、锁、attempt、result、evidence、Review、QA、人工门禁、版本合同和 Release Candidate。

### 长期 Agent 层

负责跨 Run 连续性：稳定身份、Runtime Profile、Activity Ledger、对话 archive、checkpoint、handoff、恢复包、Bridge、项目 checkpoint、索引和最终审计。

两层同时使用时，**Run 是执行状态真源**。长期层只沉淀通过校验的引用和快照。

## 4. 运行资料三层

| 层 | 权威文件 | 内容 |
| --- | --- | --- |
| 长期身份 | `AGENT_PROFILE.json` | 稳定 Agent ID、角色版本、ROLE hash、declared policy |
| 单次运行 | `RP-NNNNNN.json` | actual model/provider/platform/session/workspace、来源、状态和 hash chain |
| 单次尝试 | `ACTIVITY-NNNNNN.json` | Run/Task/Attempt、Runtime 引用、状态、证据和真实 usage receipt |

declared default 不能证明本次实际运行。actual 缺失、未采集和冲突必须保持显式状态，不用默认值补齐。

## 5. 事务、不变性与安全

- 持久记录不可静默覆盖，连续记录使用顺序 ID、前驱 hash 和 append-only index。
- 同目录发布使用临时文件、fsync、原子替换和跨平台排他锁。
- 迁移与 repair 使用 dry-run、plan hash、备份/暂存、apply 门禁和回滚。
- 项目产物路径限制在 binding allowed roots；治理路径限制在 governance project。
- 只逐键读取固定环境 allowlist，不枚举完整环境。
- API key、token、Authorization、Cookie、密码、私钥和 Secret-shaped 值写前写后均 fail-closed。

## 6. Run 到长期层收口

```text
Protocol v3 completion gate
→ archive_run_to_agents.py
→ create_project_checkpoint.py
→ rebuild_index.py
→ validate_agents.py
→ finalize_project.py
```

任何一步失败都必须修复事实或引用后重试，不让 finalization 自动补齐。Candidate Index 只读且不授权发布。

## 7. 恢复

1. 校验 binding 与真实项目根。
2. 读 TEAM、AGENT_PROFILE、ROLE 与 CURRENT_CONTEXT。
3. 读最新 checkpoint/handoff 与当前 Run state。
4. 校验 Runtime Profile、Activity 与引用 hash。
5. 对比项目 Git、文件、session 和 runtime，记录 drift 后再继续。

## 8. 验证入口

```bash
python3 scripts/validate_run.py "<governance-project>/runs/<run-id>"
python3 scripts/validate_agents.py \
  --project-root "<project-root>" \
  --governance-root "<governance-home>" \
  --project-id "<project-id>"
```
