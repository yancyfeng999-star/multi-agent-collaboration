# 跨平台恢复协议

## 适用边界

Direct 没有持久治理状态，恢复依赖当前项目、Git 和用户上下文。Coordinated 恢复依赖项目外 Governance Home 中的可验证文档，不要求平台私有会话数据库可用。

## 恢复顺序

1. 确认真实项目根和 Governance Home。
2. 读取 `project-binding.yaml`，校验 `project_root`、`project_id` 和 `allowed_roots`。
3. 读取 Governance Home 中的 `PROTOCOL.md`、`TEAM.yaml` 和 `CURRENT_PROJECT_CONTEXT.md`。目标项目的 `AGENTS.md` 只在它原本存在时作为项目级指令读取；Skill 不创建或修改它。
4. 确认 Agent ID，读取 `ROLE.md`、`SYSTEM_PROMPT.md`、`CURRENT_CONTEXT.md` 和 `latest_checkpoint`。
5. 活动任务优先使用 CURRENT_CONTEXT 显式指针；否则从 Run `state.yaml` 与任务 `created_at` 选择，不按文件名猜测。
6. 对比真实项目路径、Git HEAD、dirty files、引用存在性和 SHA-256。
7. 汇报身份、任务、决策、待办、下一步和漂移后再继续写入。

`SESSION_MAP.json` 只是可选恢复线索。会话失效时，直接使用 checkpoint、task、handoff、Run 状态和 Git，不覆盖历史。

## 漂移

恢复包的 `drift` 必须表达：检查状态、预期/实际 Git HEAD、dirty files、缺失引用、hash 不匹配与项目路径匹配。出现漂移时停止自动覆盖，保留预期/实际证据，由 Coordinator 决定更正上下文、重派或保留变更。

## CLI

```bash
python3 scripts/resume_brief.py \
  --project-root "<root>" \
  --governance-root "<governance-home>" \
  --project-id "<project-id>" \
  --agent-id "<agent-id>" \
  --detect-drift
```

CI 门禁可使用 `--fail-on-drift`，发现漂移返回 `2`。默认输出到当前 Agent 的 Governance Home 目录。显式输出只能位于项目根、Agent 治理根或 `--safe-output-dir`。

恢复包只包含身份、checkpoint、当前任务、最近交接、必读路径和漂移摘要，不复制全部历史。
