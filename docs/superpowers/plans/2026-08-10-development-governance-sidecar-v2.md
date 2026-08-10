# 开发治理外置与 Direct/Coordinated 双路径实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将多线程协作资料、项目 Agent、Run、handoff、candidate index 和恢复/审计资料从目标项目目录迁移到外部开发治理空间，并让普通网站更新默认走不创建治理资料的 Direct 路径。

**Architecture:** 目标项目只保存业务源码、测试、构建配置和项目版本权威源；开发治理资料保存在用户级或用户显式指定的 Governance Home。新增统一路径解析器和项目绑定文件，所有现有协议脚本通过绑定访问项目与治理空间。Protocol v3 的任务和事件语义保持不变，Skill 升级为 2.0.0，并新增 Governance Storage Schema 1.0。

**Tech Stack:** Python 3 标准库、受限 YAML/JSON、SHA-256、原子文件写入、`unittest`/`pytest`、Markdown。

## Global Constraints

- 只修改 `multi-agent-collaboration` Skill 项目，不访问或修改目标网站项目。
- 默认 `coordination_mode` 为 `direct`；Direct 不创建 Agent、Run、handoff、candidate index 或项目内治理目录。
- `coordinated` 才允许创建持久治理资料，并且治理根目录必须位于目标项目之外。
- Skill 不得自动创建或修改目标项目的 `AGENTS.md`。
- 网站源码、构建、启动、部署和线上运行不得读取治理资料。
- `governance`、`execution_profile`、`dispatch_policy` 与项目业务版本保持独立；Direct 不要求 `dispatch_policy`。
- Protocol 继续为 v3；不改变任务、事件、ACK、lease、claim、Review 或 QA 状态语义。
- Skill 版本升级为 `2.0.0`；新增 Governance Storage Schema `1.0`。
- 旧项目内 `.multi-agent-collaboration/` 只读兼容，通过显式迁移工具迁移，不自动删除。
- 不增加新的 Agent；Reviewer 与 QA 继续优先合并为一个独立质量能力。

---

### Task 1: 建立外置治理路径与绑定契约

**Files:**
- Create: `scripts/governance_paths.py`
- Create: `assets/project-binding.yaml.template`
- Create: `assets/schemas/project-binding.schema.json`
- Create: `tests/test_governance_paths.py`
- Modify: `assets/README.md`

**Interfaces:**
- Produces: `default_governance_root() -> Path`
- Produces: `resolve_governance_project(project_root: Path, project_id: str, governance_root: Path | None, *, require_existing: bool) -> GovernancePaths`
- Produces: `write_project_binding(paths: GovernancePaths, project_name: str) -> Path`
- Produces: `load_project_binding(governance_project_root: Path) -> dict[str, object]`
- Produces: `GovernancePaths(project_root, governance_root, project_id, project_key, project_dir, agents_dir, runs_dir)`

- [ ] **Step 1: 写治理目录必须位于项目外的失败测试**

  在 `tests/test_governance_paths.py` 创建临时项目，断言治理根目录等于项目目录或位于项目子目录时抛出 `ProtocolError`，项目目录之外时返回稳定路径。

- [ ] **Step 2: 运行测试确认 RED**

  Run: `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_governance_paths -v`

  Expected: FAIL，因为 `governance_paths` 尚不存在。

- [ ] **Step 3: 实现最小路径解析器**

  使用 `Path.resolve()` 和真实父子路径关系校验，不使用字符串前缀；默认根目录为 `~/.codex/governance/multi-agent-collaboration`，显式参数优先。

- [ ] **Step 4: 写并验证 project binding**

  binding 必须包含 `storage_schema`、`project_id`、`project_name`、`project_root`、`project_key`、`allowed_roots`、`created_at`，并用原子写入发布。

- [ ] **Step 5: 运行 GREEN 与 Schema 解析**

  Run: `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_governance_paths -v`

  Run: `python -m json.tool assets/schemas/project-binding.schema.json >/dev/null`

- [ ] **Step 6: 提交路径契约**

  ```bash
  git add scripts/governance_paths.py assets/project-binding.yaml.template assets/schemas/project-binding.schema.json assets/README.md tests/test_governance_paths.py
  git commit -m "feat: add external governance path contract"
  ```

### Task 2: 将 Run 初始化迁移到外部治理空间

**Files:**
- Modify: `scripts/init_run.py`
- Modify: `assets/project.yaml.template`
- Modify: `assets/manifest.yaml.template`
- Modify: `tests/test_protocol_v3.py`
- Create: `tests/test_external_run_storage.py`

**Interfaces:**
- Consumes: `resolve_governance_project(...)`
- Produces CLI: `init_run.py --coordination-mode coordinated --governance-root <path> --project-id <id>`
- Preserves: `--project-root` as the source/worktree boundary, not storage location.

- [ ] **Step 1: 写外置 Run 的失败测试**

  测试 coordinated 初始化后 `run_dir` 位于 `<governance-root>/projects/<project-key>/runs/`，目标项目内没有 `.multi-agent-collaboration/` 和新 `AGENTS.md`。

- [ ] **Step 2: 写 Direct 不初始化 Run 的失败测试**

  `--coordination-mode direct` 必须拒绝创建 Run，并给出“Direct 不需要 init_run”的明确退出信息；项目目录保持无变化。

- [ ] **Step 3: 运行 RED**

  Run: `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_external_run_storage -v`

- [ ] **Step 4: 改造 `init_run.py`**

  增加 `--coordination-mode`、`--governance-root`、`--project-id`、`--project-name`；移除 `project_root / ".multi-agent-collaboration"` 推导，通过 resolver 创建 binding、protocol、project 和 run。

- [ ] **Step 5: 保持项目路径和版本源校验**

  `version_source`、owned paths 和 allowed roots 继续绑定真实 `project_root`；治理目录绝不自动加入 Agent writable paths。

- [ ] **Step 6: 运行 GREEN 和 Protocol 回归**

  Run: `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_external_run_storage tests.test_protocol_v3 -v`

- [ ] **Step 7: 提交外置 Run 初始化**

  ```bash
  git add scripts/init_run.py assets/project.yaml.template assets/manifest.yaml.template tests/test_external_run_storage.py tests/test_protocol_v3.py
  git commit -m "refactor: store coordinated runs outside projects"
  ```

### Task 3: 将长期 Agent、会话和 handoff 迁移到外部治理空间

**Files:**
- Modify: `scripts/project_memory_lib.py`
- Modify: `scripts/init_project_agents.py`
- Modify: `scripts/bind_session.py`
- Modify: `scripts/sync_conversation.py`
- Modify: `scripts/create_checkpoint.py`
- Modify: `scripts/resume_brief.py`
- Modify: `scripts/record_agent_runtime.py`
- Modify: `scripts/record_agent_activity.py`
- Modify: `scripts/validate_agents.py`
- Modify: `scripts/manage_project_agents.py`
- Modify: `scripts/rebuild_index.py`
- Create: `tests/test_external_agent_storage.py`
- Modify: persistent Agent test fixtures that currently assume `project/.multi-agent-collaboration`.

**Interfaces:**
- Consumes: `GovernancePaths` and project binding.
- Produces CLI convention: commands accept `--project-root`, `--project-id`, and optional `--governance-root`.
- Removes: automatic creation or mutation of target-project `AGENTS.md`.

- [ ] **Step 1: 写外置 Agent 初始化失败测试**

  断言 TEAM、ROLE、AGENT_PROFILE、SESSION_MAP、checkpoint 和 handoff 全部位于治理目录；目标项目文件清单与初始化前一致。

- [ ] **Step 2: 运行 RED**

  Run: `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_external_agent_storage -v`

- [ ] **Step 3: 改造共享根目录解析**

  `project_memory_lib.bus_root()` 不再拼接项目路径，改为接收 resolver 结果；保留 Secret 检查、锁、原子写和不可变记录能力。

- [ ] **Step 4: 改造 Agent 初始化事务**

  staging 目录创建在治理根目录，TEAM 仍是完成标记；删除创建项目 `AGENTS.md` 的代码及回滚分支。

- [ ] **Step 5: 改造长期资料命令**

  所有命令通过同一 binding 找 Agent 根目录；恢复报告同时显示项目路径和治理路径，但不把治理路径写进网站项目。

- [ ] **Step 6: 运行 GREEN 与长期层回归**

  Run: `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_external_agent_storage tests.test_persistent_agents tests.test_persistent_validation tests.test_runtime_initialization -v`

- [ ] **Step 7: 提交外置长期 Agent 存储**

  ```bash
  git add scripts tests
  git commit -m "refactor: move persistent agent data to governance home"
  ```

### Task 4: 解耦 Bridge、Checkpoint、Finalization 与 Candidate Index

**Files:**
- Modify: `scripts/archive_run_to_agents.py`
- Modify: `scripts/create_project_checkpoint.py`
- Modify: `scripts/finalize_project.py`
- Modify: `scripts/build_candidate_index.py`
- Modify: `scripts/preflight_lib.py`
- Modify: `tests/test_run_memory_bridge.py`
- Modify: `tests/test_project_checkpoint_runtime.py`
- Modify: `tests/test_project_finalization.py`
- Modify: `tests/test_optimization_features.py`

**Interfaces:**
- Bridge derives `project_root` from binding, never from `bus.parent`.
- Candidate index remains a read-only derived JSON view and never grants release permission.
- Finalization writes only to governance storage.

- [ ] **Step 1: 写跨目录 Bridge 失败测试**

  Run 和 persistent Agent 位于同一外部 governance project，项目源码位于另一目录；Bridge 必须保持 task/result/evidence hash 并成功归档。

- [ ] **Step 2: 写 Candidate 非项目依赖测试**

  删除 candidate 输出后，项目源码、测试和模拟构建不受影响；candidate 中只引用 commit、证据和治理路径。

- [ ] **Step 3: 运行 RED**

  Run: `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_run_memory_bridge tests.test_project_finalization tests.test_optimization_features -v`

- [ ] **Step 4: 移除 `bus.parent == project_root` 假设**

  所有项目事实通过 binding 解析；治理相对引用基于 governance project，项目 artifact 引用基于 project root，并分别执行越界校验。

- [ ] **Step 5: 运行 GREEN**

  Run: `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_run_memory_bridge tests.test_project_checkpoint_runtime tests.test_project_finalization tests.test_optimization_features -v`

- [ ] **Step 6: 提交治理收口解耦**

  ```bash
  git add scripts tests
  git commit -m "refactor: decouple governance closure from project storage"
  ```

### Task 5: 增加旧项目本地治理资料的安全迁移

**Files:**
- Create: `scripts/migrate_governance_storage.py`
- Create: `tests/test_governance_storage_migration.py`
- Modify: `scripts/README.md`

**Interfaces:**
- Produces CLI: `migrate_governance_storage.py --project-root <path> --project-id <id> --governance-root <path> --dry-run|--apply`
- Produces immutable migration manifest with source path, target path, file inventory and SHA-256.

- [ ] **Step 1: 写 dry-run 无副作用失败测试**

  dry-run 返回文件清单、目标路径和冲突，不创建治理目录，不修改旧目录。

- [ ] **Step 2: 写 apply 原字节迁移和回滚失败测试**

  apply 使用 staging、验证所有 hash 后原子发布；注入复制或验证失败时目标不存在或保持原状态，旧目录始终保留。

- [ ] **Step 3: 运行 RED**

  Run: `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_governance_storage_migration -v`

- [ ] **Step 4: 实现最小迁移器**

  只复制 `.multi-agent-collaboration/` 的常规文件和目录，拒绝 symlink escape、目标冲突和部分目标；不删除旧目录，不修改项目 `.gitignore`。

- [ ] **Step 5: 运行 GREEN 与 CLI help**

  Run: `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_governance_storage_migration -v`

  Run: `python scripts/migrate_governance_storage.py --help`

- [ ] **Step 6: 提交迁移工具**

  ```bash
  git add scripts/migrate_governance_storage.py scripts/README.md tests/test_governance_storage_migration.py
  git commit -m "feat: migrate legacy governance stores safely"
  ```

### Task 6: 重写 Skill 路由、文档和 Agent 入口边界

**Files:**
- Modify: `SKILL.md`
- Modify: `README.md`
- Modify: `agents/openai.yaml`
- Modify: `agents.html`
- Modify: `docs/MERGED_STRUCTURE.md`
- Modify: `references/README.md`
- Modify: `references/interview-and-planning.md`
- Modify: `references/modes-and-gates.md`
- Modify: `references/storage-protocol.md`
- Modify: `references/document-protocol.md`
- Modify: `references/cross-platform-resume.md`
- Modify: `references/run-memory-bridge.md`
- Modify: `references/project-finalization.md`
- Modify: `references/version-governance.md`
- Modify: `references/agent-catalog.md`
- Modify: `tests/test_agent_catalog.py`
- Modify: `tests/test_skill_contract.py`

**Interfaces:**
- Defines `direct` as the default coordination mode.
- Defines `coordinated` as the only mode that creates external governance records.
- Keeps `agents.html` as Skill-local static role selector without project writes or live status.

- [ ] **Step 1: 写文档契约失败测试**

  断言中英文入口包含 Direct/Coordinated、外部 Governance Home、禁止项目内治理目录、禁止自动创建 `AGENTS.md` 和网站运行零依赖；断言不再把 `.multi-agent-collaboration/` 声明为项目目录。

- [ ] **Step 2: 运行 RED**

  Run: `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_agent_catalog tests.test_skill_contract -v`

- [ ] **Step 3: 精简 `SKILL.md`**

  将核心路由、边界和最小工作流留在主文件，把存储布局、迁移、长期恢复细节放入一级 references；目标控制在 500 行以内。

- [ ] **Step 4: 更新中英文 README、架构和静态角色页**

  明确单 Agent 网站更新不创建 Run；Agent 角色页不复制到目标网站；复杂协同资料只存在于外部治理层。

- [ ] **Step 5: 运行 GREEN 与本地链接检查**

  Run: `PYTHONDONTWRITEBYTECODE=1 python -m unittest tests.test_agent_catalog tests.test_skill_contract -v`

  Run: `python /Users/yancyfeng/.codex/skills/.system/skill-creator/scripts/quick_validate.py .`

- [ ] **Step 6: 提交文档与入口边界**

  ```bash
  git add SKILL.md README.md agents agents.html docs references tests
  git commit -m "docs: separate development governance from project runtime"
  ```

### Task 7: 更新版本并完成全量验证

**Files:**
- Modify: `VERSION`
- Modify: `CHANGELOG.md`
- Modify: `tests/README.md`
- Modify: remaining tests that still hard-code project-local governance storage.

**Interfaces:**
- Skill version: `2.0.0`
- Protocol version: `3`
- Governance Storage Schema: `1.0`

- [ ] **Step 1: 扫描残留项目内治理路径**

  Run: `rg -n 'project_root / "\.multi-agent-collaboration"|project / "\.multi-agent-collaboration"|项目目录是长期真源' scripts tests references SKILL.md README.md`

  Expected: 只允许出现在 legacy migration 文档、迁移测试和明确的禁止性说明中。

- [ ] **Step 2: 更新版本与 CHANGELOG**

  `VERSION` 写 `2.0.0`；CHANGELOG 说明存储默认值属于 breaking change，Protocol v3 保持兼容，旧项目本地资料只读迁移。

- [ ] **Step 3: 运行全量测试**

  Run: `PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_*.py' -q`

  Run: `PYTHONDONTWRITEBYTECODE=1 python -m pytest -q`

- [ ] **Step 4: 运行发布门禁**

  Run: `PYTHONDONTWRITEBYTECODE=1 python -m py_compile scripts/*.py scripts/adapters/*.py`

  Run: `python /Users/yancyfeng/.codex/skills/.system/skill-creator/scripts/quick_validate.py .`

  Run: `git diff --check`

  Run: 所有 `assets/schemas/*.json` 使用 stdlib JSON 解析。

  Run: 所有顶层 `scripts/*.py --help` 返回成功。

- [ ] **Step 5: 验证网站运行零依赖**

  在临时模拟网站项目执行 Direct 流程，确认项目文件清单未出现治理资料；移除外部 Governance Home 后，项目测试和模拟构建仍通过。

- [ ] **Step 6: 提交 2.0.0 发布候选**

  ```bash
  git add VERSION CHANGELOG.md README.md SKILL.md agents agents.html assets docs references scripts tests
  git commit -m "chore: release multi-agent collaboration 2.0.0"
  ```

## Self-Review Checklist

- [ ] 每一项新行为都有先失败再通过的测试。
- [ ] Direct 不创建任何治理资料。
- [ ] Coordinated 治理根目录强制位于目标项目之外。
- [ ] Skill 不自动创建或修改项目 `AGENTS.md`。
- [ ] 项目版本、Skill 版本、Protocol 版本和 Storage Schema 相互独立。
- [ ] 旧目录迁移不删除源数据且支持故障回滚。
- [ ] Candidate index 仍为只读视图，不能授予发布权限。
- [ ] Protocol v3 原有状态机和证据门禁测试全部通过。
- [ ] 文档没有 TBD、TODO、占位步骤或与实现不一致的 CLI。
