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

# ffmpeg/ffprobe voor previews van video's (zonder ffmpeg krijgen video's gewoon geen preview)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --frozen --no-dev

COPY api/app ./app
COPY api/migrations ./migrations
COPY --from=web /src/dist ./web

RUN useradd --system --uid 10001 platform && install -d -o platform -g platform /data
USER platform
VOLUME /data
# 8080 = http (ook voor de Let's Encrypt-controle), 8443 = https zodra er een certificaat is
EXPOSE 8080 8443
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"
CMD ["python", "-m", "app.serve"]
