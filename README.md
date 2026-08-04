# AgentHub

AgentHub 是**以群聊写代码为核心的**多 Agent 软件交付工作台。开发者可以在群聊中 `@子Agent` 分派开发任务，也可以直接发送需求，由 Orchestrator 自动拆解并调度子 Agent（架构设计、代码生成、代码审查、测试）各司其职，完成从需求到部署的全流程。

单聊模式仅支持 DeepSeek（OpenAI 兼容接口），提供轻量高效的对话式编程体验。

## 当前状态

全部 11 个 Phase 已完成，首版核心闭环验收通过。各阶段详情见 `DEVELOPMENT_PLAN.md`。

已完成功能：
- FastAPI 应用工厂、类型化配置、敏感信息保护
- PostgreSQL 业务模型、Alembic 迁移、pgvector + pg_trgm 扩展
- Pydantic v2 Schema、仓储层、聊天消息核心服务
- REST API（`/api/v1`）、WebSocket 实时推送
- Mock Adapter 和 DeepSeek OpenAI 兼容 Adapter
- 群聊显式 @Agent 并行分发与隐式 Orchestrator Pipeline 编排
- 4 Agent 串行 Pipeline（架构设计 → 代码生成 → 代码审查 → 测试）+ 架构审批 HITL
- RAG 知识库：代码/文档分块、pgvector 向量化、混合检索（关键词 + 语义）
- 会话记忆：渐进摘要、全量合并、向量检索、遗忘策略
- Code Diff 提取与审批、本地 HTML/CSS/JS 预览
- Vercel 部署（含轮询状态追踪）
- 安全基础设施：速率限制、请求追踪、Prometheus 指标、结构日志、路径穿越/XSS/CSRF 防御
- Docker Compose 全栈编排（PostgreSQL + 后端 + 前端 Nginx）

## 预置子 Agent

群聊模式下预置 4 个垂直代码子 Agent：

| Agent | 能力 | 职责 |
|---|---|---|
| 架构设计专家 | architecture_design | 需求 → 架构设计文档 |
| 代码生成专家 | code_generation | 设计文档 → 高质量代码 |
| 代码审查专家 | code_review | 代码质量、安全性和最佳实践审查 |
| 测试专家 | testing | 验收标准 → 测试用例与代码 |

群聊中使用 `@Agent名` 直接路由到指定 Agent，无 @ 时由 Orchestrator Pipeline 按架构→代码→审查→测试顺序自动串行调度。

## 技术栈

- **后端**：Python 3.13, FastAPI, Pydantic v2, SQLAlchemy 2 (async), asyncpg, Alembic, LangGraph
- **数据库**：PostgreSQL + pgvector + pg_trgm
- **前端**：React 19, Vite, TypeScript strict, React Router, TanStack Query, Lucide
- **基础设施**：Docker Compose
- **质量**：Ruff, mypy, pytest, ESLint, Vitest, Playwright

## 环境要求

- Python 3.13+
- uv
- Node.js 24 或兼容版本
- npm（Windows 上使用 `npm.cmd`）
- Docker Desktop（可选，用于运行开发基础设施）

## 首次配置

根目录 `.env.example` 只包含占位符。需要本地配置时创建未被 Git 跟踪的 `.env`，并替换 `YOUR_API_KEY_HERE` 等假值。

```dotenv
# DeepSeek（单聊和子Agent共用）
AGENTHUB_LLM_BASE_URL=https://api.deepseek.com/v1
AGENTHUB_LLM_API_KEY=YOUR_API_KEY_HERE
AGENTHUB_LLM_MODEL=deepseek-chat

# PostgreSQL
AGENTHUB_DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/agenthub

# Embedding（RAG 知识库）
AGENTHUB_EMBEDDING_BASE_URL=https://api.deepseek.com/v1
AGENTHUB_EMBEDDING_API_KEY=YOUR_API_KEY_HERE
AGENTHUB_EMBEDDING_MODEL=text-embedding-3-small
```

## 后端开发

```powershell
cd D:\codexWorkPlace\AgentHub\backend
uv sync --all-groups
uv run uvicorn agenthub.main:app --reload
```

API 默认地址为 `http://127.0.0.1:8000`。
非生产环境 OpenAPI 文档：`GET /docs`

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

启动前在根目录 `.env` 中配置已注册项目的 UUID：

```dotenv
VITE_PROJECT_ID=00000000-0000-0000-0000-000000000001
```

Vite 默认地址为 `http://127.0.0.1:5173`，开发代理会将 `/health`、`/api` 和 `/ws` 转发到后端 `8000` 端口。

前端质量检查：

```powershell
cd D:\codexWorkPlace\AgentHub\frontend
npm.cmd run lint
npm.cmd run typecheck
npm.cmd run test -- --run
npm.cmd run build
npm.cmd run e2e
```

## 基础设施

Compose 提供 PostgreSQL + pgvector + 后端 + 前端 Nginx 全栈服务。检查解析后的 Compose 配置：

```powershell
cd D:\codexWorkPlace\AgentHub
docker compose --env-file .env.example -f infra\compose.yaml config --quiet
```

## 目录

```text
backend/   FastAPI 应用、配置与后端测试
frontend/  React 工作台、前端测试与 E2E 配置
infra/     本地开发基础设施配置
docs/      验收报告
```

阶段范围、公共契约和后续顺序以 `AGENTS.md` 和 `DEVELOPMENT_PLAN.md` 为准。