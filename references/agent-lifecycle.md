# Agent 生命周期与存储迁移

长期 Agent 以 Governance Home 中 `TEAM.yaml` 的 `agent_id` 为稳定身份。生命周期工具只修改项目外 `<governance-root>/projects/<project-key>/`，并与初始化/修复/迁移共用 `.init.lock`，避免并发读改写破坏 TEAM。目标项目的构建、测试、部署和运行不读取这些资料。

## 生命周期命令

```bash
python3 scripts/manage_project_agents.py list --project-root <project>
python3 scripts/manage_project_agents.py add --project-root <project> \
  --agent-id A03-qa --role-name quality --domain tests
python3 scripts/manage_project_agents.py update --project-root <project> \
  --agent-id A03-qa --role-name release-quality --domain release
python3 scripts/manage_project_agents.py pause --project-root <project> \
  --agent-id A03-qa --reason maintenance
python3 scripts/manage_project_agents.py resume --project-root <project> --agent-id A03-qa
python3 scripts/manage_project_agents.py retire --project-root <project> \
  --agent-id A03-qa --reason reorganization
python3 scripts/manage_project_agents.py repair --project-root <project> --agent-id A03-qa
```

### 不变量

- `agent_id` 创建后不可变；工具拒绝 `update --new-agent-id`，不会按岗位静默生成新 ID 或重命名目录。
- 转岗修改 `role_name`/`domain`，旧岗位追加到 `role_history`。当前 `ROLE.md` 可更新，但历史记录不被覆盖。
- 状态为 `active`、`paused`、`retired`。状态变化追加到 `status_history`；退役不可恢复，如需新身份应显式 `add`。
- `pause` 和 `retire` 不移动或删除 Agent 目录。
- `repair` 只补齐缺失目录和脚手架文件；已存在的 `ROLE.md`、会话、archive、checkpoint 或其他历史一律不覆盖。
- 新增、更新和状态切换通过原子替换写 TEAM。失败时旧 TEAM 保持有效。

## 存储 Schema 迁移

当前 Agent 内部存储 schema 为 `1.1`，写入 Governance Home 项目目录下的 `STORAGE.json`：

```bash
# 只查看计划，不写文件、不创建备份
python3 scripts/migrate_project_agents.py --project-root <project> --dry-run

# 执行事务迁移
python3 scripts/migrate_project_agents.py --project-root <project>
```

非默认治理根目录需补充 `--governance-root <path>`；有多个绑定时再补充 `--project-id <id>`。

迁移行为：

1. 读取并验证当前存储状态；同版本再次执行输出 `already`，不重复迁移。
2. 在同一文件系统建立临时完整备份。
3. 原子更新 TEAM 生命周期元数据并创建 `STORAGE.json`。
4. 校验所有 `archive` 与 `checkpoint(s)` 文件仍存在且 SHA-256 不变。
5. 成功后在 `migrations/<timestamp>-to-<version>/` 保存 `manifest.json` 和迁移前 `backup/`。manifest 记录迁移前后文件校验和。
6. 任一步失败即删除部分新状态并从临时备份恢复整个存储；失败迁移不会留下 `STORAGE.json` 或半更新 TEAM。

备份目录也是历史，只能保留或复制，不应由生命周期/迁移工具清理。`AGENT_MIGRATION_FAIL_AFTER` 仅用于自动化故障注入测试，不是用户接口。
