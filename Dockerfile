# syntax=docker/dockerfile:1
# Het hele platform als één image: de API serveert ook de gebouwde web-app.
FROM node:22-alpine AS web
WORKDIR /src
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web ./
RUN npm run build

FROM python:3.12-slim
COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1 \
    PLATFORM_ENVIRONMENT=production PLATFORM_DATA_DIR=/data PLATFORM_WEB_DIST=/srv/web
WORKDIR /srv

COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --frozen --no-dev

COPY api/app ./app
COPY api/migrations ./migrations
COPY --from=web /src/dist ./web

RUN useradd --system --uid 10001 platform && install -d -o platform -g platform /data
USER platform
VOLUME /data
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
