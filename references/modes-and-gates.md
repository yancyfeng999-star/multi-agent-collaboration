# 治理模式和人工门禁

## 1. Light

适用：

- 调研、方案、文档、内容整理。
- 不修改运行时代码或外部系统。

必须：

- 任务文档。
- Agent Registry。
- 事件和结果。
- 完成总结。
- Run 内用户确认记录。
- task、ACK 和 result 的精确 payload hash。

可选：

- Git commit。
- Review 和 QA。

## 2. Standard

适用：

- 日常代码修改、测试、重构和本地工具。

除 Light 外必须：

- Git 状态和分支记录。
- owned/forbidden paths。
- implementation commit 或明确未提交原因。
- reviewer、QA、验证结果。
- 冲突文件锁。
- 风险和回滚说明。

可执行门禁：

- `TASK_READY` 前 Reviewer 和 QA 必须是 Run Registry 中的真实 Agent；两项职责可以由
  同一个质量智能体承担，但不能由任务 Owner 自审。
- `TASK_COMPLETED` 必须经过 `REVIEW_APPROVED` 和 `QA_PASSED`。
- result 的 verification 必须为 passed。
- result 必须记录 implementation commit 或明确未提交原因。
- 风险、回滚和验证引用必须写入不可变 result/evidence。

## 3. Strict

适用：

- 生产项目。
- 数据库、migration、支付、资金、积分、权限、密钥。
- worker、queue、storage、外部 provider、发布和回滚。

除 Standard 外必须：

- 变更编号。
- 正式 handoff。
- 项目已有的 change registry 或等价账本。
- Review / Security。
- 数据库、环境变量、外部服务和数据影响。
- 发布许可。
- 人工确认。
- 回滚和恢复步骤。
- 发布前干净工作区。

可执行门禁：

- dispatch 前 manifest 必须有 change id，以及 registry、Git 状态、环境影响、回滚和安全
  审查的有效文件引用。
- dispatch 时必须处于可访问、非 detached 的项目 Git worktree；manifest `git_branch`
  必须等于当前分支，且除文档总线外的工作区必须真实干净。
- 风险 flag 必须映射到 task 的已批准 gate，且批准时间不晚于 `TASK_READY`。
- completion 必须引用当前 `git_branch` 可达的真实 Git commit；result 中每个
  `changed_file` 都必须出现在该 commit，且 Strict 结果不得省略 changed files。
- release 必须有目标环境、批准的 release gate、发布许可和干净工作区证据；验证器同时
  实时检查除 `.multi-agent-collaboration/` 外的工作区状态。
- release 必须使用 `tracked` 项目版本治理，存在不可变 RC，最新 RC commit 与发布任务
  结果一致，且版本权威源已经写入预留目标版本。

## 4. 人工门禁

以下事件必须停止自动调度并进入 `WAITING_USER_APPROVAL`：

- 第一次创建多个 Codex 线程。
- 扩大用户已确认的修改范围。
- 修改数据库 schema 或执行 migration。
- 使用生产或真实资金凭据。
- 操作生产数据库、用户数据或对象存储。
- SSH、上传、部署、重启、回滚和删除。
- 修改发布许可。
- 覆盖他人未提交改动。
- 高冲突文件所有权不明确。
- Review 或 QA 发现阻塞问题但修复会改变业务规则。
- 首次把通用智能体内部子代理升级为受管子代理，或提高默认委派深度。

批准或拒绝必须生成新的不可变 `human_gate` 文件，并分别写
`APPROVAL_GRANTED` / `APPROVAL_REJECTED`。批准后任务回到 `ready`，重新执行依赖、权限、
锁和并行额度检查；不直接回到 running。

`manage_run.py record-gate` 写 `approved` 或 `rejected` 时必须显式传
`--human-confirmed`；缺少该参数时拒绝落盘。这个参数只用于记录已经发生的人工决定，
不能把自动化判断冒充成人工授权。

受管子代理继承当前 run 的治理模式，不能通过委派从 Strict 降为 Standard 或 Light，也
不能继承父智能体的生产凭据和人工许可。

## 5. 发布门禁

Release 只能接受：

- 任务和变更编号可追踪。
- implementation commit 真实存在。
- handoff 和 registry 一致。
- Review、QA 和必需验证通过。
- 数据库、环境变量、外部依赖和回滚清楚。
- 用户对目标环境明确授权。

文档存在不代表允许发布。`local_only`、blocked、forbidden、superseded、unconfirmed 一律不是发布候选。
