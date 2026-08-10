# Protocol v3 Run → 长期 Agent 记忆桥接

`archive_run_to_agents.py` 将已通过 completion 验证的 Run 事实沉淀到同一外部 governance project 的长期 Agent 层。它不是 Run archive 命令，不修改 Run。

## 前置条件

1. `project-binding.yaml` 存在且指向真实项目根。
2. `TEAM.yaml` 与 `agents/Axx-*/` 已在同一 governance project 初始化。
3. Run 位于该 governance project 的 `runs/<run-id>/`，所有任务进入终态并通过：

```bash
python3 scripts/validate_run.py "<run-dir>" --phase completion
```

4. 每个 run-local Owner 都有显式的 persistent Agent 映射；脚本不按角色名猜测身份。

## 使用

```bash
python3 scripts/archive_run_to_agents.py \
  --run-dir "<governance-home>/projects/<project-key>/runs/RUN-123" \
  --agent-map worker=A02-worker \
  --agent-map quality=A03-quality \
  --dry-run
```

确认后移除 `--dry-run`。成功输出：

```text
<governance-project>/bridges/RUN-123.json
```

## 路径与哈希

- Run task/result/evidence 是 governance source，必须位于当前 governance project。
- changed files 和业务 artifact 是 project source，必须位于 binding `allowed_roots`。
- Bridge 使用 `governance://` 和 `project://` 相对引用区分两类来源，并保存 SHA-256。
- 外部业务 artifact 本体不复制到治理层，只记录规范化路径和 hash。

## 不变量

- 每次执行（包括幂等重跑）都重新运行 completion validator。
- Run inventory hash 排除锁文件，不修改 Run 任何内容。
- 任意产物 hash 不匹配时，首次写入前失败。
- 所有预期目标存在且 hash 一致时幂等成功；部分目标或内容冲突时 fail-closed，不覆盖。
- 写入由 governance project 级 `.run-memory-bridge.lock` 串行。
- dry-run 只验证、构造计划和检查冲突，无副作用。

合法更正必须创建新 Run 或新的不可变协议记录，不覆盖已桥接事实。
