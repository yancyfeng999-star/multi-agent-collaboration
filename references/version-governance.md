# 集中式项目版本治理

## 0. Skill、Protocol、Storage 与项目版本边界

四个版本对象必须独立：Skill 版本以根 `VERSION` 为权威源；Protocol 版本当前为 `3`；Governance Storage Schema 当前为 `1.1`（兼容读取 `1.0`）；项目业务版本只以目标项目自身权威源为准。

Direct 默认不创建版本合同或 Run，但仍必须按项目自身规则判断本次交付是否应更新业务版本。Coordinated 才使用 `tracked`/`not_applicable`、version contract、Release Train 和 RC，且这些资料只写项目外 Governance Home。

旧 Run 不会被原地改写。`migrate_run_optimization.py` 只迁移 v3 Run 的可选执行字段；`migrate_governance_storage.py` 将旧项目内治理资料复制并校验到 Governance Home。两种迁移都不改变项目业务版本，也不授予发布权限。

运行时的项目版本继续由 Coordinator 集中治理；`emergency`、`fast`、self-service、claim 或 retry attempt
都不能直接递增项目版本。只有交付范围、兼容性或项目自身版本规则触发时，才建立新的版本合同。

Direct Hotfix 不创建版本合同；当前 Agent 先按目标项目自身规则判断是否需要业务版本变更，
涉及正式发布时才在发布动作前进入项目既有版本/授权流程。Coordinated Emergency 仍必须在
初始化时明确 `tracked` 或 `not_applicable`，但不因建立短期 executor 自动递增业务版本。

## 1. 目标

项目版本是多个 Agent 共同进入同一交付物时的治理边界。它不等同于协议版本、Run、
任务 revision、attempt 或 RC 编号。

版本治理只使用现有角色：

- Coordinator：版本判断、合同冻结、任务绑定、RC 编号和版本重评。
- Owner：完成绑定版本下的具体任务，不自行决定或递增项目版本。
- Reviewer / QA：按原职责验证，不承担版本管理；两项职责默认由同一个独立质量智能体
  承担。
- Release：仅在已有发布任务时写入最终版本并执行发布；不为版本治理额外创建角色。

自助发布的工作 Agent 可以提出或完成版本相关子任务，但不能修改版本合同、编号 RC 或写入
正式项目版本；这些动作仍由 Coordinator/既有 Release 角色执行。

## 2. 何时启用

以下工作使用 `tracked`：

- 多个 Agent 的结果汇入同一正式交付物。
- 修改代码、数据库、API、公共数据格式、运行配置或兼容性。
- 产生构建、安装包、部署、发布或客户交付。
- 项目规则要求版本、change id、release registry 或发布账本。

只读分析、调研和不进入正式交付物的草稿可以使用 `not_applicable`。两种模式都必须明确
写入初始化参数和判断理由。

## 3. 版本权威源

Coordinator 先读取项目已有规则，识别唯一版本权威源，例如 `package.json`、
`pyproject.toml`、`Cargo.toml`、`VERSION`、Git tag 或项目 Registry。Skill 不要求所有
项目采用 SemVer，也不允许多个来源各自递增。

支持的策略标识：

- `semver`
- `calendar`
- `registry_managed`
- `custom`

自定义规则可通过 `--version-policy-ref` 固定文件和 SHA-256。

## 4. 版本合同

初始化器创建不可变的 `versions/version-contract.yaml`。合同至少包含：

- `release_train_id`
- `versioning_mode`
- `version_scheme`
- `baseline_version`
- `baseline_commit`
- `target_version`
- `version_source_ref` 和 SHA-256
- 可选版本策略引用和 SHA-256
- 固定 `owner_agent: coordinator`
- 判断理由和时间

manifest 固定合同路径和 hash。所有任务固定相同的 `release_train_id`、
`delivery_version` 和合同 hash。任何字段不一致都禁止 dispatch。

## 5. 迭代规则

三种迭代互不替代：

- 任务返工：增加 `attempt_id`。
- 重新集成：增加 RC 编号。
- 交付范围或兼容性变化：重新评估项目目标版本。

Coordinator 使用：

```bash
python3 <skill-dir>/scripts/manage_run.py record-release-candidate \
  --run-dir "<run-dir>" \
  --summary "<candidate-summary>" \
  --implementation-commit "<commit>" \
  --artifact-ref "<artifact>"
```

候选版本按 `RC-001`、`RC-002` 顺序写入不可变文件，并生成
`<target-version>-rc.1`、`<target-version>-rc.2`。不得覆盖或复用旧候选记录。

## 6. 版本重评

以下变化需要 Coordinator 停止受影响任务并重新判断目标版本：

- 用户扩大或缩小正式交付范围。
- 引入破坏性 API、schema、权限或兼容性变化。
- 新任务加入当前交付版本。
- baseline 被另一个发布改变。
- 项目版本规则或权威源发生变化。

范围和验收不变的返工不改变项目版本；范围变化使用新的任务 revision。当前 v3 合同不会
原地改写。需要改变基线、目标版本或权威源时，关闭或 supersede 当前 Run，并以新的合同
初始化后继 Run。

## 7. 发布门禁

发布前必须同时满足：

- Run 使用 `tracked`。
- 至少有一个不可变 RC。
- 最新 RC 使用真实 Git commit。
- 最新 RC commit 与 Release 任务结果一致。
- 所有必需 Review、QA 和人工许可通过。
- 版本权威源已写入预留目标版本。
- 工作区、回滚和环境证据满足治理模式。

已经正式发布的版本号不能对应另一份内容。回滚后是否创建 patch、hotfix 或新日期版本，
服从项目版本策略，不复用已经发布的版本号。

## 8. v2 到 v3

协议 v3 新增版本判断、版本合同、任务版本绑定和 RC 记录。v1/v2 Run 保留为只读历史，
不会自动补写版本事实。需要继续执行时，以原 Run 的真实状态为输入，由用户确认后创建
新的 v3 Run。
