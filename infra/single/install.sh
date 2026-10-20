#!/usr/bin/env bash
# Installeert de eerste versie van het platform op één Debian- of Ubuntu-container.
# Draai als root in de console van de container:
#
#   apt update && apt install -y curl
#   curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/main/infra/single/install.sh | bash
#
# Opnieuw draaien werkt het platform bij; bestaande geheimen en data blijven staan.
# Zonder vragen: DOMAIN=app.voorbeeld.nl ACME_EMAIL=jij@voorbeeld.nl bash install.sh
set -euo pipefail

REPO=https://github.com/Jonasz1996/alles-in-1-file-office-media-platform.git
BRANCH=${PLATFORM_BRANCH:-main}
PLATFORM_ROOT=/opt/platform
SRC=$PLATFORM_ROOT/src
ENV_FILE=$PLATFORM_ROOT/.env

stap() { printf '\n== %s\n' "$1"; }
vraag() { # vraag <variabele> <tekst>
  local var=$1 tekst=$2
  if [ -z "${!var:-}" ] && [ -r /dev/tty ]; then
    read -r -p "$tekst" "${var?}" < /dev/tty || true
  fi
}

[ "$(id -u)" = 0 ] || { echo "Draai dit als root."; exit 1; }
# shellcheck source=/dev/null
. /etc/os-release
case "$ID" in debian | ubuntu) ;; *) echo "Alleen Debian en Ubuntu worden ondersteund (gevonden: $ID)."; exit 1 ;; esac

stap "Pakketten"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q ca-certificates curl git openssl unattended-upgrades >/dev/null

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
if mountpoint -q /tank; then DATA_ROOT=/tank; else
  DATA_ROOT=$PLATFORM_ROOT/data
  echo "Let op: /tank is niet gekoppeld; data komt in $DATA_ROOT op de rootdisk."
fi
if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  echo "Bestaande instellingen gebruikt ($ENV_FILE)."
else
  vraag DOMAIN "Domeinnaam voor het platform (bijv. app.voorbeeld.nl, leeg = alleen http in je LAN): "
  if [ -n "${DOMAIN:-}" ]; then vraag ACME_EMAIL "E-mailadres voor Let's Encrypt: "; fi
  umask 077
  cat > "$ENV_FILE" <<ENV
DOMAIN=${DOMAIN:-}
ACME_EMAIL=${ACME_EMAIL:-}
ACME_STAGING=${ACME_STAGING:-false}
DATA_ROOT=$DATA_ROOT
PLATFORM_ROOT=$PLATFORM_ROOT
NODE_NAME=$(hostname)
POSTGRES_PASSWORD=$(openssl rand -hex 24)
S3_ACCESS_KEY=$(openssl rand -hex 16)
S3_SECRET_KEY=$(openssl rand -hex 32)
ADMIN_TOKEN=$(openssl rand -hex 24)
ENV
  # shellcheck disable=SC1090
  . "$ENV_FILE"
fi

stap "Configuratie"
mkdir -p "$DATA_ROOT"/{postgres,seaweedfs,traefik} "$PLATFORM_ROOT"/{seaweedfs,traefik/dynamic}
chmod 700 "$DATA_ROOT/traefik"
cat > "$PLATFORM_ROOT/seaweedfs/s3.json" <<JSON
{"identities": [{"name": "platform",
  "credentials": [{"accessKey": "$S3_ACCESS_KEY", "secretKey": "$S3_SECRET_KEY"}],
  "actions": ["Admin", "Read", "Write", "List", "Tagging"]}]}
JSON

if [ -n "${DOMAIN:-}" ]; then
  entry=websecure
  rule="Host(\`$DOMAIN\`)"
  ca=""
  [ "${ACME_STAGING:-false}" = true ] && ca="      caServer: https://acme-staging-v02.api.letsencrypt.org/directory"
  cat > "$PLATFORM_ROOT/traefik/traefik.yml" <<YML
entryPoints:
  web:
    address: ":80"
    http:
      redirections:
        entryPoint: { to: websecure, scheme: https }
  websecure:
    address: ":443"
    http3: {}
    http:
      tls: { certResolver: le }
  intern:
    address: "127.0.0.1:8082"
ping: { entryPoint: intern }
providers:
  file: { directory: /etc/traefik/dynamic, watch: true }
certificatesResolvers:
  le:
    acme:
      email: ${ACME_EMAIL:-}
      storage: /data/acme.json
$ca
      httpChallenge: { entryPoint: web }
accessLog: {}
YML
else
  entry=web
  rule="PathPrefix(\`/\`)"
  cat > "$PLATFORM_ROOT/traefik/traefik.yml" <<YML
entryPoints:
  web:
    address: ":80"
  intern:
    address: "127.0.0.1:8082"
ping: { entryPoint: intern }
providers:
  file: { directory: /etc/traefik/dynamic, watch: true }
accessLog: {}
YML
fi

cat > "$PLATFORM_ROOT/traefik/dynamic/routes.yml" <<YML
http:
  routers:
    api:
      rule: "$rule && PathPrefix(\`/api\`)"
      entryPoints: [$entry]
      service: api
      middlewares: [security-headers, rate-limit]
    web:
      rule: "$rule"
      entryPoints: [$entry]
      service: web
      middlewares: [security-headers, rate-limit]
  services:
    api:
      loadBalancer:
        servers: [{ url: "http://127.0.0.1:8000" }]
    web:
      loadBalancer:
        servers: [{ url: "http://127.0.0.1:8080" }]
  middlewares:
    security-headers:
      headers:
        stsSeconds: 31536000
        contentTypeNosniff: true
        frameDeny: true
        referrerPolicy: strict-origin-when-cross-origin
    rate-limit:
      rateLimit: { average: 50, burst: 100 }
YML

stap "Platform bouwen en starten (de eerste keer duurt dit een paar minuten)"
compose=(docker compose --project-directory "$SRC/infra/single" -f "$SRC/infra/single/compose.yaml" --env-file "$ENV_FILE")
"${compose[@]}" up -d --build --remove-orphans

stap "S3-buckets"
for _ in $(seq 1 30); do
  if curl -fs -o /dev/null http://127.0.0.1:9333/cluster/status; then break; fi
  sleep 2
done
bestaand=$(echo "s3.bucket.list" | "${compose[@]}" exec -T seaweedfs weed shell -master=127.0.0.1:9333 2>/dev/null || true)
for b in vault media thumbs cache backups; do
  if ! grep -qw "$b" <<<"$bestaand"; then
    echo "s3.bucket.create -name $b" | "${compose[@]}" exec -T seaweedfs weed shell -master=127.0.0.1:9333 >/dev/null
    echo "aangemaakt: $b"
  fi
done

ip=$(hostname -I | awk '{print $1}')
if [ -n "${DOMAIN:-}" ]; then url="https://$DOMAIN"; else url="http://$ip"; fi
cat <<KLAAR

Klaar. Het platform draait op $url
Beheer: $url/beheer
Beheertoken: $ADMIN_TOKEN
(staat ook in $ENV_FILE; bewaar het goed)

Bijwerken: draai dit script opnieuw.
KLAAR
