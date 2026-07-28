# API

Phase 1 提供以下基础端点：

- `GET /health/live`：证明 API 进程能够响应。
- `GET /health/ready`：证明类型化配置已加载；当前不检查外部服务。

业务 API 将统一使用 `/api/v1` 前缀，并在对应阶段补充契约文档。

