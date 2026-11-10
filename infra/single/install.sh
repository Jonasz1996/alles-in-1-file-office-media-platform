#!/usr/bin/env bash
# Installeert het platform op één Debian- of Ubuntu-container: één Docker-container
# met API en web-app, data in /tank/platform, en (fase 4) OnlyOffice Docs als tweede
# container voor het bewerken van documenten. Draai als root in de console:
#
#   apt update && apt install -y curl
#   curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/main/infra/single/install.sh | bash
#
# Opnieuw draaien werkt het platform bij; instellingen en data blijven staan.
# Andere poort dan 80: curl … | PORT=8080 bash (later aan te passen in /opt/platform/.env)
# Office uit: curl … | OFFICE=uit bash. Weer aan: OFFICE=aan.
# Media (fase 5) draait standaard afgeschermd in een eigen container. Nooduitgang, zonder die
# afscherming: curl … | MEDIA_SANDBOX=uit bash. Weer afgeschermd: MEDIA_SANDBOX=aan.
# Plaatsnamen en landen bij foto's (fase 6): het script haalt twee bronnen op. Uit: curl … |
# PLAATSEN=uit bash. Vóór elke update maakt het een reservekopie van de database in
# <data>/backup (zonder gezichten en beeld); lukt die niet, dan stopt het vóór er iets verandert.
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

# Net zo voor media (fase 5): MEDIA_SANDBOX uit de omgeving wint van .env. Alleen aan of uit.
want_media() {
  case "${MEDIA_SANDBOX-}" in
    "" | aan | uit) printf '%s' "${MEDIA_SANDBOX-}" ;;
    *)
      echo "MEDIA_SANDBOX moet aan of uit zijn (nu: $MEDIA_SANDBOX)." >&2
      return 1
      ;;
  esac
}

# En voor plaatsnamen (fase 6, Keuze 29): PLAATSEN uit de omgeving wint van .env. Alleen aan of
# uit.
want_places() {
  case "${PLAATSEN-}" in
    "" | aan | uit) printf '%s' "${PLAATSEN-}" ;;
    *)
      echo "PLAATSEN moet aan of uit zijn (nu: $PLAATSEN)." >&2
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

# En media? 0.6.0 (terugrollen, Keuze 47) heeft het nog niet.
media_supported() { [ -f "$SRC/infra/single/compose.media.yaml" ]; }

# En plaatsnamen? 0.7.1 (terugrollen, §3.10) heeft ze nog niet.
places_supported() { [ -f "$SRC/api/app/geo/load.py" ]; }

# Na het maken of lezen van .env, als de versie plaatsnamen heeft: PLAATSEN=aan erbij als hij
# ontbreekt. Een bestaande waarde blijft; een gevraagde PLAATSEN vervangt hem.
merge_places_env() {
  add_key PLAATSEN aan
  [ -z "$1" ] || set_key PLAATSEN "$1"
  chmod 600 "$ENV_FILE"
}

# Na het maken of lezen van .env, als de versie media heeft. Een bestaande waarde blijft; een
# gevraagde MEDIA_SANDBOX vervangt hem. MEDIA_WORKER_KEY (de sleutel waarmee de worker zich bij
# de app bewijst, compose.media.yaml) komt er één keer bij, zoals de geheimen van office, en
# wordt nooit vervangen.
merge_media_env() {
  add_key MEDIA_SANDBOX aan
  add_key MAX_UPLOAD_BYTES 53687091200
  add_key MEDIA_WORKER_KEY "$(hex)"
  [ -z "$1" ] || set_key MEDIA_SANDBOX "$1"
  chmod 600 "$ENV_FILE"
}

# Beslist over media voor deze run. Zet MEDIA_ON (1: de worker in zijn eigen container),
# MEDIA_LOCAL (1: MEDIA_SANDBOX=uit, de app start ffmpeg zelf) en MEDIA_REASON (waarom niet
# afgeschermd). Breekt nooit af: alles staat in een if, zoals decide_office. Een andere waarde
# dan aan of uit in .env (met de hand bewerkt) houdt de afscherming aan: dat is de veilige kant.
decide_media() {
  MEDIA_ON=0 MEDIA_LOCAL=0 MEDIA_REASON=""
  if ! media_supported; then
    MEDIA_REASON="deze versie van het platform heeft nog geen media"
  elif [ "${MEDIA_SANDBOX:-aan}" = uit ]; then
    MEDIA_LOCAL=1
    MEDIA_REASON="MEDIA_SANDBOX=uit: ffmpeg draait zonder afscherming in de app"
  elif [ "${MEDIA_SANDBOX:-aan}" != aan ]; then
    MEDIA_ON=1
    MEDIA_REASON="MEDIA_SANDBOX in $ENV_FILE moet aan of uit zijn (nu: $MEDIA_SANDBOX); media blijft afgeschermd"
  else
    MEDIA_ON=1
  fi
}

# DATA_ROOT is van de gebruiker van de app (10001): wat root daar met een pad doet, volgt een
# symlink die de app er kan neerzetten. install -d, chown, chmod en curl -o zouden zo een pad
# buiten DATA_ROOT aan 10001 geven, of overschrijven (review golf 4, veilig F2). Daarom doet root
# daar niets met een pad dat een symlink volgt. as_app_in gaat de map in (cd -P: de map die er nu
# is), kijkt dat die van 10001 is, en laat 10001 zelf het commando doen, met namen in die map.
# Een symlink geeft dan nooit meer dan wat 10001 al mag.
as_app_in() {
  local dir=$1
  shift
  (cd -P -- "$dir" && [ "$(stat -c %u .)" = 10001 ] &&
    exec setpriv --reuid=10001 --regid=10001 --clear-groups -- "$@")
}

# Mappen direct onder DATA_ROOT voor de app, met deze rechten: app_dir <rechten> <map>…. Een
# symlink of iets anders dan een map raakt hij niet aan: uitleg en return 1. Een nieuwe map maakt
# 10001 zelf; een map die er al is, krijgt 10001 als eigenaar met chown -h (dat volgt nooit een
# symlink) en zijn rechten van 10001 zelf.
app_dir() {
  local mode=$1 dir
  shift
  for dir in "$@"; do
    if [ -L "$dir" ] || { [ -e "$dir" ] && [ ! -d "$dir" ]; }; then
      echo "$dir is geen gewone map maar een symlink of een bestand; dit script raakt hem niet aan."
      echo "Maak er een gewone map van (of haal hem weg) en draai dit script opnieuw."
      return 1
    fi
    if [ ! -d "$dir" ]; then
      as_app_in "$(dirname -- "$dir")" mkdir -m "$mode" -- "$(basename -- "$dir")" || return 1
    else
      chown -h 10001:10001 "$dir" && as_app_in "$dir" chmod "$mode" . || return 1
    fi
  done
}

# De mappen voor media en de import, van de gebruiker van de app (10001)
media_dirs() { app_dir 750 "$DATA_ROOT/media" "$DATA_ROOT/import"; }

# Na het lezen van .env: een andere waarde dan aan of uit (met de hand bewerkt) zou de nieuwe app
# niet laten starten (PLATFORM_PLACES, compose.yaml). Dan stopt het script hier, vóór er iets
# veranderd is aan de app.
check_places() {
  places_supported || return 0
  case "${PLAATSEN:-aan}" in
    aan | uit) return 0 ;;
  esac
  echo "PLAATSEN in $ENV_FILE moet aan of uit zijn (nu: $PLAATSEN). Zet het goed en draai dit"
  echo "script opnieuw; de app is niet aangeraakt."
  return 1
}

# De twee bronnen voor plaatsnamen en landen (Keuze 29): naam, bytes, sha256 en adres. Gelijk aan
# api/app/geo/load.py (LANDEN, PLAATSEN) en tools/geo/haal.py; een test vergelijkt ze.
geo_bronnen() {
  cat <<'BRONNEN'
ne_10m_admin_0_countries.geojson 13287234 239eec57ac17f100a11e2536cffc56752c318b50ae765b0918ff7aab4ce8f255 https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_10m_admin_0_countries.geojson
reverse_geocoder-1.5.1.tar.gz 2246559 2a2e781b5f69376d922b78fe8978f1350c84fce0ddb07e02c834ecf98b57c75c https://files.pythonhosted.org/packages/0b/0f/b7d5d4b36553731f11983e19e1813a1059ad0732c5162c01b3220c927d31/reverse_geocoder-1.5.1.tar.gz
BRONNEN
}

# Staat dit bestand er goed: precies zoveel bytes en deze sha256?
geo_ok() {
  [ -f "$1" ] && [ "$(stat -c %s "$1")" = "$2" ] && [ "$(sha256sum "$1" | cut -d' ' -f1)" = "$3" ]
}

# Stap 2 van §3.10: de bronnen naar DATA_ROOT/geo, als PLAATSEN=aan en ze er nog niet (goed)
# staan. Elk bestand komt eerst in een eigen map van root buiten DATA_ROOT (mktemp -d) en gaat pas
# naar zijn plek als grootte en sha256 kloppen; daar zet 10001 het zelf neer, via naam.part
# (as_app_in: root schrijft nooit in DATA_ROOT/geo).
# Breekt nooit af: lukt het niet, dan "Plaatsnamen: niet geladen", en de volgende run probeert
# het opnieuw. De app bouwt er bij de start places.sqlite van (±1 minuut). Zet GEO_STATE: uit,
# klaar (stonden er al), nieuw (net opgehaald), mislukt, of leeg (deze versie heeft het niet).
geo_download() {
  local dir=$DATA_ROOT/geo name size sha url tmp failed=0 new=0
  GEO_STATE=""
  places_supported || return 0
  if [ "${PLAATSEN:-aan}" = uit ]; then
    GEO_STATE=uit
    echo "Plaatsnamen: uit (PLAATSEN=uit); er wordt niets opgehaald."
    return 0
  fi
  if ! app_dir 750 "$dir"; then
    GEO_STATE=mislukt
    echo "Plaatsnamen: niet geladen ($dir is geen gewone map). Het platform werkt verder zonder; de volgende run probeert het opnieuw."
    return 0
  fi
  if ! tmp=$(mktemp -d); then
    GEO_STATE=mislukt
    echo "Plaatsnamen: niet geladen (geen tijdelijke map). Het platform werkt verder zonder; de volgende run probeert het opnieuw."
    return 0
  fi
  while read -r name size sha url; do
    [ ! -L "$dir/$name" ] && geo_ok "$dir/$name" "$size" "$sha" && continue
    # shellcheck disable=SC2016  # $1 is van sh -c
    if curl -fsSL --retry 2 --connect-timeout 20 --max-time 600 --max-filesize "$size" -o "$tmp/$name" "$url" </dev/null &&
      geo_ok "$tmp/$name" "$size" "$sha" &&
      as_app_in "$dir" sh -c 'umask 027 && rm -f -- "$1.part" && { cat >"$1.part" && mv -fT -- "$1.part" "$1" || { rm -f -- "$1.part"; exit 1; }; }' sh "$name" <"$tmp/$name"; then
      new=1
    else
      failed=1
      echo "Kon $name niet ophalen, of de sha256 klopt niet ($url)."
    fi
    rm -f "$tmp/$name"
  done < <(geo_bronnen)
  rm -rf "$tmp"
  if [ "$failed" = 1 ]; then
    GEO_STATE=mislukt
    echo "Plaatsnamen: niet geladen. Het platform werkt verder zonder; de volgende run probeert het opnieuw."
  elif [ "$new" = 1 ]; then
    GEO_STATE=nieuw
    echo "Plaatsnamen: de bronnen staan klaar in $dir; de app laadt ze bij de start (±1 minuut)."
  else
    GEO_STATE=klaar
    echo "Plaatsnamen: de bronnen staan er al ($dir)."
  fi
}

# Stap 4 van §3.10 (Keuze 52): een reservekopie van de database, terwijl de oude app nog draait,
# met het image dat er nu is (ook dat van 0.7.1) en alleen de standaardbibliotheek. De tekst is
# api/app/backup.py van de broncode die nu klaarstaat; app.cli db-backup gebruikt dezelfde. Hij
# heet naar de alembic-head: DATA_ROOT/backup/platform-<head>-<tijd>.db, rechten 600, van de
# gebruiker van de app, zonder gezichten en beeld. Per head blijft de nieuwste. Een eerste
# installatie (nog geen database) slaat dit over. Lukt de kopie niet, dan stopt het script: er is
# dan nog niets veranderd aan de app, de database en office. Zet BACKUP_FILE.
reservekopie() {
  local text out="" name
  BACKUP_FILE=""
  if [ ! -f "$DATA_ROOT/platform.db" ]; then
    echo "Reservekopie: nog geen database (eerste installatie)."
    return 0
  fi
  if [ ! -f "$SRC/api/app/backup.py" ]; then
    echo "Reservekopie: deze versie van het platform kent hem nog niet; overgeslagen."
    return 0
  fi
  # In `reservekopie || exit 1` doet set -e niets: elke stap zegt dus zelf dat het stopt. De
  # laatste regel van de uitvoer is het pad van de kopie in de container
  if text=$(cat "$SRC/api/app/backup.py") && app_dir 700 "$DATA_ROOT/backup" &&
    out=$("${compose[@]}" run --rm --no-deps -T platform python -I -c "$text" /data/platform.db /data/backup </dev/null); then
    name=$(basename "${out##*$'\n'}")
    case "$name" in
      platform-*.db)
        BACKUP_FILE=$DATA_ROOT/backup/$name
        echo "Reservekopie: $BACKUP_FILE"
        return 0
        ;;
    esac
  fi
  echo "De reservekopie van de database lukte niet (de melding staat hierboven). Er is nog niets"
  echo "veranderd: de app draait zoals hij draaide. Los het op en draai dit script opnieuw."
  return 1
}

# Na de app: de bronnen zijn in deze run opgehaald, maar de app is niet opnieuw gestart (hetzelfde
# image en dezelfde instellingen). Dan bouwt geo-load places.sqlite nu, in de draaiende app; de app
# opent hem zelf bij de volgende vraag. Breekt nooit af.
geo_load_running() {
  [ "${GEO_STATE-}" = nieuw ] || return 0
  [ -n "$1" ] && [ "$1" = "$2" ] || return 0
  echo "Plaatsnamen laden (±1 minuut)…"
  "${compose[@]}" exec -T platform python -m app.cli geo-load </dev/null ||
    echo "Let op: plaatsnamen laden lukte niet (zie hierboven); het platform werkt verder."
}

# De container van de app (draaiend), of niets
platform_container() {
  docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter label=com.docker.compose.service=platform | head -n 1
}

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

# De compose-bestanden voor deze run (zet de array compose). $1 = office, $2 = media in zijn
# eigen container, $3 = media zonder afscherming (MEDIA_SANDBOX=uit: de app krijgt media, maar
# geen PLATFORM_MEDIA_WORKER, en start de worker zelf). Zonder $2 en $3: geen media (0.6.0).
compose_files() {
  local dir=$SRC/infra/single
  compose=(docker compose --project-directory "$dir" --env-file "$ENV_FILE" -f "$dir/compose.yaml")
  if [ "$1" = 1 ]; then
    compose+=(-f "$dir/compose.office.yaml")
    if limits_supported; then compose+=(-f "$dir/compose.office-limits.yaml"); fi
  fi
  if [ "${2:-0}" = 1 ]; then
    compose+=(-f "$dir/compose.media.yaml")
    if limits_supported; then compose+=(-f "$dir/compose.media-limits.yaml"); fi
  elif [ "${3:-0}" = 1 ]; then
    compose+=(-f "$dir/compose.media-local.yaml")
  fi
}

# Na de app (en vóór office): de worker in zijn container, en daarna de staat van media. Lukt
# de start niet, dan draait de app zonder worker (alleen direct play) en zegt het script hoe het
# verder kan. Breekt nooit af (Keuze 47): de app draait al.
media_start() {
  if [ "$MEDIA_ON" = 1 ]; then
    if ! "${compose[@]}" up -d --no-deps media </dev/null; then
      MEDIA_REASON="de mediaworker kon niet starten (de melding van Docker staat hierboven)"
      echo "Let op: $MEDIA_REASON."
      echo "Afspelen werkt alleen direct. Nooduitgang: MEDIA_SANDBOX=uit (zonder afscherming)."
      return 0
    fi
  elif [ "$MEDIA_LOCAL" != 1 ]; then
    return 0
  fi
  "${compose[@]}" exec -T platform python -m app.cli media-status --wait 20 </dev/null ||
    echo "Let op: media meldt een probleem (zie hierboven); het platform werkt verder."
}

# Vóór het vervangen van de app: kijkt er iemand? Alleen een waarschuwing (Keuze 47): die
# streams staan tot ±60 s stil, en de speler probeert het zelf opnieuw. Een oudere app kent het
# commando niet, of media staat uit; dan zegt het script niets.
watchers() {
  local id out
  id=$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter label=com.docker.compose.service=platform | head -n 1)
  [ -n "$id" ] || return 0
  out=$("${compose[@]}" exec -T platform python -m app.cli media-status --sessions </dev/null 2>/dev/null) || true
  printf '%s\n' "$out" | grep -E '^(Let op: er |  )' || true
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
  local want=$1 want_m=$2 want_p=${3-}
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
  if media_supported; then
    merge_media_env "$want_m"
  elif [ -n "$want_m" ]; then
    # Een versie zonder media (terugrollen naar 0.6.0): de wens bewaren, verder niets
    add_key MEDIA_SANDBOX "$want_m"
    set_key MEDIA_SANDBOX "$want_m"
  fi
  if places_supported; then
    merge_places_env "$want_p"
  elif [ -n "$want_p" ]; then
    # Een versie zonder plaatsnamen (terugrollen naar 0.7.1): de wens bewaren, verder niets
    add_key PLAATSEN "$want_p"
    set_key PLAATSEN "$want_p"
  fi
  chmod 600 "$ENV_FILE"
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  # De app draait in de container als gebruiker 10001
  install -d -m 750 "$DATA_ROOT"
  chown 10001:10001 "$DATA_ROOT"
  if media_supported; then media_dirs; fi
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
  local want want_m want_p old_app new_app previous="" pin="" container app_before app_after
  # Eerst: OFFICE, MEDIA_SANDBOX en PLAATSEN uit de omgeving, vóór er een .env gelezen wordt (M13)
  want=$(want_office) || exit 1
  want_m=$(want_media) || exit 1
  want_p=$(want_places) || exit 1
  GEO_STATE="" BACKUP_FILE=""
  check_host

  stap "Pakketten"
  pakketten

  stap "Docker"
  docker_setup

  stap "Broncode (${SRC_REF:-$BRANCH})"
  broncode

  stap "Instellingen"
  instellingen "$want" "$want_m" "$want_p"
  check_places || exit 1

  if places_supported; then
    stap "Plaatsnamen"
    geo_download
  fi

  stap "Office"
  decide_office
  if [ "$OFFICE_ON" = 1 ]; then
    echo "Office: aan ($OFFICE_IMG)"
  else
    echo "Office: uit ($OFFICE_REASON)"
  fi
  [ -z "$OFFICE_REASON" ] || [ "$OFFICE_ON" = 0 ] || echo "Let op: $OFFICE_REASON."

  stap "Media"
  decide_media
  if [ "$MEDIA_ON" = 1 ]; then
    echo "Media: aan (afgeschermd)"
    [ -z "$MEDIA_REASON" ] || echo "Let op: $MEDIA_REASON."
  elif [ "$MEDIA_LOCAL" = 1 ]; then
    echo "Media: aan, zonder afscherming ($MEDIA_REASON)"
  else
    echo "Media: uit ($MEDIA_REASON)"
  fi
  compose_files "$OFFICE_ON" "$MEDIA_ON" "$MEDIA_LOCAL"
  container=$(office_container)
  if [ -n "$container" ]; then previous=$(docker inspect --format '{{.Image}}' "$container" 2>/dev/null || true); fi
  [ -z "${OFFICE_IMAGE:-}" ] || pin=$(image_id "$OFFICE_IMAGE")

  stap "Platform bouwen en starten (de eerste keer duurt dit een paar minuten)"
  watchers
  checkpoint
  reservekopie || exit 1
  app_before=$(platform_container)
  if [ "$OFFICE_ON" = 0 ] && [ -n "$container" ]; then
    # Eerst DS netjes stoppen, terwijl de app er nog is: zo vindt zijn laatste opslag de app
    echo "Office stoppen (slaat open documenten op)…"
    docker stop -t 60 "$container" >/dev/null || true
  fi
  old_app=$(image_id platform:local)
  OFFICE_IMAGE=$OFFICE_IMG "${compose[@]}" up -d --build --remove-orphans platform
  new_app=$(image_id platform:local)

  if ! wait_health; then
    echo "Het platform reageert niet. Laatste logregels:"
    "${compose[@]}" logs --tail 50 platform
    exit 1
  fi
  app_after=$(platform_container)
  geo_load_running "$app_before" "$app_after"

  if [ "$MEDIA_ON" = 1 ] || [ "$MEDIA_LOCAL" = 1 ]; then
    stap "Media starten"
    media_start
  fi

  if [ "$OFFICE_ON" = 1 ]; then
    stap "Office starten"
    office_start "$container" "$previous" "$pin"
  fi

  # Pas nu het vorige image van de app weg (alleen dat; geen prune van andermans images): de
  # oude mediaworker draaide er tot media_start nog op, en dan faalde het weghalen stil
  if [ -n "$old_app" ] && [ "$old_app" != "$new_app" ]; then docker image rm "$old_app" >/dev/null 2>&1 || true; fi

  slot
}

slot() {
  # Zo draait Jonas het script (curl … | bash): dan staat er geen install.sh in de werkmap. Het
  # script en de code van de branch waar deze run van kwam: buiten main ook met PLATFORM_BRANCH,
  # anders haalt het volgende commando de code van main op
  local ip url https_port pre="" script="curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/$BRANCH/infra/single/install.sh"
  [ "$BRANCH" = main ] || pre="PLATFORM_BRANCH=$BRANCH "
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
    echo "  Uit: $script | ${pre}OFFICE=uit bash"
  elif office_supported && [ "${OFFICE:-aan}" = aan ]; then
    # Office staat al aan, maar DS kwam niet op (Docker, het image, de start): OFFICE=aan helpt
    # dan niet, opnieuw proberen wel
    echo "Office: uit, want $OFFICE_REASON."
    echo "  Opnieuw proberen: $script | ${pre}bash"
    echo "  Uit: $script | ${pre}OFFICE=uit bash"
  else
    echo "Office: uit, want $OFFICE_REASON."
    office_supported && echo "  Aan: $script | ${pre}OFFICE=aan bash"
  fi
  slot_media "$script" "$pre"
  slot_search "$script" "$pre"
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

# De regels over zoeken en foto's in de eindtekst (fase 6, §3.10 stap 7): zoeken, de plaatsnamen
# (en hoe het verder kan) en de reservekopie van deze run. Een versie zonder fase 6: niets.
slot_search() {
  local script=$1 pre=$2
  places_supported || return 0
  echo "Zoeken op naam werkt meteen. De inhoud wordt nu op de achtergrond gelezen; volg het op /beheer → Zoeken en foto's."
  case "${GEO_STATE-}" in
    uit)
      echo "Plaatsnamen: uit (PLAATSEN=uit)."
      echo "  Aan: $script | ${pre}PLAATSEN=aan bash"
      ;;
    mislukt)
      echo "Plaatsnamen: niet geladen (de download lukte niet)."
      echo "  Opnieuw proberen: $script | ${pre}bash"
      ;;
    nieuw | klaar)
      echo "Plaatsnamen: aan."
      echo "  Uit: $script | ${pre}PLAATSEN=uit bash"
      ;;
  esac
  [ -z "${BACKUP_FILE-}" ] || echo "Reservekopie van vóór deze run: $BACKUP_FILE"
}

# De regels over media in de eindtekst: aan (afgeschermd), of waarom niet, en hoe het verder kan
slot_media() {
  local script=$1 pre=$2
  # Zonder decide_media (een test die alleen slot draait): niets over media
  [ -n "${MEDIA_ON+x}" ] || return 0
  if [ "$MEDIA_ON" = 1 ] && [ -z "$MEDIA_REASON" ]; then
    echo "Media: aan (afgeschermd)."
    echo "  Nooduitgang, zonder afscherming: $script | ${pre}MEDIA_SANDBOX=uit bash"
  elif [ "$MEDIA_ON" = 1 ]; then
    echo "Media: aan (afgeschermd), maar $MEDIA_REASON."
    echo "  Opnieuw proberen: $script | ${pre}bash"
    echo "  Nooduitgang, zonder afscherming: $script | ${pre}MEDIA_SANDBOX=uit bash"
  elif [ "$MEDIA_LOCAL" = 1 ]; then
    echo "Media: aan, zonder afscherming ($MEDIA_REASON)."
    echo "  Weer afgeschermd: $script | ${pre}MEDIA_SANDBOX=aan bash"
  else
    echo "Media: uit, want $MEDIA_REASON."
  fi
}

[ "${INSTALL_LIB:-}" = 1 ] || main "$@"
