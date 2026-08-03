# Scripts 使用说明

脚本实现协议 v3 的本地文档总线。所有命令只应在用户确认的项目范围内执行。

| 脚本 | 职责 |
| --- | --- |
| `init_run.py` | 用户确认后初始化项目总线、Run、版本合同和初始门禁 |
| `manage_run.py` | 创建 Agent/任务/证据/RC，管理 ACK、lease、锁、恢复和归档 |
| `emit_event.py` | 校验并原子写入事件，然后重建派生状态 |
| `validate_run.py` | 对 structure、dispatch、completion 或 release 执行 fail-closed 校验 |
| `protocol_lib.py` | YAML 子集、路径、hash、状态机和运行文档的共享实现 |

## 推荐顺序

```text
只读扫描与用户确认
→ init_run.py
→ manage_run.py add-agent
→ manage_run.py create-task
→ emit_event.py TASK_READY
→ emit_event.py TASK_DISPATCHED
→ ACK / lease / result / Review / QA
→ validate_run.py
→ manage_run.py archive-run
```

版本化发布在 QA 和发布门禁之间增加：

```text
manage_run.py record-release-candidate
→ RELEASE_READY
→ validate_run.py --phase release
```

## 获取精确参数

```bash
python3 scripts/init_run.py --help
python3 scripts/manage_run.py --help
python3 scripts/manage_run.py <subcommand> --help
python3 scripts/emit_event.py --help
python3 scripts/validate_run.py --help
```

## 维护约定

- 不绕过 `protocol_lib.py` 的 canonical path、SHA-256 和原子写入能力。
- 新事件必须同时更新状态机、actor/payload 校验、模板、规范和测试。
- 新 manifest/task 字段必须同步初始化器、管理器、事件器和验证器。
- 行为不兼容时递增协议版本，不静默解释旧 Run。
- 生成的 `__pycache__` 和 `.test-tmp` 不进入交付目录。
