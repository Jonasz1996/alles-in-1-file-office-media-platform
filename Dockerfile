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
# start ze; zonder ffmpeg krijgen video's gewoon geen preview.
# tesseract met Nederlands, Engels en de OSD voor de OCR van scans en foto's (fase 6, X1, Keuze 24;
# Jonas: "Ja, erin", Vraag 15; spike S5: 69 MiB). Ook alleen de worker start hem (ops/ocr.py), en
# alleen op een grijs beeld dat render zelf maakte, nooit op de ruwe blob (Keuze 22)
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
       tesseract-ocr tesseract-ocr-nld tesseract-ocr-eng tesseract-ocr-osd \
    && rm -rf /var/lib/apt/lists/*

COPY api/pyproject.toml api/uv.lock ./
RUN uv sync --frozen --no-dev

# Analyse (fase 6, Keuze 57): de extra `analyse` staat NIET in /opt/venv, maar in /opt/analyse
# (0750 root:analyse). Alleen de kinderen van de worker zetten die map in sys.path. Voor 10001 en
# 10002 bestaat numpy dan niet, ook niet als iemand de map zelf in sys.path zet (find_spec is
# None; haalbaarheid.md, K2-09). In /opt/venv met o-rwx zou `import numpy` een leeg
# namespace-pakket geven. Precies vijf pakketten: een zesde laat de build falen, want een nieuw
# pakket is een vraag aan Jonas (B19). chown en chmod in dezelfde RUN: een latere laag kopieert
# alle bestanden opnieuw. --no-cache: anders blijven de wheels en hun uitgepakte kopie (±120 MB)
# in /root/.cache/uv achter, en dan haalt het image de grens van +160 MB niet (A0, a0.md). g-w:
# niets in /opt/analyse is schrijfbaar voor de groep analyse (ook .lock van uv niet).
# 10004 (omzetten) is de gebruiker van analyse-omzetten (X3, K1-05): groep analyse, zodat hij
# numpy kan lezen, maar zonder sleutel, zonder socket en zonder /models.
RUN groupadd --system --gid 10003 analyse \
    && useradd --system --uid 10003 --gid 10003 --no-create-home analyse \
    && useradd --system --uid 10004 --gid 10003 --no-create-home omzetten \
    && uv export --frozen --no-dev --no-emit-project --no-hashes --extra analyse \
       | grep -E '^[a-z]' | sort > /tmp/alles.txt \
    && uv export --frozen --no-dev --no-emit-project --no-hashes \
       | grep -E '^[a-z]' | sort > /tmp/basis.txt \
    && comm -23 /tmp/alles.txt /tmp/basis.txt > /tmp/analyse.txt \
    && test "$(wc -l < /tmp/analyse.txt)" -eq 5 \
    && uv pip install --no-cache --python /opt/venv/bin/python --no-deps --compile-bytecode \
       --target /opt/analyse -r /tmp/analyse.txt \
    && rm /tmp/alles.txt /tmp/basis.txt /tmp/analyse.txt \
    && chown -R root:analyse /opt/analyse \
    && chmod -R g-w,o-rwx /opt/analyse && chmod 0750 /opt/analyse

COPY api/app ./app
COPY api/migrations ./migrations
# De zoekbench (fase 6, Z7, Keuze 54): `python -m app.cli bench --los` laadt hem van hier. Alleen
# de standaardbibliotheek; hij maakt zijn boom in /data/cache en ruimt hem weer op
COPY tools/zoekbench/bench.py ./tools/zoekbench/bench.py
COPY --from=web /src/dist ./web

RUN useradd --system --uid 10001 platform && install -d -o platform -g platform /data

# Media (fase 5, Keuze 11 en 47): de mediaworker draait als media (10002) en kan een blob van
# platform (10001, 0600) dus niet via /proc opnieuw openen. /run/media is van media met groep
# platform (2750, setgid): de socket krijgt groep platform, en de app kan er niets in maken.
# ffmpeg en ffprobe 0711: een proces uit een programma dat niemand mag lezen, is niet dumpbaar.
# nice en ionice ook: zij staan vóór ffmpeg in de argv van een job (args.niceness), en een
# leesbare nice maakt het kind dumpbaar tot zijn exec van ffmpeg, terwijl het de fd's van de job
# al heeft (review golf 1, S1). env ook 0711: de healthcheck van media start via `env -i`, en
# zo is de sleutel van hello ook in die korte tijd niet in /proc/<pid>/environ te lezen; en env
# staat vóór tesseract (OMP_THREAD_LIMIT=1). tesseract 0711 om dezelfde reden: hij heeft de PGM
# van een opdracht op stdin (fase 6, X1, C14).
# /srv/zelftest-10001 (leeg, van platform) is voor de zelftest van de worker (V1: opnieuw
# openen om te schrijven faalt); de zelftest van de analyse gebruikt hem ook. /run/analyse
# (fase 6, §3.11) volgt het patroon van /run/media: van analyse met groep platform.
RUN useradd --system --uid 10002 --no-create-home media \
    && install -d -o media -g platform -m 2750 /run/media \
    && install -d -o analyse -g platform -m 2750 /run/analyse \
    && chmod 0711 /usr/bin/ffmpeg /usr/bin/ffprobe /usr/bin/env /usr/bin/nice /usr/bin/ionice /usr/bin/tesseract \
    && install -o platform -g platform -m 0644 /dev/null /srv/zelftest-10001
# Fase 6, Z2 (Keuze 17, spike S11): het kind van de opdracht text (tekst uit documenten) start
# uit deze kopie van python die niemand mag lezen; dan is het vanaf zijn exec niet dumpbaar. Hij
# vindt de standaardbibliotheek en het venv via /opt/venv/pyvenv.cfg (ops/text.py: python()).
RUN install -o root -g root -m 0711 "$(readlink -f /opt/venv/bin/python)" /opt/venv/bin/python-tekst
USER platform
VOLUME /data
# 8080 = http (ook voor de Let's Encrypt-controle), 8443 = https zodra er een certificaat is
EXPOSE 8080 8443
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health').status==200 else 1)"
CMD ["python", "-m", "app.serve"]
