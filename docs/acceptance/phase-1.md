# Phase 1 验收记录

验收日期：2026-07-28

## 质量门禁

| 范围 | 命令 | 结果 |
|---|---|---|
| 后端依赖 | `uv sync --all-groups` | 通过，依赖与 `uv.lock` 同步 |
| 后端导入 | `uv run python -c "from agenthub.main import app; print(app.title)"` | 通过，输出 `AgentHub API` |
| Python Lint | `uv run ruff check .` | 通过 |
| Python 格式 | `uv run ruff format --check .` | 通过 |
| Python 类型 | `uv run mypy src tests` | 通过 |
| 后端测试 | `uv run pytest` | 通过，5 项测试 |
| 前端依赖 | `npm.cmd ci --prefer-offline --no-audit --no-fund` | 通过，安装 272 个包 |
| 前端 Lint | `npm.cmd run lint` | 通过 |
| TypeScript 类型 | `npm.cmd run typecheck` | 通过 |
| 前端单测 | `npm.cmd run test -- --run` | 通过，3 项测试 |
| 前端构建 | `npm.cmd run build` | 通过 |
| 前端 E2E | `npm.cmd run e2e` | 通过，桌面与移动端共 2 项测试 |
| Compose 解析 | `docker compose --env-file .env.example -f infra\compose.yaml config --quiet` | 通过，仅静态解析 |

## 行为验收

- `/health/live` 与 `/health/ready` 均可访问并返回稳定的类型化响应。
- 配置默认值、缺失运行时配置的安全错误和敏感值不泄漏均有自动测试覆盖。
- 桌面与移动端工作台截图已人工检查，无文字重叠、横向溢出或不可达控件。
- `test_main.http` 已保留并更新为两个正式健康检查地址。
- 正式后端入口验证完成后，根目录旧 `main.py` 已移除。
- `.idea/` 与根目录 `.venv/` 保持不变。

## 环境限制

- Docker CLI 与守护进程可用，版本为 29.6.2；本阶段没有启动 PostgreSQL 容器，也没有声称数据库已运行。
- `psql` 不在 PATH，PostgreSQL 命令行集成未验证。
- 可发现 `codex.exe`，但执行 `codex.exe --version` 返回 `Access is denied`；这不阻塞 Phase 1，后续 Codex CLI Adapter 阶段仍需单独处理。
- 当前目录不是 Git 仓库，因此本次没有 Git 状态或提交记录。
