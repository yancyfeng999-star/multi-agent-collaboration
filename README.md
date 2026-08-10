# 多智能体协同 | Multi-Agent Collaboration

## 名称 | Name

| 项目 | 内容 |
| --- | --- |
| 中文名称 | 多智能体协同 |
| English Name | Multi-Agent Collaboration |
| Skill ID | `multi-agent-collaboration` |
| Skill Version | `2.0.0` |
| Protocol Version | `3` |
| Governance Storage Schema | `1.0` |
| GitHub | [yancyfeng999-star/multi-agent-collaboration](https://github.com/yancyfeng999-star/multi-agent-collaboration) |
| License | [MIT](LICENSE) |
| 调用方式 | `$multi-agent-collaboration` |

Multi-Agent Collaboration 是面向通用项目的 Agent 角色目录、人工启动与按需多智能体治理 Skill。普通任务默认使用 Direct；只在需要多 Agent、交接、独立质量、跨会话恢复或正式审计时启用 Coordinated。

## 两条工作路径

| 路径 | 适用场景 | 写入治理资料 |
| --- | --- | --- |
| **Direct** | 单一 Agent 可完成的网站更新、修复、调研、文档或普通开发 | 否，默认不创建治理资料 |
| **Coordinated** | 多 Agent 并行、受管交接、Review/QA、跨会话恢复、Strict 门禁 | 是，仅保存在项目外 Governance Home |

网站构建、启动、测试、部署和线上运行对治理资料零依赖。本 Skill 不自动创建或修改目标项目的 `AGENTS.md`，也不把 Agent、Run、handoff、candidate index 或 checkpoint 复制到网站项目。

## 默认入口：Agent 目录与人工启动

打开 [agents.html](agents.html)，了解 Coordinator、Owner、Quality 和按需 Release，填写项目根目录、目标、范围与验收标准，生成 Direct 启动指令并人工启动。

页面不读取 Run、不显示当前任务、不自动编排、不创建线程或 Agent、不写入目标项目。需要高级治理时，再由用户确认切换到 Coordinated。

## 最小必要 Agent

角色是能力，不等于必须新建 Agent。

- Coordinator 同时负责目标、任务图、范围、集成、版本治理和收口。
- Owner 负责一个明确、可验证、唯一写入范围的目标。
- Quality 默认合并 Reviewer + QA，但必须独立于该任务 Owner。
- Release、Security、Data、UI 和 Operations 是按需能力，不因功能增加就自动新建 Agent。

只有权限隔离、自审冲突、owned paths 冲突、独立环境、项目强制独立审查或明确并行收益才增加 Agent。

## Governance Home

Coordinated 默认使用：

```text
~/.codex/governance/multi-agent-collaboration/projects/<project-key>/
├── project-binding.yaml
├── TEAM.yaml
├── agents/
├── runs/
├── bridges/
├── project-checkpoints/
└── migrations/
```

Governance Home 必须位于项目外。`project-binding.yaml` 绑定真实项目根和唯一 allowed root。Run、Agent 身份、会话、handoff、checkpoint、final audit 和 candidate view 全部是开发治理资料，不是项目运行必要条件。

## Coordinated 快速开始

Coordinated 是按需启用的高级治理，不是普通项目更新的默认前置。

只需一次 Run 时，可直接创建 Run，无需先建长期 Agent 层：

```bash
python3 scripts/init_run.py \
  --coordination-mode coordinated \
  --project-root "<project-root>" \
  --governance-root "<governance-home>" \
  --project-id "<project-id>" \
  --project-name "<project-name>" \
  --governance standard \
  --transport hybrid \
  --objective "<objective>" \
  --versioning-mode tracked \
  --versioning-reason "<reason>" \
  --user-confirmed
```

需要长期身份与恢复时：

```bash
python3 scripts/init_project_agents.py \
  --project-root "<project-root>" \
  --governance-root "<governance-home>" \
  --project-id "<project-id>" \
  --project-name "<project-name>" \
  --agents "A01-coordinator,A02-owner,A03-quality" \
  --governance standard \
  --user-confirmed
```

## 快车道与受控自助

Light/Standard 可使用 `execution_profile=fast`，把重复门禁汇总为一次 preflight 和一次 completion preflight；Strict 禁止 fast。

`hybrid` 或 `self_service` 允许工作 Agent 在已冻结父任务范围内发布子任务，不必每次唤醒主架构 Agent。任务发布、任务 claim、thread claim 和高冲突资源都用独立锁串行，不能覆盖未过期持有者，不能扩大路径、权限、版本或发布资格。

## 版本边界

| 对象 | 当前值/权威源 | 变化条件 |
| --- | --- | --- |
| Skill 版本 | `2.0.0` / [VERSION](VERSION) | Skill 用户可见行为、文档、脚本、Schema 或默认值变化 |
| Protocol 版本 | `3` / `scripts/protocol_lib.py` | 任务、事件、状态机、证据或恢复语义不兼容变化 |
| Storage Schema | `1.0` / binding schema | Governance Home 布局与绑定契约变化 |
| 项目业务版本 | 目标项目唯一版本权威源 | 项目交付范围、兼容性或发布内容变化 |

Skill 升级不会自动修改项目业务版本。版本治理集中由 Coordinator 承担，不单独增加 Version Agent。

## 旧项目迁移

```bash
python3 scripts/migrate_governance_storage.py \
  --project-root "<project-root>" \
  --project-id "<project-id>" \
  --project-name "<project-name>" \
  --governance-root "<governance-home>" \
  --dry-run
```

核对目标、文件清单和 SHA-256 后再用 `--apply`。工具只复制并验证，不删除旧 `.multi-agent-collaboration/`。

## 快速导航 | Quick Navigation

| 入口 | 用途 |
| --- | --- |
| [agents.html](agents.html) | 角色说明与 Direct 人工启动 |
| [SKILL.md](SKILL.md) | Codex 必须遵循的核心路由与边界 |
| [references/README.md](references/README.md) | 按场景读取详细协议 |
| [scripts/README.md](scripts/README.md) | CLI 和安全操作顺序 |
| [assets/README.md](assets/README.md) | 模板与 Schema |
| [docs/MERGED_STRUCTURE.md](docs/MERGED_STRUCTURE.md) | Direct/Coordinated 与外置治理架构 |
| [CHANGELOG.md](CHANGELOG.md) | 用户可见变更 |

## English Summary

Multi-Agent Collaboration defaults to **Direct**: one Agent completes a clear task and creates no governance records. Use **Coordinated** only when multiple Agents, governed handoffs, independent quality gates, recovery, or auditability are truly required.

All coordinated artifacts live in an external **Governance Home**. The target project contains only product source, tests, build configuration, its authoritative version source, and required deliverables. The Skill never creates or edits the target project's `AGENTS.md`. Website build, test, deployment, and runtime remain fully independent of Agent roles, Runs, handoffs, checkpoints, and candidate views.
