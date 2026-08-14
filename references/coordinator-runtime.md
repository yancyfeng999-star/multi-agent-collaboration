# Coordinator runtime

`coordinator.py` is a bounded protocol-v3 scheduler. One invocation performs one tick; it never starts an implicit polling loop.

Run manifest additionally records `execution_profile` (`emergency`/`fast`/`normal`), `preflight_scope`,
`executor_policy`, `executor_scale_authorized` and `dispatch_policy` (`central`/`hybrid`/`self_service`).
The profile reduces waiting strategy only; it never grants additional paths or release authority.

## One tick

```bash
python3 scripts/coordinator.py --run-dir '<governance-root>/projects/<project-key>/runs/RUN-ID' --once
python3 scripts/coordinator.py --run-dir '<governance-root>/projects/<project-key>/runs/RUN-ID' --dry-run
```

The tick reads the run-local manifest, registry, frozen tasks, immutable events, active claims and locks.
It replays task state, selects only tasks whose dependencies are completed, subtracts active tasks from
`max_parallel`, and evaluates dependency/path/logical/workspace/environment/release conflict
fingerprints. Selection is deterministic by manifest task order, but a blocked task does not consume a
slot for unrelated tasks.

For `execution_profile=emergency`, a non-central Run uses task-scoped Preflight. Light/Standard
low-risk local work does not require a complete Run-level scope freeze; Strict Emergency still does.
A missing gate,
scope, capability or executor is returned in `blocked_tasks`; a capacity or resource collision is
returned in `deferred_tasks`/`resource_waits`; only malformed Run-wide state appears in
`run_level_blockers`. Legacy Runs without `preflight_scope` retain run-scoped behavior.

For new work it can emit `TASK_READY` and `TASK_DISPATCHED` through the existing safe `emit_event.py`, then calls `wake_agent.py`. `--dry-run` writes neither events, operations, nor invocation packages. `--no-emit-events` is preview-only and must be combined with `--dry-run`; real delivery without protocol events is rejected.

The coordinator does not fabricate Review, QA, retry, dead-letter, or release evidence. Those transitions remain evidence-driven Protocol v3 events. Timeout results are actionable recommendations, not claims that recovery already happened.

For non-central Runs with a frozen scope, the tick runs the read-only preflight gate first. In task
scope, only the affected task is blocked. A claimable task is made `ready` for the pool and is not
woken by Coordinator; an eligible Agent must acquire the task claim, emit the claimant-owned
`TASK_DISPATCHED`, and wake itself. This keeps the ready wave bounded without making Coordinator a
bottleneck.

### Self-service publication

An authorized worker can use `agent_dispatch.py publish` only when its Agent Registry contains
`task_publish`, the policy is `hybrid` or `self_service`, and the child paths are within the parent
owned paths or frozen scope. The publication lock serializes task creation and the two task events.
Fixed-owner children are then delivered directly; pooled children stop at `TASK_READY` until an
eligible claimant uses `agent_claim.py claim-task`.

Task claims and thread claims use separate locks and immutable lease records. A second claimant gets
the current holder and an actionable conflict; it never overwrites the first claim. `wake_agent.py`,
`manage_run.py write-ack/write-lease/write-result`, and event validation resolve a pooled task to
its current claimant so the rest of the lifecycle remains serial and auditable.

### Capability pool and worktree safety

`principal_agent_id` is the stable permission identity; `executor_id` is one task attempt. A single
principal may receive multiple executor bindings when tasks are independent and the Run has capacity.
Bindings are written to `executors/EXEC-*.yaml`, and release/expiry facts are appended under
`executors/releases/`. Native runtimes require `executor_scale_authorized=true` before a new binding
is created. `isolated_writer` bindings cannot share a worktree; `shared_read_only` may share one. The
same checks run for Coordinator dispatch and self-service claim/publication, so a worker does not
bypass conflict serialization by waking its child directly.

## Wake operations and adapters

Every wake has a deterministic operation ID derived from run/task/agent/task hash. The operation JSON under `runs/<run>/operations/` is immutable; a restart reuses byte-equivalent operation and inbox package rather than delivering duplicates.

The document adapter writes a real JSON invocation package to the registered owner's run-local inbox. The package includes workspace, exact task path/hash, owned/forbidden paths, and ACK/result instructions.

Hermes and Codex adapters are **unsupported unless an explicit external CLI/API bridge command is supplied**. `wake_agent.py` never discovers or guesses a command and never reports a wake merely because a binary exists. Before an external command is allowed, it validates a supplied `SESSION_MAP.json` for:

- stable agent identity;
- requested platform;
- active session ID;
- exact project workspace.

Unsupported or failed external delivery falls back to the document adapter and reports `fallback_document`, never `woken`.

## Timeout report

A tick scans dispatched tasks for ACK timeout and running tasks for expired latest lease. It reports `retry` while event/attempt evidence is below `max_attempts`, otherwise `dead_letter`. It does not invent a failure event or side-effect state, so it only prints the existing safe CLI constraint rather than calling `write-dead-letter` without valid evidence. Operators must inspect side effects, persist a real failure, then use `manage_run.py write-dead-letter` when its protocol preconditions are met.

`recover_timeout.py` records a bounded `block` recommendation or refuses an unsafe retry until a
real owner failure/side-effect record exists. `fast` does not bypass this rule.

## Guarantees and limits

- Process restarts are idempotent for operation/package creation.
- Active/malformed locks fail closed; expired locks do not block a wave.
- External execution success means the configured command exited zero; document fallback means only that a package was durably written.
- No default daemon, busy polling, fabricated ACK, lease, result, or wake state.

## Candidate and serial integration lane

独立 Agent 可以在各自工作区形成候选，不必唤醒 Coordinator 才能开始工作。候选 JSON 必须记录
baseline/candidate commit、实际 changed paths、验证结果、依赖、逻辑资源、workspace、环境资源、
版本权威引用、migration order 和 release lane。`integration_lane.py evaluate` 只读校验 Git 事实，
并且只把有真实交集的候选标为 `conflicted`；无交集的候选可以同时保持 `ready`。

```bash
python3 scripts/integration_lane.py evaluate \
  --project-root "<project-root>" \
  --policy "<external-policy>" \
  --candidate "<candidate-json>" \
  --against-candidate "<other-candidate-json>"
```

写入只能由一个串行 Integration Lane 完成，并且要求策略存在、当前工作区干净、目标分支没有
被其它 worktree 检出、用户明确确认、候选基线仍与目标兼容、`git merge-tree` 预检通过。默认
集成方法会用 fast-forward 或 merge 保留 candidate commit 的可达性；`fast_forward_only` 只接受
目标是 candidate 的祖先。任何失败都停止当前候选，不会 reset、clean、force 或移动无关候选。

```bash
python3 scripts/integration_lane.py integrate \
  --project-root "<project-root>" \
  --policy "<external-policy>" \
  --candidate "<candidate-json>" \
  --target working \
  --user-confirmed
```
