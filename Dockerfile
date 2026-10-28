# ---------- 前端构建 ----------
FROM node:22-slim AS web
WORKDIR /web
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

# ---------- 后端 ----------
FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.4 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    PATH=/app/.venv/bin:$PATH \
    DA_HOST=0.0.0.0 DA_PORT=8000 DA_DATA_DIR=/data DA_WORKSPACES_DIR=/workspaces \
    DA_STATIC_DIR=/app/static DA_SANDBOX_URL=http://sandbox:8901
WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY backend/README.md ./
COPY backend/src ./src
RUN uv sync --frozen --no-dev
COPY --from=web /web/dist ./static
# 目录先建好并交给 app 用户，新建的数据卷会继承属主
RUN useradd --uid 1000 --create-home app \
 && mkdir -p /data /workspaces && chown app:app /data /workspaces
USER app
VOLUME ["/data", "/workspaces"]
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["data-analyze"]
