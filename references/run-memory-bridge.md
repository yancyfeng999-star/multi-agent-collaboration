# Protocol v3 Run → 长期 Agent 记忆桥接

`archive_run_to_agents.py` 把已经通过 **completion** 验证的 Protocol v3 Run 事实沉淀到项目内的长期 Agent 存储。它不是 Run archive 命令，也不会改变 Run 的任何文件。

## 前置条件

1. 项目已用 `init_project_agents.py` 初始化长期 Agent（`.multi-agent-collaboration/TEAM.yaml` 与 `agents/Axx-*/`）。
2. Run 与长期 Agent 存储位于同一个项目的 `.multi-agent-collaboration/`。
3. Run 的所有任务均已进入终态，并能通过：

```bash
python3 scripts/validate_run.py <run-dir> --phase completion
```

4. 每个任务 Owner 都有明确的 run-local → persistent Agent 映射。脚本不按角色名猜测身份。

## 使用

先预览（不创建目录、不写文件）：

```bash
python3 scripts/archive_run_to_agents.py \
  --run-dir <project>/.multi-agent-collaboration/runs/RUN-123 \
  --agent-map worker=A02-worker \
  --agent-map reviewer=A03-reviewer \
  --dry-run
```

确认后执行同一命令并移除 `--dry-run`。成功时 stdout 是桥接 manifest 路径：

```text
<project>/.multi-agent-collaboration/bridges/RUN-123.json
```

## 写入布局

对于任务 `TASK-001`、Run `RUN-123`、目标长期 Agent `A02-worker`：

- `agents/A02-worker/tasks/RUN-123--TASK-001.md`：冻结 task 的字节级镜像。
- `agents/A02-worker/handoffs/RUN-123--TASK-001--ATTEMPT-001.md`：owner result/handoff 镜像。
- `agents/A02-worker/artifacts/RUN-123--TASK-001--evidence--<evidence-id>.yaml`：任务 evidence 镜像。
- `agents/A02-worker/artifacts/RUN-123--TASK-001--bundle.json`：evidence、handoff、外部 artifact 的来源路径、相对路径和 SHA-256 元数据。
- `bridges/RUN-123.json`：全 Run 桥接 manifest，记录 Run id、最终 event sequence、Run/manifest/state 来源路径与哈希、Agent 映射和每个任务的归档目标。

外部 artifact 本体不复制；bundle 保存其规范化 source path 与 SHA-256 引用。task、result 和 evidence 属于 Run 冻结事实，按原字节镜像。

## 安全与不变量

- **先验证后写入**：每次执行（包括幂等重跑）都重新运行 completion validator。
- **Run 只读**：脚本不写 `manifest.yaml`、`state.yaml`、`events/`，也不写 Run 内其他文件；Run inventory hash 排除锁文件 `.sequence.lock`。
- **哈希绑定**：evidence 声明的 `artifact_hashes` 必须与实际 artifact 一致，否则在首次写入前失败。
- **显式身份映射**：映射目标必须存在于 `TEAM.yaml`，遗漏任务 Owner 或指向未知 Agent 都失败。
- **幂等**：所有预期目标均存在且哈希完全一致时直接成功，不覆盖、不更新时间戳。
- **fail-closed 冲突**：任一目标内容不同，或只存在部分目标集合，整次执行失败且不覆盖冲突文件。
- **并发串行化**：真实写入使用项目级 `.run-memory-bridge.lock`。
- **dry-run 无副作用**：只验证、构造计划并检查已有目标冲突，stdout 输出 JSON 计划。

## 篡改响应

- Run task/result/evidence/event 或 evidence 引用的 artifact 被篡改：Run validator 或 artifact hash 检查失败，不写长期存储。
- 已桥接镜像或 bridge manifest 被篡改：下一次运行报告 immutable destination conflict，不自动修复。
- 发生合法更正时，应生成新的 Run 或新的不可变协议记录；不要覆盖已桥接事实。
