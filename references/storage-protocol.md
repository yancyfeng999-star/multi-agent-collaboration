# 项目本地存储协议

## 权威来源

优先级：实际项目文件/外部状态与 Git → 协议 v3 事件与 `state.yaml` → 项目级上下文 → Agent 当前上下文与检查点 → 平台会话描述。

平台会话是运行时；`.multi-agent-collaboration/` 是可移植的长期协作档案。复制项目目录后，任何可读取项目文件的平台都应能恢复工作。

## 持久层

```text
.multi-agent-collaboration/
├── TEAM.yaml
├── PROTOCOL.md
├── CURRENT_PROJECT_CONTEXT.md
├── DECISIONS.md
├── INDEX.md
├── index.jsonl
├── schemas/
├── templates/
├── agents/<agent-id>/
│   ├── ROLE.md
│   ├── SYSTEM_PROMPT.md
│   ├── CHECKLIST.md
│   ├── conversations/{CURRENT_CONTEXT.md,SESSION_MAP.json,INDEX.md,archive/,checkpoints/}
│   ├── tasks/
│   ├── handoffs/
│   └── artifacts/
└── runs/<run-id>/...
```

`runs/` 继续承担协议 v3 的不可变任务、事件、ACK、lease、result、Review、QA、版本合同、
scope freeze、retry policy 和 `claims/tasks`/`claims/threads`；`agents/` 承担跨 Run 的稳定
身份、可恢复上下文和会话镜像。不能用长期上下文覆盖 Run 事实。claim 文件只记录短期
串行占用和 lease，不是第二套任务状态机。

## 写入所有权

- 初始化器：TEAM、PROTOCOL、schemas、templates、初始 Agent 身份文件。
- Coordinator：项目上下文、决策、Agent 任务。
- 对应 Agent：自己的 CURRENT_CONTEXT、handoffs、artifacts。
- 专用脚本：SESSION_MAP、archive、checkpoints、INDEX/index.jsonl。
- 协议 v3 文件仍遵守 `document-protocol.md` 的唯一写入者规则。

不可变 archive、checkpoint 和 handoff 一旦被引用，不得覆盖；更正应生成新文件并保留替代关系。

## 检索顺序

`CURRENT_PROJECT_CONTEXT.md` → 项目 `INDEX.md` → Agent `CURRENT_CONTEXT.md` → Agent `INDEX.md` → 最新 checkpoint → 当前 task/handoff → 按需读取 archive → 检查真实文件与 Git。

## 安全

不落盘密码、API Key、Cookie、access/refresh token、私钥、支付数据或未脱敏个人数据。会话同步默认脱敏；会话映射只能保存平台、会话 ID、profile、workspace 和同步游标等恢复线索。
