# Tests 说明

`test_protocol_v3.py` 是协议 v3 的端到端回归测试，使用临时项目验证真实文档、事件、
Git 和权限闭环。

运行：

```bash
PYTHONDONTWRITEBYTECODE=1 \
python3 -m unittest discover -s tests -v
```

当前主要覆盖：

- 初始化确认、Run 隔离和协议字段。
- 最小必要智能体、Reviewer/QA 合并和 Owner 自审阻断。
- owned/forbidden paths、canonical path 和资源锁。
- 任务状态机、ACK、lease、重试、dead letter 和不可变 attempt。
- Review、QA、人工门禁和严格发布。
- 项目版本合同、版本源漂移、RC 和 Release Train。
- Codex 原生 operation 与通用受管子代理 binding。
- payload/evidence hash 篡改检测、恢复和归档。

新增行为必须至少包含一个成功路径和一个 fail-closed 路径。测试不得依赖外部网络、真实
凭据或用户项目。
