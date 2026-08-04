# Infrastructure

AgentHub 全栈 Docker Compose 编排，包含 PostgreSQL + pgvector、FastAPI 后端和 React 前端（Nginx）。

## 快速启动

```powershell
# 从项目根目录启动
docker compose -f infra/compose.yaml up -d

# 查看日志
docker compose -f infra/compose.yaml logs -f

# 停止
docker compose -f infra/compose.yaml down
```

## 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| 前端（Nginx） | 3000 | SPA + API 反向代理 |
| 后端（FastAPI） | 8000 | REST + WebSocket |
| PostgreSQL | 5432 | 数据库（本地访问） |

## 环境变量

复制 `.env.example` 为 `.env` 并填写实际值。Compose 文件使用 `infra/compose.yaml` 中的默认值，可通过 `.env` 覆盖。

必需配置：
- `AGENTHUB_LLM_API_KEY`：DeepSeek API Key
- `AGENTHUB_POSTGRES_PASSWORD`：PostgreSQL 密码

## 容器构建

```powershell
# 构建全部镜像
docker compose -f infra/compose.yaml build

# 仅构建后端
docker compose -f infra/compose.yaml build backend

# 仅构建前端
docker compose -f infra/compose.yaml build frontend
```

## 健康检查

```powershell
# 检查所有服务健康状态
docker compose -f infra/compose.yaml ps

# 后端存活检查
curl http://localhost:8000/health/live

# 前端就绪检查
curl http://localhost:3000
```

## 安全加固

- 所有端口绑定到 127.0.0.1（仅本地访问）
- Nginx 配置包含 Content-Security-Policy、X-Content-Type-Options、X-Frame-Options 等安全头
- 后端日志自动脱敏 API Key、密码等敏感字段
- 速率限制默认启用（100 req/s 全局，20 req/s 单 IP）
- Prometheus 指标通过 `/metrics` 端点暴露（生产环境默认关闭）

## 注意事项

- 首次启动需要先创建数据库并运行 Alembic 迁移（见 `backend/` 的 README）
- Docker Compose 适用于本地开发和演示，生产部署需要额外配置 HTTPS、认证和安全组
- 后端 Dockerfile 使用 uv 从缓存层安装依赖，修改 `pyproject.toml` 后会触发重新构建
- 前端 Dockerfile 会自动运行 `generate:api` 生成 TypeScript API schema
