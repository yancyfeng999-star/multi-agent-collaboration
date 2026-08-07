#!/usr/bin/env python3
"""
初始化项目 Agent 结构

用法:
    python3 scripts/init_project_agents.py \\
        --project-root "<project-root>" \\
        --project-id "<project-id>" \\
        --project-name "<project-name>" \\
        --agents "A01-coordinator,A02-frontend,A03-backend" \\
        --governance standard \\
        --user-confirmed
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# 添加当前目录到 Python 路径
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))

from protocol_lib import ProtocolError, atomic_write


AGENT_ID_RE = re.compile(r"^A\d{2}-[a-z0-9][a-z0-9-]*$")
SKILL_ROOT = SCRIPT_DIR.parent


def canonical_path(p: Path) -> Path:
    """规范化路径"""
    return p.resolve()


def ensure_dir(p: Path):
    """确保目录存在"""
    p.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, content: str):
    """写入文本文件"""
    atomic_write(path, content)


def write_json(path: Path, data: dict):
    """写入 JSON 文件"""
    content = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    atomic_write(path, content)


def parse_args():
    parser = argparse.ArgumentParser(description="初始化项目 Agent 结构")
    parser.add_argument("--project-root", required=True, help="项目根目录")
    parser.add_argument("--project-id", required=True, help="项目 ID")
    parser.add_argument("--project-name", required=True, help="项目名称")
    parser.add_argument(
        "--agents",
        required=True,
        help="Agent 列表，逗号分隔，格式: A01-coordinator,A02-frontend",
    )
    parser.add_argument(
        "--governance",
        default="standard",
        choices=["light", "standard", "strict"],
        help="治理模式",
    )
    parser.add_argument("--max-parallel", type=int, default=4, help="最大并行数")
    parser.add_argument("--user-confirmed", action="store_true", help="用户已确认")
    return parser.parse_args()


def create_agent_directory(base_dir: Path, agent_id: str, role_name: str):
    """为单个 Agent 创建目录结构"""
    agent_dir = base_dir / "agents" / agent_id
    ensure_dir(agent_dir)

    # 创建子目录
    subdirs = [
        "conversations",
        "conversations/archive",
        "conversations/checkpoints",
        "tasks",
        "handoffs",
        "artifacts",
        "runtime/logs",
    ]
    for subdir in subdirs:
        ensure_dir(agent_dir / subdir)

    # 创建空文件
    empty_files = [
        "conversations/README.md",
        "conversations/INDEX.md",
    ]
    for f in empty_files:
        path = agent_dir / f
        if not path.exists():
            atomic_write(path, "")

    return agent_dir


def create_agent_profile(agent_dir: Path, agent_id: str, role_name: str):
    """Create identity/default-policy metadata without claiming actual runtime facts."""
    now = datetime.now(timezone.utc).isoformat()
    role_path = agent_dir / "ROLE.md"
    role_sha256 = hashlib.sha256(role_path.read_bytes()).hexdigest()
    profile = {
        "schema_version": "1.0",
        "doc_type": "agent_profile",
        "profile_version": 1,
        "agent_id": agent_id,
        "role": {
            "role_id": role_name,
            "path": f"agents/{agent_id}/ROLE.md",
            "sha256": role_sha256,
        },
        "declared_model_policy": {
            "policy_kind": "declared_default",
            "preferred_models": [],
            "preferred_provider": None,
            "runtime_kind": None,
            "source": "team_registry",
        },
        "lifecycle": {
            "status": "active", "created_at": now, "updated_at": now,
            "paused_at": None, "retired_at": None, "retirement_reason": None,
        },
        "metadata": {"display_name": role_name.replace("-", " ").title(), "labels": [role_name]},
    }
    write_json(agent_dir / "AGENT_PROFILE.json", profile)


def copy_support_files(base_dir: Path) -> None:
    """Copy schemas/templates into the project so the archive is portable."""
    for name in ("schemas", "templates"):
        source = SKILL_ROOT / "assets" / name
        target = base_dir / name
        if target.exists():
            raise ProtocolError(f"refusing to overwrite existing support directory: {target}")
        shutil.copytree(source, target)


def create_role_md(agent_dir: Path, agent_id: str, role_name: str):
    """创建 ROLE.md"""
    now = datetime.now(timezone.utc).isoformat()
    content = f"""---
schema_version: "1.0"
doc_type: role
agent_id: "{agent_id}"
created_at: "{now}"
updated_at: "{now}"
---

# Agent 岗位章程

## 基本信息

| 字段 | 值 |
|------|-----|
| Agent ID | {agent_id} |
| 岗位名称 | {role_name} |
| 专业领域 | 待定义 |

## 长期使命

待定义

## 默认拥有域

待定义

## 禁止写入域

待定义

## 主要协作对象

待定义

## 升级路径

待定义

## 完成标准

待定义
"""
    write_text(agent_dir / "ROLE.md", content)


def create_system_prompt_md(
    agent_dir: Path, agent_id: str, role_name: str, project_root: str
):
    """创建 SYSTEM_PROMPT.md"""
    now = datetime.now(timezone.utc).isoformat()
    content = f"""---
schema_version: "1.0"
doc_type: system_prompt
agent_id: "{agent_id}"
created_at: "{now}"
updated_at: "{now}"
---

# Agent 恢复提示词

## 项目根目录规则

项目根目录为：`{project_root}`

所有文件操作必须基于此目录。不要读取或修改此目录之外的文件（除非明确授权）。

## 岗位身份

你是 **{agent_id}**，岗位为 **{role_name}**。

## 权限边界

### 可写入

待定义

### 禁止写入

待定义

## 工作方式

1. 读取任务文件，理解目标和验收标准
2. 检查实际文件和 Git 状态
3. 执行任务，保留真实命令和结果
4. 生成标准化交接文档
5. 不越界修改其他 Agent 的文件

## 证据要求

- 所有修改必须有真实命令和结果
- 测试必须有真实输出
- 文件变更必须有 diff 或 commit
- 不接受"已完成"作为唯一证据

## 交接格式

使用标准 HANDOFF.md 模板，必须包含：
- 状态（completed/blocked/failed，与 Protocol v3 result status 一致）
- 修改文件清单
- 命令和真实结果
- 风险和未完成项

## 上下文恢复顺序

1. 读取 `conversations/CURRENT_CONTEXT.md`
2. 读取最新 checkpoint
3. 读取当前任务
4. 读取最近交接
5. 检查实际文件状态
6. 汇报恢复结果

## 禁止规则

- 不依赖未落盘的聊天历史
- 不读取其他 Agent 的 inbox/outbox
- 不修改 state.yaml 或事件序号
- 不直接调度下游任务
- 不伪造测试结果或证据
"""
    write_text(agent_dir / "SYSTEM_PROMPT.md", content)


def create_checklist_md(agent_dir: Path, agent_id: str):
    """Create the stable execution and handoff checklist."""
    now = datetime.now(timezone.utc).isoformat()
    content = f'''---
schema_version: "1.0"
doc_type: "checklist"
agent_id: "{agent_id}"
created_at: "{now}"
---

# Agent 检查清单

## 开始前

- [ ] 任务自包含，依赖已满足，写入域和资源已隔离。
- [ ] 已检查实际文件、Git、运行环境和验收命令。

## 执行中

- [ ] 只修改授权范围，记录命令、真实结果、错误和重试。
- [ ] 跨域或共享合同问题交回 Coordinator，不越界修改。

## 交接前

- [ ] 交接包含状态、changed files、命令、精确结果、证据、风险与未解决项。
- [ ] 需要时已同步对话、创建 checkpoint、更新当前上下文和索引。
'''
    write_text(agent_dir / "CHECKLIST.md", content)


def create_session_map(agent_dir: Path, agent_id: str):
    """创建 SESSION_MAP.json"""
    now = datetime.now(timezone.utc).isoformat()
    session_map = {
        "schema_version": "1.0",
        "agent_id": agent_id,
        "active": None,
        "history": [],
    }
    write_json(agent_dir / "conversations" / "SESSION_MAP.json", session_map)


def create_current_context(agent_dir: Path, agent_id: str):
    """创建 CURRENT_CONTEXT.md"""
    now = datetime.now(timezone.utc).isoformat()
    content = f"""---
schema_version: "1.0"
doc_type: current_agent_context
agent_id: "{agent_id}"
updated_at: "{now}"
latest_checkpoint: null
active_task: null
---

# 当前 Agent 上下文

## 长期使命

待定义

## 当前任务

无

## 当前状态

空闲

## 已确认需求

无

## 已确认决策

无

## 已完成

无

## 待完成

无

## 当前写入范围

待定义

## 关键文件

无

## 验证状态

无

## 已知风险与阻塞

无

## 下一步

等待任务分配

## 必读资料

无

## 按需读取的完整原文

无
"""
    write_text(agent_dir / "conversations" / "CURRENT_CONTEXT.md", content)


def create_team_yaml(
    base_dir: Path,
    project_id: str,
    project_name: str,
    project_root: str,
    agents: list,
    governance: str,
    max_parallel: int,
):
    """创建 TEAM.yaml"""
    now = datetime.now(timezone.utc).isoformat()

    agent_records = []
    for agent_id in agents:
        role_name = agent_id.split("-", 1)[1] if "-" in agent_id else "member"
        agent_records.append(
            {
                "agent_id": agent_id,
                "role_name": role_name,
                "domain": None,
                "status": "active",
                "role_file": f"agents/{agent_id}/ROLE.md",
                "system_prompt_file": f"agents/{agent_id}/SYSTEM_PROMPT.md",
                "agent_profile_file": f"agents/{agent_id}/AGENT_PROFILE.json",
            }
        )

    registry = {
        "schema_version": "1.0",
        "doc_type": "team",
        "project_id": project_id,
        "project_name": project_name,
        "project_root": project_root,
        "created_at": now,
        "updated_at": now,
        "governance_mode": governance,
        "max_parallel": max_parallel,
        "declared_model_policy": {
            "policy_kind": "declared_default",
            "preferred_models": [],
            "preferred_provider": None,
            "runtime_kind": None,
            "source": "team_registry",
        },
        "agents": agent_records,
        "collaboration_rules": [
            "Coordinator owns task assignment and integration",
            "Every task has exactly one Owner",
            "Parallel tasks require isolated write scopes and runtime resources",
            "Handoffs contain exact commands, results, evidence, risks, and unresolved work",
            "Project completion requires Coordinator verification",
        ],
    }
    content = json.dumps(registry, ensure_ascii=False, indent=2) + "\n"
    write_text(base_dir / "TEAM.yaml", content)


def create_protocol_md(base_dir: Path):
    """创建 PROTOCOL.md"""
    now = datetime.now(timezone.utc).isoformat()
    content = f"""---
schema_version: "1.0"
doc_type: protocol
created_at: "{now}"
updated_at: "{now}"
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
"""
    write_text(base_dir / "PROTOCOL.md", content)


def create_decisions_md(base_dir: Path, project_id: str):
    """创建 DECISIONS.md"""
    now = datetime.now(timezone.utc).isoformat()
    content = f"""---
schema_version: "1.0"
doc_type: decisions
project_id: "{project_id}"
created_at: "{now}"
updated_at: "{now}"
---

# 决策记录

## 决策列表

| 决策 ID | 时间 | 决策内容 | 原因 | 影响范围 | 状态 |
|---------|------|----------|------|----------|------|
| DEC-0001 | {now} | 项目初始化 | 新项目启动 | 全局 | 已确认 |

## 决策详情

### DEC-0001: 项目初始化

**时间**: {now}
**决策**: 初始化多智能体协同项目
**原因**: 新项目启动
**影响范围**: 全局
**状态**: 已确认
**相关文件**:
- .multi-agent-collaboration/project.yaml
- .multi-agent-collaboration/TEAM.yaml
"""
    write_text(base_dir / "DECISIONS.md", content)


def create_index_md(base_dir: Path, agents: list):
    """创建 INDEX.md"""
    now = datetime.now(timezone.utc).isoformat()

    agent_index_rows = []
    for agent_id in agents:
        agent_index_rows.append(f"| {agent_id} | agents/{agent_id}/ | 活跃 |")

    content = f"""---
schema_version: "1.0"
doc_type: index
created_at: "{now}"
updated_at: "{now}"
---

# 项目索引

## Agent 索引

| Agent ID | 目录 | 状态 |
|----------|------|------|
{chr(10).join(agent_index_rows)}

## 快速导航

- 团队清单: [TEAM.yaml](TEAM.yaml)
- 协同协议: [PROTOCOL.md](PROTOCOL.md)
- 项目上下文: [CURRENT_PROJECT_CONTEXT.md](CURRENT_PROJECT_CONTEXT.md)
- 决策记录: [DECISIONS.md](DECISIONS.md)

## 检索策略

1. 项目级索引: INDEX.md
2. Agent 级索引: agents/<id>/conversations/INDEX.md
3. 任务索引: runs/<run-id>/tasks/
4. 决策索引: DECISIONS.md
"""
    write_text(base_dir / "INDEX.md", content)


def create_current_project_context(base_dir: Path, project_id: str, agents: list):
    """创建 CURRENT_PROJECT_CONTEXT.md"""
    now = datetime.now(timezone.utc).isoformat()

    agent_rows = []
    for agent_id in agents:
        role_name = agent_id.split("-", 1)[1] if "-" in agent_id else "member"
        agent_rows.append(
            f"| {agent_id} | - | 空闲 | - | - |"
        )

    content = f"""---
schema_version: "1.0"
doc_type: current_project_context
project_id: "{project_id}"
updated_at: "{now}"
latest_project_checkpoint: null
---

# 当前项目上下文

## 最终目标

待定义

## 当前阶段

初始化

## Agent 状态

| Agent | 当前任务 | 状态 | 最新检查点 | 最近交接 |
|-------|----------|------|------------|----------|
{chr(10).join(agent_rows)}

## 已确认决策

- 项目初始化

## 已完成里程碑

- 项目结构初始化

## 正在进行

无

## 阻塞项

无

## 风险

无

## 下一并行波次

待规划

## 项目级验收状态

未开始

## 恢复时必须读取

- TEAM.yaml
- PROTOCOL.md
- DECISIONS.md
- INDEX.md
"""
    write_text(base_dir / "CURRENT_PROJECT_CONTEXT.md", content)


def main():
    args = parse_args()

    if not args.user_confirmed:
        print("ERROR: 必须传 --user-confirmed 确认初始化")
        sys.exit(1)

    project_root = canonical_path(Path(args.project_root))
    if not project_root.exists():
        print(f"ERROR: 项目根目录不存在: {project_root}")
        sys.exit(1)

    # 解析 Agent 列表
    agents = [a.strip() for a in args.agents.split(",") if a.strip()]
    if not agents:
        print("ERROR: 至少需要一个 Agent")
        sys.exit(1)
    if len(set(agents)) != len(agents):
        print("ERROR: Agent ID 不得重复")
        sys.exit(1)
    invalid_agents = [agent for agent in agents if not AGENT_ID_RE.fullmatch(agent)]
    if invalid_agents:
        print(f"ERROR: Agent ID 格式无效: {', '.join(invalid_agents)}")
        sys.exit(1)
    if args.max_parallel < 1:
        print("ERROR: --max-parallel 必须大于 0")
        sys.exit(1)

    # 确保第一个 Agent 是 coordinator
    if not agents[0].endswith("-coordinator"):
        print("WARNING: 第一个 Agent 建议是 coordinator")

    # TEAM.yaml is the completion marker. Build a fresh tree beside the target
    # and publish it with one directory rename so no partial tree is observable.
    base_dir = project_root / ".multi-agent-collaboration"
    if (base_dir / "TEAM.yaml").exists():
        missing_agents = [agent for agent in agents if not (base_dir / "agents" / agent / "conversations/SESSION_MAP.json").is_file()]
        if missing_agents:
            print(f"ERROR: 检测到不完整初始化，缺少 Agent: {', '.join(missing_agents)}", file=sys.stderr)
            print("请先运行 manage_project_agents.py repair；不要把该目录视为初始化成功。", file=sys.stderr)
            sys.exit(1)
        print(f"WARNING: TEAM.yaml 已存在，跳过初始化")
        sys.exit(0)

    if base_dir.exists():
        print("ERROR: 检测到不完整初始化；TEAM.yaml 不存在，拒绝覆盖。", file=sys.stderr)
        print("请先移走该目录或运行修复工具；不要把该目录视为初始化成功。", file=sys.stderr)
        sys.exit(1)

    staging_root = Path(tempfile.mkdtemp(prefix=".multi-agent-init-", dir=project_root))
    base_dir = staging_root / ".multi-agent-collaboration"
    ensure_dir(base_dir)
    agents_entry = project_root / "AGENTS.md"
    created_agents_entry = False

    print(f"初始化项目 Agent 结构...")
    print(f"  项目根目录: {project_root}")
    print(f"  项目 ID: {args.project_id}")
    print(f"  Agent 数量: {len(agents)}")
    print(f"  治理模式: {args.governance}")
    print()

    try:
        # TEAM.yaml 是完成标记，必须最后写入暂存树。
        create_protocol_md(base_dir)
        print("✓ 创建 PROTOCOL.md")

        create_decisions_md(base_dir, args.project_id)
        print("✓ 创建 DECISIONS.md")

        create_index_md(base_dir, agents)
        print("✓ 创建 INDEX.md")

        create_current_project_context(base_dir, args.project_id, agents)
        print("✓ 创建 CURRENT_PROJECT_CONTEXT.md")

        copy_support_files(base_dir)
        print("✓ 复制 schemas/ 与 templates/")

        for agent_id in agents:
            role_name = agent_id.split("-", 1)[1] if "-" in agent_id else "member"
            agent_dir = create_agent_directory(base_dir, agent_id, role_name)
            print(f"✓ 创建 Agent 目录: {agent_id}")

            create_role_md(agent_dir, agent_id, role_name)
            print("  ✓ 创建 ROLE.md")
            create_system_prompt_md(agent_dir, agent_id, role_name, str(project_root))
            print("  ✓ 创建 SYSTEM_PROMPT.md")
            create_checklist_md(agent_dir, agent_id)
            print("  ✓ 创建 CHECKLIST.md")
            create_session_map(agent_dir, agent_id)
            print("  ✓ 创建 SESSION_MAP.json")
            create_current_context(agent_dir, agent_id)
            print("  ✓ 创建 CURRENT_CONTEXT.md")
            create_agent_profile(agent_dir, agent_id, role_name)
            print("  ✓ 创建 AGENT_PROFILE.json 与 runtime/ 结构")

            if os.environ.get("AGENT_INIT_FAIL_AFTER") == agent_id:
                raise RuntimeError("injected initialization failure")

        create_team_yaml(
            base_dir, args.project_id, args.project_name, str(project_root),
            agents, args.governance, args.max_parallel,
        )
        if not agents_entry.exists():
            write_text(agents_entry, "# Project Agents\n\n多智能体协作入口：`.multi-agent-collaboration/PROTOCOL.md`。项目目录是长期真源。\n")
            created_agents_entry = True
        os.replace(base_dir, project_root / ".multi-agent-collaboration")
    except Exception as exc:
        if created_agents_entry:
            agents_entry.unlink(missing_ok=True)
        print(f"ERROR: 初始化事务已回滚: {exc}", file=sys.stderr)
        sys.exit(1)
    finally:
        shutil.rmtree(staging_root, ignore_errors=True)
    print("✓ 创建 TEAM.yaml（初始化完成标记）")

    print()
    print("初始化完成！")
    print(f"项目 Agent 结构已创建在: {project_root / '.multi-agent-collaboration'}")
    print()
    print("下一步:")
    print("  1. 编辑 ROLE.md 定义每个 Agent 的职责")
    print("  2. 编辑 SYSTEM_PROMPT.md 定义权限边界")
    print("  3. 使用 bind_session.py 绑定平台会话")
    print("  4. 使用 init_run.py 创建第一个 Run")


if __name__ == "__main__":
    main()
