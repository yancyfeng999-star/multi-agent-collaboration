# 跨平台恢复协议

任何 Hermes、Codex、Claude Code、其他文件型 Agent 或人工接管者都按同一顺序恢复。恢复依据是项目内可移植文件，不要求平台私有会话数据库可用：

1. 确认项目根目录。
2. 读取项目 `AGENTS.md`（若存在）、`.multi-agent-collaboration/PROTOCOL.md` 和 `TEAM.yaml`。
3. 确认 Agent ID，读取自己的 `ROLE.md`、`SYSTEM_PROMPT.md`。
4. 读取 `conversations/CURRENT_CONTEXT.md` 和其 `latest_checkpoint`。
5. 当前任务优先使用 `CURRENT_CONTEXT` 的 `active_task`；未设置时，从 Run `state.yaml` 的活动状态中选择，并按任务 frontmatter `created_at` 判定最新。最近交接与无显式指针的 checkpoint 也按 `created_at`，禁止按文件名排序猜测。
6. 检查实际项目路径、Git HEAD、dirty files、引用存在性与 checkpoint 保存的引用 SHA-256。
7. 汇报身份、任务、决策、待办、下一步及漂移后再修改。

`SESSION_MAP.json` 保存 active/history 平台会话映射，但会话 ID 只是可选恢复线索。文件缺失、会话失效或平台数据库不可用时，直接使用 `CURRENT_CONTEXT`、checkpoint、任务、交接、Run 状态与 Git 恢复；不得因此覆盖历史或中止恢复。

## 漂移

恢复包 frontmatter 包含机器可读 `drift` 对象：

- `checked`、`detected`
- `git.available`、预期/实际 HEAD、预期/实际 dirty files 及匹配结果
- `missing_references`
- `hash_mismatches`（路径、预期/实际 SHA-256）
- `project_path`（预期、实际、是否匹配）

检查点可用 `git_head`（或 `git_commit`）、`dirty_files`、`project_root`、`referenced_files`、`reference_hashes` 描述恢复基线；`*_ref` 与对应 `*_ref_sha256` 也会自动参与引用检查。

检查点与实际状态不一致时，停止自动覆盖：记录涉及文件、预期/实际状态和来源；查阅后续原文、Git 和外部事实；由 Coordinator 决定是否更正上下文、重派或保留变更。

## 最小恢复包

生成并实际检测漂移：

```bash
python3 scripts/resume_brief.py \
  --project-root "<root>" \
  --agent-id "<agent-id>" \
  --detect-drift
```

在 CI 或恢复门禁中发现漂移时以退出码 `2` 失败：

```bash
python3 scripts/resume_brief.py \
  --project-root "<root>" \
  --agent-id "<agent-id>" \
  --fail-on-drift
```

`--fail-on-drift` 隐含执行漂移检查。未指定 `--output` 时写入该 Agent 的 `conversations/RESUME_BRIEF.md`。显式输出默认只能位于项目根内；确需导出到项目外时，必须逐个声明安全目录：

```bash
python3 scripts/resume_brief.py \
  --project-root "<root>" \
  --agent-id "<agent-id>" \
  --output "/approved/export/resume.md" \
  --safe-output-dir "/approved/export"
```

恢复包只包含身份、checkpoint、当前任务、最近交接、必读路径和漂移摘要，不复制全部历史。

## 恢复完成门禁

恢复汇报必须说明：Agent ID、项目根、当前任务及选择来源、最新 CP、确认决策、未完成事项、实际状态是否一致、漂移/冲突/原文缺口。实际文件和真实验证优先，禁止仅凭摘要宣称完成。
