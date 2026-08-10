# Project Checkpoints and Finalization

Project checkpoint 与 finalization 是 Coordinated 的开发治理收口，只写外部 Governance Home。它们不是网站构建、测试、部署或运行的前置条件。

## Project checkpoint

```bash
python3 scripts/create_project_checkpoint.py \
  --project-root "/absolute/project" \
  --governance-root "<governance-home>" \
  --project-id "<project-id>" \
  --run-id RUN-001
python3 scripts/rebuild_index.py \
  --project-root "/absolute/project" \
  --governance-root "<governance-home>" \
  --project-id "<project-id>"
```

命令从 binding 解析项目根，读取 TEAM、Agent CURRENT_CONTEXT 与最新 CP/handoff、Run manifest/state/summary、DECISIONS 和项目 Git 状态。它在 Governance Home `project-checkpoints/` 中创建链式不可变 `PCP-XXXX.md`，并原子更新外部 `CURRENT_PROJECT_CONTEXT.md`。

`--dry-run` 只验证并预览下一 ID。`.project-checkpoint.lock` 串行创建，历史 PCP 永不覆盖。

## Final project closure

```bash
python3 scripts/finalize_project.py \
  --project-root "/absolute/project" \
  --governance-root "<governance-home>" \
  --project-id "<project-id>" \
  --run-id RUN-001
```

Finalization fail-closed，直到：所有 Run 已完成/归档且已 Bridge；最新 PCP 精确覆盖选定 Run；确定性索引包含 PCP；任务终态安全；人工/发布门禁已批准；Agent 与 Run validator 通过。

通过后在 Governance Home 原子创建：

- `PROJECT_FINAL_REPORT.md`
- `AUDIT_MANIFEST.json`
- `ARTIFACT_INDEX.jsonl`

`--dry-run` 执行所有门禁但不写入。`.project-finalization.lock` 串行写入。完整已有 bundle 幂等返回；部分 bundle、不同 Run 集合或任意 source hash 漂移都拒绝覆盖。

Finalization 记录残余风险和审批，不默认接受风险，不授予项目发布权限。
