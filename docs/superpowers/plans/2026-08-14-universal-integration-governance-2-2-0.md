# 通用协作效率与集成治理 2.2.0 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` or `superpowers:executing-plans` task-by-task. Steps use checkbox syntax for tracking.

**Goal:** 让普通任务默认由一个 Owner 闭环，让独立任务并行形成候选，并通过项目可选策略实现单写入集成、发布冻结、安全 worktree 收口和不越级的证据报告。

**Architecture:** Skill 核心保持项目无关。只读路由器根据事实选择 Direct、Reviewed、Coordinated 或 Release；项目可选的 Integration Policy 描述 canonical/working branch 和集成权限；候选与治理记录位于外部 Governance Home 或调用方指定位置，不成为目标项目运行依赖。集成和清理命令均需要显式确认，Protocol 继续使用 v3。

**Tech Stack:** Python 3 标准库、JSON Schema、扁平 YAML/JSON 合同、Git refs/worktrees、`unittest`。

## Global Constraints

- 核心不得硬编码任何项目名、Agent 编号、分支名、环境名、服务器或发布命令。
- Direct 是默认路径；升级必须由 writer 数量、独立质量、跨会话或真实发布事实证明。
- 不新增长期 Agent。Coordinator 与 Integration Owner 可由同一非实现者承担；Reviewer 与 QA 默认合并为 Quality。
- 没有 Integration Policy 时只提供路由、标准候选和只读建议，不创建分支、不提交、不集成。
- 自动 candidate submit 必须由项目策略显式设为 `authorized_auto`；默认 `manual`。
- 命令字段使用 argv 数组，不使用 shell，不允许绝对可执行路径、路径逃逸或 shell 元字符。
- 候选默认通过 fast-forward 或 merge 保留原 candidate commit 的可达性；若未来支持变换提交，必须另记 `integrated_commit`。
- release freeze 只阻止 canonical branch 移动，不阻止独立候选产生。
- worktree cleanup 只在 clean、无冲突、无活动进程、commit 已由其它 ref 保存且无发布占用时执行；不使用 reset/clean/force。
- 证据层使用通用 `local/candidate/quality/canonical/deployments/external_acceptance`，项目 adapter 可把部署环境映射为 MG、成都或其它名称。
- 本轮只修改本 Skill，不安装、不推送、不发布、不操作真实目标项目。

---

### Task 1：模式路由与反过度编排合同

**Files:**

- Create: `scripts/mode_router.py`
- Modify: `SKILL.md`
- Modify: `references/modes-and-gates.md`
- Modify: `references/agent-catalog.md`
- Create: `tests/test_mode_router.py`
- Modify: `tests/test_skill_contract.py`

**Interfaces:**

- Consumes: writer count、emergency、quality、cross-session、release/production facts。
- Produces: `mode`、`upgrade_reasons`、`required_roles`、`persistence_level`。

- [ ] 写失败测试：普通任务与单一紧急 Bug 都不创建 Run，也不要求 Coordinator/Release。
- [ ] 写失败测试：独立 writer 至少 2 个才升级 Coordinated；真实发布意图优先进入 Release。
- [ ] 实现纯函数 `select_mode(facts: dict) -> dict` 和只读 CLI。
- [ ] 更新 Skill 入口，明确 Direct、Reviewed、Coordinated、Release 四个家族与临时 executor。
- [ ] 运行 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_mode_router tests.test_skill_contract tests.test_emergency_contract -v`。

### Task 2：通用 Integration Policy

**Files:**

- Create: `assets/schemas/integration-policy.schema.json`
- Create: `assets/integration-policy.yaml.template`
- Create: `scripts/integration_policy.py`
- Modify: `assets/README.md`
- Modify: `references/version-governance.md`
- Create: `tests/test_integration_policy.py`

**Interfaces:**

- Consumes: policy YAML、project root。
- Produces: normalized policy dict；缺失策略返回 read-only blocker。

- [ ] 写失败测试：canonical/working 分支相同、路径逃逸、绝对命令、shell 元字符和未知字段均拒绝。
- [ ] 写失败测试：`manual` 为默认提交模式，分支名称由 adapter 提供而非核心默认。
- [ ] 实现 `load_integration_policy(path, project_root)`，只接受 argv 数组和相对高冲突路径。
- [ ] 增加项目无关模板；示例值只作为 adapter 示例，不作为 runtime 默认。
- [ ] 运行 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_integration_policy -v`。

### Task 3：候选合同与串行 Integration Lane

**Files:**

- Create: `assets/schemas/integration-candidate.schema.json`
- Create: `assets/integration-candidate.json.template`
- Modify: `assets/schemas/candidate-summary.schema.json`
- Create: `scripts/integration_lib.py`
- Create: `scripts/integration_lane.py`
- Modify: `scripts/README.md`
- Modify: `references/coordinator-runtime.md`
- Create: `tests/test_integration_lane.py`

**Interfaces:**

- Consumes: policy、candidate JSON、Git common-dir facts、可选其它候选和 release freeze。
- Produces: `ready|deferred|conflicted|blocked`；显式 integrate 产生 `integrated_commit` 和 reachability 证据。

- [ ] 写失败测试：候选 commit、baseline、changed paths、验证记录或 Quality 不真实时 fail closed。
- [ ] 写失败测试：路径、依赖、逻辑资源、workspace、environment、version source、migration order 或 release lane 冲突只暂停相关候选。
- [ ] 写失败测试：evaluate 不改变 refs/worktree；integrate 缺确认或策略时拒绝。
- [ ] 写失败测试：只有一个 integration lock holder；集成后 candidate commit 必须可达目标 branch。
- [ ] 实现只读 `evaluate` 和显式 `integrate --target working|canonical --user-confirmed`。
- [ ] 使用 `git merge-tree` 预检并使用保留 candidate commit 的 ff/merge；不自动 reset/abort/force。
- [ ] 运行 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_integration_lane -v`。

### Task 4：Release Freeze 与通用证据分层

**Files:**

- Create: `assets/schemas/release-freeze.schema.json`
- Create: `assets/release-freeze.yaml.template`
- Create: `assets/schemas/evidence-layers.schema.json`
- Create: `scripts/evidence_layers.py`
- Modify: `scripts/build_candidate_index.py`
- Modify: `references/version-governance.md`
- Modify: `references/project-finalization.md`
- Create: `tests/test_release_freeze.py`
- Create: `tests/test_candidate_evidence_layers.py`

**Interfaces:**

- Consumes: exact canonical ref/commit、version、scope、expiry 和调用方提供的环境证据。
- Produces: canonical movement gate；独立的 local/candidate/quality/canonical/deployments/external acceptance 状态。

- [ ] 写失败测试：active freeze 允许候选形成但拒绝 canonical branch 移动。
- [ ] 写失败测试：canonical mismatch 不能通过改变 scope 绕过。
- [ ] 写失败测试：部署完成与外部/provider acceptance 的 `not_verified|blocked_unknown` 可同时成立。
- [ ] 实现通用环境数组，不硬编码 MG、成都或 provider 名称。
- [ ] 扩展 Candidate Index，所有缺失证据输出 `not_verified`，禁止向上推断。
- [ ] 运行相关 unittest。

### Task 5：安全 Worktree Finalizer

**Files:**

- Create: `scripts/finalize_worktree.py`
- Modify: `references/project-finalization.md`
- Modify: `references/storage-protocol.md`
- Create: `tests/test_worktree_finalization.py`

**Interfaces:**

- Consumes: project root、registered worktree、candidate commit、可选 release freeze。
- Produces: audit JSON 或非强制 `git worktree remove`。

- [ ] 写失败测试：项目根、home/root、symlink、dirty、conflicted、活动进程、活动发布和未保存 commit 均拒绝。
- [ ] 写失败测试：clean、无进程、commit 由其它 ref 保存的临时 worktree 可收口。
- [ ] 实现 `audit` 只读；`cleanup` 要求 `--user-confirmed` 且重新执行全部 audit。
- [ ] cleanup 不使用 `--force`，不删除 branch/ref，不自动处理未知 worktree。
- [ ] 运行 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_worktree_finalization -v`。

### Task 6：通信降噪与结构化 blocker

**Files:**

- Create: `scripts/message_contract.py`
- Modify: `scripts/coordinator.py`
- Modify: `scripts/agent_dispatch.py`
- Modify: `references/document-protocol.md`
- Modify: `references/codex-native-protocol.md`
- Create: `tests/test_agent_message_compaction.py`

**Interfaces:**

- Consumes: `STARTED|BLOCKED|CANDIDATE_READY|INTEGRATED` 摘要。
- Produces: 不改变 Protocol v3 事件状态机的 `coordination_messages`。

- [ ] 写失败测试：普通进度不生成主动消息。
- [ ] 写失败测试：BLOCKED 缺 code/evidence/impact/disposition 时拒绝。
- [ ] 写失败测试：一个 blocker 只影响对应 task/candidate。
- [ ] Coordinator 和 self-service dispatch 输出去重后的结构化摘要，不新增长期角色或协议事件。
- [ ] 运行 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_message_compaction -v`。

### Task 7：版本、文档与完整验证

**Files:**

- Modify: `VERSION`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `SKILL.md`
- Modify: `agents/openai.yaml`
- Modify: `agents.html`
- Modify: `tests/README.md`

**Interfaces:**

- Produces: Skill `2.2.0`；Protocol v3；兼容的新增 Schema，不改变目标项目业务版本。

- [ ] 将所有用户可见入口更新为通用路由、候选、Integration Policy 和证据层。
- [ ] 更新版本为 `2.2.0`，明确不自动安装、发布或修改目标项目版本。
- [ ] 在临时 Git 仓库验证 Direct、四候选并行评估、串行集成、freeze 与 worktree cleanup。
- [ ] 运行完整 unittest、compileall、全部 JSON Schema、全部 CLI `--help`、Markdown 链接和 `git diff --check`。
- [ ] 使用 Skill 官方 quick validator 校验目录。
- [ ] 本地提交候选；不安装、不推送，交由用户选择集成方式。
