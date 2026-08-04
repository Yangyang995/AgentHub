# AgentHub 后端 Docker 镜像
# 多阶段构建：阶段 1 安装依赖，阶段 2 生成运行时镜像

# ── 阶段 1: 依赖安装 ──────────────────────────────────────────────────
FROM python:3.13-slim AS builder

WORKDIR /build

# 安装 uv——比 pip 更快更可靠的包管理器
COPY --from=ghcr.io/astral-sh/uv:0.11.8 /uv /usr/local/bin/uv

# 先复制依赖清单，利用 Docker 缓存加速重建
COPY backend/pyproject.toml backend/.python-version ./

# 创建虚拟环境并安装依赖（包括 dev 组以满足所有导入）
RUN uv sync --all-groups --frozen

# 复制源码和 prompts
COPY backend/src ./src
COPY backend/prompts ./prompts

# ── 阶段 2: 运行时镜像 ──────────────────────────────────────────────────
FROM python:3.13-slim AS runtime

WORKDIR /app

# 仅复制虚拟环境（不含 pip/uv 工具链以减小体积）
COPY --from=builder /build/.venv /app/.venv
COPY --from=builder /build/src /app/src
COPY --from=builder /build/prompts /app/prompts

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "agenthub.main:app", "--host", "0.0.0.0", "--port", "8000"]
