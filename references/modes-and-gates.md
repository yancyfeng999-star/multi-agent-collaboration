# 治理模式和人工门禁

## 0. 执行配置与派发策略

治理模式决定证据和人工门禁；执行配置决定等待和交接策略，二者不能互相替代：

| 字段 | 值 | 规则 |
| --- | --- | --- |
| `execution_profile` | `fast` | Light/Standard；一次 dispatch preflight + 一次 completion preflight；Light 可不设 Reviewer/QA，Standard 仍需一次合并质量交接 |
| `execution_profile` | `normal` | Standard/Strict 默认；保留完整质量、版本和收口链 |
| `dispatch_policy` | `central` | 只有 Coordinator 可写 TASK_READY/TASK_DISPATCHED |
| `dispatch_policy` | `hybrid` | 授权工作 Agent 可在父任务范围内发布；任务 claim 可用 |
| `dispatch_policy` | `self_service` | 允许父任务内发布、任务池 claim 和 thread claim |

`fast` 不改变 owned/forbidden paths、secret 禁区、不可变文档、真实验证或高风险人工门禁；
Strict 与 `fast` 组合直接拒绝。

### 快车道门禁

派发前运行 `preflight_run.py`，一次性列出任务图、scope freeze、活动锁、路径冲突和适用
治理证据。完成前运行 `completion_preflight.py`，一次性列出 result、验证、Review/QA、
commit、handoff 和候选版本缺口。两个脚本只读，不写事件、不唤醒 Agent、不授予发布许可。

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

`light + fast` 可以省略下游质量交接，但不能省略任务、事件、result、hash、范围冻结和真实
完成检查。`standard + fast` 仍必须完成独立于 Owner 的一次 Reviewer/QA 合并质量交接，只是
通过一次性 preflight 减少重复等待。

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

Standard 可以使用 `hybrid`/`self_service` 缩短派发等待，但工作 Agent 只能发布父任务范围内
的子任务；Review、QA、TASK_COMPLETED 和 RELEASE_READY 仍按 Coordinator 事件门禁执行。

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

## 5. 自助发布、任务抢占和线程抢占

自助能力不是新增 Agent，也不是 Coordinator 权限下放：

- `task_publish`：工作 Agent 只能以自己的父任务、父 hash、协作者声明和冻结 scope 为边界
  发布；发布锁保证任务文档、TASK_READY 和固定 Owner 的 TASK_DISPATCHED 不被交错。
- `task_claim`：任务必须是 `owner_agent: pool`，并列出唯一 `eligible_agents`；抢占锁保证
  同一 task 同时只有一个未过期 claimant。claim 后有效 Owner 解析到 claimant，ACK/lease/result
  必须写入 claimant outbox。
- `thread_claim`：同一 thread id 使用独立锁和不可变 lease；platform、session 线索和精确
  workspace 必须匹配，不能把一个线程同时绑定给两个 Agent。

第二个 claimant 不覆盖旧 claim，而是得到 `blocked_by`、持有者和下一动作。claim 到期不等于
任务失败；仍需检查副作用并通过 `recover_timeout.py` 决定 block、重新 claim 或人工处理。

Coordinator 仍独占 Strict/central 派发、全局状态序号、人工许可、重试/dead-letter、完成和
发布事件。

## 6. 发布门禁

Release 只能接受：

- 任务和变更编号可追踪。
- implementation commit 真实存在。
- handoff 和 registry 一致。
- Review、QA 和必需验证通过。
- 数据库、环境变量、外部依赖和回滚清楚。
- 用户对目标环境明确授权。

文档存在不代表允许发布。`local_only`、blocked、forbidden、superseded、unconfirmed 一律不是发布候选。
