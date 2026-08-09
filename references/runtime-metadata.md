# Runtime Metadata Operations

本规范说明长期 Agent 的运行资料（Runtime Profile）、activity usage 和恢复时 runtime drift
如何采集、判定、持久化与验证。运行资料是审计证据，不是 Protocol v3 的任务状态真源。

## 1. 采集顺序：自动探测优先，缺失显式补充

按以下优先级收集，前一步已产生足够且无冲突的 actual evidence 时不再询问用户：

1. 调用方明确提供并已获准的 runtime context / CLI 字段。
2. 平台 API、signed session metadata 或显式配置的 external bridge 返回的结构化证据。
3. Agent 身份一致、hash/Schema 有效的 `SESSION_MAP.json` active binding。
4. 固定环境 allowlist 中的非秘密字段；只按键 `get`，禁止枚举环境。
5. Registry、project config 和安全默认值，仅作为 declared evidence。
6. 仍缺失或发生冲突时，要求用户/操作者显式补充或裁决。

自动探测不能猜测命令、平台、模型或 session。不得因为某二进制存在、目录名相似或上次使用
过某平台，就宣称本次运行资料已解析。

## 2. Actual 与 Declared

| claim kind | 含义 | 能否证明当前实际运行 |
| --- | --- | --- |
| `observed_actual` | 本次运行上下文、平台/桥接回执、有效 active session 等实际观测 | 可以，仍受 freshness/trust/conflict 约束 |
| `declared_default` | Registry、项目配置、默认模型、期望 provider/runtime | 不可以 |
| `derived` | 从已绑定证据确定性推导 | 只证明推导结果，不提升源证据等级 |
| `legacy_import` | 旧文档迁移值 | 只表示历史记录，不追溯伪造 actual |

declared 值可用于提示、预期对比和 config fingerprint，但不得覆盖 actual、消除 actual conflict，
也不得把 capture 从 partial/unresolved 提升为 complete。恢复报告必须分别展示 stored/declared 与
当前 actual；任一侧缺失时比较结果为 `unknown`，不能判为 same 或 changed。

## 3. 字段状态

- `known`：至少一个获准来源支持唯一值，并记录 confidence、selected source IDs 和说明。
- `unknown`：当前应采集，但获准来源未暴露、不可用、失败、证据不足、权限不足或为安全而脱敏；
  value 必须为 `null`，并记录 `U001`–`U007` reason code。
- `not_collected`：只用于历史/legacy 时点根本没有采集该字段（`U008_LEGACY_NOT_COLLECTED`）；
  不能用于掩盖当前探测失败或省略必采字段。
- `conflict`：两个或以上可信 actual 候选不一致；value 必须为 `null`，保留候选 ID 和来源，
  要求显式确认。禁止按数组顺序、默认值或较新但未验证的提示静默择一。

禁止用字符串 `"unknown"`、`"n/a"`、零值或虚构 session 填充未知字段。capture status 由字段
状态导出：全 known 为 complete；混合 known/unknown 为 partial；含 conflict 为 conflicted；全
unknown 为 unresolved；legacy import 使用 legacy_imported。

## 4. Token 与费用

Token/费用**不估算**。只有以下真实来源可写非空 usage：

- `provider_response`
- `runtime_meter`
- `billing_export`

记录必须绑定 `source_ref`、`source_sha256` 和 `reported_at`。不得通过字符数、词数、上下文窗口、
模型标价、运行时长或历史平均值反推 Token/费用。没有真实回执时，各 token 字段、
`cost_minor_units`、`currency` 保持 `null`，`usage_source` 使用 `unavailable`；确实不需要采集时
使用 `none_required`。费用存在时必须同时记录三字母 currency。

## 5. Secret 禁区

允许持久化的资料限于必要的非秘密身份、来源摘要、hash、状态和审计引用。禁止采集或保存：

- 全量环境快照、未知环境键；
- prompt、完整对话原文（除受独立归档/脱敏协议治理者）、原始命令输入/输出；
- API/access/refresh/session token、Authorization、Cookie、密码、凭据、私钥、签名、webhook；
- 数据库 URI、带 query/fragment 的 URL，或 secret-shaped session/profile/model/provider 值。

入口先执行字段名和高置信 secret 扫描，写盘前再扫描最终序列化字节。命中即 fail-closed；异常
只返回非敏感错误码。冲突记录只保留 source/candidate 元数据，不回显被拒绝或互相冲突的原值。
hash 不能作为“可以保存秘密”的理由。

## 6. 写入与验证

```bash
python3 scripts/record_agent_runtime.py \
  --project-root /absolute/project \
  --agent-id A01-coordinator \
  [--model MODEL] [--provider PROVIDER] [--platform PLATFORM] \
  [--session-id SESSION] [--profile PROFILE] \
  [--workspace /absolute/project] [--runtime-kind KIND]

python3 scripts/record_agent_activity.py --help
python3 scripts/validate_agents.py --project-root /absolute/project
```

Runtime Profile 使用不可变 `RP-NNNNNN` 快照、hash chain、`CURRENT_RUNTIME.json` 指针和
`RUNTIME_INDEX.jsonl`。新证据产生新 profile；不得编辑旧 profile。Activity 按
Run/Task/Attempt/Agent 分区形成不可变链，并绑定 runtime profile、来源和 evidence。

## 7. Bridge 到最终收口

完成 Run 的长期沉淀顺序固定如下：

1. **Bridge**：`archive_run_to_agents.py` 验证 Run 后，将 task/result/evidence 按原字节镜像并
   生成 hash-bound bridge manifest。
2. **PCP**：`create_project_checkpoint.py` 创建不可变项目 checkpoint，绑定 Agent、Run、决策、
   handoff、runtime/activity 视图和 live Git 来源。
3. **index**：`rebuild_index.py` 确定性重建项目与 Agent 索引。
4. **validator**：`validate_agents.py` 校验 Schema、hash chain、引用、索引、DAG/owner/冲突和
   secret 门禁；Run validator 继续校验 completion/release。
5. **finalize**：`finalize_project.py` 只在前置门禁全部满足时生成最终报告、audit manifest 和
   artifact index；它不会自动修复或补齐 Bridge、PCP、index、runtime 或证据。

任一步失败都先修复事实或协议资料再重试，不越过 validator 直接 finalize。

## 8. 远程 Adapter 与真实 Session 唤醒边界

Hermes/Codex adapter 只调用操作者显式配置的外部 CLI/API bridge。调用前必须验证
`SESSION_MAP.json` 的 stable agent identity、目标 platform、真实 active session ID 和精确 project
workspace；不得自动发现/猜测 bridge 命令或复用不匹配 session。

- `message_sent` 只表示显式 bridge 命令退出 0，或 document invocation package 已写入；不表示
  目标已 ACK、已运行或已完成。
- ACK、lease、result、Review、QA 仍必须由真实文档/evidence/event 驱动。
- 未配置、session 校验失败或外部投递失败时，回退 document adapter，持久化完整 invocation
  package 并报告 `fallback_document`；这也只证明 package 已写入。
- 无外部执行器消费 package 时，必须提供下一条可复制调用命令，不得宣称自动执行。
