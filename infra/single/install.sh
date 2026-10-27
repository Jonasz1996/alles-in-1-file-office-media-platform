#!/usr/bin/env bash
# Installeert het platform op één Debian- of Ubuntu-container: één Docker-container
# met API en web-app, data in /tank/platform, en (fase 4) OnlyOffice Docs als tweede
# container voor het bewerken van documenten. Draai als root in de console:
#
#   apt update && apt install -y curl
#   curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/main/infra/single/install.sh | bash
#
# Opnieuw draaien werkt het platform bij; instellingen en data blijven staan.
# Andere poort dan 80: PORT=8080 bash install.sh (later aan te passen in /opt/platform/.env)
# Office uit: OFFICE=uit bash install.sh (of: curl … | OFFICE=uit bash). Weer aan: OFFICE=aan.
#
# Voor tests (Jonas heeft ze niet nodig): PLATFORM_REPO (ook een lokaal pad of file://),
# PLATFORM_SRC_REF (een commit of tag in plaats van de branch) en PLATFORM_ROOT (in plaats van
# /opt/platform). Met INSTALL_LIB=1 laadt een test alleen de functies, zonder iets te doen.
set -euo pipefail

REPO=${PLATFORM_REPO:-https://github.com/Jonasz1996/alles-in-1-file-office-media-platform.git}
BRANCH=${PLATFORM_BRANCH:-main}
SRC_REF=${PLATFORM_SRC_REF:-}
PLATFORM_ROOT=${PLATFORM_ROOT:-/opt/platform}
SRC=$PLATFORM_ROOT/src
ENV_FILE=$PLATFORM_ROOT/.env

# OnlyOffice Docs Community 10.0.0.1, vast op digest (Keuze 1). Dezelfde digest op Docker Hub
# en mirror.gcr.io.
OFFICE_REPO=onlyoffice/documentserver
OFFICE_TAG=10.0.0.1
OFFICE_DIGEST=sha256:ca17889f10b11141cc6441836aebdd2835cf3e1b49681b2fd6e155c94b7850b3
# Oud en nieuw image van DS even naast elkaar (±4 GB elk)
OFFICE_MIN_FREE_GB=8
# Het project heet "platform" (compose.yaml)
PROJECT=platform

stap() { printf '\n== %s\n' "$1"; }

# 64 hex-tekens uit /dev/urandom: veilig om .env als shell te lezen (Keuze 10)
hex() { od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; }

set_key() { sed -i "s|^$1=.*|$1=$2|" "$ENV_FILE"; }

# Een ontbrekende sleutel toevoegen; een lege waarde (zoals in .env.example) vullen. Een
# bestaande waarde blijft altijd staan. Eindigt .env niet op een regeleinde (met de hand
# bewerkt), dan eerst dat, anders plakt de sleutel aan de laatste instelling vast.
add_key() {
  if grep -q "^$1=." "$ENV_FILE"; then
    return 0
  elif grep -q "^$1=" "$ENV_FILE"; then
    set_key "$1" "$2"
  else
    [ ! -s "$ENV_FILE" ] || [ -z "$(tail -c 1 "$ENV_FILE")" ] || printf '\n' >>"$ENV_FILE"
    printf '%s=%s\n' "$1" "$2" >>"$ENV_FILE"
  fi
}

# De eerste regel van main, vóór er een .env gelezen wordt (M13): een OFFICE uit de omgeving
# wint van .env. Alleen aan of uit.
want_office() {
  case "${OFFICE-}" in
    "" | aan | uit) printf '%s' "${OFFICE-}" ;;
    *)
      echo "OFFICE moet aan of uit zijn (nu: $OFFICE)." >&2
      return 1
      ;;
  esac
}

# Na het maken of lezen van .env: de sleutels van office erbij, nooit overschreven. Een
# gevraagde OFFICE vervangt daarna de waarde.
merge_env() {
  add_key OFFICE aan
  add_key OFFICE_JWT_SECRET "$(hex)"
  add_key OFFICE_LINK_SECRET "$(hex)"
  [ -z "$1" ] || set_key OFFICE "$1"
  chmod 600 "$ENV_FILE"
}

# Heeft de broncode die nu klaarstaat office? (Een oudere versie, bijvoorbeeld bij terugrollen,
# heeft het nog niet.)
office_supported() { [ -f "$SRC/infra/single/compose.office.yaml" ]; }

# overlay2, of overlayfs bij Docker 29 met de opslag van containerd (M8). Bij vfs wordt het image
# vele malen groter.
driver_ok() {
  case "$1" in
    overlay2 | overlayfs) return 0 ;;
    *) return 1 ;;
  esac
}

free_gb() {
  local dir
  dir=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
  [ -n "$dir" ] && [ -d "$dir" ] || dir=/
  df --output=avail -B1G "$dir" | tail -n 1 | tr -d ' '
}

# Is het image dat office_image zou nemen er al? Dan hoeft er niets opgehaald te worden.
office_image_local() {
  if [ -n "${OFFICE_IMAGE:-}" ]; then
    docker image inspect "$OFFICE_IMAGE" >/dev/null 2>&1
  else
    docker image inspect "$OFFICE_REPO@$OFFICE_DIGEST" >/dev/null 2>&1 ||
      docker image inspect "mirror.gcr.io/$OFFICE_REPO@$OFFICE_DIGEST" >/dev/null 2>&1
  fi
}

# Vóór het ophalen van het image: de opslagdriver en de ruimte. De ruimte alleen als er een
# image opgehaald moet worden (uitrol-6). Niet goed: uitleg op stdout, en office blijft zoals
# het was (Keuze 30).
office_preflight() {
  local driver free
  driver=$(docker info --format '{{.Driver}}' 2>/dev/null || true)
  if ! driver_ok "$driver"; then
    echo "De opslagdriver van Docker is '$driver'; office vraagt overlay2 of overlayfs. Met"
    echo "'$driver' wordt het image van DS vele malen groter. Zet in Proxmox bij deze container"
    echo "'nesting' aan en gebruik een rootdisk op ZFS of ext4."
    return 1
  fi
  if office_image_local; then return 0; fi
  free=$(free_gb)
  if [ "${free:-0}" -lt "$OFFICE_MIN_FREE_GB" ]; then
    echo "Er is maar $free GB vrij voor Docker; office vraagt er $OFFICE_MIN_FREE_GB (het oude"
    echo "en het nieuwe image van DS staan even naast elkaar). Maak ruimte en draai dit opnieuw."
    return 1
  fi
}

# Het image: een pin in .env gaat voor. Anders op digest, eerst Docker Hub, dan de mirror,
# en daarna altijd de tag van Docker Hub erop (M19), op precies die digest. Compose krijgt de
# tag: op de klassieke image-opslag (overlay2) is er na een pull van de mirror geen naam
# `tag@digest` onder Docker Hub, en dan zou compose het image van Docker Hub halen (uitrol-1).
office_image() {
  local hub="$OFFICE_REPO@$OFFICE_DIGEST" mirror="mirror.gcr.io/$OFFICE_REPO@$OFFICE_DIGEST" img
  if [ -n "${OFFICE_IMAGE:-}" ]; then
    docker image inspect "$OFFICE_IMAGE" >/dev/null 2>&1 || docker pull -q "$OFFICE_IMAGE" >/dev/null 2>&1 || return 1
    printf '%s' "$OFFICE_IMAGE"
    return 0
  fi
  for _ in 1 2 3; do
    for img in "$hub" "$mirror"; do
      if docker image inspect "$img" >/dev/null 2>&1 || docker pull -q "$img" >/dev/null 2>&1; then
        docker tag "$img" "$OFFICE_REPO:$OFFICE_TAG"
        printf '%s' "$OFFICE_REPO:$OFFICE_TAG"
        return 0
      fi
    done
    sleep 5
  done
  return 1
}

# De container van DS (draaiend of niet), of niets
office_container() {
  docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" \
    --filter label=com.docker.compose.service=office | head -n 1
}

# Het image van de draaiende DS, of niets
office_running_image() {
  local id
  id=$(office_container)
  [ -n "$id" ] || return 1
  [ "$(docker inspect --format '{{.State.Running}}' "$id" 2>/dev/null)" = true ] || return 1
  docker inspect --format '{{.Config.Image}}' "$id"
}

# Kan de kernel geheugen- en cpu-grenzen aan? Anders start de container niet in een LXC zonder
# die cgroup-controllers (Keuze 29).
limits_supported() {
  [ "$(docker info --format '{{.MemoryLimit}} {{.CPUCfsQuota}}' 2>/dev/null)" = "true true" ]
}

# Beslist over office voor deze run. Zet OFFICE_ON (1 of 0), OFFICE_IMG (het image) en
# OFFICE_REASON (waarom uit of waarom de vorige). Breekt nooit af: alles staat in een if (M13).
decide_office() {
  local running
  OFFICE_ON=0 OFFICE_IMG="" OFFICE_REASON=""
  running=$(office_running_image || true)
  if ! office_supported; then
    OFFICE_REASON="deze versie van het platform heeft nog geen office"
  elif [ "${OFFICE:-aan}" = uit ]; then
    OFFICE_REASON="OFFICE=uit in $ENV_FILE"
  elif [ "${OFFICE:-aan}" != aan ]; then
    OFFICE_REASON="OFFICE in $ENV_FILE moet aan of uit zijn (nu: $OFFICE)"
    [ -z "$running" ] || { OFFICE_ON=1 OFFICE_IMG=$running; OFFICE_REASON="$OFFICE_REASON; office blijft zoals het was"; }
  elif ! office_preflight; then
    OFFICE_REASON="de controle van Docker gaf een probleem (zie boven)"
    [ -z "$running" ] || { OFFICE_ON=1 OFFICE_IMG=$running; OFFICE_REASON="$OFFICE_REASON; office blijft op de vorige versie"; }
  elif OFFICE_IMG=$(office_image); then
    OFFICE_ON=1
  elif [ -n "$running" ]; then
    OFFICE_ON=1 OFFICE_IMG=$running
    OFFICE_REASON="het image van DS was niet op te halen; office blijft op de vorige versie"
  else
    OFFICE_IMG=""
    OFFICE_REASON="het image van DS was niet op te halen (Docker Hub en mirror.gcr.io); het platform werkt zonder editor, draai het script later opnieuw"
  fi
}

# De compose-bestanden voor deze run (zet de array compose)
compose_files() {
  local dir=$SRC/infra/single
  compose=(docker compose --project-directory "$dir" --env-file "$ENV_FILE" -f "$dir/compose.yaml")
  if [ "$1" = 1 ]; then
    compose+=(-f "$dir/compose.office.yaml")
    if limits_supported; then compose+=(-f "$dir/compose.office-limits.yaml"); fi
  fi
}

# Het volledige id van een image, of niets
image_id() { docker image inspect --format '{{.Id}}' "$1" 2>/dev/null || true; }

# Na de app: DS starten of bijwerken, en daarna de oude images weg. Compose vervangt DS alleen
# als zijn image of instellingen veranderden. Een andere inhoud van het gekoppelde
# local-production-linux.json ziet compose niet; daarom houdt het script de sha256 ervan bij
# en maakt het DS dan opnieuw. Die sha256 komt pas na een gelukte start in het bestand, anders
# zou de volgende run DS met de oude instelling laten draaien (uitrol-4). Breekt nooit af
# (Keuze 30): de app draait al. Lukt het niet, dan zeggen OFFICE_ON en OFFICE_REASON het voor
# de eindtekst.
office_start() {
  local container=$1 previous=$2 pin=$3 state=$PLATFORM_ROOT/office-config.sha256 now started=0
  now=$(sha256sum "$SRC/infra/single/office/local-production-linux.json" | cut -d' ' -f1)
  if [ -n "$container" ] && [ "$now" != "$(cat "$state" 2>/dev/null || true)" ]; then
    if OFFICE_IMAGE=$OFFICE_IMG "${compose[@]}" up -d --no-deps --force-recreate office; then started=1; fi
  elif OFFICE_IMAGE=$OFFICE_IMG "${compose[@]}" up -d --no-deps office; then
    started=1
  fi
  if [ "$started" = 1 ]; then
    printf '%s\n' "$now" >"$state"
    office_cleanup "$(image_id "$OFFICE_IMG")" "$previous" "$pin"
    return 0
  fi
  if [ -n "$(office_running_image || true)" ]; then
    OFFICE_REASON="DS kon niet opnieuw starten (de melding van Docker staat hierboven); de vorige DS draait door, draai het script later opnieuw"
  else
    OFFICE_ON=0
    OFFICE_REASON="DS kon niet starten (de melding van Docker staat hierboven); het platform werkt zonder editor, draai het script later opnieuw"
  fi
  echo "Let op: $OFFICE_REASON."
}

# Oude images van DS weg die geen container meer gebruikt, behalve het huidige, het vorige
# (voor terugrollen) en de pin (Keuze 30). Per naam, zodat een image met nog een andere naam
# blijft staan.
office_cleanup() {
  local current=$1 previous=$2 pin=$3 used="" c id repo tag digest ref
  for c in $(docker ps -aq); do
    used="$used$(docker inspect --format '{{.Image}}' "$c" 2>/dev/null || true)
"
  done
  docker images --no-trunc --digests --format '{{.ID}} {{.Repository}} {{.Tag}} {{.Digest}}' \
    --filter "reference=$OFFICE_REPO" --filter "reference=mirror.gcr.io/$OFFICE_REPO" \
    | while read -r id repo tag digest; do
      case " $current $previous $pin " in *" $id "*) continue ;; esac
      if printf '%s\n' "$used" | grep -qxF "$id"; then continue; fi
      if [ "$tag" = "<none>" ]; then ref="$repo@$digest"; else ref="$repo:$tag"; fi
      if docker image rm "$ref" >/dev/null 2>&1; then echo "Oud image van DS weg: $ref"; fi
    done
}

check_host() {
  [ "$(id -u)" = 0 ] || { echo "Draai dit als root."; exit 1; }
  if [ -d /etc/pve ] || command -v pveversion >/dev/null; then
    echo "Dit is de Proxmox-host zelf. Draai het script in de Console van de container"
    echo "(in Proxmox: container aanklikken, dan Console), niet in de Shell van de node."
    exit 1
  fi
  # shellcheck source=/dev/null
  . /etc/os-release
  case "$ID" in debian | ubuntu) ;; *) echo "Alleen Debian en Ubuntu worden ondersteund (gevonden: $ID)."; exit 1 ;; esac
}

pakketten() {
  local nodig=(ca-certificates curl git unattended-upgrades)
  # Al geïnstalleerd: niets te doen (unattended-upgrades houdt ze bij)
  if dpkg -s "${nodig[@]}" >/dev/null 2>&1; then
    echo "Al geïnstalleerd: ${nodig[*]}"
    return 0
  fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -q
  apt-get install -y -q "${nodig[@]}" >/dev/null
}

docker_setup() {
  local nieuw_config=0 img=""
  if ! command -v docker >/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL "https://download.docker.com/linux/$ID/gpg" -o /etc/apt/keyrings/docker.asc
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/$ID $VERSION_CODENAME stable" \
      >/etc/apt/sources.list.d/docker.list
    apt-get update -q
    apt-get install -y -q docker-ce docker-ce-cli containerd.io docker-compose-plugin >/dev/null
  fi
  mkdir -p /etc/docker
  if [ ! -f /etc/docker/daemon.json ]; then
    cat >/etc/docker/daemon.json <<'JSON'
{ "log-driver": "local", "log-opts": { "max-size": "20m", "max-file": "5" }, "live-restore": true }
JSON
    nieuw_config=1
  fi
  # Alleen starten als Docker niet draait, en alleen herstarten als daemon.json net geschreven
  # is: een herstart zonder live-restore stopt DS vóór het tussentijds opslaan (F3)
  if ! docker info >/dev/null 2>&1; then systemctl enable --now docker >/dev/null; fi
  if [ "$nieuw_config" = 1 ] && [ -d /run/systemd/system ]; then systemctl restart docker; fi
  # hello-world eerst van de mirror (Docker Hub geeft soms 429)
  for img in mirror.gcr.io/library/hello-world hello-world; do
    if docker image inspect "$img" >/dev/null 2>&1 || docker pull -q "$img" >/dev/null 2>&1; then break; fi
    img=""
  done
  if [ -z "$img" ]; then
    echo "Docker kan geen image ophalen, ook hello-world niet (van mirror.gcr.io en Docker Hub)."
    echo "Controleer het netwerk van deze container (DNS, internet). Geeft Docker Hub 429"
    echo "(te veel verzoeken), wacht dan even en draai dit opnieuw."
    exit 1
  fi
  if ! docker run --rm "$img" </dev/null >/dev/null 2>&1; then
    echo "Docker kan geen containers starten. Zet in Proxmox bij deze container"
    echo "Options -> Features 'nesting' en 'keyctl' aan, herstart de container en draai dit opnieuw."
    exit 1
  fi
  echo "Docker $(docker version --format '{{.Server.Version}}'), opslagdriver $(docker info --format '{{.Driver}}')"
}

broncode() {
  if [ -d "$SRC/.git" ]; then
    [ "$(git -C "$SRC" remote get-url origin)" = "$REPO" ] || git -C "$SRC" remote set-url origin "$REPO"
    if [ -n "$SRC_REF" ]; then
      git -C "$SRC" cat-file -e "$SRC_REF^{commit}" 2>/dev/null || git -C "$SRC" fetch -q --tags origin
      git -C "$SRC" checkout -q --detach "$SRC_REF"
    else
      git -C "$SRC" fetch -q origin "$BRANCH"
      git -C "$SRC" checkout -q -B "$BRANCH" "origin/$BRANCH"
    fi
  elif [ -n "$SRC_REF" ]; then
    git clone -q "$REPO" "$SRC"
    git -C "$SRC" checkout -q --detach "$SRC_REF"
  else
    git clone -q --branch "$BRANCH" "$REPO" "$SRC"
  fi
  echo "Versie $(git -C "$SRC" log -1 --format='%h %s')"
}

instellingen() {
  local want=$1
  mkdir -p "$PLATFORM_ROOT"
  if [ -f "$ENV_FILE" ] && grep -q '^POSTGRES_PASSWORD=' "$ENV_FILE"; then
    # Instellingen van de eerste versie (met Traefik, PostgreSQL en SeaweedFS)
    mv "$ENV_FILE" "$ENV_FILE.oud"
    echo "Oude instellingen bewaard als $ENV_FILE.oud."
  fi
  if [ -f "$ENV_FILE" ]; then
    echo "Bestaande instellingen gebruikt ($ENV_FILE)."
  else
    local datamap
    if mountpoint -q /tank; then datamap=/tank/platform; else
      datamap=$PLATFORM_ROOT/data/platform
      echo "Let op: /tank is niet gekoppeld; data komt in $datamap op de rootdisk."
    fi
    (umask 077 && cat >"$ENV_FILE" <<ENV
DATA_ROOT=$datamap
NODE_NAME=$(hostname)
PORT=${PORT:-80}
HTTPS_PORT=${HTTPS_PORT:-443}
SECURE_COOKIES=false
ENV
    )
  fi
  if office_supported; then
    merge_env "$want"
  elif [ -n "$want" ]; then
    # Een versie zonder office (terugrollen): de wens bewaren, verder niets
    add_key OFFICE "$want"
    set_key OFFICE "$want"
  fi
  chmod 600 "$ENV_FILE"
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  # De app draait in de container als gebruiker 10001
  install -d -m 750 "$DATA_ROOT"
  chown 10001:10001 "$DATA_ROOT"
}

# Vóór het vervangen van de app: alle open documenten tussentijds opslaan. Een oudere app kent
# het commando niet; dan gaat het script gewoon door. </dev/null: bij `curl … | bash` is stdin
# de rest van dit script, en `compose exec` zou die anders opeten.
checkpoint() {
  local id
  id=$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter label=com.docker.compose.service=platform | head -n 1)
  [ -n "$id" ] || return 0
  if "${compose[@]}" exec -T platform python -m app.cli office-checkpoint --wait 30 </dev/null >/dev/null 2>&1; then
    echo "Open documenten zijn tussentijds opgeslagen."
  fi
}

wait_health() {
  for _ in $(seq 1 30); do
    if curl -fs -o /dev/null "http://127.0.0.1:$PORT/health"; then return 0; fi
    sleep 2
  done
  curl -fs -o /dev/null "http://127.0.0.1:$PORT/health"
}

main() {
  local want old_app new_app previous="" pin="" container
  # Eerst: OFFICE uit de omgeving, vóór er een .env gelezen wordt (M13)
  want=$(want_office) || exit 1
  check_host

  stap "Pakketten"
  pakketten

  stap "Docker"
  docker_setup

  stap "Broncode (${SRC_REF:-$BRANCH})"
  broncode

  stap "Instellingen"
  instellingen "$want"

  stap "Office"
  decide_office
  if [ "$OFFICE_ON" = 1 ]; then
    echo "Office: aan ($OFFICE_IMG)"
  else
    echo "Office: uit ($OFFICE_REASON)"
  fi
  [ -z "$OFFICE_REASON" ] || [ "$OFFICE_ON" = 0 ] || echo "Let op: $OFFICE_REASON."
  compose_files "$OFFICE_ON"
  container=$(office_container)
  if [ -n "$container" ]; then previous=$(docker inspect --format '{{.Image}}' "$container" 2>/dev/null || true); fi
  [ -z "${OFFICE_IMAGE:-}" ] || pin=$(image_id "$OFFICE_IMAGE")

  stap "Platform bouwen en starten (de eerste keer duurt dit een paar minuten)"
  checkpoint
  if [ "$OFFICE_ON" = 0 ] && [ -n "$container" ]; then
    # Eerst DS netjes stoppen, terwijl de app er nog is: zo vindt zijn laatste opslag de app
    echo "Office stoppen (slaat open documenten op)…"
    docker stop -t 60 "$container" >/dev/null || true
  fi
  old_app=$(image_id platform:local)
  OFFICE_IMAGE=$OFFICE_IMG "${compose[@]}" up -d --build --remove-orphans platform
  new_app=$(image_id platform:local)
  # Het vorige image van de app weg (alleen dat; geen prune van andermans images)
  if [ -n "$old_app" ] && [ "$old_app" != "$new_app" ]; then docker image rm "$old_app" >/dev/null 2>&1 || true; fi

  if ! wait_health; then
    echo "Het platform reageert niet. Laatste logregels:"
    "${compose[@]}" logs --tail 50 platform
    exit 1
  fi

  if [ "$OFFICE_ON" = 1 ]; then
    stap "Office starten"
    office_start "$container" "$previous" "$pin"
  fi

  slot
}

slot() {
  local ip url https_port
  ip=$(hostname -I | awk '{print $1}')
  url="http://$ip"
  [ "$PORT" = 80 ] || url="$url:$PORT"
  # Een .env van een oudere versie heeft nog geen HTTPS_PORT; compose.yaml neemt dan 443
  https_port=${HTTPS_PORT:-443}
  cat <<KLAAR

Klaar. Het platform draait op $url

Open dat adres in je browser. Bij het eerste bezoek maak je het beheerdersaccount
aan en zet je 2FA aan (verplicht); doe dat voordat je de poort in je router openzet.

Van buiten bereikbaar maken:
  1. Laat je domein naar je publieke IP-adres wijzen en stuur in je router door:
       poort 80  -> $ip:$PORT
       poort 443 -> $ip:$https_port
     Buiten moeten het echt 80 en 443 zijn: Let's Encrypt controleert altijd via
     poort 80, en http stuurt door naar https zonder poortnummer (dus 443).
  2. Vul op /beheer bij HTTPS je domein in; het certificaat komt vanzelf.

2FA kwijt en je bent de enige beheerder:
  cd $SRC/infra/single && docker compose --env-file $ENV_FILE exec platform python -m app.cli reset-2fa <e-mailadres>

KLAAR
  if [ "$OFFICE_ON" = 1 ]; then
    echo "Office: aan. De editor start op de achtergrond; bewerken kan over 2 à 3 minuten"
    echo "  (DS bouwt eerst zijn fonts en herstart zichzelf; /beheer zegt zolang 'Start op')."
    [ -z "$OFFICE_REASON" ] || echo "  Let op: $OFFICE_REASON."
    echo "  Uit: OFFICE=uit bash install.sh"
  else
    echo "Office: uit, want $OFFICE_REASON."
    office_supported && echo "  Aan: OFFICE=aan bash install.sh"
  fi
  cat <<KLAAR

Instellingen: $ENV_FILE
Data:         $DATA_ROOT
Bijwerken:    draai dit script opnieuw.
KLAAR
  for oud in postgres seaweedfs traefik; do
    if [ -d "/tank/$oud" ] || [ -d "$PLATFORM_ROOT/data/$oud" ]; then
      echo "Map '$oud' van de eerste versie wordt niet meer gebruikt; die mag weg."
    fi
  done
}

[ "${INSTALL_LIB:-}" = 1 ] || main "$@"
