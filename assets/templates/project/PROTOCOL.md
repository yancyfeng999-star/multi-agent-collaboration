---
schema_version: "1.0"
doc_type: protocol
created_at: "{created_at}"
updated_at: "{updated_at}"
---

# 多智能体协同协议

## 核心原则

1. **项目目录是长期真源** - 平台会话是运行时，项目文件是可移植记忆
2. **完整可追溯** - 每个结论都能追溯到任务、对话、文件或测试
3. **可压缩但不丢失** - 压缩不能覆盖或删除原文
4. **真实完成导向** - Agent 自报完成不能作为项目完成依据

## 协议版本

当前协议版本：**v3**

## 通信方式

- 文档协议是持久化通信底座
- inbox/outbox 是 Agent 间通信机制
- 事件系统是状态追踪机制

## 任务生命周期

```
TASK_READY → TASK_DISPATCHED → ACK → LEASE_ACQUIRED →
HANDOFF_READY → REVIEW_STARTED → REVIEW_APPROVED →
QA_PASSED → TASK_COMPLETED
```

## Agent 职责

| 角色 | 职责 |
|------|------|
| 总控 | 任务分配、依赖管理、收口验证 |
| Owner | 任务执行、交接生成 |
| Reviewer | 代码/文档审查 |
| QA | 验收测试 |

## 并行条件

两个任务可以并行当且仅当：
1. 无顺序依赖
2. 写入路径不重叠
3. 不共享不可隔离的运行资源
4. 输入上下文各自完整
5. 验收方式明确

## 交接格式

使用标准 HANDOFF.md 模板，必须包含：
- 状态（completed/blocked/failed，与 Protocol v3 result status 一致）
- 修改文件清单
- 命令和真实结果
- 风险和未完成项

## 验证规则

总控必须：
1. 收集所有交接
2. 检查写入冲突
3. 检查交付文件
4. 复核命令和测试证据
5. 运行项目级验证
6. 对照原始目标判断是否真正完成
