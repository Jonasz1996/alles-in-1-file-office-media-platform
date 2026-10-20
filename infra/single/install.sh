#!/usr/bin/env bash
# Installeert het platform op één Debian- of Ubuntu-container: één Docker-container
# met API en web-app, data in /tank/platform. Draai als root in de console:
#
#   apt update && apt install -y curl
#   curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/main/infra/single/install.sh | bash
#
# Opnieuw draaien werkt het platform bij; instellingen en data blijven staan.
# Andere poort dan 80: PORT=8080 bash install.sh (later aan te passen in /opt/platform/.env)
set -euo pipefail

REPO=https://github.com/Jonasz1996/alles-in-1-file-office-media-platform.git
BRANCH=${PLATFORM_BRANCH:-main}
PLATFORM_ROOT=/opt/platform
SRC=$PLATFORM_ROOT/src
ENV_FILE=$PLATFORM_ROOT/.env

stap() { printf '\n== %s\n' "$1"; }

[ "$(id -u)" = 0 ] || { echo "Draai dit als root."; exit 1; }
# shellcheck source=/dev/null
. /etc/os-release
case "$ID" in debian | ubuntu) ;; *) echo "Alleen Debian en Ubuntu worden ondersteund (gevonden: $ID)."; exit 1 ;; esac

stap "Pakketten"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q ca-certificates curl git unattended-upgrades >/dev/null

stap "Docker"
if ! command -v docker >/dev/null; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID $VERSION_CODENAME stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -q
  apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null
fi
mkdir -p /etc/docker
[ -f /etc/docker/daemon.json ] || cat > /etc/docker/daemon.json <<'JSON'
{ "log-driver": "local", "log-opts": { "max-size": "20m", "max-file": "5" }, "live-restore": true }
JSON
systemctl enable --now docker >/dev/null
systemctl restart docker
if ! docker run --rm hello-world >/dev/null 2>&1; then
  echo "Docker kan geen containers starten. Zet in Proxmox bij deze container"
  echo "Options -> Features 'nesting' en 'keyctl' aan, herstart de container en draai dit opnieuw."
  exit 1
fi
echo "Docker $(docker version --format '{{.Server.Version}}'), opslagdriver $(docker info --format '{{.Driver}}')"

stap "Broncode ($BRANCH)"
if [ -d "$SRC/.git" ]; then
  git -C "$SRC" fetch -q origin "$BRANCH"
  git -C "$SRC" checkout -q -B "$BRANCH" "origin/$BRANCH"
else
  git clone -q --branch "$BRANCH" "$REPO" "$SRC"
fi
echo "Versie $(git -C "$SRC" log -1 --format='%h %s')"

stap "Instellingen"
mkdir -p "$PLATFORM_ROOT"
if [ -f "$ENV_FILE" ] && grep -q '^POSTGRES_PASSWORD=' "$ENV_FILE"; then
  # Instellingen van de eerste versie (met Traefik, PostgreSQL en SeaweedFS)
  mv "$ENV_FILE" "$ENV_FILE.oud"
  echo "Oude instellingen bewaard als $ENV_FILE.oud."
fi
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  echo "Bestaande instellingen gebruikt ($ENV_FILE)."
else
  if mountpoint -q /tank; then DATA_ROOT=/tank/platform; else
    DATA_ROOT=$PLATFORM_ROOT/data/platform
    echo "Let op: /tank is niet gekoppeld; data komt in $DATA_ROOT op de rootdisk."
  fi
  cat > "$ENV_FILE" <<ENV
DATA_ROOT=$DATA_ROOT
NODE_NAME=$(hostname)
PORT=${PORT:-80}
SECURE_COOKIES=false
ENV
  # shellcheck disable=SC1090
  . "$ENV_FILE"
fi
# De app draait in de container als gebruiker 10001
install -d -m 750 "$DATA_ROOT"
chown 10001:10001 "$DATA_ROOT"

stap "Platform bouwen en starten (de eerste keer duurt dit een paar minuten)"
compose=(docker compose --project-directory "$SRC/infra/single" -f "$SRC/infra/single/compose.yaml" --env-file "$ENV_FILE")
"${compose[@]}" up -d --build --remove-orphans
docker image prune -f >/dev/null

for _ in $(seq 1 30); do
  if curl -fs -o /dev/null "http://127.0.0.1:$PORT/health"; then break; fi
  sleep 2
done
if ! curl -fs -o /dev/null "http://127.0.0.1:$PORT/health"; then
  echo "Het platform reageert niet. Laatste logregels:"
  "${compose[@]}" logs --tail 50 platform
  exit 1
fi

ip=$(hostname -I | awk '{print $1}')
url="http://$ip"
[ "$PORT" = 80 ] || url="$url:$PORT"
cat <<KLAAR

Klaar. Het platform draait op $url

Open dat adres in je browser. Bij het eerste bezoek maak je het beheerdersaccount
aan; doe dat voordat je de poort in je router openzet.

Instellingen: $ENV_FILE
Data:         $DATA_ROOT
Bijwerken:    draai dit script opnieuw.
KLAAR
for oud in postgres seaweedfs traefik; do
  if [ -d "/tank/$oud" ] || [ -d "$PLATFORM_ROOT/data/$oud" ]; then
    echo "Map '$oud' van de eerste versie wordt niet meer gebruikt; die mag weg."
  fi
done
