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
# Gezichten en zoeken op beeld (fase 6, de container analyse) staan sinds 0.8.1 standaard aan
# (ANALYSE_STANDAARD hieronder): zonder ANALYSE in .env zet het script ANALYSE=aan erbij, haalt
# het de modellen van gezichten op (±39 MB) en start het de container analyse. Elke gebruiker zet
# gezichten en zoeken op beeld daarna zelf aan (Account → Foto's). Uit: curl … | ANALYSE=uit bash.
# Met de analyse aan haalt het ook één keer het model voor zoeken op beeld op (SigLIP 2, ±1,55 GB)
# en zet het om. Zonder: curl … | BEELD=uit bash.
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
# De analyse (gezichten en zoeken op beeld, fase 6, Keuze 56–58 en 84) als ANALYSE niet in .env
# en niet in de omgeving staat. Sinds 0.8.1 aan (I2; bouwontwerp §3.11: ANALYSE=aan in .env): een
# .env van 0.8.0 zonder ANALYSE krijgt ANALYSE=aan, de sleutel, de twee mappen en BEELD=aan
# (merge_analyse_env). Tot en met 0.8.0 stond hij op uit, en deed dit script zonder ANALYSE precies
# wat 0.8.0 deed; met ANALYSE=uit doet het dat nog steeds (dezelfde aanroepen van Docker, dezelfde
# mappen, geen download), met ANALYSE=uit in .env en de regels over de analyse in de uitvoer.
ANALYSE_STANDAARD=aan

stap() { printf '\n== %s\n' "$1"; }

# 64 hex-tekens uit /dev/urandom: veilig om .env als shell te lezen (Keuze 10)
hex() { od -An -N32 -tx1 /dev/urandom | tr -d ' \n'; }

# Een sleutel op een waarde zetten: elke regel die met "$1=" begint. Met alleen builtins van bash
# voor de waarde: een sleutel als ANALYSE_WORKER_KEY komt zo nooit in de argv of de omgeving van
# een ander proces (sed -i had hem in zijn argv, voor iedereen te lezen in /proc/<pid>/cmdline;
# review golf 5, veilig F5), en |, & en \ in de waarde zijn gewoon tekens. Zoals sed -i: een nieuw
# bestand ernaast met de rechten en de eigenaar van het oude, dan mv; een laatste regel zonder
# regeleinde blijft zonder. mktemp, chmod, chown en mv krijgen alleen paden.
set_key() {
  local line end out="" tmp
  [ -f "$ENV_FILE" ] || return 1
  while :; do
    if IFS= read -r line; then end=$'\n'; else [ -n "$line" ] || break; end=""; fi
    case $line in "$1="*) line=$1=$2 ;; esac
    out+=$line$end
    [ -n "$end" ] || break
  done <"$ENV_FILE"
  tmp=$(mktemp "$ENV_FILE.XXXXXX") || return 1
  if printf '%s' "$out" >"$tmp" && chmod --reference="$ENV_FILE" -- "$tmp" &&
    chown --reference="$ENV_FILE" -- "$tmp" && mv -f -- "$tmp" "$ENV_FILE"; then
    return 0
  fi
  rm -f -- "$tmp"
  return 1
}

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

# Mediabeheer (fase 11): qBittorrent, Prowlarr en Lidarr in compose.arr.yaml. ARR uit de omgeving wint
# van .env en wordt bewaard; zonder ARR staat het uit (zoals tot en met 0.8.x).
decide_arr() {
  ARR_ON=0
  [ -f "$SRC/infra/single/compose.arr.yaml" ] || return 0
  case "${ARR-}" in
    aan | uit) add_key ARR "$ARR" && set_key ARR "$ARR" ;;
    "") ;;
    *)
      echo "ARR moet aan of uit zijn (nu: $ARR)." >&2
      exit 1
      ;;
  esac
  if [ "$(sed -n 's/^ARR=//p' "$ENV_FILE" | tail -n 1)" = aan ]; then ARR_ON=1; fi
}

# Na de app: qBittorrent, Prowlarr en Lidarr vooraf instellen en koppelen (app.cli arr-koppel), dan
# starten. Breekt nooit af: de app draait al.
arr_start() {
  if ! "${compose[@]}" exec -T platform python -m app.cli arr-koppel </dev/null; then
    echo "Let op: qBittorrent, Prowlarr en Lidarr koppelen lukte niet; het platform werkt verder."
    return 0
  fi
  if ! "${compose[@]}" up -d --no-deps qbittorrent prowlarr lidarr </dev/null; then
    echo "Let op: qBittorrent, Prowlarr of Lidarr kon niet starten (de melding van Docker staat hierboven)."
    return 0
  fi
  echo "Prowlarr: voeg je torrentsites toe op http://<ip-van-deze-container>:9696"
  echo "Lidarr: volg artiesten en albums op http://<ip-van-deze-container>:8686"
}

# En voor de analyse (fase 6, Keuze 56): ANALYSE uit de omgeving wint van .env. Alleen aan of uit.
want_analyse() {
  case "${ANALYSE-}" in
    "" | aan | uit) printf '%s' "${ANALYSE-}" ;;
    *)
      echo "ANALYSE moet aan of uit zijn (nu: $ANALYSE)." >&2
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

# En de analyse? 0.8.0 (terugrollen) heeft hem nog niet.
analyse_supported() { [ -f "$SRC/infra/single/compose.analyse.yaml" ]; }

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

# Na het maken of lezen van .env, als de versie de analyse heeft. Is hij aan (gevraagd, al aan in
# .env, of ANALYSE_STANDAARD), dan komen ANALYSE=aan, de sleutel van de worker en de twee mappen
# erbij, en als de versie zoeken op beeld heeft BEELD=aan (Keuze 84). ANALYSE_WORKER_KEY
# (compose.analyse.yaml) komt er één keer bij, zoals MEDIA_WORKER_KEY, en wordt nooit vervangen;
# ook niet als de analyse daarna uit en weer aan gaat. Anders komt er alleen een gevraagde uit in
# .env, en verder niets (geen sleutel, geen map): op een regel die er al is, of als er geen regel
# is en de standaard aan is (sinds 0.8.1). Zonder die regel zou de volgende run zonder ANALYSE de
# analyse weer aanzetten. Met ANALYSE_STANDAARD=uit (tot en met 0.8.0) blijft een ontbrekende
# ANALYSE ontbreken, ook bij een gevraagde uit (review golf 5, uitrol F4).
# $2 is een gevraagde BEELD: die vervangt een regel BEELD die er is, en komt er nooit bij zonder
# de analyse aan.
merge_analyse_env() {
  local now
  now=$(sed -n 's/^ANALYSE=//p' "$ENV_FILE" | tail -n 1)
  if [ "${1:-${now:-$ANALYSE_STANDAARD}}" = aan ]; then
    add_key ANALYSE aan
    set_key ANALYSE aan
    add_key ANALYSE_WORKER_KEY "$(hex)"
    add_key MODELLEN_ROOT "$PLATFORM_ROOT/modellen"
    add_key SLEUTELS_ROOT "$PLATFORM_ROOT/sleutels/gezichten"
    if beeld_supported; then add_key BEELD aan; fi
  elif [ -n "$1" ] && { [ "$ANALYSE_STANDAARD" = aan ] || grep -q '^ANALYSE=' "$ENV_FILE"; }; then
    add_key ANALYSE "$1"
    set_key ANALYSE "$1"
  fi
  if [ -n "${2-}" ] && grep -q '^BEELD=' "$ENV_FILE"; then set_key BEELD "$2"; fi
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

# Staat dit bestand er goed: een gewoon bestand (geen symlink) met precies zoveel bytes en deze
# sha256? geo_ok <pad> <bytes> <sha256>, als tekst voor sh -c: root kijkt zo in zijn eigen
# tijdelijke map, en 10001 zelf in DATA_ROOT/geo (geo_ok_app). Zet de app net na stat iets
# anders op die plek, dan klopt de hash niet en houdt dd toch op: hij leest met iflag=nonblock
# (een FIFO zonder schrijver: niets, in plaats van te wachten), nofollow (een symlink die na
# [ ! -L ] kwam, volgt hij niet, ook niet naar /dev/zero) en ten hoogste één byte meer dan de
# grootte (count_bytes: een groter of eindeloos bestand, of een FIFO waar de app in blijft
# schrijven, leest hij niet tot het eind)
# shellcheck disable=SC2016  # $1, $2 en $3 zijn van sh -c
GEO_OK='[ ! -L "$1" ] && [ -f "$1" ] && [ "$(stat -c %s -- "$1")" = "$2" ] && [ "$(dd if="$1" iflag=nonblock,nofollow,count_bytes count=$(($2 + 1)) bs=64K status=none 2>/dev/null | sha256sum | cut -d" " -f1)" = "$3" ]'
geo_ok() { sh -c "$GEO_OK" sh "$@"; }

# Staat <naam> in <map> van de app er goed? 10001 kijkt zelf, met de naam in die map (as_app_in):
# root leest in DATA_ROOT/geo niets. Zo kan de app, door net na de controle op een symlink er een
# neer te zetten, root geen bestand van de host meer laten hashen (nacontrole golf 4, z7.md).
# geo_ok_app <map> <naam> <bytes> <sha256>
geo_ok_app() {
  local dir=$1
  shift
  as_app_in "$dir" sh -c "$GEO_OK" sh "$@"
}

# Stap 2 van §3.10: de bronnen naar DATA_ROOT/geo, als PLAATSEN=aan en ze er nog niet (goed)
# staan. Of ze er goed staan, kijkt 10001 zelf (geo_ok_app). Elk bestand komt eerst in een eigen
# map van root buiten DATA_ROOT (mktemp -d) en gaat pas naar zijn plek als grootte en sha256
# kloppen; daar zet 10001 het zelf neer, via naam.part (as_app_in: root schrijft en leest nooit
# in DATA_ROOT/geo).
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
    geo_ok_app "$dir" "$name" "$size" "$sha" && continue
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

# --- de analyse (fase 6, Keuze 56–58; K1-06) --------------------------------------------------
# Zegt het script iets over de analyse? Alleen als de versie hem heeft en ANALYSE in .env (of de
# omgeving) staat, of de standaard aan is (sinds 0.8.1). Met ANALYSE_STANDAARD=uit en zonder
# ANALYSE (zo was het tot en met 0.8.0): geen regel uitvoer.
analyse_shown() {
  analyse_supported && { [ -n "${ANALYSE-}" ] || [ "$ANALYSE_STANDAARD" = aan ]; }
}

# Een pad voor de analyse (K1-06): analyse_path_ok <pad> <1: het laatste deel mag van 10001 zijn>.
# Absoluut, zonder . of .. of //, geen deel een symlink of iets anders dan een map, elk deel dat er
# is van root (behalve het laatste van SLEUTELS_ROOT) en niet door anderen te schrijven, tenzij
# met de sticky bit (zoals /tmp: daar kan niemand anders een map van root vervangen). Het pad ligt
# niet onder DATA_ROOT, en DATA_ROOT niet eronder: DATA_ROOT is van de app, en een symlink die de
# app daar neerzet, zou root anders volgen. Zegt waarom niet, en return 1.
analyse_path_ok() {
  local path=$1 app_last=$2 rest now="" part data
  case "$path/" in
    /*) ;;
    *)
      echo "$path is geen absoluut pad."
      return 1
      ;;
  esac
  case "$path/" in
    // | *//* | */./* | */../*)
      echo "$path hoort een pad zonder . of .. of // te zijn, en niet / zelf."
      return 1
      ;;
  esac
  data=$(realpath -m -- "$DATA_ROOT")
  case "$path/" in
    "$data/"*)
      echo "$path ligt in DATA_ROOT ($DATA_ROOT), en dat is van de app; kies een map daarbuiten."
      return 1
      ;;
  esac
  case "$data/" in
    "$path/"*)
      echo "DATA_ROOT ($DATA_ROOT) ligt in $path; kies een eigen map voor de analyse."
      return 1
      ;;
  esac
  rest=${path#/}
  while [ -n "$rest" ]; do
    part=${rest%%/*}
    if [ "$part" = "$rest" ]; then rest=""; else rest=${rest#*/}; fi
    now=$now/$part
    if [ -L "$now" ]; then
      echo "$now is een symlink; dit script raakt hem niet aan. Maak er een gewone map van."
      return 1
    fi
    [ -e "$now" ] || return 0
    if [ ! -d "$now" ]; then
      echo "$now is geen map; dit script raakt het niet aan."
      return 1
    fi
    [ -z "$rest" ] && [ "$app_last" = 1 ] && return 0
    if [ "$(stat -c %u -- "$now")" != 0 ]; then
      echo "$now is niet van root; dit script raakt het niet aan."
      return 1
    fi
    if [ -n "$(find "$now" -maxdepth 0 -perm /022 ! -perm -1000)" ]; then
      echo "$now mag door anderen dan root beschreven worden; dit script raakt het niet aan."
      return 1
    fi
  done
}

# De mappen van de analyse, buiten DATA_ROOT (Keuze 68, K1-06): MODELLEN_ROOT (0755, van root) en
# SLEUTELS_ROOT (0700, van 10001: de app bewaart er de sleutel per eigenaar). Klopt een pad niet
# (analyse_path_ok), of ligt het ene in het andere, dan weigert het met uitleg en return 1, en
# is er niets aangemaakt. Root maakt alleen mappen onder mappen van root; nooit iets onder een
# map van de app.
analyse_dirs() {
  local m=$MODELLEN_ROOT s=$SLEUTELS_ROOT
  analyse_path_ok "$m" 0 || return 1
  analyse_path_ok "$s" 1 || return 1
  case "$m/" in
    "$s/"*)
      echo "MODELLEN_ROOT ($m) ligt in SLEUTELS_ROOT ($s); kies twee aparte mappen."
      return 1
      ;;
  esac
  case "$s/" in
    "$m/"*)
      echo "SLEUTELS_ROOT ($s) ligt in MODELLEN_ROOT ($m); kies twee aparte mappen."
      return 1
      ;;
  esac
  (umask 022 && mkdir -p -- "$m" "$(dirname -- "$s")") || return 1
  chown root:root "$m" && chmod 0755 "$m" || return 1
  if [ ! -d "$s" ]; then mkdir -m 0700 -- "$s" || return 1; fi
  chown -h 10001:10001 "$s" && chmod 0700 "$s"
}

# Een naam uit models.lock: delen van [A-Za-z0-9._-], gescheiden door /, zonder . of .. (zoals
# app/analyse/lock.py). Een andere naam zou buiten MODELLEN_ROOT kunnen wijzen.
model_name_ok() {
  case "/$1/" in
    // | *//* | */./* | */../*) return 1 ;;
    *[!abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-]*) return 1 ;;
  esac
}

# Haalt <url> naar <doel> (in een eigen map van root) en kijkt of grootte en sha256 kloppen:
# ophalen <url> <bytes> <sha256> <doel>. curl stopt bij meer bytes dan verwacht (--max-filesize)
# en heeft per MB een seconde extra. Alleen de bestanden van de analyse: de bronnen van geo houden
# hun eigen curl (--retry 2, zoals in 0.8.0).
ophalen() {
  curl -fsSL --retry 3 --connect-timeout 20 --max-time $((600 + $2 / 1000000)) --max-filesize "$2" -o "$4" "$1" </dev/null &&
    geo_ok "$4" "$2" "$3"
}

# Een map in MODELLEN_ROOT voor een model met een map in de naam (X3: siglip2-b32-256/…): elk deel
# een gewone map van root (0755), nooit een symlink. model_dir <naam>
model_dir() {
  local rest=$1 now=$MODELLEN_ROOT part
  while [ "${rest#*/}" != "$rest" ]; do
    part=${rest%%/*}
    rest=${rest#*/}
    now=$now/$part
    if [ -L "$now" ] || { [ -e "$now" ] && [ ! -d "$now" ]; }; then
      echo "$now is een symlink of geen map; dit script raakt het niet aan."
      return 1
    fi
    [ -d "$now" ] || mkdir -m 0755 -- "$now" || return 1
    if [ "$(stat -c %u -- "$now")" != 0 ]; then
      echo "$now is niet van root; dit script raakt het niet aan."
      return 1
    fi
  done
}

# Zet een bestand van root in zijn eigen tijdelijke map neer als <doel> (0644, van root): eerst
# install in die map, dan mv -T (een rename: een symlink op het doel volgt hij niet, hij vervangt
# hem). zet_neer <bron> <tijdelijke map> <doel>
zet_neer() {
  install -m 0644 -o root -g root -- "$1" "$2/klaar" && mv -fT -- "$2/klaar" "$3"
}

# De modellen van models.lock in MODELLEN_ROOT (Keuze 58, K1-06). Per regel: staat het bestand er
# al goed (geen symlink, gewoon bestand, grootte en sha256), dan niets. Anders ophalen naar een map
# van mktemp -d onder MODELLEN_ROOT (0700, van root) en pas dan op zijn plek. Een symlink of iets
# anders op die plek raakt het niet aan. Een url "-" maakt de host zelf (X3). De regels in de map
# van zoeken op beeld (BEELD_MAP/…: de vijf bronnen van SigLIP 2 en de omzetting) haalt deze
# functie niet: die zijn van de stap BEELD (Keuze 84, X3b), met BEELD=aan en een eigen controle
# op de ruimte; ze blijven wel staan (review golf 5, uitrol F3). Een model dat niet lukt, is niet
# fataal: de container start, en zegt models_missing. Daarna: *.onnx die niet meer in de lock
# staan weg, en de licenties ernaast. Zet MODELS_CHANGED (1: --force-recreate) en MODELS_FAILED
# (de namen die niet lukten).
BEELD_MAP=siglip2-b32-256
modellen_ophalen() {
  local root=$MODELLEN_ROOT lock=$SRC/api/app/analyse/models.lock lic=$SRC/api/app/analyse/licenties
  local sha size name url rest target tmp need=0 avail keep="" f rel line todo=()
  MODELS_CHANGED=0 MODELS_FAILED=""
  if [ ! -f "$lock" ]; then
    echo "Let op: deze versie heeft geen models.lock; er wordt niets opgehaald."
    return 0
  fi
  while read -r sha size name url rest; do
    case "$sha" in "" | "#"*) continue ;; esac
    if ! [[ $sha =~ ^[0123456789abcdef]{64}$ && $size =~ ^[123456789][0123456789]{0,11}$ ]] ||
      ! model_name_ok "$name" || [ -z "$url" ] || { [ -n "$rest" ] && [ "${rest#\#}" = "$rest" ]; }; then
      echo "Let op: een regel van models.lock klopt niet (${name:-$sha}); overgeslagen."
      continue
    fi
    keep=$keep$name$'\n'
    case $name in "$BEELD_MAP"/*) continue ;; esac
    geo_ok "$root/$name" "$size" "$sha" && continue
    [ "$url" != - ] || continue
    todo+=("$sha $size $name $url")
    need=$((need + size))
  done <"$lock"
  rm -rf -- "$root"/.ophalen.*
  if [ "${#todo[@]}" -gt 0 ]; then
    avail=$(df --output=avail -B1 -- "$root" | tail -n 1 | tr -d ' ')
    if ! [[ $avail =~ ^[0-9]+$ ]] || [ "$avail" -lt $((need + 100000000)) ]; then
      echo "Let op: te weinig ruimte in $root voor de modellen ($((need / 1000000)) MB nodig, plus 100 MB marge)."
      for line in "${todo[@]}"; do
        read -r sha size name url <<<"$line"
        MODELS_FAILED="$MODELS_FAILED $name"
        echo "Let op: $name kon niet geladen worden; gezichten wachten tot de volgende installatie."
      done
      todo=()
    fi
  fi
  if ! tmp=$(mktemp -d -- "$root/.ophalen.XXXXXX"); then
    echo "Let op: geen tijdelijke map in $root; er wordt niets opgehaald."
    for line in "${todo[@]}"; do
      read -r sha size name url <<<"$line"
      MODELS_FAILED="$MODELS_FAILED $name"
      echo "Let op: $name kon niet geladen worden; gezichten wachten tot de volgende installatie."
    done
    MODELS_FAILED=${MODELS_FAILED# }
    return 0
  fi
  for line in "${todo[@]}"; do
    read -r sha size name url <<<"$line"
    target=$root/$name
    if ! model_dir "$name"; then
      :
    elif [ -L "$target" ] || { [ -e "$target" ] && [ ! -f "$target" ]; }; then
      echo "$target is een symlink of geen gewoon bestand; dit script raakt het niet aan."
    elif ophalen "$url" "$size" "$sha" "$tmp/model" && zet_neer "$tmp/model" "$tmp" "$target"; then
      MODELS_CHANGED=1
      echo "Model geladen: $name"
      rm -f -- "$tmp/model"
      continue
    else
      echo "Kon $name niet ophalen, of de grootte of de sha256 klopt niet ($url)."
    fi
    rm -f -- "$tmp/model" "$tmp/klaar"
    MODELS_FAILED="$MODELS_FAILED $name"
    echo "Let op: $name kon niet geladen worden; gezichten wachten tot de volgende installatie."
  done
  while IFS= read -r -d '' f; do
    rel=${f#"$root"/}
    if ! grep -qxF -- "$rel" <<<"$keep"; then
      rm -f -- "$f" && MODELS_CHANGED=1 && echo "Oud model weg: $rel"
    fi
  done < <(find "$root" -mindepth 1 -maxdepth 2 -path "$root/.ophalen.*" -prune -o -type f -name '*.onnx' -print0)
  for f in "$lic"/*.txt; do
    [ -f "$f" ] || continue
    target=$root/${f##*/}
    if [ -L "$target" ] || { [ -e "$target" ] && [ ! -f "$target" ]; }; then
      echo "Let op: $target is een symlink of geen gewoon bestand; de licentie staat er niet naast."
    elif ! cmp -s -- "$f" "$target"; then
      zet_neer "$f" "$tmp" "$target" || echo "Let op: de licentie ${f##*/} kon niet naast de modellen."
    fi
  done
  rm -rf -- "$tmp"
  MODELS_FAILED=${MODELS_FAILED# }
}

# Beslist over de analyse voor deze run (Keuze 56–58). Zet ANALYSE_ON (1: de container analyse en
# compose.analyse.yaml) en ANALYSE_REASON (waarom uit, of wat er niet goed ging). Bij aan eerst de
# mappen en dan de modellen. Breekt nooit af: weigert het script een map, dan draait het platform
# deze keer zonder analyse; mist er een model, dan start de container en zegt hij models_missing.
# Een andere waarde dan aan of uit in .env (met de hand bewerkt): uit, met uitleg. Uit zegt waar
# het vandaan komt: .env, of alleen de aanroep (ANALYSE=uit bij een .env zonder regel ANALYSE: met
# ANALYSE_STANDAARD=uit schrijft merge_analyse_env die niet; review golf 5, uitrol F4).
decide_analyse() {
  ANALYSE_ON=0 ANALYSE_REASON="" MODELS_CHANGED=0 MODELS_FAILED=""
  analyse_shown || return 0
  case "${ANALYSE:-$ANALYSE_STANDAARD}" in
    aan) ;;
    uit)
      if grep -q '^ANALYSE=' "$ENV_FILE" 2>/dev/null; then
        ANALYSE_REASON="ANALYSE=uit in $ENV_FILE"
      else
        ANALYSE_REASON="ANALYSE=uit bij de aanroep; $ENV_FILE heeft geen regel ANALYSE"
      fi
      return 0
      ;;
    *)
      ANALYSE_REASON="ANALYSE in $ENV_FILE moet aan of uit zijn (nu: $ANALYSE); de analyse blijft uit"
      return 0
      ;;
  esac
  if ! analyse_dirs; then
    ANALYSE_REASON="de mappen voor de analyse kloppen niet (zie hierboven); het platform draait deze keer zonder"
    return 0
  fi
  modellen_ophalen
  gezichten_ruimte
  ANALYSE_ON=1
  [ -z "$MODELS_FAILED" ] || ANALYSE_REASON="niet alle modellen zijn er ($MODELS_FAILED); gezichten wachten tot de volgende installatie"
}

# --- zoeken op beeld (fase 6, X3b; Keuze 73 en 84; K1-05, K1-06, K2-19) ------------------------
# Het model staat in MODELLEN_ROOT/BEELD_MAP. Alleen met de analyse aan, en alleen als de versie
# het heeft: compose.analyse.yaml (de dienst analyse-omzetten) en regels van BEELD_MAP in
# models.lock (de vijf bronnen en de uitkomst van de omzetter). Met ANALYSE=uit (of, met
# ANALYSE_STANDAARD=uit, zonder ANALYSE) of met BEELD=uit doet het niets: geen download, geen
# omzetting, geen map.
beeld_supported() {
  analyse_supported && [ -f "$SRC/api/app/analyse/models.lock" ] &&
    grep -Eq "^[0123456789abcdef]{64}[[:space:]]+[0123456789]+[[:space:]]+$BEELD_MAP/" "$SRC/api/app/analyse/models.lock"
}

# BEELD uit de omgeving wint van .env, zoals ANALYSE. Alleen aan of uit.
want_beeld() {
  case "${BEELD-}" in
    "" | aan | uit) printf '%s' "${BEELD-}" ;;
    *)
      echo "BEELD moet aan of uit zijn (nu: $BEELD)." >&2
      return 1
      ;;
  esac
}

# De ruimte die DATA_ROOT vrij moet hebben voor de vectoren en de cache van zoeken op beeld
# (±205 + ±154 MB per 100.000 foto's, Keuze 84), met marge; en de marge op MODELLEN_ROOT
BEELD_DATA=1000000000
BEELD_MARGE=100000000
BEELD_MISLUKT="Zoeken op beeld: het model is niet geladen. De volgende run probeert het opnieuw."

# De ruimte die DATA_ROOT vrij hoort te hebben voor de gezichten (K2-19; x2b.md, Open 7): de
# bovenkant van bouwontwerp §3.1a "Ruimte", ±90 MB per 100.000 gezichten plus ±15 MB voor
# face_scan per 100.000 foto's. Minder vrij: een waarschuwing, nooit een stop; de analyse gaat
# door (/beheer toont de ruimte van de gezichten).
GEZICHTEN_DATA=105000000

# Een waarschuwing als DATA_ROOT minder dan GEZICHTEN_DATA vrij heeft, of df niets bruikbaars
# zegt. Altijd return 0: het script stopt hier nooit (set -e), ook niet als df mislukt.
gezichten_ruimte() {
  local avail
  avail=$(df --output=avail -B1 -- "$DATA_ROOT" 2>/dev/null | tail -n 1 | tr -d ' ') || avail=""
  if ! [[ $avail =~ ^[0-9]+$ ]] || [ "$avail" -lt "$GEZICHTEN_DATA" ]; then
    echo "Let op: weinig ruimte in $DATA_ROOT voor de gezichten ($((GEZICHTEN_DATA / 1000000)) MB aangeraden; ±90 MB per 100.000 gezichten). De analyse gaat door."
  fi
  return 0
}

# De regels van BEELD_MAP uit models.lock: BEELD_BRON (sha256 bytes naam url, de bronnen in
# BEELD_MAP/bron/) en BEELD_UIT (sha256 bytes naam, de uitkomst in BEELD_MAP/, url -), en
# BEELD_NODIG: de bronnen plus twee keer de uitkomst (de uitvoermap en de nieuwe map van root).
# Een regel die niet klopt, of geen bron of geen uitkomst: uitleg en return 1.
beeld_regels() {
  local lock=$SRC/api/app/analyse/models.lock sha size name url rest base
  BEELD_BRON=() BEELD_UIT=() BEELD_NODIG=0
  while read -r sha size name url rest; do
    case "$sha" in "" | "#"*) continue ;; esac
    case $name in "$BEELD_MAP"/*) ;; *) continue ;; esac
    base=${name##*/}
    if ! [[ $sha =~ ^[0123456789abcdef]{64}$ && $size =~ ^[123456789][0123456789]{0,11}$ ]] ||
      ! model_name_ok "$name" || [ -z "$url" ] || { [ -n "$rest" ] && [ "${rest#\#}" = "$rest" ]; }; then
      echo "Let op: een regel van models.lock klopt niet ($name)."
      return 1
    fi
    if [ "$name" = "$BEELD_MAP/bron/$base" ] && [ "$url" != - ]; then
      BEELD_BRON+=("$sha $size $base $url")
      BEELD_NODIG=$((BEELD_NODIG + size))
    elif [ "$name" = "$BEELD_MAP/$base" ] && [ "$url" = - ]; then
      BEELD_UIT+=("$sha $size $base")
      BEELD_NODIG=$((BEELD_NODIG + 2 * size))
    else
      echo "Let op: een regel van models.lock klopt niet ($name)."
      return 1
    fi
  done <"$lock"
  if [ "${#BEELD_BRON[@]}" = 0 ] || [ "${#BEELD_UIT[@]}" = 0 ]; then
    echo "Let op: models.lock heeft niet alle regels van $BEELD_MAP (bronnen en uitkomst)."
    return 1
  fi
}

# Is er genoeg ruimte (K2-19)? Op MODELLEN_ROOT voor de bronnen en de uitkomst plus de marge, op
# DATA_ROOT voor de vectoren en de cache. free_gb kijkt naar de map van Docker; die is er voor
# het image. Te weinig: uitleg en return 1.
beeld_ruimte() {
  local avail
  avail=$(df --output=avail -B1 -- "$MODELLEN_ROOT" | tail -n 1 | tr -d ' ')
  if ! [[ $avail =~ ^[0-9]+$ ]] || [ "$avail" -lt $((BEELD_NODIG + BEELD_MARGE)) ]; then
    echo "Let op: te weinig ruimte in $MODELLEN_ROOT voor het model van zoeken op beeld ($(((BEELD_NODIG + BEELD_MARGE) / 1000000)) MB nodig, met de bron)."
    return 1
  fi
  avail=$(df --output=avail -B1 -- "$DATA_ROOT" | tail -n 1 | tr -d ' ')
  if ! [[ $avail =~ ^[0-9]+$ ]] || [ "$avail" -lt "$BEELD_DATA" ]; then
    echo "Let op: te weinig ruimte in $DATA_ROOT voor de vectoren en de cache van zoeken op beeld ($((BEELD_DATA / 1000000)) MB nodig; ±360 MB per 100.000 foto's)."
    return 1
  fi
}

# Stap 4 tot 6 van Keuze 84 in <tmp>, een map van root onder MODELLEN_ROOT (mktemp -d, 0700):
# - de bronnen naar <tmp>/bron (0755, de bestanden 0644, van root: de omzetter leest ze), elk via
#   een tijdelijk bestand met grootte en sha256 (ophalen: curl --retry 3 --max-filesize), dan mv;
# - een lege uitvoermap <tmp>/uit van uid 10004 (0700), en de dienst analyse-omzetten (profiel
#   omzetten): zonder netwerk, sleutel, analyse-run en /models (K1-05), de bron alleen-lezen;
# - als root, per bestand van de uitkomst: geen symlink en een gewoon bestand, dan een kopie met
#   dd (nofollow, nonblock, hooguit één byte meer dan models.lock zegt) in <tmp>/nieuw (0755,
#   van root), en pas die kopie wordt nagekeken op grootte en sha256. Root doet nooit chmod of mv
#   op een bestand van 10004, en volgt er geen symlink.
# Lukt iets niet: uitleg en return 1 (beeld_model ruimt <tmp> op).
beeld_omzetten() {
  local tmp=$1 line sha size base url f
  mkdir -m 0755 -- "$tmp/bron" "$tmp/nieuw" || return 1
  for line in "${BEELD_BRON[@]}"; do
    read -r sha size base url <<<"$line"
    if ophalen "$url" "$size" "$sha" "$tmp/bron/.deel" && chmod 0644 -- "$tmp/bron/.deel" &&
      mv -fT -- "$tmp/bron/.deel" "$tmp/bron/$base"; then
      echo "Bron opgehaald: $BEELD_MAP/bron/$base"
    else
      echo "Let op: kon $BEELD_MAP/bron/$base niet ophalen, of de grootte of de sha256 klopt niet ($url)."
      return 1
    fi
  done
  mkdir -m 0700 -- "$tmp/uit" && chown 10004:10003 -- "$tmp/uit" || return 1
  echo "Het model omzetten (een paar minuten, zonder netwerk)…"
  if ! OMZET_BRON=$tmp/bron OMZET_UIT=$tmp/uit "${compose[@]}" --profile omzetten run --rm --no-deps -T analyse-omzetten </dev/null; then
    echo "Let op: de omzetting lukte niet (de melding staat hierboven)."
    return 1
  fi
  for line in "${BEELD_UIT[@]}"; do
    read -r sha size base <<<"$line"
    f=$tmp/uit/$base
    if [ -L "$f" ] || [ ! -f "$f" ]; then
      echo "Let op: $base van de omzetter is geen gewoon bestand; niet overgenomen."
      return 1
    fi
    if ! (umask 022 && dd if="$f" of="$tmp/nieuw/$base" iflag=nofollow,nonblock,count_bytes count=$((size + 1)) bs=64K status=none 2>/dev/null) ||
      ! geo_ok "$tmp/nieuw/$base" "$size" "$sha"; then
      echo "Let op: $base van de omzetter klopt niet met models.lock (grootte of sha256); niet overgenomen."
      return 1
    fi
  done
}

# De stap BEELD (Keuze 84), na de start van de app (de omzetter draait in het image van deze
# versie) en vóór analyse_start. Zet BEELD_STATE: uit (BEELD=uit), ongeldig (een andere waarde
# in .env, met de hand bewerkt: dan ook niets), klaar (het model stond er al), nieuw (net omgezet; dan MODELS_CHANGED=1, en start analyse_start
# de container opnieuw) of mislukt. Stappen:
# 1. BEELD: standaard aan; uit doet niets.
# 2. Staat elk bestand van de uitkomst er al met de juiste grootte en sha256: niets doen.
# 3-6. De ruimte (beeld_ruimte), dan beeld_omzetten in een eigen map onder MODELLEN_ROOT.
#    Daarna gaat <tmp>/nieuw in één rename op de plek van BEELD_MAP (een oude map gaat eerst
#    opzij, in <tmp>): de worker ziet nooit een half model. caps telt de uitkomst pas als die
#    map er is (app/analyse/worker/caps.py), dus gezichten merken niets van een model dat nog
#    niet omgezet is.
# 7. <tmp> weg: de bron (1,55 GB), de uitvoermap en een oude map.
# 8. De licentie: siglip2-Apache-2.0.txt is een bestand van de uitkomst (met zijn sha256 in
#    models.lock), en modellen_ophalen zet licenties/*.txt naast de modellen.
# Een symlink of iets anders dan een map op de plek van BEELD_MAP, of iets anders dan een gewoon
# bestand op de plek van een bestand erin, raakt het niet aan (K1-06). Nooit fataal: bij een
# fout "Zoeken op beeld: het model is niet geladen. …", en de rest werkt gewoon.
beeld_model() {
  local target=$MODELLEN_ROOT/$BEELD_MAP line sha size base f tmp alles=1
  BEELD_STATE=""
  case "${BEELD:-aan}" in
    aan) ;;
    uit)
      BEELD_STATE=uit
      return 0
      ;;
    *)
      BEELD_STATE=ongeldig
      echo "Let op: BEELD in $ENV_FILE moet aan of uit zijn (nu: $BEELD); er wordt niets opgehaald."
      return 0
      ;;
  esac
  BEELD_STATE=mislukt
  if ! beeld_regels; then
    echo "$BEELD_MISLUKT"
    return 0
  fi
  if [ -L "$target" ] || { [ -e "$target" ] && [ ! -d "$target" ]; }; then
    echo "$target is een symlink of geen map; dit script raakt het niet aan."
    echo "$BEELD_MISLUKT"
    return 0
  fi
  for line in "${BEELD_UIT[@]}"; do
    read -r sha size base <<<"$line"
    f=$target/$base
    if [ -L "$f" ] || { [ -e "$f" ] && [ ! -f "$f" ]; }; then
      echo "$f is een symlink of geen gewoon bestand; dit script raakt het niet aan."
      echo "$BEELD_MISLUKT"
      return 0
    fi
    geo_ok "$f" "$size" "$sha" || alles=0
  done
  if [ "$alles" = 1 ]; then
    BEELD_STATE=klaar
    echo "Zoeken op beeld: het model staat er al ($target)."
    return 0
  fi
  rm -rf -- "$MODELLEN_ROOT"/.beeld.* || true
  if ! beeld_ruimte; then
    echo "$BEELD_MISLUKT"
    return 0
  fi
  if ! tmp=$(mktemp -d -- "$MODELLEN_ROOT/.beeld.XXXXXX"); then
    echo "Let op: geen tijdelijke map in $MODELLEN_ROOT."
    echo "$BEELD_MISLUKT"
    return 0
  fi
  if beeld_omzetten "$tmp" && { [ ! -e "$target" ] || mv -T -- "$target" "$tmp/oud"; }; then
    if mv -T -- "$tmp/nieuw" "$target"; then
      BEELD_STATE=nieuw
      MODELS_CHANGED=1
    elif [ -e "$tmp/oud" ]; then
      mv -T -- "$tmp/oud" "$target" || true
    fi
  fi
  rm -rf -- "$tmp" || true
  if [ "$BEELD_STATE" = nieuw ]; then
    echo "Zoeken op beeld: het model staat klaar ($target)."
  else
    echo "$BEELD_MISLUKT"
  fi
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
# $4 = de analyse (fase 6): compose.analyse.yaml, en met grenzen compose.analyse-limits.yaml.
# $5 = mediabeheer (fase 11): compose.arr.yaml.
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
  if [ "${4:-0}" = 1 ]; then
    compose+=(-f "$dir/compose.analyse.yaml")
    if limits_supported; then compose+=(-f "$dir/compose.analyse-limits.yaml"); fi
  fi
  if [ "${5:-0}" = 1 ]; then compose+=(-f "$dir/compose.arr.yaml"); fi
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

# Na de app en media: de container analyse, en daarna zijn stand (app.cli analyse-status, de eerste
# regel in ANALYSE_STATUS). Is er een model veranderd, dan met --force-recreate: de worker leest
# de modellen bij de start. Lukt de start niet, of is de stand niet ok, dan alleen een
# waarschuwing (zoals media_start): de app draait al, en gezichten wachten.
analyse_start() {
  local force=() out
  ANALYSE_STATUS=""
  [ "${MODELS_CHANGED:-0}" != 1 ] || force=(--force-recreate)
  if ! "${compose[@]}" up -d --no-deps "${force[@]}" analyse </dev/null; then
    ANALYSE_REASON="de container analyse kon niet starten (de melding van Docker staat hierboven)"
    echo "Let op: $ANALYSE_REASON; het platform werkt verder zonder gezichten."
    return 0
  fi
  if out=$("${compose[@]}" exec -T platform python -m app.cli analyse-status --wait 20 </dev/null); then
    printf '%s\n' "$out"
    ANALYSE_STATUS=${out%%$'\n'*}
  fi
  if [ "$ANALYSE_STATUS" != "Analyse: ok" ]; then
    [ -n "$ANALYSE_REASON" ] || ANALYSE_REASON="de analyse meldt een probleem (zie hierboven)"
    echo "Let op: $ANALYSE_REASON; het platform werkt verder."
  fi
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
  local want=$1 want_m=$2 want_p=${3-} want_a=${4-} want_b=${5-}
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
  if analyse_supported; then
    merge_analyse_env "$want_a" "$want_b"
  elif [ -n "$want_a" ] && { [ "$want_a" = aan ] || [ "$ANALYSE_STANDAARD" = aan ] || grep -q '^ANALYSE=' "$ENV_FILE"; }; then
    # Een versie zonder analyse (terugrollen naar 0.8.0): de wens bewaren, verder niets. Een
    # gevraagde uit komt er ook zonder regel bij als de standaard aan is (zoals in
    # merge_analyse_env): anders zet de volgende run met een nieuwere versie de analyse aan.
    add_key ANALYSE "$want_a"
    set_key ANALYSE "$want_a"
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
  local want want_m want_p want_a want_b old_app new_app previous="" pin="" container app_before app_after
  # Eerst: OFFICE, MEDIA_SANDBOX, PLAATSEN, ANALYSE en BEELD uit de omgeving, vóór er een .env
  # gelezen wordt (M13)
  want=$(want_office) || exit 1
  want_m=$(want_media) || exit 1
  want_p=$(want_places) || exit 1
  want_a=$(want_analyse) || exit 1
  want_b=$(want_beeld) || exit 1
  GEO_STATE="" BACKUP_FILE="" BEELD_STATE=""
  check_host

  stap "Pakketten"
  pakketten

  stap "Docker"
  docker_setup

  stap "Broncode (${SRC_REF:-$BRANCH})"
  broncode

  stap "Instellingen"
  instellingen "$want" "$want_m" "$want_p" "$want_a" "$want_b"
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

  # De analyse (fase 6): alleen met ANALYSE_STANDAARD=uit en zonder ANALYSE (zo was het tot en met
  # 0.8.0) geen stap en geen regel uitvoer
  if analyse_shown; then stap "Analyse (gezichten)"; fi
  decide_analyse
  if [ "$ANALYSE_ON" = 1 ]; then
    echo "Analyse: aan (modellen in $MODELLEN_ROOT)"
  elif analyse_shown; then
    echo "Analyse: uit ($ANALYSE_REASON)"
  fi
  decide_arr
  compose_files "$OFFICE_ON" "$MEDIA_ON" "$MEDIA_LOCAL" "$ANALYSE_ON" "$ARR_ON"
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
  # Zonder BUILDX_NO_DEFAULT_ATTESTATIONS hangt Docker (29+, containerd-opslag) aan elke build een
  # herkomstverklaring met de bouwtijd: dan krijgt dezelfde code toch een nieuwe image-id, en
  # zet compose de app voor niets opnieuw op (en ruimt hieronder het "oude" image op)
  BUILDX_NO_DEFAULT_ATTESTATIONS=1 OFFICE_IMAGE=$OFFICE_IMG "${compose[@]}" up -d --build --remove-orphans platform
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

  # Zoeken op beeld (Keuze 84): na de app, want de omzetter draait in het image van deze versie;
  # vóór analyse_start, die de container met --force-recreate start als het model nieuw is
  if [ "$ANALYSE_ON" = 1 ] && beeld_supported; then
    if [ "${BEELD:-aan}" != uit ]; then stap "Zoeken op beeld (het model)"; fi
    beeld_model
  fi

  if [ "$ANALYSE_ON" = 1 ]; then
    stap "Analyse starten"
    analyse_start
  fi

  if [ "$ARR_ON" = 1 ]; then
    stap "Mediabeheer (qBittorrent, Prowlarr en Lidarr)"
    arr_start
  fi

  if [ "$OFFICE_ON" = 1 ]; then
    stap "Office starten"
    office_start "$container" "$previous" "$pin"
  fi

  # Pas nu het vorige image van de app weg (alleen dat; geen prune van andermans images): de
  # oude mediaworker en de oude analyse draaiden er tot media_start en analyse_start nog op, en
  # dan faalde het weghalen stil
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
  slot_analyse "$script" "$pre"
  slot_beeld "$script" "$pre"
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

# De regels over de analyse in de eindtekst: aan, of waarom niet, en hoe het verder kan. Met
# ANALYSE_STANDAARD=uit en zonder ANALYSE (tot en met 0.8.0), of zonder decide_analyse (een test
# die alleen slot draait): niets. Aan zegt, zoals zoeken op beeld, dat elke gebruiker het zelf
# aanzet: per gebruiker staan gezichten standaard uit (api/app/prefs.py).
slot_analyse() {
  local script=$1 pre=$2
  [ -n "${ANALYSE_ON+x}" ] && analyse_shown || return 0
  if [ "$ANALYSE_ON" = 1 ] && [ -z "$ANALYSE_REASON" ]; then
    echo "Gezichten (de container analyse): aan; elke gebruiker zet het zelf aan (Account → Foto's)."
    echo "  Uit: $script | ${pre}ANALYSE=uit bash"
  elif [ "$ANALYSE_ON" = 1 ]; then
    echo "Gezichten (de container analyse): aan, maar $ANALYSE_REASON."
    echo "  Opnieuw proberen: $script | ${pre}bash"
    echo "  Uit: $script | ${pre}ANALYSE=uit bash"
  elif [ "${ANALYSE:-$ANALYSE_STANDAARD}" = uit ]; then
    echo "Gezichten (de container analyse): uit (ANALYSE=uit)."
    if [ -d "${MODELLEN_ROOT-}" ]; then
      echo "  De modellen blijven in $MODELLEN_ROOT tot je die map weggooit."
    fi
    echo "  Aan: $script | ${pre}ANALYSE=aan bash"
  else
    echo "Gezichten (de container analyse): uit, want $ANALYSE_REASON."
  fi
}

# De regels over zoeken op beeld in de eindtekst: alleen als de stap BEELD liep (de analyse aan,
# en de versie heeft het model)
slot_beeld() {
  local script=$1 pre=$2
  case "${BEELD_STATE-}" in
    klaar | nieuw)
      echo "Zoeken op beeld: het model staat klaar; elke gebruiker zet het zelf aan."
      echo "  Uit (geen download meer; het model blijft staan): $script | ${pre}BEELD=uit bash"
      ;;
    mislukt)
      echo "$BEELD_MISLUKT"
      echo "  Opnieuw proberen: $script | ${pre}bash"
      ;;
    uit)
      echo "Zoeken op beeld: uit (BEELD=uit); er wordt niets opgehaald of omgezet."
      echo "  Aan: $script | ${pre}BEELD=aan bash"
      ;;
    ongeldig)
      echo "Zoeken op beeld: uit, want BEELD in $ENV_FILE moet aan of uit zijn (nu: $BEELD)."
      echo "  Aan: $script | ${pre}BEELD=aan bash"
      ;;
  esac
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
