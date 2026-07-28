# Infrastructure

Phase 1 只提供 PostgreSQL + pgvector 的本地开发 Compose 配置。后端、前端镜像和完整启动编排将在发布阶段实现。

运行 `docker compose --env-file .env.example -f infra/compose.yaml config --quiet` 可做静态解析检查，不会启动容器。

