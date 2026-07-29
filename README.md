# AgentHub

AgentHub 是本地优先的聊天式多 Agent 软件交付工作台。当前仓库完成到 Phase 5 的 P0 单聊前后端；群聊、`@Agent` 路由和 Orchestrator 仍属于后续阶段。

## 当前能力

- FastAPI 应用工厂与 `/health/live`、`/health/ready`。
- 基于 `pydantic-settings` 的类型化配置和敏感值保护。
- PostgreSQL 业务模型、Alembic 迁移和 `pg_trgm` 模糊搜索基础。
- 单聊会话创建与查询、消息提交、OpenAI 兼容模型流式执行和完整 Agent 消息持久化。
- 可持久化、按执行序号补发的 WebSocket 事件，以及执行取消和安全错误映射。
- React 19、React Router、TanStack Query 和 Lucide 单聊工作台，支持流式回复、断线重连、取消和 Markdown 展示。
- Ruff、mypy、pytest、ESLint、TypeScript、Vitest 和 Playwright 配置。
- PostgreSQL + pgvector 的开发 Compose 配置。

默认后端单聊可通过 `openai_compatible` Agent 调用 DeepSeek 等 OpenAI 兼容模型；
确定性 Mock Adapter 继续用于自动化测试。真实 Codex CLI Adapter 仍需显式健康检查。

## 环境要求

- Python 3.13
- uv
- Node.js 24 或兼容版本
- npm
- Docker Desktop，可选，仅用于检查或运行开发基础设施

Windows PowerShell 的执行策略可能阻止 `npm.ps1`，本项目命令统一使用 `npm.cmd`。

## 首次配置

根目录 `.env.example` 只包含占位符。需要本地配置时创建未被 Git 跟踪的 `.env`，并替换 `YOUR_API_KEY_HERE` 等假值。不要在命令输出、问题记录或提交中展示真实配置。

使用 DeepSeek 时配置 OpenAI 兼容接口，密钥只保存在本地 `.env`：

```dotenv
AGENTHUB_LLM_BASE_URL=https://api.deepseek.com/v1
AGENTHUB_LLM_API_KEY=YOUR_API_KEY_HERE
AGENTHUB_LLM_MODEL=deepseek-chat
```

## 后端开发

```powershell
cd D:\codexWorkPlace\AgentHub\backend
uv sync --all-groups
uv run uvicorn agenthub.main:app --reload
```

API 默认地址为 `http://127.0.0.1:8000`：

- `GET /health/live`
- `GET /health/ready`
- `POST /api/v1/projects/{project_id}/conversations`
- `GET /api/v1/projects/{project_id}/conversations`
- `GET /api/v1/projects/{project_id}/conversations/{conversation_id}`
- `GET /api/v1/projects/{project_id}/conversations/{conversation_id}/messages`
- `POST /api/v1/projects/{project_id}/conversations/{conversation_id}/messages`
- `POST /api/v1/projects/{project_id}/executions/{execution_id}/cancel`
- `WS /ws/conversations/{conversation_id}?project_id={project_id}&execution_id={execution_id}&last_sequence={sequence}`
- 非生产环境 OpenAPI：`GET /docs`

WebSocket 补发游标中的 `last_sequence` 是排他游标，服务端返回该执行中序号更大的事件。`sequence` 只在单次执行内单调递增，因此补发时必须同时提供 `execution_id`。

运行数据库迁移：

```powershell
cd D:\codexWorkPlace\AgentHub\backend
uv run alembic upgrade head
```

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

启动前在根目录 `.env` 中配置已注册项目和 Agent 的公开 UUID：

```dotenv
VITE_PROJECT_ID=00000000-0000-0000-0000-000000000001
VITE_AGENT_ID=00000000-0000-0000-0000-000000000002
```

Phase 4 尚未提供项目和 Agent 列表接口，因此这两个值由部署配置提供，不是密钥。Vite 默认地址为 `http://127.0.0.1:5173`，开发代理会把 `/health`、`/api` 和 `/ws` 转发到本机 `8000` 端口的后端。

前端 API 类型由实际 FastAPI OpenAPI 和 Pydantic 事件 Schema 生成。`typecheck` 与 `build` 会自动重新生成，也可单独执行：

```powershell
npm.cmd run generate:api
```

前端质量检查：

```powershell
cd D:\codexWorkPlace\AgentHub\frontend
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run test -- --run
npm.cmd run build
npm.cmd run e2e
```

Playwright 使用与后端契约一致的浏览器级 Mock 覆盖桌面与移动端单聊流程；连接真实后端仍需要可用的 PostgreSQL、已注册项目和已启用 Agent。

## 基础设施

Compose 提供 PostgreSQL + pgvector 开发服务。检查解析后的 Compose 配置不会启动容器：

```powershell
cd D:\codexWorkPlace\AgentHub
docker compose --env-file .env.example -f infra\compose.yaml config --quiet
```

Compose 静态检查不代表数据库已可用；后端数据库测试和迁移需要实际运行的 PostgreSQL。

## 目录

```text
backend/   FastAPI 应用、配置与后端测试
frontend/  React 工作台、前端测试与 E2E 配置
infra/     本地开发基础设施配置
docs/      架构、API 与验收记录
```

阶段范围、公共契约和后续顺序以 `AGENTS.md` 与 `DEVELOPMENT_PLAN.md` 为准。
