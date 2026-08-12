# 多智能体协同：紧急修复与任务中心化并行调度优化计划

> **给执行 Agent 的要求：** 实施本计划时使用 `superpowers:executing-plans` 按任务逐项执行，并使用测试驱动方式修改行为。不得为了执行本计划额外增加长期 Agent 角色；只有用户已经授权并行执行实例时，才可在约定并行额度内启动多个执行实例。

**目标：** 让单一紧急 Bug 可以直接快速修复；让多个彼此独立的任务即使需要同一种能力，也能同时分配给多个同类型执行实例；只在真正冲突的任务、路径、资源、线程或发布步骤上串行，不再因为某个 Agent 忙碌或某个无关任务门禁不完整而阻塞整个 Run。

**推荐架构：** 保留少量稳定 Agent 角色，把实际工作者改为 Run 内短生命周期的“执行实例”。调度以任务、能力和冲突资源为中心，不以固定 Agent 身份为队列。新增 `emergency` 执行档、任务级 Preflight、能力池执行实例和冲突指纹；Direct 紧急直修不创建治理资料，Coordinated 应急只记录恢复服务所需的最小事实。生产、数据、支付、权限、密钥、迁移、部署和破坏性操作的硬门禁继续保留，但门禁只阻塞它所保护的步骤。

**版本建议：** Skill `2.0.0 → 2.1.0`；Protocol 继续使用 v3；新增 Executor Binding Schema `1.0`，Preflight Result Schema 升级到 `1.1`，Governance Storage Schema 以向后兼容方式升级到 `1.1`。目标项目业务版本不由本 Skill 自动修改。

**技术栈：** Python 3 标准库、现有扁平 YAML/JSON/Markdown 协议、SHA-256、原子写入、文件锁、Git worktree、`unittest`。

## 1. 结论先行

本轮不应只把 `max_parallel` 从 2 改成更大的数字。当前时延来自三种边界被混在一起：

1. 同一任务只能有一个有效执行者。
2. 同一路径、逻辑资源或线程不能同时被多个任务写入。
3. 同一种 Agent 能力被误解为只能有一个实际工作者。

前两条必须保留，第三条必须取消。

优化后的核心规则是：

- **同一任务串行：** 同一个 `task_id` 只能存在一个有效 Owner claim。
- **同一资源串行：** 相同 owned path、逻辑资源、数据库 schema、版本源、发布通道或 Native thread 只允许一个持有者。
- **不同任务并行：** 任务之间没有依赖、路径重叠、资源重叠和外部副作用冲突时，立即并行。
- **同类型可扩容：** 两个独立任务都需要 Owner、Frontend、Backend 或测试能力时，可同时建立两个同类型执行实例，不必等待原实例完成。
- **角色不膨胀：** “Owner”仍只是一种稳定角色；`owner@TASK-A#1` 与 `owner@TASK-B#1` 是临时执行实例，不是两个新的长期 Agent 角色。
- **门禁局部化：** 一个任务缺字段、缺授权或存在冲突，只阻塞该任务或相应步骤；不得让整个 Run 的无关任务停止。
- **紧急不等于越权：** 紧急档减少往返、文档和重复检查，不取消真实生产授权、数据安全、回滚和发布许可。

## 2. 当前实现中的具体问题

### 2.1 `fast` 仍然不是紧急修复路径

当前 `fast` 仍要求一次 dispatch preflight、一次 completion preflight；Standard 还必须完成一次独立合并质量交接。它适合“减少一些等待”，但不适合单一、明确、需要尽快恢复的 Bug。

结果是：即使修复范围只有一个文件，Agent 仍可能先创建 Run、冻结范围、补登记、等待质量 Agent，再完成候选和收口资料。

### 2.2 Coordinator 使用 Run 级 Preflight 作为全局开关

当前 `scripts/coordinator.py` 在非 central Run 中先调用一次 `run_preflight(run_dir)`。只要报告不是 ready，就直接返回 `preflight_blocked`，派发列表为空。

这会产生队头阻塞：

```text
TASK-A 缺一个门禁字段 ─┐
                       ├─ Run 全部不派发
TASK-B 完全独立且已就绪 ┘
```

正确行为应是：TASK-A 进入自己的 blocked report，TASK-B 继续派发。

### 2.3 固定 Agent 身份与实际执行容量绑定过紧

当前任务池通过 `eligible_agents` 指向已经注册的 Agent ID，claim 也要求 claimant 是 Registry 中的 Agent。这个规则可以保护身份和权限，但容易被执行器解释为“一个 Owner Agent 忙时，所有 Owner 类任务都要排队”。

实际上，角色能力和运行实例是两层概念：

```text
稳定角色：owner
  ├─ 执行实例：owner@TASK-A#1 / worktree-A / thread-A
  └─ 执行实例：owner@TASK-B#1 / worktree-B / thread-B
```

只要 TASK-A 与 TASK-B 无冲突，它们不应共享执行队列。

### 2.4 文档中的“抢占不是额外并发量”容易被扩大解释

现有规则本意是“抢同一任务不能产生两个 Owner”，但容易被解释为“同一类型 Agent 不能并行”。需要把边界改写为：

- claim 决定一个任务的唯一执行实例；
- 并行额度由 Run 总预算和资源冲突决定；
- 不同任务可以由同一角色模板的不同执行实例分别 claim。

### 2.5 全局并行上限是容量上限，不应成为角色数量

`max_parallel` 应继续存在，但只表示 Run 同时允许多少个活动任务。它不表示“每种角色只有一个实例”，也不要求提前创建同等数量的长期 Agent。

### 2.6 当前正确能力必须保留

本轮不是推倒重写。以下机制仍然正确：

- 同一 task claim 唯一；
- 同一 thread claim 唯一；
- owned path 父子路径视为冲突；
- 共享资源按 bundle/FIFO 串行；
- 发布锁和事件序号锁只保护短事务；
- Owner 不能自审；
- Strict/Release fail-closed；
- Native 不可用时回退 Document package；
- Direct 不创建治理资料。

## 3. 三种方案对比

| 方案 | 优点 | 缺陷 | 结论 |
| --- | --- | --- | --- |
| 只提高 `max_parallel` | 改动最小 | 全局 Preflight、固定实例和角色队列仍会阻塞；也可能放大冲突 | 不采用 |
| 取消 claim、Preflight 和质量门禁 | 表面最快 | 同任务双写、生产越权、结果不可追踪，故障恢复风险高 | 不采用 |
| 任务/资源中心调度 + 临时同类型执行实例 + 紧急档 | 独立任务真正并行；冲突仍串行；不增加长期角色 | 需要补充执行实例合同和任务级报告 | **推荐** |

## 4. 目标运行模型

### 4.1 三个维度必须分开

```text
coordination_mode  = direct | coordinated
governance         = light | standard | strict
execution_profile  = emergency | fast | normal
```

- `coordination_mode` 决定是否建立外部治理 Run。
- `governance` 决定风险、证据和人工门禁下限。
- `execution_profile` 决定等待、交接和资料生成时机。

`emergency` 是时效策略，不是低风险等级。它允许与 Light、Standard、Strict 组合；与 Strict 组合时只压缩非关键等待，不能取消 Strict 的生产和高风险硬门禁。

### 4.2 路径一：Direct Hotfix（紧急直修）

适用条件：

- 一个 Agent 可以完整处理；
- Bug 范围明确且改动集中；
- 不需要多 Agent 交接、恢复或独立并行；
- 用户没有要求创建受管协同 Run。

最小流程：

```text
读取项目规则与故障事实
  → 确认修改范围和不可改边界
  → 修复
  → 定向测试/回归/必要的运行验证
  → 报告变更、验证、风险和待授权动作
```

Direct Hotfix 不创建 Agent、Run、handoff、candidate index、checkpoint 或治理目录。若涉及生产部署、真实数据、迁移、支付、权限、密钥或破坏性操作，仍在对应动作前等待项目规则要求的用户授权；等待授权不妨碍先完成本地修复和安全验证。

### 4.3 路径二：Coordinated Emergency（协同应急）

适用条件：

- 同一事故存在两个或更多可独立推进的修复任务；
- 需要独立质量验证、跨模块交接、恢复记录或发布审计；
- 用户选择多 Agent 协同。

最小流程：

```text
最小应急简报
  → 任务拆分与冲突指纹
  → 每个任务独立 Preflight
  → 为已就绪任务分配执行实例
  → 独立任务同波次并行
  → 每个任务定向验证
  → 独立 Quality 合并验证
  → 发布步骤单独门禁与串行
  → 服务恢复后补齐非阻塞治理收口
```

应急简报只要求立即决策所需的事实：症状、影响、范围、目标、不可改边界、验收、回滚、目标环境和用户已给出的授权。Bridge、PCP、candidate index、长期 checkpoint 等收口资料不得阻塞修复和本地验证；如果本次工作需要正式发布，它们可在恢复后补齐，但不能被伪造为已完成。

## 5. 稳定角色与临时执行实例

### 5.1 角色仍保持最小编制

稳定目录继续只保留必要角色：

- Coordinator：全局范围、冲突仲裁、版本、集成、发布和收口。
- Owner：实现能力模板，可包含 Frontend、Backend、Data、测试等能力标签。
- Quality：独立于 Owner，默认合并 Reviewer 与 QA。
- Release：仅在项目真实发布边界不能由 Coordinator 承担时存在。

不新增 Dispatcher、Queue、Emergency、Version、Hotfix、Gatekeeper 等长期角色。

### 5.2 新增 Run 内 Executor Binding

执行实例是某个稳定 Agent/角色模板在一个任务 attempt 上的短期绑定：

```yaml
schema_version: "1.0"
executor_id: "EXEC-owner-TASK-A-001"
principal_agent_id: "owner"
role_ref: "owner"
task_id: "TASK-A"
attempt_id: "ATTEMPT-001"
required_capabilities: ["frontend"]
runtime: "codex_thread"
session_id: "..."
thread_id: "..."
workspace: "/project-worktrees/TASK-A"
worktree_policy: "isolated_writer"
lease_acquired_at: "..."
lease_expires_at: "..."
status: "active"
```

约束：

- 一个 `executor_id` 只绑定一个 task attempt。
- 一个任务同时只有一个 active Executor Binding。
- 同一个 `principal_agent_id` 可在不同任务上拥有多个执行实例。
- 每个并发写实例默认使用独立 worktree；只读实例可共享工作区。
- 同一 worktree 不允许两个写实例同时执行 Git checkout、merge、stage、commit 或修改重叠文件。
- 执行结束、让出、超时或失败后追加不可变 release/expiry 记录，不覆盖历史绑定。
- 执行实例不写入长期 TEAM，不出现在 `agents.html`，不增加长期 Agent 数量。

### 5.3 一次授权并行额度，避免反复询问

用户在启用 Coordinated 时可以一次确认：

```yaml
executor_scale_authorized: true
max_parallel: 4
max_instances_per_role: {"owner": 4, "quality": 1}
```

在该范围内，调度器可按任务需要复用或建立执行实例，不再为每个同类型实例重复询问。以下情况仍需重新确认：

- 超过已批准的 `max_parallel`；
- 创建新的权限边界或不同 runtime；
- 扩大项目范围；
- 进入生产、真实数据、迁移或破坏性动作；
- 提高受管委派深度。

## 6. 任务中心化冲突模型

### 6.1 每个任务生成冲突指纹

Task 增加或推导以下字段：

```yaml
required_capabilities: ["frontend"]
owned_paths: ["src/features/a"]
logical_resources: []
environment_resources: []
workspace_policy: "isolated_writer"
release_lane: "none"
```

调度器基于六类冲突判断，而不是基于 Agent 名称判断：

1. **依赖冲突：** 上游任务未完成。
2. **路径冲突：** owned paths 相同或存在父子包含。
3. **逻辑资源冲突：** 数据库 schema、包锁、全局 CSS、注册表、版本源、共享配置。
4. **工作区冲突：** 两个写任务使用同一未隔离 worktree 或 Git index。
5. **外部副作用冲突：** 同一生产环境、账户、队列、对象存储、支付事务或 migration。
6. **发布通道冲突：** merge、版本递增、RC、部署和回滚必须单通道串行。

### 6.2 调度决策表

| 判断结果 | 行为 |
| --- | --- |
| 无依赖、无路径/资源/环境冲突 | 立即并行派发 |
| 同一种能力但任务无冲突 | 分配不同同类型执行实例并行 |
| 同一 task 被两个实例 claim | 首个合法 claim 获胜，另一个立即返回 holder/expiry/next action |
| 只在某个 resource step 冲突 | 只把该步骤放入对应资源队列，任务的其他安全步骤可继续 |
| 某个任务缺门禁或字段 | 只把该任务放入 `blocked_tasks`，继续扫描其他 ready task |
| 冲突无法判断 | 只阻塞涉及不确定资源的任务，并给出补充信息动作 |
| merge/version/release 冲突 | 实现可并行，最终集成与发布串行 |

### 6.3 不再按角色排全局队列

错误模型：

```text
Owner 忙 → 所有 Owner 任务排队
```

目标模型：

```text
TASK-A(frontend, src/a) → owner executor A
TASK-B(frontend, src/b) → owner executor B
TASK-C(frontend, src/a/shared) → 等待 A 的路径锁
```

TASK-B 不应因为 TASK-A 或 TASK-C 的状态被延迟。

### 6.4 并行预算的计算

```text
可派发数量 = min(
  max_parallel - 当前活动任务数,
  当前无冲突 ready task 数,
  已授权的 runtime/thread/worktree 容量
)
```

`max_instances_per_role` 默认不再是 1；缺省时继承 `max_parallel`。Quality 默认仍为 1，除非多个完全独立的质量任务和用户授权证明并行有实际收益。

## 7. 门禁局部化与应急规则

### 7.1 四级门禁作用域

| 门禁级别 | 示例 | 阻塞范围 |
| --- | --- | --- |
| G0 任务事实 | 目标、owned paths、验收缺失 | 当前任务 |
| G1 共享资源 | 文件、schema、包锁、版本源被占用 | 当前资源步骤 |
| G2 高风险动作 | migration、真实数据、支付、权限、密钥 | 对应高风险步骤 |
| G3 发布动作 | merge、RC、部署、重启、回滚 | release lane |

门禁必须放在“最晚但仍然安全”的位置。例如：缺少生产部署授权时，可以继续读取、修复、写测试和本地验证，但不能执行部署；缺少 migration 授权时，可以完成迁移方案和 dry-run 检查，但不能操作真实数据库。

### 7.2 Emergency 可压缩的流程

可以压缩或延后：

- 重复的全 Run 扫描；
- 不影响修复决策的长期 Agent 资料；
- Bridge、PCP、candidate index 和长期 checkpoint；
- 多次状态汇报和重复 handoff；
- Standard 中 Reviewer/QA 的两次独立唤醒，合并为一个独立 Quality 实例；
- 无关任务的完成证据等待。

不能取消：

- 修改范围和 forbidden paths；
- 真实测试结果，不得伪造通过；
- Owner 自审禁令；
- 生产、真实数据、支付、资金、权限、密钥、migration、部署、删除和回滚授权；
- 目标环境和回滚信息；
- 同任务、同路径、同资源、同线程和同发布通道唯一持有者；
- 发布前的定向回归和独立质量结论；
- 实际 commit、构建、部署和线上状态的证据边界。

### 7.3 Standard/Strict 应急质量策略

- Light emergency：Owner 完成定向验证即可，不强制独立 Quality。
- Standard emergency：一个独立 Quality 实例合并 Reviewer 与 QA，只检查本次修复范围、定向回归和关键邻接风险。
- Strict emergency：仍需独立 Quality；Security、Data 或 Release 只有项目规则明确要求职责隔离时才单独存在，否则由 Coordinator/Quality 承担相容能力。
- Quality 可以在单个任务完成时立即验证，不必等待同波次所有独立任务结束。
- 最终集成后只做一次面向集成结果的必要回归，不重复每个任务已经通过且未受影响的检查。

## 8. Coordinator 的目标算法

当前 Run 级 fail-all Preflight 改为任务级聚合：

```python
capacity = available_capacity(run)
active_fingerprints = load_active_conflicts(run)

for task in ready_tasks_in_fair_order(run):
    report = run_preflight(run, task_id=task.id)

    if not report.ready:
        blocked_tasks.append(task_report(task, report))
        continue

    conflict = find_conflict(task, active_fingerprints)
    if conflict:
        deferred_tasks.append(conflict_report(task, conflict))
        continue

    if capacity == 0:
        deferred_tasks.append(capacity_report(task))
        continue

    executor = executor_pool.allocate(
        role=task.role_ref,
        capabilities=task.required_capabilities,
        workspace_policy=task.workspace_policy,
    )
    dispatch(task, executor)
    active_fingerprints.add(task.conflict_fingerprint)
    capacity -= 1
```

一次 tick 返回：

```json
{
  "dispatches": [],
  "blocked_tasks": [],
  "deferred_tasks": [],
  "resource_waits": [],
  "run_level_blockers": []
}
```

只有 manifest 损坏、Protocol 不可回放、用户撤销整个 Run、项目根目录失效等真正 Run 级问题，才允许 `run_level_blockers` 阻止全部派发。

## 9. 数据合同与兼容策略

### 9.1 Manifest 可选字段

```yaml
execution_profile: "emergency"
preflight_scope: "task"
executor_policy: "capability_pool"
executor_scale_authorized: true
max_parallel: 4
max_instances_per_role: {"owner": 4, "quality": 1}
incident_ref: "decisions/INCIDENT-001.yaml"
```

兼容默认值：

- 旧 Run 缺少 `preflight_scope` 时按 `run` 解释。
- 旧 Run 缺少 `executor_policy` 时按 `fixed` 解释。
- 旧 Run 不会因为安装新版 Skill 自动扩容或改变调度行为。
- `executor_scale_authorized` 缺失或为 false 时，不自动创建新 Native 线程。

### 9.2 Task 可选字段

```yaml
role_ref: "owner"
required_capabilities: ["frontend"]
logical_resources: []
environment_resources: []
workspace_policy: "isolated_writer"
release_lane: "none"
```

现有 `fixed` 和 `claimable` assignment 保持可读。新能力池任务仍维持单任务唯一 claim，但允许 claimant 使用某个角色模板下的新执行实例。

### 9.3 Claim 和事件的身份边界

- `agent_id`/`principal_agent_id` 表示稳定角色和权限主体。
- `executor_id` 表示一次实际运行实例。
- task claim、thread claim、ACK、lease、result、operation 和 invocation package 增加可选 `executor_id`。
- 旧记录缺少 `executor_id` 时，按 `executor_id = principal_agent_id` 兼容读取。
- Review 独立性按 `principal_agent_id` 和实现 lineage 判断，不能通过新建另一个 Owner 执行实例伪装独立 Reviewer。
- Protocol v3 的事件名称和状态机不变，因此不升 Protocol v4。

### 9.4 新目录

```text
runs/<run-id>/
  executors/
    EXEC-*.yaml
    releases/
  claims/
    tasks/
    threads/
```

`executors/` 属于外部治理空间，不写入目标项目，不影响网站或应用运行。

## 10. 详细实施任务

### Task 1：冻结 Emergency 与任务级调度合同

**Files:**

- Modify: `references/modes-and-gates.md`
- Modify: `references/interview-and-planning.md`
- Modify: `references/coordinator-runtime.md`
- Modify: `references/document-protocol.md`
- Modify: `assets/manifest.yaml.template`
- Modify: `assets/task.md.template`
- Modify: `assets/schemas/task.schema.json`
- Modify: `assets/schemas/preflight-result.schema.json`
- Create: `assets/executor-binding.yaml.template`
- Create: `assets/schemas/executor-binding.schema.json`
- Create: `tests/test_emergency_contract.py`

- [ ] 写失败测试：文档和模板必须同时定义 `emergency`、任务级门禁、能力池、同类型执行实例和硬门禁边界。
- [ ] 写失败测试：Protocol v3 事件状态机不得因新字段变化。
- [ ] 实现最小合同与 Schema；所有新字段保持可选，旧 fixture 继续有效。
- [ ] 运行：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_emergency_contract tests.test_protocol_v3 -v`
- [ ] 运行：`python3 -m json.tool assets/schemas/executor-binding.schema.json >/dev/null`

### Task 2：把全局 Preflight 改为任务级隔离

**Files:**

- Modify: `scripts/preflight_lib.py`
- Modify: `scripts/preflight_run.py`
- Modify: `scripts/completion_preflight.py`
- Modify: `scripts/coordinator.py`
- Modify: `tests/test_coordinator_runtime.py`
- Create: `tests/test_task_scoped_preflight.py`

- [ ] 写失败测试：TASK-A 缺任务字段时，TASK-B 独立且 ready，Coordinator 仍派发 TASK-B。
- [ ] 写失败测试：TASK-A 路径冲突、TASK-B 无冲突、TASK-C 缺授权时，三者分别进入 deferred/dispatch/blocked。
- [ ] 写失败测试：manifest 或事件历史损坏仍全 Run fail-closed。
- [ ] 将 `run_preflight(run_dir, task_id=...)` 作为 Coordinator 的派发单位。
- [ ] 保留 Run 级摘要，但不再使用一个任务的缺口触发 fail-all。
- [ ] Completion Preflight 同样按 task 关闭；release readiness 再聚合所有正式交付任务。
- [ ] 运行：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_task_scoped_preflight tests.test_coordinator_runtime -v`

### Task 3：实现 Run 内执行实例池

**Files:**

- Create: `scripts/executor_pool.py`
- Modify: `scripts/agent_claim.py`
- Modify: `scripts/claim_lib.py`
- Modify: `scripts/wake_agent.py`
- Modify: `scripts/adapters/codex.py`
- Modify: `scripts/adapters/hermes.py`
- Modify: `scripts/adapters/document.py`
- Modify: `scripts/validate_run.py`
- Create: `tests/test_executor_pool.py`
- Modify: `tests/test_optimization_features.py`

- [ ] 写失败测试：两个不重叠任务需要相同 `principal_agent_id=owner`，可获得两个不同 `executor_id` 并在同一 tick 派发。
- [ ] 写失败测试：两个执行实例抢同一 task，只有一个有效 claim。
- [ ] 写失败测试：同一 thread 仍只有一个 active claimant。
- [ ] 写失败测试：没有 `executor_scale_authorized` 时不得静默创建新 Native thread，只能复用合法实例或返回可执行缺口。
- [ ] 实现原子 Executor Binding、lease、release 和过期读取；不修改长期 TEAM。
- [ ] 把 `executor_id` 贯穿 claim、wake operation、Document fallback、ACK、lease 和 result 验证。
- [ ] 保持 `principal_agent_id` 的权限和独立 Review 约束。
- [ ] 运行：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_executor_pool tests.test_optimization_features -v`

### Task 4：实现冲突指纹和无队头阻塞调度

**Files:**

- Create: `scripts/conflict_model.py`
- Modify: `scripts/coordinator.py`
- Modify: `scripts/agent_dispatch.py`
- Modify: `scripts/manage_run.py`
- Modify: `scripts/resource_queue.py`
- Modify: `scripts/emit_event.py`
- Create: `tests/test_task_parallelism.py`
- Modify: `tests/test_coordinator_runtime.py`

- [ ] 写失败测试：同类型、不同路径、不同 logical resource 的任务同波次派发。
- [ ] 写失败测试：队列前方任务被共享资源阻塞时，后方无关任务仍派发。
- [ ] 写失败测试：父子 owned path、同一 logical resource、同一 version source 和同一 release lane 仍串行。
- [ ] 写失败测试：publication/event lock 只保护短事务，不能被解释为执行锁。
- [ ] 统一生成 dependency/path/logical/workspace/environment/release 六维冲突指纹。
- [ ] `max_parallel` 只计算活动任务数量；同类型实例不再有隐式 1 个上限。
- [ ] 资源队列按 `queue_key` 各自 FIFO；不同 queue key 之间不得互相阻塞。
- [ ] 运行：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_task_parallelism tests.test_coordinator_runtime -v`

### Task 5：实现 Emergency 路由和局部门禁

**Files:**

- Modify: `scripts/init_run.py`
- Modify: `scripts/freeze_scope.py`
- Modify: `scripts/preflight_lib.py`
- Modify: `scripts/completion_preflight.py`
- Modify: `references/modes-and-gates.md`
- Create: `tests/test_emergency_flow.py`

- [ ] 写失败测试：`light|standard|strict + emergency` 均可表达，但严格治理硬门禁不减少。
- [ ] 写失败测试：缺生产部署授权只阻止 release step，不阻止本地修复和测试 task。
- [ ] 写失败测试：Standard emergency 只要求一个独立合并 Quality，不要求 Reviewer/QA 两次唤醒。
- [ ] 写失败测试：Direct Hotfix 不创建治理文件。
- [ ] 增加最小 incident/scope/acceptance/rollback 输入，不复制完整治理问卷。
- [ ] 将门禁结果标注为 `task`、`resource_step`、`risk_step` 或 `release_lane` 作用域。
- [ ] 运行：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_emergency_flow tests.test_external_run_storage -v`

### Task 6：并发写工作区与串行集成

**Files:**

- Modify: `scripts/executor_pool.py`
- Modify: `scripts/preflight_lib.py`
- Modify: `scripts/completion_preflight.py`
- Modify: `references/coordinator-runtime.md`
- Create: `tests/test_parallel_worktree_policy.py`

- [ ] 写失败测试：两个写实例绑定同一 worktree 时拒绝并行。
- [ ] 写失败测试：两个独立 worktree、不同 owned paths 时允许并行。
- [ ] 写失败测试：并行工作完成后，merge、版本源修改、RC 和 release lane 仍一次只允许一个任务。
- [ ] 实现 `isolated_writer|shared_read_only|shared_no_git_mutation` 三种工作区策略。
- [ ] 不在 Skill 中自动删除 worktree；清理由明确的完成或人工操作触发，并保留未提交工作。
- [ ] 运行：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_parallel_worktree_policy -v`

### Task 7：更新用户入口、说明和兼容迁移

**Files:**

- Modify: `SKILL.md`
- Modify: `README.md`
- Modify: `agents.html`
- Modify: `agents/openai.yaml`
- Modify: `references/README.md`
- Modify: `references/agent-catalog.md`
- Modify: `scripts/README.md`
- Modify: `scripts/migrate_run_optimization.py`
- Modify: `tests/test_agent_catalog.py`
- Modify: `tests/test_skill_contract.py`

- [ ] 把“紧急修 Bug”路由放到用户入口最前面：单任务默认 Direct Hotfix，多独立任务才建议 Coordinated Emergency。
- [ ] 明确 agents.html 仍只展示角色和启动方式，不显示运行状态、不自动编排、不列出临时执行实例。
- [ ] 把“不得为了并行新增 Agent”改写为“不得增加长期角色；允许在授权并行额度内建立同角色短期执行实例”。
- [ ] 扩展现有迁移工具，为旧 Run 提供 dry-run/apply/rollback；默认不改变旧 Run 行为。
- [ ] 文档必须给出同类型实例并行、冲突任务串行和发布通道串行的完整示例。
- [ ] 运行：`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_agent_catalog tests.test_skill_contract -v`

### Task 8：版本更新与全量验证

**Files:**

- Modify: `VERSION`
- Modify: `CHANGELOG.md`
- Modify: `README.md`
- Modify: `SKILL.md`
- Modify: `agents/openai.yaml`
- Modify: `tests/README.md`

- [ ] 将 Skill 版本统一更新为 `2.1.0`。
- [ ] 记录 Protocol v3 未变化、Executor Binding Schema 1.0、Preflight Result Schema 1.1 和 Governance Storage Schema 1.1。
- [ ] 明确本次版本变更不自动修改任何目标项目业务版本。
- [ ] 运行完整 `unittest`、所有 JSON Schema 解析、Python 编译、CLI `--help`、Markdown 链接和 `git diff --check`。
- [ ] 输出真实通过数；任何未安装的测试框架或未执行的远程验证必须单独说明，不得冒充通过。

## 11. 关键验收场景

### 场景 A：单一紧急 Bug

- 用户说“紧急修这个 Bug”。
- 当前 Agent 直接读取、修复和定向验证。
- 不创建 Run，不等待 Coordinator，不建立多个 Agent。
- 涉及部署时，只在部署动作前请求授权。

### 场景 B：两个同类型但完全独立的前端 Bug

- TASK-A 修改 `src/a/**`。
- TASK-B 修改 `src/b/**`。
- 两者均需要 Owner/Frontend 能力。
- 调度器创建两个不同 executor binding，在两个隔离 worktree 中同时工作。
- 不要求 TASK-B 找到 TASK-A 的 Agent 排队。

### 场景 C：三个任务中一个门禁不完整

- TASK-A 缺高风险授权，进入 `blocked_tasks`。
- TASK-B 无冲突，立即派发。
- TASK-C 与 TASK-B 共享 package lock，只等待相应 resource step。
- Run 不返回全局 `preflight_blocked`。

### 场景 D：两个实例抢同一任务

- 两个实例同时请求 TASK-D。
- task claim 锁下重新校验。
- 只有一个 active claim；另一个立即获得持有者、到期时间和下一动作。
- 不忙轮询、不静默接管。

### 场景 E：生产紧急修复

- 修复、单元测试、构建和本地回归可先并行推进。
- 生产凭据、部署、重启、真实数据和回滚仍需明确授权。
- Release lane 串行，只发布经选择和验证的集成 commit。
- 本地通过、MG 通过和生产通过分开报告。

### 场景 F：两个竞争性解决方案处理同一 Bug

- 可让多个实例在不同 worktree 中独立诊断或形成候选补丁。
- 每个候选仍绑定自己的 attempt 和验证。
- 只选择一个候选进入集成；不得把两个相互替代的修复都合并。
- 未选候选标记 superseded，不删除其证据。

## 12. 验证命令

实施完成后至少执行：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_emergency_contract \
  tests.test_task_scoped_preflight \
  tests.test_executor_pool \
  tests.test_task_parallelism \
  tests.test_emergency_flow \
  tests.test_parallel_worktree_policy -v

PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v

python3 -m compileall -q scripts tests

for schema in assets/schemas/*.json; do
  python3 -m json.tool "$schema" >/dev/null || exit 1
done

for script in scripts/*.py; do
  python3 "$script" --help >/dev/null || exit 1
done

git diff --check
git status --short
```

还必须做一次临时目录端到端演练：创建 3 个任务，其中 2 个使用同一角色但路径独立，1 个存在门禁缺口；断言同一 tick 派发前 2 个且只阻塞第 3 个。

## 13. 成功标准

实现完成后必须同时满足：

1. 单一紧急 Bug 默认可走 Direct Hotfix，不创建治理资料。
2. Coordinated Emergency 不需要完整 Run 资料就开始低风险、本地可逆的修复步骤。
3. 任一任务的普通缺口不会阻塞无关 ready task。
4. 两个同类型、不同任务、无冲突的执行实例可在同一 tick 派发。
5. 同一任务、路径、逻辑资源、线程、worktree 写操作和发布通道仍严格唯一。
6. `max_parallel` 是容量上限，不是 Agent 角色数量。
7. 不新增长期角色；临时执行实例不会污染 TEAM 或 agents.html。
8. Standard/Strict 仍禁止 Owner 自审。
9. Strict emergency 不能绕过生产、数据、迁移、支付、权限、密钥、部署、删除或回滚授权。
10. 旧 Protocol v3 Run 默认行为不变，并能继续验证。
11. Skill 版本、Schema 版本和项目业务版本边界清楚。
12. 所有测试结果按实际证据报告。

## 14. 风险与回滚

| 风险 | 控制 |
| --- | --- |
| 同角色多个实例导致身份混淆 | `principal_agent_id` 与 `executor_id` 分离，所有 attempt/claim/result 绑定 executor |
| 不同目录实际共享隐藏资源 | 冲突指纹包含 logical/workspace/environment/release，不只比较文件路径 |
| 并发 worktree 合并冲突 | 实现并行、集成串行；冲突回到明确 Owner，不自动覆盖 |
| Emergency 被当作越权入口 | 高风险门禁按动作作用域保留，文档与测试覆盖 Strict emergency |
| 动态实例造成 Agent 臃肿 | 实例只在 Run 内存在，不进入长期目录和用户角色页 |
| 旧 Run 行为漂移 | 新字段缺失时使用旧默认；迁移必须显式 dry-run/apply/rollback |
| 调度器复杂度上升 | 冲突模型单独模块化，Coordinator 只消费确定性决策结果 |

发生问题时的回滚顺序：

1. 把新 Run 的 `executor_policy` 切回 `fixed`。
2. 把 `preflight_scope` 切回 `run`。
3. 把 `execution_profile` 切回 `normal`。
4. 停止创建新执行实例，保留现有不可变 claim/result 证据。
5. 不删除 worktree 或未提交修复；由人工确认后收口。

## 15. 本轮非目标

- 不开发实时编排页面或状态看板。
- 不让 `agents.html` 自动创建、调度或监控 Agent。
- 不建立常驻 daemon、远程队列服务或自动扩缩容平台。
- 不为 Emergency、Queue、Version、Release 单独增加长期 Agent。
- 不自动判断或执行生产发布。
- 不改变目标项目的业务逻辑或业务版本。
- 不通过提高默认委派深度来换取速度。

## 16. 推荐实施顺序

按以下顺序实施，避免一次改动过大：

1. **P0：任务级 Preflight + Emergency 路由。** 先消除一个任务阻塞整个 Run，并让单 Bug 走 Direct Hotfix。
2. **P0：同角色多执行实例。** 再解决同类型任务排队，保持单任务唯一 claim。
3. **P0：冲突指纹 + worktree 隔离。** 确保并行基于真实资源边界。
4. **P1：Quality 与发布门禁局部化。** 减少重复交接，同时保留高风险边界。
5. **P1：兼容迁移、文档和 2.1.0 版本收口。** 旧 Run 默认不变，新 Run 显式启用。

本计划的关键不是“创建更多 Agent”，而是允许一个最小角色体系在真实存在独立任务时临时增加执行容量；容量随任务结束释放，治理边界仍由任务、资源和发布动作控制。

## 17. 执行记录

执行日期：2026-08-10

本计划已在当前 Skill 仓库完成实现，版本更新为 `2.1.0`。本次实现保持 Protocol v3 不变，并将执行实例、任务级门禁和治理存储作为向后兼容的附加能力：

- Emergency 默认使用任务级 Preflight；单个任务缺口只阻塞该任务，Run 级损坏或高风险硬门禁仍 fail-closed。
- Direct Hotfix 不创建治理 Run；Coordinated Emergency 支持最小任务范围、事件、验收与回滚事实。
- 同一稳定角色可按授权的能力池创建多个短生命周期执行实例；`principal_agent_id`、`executor_id`、task、attempt、thread、workspace 和 lease 全部绑定并可追溯。
- 不同路径、逻辑资源和工作区的独立任务可以同一 tick 并行；同一任务、路径、资源、Native thread、worktree 写入和 release lane 继续串行。
- 执行实例支持 claim、wake、ACK、lease、result、release 和 stale lease expiry；释放记录不可变且可幂等重放。
- 旧 Run 缺少新字段时继续使用旧的 run-wide preflight 与 fixed executor 默认，迁移工具支持兼容补全。

已完成验证：

1. `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'`：**259 tests，OK**。
2. Python `compileall`、全部 JSON Schema 解析、全部脚本 `--help`：通过。
3. Markdown 本地链接检查：51 条链接通过。
4. `git diff --check`：通过。
5. 临时目录端到端演练：两个同角色且路径独立的任务同 tick 派发，第三个门禁不完整任务单独阻塞；执行实例数量为 2。

未执行且不由本地实现冒充的事项：远程 GitHub 推送、Pull Request、目标项目 MG/生产部署和真实外部服务验证。上述动作需在集成方式与外部授权明确后单独执行。
