# 外置治理存储协议

## 1. 存储边界

Direct 默认不创建治理资料。Coordinated 将所有 Agent、Run、handoff、checkpoint、Bridge、candidate 和审计资料保存到项目外 Governance Home。

默认根：

```text
~/.codex/governance/multi-agent-collaboration/
```

网站构建、启动、测试、部署和线上运行不得读取 Governance Home。Skill 不自动创建或修改目标项目 `AGENTS.md`。

## 2. 项目绑定

```text
<governance-home>/projects/<project-key>/project-binding.yaml
```

binding 使用 Governance Storage Schema `1.0`，必须包含：

- `storage_schema`
- `project_id`
- `project_name`
- `project_root`
- `project_key`
- `allowed_roots`（只能是规范化 `project_root`）
- `created_at`

治理根与项目根不能重叠；任意一方位于另一方内都 fail-closed。同一 `project_id` 对应多个不同项目根时，使用稳定 path hash 后缀避免覆盖。

## 3. 治理项目布局

```text
projects/<project-key>/
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
│   ├── ROLE.md
│   ├── SYSTEM_PROMPT.md
│   ├── CHECKLIST.md
│   ├── AGENT_PROFILE.json
│   ├── runtime/
│   ├── activity/
│   ├── conversations/
│   ├── tasks/
│   ├── handoffs/
│   └── artifacts/
├── runs/<run-id>/
├── bridges/
├── project-checkpoints/
└── migrations/
```

Run 保存执行事实；Agents 保存跨 Run 身份和恢复资料。长期层不得覆盖 Run 状态。

## 4. 写入所有权

- binding 和初始身份：初始化器。
- Run 任务、事件和派生状态：Protocol v3 唯一写入者规则。
- Agent 长期资料：对应 Agent 或专用事务脚本。
- Bridge、PCP、final audit 和 migration manifest：对应专用脚本。

不可变 archive、checkpoint、handoff、Runtime Profile、Activity、Bridge、PCP 和 final audit 不得覆盖。更正必须新建记录并保留替代关系。

## 5. 路径与安全

- 项目产物引用限制在 binding `allowed_roots`。
- 治理引用限制在当前 governance project。
- 拒绝 `..`、symlink escape、特殊文件、未授权绝对路径和重叠根。
- 不落盘 API Key、Token、Cookie、密码、私钥、支付数据或未脱敏个人数据。
- 会话映射只保存平台、会话 ID、profile、workspace 和同步游标等恢复线索。

## 6. Legacy 迁移

项目内 `.multi-agent-collaboration/` 仅作旧版只读来源。`migrate_governance_storage.py --dry-run` 生成清单、目标和 SHA-256；`--apply` 复制到 staging，逐文件验证后原子发布。源目录不删除、不改写。
