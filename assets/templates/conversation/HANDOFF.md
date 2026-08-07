---
schema_version: "1.1"
doc_type: handoff
task_id: "{task_id}"
agent_id: "{agent_id}"
status: "{completed|blocked|failed}"
created_at: "{created_at}"
runtime_profile_id: "{runtime_profile_id}"
runtime_profile_sha256: "{runtime_profile_sha256}"
activity_record_path: "{activity_record_path}"
activity_record_sha256: "{activity_record_sha256}"
actual_model_status: "{actual_model_status}"
actual_model: {actual_model}
actual_provider_status: "{actual_provider_status}"
actual_provider: {actual_provider}
usage_summary: {usage_summary}
---

# 交接文档

## 任务信息

| 字段 | 值 |
|------|-----|
| 任务 ID | {task_id} |
| Agent ID | {agent_id} |
| 状态 | {status} |
| 创建时间 | {created_at} |

## 运行资料引用

| 字段 | 值 |
|------|-----|
| Runtime Profile ID | {runtime_profile_id} |
| Runtime Profile SHA-256 | {runtime_profile_sha256} |
| Activity Record 路径 | {activity_record_path} |
| Activity Record SHA-256 | {activity_record_sha256} |
| 实际模型状态 | {actual_model_status} |
| 实际模型 | {actual_model} |
| 实际 Provider 状态 | {actual_provider_status} |
| 实际 Provider | {actual_provider} |

> `known` 状态必须提供已观测的实际值；`unknown`、`not_collected` 或 `conflict` 必须使用 `null`，不得用默认配置冒充实际值。

## Usage 摘要引用

{usage_summary}

> Token/费用仅可引用真实回执。`unavailable` 或 `none_required` 时，数值、币种和来源引用必须全部为 `null`，不得以 `0` 冒充实际统计。

## 任务总结

{summary}

## 根本原因或决策

{root_cause_or_decision}

## 修改文件

{changed_files}

## 未修改的边界

{untouched_boundaries}

## 执行的命令

{commands_run}

## 命令的精确结果

{exact_results}

## 验收证据

{acceptance_evidence}

## 产物

{artifacts}

## 风险

{risks}

## 未解决事项

{unresolved}

## 请求的合同变更

{contract_changes_requested}

## 建议的下一个 Owner

{recommended_next_owner}

## 回滚说明

{rollback_note}
