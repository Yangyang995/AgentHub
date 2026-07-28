# AgentHub

AgentHub 是本地优先的聊天式多 Agent 软件交付工作台。当前仓库处于 Phase 1，仅包含可运行的 FastAPI 基础服务、React 工作台外壳、质量工具和开发基础设施；聊天、数据库业务、RAG 与 Agent Adapter 尚未实现。

## 当前能力

- FastAPI 应用工厂与 `/health/live`、`/health/ready`。
- 基于 `pydantic-settings` 的类型化配置和敏感值保护。
- React 19、React Router、TanStack Query 和 Lucide 工作台基础页面。
- Ruff、mypy、pytest、ESLint、TypeScript、Vitest 和 Playwright 配置。
- PostgreSQL + pgvector 的开发 Compose 配置骨架。

`/health/ready` 在 Phase 1 只表示应用配置已成功加载，不会连接数据库、LLM 或 Agent。外部依赖将在后续阶段加入真实探测。

## 环境要求

- Python 3.13
- uv
- Node.js 24 或兼容版本
- npm
- Docker Desktop，可选，仅用于检查或运行开发基础设施

Windows PowerShell 的执行策略可能阻止 `npm.ps1`，本项目命令统一使用 `npm.cmd`。

## 首次配置

根目录 `.env.example` 只包含占位符。需要本地配置时创建未被 Git 跟踪的 `.env`，并替换 `YOUR_API_KEY_HERE` 等假值。不要在命令输出、问题记录或提交中展示真实配置。

## 后端开发

```powershell
cd D:\codexWorkPlace\AgentHub\backend
uv sync --all-groups
uv run uvicorn agenthub.main:app --reload
```

API 默认地址为 `http://127.0.0.1:8000`：

- `GET /health/live`
- `GET /health/ready`
- 非生产环境 OpenAPI：`GET /docs`

后端质量检查：

```powershell
cd D:\codexWorkPlace\AgentHub\backend
uv run python -c "from agenthub.main import app; print(app.title)"
uv run ruff check .
uv run ruff format --check .
uv run mypy src tests
uv run pytest
```

## 前端开发

```powershell
cd D:\codexWorkPlace\AgentHub\frontend
npm.cmd ci
npm.cmd run dev
```

Vite 默认地址为 `http://127.0.0.1:5173`，开发代理会把 `/health` 请求转发到本机 `8000` 端口的后端。

前端质量检查：

```powershell
cd D:\codexWorkPlace\AgentHub\frontend
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run test -- --run
npm.cmd run build
npm.cmd run e2e
```

Phase 1 的必需门禁不包含 Playwright 浏览器安装和 E2E 执行，但脚本与基础用例已经建立，后续界面阶段会将其纳入必需验收。

## 基础设施

当前 Compose 只提供后续阶段使用的 PostgreSQL + pgvector 开发服务。检查解析后的 Compose 配置不会启动容器：

```powershell
cd D:\codexWorkPlace\AgentHub
docker compose --env-file .env.example -f infra\compose.yaml config --quiet
```

Phase 1 不执行数据库迁移，也不把 Compose 静态检查视为数据库已可用。

## 目录

```text
backend/   FastAPI 应用、配置与后端测试
frontend/  React 工作台、前端测试与 E2E 配置
infra/     本地开发基础设施配置
docs/      架构、API 与验收记录
```

阶段范围、公共契约和后续顺序以 `AGENTS.md` 与 `DEVELOPMENT_PLAN.md` 为准。

