# AgentHub 前端 Docker 镜像
# 多阶段构建：阶段 1 用 Node.js 构建产物，阶段 2 用 Nginx 提供静态服务

# ── 阶段 1: 构建 ──────────────────────────────────────────────────────
FROM node:24-alpine AS builder

WORKDIR /app

# 先复制依赖清单以利用 Docker 缓存
COPY frontend/package.json frontend/package-lock.json ./

RUN npm ci

# 复制源码并构建
COPY frontend/ ./

# 生成 API schema 并构建
RUN npm run generate:api && npm run build

# ── 阶段 2: Nginx 运行时 ──────────────────────────────────────────────
FROM nginx:alpine AS runtime

# 复制 Nginx 配置
COPY infra/frontend.nginx.conf /etc/nginx/conf.d/default.conf

# 复制构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
