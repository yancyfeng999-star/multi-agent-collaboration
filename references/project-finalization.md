# Project Checkpoints and Finalization

## Project checkpoint

```bash
python3 scripts/create_project_checkpoint.py \
  --project-root /absolute/project \
  --run-id RUN-001
python3 scripts/rebuild_index.py --project-root /absolute/project
```

The command reads `TEAM.yaml`, each Agent's `CURRENT_CONTEXT.md` and latest CP/handoff, selected Run manifest/state/summary, `DECISIONS.md`, and live Git state. It creates an exclusive `PCP-XXXX.md` under `project-checkpoints/`, binds every source by SHA-256, links the previous PCP, and atomically refreshes `CURRENT_PROJECT_CONTEXT.md`.

Use `--dry-run` to validate and preview the next ID without writing. Creation is serialized by `.project-checkpoint.lock`; existing PCP files are never overwritten.

## Final project closure

```bash
python3 scripts/finalize_project.py \
  --project-root /absolute/project \
  --run-id RUN-001
```

Finalization fails closed unless every associated Run is completed/archived and bridged to persistent Agents, the latest PCP covers exactly the selected Runs, the deterministic index contains that PCP, every task is completion-safe terminal, human/release gates are approved, and both the persistent-Agent validator and Run completion/release validators pass. It then atomically creates:

- `PROJECT_FINAL_REPORT.md`: tasks, Agents, Runs, decisions, handoffs, evidence, risks, unresolved items, approvals/risk acceptance.
- `AUDIT_MANIFEST.json`: associated Runs, validator result, source SHA-256 bindings, report hash.
- `ARTIFACT_INDEX.jsonl`: deterministic artifact inventory.

`--dry-run` performs all gates without writing. `.project-finalization.lock` serializes writers. A complete existing bundle returns `already_finalized` without mutation; a partial bundle or a different Run set is rejected rather than repaired or overwritten.

## Operating rules

- Run from a stable filesystem snapshot; source mutation after a checkpoint is detectable through hashes.
- Do not edit PCP or final bundle files manually.
- Resolve pending gates, failed validation, nonterminal tasks, and unresolved verification before retrying.
- Finalization records residual risks and approvals; it does not silently accept risk.
