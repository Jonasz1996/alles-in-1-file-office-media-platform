#!/usr/bin/env bash
# Media in install.sh (bouwontwerp fase 5, §2 M10; Keuze 47). Twee delen:
#
#   bash infra/single/tests/test_install_media.sh             de functies (job infra van CI)
#   bash infra/single/tests/test_install_media.sh --stappen   de installatietest (sandbox, en de
#                                                             job media-image van CI)
#
# De functies: install.sh met INSTALL_LIB=1, zoals test_install_env.sh. Er wordt niets
# geïnstalleerd; `docker` is een nepfunctie, behalve bij `docker compose config`, dat de echte
# compose-bestanden controleert. Het controleert:
# - een MEDIA_SANDBOX uit de omgeving wint van .env; een foute waarde stopt install.sh op de
#   eerste regel, vóór er iets verandert;
# - .env: MEDIA_SANDBOX, MAX_UPLOAD_BYTES en MEDIA_WORKER_KEY (64 hex, één keer, nooit
#   vervangen, chmod 600); een versie zonder media (0.6.0) bewaart alleen de wens;
# - decide_media en media_start stoppen nooit (set -e), ook niet als Docker faalt;
# - de compose-bestanden per keuze, en `docker compose config -q` voor alle 8 combinaties
#   (office aan/uit × media aan/uit × grenzen ja/nee); compose.media.yaml zet
#   stop_grace_period 30s voor platform en user media voor de worker;
# - de volgorde van main: wie kijkt (media-status --sessions, alleen een waarschuwing), de app,
#   media, office, en pas dan het oude image van de app weg. Na twee updates is er één image
#   platform:local en is de oude id weg, ook als de oude worker er nog op draaide (een nep-Docker
#   die een image in gebruik niet weghaalt, zoals de echte).
#
# De installatietest (--stappen) staat onder stappen() hieronder, met zijn eigen uitleg.
#
# De variabelen en functies komen uit install.sh (shellcheck volgt de bron niet in CI):
# shellcheck disable=SC2034,SC2153,SC2154
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
top=$(cd "$here/../../.." && pwd)
install_sh=$here/../install.sh

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
not() { ! "$@"; }
is_hex64() { [[ $1 =~ ^[0-9a-f]{64}$ ]]; }

einde() {
  echo
  if [ "$fails" -gt 0 ]; then
    echo "$fails controle(s) mislukt ($oks goed)."
    exit 1
  fi
  echo "Alle $oks controles geslaagd."
}

# ============================================================================================
# Deel 1: de functies
# ============================================================================================

functies() {
  local work want out code key key2 i office media limits files name
  work=$(mktemp -d)
  # shellcheck disable=SC2064  # work staat nu vast
  trap "rm -rf '$work'" EXIT

  export PLATFORM_ROOT=$work/root INSTALL_LIB=1
  unset OFFICE OFFICE_IMAGE MEDIA_SANDBOX
  # shellcheck source=/dev/null
  . "$install_sh"
  mkdir -p "$PLATFORM_ROOT" "$SRC/infra/single"

  has_line() { grep -qxF "$1" "$ENV_FILE"; }
  value() { sed -n "s/^$1=//p" "$ENV_FILE"; }
  mode() { stat -c %a "$1"; }
  fase4_env() {
    cat >"$ENV_FILE" <<'ENV'
DATA_ROOT=/tank/platform
NODE_NAME=node1
PORT=80
HTTPS_PORT=443
SECURE_COOKIES=false
OFFICE=aan
OFFICE_JWT_SECRET=1111111111111111111111111111111111111111111111111111111111111111
OFFICE_LINK_SECRET=2222222222222222222222222222222222222222222222222222222222222222
ENV
    chmod 644 "$ENV_FILE"
  }
  with_media() { : >"$SRC/infra/single/compose.media.yaml"; }
  without_media() { rm -f "$SRC/infra/single/compose.media.yaml"; }

  echo "== merge_media_env op een .env van 0.6.0"
  fase4_env
  merge_media_env ""
  check "MEDIA_SANDBOX=aan erbij" has_line "MEDIA_SANDBOX=aan"
  check "MAX_UPLOAD_BYTES=53687091200 erbij (50 GiB)" has_line "MAX_UPLOAD_BYTES=53687091200"
  key=$(value MEDIA_WORKER_KEY)
  check "MEDIA_WORKER_KEY is 64 hex-tekens" is_hex64 "$key"
  check "een eigen sleutel, niet die van office" not grep -qxF "OFFICE_JWT_SECRET=$key" "$ENV_FILE"
  check "chmod 600, zoals de andere geheimen" test "$(mode "$ENV_FILE")" = 600
  check "de rest staat er nog" has_line "OFFICE_LINK_SECRET=2222222222222222222222222222222222222222222222222222222222222222"
  check "precies elf regels" test "$(wc -l <"$ENV_FILE")" = 11

  echo "== een tweede run: de sleutel en een eigen grens blijven, een gevraagde keuze wint"
  set_key MAX_UPLOAD_BYTES 1000
  merge_media_env ""
  check "de sleutel blijft (nooit vervangen)" test "$(value MEDIA_WORKER_KEY)" = "$key"
  check "een eigen MAX_UPLOAD_BYTES blijft" has_line "MAX_UPLOAD_BYTES=1000"
  merge_media_env uit
  check "MEDIA_SANDBOX=uit gevraagd: vervangt aan" has_line "MEDIA_SANDBOX=uit"
  check "en de sleutel blijft" test "$(value MEDIA_WORKER_KEY)" = "$key"
  merge_media_env ""
  check "zonder vraag blijft uit staan" has_line "MEDIA_SANDBOX=uit"
  merge_media_env aan
  check "MEDIA_SANDBOX=aan gevraagd" has_line "MEDIA_SANDBOX=aan"
  check "nog steeds één sleutel" test "$(grep -c '^MEDIA_WORKER_KEY=' "$ENV_FILE")" = 1

  echo "== .env.example: de lege sleutel wordt gevuld, de rest is de standaard"
  cp "$here/../.env.example" "$ENV_FILE"
  merge_env ""
  merge_media_env ""
  check "MEDIA_WORKER_KEY gevuld" is_hex64 "$(value MEDIA_WORKER_KEY)"
  check "MEDIA_SANDBOX=aan uit het voorbeeld" has_line "MEDIA_SANDBOX=aan"
  check "MAX_UPLOAD_BYTES uit het voorbeeld" has_line "MAX_UPLOAD_BYTES=53687091200"
  key2=$(value MEDIA_WORKER_KEY)
  merge_media_env ""
  check "een tweede run vervangt de gevulde sleutel niet" test "$(value MEDIA_WORKER_KEY)" = "$key2"

  echo "== want_media vóór het lezen van .env: de omgeving wint (de volgorde van main)"
  fase4_env
  merge_media_env ""
  want=$(MEDIA_SANDBOX=uit want_media)
  merge_media_env "$want"
  # shellcheck source=/dev/null
  . "$ENV_FILE"
  check "MEDIA_SANDBOX=uit uit de omgeving wint van aan in .env" test "$MEDIA_SANDBOX" = uit
  want=$(want_media)
  merge_media_env "$want"
  # shellcheck source=/dev/null
  . "$ENV_FILE"
  check "daarna zonder MEDIA_SANDBOX: .env blijft uit" test "$MEDIA_SANDBOX" = uit
  unset MEDIA_SANDBOX
  check "want_media zonder MEDIA_SANDBOX geeft leeg" test "$(want_media)" = ""
  check "want_media met aan" test "$(MEDIA_SANDBOX=aan want_media)" = aan
  for gek in ja Aan " uit" off 1; do
    if out=$(MEDIA_SANDBOX=$gek want_media 2>&1); then
      fout "want_media weigert '$gek'"
    else
      check "want_media weigert '$gek' met uitleg" grep -qF "MEDIA_SANDBOX moet aan of uit zijn (nu: $gek)" <<<"$out"
    fi
  done

  echo "== MEDIA_SANDBOX=ja stopt install.sh op de eerste regel, vóór er iets verandert"
  local fake_bin=$work/bin
  mkdir -p "$fake_bin"
  for cmd in apt-get dpkg docker git systemctl curl install chown mountpoint id hostname; do
    printf '#!/bin/sh\necho "%s $*" >>"%s"\nexit 99\n' "$cmd" "$work/calls.log" >"$fake_bin/$cmd"
    chmod +x "$fake_bin/$cmd"
  done
  local envs env
  for env in "MEDIA_SANDBOX=ja" "OFFICE=aan MEDIA_SANDBOX=nee"; do
    read -r -a envs <<<"$env"
    : >"$work/calls.log"
    cp "$ENV_FILE" "$work/env.voor"
    set +e
    out=$(env "${envs[@]}" INSTALL_LIB='' PLATFORM_ROOT="$PLATFORM_ROOT" PATH="$fake_bin:$PATH" bash "$install_sh" 2>&1)
    code=$?
    set -e
    check "$env: exit 1" test "$code" = 1
    check "$env: met uitleg" grep -qF "MEDIA_SANDBOX moet aan of uit zijn" <<<"$out"
    check "$env: geen enkel commando aangeroepen" test ! -s "$work/calls.log"
    check "$env: .env onveranderd" cmp -s "$ENV_FILE" "$work/env.voor"
  done

  echo "== instellingen: een versie zonder media (terugrollen) bewaart alleen de wens"
  # Alleen wat instellingen buiten .env doet, nagebootst (geen root nodig)
  # shellcheck disable=SC2329  # instellingen roept ze aan
  install() { printf 'install %s\n' "$*" >>"$work/install.log"; }
  # shellcheck disable=SC2329
  chown() { :; }
  # shellcheck disable=SC2329
  mountpoint() { return 1; }
  fase4_env
  : >"$work/install.log"
  without_media
  instellingen "" uit >/dev/null
  check "zonder media: MEDIA_SANDBOX=uit bewaard" has_line "MEDIA_SANDBOX=uit"
  check "zonder media: geen sleutel" not grep -q '^MEDIA_WORKER_KEY=' "$ENV_FILE"
  check "zonder media: geen MAX_UPLOAD_BYTES" not grep -q '^MAX_UPLOAD_BYTES=' "$ENV_FILE"
  check "zonder media: geen mappen van media" not grep -qF "/media" "$work/install.log"
  fase4_env
  : >"$work/install.log"
  instellingen "" "" >/dev/null
  check "zonder media en zonder wens: niets van media in .env" not grep -q '^MEDIA_' "$ENV_FILE"
  fase4_env
  : >"$work/install.log"
  with_media
  instellingen "" "" >/dev/null
  check "met media: de sleutel erbij" is_hex64 "$(value MEDIA_WORKER_KEY)"
  check "met media: MEDIA_SANDBOX=aan" has_line "MEDIA_SANDBOX=aan"
  check "met media: de mappen media en import, van 10001, 750" grep -qxF \
    "install -d -m 750 -o 10001 -g 10001 /tank/platform/media /tank/platform/import" "$work/install.log"
  check "met media: chmod 600" test "$(mode "$ENV_FILE")" = 600
  unset -f install chown mountpoint
  unset MEDIA_SANDBOX DATA_ROOT

  echo "== decide_media stopt nooit (set -e), en kiest de veilige kant"
  decide() {
    (
      set -e
      MEDIA_SANDBOX=$1
      [ -n "$1" ] || unset MEDIA_SANDBOX
      decide_media
      printf '%s|%s|%s\n' "$MEDIA_ON" "$MEDIA_LOCAL" "$MEDIA_REASON"
    )
  }
  without_media
  out=$(decide uit) || fout "decide_media stopte (zonder media)"
  check "versie zonder media: uit met de reden" test "$out" = "0|0|deze versie van het platform heeft nog geen media"
  with_media
  out=$(decide "") || fout "decide_media stopte"
  check "standaard: afgeschermd" test "$out" = "1|0|"
  out=$(decide aan) || fout "decide_media stopte"
  check "aan: afgeschermd" test "$out" = "1|0|"
  out=$(decide uit) || fout "decide_media stopte"
  check "uit: de lokale worker, met de reden" test "$out" = "0|1|MEDIA_SANDBOX=uit: ffmpeg draait zonder afscherming in de app"
  out=$(decide kapot) || fout "decide_media stopte"
  check "een foute waarde in .env: afgeschermd, met de reden" test "$out" = "1|0|MEDIA_SANDBOX in $ENV_FILE moet aan of uit zijn (nu: kapot); media blijft afgeschermd"

  echo "== de compose-bestanden per keuze"
  local d=$SRC/infra/single
  limits_supported() { [ "$LIMITS" = 1 ]; }
  LIMITS=1
  compose_files 0
  check "zoals 0.6.0: alleen compose.yaml" test "${compose[*]}" = "docker compose --project-directory $d --env-file $ENV_FILE -f $d/compose.yaml"
  compose_files 0 0 0
  check "media uit (0.6.0): alleen compose.yaml" test "${compose[*]: -2}" = "-f $d/compose.yaml"
  compose_files 0 1 0
  check "media aan, met grenzen" test "${compose[*]: -4}" = "-f $d/compose.media.yaml -f $d/compose.media-limits.yaml"
  compose_files 1 1 0
  check "office en media, elk met hun grenzen, in die volgorde" test "${compose[*]: -8}" = \
    "-f $d/compose.office.yaml -f $d/compose.office-limits.yaml -f $d/compose.media.yaml -f $d/compose.media-limits.yaml"
  compose_files 0 0 1
  check "MEDIA_SANDBOX=uit: compose.media-local.yaml, geen compose.media.yaml" test "${compose[*]: -2}" = "-f $d/compose.media-local.yaml"
  LIMITS=0
  compose_files 0 1 0
  check "zonder cgroup-grenzen geen compose.media-limits.yaml" test "${compose[*]: -2}" = "-f $d/compose.media.yaml"

  echo "== docker compose config -q voor alle 8 combinaties (de echte bestanden)"
  # De echte infra/single, met .env.example en eenmalige geheimen (alleen in de werkmap)
  local real_env=$work/single.env
  sed -e "s/^OFFICE_JWT_SECRET=$/OFFICE_JWT_SECRET=$(hex)/" \
    -e "s/^OFFICE_LINK_SECRET=$/OFFICE_LINK_SECRET=$(hex)/" \
    -e "s/^MEDIA_WORKER_KEY=$/MEDIA_WORKER_KEY=$(hex)/" "$here/../.env.example" >"$real_env"
  local saved_src=$SRC saved_env=$ENV_FILE
  SRC=$top ENV_FILE=$real_env
  if ! command docker compose version >/dev/null 2>&1; then
    fout "docker compose ontbreekt: de 8 combinaties zijn niet gecontroleerd"
  else
    for office in 0 1; do
      for media in aan uit; do
        for limits in 0 1; do
          LIMITS=$limits
          if [ "$media" = aan ]; then compose_files "$office" 1 0; else compose_files "$office" 0 1; fi
          name="office $office, media $media, grenzen $limits"
          files=("${compose[@]:1}")
          if command docker "${files[@]}" config -q 2>"$work/config.err"; then
            ok "config -q: $name"
          else
            fout "config -q: $name ($(head -c 300 "$work/config.err"))"
          fi
          command docker "${files[@]}" config --format json 2>/dev/null >"$work/config.json" || true
          out=$(MEDIA=$media OFFICE_ON=$office LIMITS=$limits python3 -I - "$work/config.json" <<'PY'
import json, os, sys

c = json.load(open(sys.argv[1]))
s = c["services"]
p = s["platform"]
env = p.get("environment") or {}
problems = []
if env.get("PLATFORM_MEDIA_ENABLED") != "true":
    problems.append("platform: PLATFORM_MEDIA_ENABLED %r" % env.get("PLATFORM_MEDIA_ENABLED"))
if env.get("PLATFORM_MAX_UPLOAD_BYTES") != "53687091200":
    problems.append("platform: PLATFORM_MAX_UPLOAD_BYTES %r" % env.get("PLATFORM_MAX_UPLOAD_BYTES"))
if p.get("stop_grace_period") not in ("30s", 30000000000):
    problems.append("platform: stop_grace_period %r" % p.get("stop_grace_period"))
if os.environ["MEDIA"] == "aan":
    w = s.get("media")
    if not w:
        problems.append("geen dienst media")
    else:
        if w.get("user") != "media":
            problems.append("media: user %r" % w.get("user"))
        if w.get("network_mode") != "none":
            problems.append("media: network_mode %r" % w.get("network_mode"))
        if env.get("PLATFORM_MEDIA_WORKER") != "/run/media/worker.sock":
            problems.append("platform: PLATFORM_MEDIA_WORKER %r" % env.get("PLATFORM_MEDIA_WORKER"))
        limited = w.get("mem_limit") is not None
        if limited != (os.environ["LIMITS"] == "1"):
            problems.append("media: mem_limit %r" % w.get("mem_limit"))
else:
    if "media" in s:
        problems.append("MEDIA_SANDBOX=uit, maar er is een dienst media")
    if "PLATFORM_MEDIA_WORKER" in env:
        problems.append("MEDIA_SANDBOX=uit, maar platform krijgt PLATFORM_MEDIA_WORKER")
if ("office" in s) != (os.environ["OFFICE_ON"] == "1"):
    problems.append("office %r" % ("office" in s))
print("; ".join(problems) or "ok")
PY
          ) || out="(geen uitvoer van compose config)"
          check "inhoud: $name ($out)" test "$out" = ok
        done
      done
    done
  fi
  SRC=$saved_src ENV_FILE=$saved_env
  LIMITS=1

  echo "== compose.media.yaml: stop_grace_period 30s voor platform, user media voor de worker"
  out=$(python3 -I - "$here/../compose.media.yaml" <<'PY'
import re, sys

text = open(sys.argv[1]).read()
blocks = re.split(r"^  (\w[\w-]*):\n", text.split("\nservices:\n", 1)[1].split("\nvolumes:\n")[0], flags=re.M)
services = dict(zip(blocks[1::2], blocks[2::2]))
platform, media = services.get("platform", ""), services.get("media", "")
print(
    "ok"
    if re.search(r"^    stop_grace_period: 30s\b", platform, re.M)
    and re.search(r"^    user: media\b", media, re.M)
    else "platform of media mist zijn regel"
)
PY
  )
  check "zoals Keuze 47 ($out)" test "$out" = ok

  # --- de nep-Docker voor media_start, watchers en main -------------------------------------
  # Een image-opslag: elke build geeft een nieuwe id; de worker draait op de id van zijn laatste
  # start; `image rm` van een id die een container nog gebruikt faalt, zoals bij de echte Docker
  LOG=$work/docker.log
  APP=""           # de id van platform:local
  IMAGES=" "       # alle ids van de app die er zijn
  MEDIA_USES=""    # de id waarop de container media draait (leeg: geen container)
  BUILDS=0
  PLATFORM_UP=1    # draait er al een app (voor watchers en checkpoint)
  UP_MEDIA_RC=0    # wat `compose up media` teruggeeft
  EXEC_RC=0        # wat `compose exec` teruggeeft
  EXEC_OUT=""      # wat `compose exec … media-status --sessions` print
  docker() {
    printf 'docker %s\n' "$*" >>"$LOG"
    local last=${!#}
    case "$1" in
      image)
        case "$2" in
          inspect) [ -z "$APP" ] || printf '%s\n' "$APP" ;;
          rm)
            [ "$last" != "$MEDIA_USES" ] || return 1
            IMAGES=${IMAGES/ $last / }
            ;;
        esac
        ;;
      ps)
        case "$*" in *service=platform*) [ "$PLATFORM_UP" = 0 ] || echo c-app ;; esac
        ;;
      compose)
        case "$*" in
          *" up -d --build --remove-orphans platform")
            BUILDS=$((BUILDS + 1))
            APP=sha256:app-$BUILDS
            IMAGES="$IMAGES$APP "
            # --remove-orphans: zonder compose.media.yaml is media een wees
            case "$*" in *compose.media.yaml*) ;; *) MEDIA_USES="" ;; esac
            ;;
          *" up -d --no-deps media")
            [ "$UP_MEDIA_RC" = 0 ] || return "$UP_MEDIA_RC"
            MEDIA_USES=$APP
            ;;
          *" exec -T platform python -m app.cli media-status --sessions")
            [ "$EXEC_RC" = 0 ] || return "$EXEC_RC"
            printf '%s\n' "$EXEC_OUT"
            ;;
          *" exec -T platform python -m app.cli media-status --wait 20")
            [ "$EXEC_RC" = 0 ] || return "$EXEC_RC"
            echo "Media: aan (afgeschermd, ffmpeg 7.1.5)"
            ;;
          *" exec "*) return "$EXEC_RC" ;;
        esac
        ;;
    esac
  }
  reset_docker() {
    : >"$LOG"
    APP="" IMAGES=" " MEDIA_USES="" BUILDS=0 PLATFORM_UP=1 UP_MEDIA_RC=0 EXEC_RC=0 EXEC_OUT=""
  }
  line_of() { grep -nF -- "$1" "$LOG" | head -n 1 | cut -d: -f1; }
  before() {
    local a b
    a=$(line_of "$1") b=$(line_of "$2")
    [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]
  }

  echo "== media_start stopt nooit (set -e), ook niet als Docker faalt (Keuze 47)"
  # Zoals main: set -e aan, en media_start niet in een if, && of || (daarin doet set -e niets)
  start_media() {
    (
      set -e
      MEDIA_ON=$1 MEDIA_LOCAL=$2 MEDIA_REASON=""
      media_start
      printf 'REASON=%s\n' "$MEDIA_REASON"
    ) >"$work/start.out" 2>&1
  }
  reset_docker
  compose_files 0 1 0
  UP_MEDIA_RC=1
  start_media 1 0 || true
  check "mislukte start van de worker: het script loopt door" grep -q '^REASON=' "$work/start.out"
  check "met de reden" grep -qxF "REASON=de mediaworker kon niet starten (de melding van Docker staat hierboven)" "$work/start.out"
  check "en de nooduitgang" grep -qF "Nooduitgang: MEDIA_SANDBOX=uit" "$work/start.out"
  check "dan geen media-status" not grep -qF "media-status" "$LOG"
  reset_docker
  compose_files 0 1 0
  EXEC_RC=1
  start_media 1 0 || true
  check "media-status faalt: het script loopt door" grep -q '^REASON=$' "$work/start.out"
  check "met een waarschuwing" grep -qF "Let op: media meldt een probleem" "$work/start.out"
  reset_docker
  compose_files 0 1 0
  start_media 1 0 || true
  check "gelukt: de worker en dan media-status --wait 20" before "up -d --no-deps media" "media-status --wait 20"
  check "de regel van media-status staat in de uitvoer" grep -qxF "Media: aan (afgeschermd, ffmpeg 7.1.5)" "$work/start.out"
  reset_docker
  compose_files 0 0 1
  start_media 0 1 || true
  check "MEDIA_SANDBOX=uit: geen container media" not grep -qF "up -d --no-deps media" "$LOG"
  check "MEDIA_SANDBOX=uit: wel media-status" grep -qF "media-status --wait 20" "$LOG"
  reset_docker
  compose_files 0
  start_media 0 0 || true
  check "zonder media: Docker niet aangeroepen" test ! -s "$LOG"

  echo "== vóór het vervangen: wie kijkt er (media-status --sessions, alleen een waarschuwing)"
  reset_docker
  compose_files 0 1 0
  EXEC_OUT=$'Media: aan (afgeschermd, ffmpeg 7.1.5)\nLet op: er kijkt nu 1 persoon. Bij het bijwerken staat de stroom tot ±60 s stil; de speler probeert het zelf opnieuw.\n  Bert: Film (transcode 720p)'
  out=$(watchers)
  check "de waarschuwing en wie" test "$out" = $'Let op: er kijkt nu 1 persoon. Bij het bijwerken staat de stroom tot ±60 s stil; de speler probeert het zelf opnieuw.\n  Bert: Film (transcode 720p)'
  EXEC_OUT=$'Media: aan (afgeschermd, ffmpeg 7.1.5)\nKijkers: niemand'
  check "niemand: niets" test -z "$(watchers)"
  EXEC_RC=2
  out=$(watchers) || fout "watchers faalde"
  check "een oude app zonder het commando: niets, en geen fout" test -z "$out"
  reset_docker
  PLATFORM_UP=0
  compose_files 0 1 0
  watchers >/dev/null
  check "geen app: niet gevraagd" not grep -qF "exec" "$LOG"

  echo "== main: de volgorde, en na twee updates één image van de app (R18)"
  # Alles van main wat de machine raakt, nagebootst; de rest is het echte install.sh
  check_host() { :; }
  pakketten() { :; }
  docker_setup() { :; }
  broncode() { :; }
  instellingen() {
    DATA_ROOT=$work/data PORT=18999 HTTPS_PORT=18998
    [ -z "$2" ] || MEDIA_SANDBOX=$2
  }
  decide_office() { OFFICE_ON=0 OFFICE_IMG="" OFFICE_REASON="OFFICE=uit in $ENV_FILE"; }
  wait_health() { :; }
  hostname() { echo 127.0.0.1; }
  run_main() {
    (
      set -e
      [ -z "${1-}" ] || export MEDIA_SANDBOX=$1
      main
      printf 'STAAT app=%s media=%s images=%s\n' "$APP" "$MEDIA_USES" "$IMAGES"
    ) >"$work/main.out" 2>&1 || true
    sed -n 's/^STAAT //p' "$work/main.out"
  }
  with_media
  reset_docker
  # De eerste installatie (nog geen app, niemand kijkt)
  PLATFORM_UP=0
  state=$(run_main)
  check "installatie: één image, de worker draait erop" test "$state" = "app=sha256:app-1 media=sha256:app-1 images= sha256:app-1 "
  # Bijwerken, twee keer: de oude worker draait op het oude image tot media_start
  APP=sha256:app-1 MEDIA_USES=sha256:app-1 IMAGES=" sha256:app-1 " BUILDS=1 PLATFORM_UP=1
  EXEC_OUT=$'Let op: er kijkt nu 1 persoon. Bij het bijwerken staat de stroom tot ±60 s stil; de speler probeert het zelf opnieuw.\n  Bert: Film (transcode 720p)'
  : >"$LOG"
  state=$(run_main)
  check "eerste update: wie kijkt, vóór de app vervangen wordt" before "media-status --sessions" "up -d --build --remove-orphans platform"
  check "de waarschuwing staat in de uitvoer" grep -qF "Let op: er kijkt nu 1 persoon" "$work/main.out"
  check "eerst de app, dan de worker" before "up -d --build --remove-orphans platform" "up -d --no-deps media"
  check "de worker, dan media-status --wait 20" before "up -d --no-deps media" "media-status --wait 20"
  check "het oude image pas na de worker weg" before "up -d --no-deps media" "image rm sha256:app-1"
  check "eerste update: één image, de oude id weg" test "$state" = "app=sha256:app-2 media=sha256:app-2 images= sha256:app-2 "
  APP=sha256:app-2 MEDIA_USES=sha256:app-2 IMAGES=" sha256:app-2 " BUILDS=2
  : >"$LOG"
  state=$(run_main)
  check "tweede update: één image platform:local, de oude id weg" test "$state" = "app=sha256:app-3 media=sha256:app-3 images= sha256:app-3 "
  check "de eindtekst: Media: aan (afgeschermd)" grep -qxF "Media: aan (afgeschermd)." "$work/main.out"
  # Lukt de worker niet, dan stopt het script niet en blijft het oude image (in gebruik)
  APP=sha256:app-3 MEDIA_USES=sha256:app-3 IMAGES=" sha256:app-3 " BUILDS=3 UP_MEDIA_RC=1
  : >"$LOG"
  state=$(run_main)
  check "de worker start niet: main loopt door tot de eindtekst" grep -qF "Media: aan (afgeschermd), maar de mediaworker kon niet starten" "$work/main.out"
  check "(de nieuwe app draait)" test "${state%% *}" = "app=sha256:app-4"
  UP_MEDIA_RC=0
  # MEDIA_SANDBOX=uit: geen container media meer (--remove-orphans), de lokale worker
  APP=sha256:app-4 MEDIA_USES=sha256:app-4 IMAGES=" sha256:app-4 " BUILDS=4
  : >"$LOG"
  state=$(run_main uit)
  check "MEDIA_SANDBOX=uit: compose.media-local.yaml" grep -qF "compose.media-local.yaml" "$LOG"
  check "MEDIA_SANDBOX=uit: geen compose.media.yaml" not grep -qF "compose.media.yaml " "$LOG"
  check "MEDIA_SANDBOX=uit: geen up van media" not grep -qF "up -d --no-deps media" "$LOG"
  check "MEDIA_SANDBOX=uit: de container is weg en alleen het nieuwe image blijft" test "$state" = "app=sha256:app-5 media= images= sha256:app-5 "
  check "MEDIA_SANDBOX=uit: de eindtekst zegt hoe het weer aan gaat" grep -qF "Weer afgeschermd:" "$work/main.out"
  # Terug naar 0.6.0: geen media in die versie, en ook geen container
  without_media
  APP=sha256:app-5 MEDIA_USES="" IMAGES=" sha256:app-5 " BUILDS=5
  : >"$LOG"
  state=$(run_main)
  check "0.6.0: geen enkel media-bestand" not grep -qF "compose.media" "$LOG"
  check "0.6.0: geen media-status na de start" not grep -qF "media-status --wait" "$LOG"
  check "0.6.0: de eindtekst zegt waarom" grep -qF "Media: uit, want deze versie van het platform heeft nog geen media." "$work/main.out"
  check "0.6.0: één image" test "$state" = "app=sha256:app-6 media= images= sha256:app-6 "

  echo "== geen geheim in de uitvoer, en elke exec krijgt </dev/null"
  check "de sleutel staat niet in de uitvoer van main" not grep -qE '[0-9a-f]{64}' "$work/main.out"
  out=$(grep -nE '(docker run|compose\[@\]\}" exec) ' "$install_sh" | grep -v '</dev/null' || true)
  check "elke docker run en compose exec krijgt </dev/null" test -z "$out"

  einde
}

# ============================================================================================
# Deel 2: de installatietest (--stappen)
# ============================================================================================

stappen() {
  echo "De installatietest komt in de volgende commit." >&2
  exit 2
}

case "${1-}" in
  "") functies ;;
  --stappen) stappen ;;
  *)
    echo "Gebruik: $0 [--stappen]" >&2
    exit 2
    ;;
esac
