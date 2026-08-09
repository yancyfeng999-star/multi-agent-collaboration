# Coordinator runtime

`coordinator.py` is a bounded protocol-v3 scheduler. One invocation performs one tick; it never starts an implicit polling loop.

Run manifest additionally records `execution_profile` (`fast`/`normal`) and `dispatch_policy`
(`central`/`hybrid`/`self_service`). The profile reduces waiting strategy only; it never grants
additional paths or release authority.

## One tick

```bash
python3 scripts/coordinator.py --run-dir /absolute/path/to/.multi-agent-collaboration/runs/RUN-ID --once
python3 scripts/coordinator.py --run-dir /absolute/path/to/.multi-agent-collaboration/runs/RUN-ID --dry-run
```

The tick reads the run-local manifest, registry, frozen tasks, immutable events, and active locks. It replays task state, selects only tasks whose dependencies are completed, subtracts active tasks from `max_parallel`, and rejects overlapping `owned_paths` or active path locks. Selection is deterministic by manifest task order.

For new work it can emit `TASK_READY` and `TASK_DISPATCHED` through the existing safe `emit_event.py`, then calls `wake_agent.py`. `--dry-run` writes neither events, operations, nor invocation packages. `--no-emit-events` is preview-only and must be combined with `--dry-run`; real delivery without protocol events is rejected.

The coordinator does not fabricate Review, QA, retry, dead-letter, or release evidence. Those transitions remain evidence-driven Protocol v3 events. Timeout results are actionable recommendations, not claims that recovery already happened.

For non-central Runs with a frozen scope, the tick runs the read-only preflight gate first. A blocked
preflight returns the full report and dispatches nothing. A claimable task is made `ready` for the
pool and is not woken by Coordinator; an eligible Agent must acquire the task claim, emit the
claimant-owned `TASK_DISPATCHED`, and wake itself. This keeps the ready wave bounded without making
Coordinator a bottleneck.

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
