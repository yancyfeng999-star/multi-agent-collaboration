# Contributing | 贡献指南

Thank you for improving Multi-Agent Collaboration.

感谢你参与改进“多智能体协同”。

## Principles | 原则

- Preserve the durable document protocol and fail-closed governance gates.
- Prefer the minimum necessary number of agents; merge non-conflicting roles.
- Keep Skill version, protocol version, task attempts, and release candidates separate.
- Do not weaken path, permission, hash, evidence, or human-approval checks.
- 保持文档协议、权限边界、证据和人工门禁完整。
- 使用最少必要智能体，不因功能或角色名称增加 Agent。

## Before a Pull Request | 提交前

Run the complete test suite:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py'
```

Compile the protocol scripts and tests:

```bash
python3 -m compileall -q scripts tests
```

Before a release, also run all top-level CLI `--help` commands, parse every JSON Schema with the
Python standard library, validate Markdown local links, and run `git diff --check`. See
[tests/README.md](tests/README.md) for the current release gate.

When behavior changes, update the implementation, templates, tests, `SKILL.md`, relevant
references, root `README.md`, and `CHANGELOG.md` together.

行为发生变化时，必须同步实现、模板、测试、主协议、详细规范、README 和变更记录。

## Pull Requests

Describe:

- the problem and intended behavior;
- files and protocol fields changed;
- compatibility or migration impact;
- checks performed and their results;
- any remaining risk.

Never include credentials, local coordination runs, generated caches, or unrelated user files.
