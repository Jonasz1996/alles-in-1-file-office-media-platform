#!/usr/bin/env bash
# De snelle e2e van zoeken (fase 6, §4.1 "Snel lokaal", C16): ±4 minuten, groen vóór een pakket
# "klaar" meldt. Uitleg: docs/fase-6/z0-e2e.md.
#
#   e2e/slim.sh              alles: 3.46.1, de web-app als die oud is, de e2e met marker zoek
#   e2e/slim.sh --alleen-db  alleen stap 1 (de tests van zoeken in python:3.12-slim)
#   e2e/slim.sh --alleen-e2e alleen stap 2 en 3
#
# 1. De api-tests met marker zoek of triggers in python:3.12-slim (SQLite 3.46.1, zoals V8): de
#    venv van de host alleen-lezen erin, --network none, en de gemaakte bestanden van
#    tests/search/fixtures.py in een eigen map (api/.pytest_cache/slim). Zo toont een pakket
#    3.46.1 aan zonder te pushen. Zonder ocr, media en zoek_perf: die hebben hun eigen job.
#    De renderprocessen (previews.py) starten met python -I en een kale omgeving: PYTHONPATH
#    telt daar niet. Een .pth in de site-packages van het image geeft ook hen de venv.
# 2. De web-app bouwen als een bestand in web/ nieuwer is dan web/dist/index.html.
# 3. De e2e met marker zoek (e2e/test_zoek_*.py) tegen die build, op de host.
#
# SLIM_CPUS=0-2 houdt alles op die cores (docker --cpuset-cpus en taskset). Aan het eind een
# samenvatting, die begint met "=== kort ===".
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
API="$REPO/api"
WEB="$REPO/web"
IMAGE="${SLIM_IMAGE:-python:3.12-slim}"
CACHE="$API/.pytest_cache/slim"
MARKERS_DB="(zoek or triggers) and not ocr and not media and not media_perf and not zoek_perf"
MARKERS_E2E="zoek and not ocr and not perf and not media and not media_perf"

doe_db=1
doe_e2e=1
case "${1:-}" in
  "") ;;
  --alleen-db) doe_e2e=0 ;;
  --alleen-e2e) doe_db=0 ;;
  *) echo "gebruik: e2e/slim.sh [--alleen-db | --alleen-e2e]" >&2; exit 2 ;;
esac

cpus=()
docker_cpus=()
if [[ -n "${SLIM_CPUS:-}" ]]; then
  cpus=(taskset -c "$SLIM_CPUS")
  docker_cpus=(--cpuset-cpus "$SLIM_CPUS")
fi

kort=()
start=$SECONDS

stap_db() {
  if ! command -v docker >/dev/null; then
    echo "slim.sh: geen docker; stap 1 heeft $IMAGE nodig (of draai --alleen-e2e)" >&2
    exit 1
  fi
  if [[ ! -d "$API/.venv/lib/python3.12/site-packages" ]]; then
    echo "slim.sh: geen venv in api/.venv (eerst: cd api && uv sync)" >&2
    exit 1
  fi
  if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
    docker pull "$IMAGE"
  fi
  mkdir -p "$CACHE/fixtures"
  local t=$SECONDS
  local info versie site
  info="$(docker run --rm --network none "$IMAGE" \
    python -c 'import sqlite3, sysconfig; print(sqlite3.sqlite_version, sysconfig.get_path("purelib"))')"
  read -r versie site <<<"$info"
  echo "/repo/api/.venv/lib/python3.12/site-packages" >"$CACHE/venv.pth"
  echo "== 1. zoeken in $IMAGE (SQLite $versie)"
  docker run --rm --network none "${docker_cpus[@]}" \
    --user "$(id -u):$(id -g)" \
    -v "$REPO:/repo:ro" \
    -v "$CACHE:/cache" \
    -v "$CACHE/venv.pth:$site/venv.pth:ro" \
    -e HOME=/tmp \
    -e PYTHONDONTWRITEBYTECODE=1 \
    -e PYTHONPATH=/repo/api/.venv/lib/python3.12/site-packages \
    -e ZOEK_FIXTURES_CACHE=/cache/fixtures \
    -w /repo/api \
    "$IMAGE" \
    python -m pytest -q -p no:cacheprovider -m "$MARKERS_DB"
  kort+=("1. zoek en triggers in $IMAGE, SQLite $versie: groen ($((SECONDS - t)) s)")
}

stap_web() {
  local t=$SECONDS
  local index="$WEB/dist/index.html"
  local nieuwer=""
  if [[ -f "$index" ]]; then
    nieuwer="$(find "$WEB/src" "$WEB/public" "$WEB/index.html" "$WEB/package.json" \
      "$WEB/vite.config.ts" -newer "$index" -print -quit 2>/dev/null || true)"
  fi
  if [[ ! -f "$index" || -n "$nieuwer" ]]; then
    if [[ ! -d "$WEB/node_modules" ]]; then
      echo "slim.sh: geen web/node_modules (eerst: cd web && npm ci)" >&2
      exit 1
    fi
    echo "== 2. de web-app bouwen${nieuwer:+ ($nieuwer is nieuwer)}"
    (cd "$WEB" && "${cpus[@]}" npm run build --silent)
    kort+=("2. web-app gebouwd ($((SECONDS - t)) s)")
  else
    echo "== 2. de web-app is bij"
    kort+=("2. web-app was bij")
  fi
}

stap_e2e() {
  local t=$SECONDS
  echo "== 3. e2e met marker zoek"
  (cd "$API" && "${cpus[@]}" uv run --with playwright python -m pytest ../e2e -q -m "$MARKERS_E2E")
  kort+=("3. e2e (marker zoek): groen ($((SECONDS - t)) s)")
}

if ((doe_db)); then stap_db; fi
if ((doe_e2e)); then
  stap_web
  stap_e2e
fi

echo
echo "=== kort ==="
printf '%s\n' "${kort[@]}"
echo "slim.sh: groen in $((SECONDS - start)) s"
