# Tests 说明

测试套件同时覆盖 Protocol v3 Run 层和长期 Agent 层。所有测试使用临时目录，不依赖外部网络、
真实凭据或用户项目；新增行为必须至少包含一个成功路径和一个 fail-closed 路径。

## 运行完整测试

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m compileall -q scripts tests
```

## 覆盖范围

| 领域 | 主要验证 |
| --- | --- |
| Protocol v3 | 初始化、任务/事件状态机、ACK、lease、锁、Review、QA、人工门禁、RC 与发布 |
| 长期身份 | TEAM、ROLE、SYSTEM_PROMPT、AGENT_PROFILE、Session Map 和生命周期 |
| Runtime Profile | actual/declared、unknown/conflict、Secret、不可变编号、哈希链、并发和事务回滚 |
| Activity Ledger | Run/Task/Attempt/Agent 归属、usage receipt、索引、并发和桥接回滚 |
| 归档与恢复 | 增量 archive、checkpoint、handoff、resume drift、跨平台路径和敏感信息 |
| 项目收口 | Run bridge、PCP、模型分配视图、index、validator、finalize 和来源漂移 |
| 迁移与修复 | dry-run、plan hash、幂等、备份、quarantine、故障注入和回滚 |
| 适配器 | document fallback、Hermes/Codex 显式 bridge、会话/workspace 边界和不误报唤醒 |
| 用户入口 | `agents.html` 角色卡、最小启动表单、手动复制边界和无运行状态约束 |

## 发布门禁

发布前必须运行：

1. 两轮完整 `pytest`；
2. `unittest discover`；
3. `compileall`；
4. 所有顶层 `scripts/*.py --help`；
5. 所有 `assets/schemas/*.json` 的 stdlib JSON 解析；
6. Markdown 本地链接检查；
7. `git diff --check`。

当前 v1.3.0 本地回归为：`python -m unittest discover -s tests -p 'test_*.py'` 的 202 tests OK，
`compileall`、顶层 CLI help、8/8 JSON Schema、HTML/JavaScript 静态检查和本地链接检查通过。
这些数字是发布证据，不是未来版本的固定断言；测试变化后应以最新真实命令输出更新。
