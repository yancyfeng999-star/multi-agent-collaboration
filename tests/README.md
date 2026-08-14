# Tests 说明

测试套件同时覆盖 Protocol v3 Run 层和长期 Agent 层。所有测试使用临时目录，不依赖外部网络、
真实凭据或用户项目；新增行为必须至少包含一个成功路径和一个 fail-closed 路径。

## 运行完整测试

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
python3 -m compileall -q scripts tests

# 已安装 pytest 时可额外用它复跑同一套 unittest 用例
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
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
| 1.4.x 快车道与自助协同 | execution/dispatch policy、scope/preflight、工作 Agent 子任务发布、任务/线程串行 claim、超时恢复和候选索引 |
| 2.0.0 外置开发治理 | Direct 零写入、Coordinated 外置绑定、Agent/Run 侧车存储、旧资料事务迁移、项目零运行依赖与旧目录 Git 门禁 |
| 2.1.0 Emergency 并行执行 | Direct Hotfix/Coordinated Emergency 路由、任务级 Preflight、同角色 executor pool、冲突指纹、worktree policy、executor release 和 Storage Schema 1.1 兼容 |
| 2.2.0 通用集成治理 | 证据化模式路由、可选 Integration Policy、独立候选并行评估、串行 Integration Lane、Release Freeze、六层证据、worktree 安全收口和四类摘要消息 |

## 发布门禁

发布前必须运行：

1. 一轮完整 `unittest discover`（权威、零第三方依赖）；
2. 环境已安装 pytest 时，额外运行两轮完整 `pytest`；未安装时如实记录，不临时下载依赖；
3. `compileall`；
4. 所有顶层 `scripts/*.py --help`；
5. 所有 `assets/schemas/*.json` 的 stdlib JSON 解析；
6. Markdown 本地链接检查；
7. `git diff --check`。

当前 v2.2.0 基线必须同时覆盖 Protocol v3、Emergency 任务级调度、短期 executor、外置 Governance Home、通用候选集成、freeze、证据分层、worktree 收口和摘要消息。
本次实现用 `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -q`
验证的真实测试数以最终运行输出为准；同时要求顶层 CLI help、JSON Schema、
HTML/JavaScript 静态检查、Markdown 本地链接和 `git diff --check` 通过。
这些数字是发布证据，不是未来版本的固定断言；测试变化后应以最新真实命令输出更新。
