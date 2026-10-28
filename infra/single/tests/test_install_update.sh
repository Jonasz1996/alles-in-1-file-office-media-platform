#!/usr/bin/env bash
# De installatietest in de sandbox (bouwontwerp fase 4, §2 O6, §4.6 punt 3 en §5 stap 4): een
# installatie uit fase 3 bijwerken naar deze versie, met de echte install.sh, de echte Docker,
# de echte DS en de echte editor in een browser; daarna terug naar fase 3 zoals het runbook het
# zegt, en weer vooruit. Niet in CI: hij bouwt het platform vier keer en start DS een paar keer
# (±17 minuten met een warme build-cache, de eerste keer langer).
#
#   bash infra/single/tests/test_install_update.sh
#
# De stappen: A (fase 3 installeren, een beheerder met 2FA, twee bestanden met een versie), B
# (bijwerken; /api/v1/office/status zegt starting tot de font-herstart van DS voorbij is, dan
# ok; het interne netwerk met een tegenproef), B2 (een losse compose up), S (docker compose stop
# met de echte editor open en getypte tekst; na de start staat die tekst als nieuwe versie in
# het bestand, en DS komt op ondanks een oud pid-bestand van supervisord), K (een ander geheim aan één kant geeft misconfigured, terug geeft ok), C, H, G,
# D, E, F (opnieuw, geblokkeerd, pin, uit, aan), R (terug naar fase 3 met db-downgrade, zoals
# docs/runbooks/office.md) en V (weer vooruit naar deze versie). Uitslag en uitleg:
# docs/fase-4/integratie.md.
#
# Nodig op de host: docker, git, python3, ss, curl, uv en de omgeving van api/ met Playwright
# (`uv run --with playwright`, zoals de e2e) plus een Chromium (E2E_CHROMIUM, of die van de
# sandbox in /opt/pw-browsers). De API-aanroepen doet e2e/install_api.py (alleen de
# standaardbibliotheek), de browser e2e/install_editor.py.
#
# Instellingen (alle optioneel):
#   INSTALL_TEST_DIR         een lege of nieuwe werkmap, absoluut pad (standaard een nieuwe map
#                            onder /tmp). PLATFORM_ROOT en de datamap komen erin
#   INSTALL_TEST_REPO        de git-repo met beide versies (standaard deze)
#   INSTALL_TEST_FROM        de versie uit fase 3 (standaard 3ccee0a)
#   INSTALL_TEST_TO          de nieuwe versie (standaard HEAD van die repo)
#   INSTALL_TEST_PORT        de http-poort van de app (standaard 18480)
#   INSTALL_TEST_HTTPS_PORT  de https-poort (standaard 18443)
#   INSTALL_TEST_DS_SECONDS  hoe lang hooguit op een gezonde DS wachten (standaard 900)
#   INSTALL_TEST_LOGS        een map voor de logs van elke stap (anders gaan ze met de werkmap weg)
#   INSTALL_TEST_CA          een extra CA die de builds moeten vertrouwen, voor een proxy die TLS
#                            opnieuw tekent (in de sandbox standaard /root/.ccr/ca-bundle.crt;
#                            leeg = geen). De verificatie blijft altijd aan
#   INSTALL_TEST_UV          ghcr (standaard: eerst ghcr.io, dan PyPI) of pypi (meteen uv 0.11 uit
#                            het wiel van PyPI, als ghcr.io hier geweigerd wordt)
#   INSTALL_TEST_NEED_GB     zoveel GB moet er bij de start vrij zijn voor Docker (standaard 12:
#                            de 8 van install.sh voor office, plus de basisimages, de build van
#                            fase 3 en wat marge). Minder: de test begint niet
#
# Hoe:
# - install.sh draait in een container met Debian 12 (de "LXC"), met de docker-socket en de
#   docker-cli van de host, en de werkmap op hetzelfde absolute pad: de bind mounts van compose
#   lost de Docker van de host op, dus elk pad moet daar hetzelfde zijn. Het script komt via
#   stdin, zoals bij `curl … | bash`. PLATFORM_REPO is een kale kloon in de werkmap: er wordt
#   niets gepusht en niets van GitHub gehaald.
# - Eerst een installatie van INSTALL_TEST_FROM (met de nieuwe install.sh en PLATFORM_SRC_REF:
#   de oude install.sh kent PLATFORM_REPO niet); die heeft nog geen office. Dan bijwerken.
# - De basisimages van de Dockerfile komen vooraf van mirror.gcr.io (Docker Hub geeft hier vaak
#   429) en krijgen hun eigen naam; uv komt van ghcr.io zelf.
# - Kan een container geen Debian-spiegel bereiken (zoals in de sandbox), dan krijgen de runner
#   (unattended-upgrades) en python:3.12-slim (ffmpeg) een leeg pakket met die naam: apt-get
#   vindt het dan al geïnstalleerd. Video's krijgen in deze test geen preview. De runner is dan
#   buildpack-deps:bookworm-scm (Debian 12 met curl en git, die install.sh anders installeert).
# - Met INSTALL_TEST_CA krijgen python:3.12-slim en node:22-alpine die CA erbij (pip, uv, npm).
#   De runner krijgt hem bewust niet. BuildKit (Docker 29) neemt bij FROM het image uit het
#   register zodra de docker-cli daar een token kan halen, en een lokaal image pas als dat faalt
#   [gemeten]. Zonder de CA faalt dat hier, en bouwt de test met de images die hij klaarzette.
#   Daarom staat ook de frontend docker/dockerfile:1 (de eerste regel van de Dockerfile) lokaal,
#   van mirror.gcr.io.
#   Zo'n afgeleid basisimage bestaat alleen tijdens de test; de Dockerfile blijft zoals hij is.
# - Alleen in de runner staat een nep-laag om docker. Die bootst een onbereikbaar register na
#   voor DS (geen Docker Hub, of helemaal niets), en laat install.sh geen image weghalen dat
#   deze test niet zelf bouwde. Wat install.sh weg wilde halen, staat in de log ("NIET WEG").
# - Geeft dockerd minder dan de 65536 open bestanden van compose.yaml (de sandbox: 20000), dan
#   krijgt elke compose-aanroep van de test één extra bestand met de grens die dockerd wel geeft,
#   alleen voor de app. Anders start de app hier niet.
# - De test begint niet als er al een project "platform", een image platform:local of iets op de
#   poorten is, of als er te weinig ruimte is (INSTALL_TEST_NEED_GB). Aan het eind (ook bij een
#   fout of Ctrl+C) ruimt hij op wat hij maakte: de containers, volumes en netwerken van het
#   project, de images die er vooraf niet waren, de browser en de werkmap. De build-cache van
#   BuildKit blijft (in de sandbox ±1 GB per run); die deelt de test met andere builds op deze
#   Docker.
# - De login van de beheerder (wachtwoord, TOTP-geheim, tokens) staat alleen in de werkmap
#   (sessie.json, rechten 600), nooit in een log; INSTALL_TEST_LOGS neemt hem niet mee.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
# De hulpjes van de test (e2e/install_api.py, e2e/install_editor.py en de omgeving van api/)
# komen uit deze checkout; de versies die geïnstalleerd worden uit INSTALL_TEST_REPO
top=$(git -C "$here" rev-parse --show-toplevel)
REPO_SRC=${INSTALL_TEST_REPO:-$top}
FROM=${INSTALL_TEST_FROM:-3ccee0a}
TO=${INSTALL_TEST_TO:-$(git -C "$REPO_SRC" rev-parse HEAD)}
PORT=${INSTALL_TEST_PORT:-18480}
HTTPS_PORT=${INSTALL_TEST_HTTPS_PORT:-18443}
DS_SECONDS=${INSTALL_TEST_DS_SECONDS:-900}
LOGS_OUT=${INSTALL_TEST_LOGS:-}
# Een extra CA voor de builds (een proxy die TLS opnieuw tekent, zoals in de sandbox)
EXTRA_CA=${INSTALL_TEST_CA-}
if [ -z "${INSTALL_TEST_CA+x}" ] && [ -f /root/.ccr/ca-bundle.crt ]; then EXTRA_CA=/root/.ccr/ca-bundle.crt; fi
UV_FROM=${INSTALL_TEST_UV:-ghcr}
NEED_GB=${INSTALL_TEST_NEED_GB:-12}
PROJECT=platform
RUNNER_NAME=install-test-runner-$$
RUNNER_IMG=install-test-runner:$$
# De tegenproef van het interne netwerk (een losse container op de bridge van Docker)
PROBE_NAME=install-test-proef-$$
runner_built=0
editor_pid=""
watch_pid=""

fails=0
oks=0
ok() {
  printf 'ok    %s\n' "$1"
  oks=$((oks + 1))
}
fout() {
  printf 'FOUT  %s\n' "$1"
  fails=$((fails + 1))
}
check() {
  local name=$1
  shift
  if "$@"; then ok "$name"; else fout "$name"; fi
}
stop() {
  echo "STOP  $1" >&2
  exit 1
}
have() { docker image inspect "$1" >/dev/null 2>&1; }

# --- vooraf: niets van een ander raken ------------------------------------------------------

for tool in docker git python3 ss curl uv; do
  command -v "$tool" >/dev/null || stop "$tool ontbreekt"
done
docker info >/dev/null 2>&1 || stop "Docker draait niet"
# Stap S opent de echte editor met Playwright, zoals de e2e (uv run --with playwright in api/)
(cd "$top/api" && uv run -q --frozen --with playwright python -c 'import playwright' </dev/null >/dev/null 2>&1) ||
  stop "uv run --with playwright lukt niet in $top/api (nodig voor stap S)"
[ -z "$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")" ] ||
  stop "er is al een compose-project '$PROJECT'; deze test zou het raken"
[ -z "$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")" ] ||
  stop "er zijn al volumes van een project '$PROJECT'"
[ -z "$(docker network ls -q --filter "label=com.docker.compose.project=$PROJECT")" ] ||
  stop "er zijn al netwerken van een project '$PROJECT'"
! have platform:local || stop "er is al een image platform:local"
for p in "$PORT" "$HTTPS_PORT"; do
  [ -z "$(ss -Hltn "sport = :$p")" ] || stop "poort $p is bezet"
done
# install.sh zet office alleen aan met 8 GB vrij voor Docker (Keuze 30), gemeten vóór de build
# van de nieuwe versie. Vóór stap B komen er de basisimages (±1 GB) en de build van fase 3
# (±2 GB) bij. Met minder ruimte zou stap B office terecht uit laten; dat is geen uitslag over
# het bijwerken, dus dan begint de test niet.
free_gb() { df --output=avail -B1G "$(docker info --format '{{.DockerRootDir}}')" | tail -n 1 | tr -d ' '; }
free=$(free_gb)
[ "$free" -ge "$NEED_GB" ] ||
  stop "te weinig ruimte: $free GB vrij voor Docker, de test vraagt er $NEED_GB (8 voor de controle van install.sh, plus de basisimages en de build van fase 3)"

if [ -n "${INSTALL_TEST_DIR:-}" ]; then
  WORK=$INSTALL_TEST_DIR
  case "$WORK" in /*) ;; *) stop "INSTALL_TEST_DIR moet een absoluut pad zijn" ;; esac
  if [ -e "$WORK" ] && [ -n "$(ls -A "$WORK")" ]; then stop "$WORK is niet leeg"; fi
  work_existed=0
  if [ -e "$WORK" ]; then work_existed=1; fi
  mkdir -p "$WORK"
else
  WORK=$(mktemp -d /tmp/install-test.XXXXXX)
  work_existed=0
fi
WORK=$(cd "$WORK" && pwd -P)
ROOT=$WORK/platform
created=()     # images (namen) die deze test maakte
maybe=()       # namen die install.sh misschien maakt; weg als ze er vooraf niet waren
own_project=1  # vanaf hier is alles van het project "platform" van deze test

# Een proces en alles wat het startte (uv → python → de driver van Playwright → Chromium), op PID
kill_tree() {
  local child
  for child in $(ps -o pid= --ppid "$1" 2>/dev/null); do kill_tree "$child"; done
  kill "$1" 2>/dev/null || true
}

opruimen() {
  local rc=$? ids ref i pid
  set +e
  trap - EXIT INT TERM
  echo
  echo "== Opruimen"
  for pid in "$editor_pid" "$watch_pid"; do
    [ -z "$pid" ] || kill_tree "$pid"
  done
  docker rm -f "$RUNNER_NAME" "$PROBE_NAME" >/dev/null 2>&1
  if [ "$own_project" = 1 ]; then
    ids=$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")
    # shellcheck disable=SC2086
    [ -z "$ids" ] || docker rm -f -v $ids >/dev/null
    ids=$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")
    # shellcheck disable=SC2086
    [ -z "$ids" ] || docker volume rm $ids >/dev/null
    ids=$(docker network ls -q --filter "label=com.docker.compose.project=$PROJECT")
    # shellcheck disable=SC2086
    [ -z "$ids" ] || docker network rm $ids >/dev/null
    # Elke versie van de app die deze test bouwde
    if [ -f "$WORK/shim/allow" ]; then
      while read -r ref; do
        [ -z "$ref" ] || docker image rm -f "$ref" >/dev/null 2>&1
      done <"$WORK/shim/allow"
    fi
    if have platform:local; then docker image rm -f platform:local >/dev/null; fi
  fi
  for ref in "${maybe[@]}"; do
    if have "$ref"; then docker image rm "$ref" >/dev/null || echo "kon $ref niet weghalen"; fi
  done
  for ((i = ${#created[@]} - 1; i >= 0; i--)); do
    docker image rm "${created[i]}" >/dev/null 2>&1 || echo "kon ${created[i]} niet weghalen"
  done
  if [ -n "$LOGS_OUT" ] && [ -d "$WORK/logs" ]; then
    mkdir -p "$LOGS_OUT" && cp -r "$WORK/logs/." "$LOGS_OUT/" && echo "Logs: $LOGS_OUT"
  fi
  if [ "$work_existed" = 1 ]; then
    find "$WORK" -mindepth 1 -delete
  else
    rm -rf "$WORK"
  fi
  # Wat er nog over is (hoort niets te zijn)
  ids="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")$(docker network ls -q --filter "label=com.docker.compose.project=$PROJECT")"
  if [ -n "$ids" ] || have platform:local || { [ "$runner_built" = 1 ] && have "$RUNNER_IMG"; }; then
    echo "LET OP: er is nog iets van de test over"
    rc=1
  else
    echo "Alles van de test is weg (containers, volumes, netwerken, images, $WORK)."
  fi
  exit "$rc"
}
trap opruimen EXIT
trap 'exit 130' INT TERM

mkdir -p "$WORK/logs" "$WORK/shim/bin" "$WORK/build"
: >"$WORK/shim/allow"

# --- de broncode -----------------------------------------------------------------------------

echo "== Voorbereiden ($FROM -> ${TO:0:12}, werkmap $WORK)"
git clone -q --bare "$REPO_SRC" "$WORK/repo.git"
for c in "$FROM" "$TO"; do
  git -C "$WORK/repo.git" cat-file -e "$c^{commit}" 2>/dev/null || stop "versie $c staat niet in $REPO_SRC"
done
git -C "$WORK/repo.git" show "$TO:infra/single/install.sh" >"$WORK/install.sh"
digest=$(sed -n 's/^OFFICE_DIGEST=//p' "$WORK/install.sh")
ds_repo=$(sed -n 's/^OFFICE_REPO=//p' "$WORK/install.sh")
ds_tag=$(sed -n 's/^OFFICE_TAG=//p' "$WORK/install.sh")
# De tag die install.sh compose geeft (uitrol-1), en een pin zoals het runbook hem maakt: een
# naam uit RepoDigests
DS_DEFAULT=$ds_repo:$ds_tag
DS_PIN=mirror.gcr.io/$ds_repo@$digest

# --- images ----------------------------------------------------------------------------------

pull_new() {
  have "$1" && return 0
  docker pull -q "$1" >/dev/null || stop "kan $1 niet ophalen"
  created+=("$1")
}
name_as() {
  if have "$2"; then
    echo "  $2 is er al; die blijft zoals hij is"
    return 0
  fi
  docker tag "$1" "$2"
  created+=("$2")
}
cat >"$WORK/build/leeg-pakket.sh" <<'SH'
#!/bin/sh
# Een leeg pakket met deze naam, voor een container zonder Debian-spiegel
set -e
d=$(mktemp -d)
mkdir -p "$d/DEBIAN"
printf 'Package: %s\nVersion: 99:0-installatietest\nArchitecture: all\nMaintainer: installatietest <test@invalid>\nDescription: leeg pakket voor de installatietest\n' "$1" >"$d/DEBIAN/control"
dpkg-deb --build "$d" "/tmp/$1.deb" >/dev/null
dpkg -i "/tmp/$1.deb" >/dev/null
rm -rf "$d" "/tmp/$1.deb"
SH

echo "== Basisimages (van mirror.gcr.io; uv van ghcr.io)"
pull_new mirror.gcr.io/library/python:3.12-slim
pull_new mirror.gcr.io/library/node:22-alpine
apt_ok=0
if docker run --rm mirror.gcr.io/library/python:3.12-slim \
  sh -c 'apt-get update >/dev/null 2>&1; ls /var/lib/apt/lists | grep -q _Packages' </dev/null; then
  apt_ok=1
fi
ca=""
if [ -n "$EXTRA_CA" ]; then
  [ -f "$EXTRA_CA" ] || stop "INSTALL_TEST_CA wijst niet naar een bestand"
  cp "$EXTRA_CA" "$WORK/build/extra-ca.crt"
  ca=1
  echo "  de builds vertrouwen ook $EXTRA_CA (de proxy van deze omgeving)"
fi
[ "$apt_ok" = 1 ] || echo "  geen Debian-spiegel bereikbaar: lege pakketten voor ffmpeg en unattended-upgrades"
# python:3.12-slim: echt, of met de CA en/of een leeg pakket ffmpeg erbij
if [ "$apt_ok" = 1 ] && [ -z "$ca" ]; then
  name_as mirror.gcr.io/library/python:3.12-slim python:3.12-slim
else
  ! have python:3.12-slim || stop "python:3.12-slim bestaat al; deze test vervangt hem niet"
  {
    echo "FROM mirror.gcr.io/library/python:3.12-slim"
    if [ -n "$ca" ]; then
      echo "COPY extra-ca.crt /etc/ssl/installatietest-ca.crt"
      echo "ENV SSL_CERT_FILE=/etc/ssl/installatietest-ca.crt PIP_CERT=/etc/ssl/installatietest-ca.crt UV_SYSTEM_CERTS=1"
    fi
    if [ "$apt_ok" = 0 ]; then
      echo "COPY leeg-pakket.sh /tmp/leeg-pakket.sh"
      echo "RUN sh /tmp/leeg-pakket.sh ffmpeg && rm /tmp/leeg-pakket.sh"
    fi
  } >"$WORK/build/python.Dockerfile"
  docker build -q -t python:3.12-slim -f "$WORK/build/python.Dockerfile" "$WORK/build" >/dev/null
  created+=(python:3.12-slim)
fi
# node:22-alpine: echt, of met de CA voor npm
if [ -z "$ca" ]; then
  name_as mirror.gcr.io/library/node:22-alpine node:22-alpine
else
  ! have node:22-alpine || stop "node:22-alpine bestaat al; deze test vervangt hem niet"
  docker build -q -t node:22-alpine -f - "$WORK/build" >/dev/null <<'DOCKERFILE'
FROM mirror.gcr.io/library/node:22-alpine
COPY extra-ca.crt /etc/ssl/installatietest-ca.crt
ENV NODE_EXTRA_CA_CERTS=/etc/ssl/installatietest-ca.crt
DOCKERFILE
  created+=(node:22-alpine)
fi
# De runner ("de LXC"): debian:12, of zonder Debian-spiegel buildpack-deps:bookworm-scm (Debian 12
# met curl en git) met een leeg pakket unattended-upgrades
if [ "$apt_ok" = 1 ]; then
  pull_new mirror.gcr.io/library/debian:12
  RUNNER_IMG=mirror.gcr.io/library/debian:12
else
  pull_new mirror.gcr.io/library/buildpack-deps:bookworm-scm
  docker build -q -t "$RUNNER_IMG" -f - "$WORK/build" >/dev/null <<'DOCKERFILE'
FROM mirror.gcr.io/library/buildpack-deps:bookworm-scm
COPY leeg-pakket.sh /tmp/leeg-pakket.sh
RUN sh /tmp/leeg-pakket.sh unattended-upgrades && rm /tmp/leeg-pakket.sh
DOCKERFILE
  created+=("$RUNNER_IMG")
  runner_built=1
fi
if have ghcr.io/astral-sh/uv:0.11; then
  :
elif [ "$UV_FROM" != pypi ] && docker pull -q ghcr.io/astral-sh/uv:0.11 >/dev/null 2>&1; then
  created+=(ghcr.io/astral-sh/uv:0.11)
else
  # In de sandbox weigert de proxy de blobs van ghcr.io (403). Dan dezelfde uv 0.11 uit het
  # wiel van PyPI, onder de naam die de Dockerfile verwacht (alleen voor deze test)
  echo "  ghcr.io gaf uv niet: uv 0.11 uit het wiel van PyPI, onder dezelfde naam"
  docker build -q -t ghcr.io/astral-sh/uv:0.11 -f - "$WORK/build" >/dev/null <<'DOCKERFILE' ||
FROM python:3.12-slim AS wiel
RUN pip download -q --only-binary=:all: --no-deps "uv>=0.11,<0.12" -d /w \
 && python -c "import glob, zipfile; z = zipfile.ZipFile(glob.glob('/w/uv-*.whl')[0]); [open('/' + n.rsplit('/', 1)[1], 'wb').write(z.read(n)) for n in z.namelist() if n.endswith(('.data/scripts/uv', '.data/scripts/uvx'))]" \
 && chmod 755 /uv /uvx && /uv --version
FROM scratch
COPY --from=wiel /uv /uvx /
DOCKERFILE
    stop "kan uv niet ophalen (ghcr.io en PyPI)"
  created+=(ghcr.io/astral-sh/uv:0.11)
fi
# De frontend van de Dockerfile (# syntax=docker/dockerfile:1); BuildKit vindt hem lokaal als
# het register voor de runner onbereikbaar is
if ! have docker/dockerfile:1; then
  pull_new mirror.gcr.io/docker/dockerfile:1
  name_as mirror.gcr.io/docker/dockerfile:1 docker/dockerfile:1
fi
for ref in mirror.gcr.io/library/hello-world hello-world "$ds_repo:$ds_tag"; do
  have "$ref" || maybe+=("$ref")
done
if ! have "$ds_repo@$digest" && ! have "mirror.gcr.io/$ds_repo@$digest"; then
  echo "  DS staat er nog niet; ophalen van mirror.gcr.io (±3 GB)"
  pull_new "mirror.gcr.io/$ds_repo:$ds_tag@$digest"
fi
echo "  vrij voor Docker: $(free_gb) GB"
# compose.yaml geeft de app 65536 open bestanden. Mag dockerd hier minder geven (in de sandbox
# 20000), dan start geen container met die grens. Dan krijgt elke compose-aanroep van de test
# (in de runner via de nep-laag, en de losse compose van B2) één extra bestand met de grens die
# dockerd wel geeft. Alleen de grens van de app verandert; aan DS verandert niets.
SANDBOX_COMPOSE=""
if ! docker run --rm --ulimit nofile=65536:65536 mirror.gcr.io/library/python:3.12-slim true </dev/null >/dev/null 2>&1; then
  nofile=$(sed -n 's/^Max open files *[0-9]* *\([0-9]*\).*/\1/p' "/proc/$(pidof dockerd | cut -d' ' -f1)/limits" 2>/dev/null || true)
  [ -n "$nofile" ] || nofile=$(ulimit -Hn)
  docker run --rm --ulimit "nofile=$nofile:$nofile" mirror.gcr.io/library/python:3.12-slim true </dev/null >/dev/null 2>&1 ||
    stop "dockerd geeft geen 65536 open bestanden, en ook geen $nofile"
  SANDBOX_COMPOSE=$WORK/build/sandbox-nofile.yaml
  printf '# Alleen voor test_install_update.sh: dockerd geeft hier hooguit %s open bestanden\nservices:\n  platform:\n    ulimits:\n      nofile: {soft: %s, hard: %s}\n' \
    "$nofile" "$nofile" "$nofile" >"$SANDBOX_COMPOSE"
  echo "  dockerd geeft hier geen 65536 open bestanden: de app krijgt er in deze test $nofile"
fi

# --- de nep-laag om docker (alleen in de runner) ---------------------------------------------

cat >"$WORK/shim/bin/docker" <<'SHIM'
#!/usr/bin/env bash
# Nep-laag om docker, alleen in de runner van test_install_update.sh. SHIM_DS:
#   gewoon       alles echt
#   geen-hub     DS van Docker Hub is er niet en is niet te halen, tot install.sh zelf de naam
#                van Docker Hub erop zet; van mirror.gcr.io wel
#   geblokkeerd  DS is er niet en is nergens te halen
# Een image weghalen mag alleen als het in SHIM_ALLOW staat (de versies van de app die de test
# bouwde); verder niets weghalen of opruimen. SHIM_COMPOSE_EXTRA: zie hieronder bij compose.
real=/usr/bin/docker
log=${SHIM_LOG:-/dev/null}
mode=${SHIM_DS:-gewoon}
state=${SHIM_STATE:-/tmp/shim}
printf 'docker %s\n' "$*" >>"$log"
last=${!#}
is_ds() { case "$1" in *onlyoffice/documentserver*) return 0 ;; *) return 1 ;; esac; }
is_hub_ds() {
  case "$1" in onlyoffice/documentserver* | docker.io/onlyoffice/documentserver*) return 0 ;; *) return 1 ;; esac
}
hidden() {
  case "$mode" in
    geen-hub) is_hub_ds "$1" && [ ! -e "$state/hub-naam" ] ;;
    geblokkeerd) is_ds "$1" ;;
    *) return 1 ;;
  esac
}
unreachable() {
  case "$mode" in
    geen-hub) is_hub_ds "$1" ;;
    geblokkeerd) is_ds "$1" ;;
    *) return 1 ;;
  esac
}
refuse() {
  printf 'NIET WEG %s\n' "$*" >>"$log"
  exit 1
}
case "${1-}" in
  compose)
    # SHIM_COMPOSE_EXTRA: een extra compose-bestand na de laatste -f (de grens van de sandbox)
    if [ -n "${SHIM_COMPOSE_EXTRA:-}" ]; then
      args=("$@") at=-1
      for i in "${!args[@]}"; do
        if [ "${args[i]}" = -f ] && [[ ${args[i + 1]-} == *.yaml ]]; then at=$((i + 1)); fi
      done
      if [ "$at" -ge 0 ]; then
        exec "$real" "${args[@]:0:at+1}" -f "$SHIM_COMPOSE_EXTRA" "${args[@]:at+1}"
      fi
    fi
    ;;
  pull)
    if unreachable "$last"; then
      printf 'GEBLOKKEERD pull %s\n' "$last" >>"$log"
      echo "Error response from daemon: (nep) $last is niet bereikbaar" >&2
      exit 1
    fi
    ;;
  tag)
    if [ "$mode" = geblokkeerd ] && is_ds "${2-}"; then exit 1; fi
    if is_hub_ds "${3-}"; then mkdir -p "$state" && : >"$state/hub-naam"; fi
    ;;
  image)
    case "${2-}" in
      inspect) if hidden "$last"; then exit 1; fi ;;
      rm | remove)
        shift 2
        for ref in "$@"; do
          case "$ref" in -*) continue ;; esac
          grep -qxF -- "$ref" "${SHIM_ALLOW:-/dev/null}" || refuse "$ref"
        done
        exec "$real" image rm "$@"
        ;;
      prune) refuse "image prune" ;;
    esac
    ;;
  rmi) refuse "rmi ${*:2}" ;;
  system | builder | container | volume | network)
    case "${2-}" in rm | remove | prune) refuse "$*" ;; esac
    ;;
esac
exec "$real" "$@"
SHIM
chmod 755 "$WORK/shim/bin/docker"

# --- hulpjes ---------------------------------------------------------------------------------

cat >"$WORK/check.py" <<'PY'
"""De afscherming van DS volgens docker inspect (test_install_update.sh)."""

import json
import sys

container, network, limits = sys.argv[1:4]
c = json.load(open(container))[0]
n = json.load(open(network))[0]
h = c["HostConfig"]


def caps(xs):
    return sorted(x.upper().removeprefix("CAP_") for x in (xs or []))


want = sorted(["CHOWN", "SETUID", "SETGID", "DAC_OVERRIDE", "FOWNER", "NET_BIND_SERVICE", "KILL"])
problems = []
if caps(h.get("CapDrop")) != ["ALL"]:
    problems.append("CapDrop %s" % h.get("CapDrop"))
if caps(h.get("CapAdd")) != want:
    problems.append("CapAdd %s" % h.get("CapAdd"))
if "no-new-privileges:true" not in [o.replace("=", ":") for o in (h.get("SecurityOpt") or [])]:
    problems.append("SecurityOpt %s" % h.get("SecurityOpt"))
if h.get("PidsLimit") != 512:
    problems.append("PidsLimit %s" % h.get("PidsLimit"))
if h.get("Privileged"):
    problems.append("Privileged")
if h.get("PortBindings") or h.get("PublishAllPorts"):
    problems.append("poorten %s" % h.get("PortBindings"))
if sorted(c["NetworkSettings"]["Networks"]) != ["platform_office"]:
    problems.append("netwerken %s" % sorted(c["NetworkSettings"]["Networks"]))
if n.get("Internal") is not True:
    problems.append("netwerk platform_office niet intern")
if limits == "1" and (h.get("Memory") != 2 * 1024**3 or h.get("NanoCpus") != 2 * 10**9):
    problems.append("grenzen %s %s" % (h.get("Memory"), h.get("NanoCpus")))
mounts = {m["Destination"]: m for m in c["Mounts"]}
cfg = mounts.get("/etc/onlyoffice/documentserver/local-production-linux.json")
if not cfg or cfg.get("RW"):
    problems.append("local-production-linux.json niet alleen-lezen gekoppeld")
volumes = sorted(m["Name"] for m in c["Mounts"] if m["Type"] == "volume")
if volumes != ["platform_office-data", "platform_office-fonts", "platform_office-lib", "platform_office-logs"]:
    problems.append("volumes %s" % volumes)
print("; ".join(problems) or "ok")
PY

cat >"$WORK/version.py" <<'PY'
# In de container van de app: version met en zonder token, met de instellingen van de app
import asyncio

from app.config import Settings
from app.office.oo.client import DocServer


async def main():
    ds = DocServer(Settings())
    try:
        signed = await ds.command("version")
        unsigned = await ds.command("version", signed=False)
        print(signed.get("version"), unsigned.get("error"))
    finally:
        await ds.aclose()


asyncio.run(main())
PY

cid() {
  docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" \
    --filter "label=com.docker.compose.service=$1" | head -n 1
}
running() { [ -n "$1" ] && [ "$(docker inspect --format '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }
image_of() { docker inspect --format '{{.Config.Image}}' "$1" 2>/dev/null; }
env_value() { sed -n "s/^$1=//p" "$ROOT/.env"; }
container_env() { docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$1" | sed -n "s/^$2=//p"; }
same_secret() { [ "$(container_env "$1" "$2" | sha256sum)" = "$(env_value "$3" | sha256sum)" ]; }
is_hex64() { [[ $1 =~ ^[0-9a-f]{64}$ ]]; }
app_up() { curl -fs -o /dev/null -m 5 "http://127.0.0.1:$PORT/health"; }
# Na een start met de hand (compose start of up): de app krijgt een minuut
app_comes_up() {
  local end=$((SECONDS + 60))
  until app_up; do
    [ "$SECONDS" -lt "$end" ] || return 1
    sleep 2
  done
}
# De API van de installatie, als de beheerder (e2e/install_api.py; de login in sessie.json)
api() {
  INSTALL_API_URL="http://127.0.0.1:$PORT" INSTALL_API_STATE="$WORK/sessie.json" \
    python3 -I "$top/e2e/install_api.py" "$@"
}
# text_has <bestand> <woord> [versie]: staat het woord in de docx?
text_has() {
  local text
  text=$(api text "$1" ${3:+"$3"}) || return 1
  [[ $text == *"$2"* ]]
}
no_notice() { ! api notice "$1"; }
# De revisie van het schema, gelezen in de container van de app
db_rev() {
  docker exec "$(cid platform)" python -c 'import sqlite3
c = sqlite3.connect("file:/data/platform.db?mode=ro", uri=True)
print(c.execute("select version_num from alembic_version").fetchone()[0])' </dev/null 2>/dev/null
}
epoch() { date -d "$1" +%s.%N; }
# Stopte container $1 vóór container $2?
finished_before() {
  awk -v a="$(epoch "$(docker inspect -f '{{.State.FinishedAt}}' "$1")")" \
    -v b="$(epoch "$(docker inspect -f '{{.State.FinishedAt}}' "$2")")" 'BEGIN { exit !(a < b) }'
}
secrets_differ() {
  [ "$(container_env "$1" PLATFORM_OFFICE_SECRET | sha256sum)" != "$(container_env "$2" JWT_SECRET | sha256sum)" ]
}
secrets_match() {
  [ "$(container_env "$1" PLATFORM_OFFICE_SECRET | sha256sum)" = "$(container_env "$2" JWT_SECRET | sha256sum)" ]
}
env_like_b() { [ "$(sha256sum "$ROOT/.env" | cut -d' ' -f1)" = "$(cat "$WORK/env-b.sha")" ]; }
said() { grep -qF -- "$2" "$WORK/logs/$1.log"; }
in_log() { grep -qF -- "$2" "$WORK/logs/$1.docker"; }
no_office() { [ -z "$(cid office)" ]; }
has_curl() { docker exec "$1" curl --version </dev/null >/dev/null 2>&1; }
office_volumes() {
  docker volume inspect platform_office-lib platform_office-data platform_office-logs \
    platform_office-fonts >/dev/null 2>&1
}
limits=0
[ "$(docker info --format '{{.MemoryLimit}} {{.CPUCfsQuota}}')" = "true true" ] && limits=1
compose_args=(--project-directory "$ROOT/src/infra/single" --env-file "$ROOT/.env"
  -f "$ROOT/src/infra/single/compose.yaml" -f "$ROOT/src/infra/single/compose.office.yaml")
[ "$limits" = 0 ] || compose_args+=(-f "$ROOT/src/infra/single/compose.office-limits.yaml")
[ -z "$SANDBOX_COMPOSE" ] || compose_args+=(-f "$SANDBOX_COMPOSE")
# Zonder office (de functie dc uit de README als er geen container van DS is)
base_args=(--project-directory "$ROOT/src/infra/single" --env-file "$ROOT/.env"
  -f "$ROOT/src/infra/single/compose.yaml")
[ -z "$SANDBOX_COMPOSE" ] || base_args+=(-f "$SANDBOX_COMPOSE")

# Eén run van install.sh in de runner. Het script komt via stdin (zoals curl | bash).
step() {
  local name=$1 rc=0 kv envs=() id
  shift
  for kv in "$@"; do envs+=(-e "$kv"); done
  echo
  echo "== $name: install.sh $*"
  timeout 1800 docker run -i --rm --name "$RUNNER_NAME" --network host \
    -v /var/run/docker.sock:/var/run/docker.sock \
    -v "$(command -v docker)":/usr/bin/docker:ro \
    -v /usr/libexec/docker/cli-plugins:/usr/libexec/docker/cli-plugins:ro \
    -v "$WORK:$WORK" \
    -e PLATFORM_REPO="$WORK/repo.git" -e PLATFORM_ROOT="$ROOT" \
    -e PORT="$PORT" -e HTTPS_PORT="$HTTPS_PORT" \
    -e SHIM_LOG="$WORK/logs/$name.docker" -e SHIM_ALLOW="$WORK/shim/allow" \
    -e SHIM_STATE="$WORK/shim/state-$name" -e SHIM_COMPOSE_EXTRA="$SANDBOX_COMPOSE" \
    -e PATH="$WORK/shim/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
    "${envs[@]}" "$RUNNER_IMG" bash -s <"$WORK/install.sh" >"$WORK/logs/$name.log" 2>&1 || rc=$?
  # Elke versie van de app die deze test bouwt, mag later weg (en mag install.sh weghalen)
  id=$(docker image inspect --format '{{.Id}}' platform:local 2>/dev/null || true)
  if [ -n "$id" ] && ! grep -qxF "$id" "$WORK/shim/allow"; then echo "$id" >>"$WORK/shim/allow"; fi
  if [ "$rc" != 0 ]; then
    echo "install.sh gaf $rc; de laatste regels:"
    tail -n 30 "$WORK/logs/$name.log"
  fi
  sed -n 's/^\(Office: .*\)/  \1/p; s/^Let op: \(.*\)/  Let op: \1/p' "$WORK/logs/$name.log"
  grep -h '^NIET WEG' "$WORK/logs/$name.docker" 2>/dev/null | sed 's/^/  install.sh wilde weghalen (tegengehouden): /' || true
  return "$rc"
}

# Wacht tot DS gezond is, gezien vanuit de app (het interne netwerk)
wait_ds() {
  local end=$((SECONDS + DS_SECONDS)) start=$SECONDS app answer
  while [ "$SECONDS" -lt "$end" ]; do
    app=$(cid platform)
    answer=$(docker exec "$app" python -c 'import urllib.request as u
try:
    print(u.urlopen("http://office/healthcheck", timeout=5).read().decode().strip())
except Exception:
    print("nee")' 2>/dev/null </dev/null || true)
    if [ "$answer" = true ]; then
      echo "  DS gezond na $((SECONDS - start)) s"
      return 0
    fi
    sleep 5
  done
  return 1
}

# version via de client van de app: met token 10.0.0.x, zonder token error 6. Herhaalt tot het
# lukt (bij een nieuwe container herstart DS zijn diensten na de fonts)
ds_version() {
  local end=$((SECONDS + DS_SECONDS)) answer=""
  while [ "$SECONDS" -lt "$end" ]; do
    answer=$(docker exec -i "$(cid platform)" python - <"$WORK/version.py" 2>/dev/null || true)
    case "$answer" in "10.0.0"*" 6") echo "  version: $answer"; return 0 ;; esac
    sleep 5
  done
  echo "  version: ${answer:-geen antwoord}"
  return 1
}

hardened() {
  local office=$1 result
  docker inspect "$office" >"$WORK/office.json"
  docker network inspect platform_office >"$WORK/network.json"
  result=$(python3 "$WORK/check.py" "$WORK/office.json" "$WORK/network.json" "$limits")
  [ "$result" = ok ] || echo "  afscherming: $result"
  [ "$result" = ok ]
}

# --- A. Een installatie uit fase 3 -----------------------------------------------------------

step a-fase3 PLATFORM_SRC_REF="$FROM" || stop "de installatie van $FROM lukte niet"
check "A: de app draait op poort $PORT" app_up
check "A: de broncode staat op $FROM" test "$(git -C "$ROOT/src" rev-parse HEAD)" = "$(git -C "$WORK/repo.git" rev-parse "$FROM^{commit}")"
check "A: .env zoals in fase 3 (vijf regels, geen office)" test "$(wc -l <"$ROOT/.env")" = 5
check "A: geen container van DS" no_office
check "A: install.sh zegt waarom office uit is" said a-fase3 "Office: uit, want deze versie van het platform heeft nog geen office"
cp "$ROOT/.env" "$WORK/env-fase3"
check "A: het schema staat op 0006 (fase 3)" test "$(db_rev)" = 0006
# Een beheerder met 2FA (verplicht), zodat te zien is dat de data het bijwerken en terugrollen
# overleeft, en zodat de test /api/v1/office/status kan vragen. Het TOTP-geheim komt van
# /auth/2fa/setup; de codes rekent install_api.py zelf uit
check "A: een beheerder aangemaakt, met 2FA aan" api setup
check "A: inloggen zonder 2FA-code wordt geweigerd (totp_required)" api login-zonder-code
# Twee bestanden van vóór de update: notitie.txt met één versie (twee keer geüpload), en
# verslag.docx in de map Werk (die bewerkt stap S in de echte editor)
printf 'Versie 1, van vóór de update\n' >"$WORK/notitie-1.txt"
printf 'Versie 2, van vóór de update\n' >"$WORK/notitie-2.txt"
git -C "$WORK/repo.git" show "$TO:e2e/fixtures/office/proef.docx" >"$WORK/proef.docx"
note_v1=$(sha256sum "$WORK/notitie-1.txt" | cut -d' ' -f1)
note_v2=$(sha256sum "$WORK/notitie-2.txt" | cut -d' ' -f1)
docx_sha=$(sha256sum "$WORK/proef.docx" | cut -d' ' -f1)
root=$(api get /api/v1/auth/me root_id) || stop "A: /auth/me gaf geen map"
werk=$(api post "/api/v1/files/$root/folders" '{"name": "Werk"}' id) || stop "A: de map Werk lukte niet"
note=$(api upload "$root" notitie.txt "$WORK/notitie-1.txt") || stop "A: uploaden lukte niet"
api upload "$root" notitie.txt "$WORK/notitie-2.txt" replace >/dev/null || stop "A: vervangen lukte niet"
docx=$(api upload "$werk" verslag.docx "$WORK/proef.docx") || stop "A: uploaden lukte niet"
check "A: notitie.txt heeft één versie" test "$(api count "/api/v1/files/$note/versions")" = 1
check "A: verslag.docx staat in Werk, zonder versie" test "$(api count "/api/v1/files/$docx/versions")" = 0

# --- B. Bijwerken naar deze versie, DS alleen van de mirror -----------------------------------

step b-bijwerken PLATFORM_SRC_REF="$TO" SHIM_DS=geen-hub || fout "B: install.sh gaf een fout"
# Meteen na install.sh (DS is net gestart): inloggen met 2FA, en dan elke seconde
# /api/v1/office/status, op de achtergrond, tot ok (review ontwerp-2)
check "B: de beheerder logt in met wachtwoord en 2FA-code" api login
api watch "$DS_SECONDS" "$WORK/logs/b-status.txt" &
watch_pid=$!
check "B: de app draait op poort $PORT" app_up
check "B: de broncode staat op ${TO:0:12}" test "$(git -C "$ROOT/src" rev-parse HEAD)" = "$(git -C "$WORK/repo.git" rev-parse "$TO^{commit}")"
check "B: de beheerder bestaat nog (setup niet meer nodig)" grep -q '"setup_required":false' <<<"$(curl -fsS -m 5 "http://127.0.0.1:$PORT/api/v1/auth/status")"
check "B: de vijf regels van fase 3 staan er nog" test "$(head -n 5 "$ROOT/.env")" = "$(cat "$WORK/env-fase3")"
check "B: OFFICE=aan in .env" test "$(env_value OFFICE)" = aan
check "B: OFFICE_JWT_SECRET is 64 hex-tekens" is_hex64 "$(env_value OFFICE_JWT_SECRET)"
check "B: OFFICE_LINK_SECRET is 64 hex-tekens" is_hex64 "$(env_value OFFICE_LINK_SECRET)"
check "B: twee verschillende geheimen" test "$(env_value OFFICE_JWT_SECRET)" != "$(env_value OFFICE_LINK_SECRET)"
check "B: .env heeft rechten 600" test "$(stat -c %a "$ROOT/.env")" = 600
sha256sum "$ROOT/.env" | cut -d' ' -f1 >"$WORK/env-b.sha"
office=$(cid office)
app=$(cid platform)
check "B: DS draait" running "$office"
check "B: DS op de tag van Docker Hub" test "$(image_of "$office")" = "$DS_DEFAULT"
check "B: die tag wijst naar de digest van install.sh" grep -q "@$digest\$" <<<"$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$DS_DEFAULT")"
check "B: Docker Hub geblokkeerd, dus van de mirror" in_log b-bijwerken "GEBLOKKEERD pull $ds_repo@$digest"
check "B: daarna de naam van Docker Hub erop (M19)" in_log b-bijwerken "docker tag mirror.gcr.io/$ds_repo@$digest $ds_repo:$ds_tag"
check "B: de app kent DS (PLATFORM_OFFICE_URL)" test "$(container_env "$app" PLATFORM_OFFICE_URL)" = http://office
check "B: het JWT-geheim van de app komt uit .env" same_secret "$app" PLATFORM_OFFICE_SECRET OFFICE_JWT_SECRET
check "B: het JWT-geheim van DS komt uit .env" same_secret "$office" JWT_SECRET OFFICE_JWT_SECRET
check "B: het link-geheim van DS komt uit .env" same_secret "$office" SECURE_LINK_SECRET OFFICE_LINK_SECRET
check "B: de afscherming van DS zoals ontworpen" hardened "$office"
check "B: DS wordt gezond (binnen $DS_SECONDS s)" wait_ds
check "B: de app praat met DS met de geheimen uit .env (version)" ds_version
check "B: DS heeft curl (voor de volgende controle)" has_curl "$office"
if docker exec "$office" curl -s -o /dev/null -m 5 https://example.com </dev/null; then
  fout "B: DS komt niet bij internet (het netwerk office is intern)"
else
  ok "B: DS komt niet bij internet (het netwerk office is intern)"
fi
# De tegenproef: zonder die zegt "curl faalt" weinig (in de sandbox is example.com voor geen
# enkele container op te zoeken). Hetzelfde image op een gewoon netwerk (de bridge van Docker,
# niet van het project) krijgt wel een verbinding: met example.com, of met pypi.org (dat laat
# de sandbox door). remote_ip zegt of er een verbinding was, ook als TLS daarna faalt (de proxy
# van de sandbox). Daarna DS naar precies dat adres, zonder DNS (--resolve): geen route.
reach=""
for host in example.com pypi.org; do
  ip=$(docker run --rm --name "$PROBE_NAME" --network bridge --entrypoint curl "$DS_DEFAULT" \
    -s -o /dev/null -w '%{remote_ip}' -m 5 "https://$host/" </dev/null 2>/dev/null || true)
  if [ -n "$ip" ]; then
    reach="$host $ip"
    break
  fi
done
check "B: tegenproef: hetzelfde image op een gewoon netwerk komt wel buiten (${reach:-niets})" test -n "$reach"
if [ -n "$reach" ]; then
  got=$(docker exec "$office" curl -s -o /dev/null -w '%{remote_ip}' -m 5 \
    --resolve "${reach% *}:443:${reach#* }" "https://${reach% *}/" </dev/null 2>/dev/null || true)
  check "B: DS komt ook met dat adres (zonder DNS) niet buiten" test -z "$got"
fi
check "B: DS heeft geen poort op de host" test -z "$(docker port "$office")"
docker volume inspect --format '{{.Name}} {{.CreatedAt}}' platform_office-lib platform_office-data \
  platform_office-logs platform_office-fonts >"$WORK/volumes-b"
rev_b=$(db_rev || true)
rev_moved() { [ -n "$rev_b" ] && [ "$rev_b" != 0006 ]; }
check "B: de migraties liepen (schema ${rev_b:-?}, na 0006)" rev_moved
check "B: notitie.txt en zijn versie zijn er nog" test "$(api sha "$note")/$(api count "/api/v1/files/$note/versions")" = "$note_v2/1"

# Wat /api/v1/office/status zei: eerst starting, en ok pas na de font-herstart van DS. Het einde
# daarvan staat in het log van supervisord in DS (runbook "DS bijwerken": de eerste "success:
# converter" na "stopped: converter"); daarna schrijft DS api.js opnieuw, en dat ziet de app
if wait "$watch_pid"; then
  ok "B: /api/v1/office/status wordt ok (binnen $DS_SECONDS s)"
else
  fout "B: /api/v1/office/status wordt ok (binnen $DS_SECONDS s)"
fi
watch_pid=""
status_log=$WORK/logs/b-status.txt
check "B: /api/v1/office/status zei eerst starting" test "$(head -n 1 "$status_log" | cut -d' ' -f2)" = starting
not_editing_before_ok() { awk '$2 != "ok" && $3 != "false" { bad = 1 } END { exit bad }' "$status_log"; }
check "B: zolang niet ok, kan er niet bewerkt worden (editing false)" not_editing_before_ok
check "B: bij ok kan er bewerkt worden (editing true)" test "$(tail -n 1 "$status_log" | cut -d' ' -f2-)" = "ok true"
docker exec "$office" cat /var/log/supervisor/supervisord.log >"$WORK/logs/b-supervisord.log" </dev/null 2>&1 || true
font_end=$(api font-end "$WORK/logs/b-supervisord.log" "$(docker exec "$office" date +%z </dev/null)" || true)
first_ok=$(awk '$2 == "ok" { print $1; exit }' "$status_log")
check "B: DS deed zijn font-herstart (supervisord: stopped, dan weer success: converter)" test -n "$font_end"
check "B: ok pas na het einde van de font-herstart" awk -v ok="$first_ok" -v end="$font_end" 'BEGIN { exit !(ok != "" && end != "" && ok >= end) }'
ds_start=$(epoch "$(docker inspect -f '{{.State.StartedAt}}' "$office")")
echo "  status: $(awk '{ print $2 }' "$status_log" | uniq -c | awk '{ printf "%s%s %s×", sep, $2, $1; sep = ", " }')"
awk -v s="$ds_start" -v e="${font_end:-0}" -v o="${first_ok:-0}" \
  'BEGIN { printf "  na de start van DS: font-herstart voorbij na %.1f s, status ok na %.1f s\n", e - s, o - s }'
# Een nieuw document van office (O4), om ook dat bij het terugrollen te volgen
fresh=$(api post /api/v1/office/new "{\"parent_id\": \"$werk\", \"kind\": \"docx\", \"name\": \"Nieuw van office\"}" id || true)
check "B: een nieuw document via office (Nieuw van office.docx)" test "$(api get "/api/v1/files/$fresh" name 2>/dev/null)" = "Nieuw van office.docx"
fresh_sha=$(api sha "$fresh" || true)

echo
echo "== B2: een losse docker compose up -d (zonder OFFICE_IMAGE, zoals na een herstart met de hand)"
if env -u OFFICE_IMAGE docker compose "${compose_args[@]}" up -d >"$WORK/logs/b2-compose.log" 2>&1; then
  ok "B2: compose up -d lukt"
else
  fout "B2: compose up -d lukt"
  tail -n 20 "$WORK/logs/b2-compose.log"
fi
check "B2: DS niet opnieuw gemaakt" test "$(cid office)" = "$office"
check "B2: de app niet opnieuw gemaakt" test "$(cid platform)" = "$app"
check "B2: niets opgehaald" bash -c "! grep -qiE 'pull' '$WORK/logs/b2-compose.log'"
check "B2: niets vervangen" bash -c "! grep -qiE 'recreat' '$WORK/logs/b2-compose.log'"

# --- S. docker compose stop met een open editor (§4.6 punt 3: de stopvolgorde) ---------------

echo
echo "== S: docker compose stop met de echte editor open, dan weer starten"
mkdir -p "$WORK/logs/editor"
versions_before=$(api count "/api/v1/files/$docx/versions" || echo -100)
(
  cd "$top/api" &&
    INSTALL_API_URL="http://127.0.0.1:$PORT" INSTALL_API_STATE="$WORK/sessie.json" \
      exec uv run -q --frozen --with playwright python "$top/e2e/install_editor.py" \
      "$WORK/logs/editor" "$werk" "$docx" verslag.docx
) </dev/null >"$WORK/logs/s-editor.log" 2>&1 &
editor_pid=$!
end=$((SECONDS + 300))
while [ ! -s "$WORK/logs/editor/getypt" ] && kill -0 "$editor_pid" 2>/dev/null && [ "$SECONDS" -lt "$end" ]; do
  sleep 1
done
word=$(cat "$WORK/logs/editor/getypt" 2>/dev/null || true)
check "S: de echte editor is open (inloggen met 2FA, Bewerken), met getypte tekst" test -n "$word"
if [ -z "$word" ]; then
  tail -n 20 "$WORK/logs/s-editor.log"
else
  # Een oud pid-bestand van supervisord dat bij de volgende start naar een levend proces wijst
  # (pid 1 leeft altijd). Zonder het entrypoint van compose.office.yaml kwam DS daarna zonder
  # supervisord op, zoals soms na een gewone herstart (metingen.md, deel 2, bevinding 7); zo
  # hangt die herstart hier niet meer af van welk pid er toevallig vrij is
  docker exec "$office" sh -c 'echo 1 >/var/run/supervisord.pid' </dev/null || true
  check "S: een oud pid-bestand van supervisord dat naar pid 1 wijst" \
    docker exec "$office" grep -qx 1 /var/run/supervisord.pid </dev/null
  # Zoals de README en het runbook: dc, met precies de bestanden van install.sh. Compose stopt DS
  # eerst (depends_on); DS slaat bij een nette stop alles op, de app draait dan nog
  t0=$(date +%s.%N)
  if docker compose "${compose_args[@]}" stop >"$WORK/logs/s-stop.log" 2>&1; then
    ok "S: docker compose stop lukt"
  else
    fout "S: docker compose stop lukt"
    tail -n 20 "$WORK/logs/s-stop.log"
  fi
  t1=$(date +%s.%N)
  awk -v a="$t0" -v b="$t1" 'BEGIN { printf "  docker compose stop duurde %.1f s\n", b - a }'
  check "S: binnen 90 s (stop_grace_period: DS 60 s, de app 30 s)" awk -v a="$t0" -v b="$t1" 'BEGIN { exit !(b - a <= 90) }'
  check "S: DS stopte netjes (exitcode 0, niet gekild)" test "$(docker inspect -f '{{.State.ExitCode}}' "$office")" = 0
  check "S: de app stopte zonder kill (exitcode niet 137)" test "$(docker inspect -f '{{.State.ExitCode}}' "$app")" != 137
  check "S: DS stopte vóór de app (depends_on)" finished_before "$office" "$app"
  # De browser dicht vóór de start: zo kan hij na de start niets meer opslaan
  : >"$WORK/logs/editor/sluit"
  rc=0
  wait "$editor_pid" || rc=$?
  editor_pid=""
  check "S: de browser sloot zonder fout" test "$rc" = 0
  # Weer starten met dezelfde containers, zoals na een herstart van de LXC
  if docker compose "${compose_args[@]}" start >"$WORK/logs/s-start.log" 2>&1; then
    ok "S: docker compose start lukt"
  else
    fout "S: docker compose start lukt"
    tail -n 20 "$WORK/logs/s-start.log"
  fi
  check "S: de app draait weer" app_comes_up
  check "S: dezelfde containers (niets opnieuw gemaakt)" test "$(cid office)/$(cid platform)" = "$office/$app"
  check "S: de getypte tekst staat in het bestand" text_has "$docx" "$word"
  check "S: als nieuwe versie (één versie meer dan vóór de sessie)" test "$(api count "/api/v1/files/$docx/versions")" = "$((versions_before + 1))"
  older=$(api get "/api/v1/files/$docx/versions" 0.id || true)
  check "S: die versie is het bestand van vóór de sessie" test "$(api sha "$docx" "$older")" = "$docx_sha"
fi
if api wait ok "$DS_SECONDS"; then
  ok "S: DS komt weer op en de app zegt weer ok"
  check "S: supervisord draait weer, ondanks het oude pid-bestand" \
    docker exec "$office" pgrep -x supervisord </dev/null
else
  fout "S: DS komt weer op en de app zegt weer ok"
  # Na een stop blijft /var/run/supervisord.pid van het vorige leven staan (metingen.md, deel 2,
  # punt 7); laat zien of dat het was
  docker exec "$office" sh -c 'echo "pid-bestand: $(cat /var/run/supervisord.pid 2>&1)"; pgrep -a supervisord || echo "geen supervisord"' </dev/null 2>&1 | sed 's/^/  /' || true
fi

# --- K. Een ander geheim aan één kant --------------------------------------------------------

echo
echo "== K: een ander OFFICE_JWT_SECRET voor alleen de app, dan terug"
# Zoals iemand die het geheim in .env met de hand verandert en alleen de app opnieuw start (de
# README zegt: niet doen). install.sh vervangt de app met compose up -d … platform; DS blijft
# dan staan, met het oude geheim
cp -p "$ROOT/.env" "$WORK/env-k"
sed -i "s|^OFFICE_JWT_SECRET=.*|OFFICE_JWT_SECRET=$(od -An -N32 -tx1 /dev/urandom | tr -d ' \n')|" "$ROOT/.env"
if docker compose "${compose_args[@]}" up -d --remove-orphans platform >"$WORK/logs/k-fout.log" 2>&1; then
  ok "K: de app opnieuw gemaakt met het andere geheim"
else
  fout "K: de app opnieuw gemaakt met het andere geheim"
  tail -n 20 "$WORK/logs/k-fout.log"
fi
check "K: DS niet opnieuw gemaakt (houdt het oude geheim)" test "$(cid office)" = "$office"
check "K: de app en DS hebben nu een ander geheim" secrets_differ "$(cid platform)" "$office"
check "K: de app draait" app_comes_up
check "K: /api/v1/office/status zegt misconfigured" api wait misconfigured 180
check "K: dan kan er niet bewerkt worden (editing false)" test "$(api get /api/v1/office/status editing)" = false
check "K: /api/v1/admin/office zegt misconfigured" test "$(api get /api/v1/admin/office state)" = misconfigured
check "K: /beheer meldt office_misconfigured" api notice office_misconfigured
mv "$WORK/env-k" "$ROOT/.env"
if docker compose "${compose_args[@]}" up -d --remove-orphans platform >"$WORK/logs/k-terug.log" 2>&1; then
  ok "K: terug: de app weer met het geheim uit .env"
else
  fout "K: terug: de app weer met het geheim uit .env"
  tail -n 20 "$WORK/logs/k-terug.log"
fi
check "K: .env weer precies zoals na B (rechten 600)" test "$(env_like_b && stat -c %a "$ROOT/.env")" = 600
check "K: de app en DS weer hetzelfde geheim" secrets_match "$(cid platform)" "$office"
check "K: de app draait" app_comes_up
check "K: /api/v1/office/status zegt weer ok" api wait ok "$DS_SECONDS"
check "K: geen melding office_misconfigured meer" no_notice office_misconfigured
app=$(cid platform)

# --- C. Nog een keer, zonder wijziging -------------------------------------------------------

step c-nogmaals PLATFORM_SRC_REF="$TO" || fout "C: install.sh gaf een fout"
check "C: de app draait" app_up
check "C: .env onveranderd (geheimen blijven)" test "$(sha256sum "$ROOT/.env" | cut -d' ' -f1)" = "$(cat "$WORK/env-b.sha")"
check "C: DS niet opnieuw gemaakt" test "$(cid office)" = "$office"

# --- H. Een geblokkeerde pull terwijl DS draait ----------------------------------------------

step h-geblokkeerd PLATFORM_SRC_REF="$TO" SHIM_DS=geblokkeerd || fout "H: install.sh gaf een fout"
check "H: de app draait" app_up
check "H: DS blijft draaien, dezelfde container" test "$(cid office)" = "$office"
check "H: DS draait" running "$office"
check "H: install.sh zegt dat office op de vorige versie blijft" said h-geblokkeerd "office blijft op de vorige versie"

# --- G. Een pin in .env ----------------------------------------------------------------------

echo "OFFICE_IMAGE=$DS_PIN" >>"$ROOT/.env"
step g-pin PLATFORM_SRC_REF="$TO" || fout "G: install.sh gaf een fout"
check "G: de pin staat er nog, één keer" test "$(grep -c "^OFFICE_IMAGE=$DS_PIN\$" "$ROOT/.env")" = 1
pinned=$(cid office)
check "G: DS draait met de pin" test "$(image_of "$pinned")" = "$DS_PIN"
check "G: DS draait" running "$pinned"
step g-pin-nogmaals PLATFORM_SRC_REF="$TO" || fout "G2: install.sh gaf een fout"
check "G2: de pin staat er nog na een tweede run" test "$(grep -c "^OFFICE_IMAGE=$DS_PIN\$" "$ROOT/.env")" = 1
check "G2: DS niet opnieuw gemaakt" test "$(cid office)" = "$pinned"
sed -i '/^OFFICE_IMAGE=/d' "$ROOT/.env"
step g-zonder-pin PLATFORM_SRC_REF="$TO" || fout "G3: install.sh gaf een fout"
check "G3: zonder pin weer de standaard" test "$(image_of "$(cid office)")" = "$DS_DEFAULT"

# --- D. OFFICE=uit ---------------------------------------------------------------------------

step d-uit PLATFORM_SRC_REF="$TO" OFFICE=uit || fout "D: install.sh gaf een fout"
check "D: de app draait" app_up
check "D: DS is weg" no_office
check "D: OFFICE=uit in .env" test "$(env_value OFFICE)" = uit
check "D: de app kent DS niet meer" test -z "$(container_env "$(cid platform)" PLATFORM_OFFICE_URL)"
check "D: de volumes van DS blijven" office_volumes
check "D: install.sh zegt waarom" said d-uit "Office: uit, want OFFICE=uit in $ROOT/.env"

# --- E. Weer aan, maar DS is nergens te halen ------------------------------------------------

step e-aan-geblokkeerd PLATFORM_SRC_REF="$TO" OFFICE=aan SHIM_DS=geblokkeerd || fout "E: install.sh gaf een fout"
check "E: de app draait" app_up
check "E: geen DS" no_office
check "E: OFFICE=aan in .env (de wens blijft)" test "$(env_value OFFICE)" = aan
check "E: Docker Hub en de mirror geprobeerd" in_log e-aan-geblokkeerd "GEBLOKKEERD pull mirror.gcr.io/$ds_repo@$digest"
check "E: install.sh zegt waarom, en dat het platform werkt" said e-aan-geblokkeerd "het platform werkt zonder editor"

# --- F. Weer aan -----------------------------------------------------------------------------

step f-aan PLATFORM_SRC_REF="$TO" OFFICE=aan || fout "F: install.sh gaf een fout"
check "F: de app draait" app_up
office=$(cid office)
check "F: DS is terug" running "$office"
check "F: DS op de standaard" test "$(image_of "$office")" = "$DS_DEFAULT"
docker volume inspect --format '{{.Name}} {{.CreatedAt}}' platform_office-lib platform_office-data \
  platform_office-logs platform_office-fonts >"$WORK/volumes-f"
check "F: dezelfde volumes als in B" cmp -s "$WORK/volumes-b" "$WORK/volumes-f"
check "F: de afscherming van DS zoals ontworpen" hardened "$office"
check "F: DS wordt weer gezond" wait_ds
check "F: de app praat weer met DS" ds_version

# --- R. Terug naar fase 3, zoals docs/runbooks/office.md ("De app terugrollen") ----------------

echo
echo "== R: terug naar $FROM (fase 3), zoals het runbook"
# 1. Op /beheer → Office: niemand bewerkt nog, en Niet opgeslagen is leeg. Die lijst komt van
#    DS, dus eerst wachten tot office werkt (F startte een nieuwe DS)
check "R1: office werkt (ok), dus de lijsten op /beheer kloppen" api wait ok "$DS_SECONDS"
check "R1: geen open documenten op /beheer → Office" test "$(api get /api/v1/admin/office open)" = "[]"
check "R1: Niet opgeslagen is leeg" test "$(api get /api/v1/admin/office unsaved)" = "[]"
# 2. Office uit met de huidige versie: DS stopt (en slaat op) terwijl de app nog draait
step r-office-uit PLATFORM_SRC_REF="$TO" OFFICE=uit || fout "R2: install.sh gaf een fout"
check "R2: DS is weg" no_office
check "R2: de app draait (nog deze versie, zonder office)" app_up
# 3. De app stoppen en het schema terug naar 0006 (dc is nu zonder office: er is geen DS meer)
if docker compose "${base_args[@]}" stop platform >"$WORK/logs/r3-stop.log" 2>&1; then
  ok "R3: dc stop platform"
else
  fout "R3: dc stop platform"
fi
if printf 'ja\n' | docker compose "${base_args[@]}" run --rm --no-deps -T platform \
  python -m app.cli db-downgrade 0006 >"$WORK/logs/r3-downgrade.log" 2>&1; then
  ok "R3: dc run --rm --no-deps platform python -m app.cli db-downgrade 0006 (met \"ja\")"
else
  fout "R3: dc run --rm --no-deps platform python -m app.cli db-downgrade 0006 (met \"ja\")"
  tail -n 20 "$WORK/logs/r3-downgrade.log"
fi
check "R3: het commando zegt dat het schema op 0006 staat" grep -qF "De database staat nu op revisie 0006." "$WORK/logs/r3-downgrade.log"
# 4. De oude versie installeren, met office uit
step r-fase3 PLATFORM_SRC_REF="$FROM" OFFICE=uit || fout "R4: install.sh gaf een fout"
check "R: de app draait op poort $PORT" app_up
check "R: de broncode staat op $FROM" test "$(git -C "$ROOT/src" rev-parse HEAD)" = "$(git -C "$WORK/repo.git" rev-parse "$FROM^{commit}")"
check "R: het schema staat op 0006" test "$(db_rev)" = 0006
check "R: install.sh zegt dat deze versie geen office heeft" said r-fase3 "Office: uit, want deze versie van het platform heeft nog geen office"
check "R: geen container van DS" no_office
check "R: de app kent DS niet" test -z "$(container_env "$(cid platform)" PLATFORM_OFFICE_URL)"
check "R: de volumes van DS blijven" office_volumes
check "R: OFFICE=uit in .env, de geheimen blijven" test "$(env_value OFFICE)/$(is_hex64 "$(env_value OFFICE_JWT_SECRET)" && echo hex)" = uit/hex
check "R: zonder code geen login (2FA blijft verplicht)" api login-zonder-code
check "R: de beheerder logt in met wachtwoord en 2FA-code" api login
check "R: notitie.txt van vóór de update, met zijn versie" test "$(api sha "$note")/$(api count "/api/v1/files/$note/versions")" = "$note_v2/1"
older=$(api get "/api/v1/files/$note/versions" 0.id || true)
check "R: die versie is de eerste upload" test "$(api sha "$note" "$older")" = "$note_v1"
check "R: verslag.docx heeft de tekst uit de editor (S)" text_has "$docx" "${word:-?}"
check "R: met de versie van vóór office" test "$(api count "/api/v1/files/$docx/versions")" = 1
older=$(api get "/api/v1/files/$docx/versions" 0.id || true)
check "R: die versie is het bestand van vóór de sessie" test "$(api sha "$docx" "$older")" = "$docx_sha"
check "R: het nieuwe document van office is er nog" test "$(api get "/api/v1/files/$fresh" name)/$(api sha "$fresh")" = "Nieuw van office.docx/$fresh_sha"

# --- V. Weer vooruit naar deze versie ----------------------------------------------------------

step v-vooruit PLATFORM_SRC_REF="$TO" OFFICE=aan || fout "V: install.sh gaf een fout"
check "V: de app draait" app_up
check "V: de broncode staat op ${TO:0:12}" test "$(git -C "$ROOT/src" rev-parse HEAD)" = "$(git -C "$WORK/repo.git" rev-parse "$TO^{commit}")"
check "V: de migraties liepen (schema weer op ${rev_b:-?})" test "$(db_rev)" = "$rev_b"
check "V: .env weer precies zoals na B (dezelfde geheimen)" env_like_b
office=$(cid office)
check "V: DS is terug" running "$office"
check "V: DS op de standaard" test "$(image_of "$office")" = "$DS_DEFAULT"
docker volume inspect --format '{{.Name}} {{.CreatedAt}}' platform_office-lib platform_office-data \
  platform_office-logs platform_office-fonts >"$WORK/volumes-v"
check "V: dezelfde volumes als in B" cmp -s "$WORK/volumes-b" "$WORK/volumes-v"
check "V: de afscherming van DS zoals ontworpen" hardened "$office"
check "V: DS wordt gezond" wait_ds
check "V: de app praat met DS" ds_version
check "V: de beheerder logt in met wachtwoord en 2FA-code" api login
check "V: /api/v1/office/status zegt ok" api wait ok "$DS_SECONDS"
check "V: notitie.txt en verslag.docx met hun versies" test "$(api sha "$note")/$(api count "/api/v1/files/$note/versions")/$(api count "/api/v1/files/$docx/versions")" = "$note_v2/1/1"
check "V: verslag.docx heeft nog de tekst uit de editor" text_has "$docx" "${word:-?}"
check "V: verslag.docx kan weer in de editor" test "$(api get "/api/v1/office/files/$docx" can_edit)" = true

echo
echo "$oks controles goed, $fails mislukt."
if [ "$fails" -gt 0 ]; then
  echo "$fails controle(s) mislukt."
  exit 1
fi
echo "Alle controles geslaagd."
