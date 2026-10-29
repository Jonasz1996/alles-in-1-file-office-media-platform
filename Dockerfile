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

# ffmpeg/ffprobe voor de mediaworker (fase 5): de previews van video's en media. Alleen de worker
# start ze; zonder ffmpeg krijgen video's gewoon geen preview
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --frozen --no-dev

COPY api/app ./app
COPY api/migrations ./migrations
COPY --from=web /src/dist ./web

RUN useradd --system --uid 10001 platform && install -d -o platform -g platform /data

# Media (fase 5, Keuze 11 en 47): de mediaworker draait als media (10002) en kan een blob van
# platform (10001, 0600) dus niet via /proc opnieuw openen. /run/media is van media met groep
# platform (2750, setgid): de socket krijgt groep platform, en de app kan er niets in maken.
# ffmpeg en ffprobe 0711: een proces uit een programma dat niemand mag lezen, is niet dumpbaar.
# env ook 0711: de healthcheck van media start via `env -i`, en zo is de sleutel van hello ook
# in die korte tijd niet in /proc/<pid>/environ te lezen. /srv/zelftest-10001 (leeg, van
# platform) is voor de zelftest van de worker (V1: opnieuw openen om te schrijven faalt).
RUN useradd --system --uid 10002 --no-create-home media \
    && install -d -o media -g platform -m 2750 /run/media \
    && chmod 0711 /usr/bin/ffmpeg /usr/bin/ffprobe /usr/bin/env \
    && install -o platform -g platform -m 0644 /dev/null /srv/zelftest-10001
USER platform
VOLUME /data
# 8080 = http (ook voor de Let's Encrypt-controle), 8443 = https zodra er een certificaat is
EXPOSE 8080 8443
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"
CMD ["python", "-m", "app.serve"]
