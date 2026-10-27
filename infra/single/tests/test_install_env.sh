#!/usr/bin/env bash
# De functies van install.sh voor .env en het image van office (bouwontwerp fase 4, §2 O6).
#
#   bash infra/single/tests/test_install_env.sh
#
# Laadt install.sh met INSTALL_LIB=1: er wordt niets geïnstalleerd en er draait geen Docker.
# `docker` is hier een nepfunctie die elke aanroep opschrijft en per scenario antwoordt. Draait
# in de job infra van CI.
#
# De variabelen en functies komen uit install.sh (shellcheck volgt de bron niet in CI):
# shellcheck disable=SC2034,SC2153,SC2154
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
install_sh=$here/../install.sh
compose_office=$here/../compose.office.yaml
env_example=$here/../.env.example
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

export PLATFORM_ROOT=$work/root INSTALL_LIB=1
unset OFFICE OFFICE_IMAGE
# shellcheck source=/dev/null
. "$install_sh"
mkdir -p "$PLATFORM_ROOT" "$SRC/infra/single/office"

fails=0
ok() { printf 'ok    %s\n' "$1"; }
fout() {
  printf 'FOUT  %s\n' "$1"
  fails=$((fails + 1))
}
check() {
  local name=$1
  shift
  if "$@"; then ok "$name"; else fout "$name"; fi
}
has_line() { grep -qxF "$1" "$ENV_FILE"; }
value() { sed -n "s/^$1=//p" "$ENV_FILE"; }
is_hex64() { [[ $1 =~ ^[0-9a-f]{64}$ ]]; }
mode() { stat -c %a "$1"; }

fase3_env() {
  cat >"$ENV_FILE" <<'ENV'
DATA_ROOT=/tank/platform
NODE_NAME=node1
PORT=80
HTTPS_PORT=443
SECURE_COOKIES=false
ENV
  chmod 644 "$ENV_FILE"
}

# --- de nep-docker -------------------------------------------------------------------------

LOG=$work/docker.log
LOCAL=" "          # images die er lokaal zijn (namen met spaties eromheen)
PULLABLE=" "       # images die een pull ophaalt
DRIVER=overlay2
LIMITS="true true"
RUNNING_ID=""      # de container van DS, of leeg
RUNNING_IMAGE=""
IMAGES_TABLE=""    # uitvoer van `docker images` voor office_cleanup
USED_IDS=""        # images die containers gebruiken

docker() {
  printf 'docker %s\n' "$*" >>"$LOG"
  local last=${!#}
  case "$1" in
    image)
      case "$2" in
        inspect)
          case "$LOCAL" in *" $last "*) ;; *) return 1 ;; esac
          [ "$3" != --format ] || printf 'sha256:id-of-%s\n' "$last"
          ;;
        rm) return 0 ;;
      esac
      ;;
    pull)
      case "$PULLABLE" in *" $last "*) LOCAL="$LOCAL$last " ;; *) return 1 ;; esac
      ;;
    tag) LOCAL="$LOCAL$3 " ;;
    info)
      case "$last" in
        '{{.Driver}}') echo "$DRIVER" ;;
        '{{.MemoryLimit}} {{.CPUCfsQuota}}') echo "$LIMITS" ;;
        '{{.DockerRootDir}}') echo "$work" ;;
      esac
      ;;
    ps)
      case "$*" in
        *service=office*) [ -z "$RUNNING_ID" ] || echo "$RUNNING_ID" ;;
        "ps -aq") [ -z "$USED_IDS" ] || echo "c1" ;;
      esac
      ;;
    inspect)
      case "$*" in
        *'{{.State.Running}}'*) echo true ;;
        *'{{.Config.Image}}'*) echo "$RUNNING_IMAGE" ;;
        *'{{.Image}}'*) printf '%s\n' "$USED_IDS" ;;
      esac
      ;;
    images) printf '%s\n' "$IMAGES_TABLE" ;;
  esac
}
# Geen echte pauzes tussen de pogingen, en de ruimte apart getest (hieronder)
sleep() { :; }
OFFICE_MIN_FREE_GB=0

reset_docker() {
  : >"$LOG"
  LOCAL=" " PULLABLE=" " DRIVER=overlay2 LIMITS="true true" RUNNING_ID="" RUNNING_IMAGE=""
  IMAGES_TABLE="" USED_IDS=""
  unset OFFICE_IMAGE
}

HUB="$OFFICE_REPO@$OFFICE_DIGEST"
MIRROR="mirror.gcr.io/$OFFICE_REPO@$OFFICE_DIGEST"
NAME="$OFFICE_REPO:$OFFICE_TAG@$OFFICE_DIGEST"

# --- .env ----------------------------------------------------------------------------------

echo "== merge_env op een .env uit fase 3"
fase3_env
merge_env ""
check "OFFICE=aan erbij" has_line "OFFICE=aan"
jwt=$(value OFFICE_JWT_SECRET)
link=$(value OFFICE_LINK_SECRET)
check "OFFICE_JWT_SECRET is 64 hex-tekens" is_hex64 "$jwt"
check "OFFICE_LINK_SECRET is 64 hex-tekens" is_hex64 "$link"
check "twee verschillende geheimen" test "$jwt" != "$link"
check "de instellingen van fase 3 staan er nog" has_line "DATA_ROOT=/tank/platform"
check "precies acht regels" test "$(wc -l <"$ENV_FILE")" = 8
check "chmod 600" test "$(mode "$ENV_FILE")" = 600

echo "== een tweede run: een bestaand geheim blijft staan"
merge_env ""
check "OFFICE_JWT_SECRET ongewijzigd" test "$(value OFFICE_JWT_SECRET)" = "$jwt"
check "OFFICE_LINK_SECRET ongewijzigd" test "$(value OFFICE_LINK_SECRET)" = "$link"
check "geen dubbele sleutels" test "$(grep -c '^OFFICE' "$ENV_FILE")" = 3

echo "== merge_env uit op een .env met OFFICE=aan"
merge_env uit
check "OFFICE=uit" has_line "OFFICE=uit"
check "geen OFFICE=aan meer" test "$(grep -c '^OFFICE=aan$' "$ENV_FILE")" = 0
check "de geheimen blijven" test "$(value OFFICE_JWT_SECRET)" = "$jwt"
merge_env aan
check "en weer aan" has_line "OFFICE=aan"

echo "== een .env zonder regeleinde na de laatste regel (met de hand bewerkt)"
fase3_env
printf '%s' "$(cat "$ENV_FILE")" >"$ENV_FILE"
merge_env ""
check "de laatste instelling blijft zoals hij was" has_line "SECURE_COOKIES=false"
check "OFFICE=aan op een eigen regel" has_line "OFFICE=aan"
check "OFFICE_JWT_SECRET op een eigen regel" is_hex64 "$(value OFFICE_JWT_SECRET)"
merge_env ""
check "na een tweede run OFFICE=aan één keer" test "$(grep -c 'OFFICE=aan' "$ENV_FILE")" = 1
check "en elke sleutel van office op een eigen regel" test "$(grep -c '^OFFICE' "$ENV_FILE")" = 3
check "precies acht regels" test "$(wc -l <"$ENV_FILE")" = 8

echo "== want_office vóór het lezen van .env (de volgorde van main, M13)"
fase3_env
merge_env ""
# Zo leest main: eerst de omgeving, dan .env schrijven en lezen
want=$(OFFICE=uit want_office)
merge_env "$want"
OFFICE=uit
# shellcheck disable=SC1090
. "$ENV_FILE"
check "OFFICE=uit uit de omgeving wint van OFFICE=aan in .env" test "$OFFICE" = uit
# Ter vergelijking: wie .env eerst leest, verliest de waarde uit de omgeving
set_key OFFICE aan
OFFICE=uit
# shellcheck disable=SC1090
. "$ENV_FILE"
check "(zonder want_office zou .env winnen)" test "$OFFICE" = aan
unset OFFICE
check "want_office zonder OFFICE geeft leeg" test "$(want_office)" = ""
check "want_office met aan" test "$(OFFICE=aan want_office)" = aan
for gek in ja Aan 1 "uit "; do
  if msg=$(OFFICE=$gek want_office 2>&1); then
    fout "want_office weigert '$gek'"
  else
    check "want_office weigert '$gek' met uitleg" grep -q "OFFICE moet aan of uit zijn" <<<"$msg"
  fi
done

echo "== OFFICE=ja stopt install.sh op de eerste regel, vóór er iets gebeurt"
fake_bin=$work/bin
mkdir -p "$fake_bin"
for cmd in apt-get dpkg docker git systemctl curl install chown mountpoint; do
  printf '#!/bin/sh\necho "%s $*" >>"%s"\nexit 99\n' "$cmd" "$work/calls.log" >"$fake_bin/$cmd"
  chmod +x "$fake_bin/$cmd"
done
: >"$work/calls.log"
set +e
out=$(OFFICE=ja INSTALL_LIB='' PATH="$fake_bin:$PATH" bash "$install_sh" 2>&1)
code=$?
set -e
check "exit 1" test "$code" = 1
check "met uitleg" grep -q "OFFICE moet aan of uit zijn (nu: ja)" <<<"$out"
check "geen enkel commando aangeroepen" test ! -s "$work/calls.log"

echo "== .env.example: de lege geheimen worden gevuld"
cp "$env_example" "$ENV_FILE"
merge_env ""
check "OFFICE_JWT_SECRET gevuld" is_hex64 "$(value OFFICE_JWT_SECRET)"
check "OFFICE_LINK_SECRET gevuld" is_hex64 "$(value OFFICE_LINK_SECRET)"
check "OFFICE=aan blijft" has_line "OFFICE=aan"
check "de pin blijft commentaar" test "$(grep -c '^OFFICE_IMAGE=' "$ENV_FILE")" = 0

echo "== een pin in .env blijft na een tweede run"
fase3_env
pin="onlyoffice/documentserver:9.9.9@sha256:$(printf '1%.0s' $(seq 64))"
printf 'OFFICE_IMAGE=%s\n' "$pin" >>"$ENV_FILE"
merge_env ""
merge_env ""
check "de pin staat er nog, ongewijzigd" has_line "OFFICE_IMAGE=$pin"

# --- het image -----------------------------------------------------------------------------

echo "== office_image met een pin: zoals hij is, niets anders"
reset_docker
LOCAL=" $pin "
OFFICE_IMAGE=$pin
check "geeft de pin" test "$(office_image)" = "$pin"
check "geen pull en geen tag" test "$(grep -c 'docker pull\|docker tag' "$LOG")" = 0
reset_docker
OFFICE_IMAGE=$pin
if office_image >/dev/null; then fout "een pin die er niet is en niet te halen is, faalt"; else ok "een pin die er niet is en niet te halen is, faalt"; fi
check "en valt niet terug op de digest" test "$(grep -c "$OFFICE_DIGEST" "$LOG")" = 0

echo "== office_image zonder pin: eerst Docker Hub, dan de mirror, daarna de naam van Docker Hub"
reset_docker
PULLABLE=" $HUB "
check "van Docker Hub" test "$(office_image)" = "$NAME"
check "naam van Docker Hub erop" grep -qxF "docker tag $HUB $OFFICE_REPO:$OFFICE_TAG" "$LOG"
check "de mirror niet gevraagd" test "$(grep -c "mirror.gcr.io" "$LOG")" = 0
reset_docker
PULLABLE=" $MIRROR "
img=$(office_image)
check "alleen de mirror: dezelfde naam" test "$img" = "$NAME"
check "eerst Docker Hub geprobeerd" grep -qxF "docker pull -q $HUB" "$LOG"
check "na de mirror de naam van Docker Hub erop (M19)" grep -qxF "docker tag $MIRROR $OFFICE_REPO:$OFFICE_TAG" "$LOG"
reset_docker
LOCAL=" $HUB "
check "al lokaal: geen pull" test "$(office_image)" = "$NAME"
check "(geen pull in de log)" test "$(grep -c 'docker pull' "$LOG")" = 0

echo "== de naam van install.sh is precies de standaard van compose.office.yaml"
# shellcheck disable=SC2016  # de $ hoort bij de tekst van compose
default=$(sed -n 's/.*\${OFFICE_IMAGE:-\([^}]*\)}.*/\1/p' "$compose_office")
check "zelfde naam, dus een losse 'docker compose up' maakt office niet opnieuw" test "$default" = "$NAME"
check "e2e/office_ds.sh pint dezelfde digest" grep -q "OFFICE_DIGEST=$OFFICE_DIGEST" "$here/../../../e2e/office_ds.sh"

echo "== een image dat niet te halen is, stopt het script niet (set -e, M13)"
reset_docker
OFFICE=aan
touch "$SRC/infra/single/compose.office.yaml"
decide_office >/dev/null
check "office uit voor deze run" test "$OFFICE_ON" = 0
check "met de reden" grep -q "niet op te halen" <<<"$OFFICE_REASON"
check "drie rondes, Docker Hub en mirror" test "$(grep -c 'docker pull' "$LOG")" = 6
reset_docker
RUNNING_ID=c-office RUNNING_IMAGE=$NAME
decide_office >/dev/null
check "draait er al een DS, dan blijft die" test "$OFFICE_ON:$OFFICE_IMG" = "1:$NAME"
check "met de reden" grep -q "vorige versie" <<<"$OFFICE_REASON"
reset_docker
PULLABLE=" $HUB "
decide_office >/dev/null
check "lukt de pull, dan aan zonder reden" test "$OFFICE_ON:$OFFICE_IMG:$OFFICE_REASON" = "1:$NAME:"

echo "== de keuze van office"
reset_docker
PULLABLE=" $HUB "
OFFICE=uit
decide_office >/dev/null
check "OFFICE=uit: uit, zonder pull" test "$OFFICE_ON:$(grep -c 'docker pull' "$LOG")" = "0:0"
OFFICE=gek RUNNING_ID=c-office RUNNING_IMAGE=$NAME
decide_office >/dev/null
check "een vreemde OFFICE in .env: blijft zoals het was" test "$OFFICE_ON:$OFFICE_IMG" = "1:$NAME"
OFFICE=aan
rm "$SRC/infra/single/compose.office.yaml"
decide_office >/dev/null
check "een versie zonder office: uit" test "$OFFICE_ON" = 0
check "(met die reden)" grep -q "nog geen office" <<<"$OFFICE_REASON"
touch "$SRC/infra/single/compose.office.yaml"

echo "== de controle van de opslagdriver en de ruimte"
for d in overlay2 overlayfs; do check "driver $d mag" driver_ok "$d"; done
for d in vfs btrfs ""; do
  if driver_ok "$d"; then fout "driver '$d' geweigerd"; else ok "driver '$d' geweigerd"; fi
done
reset_docker
DRIVER=vfs RUNNING_ID=c-office RUNNING_IMAGE=$NAME PULLABLE=" $HUB "
out=$(decide_office)
decide_office >/dev/null
check "vfs: office blijft op de vorige versie" test "$OFFICE_ON:$OFFICE_IMG" = "1:$NAME"
check "vfs: geen pull" test "$(grep -c 'docker pull' "$LOG")" = 0
check "vfs: met uitleg" grep -q "overlay2 of overlayfs" <<<"$out"
reset_docker
DRIVER=vfs PULLABLE=" $HUB "
decide_office >/dev/null
check "vfs zonder DS: uit" test "$OFFICE_ON" = 0
reset_docker
OFFICE_MIN_FREE_GB=999999
if out=$(office_preflight); then fout "te weinig ruimte geweigerd"; else ok "te weinig ruimte geweigerd"; fi
check "met uitleg" grep -q "GB vrij voor Docker" <<<"$out"
OFFICE_MIN_FREE_GB=0
check "genoeg ruimte" office_preflight

echo "== de compose-bestanden"
reset_docker
compose_files 0
check "zonder office alleen compose.yaml" test "${compose[*]}" = "docker compose --project-directory $SRC/infra/single --env-file $ENV_FILE -f $SRC/infra/single/compose.yaml"
compose_files 1
check "met office en grenzen" test "${compose[*]: -4}" = "-f $SRC/infra/single/compose.office.yaml -f $SRC/infra/single/compose.office-limits.yaml"
LIMITS="false true"
compose_files 1
check "zonder cgroup-grenzen geen limits-bestand" test "${compose[*]: -2}" = "-f $SRC/infra/single/compose.office.yaml"

echo "== de instelling van DS veranderd?"
cfg=$SRC/infra/single/office/local-production-linux.json
echo '{}' >"$cfg"
check "de eerste keer: ja" office_config_changed
if office_config_changed; then fout "ongewijzigd: nee"; else ok "ongewijzigd: nee"; fi
echo '{"a": 1}' >"$cfg"
check "andere inhoud: ja" office_config_changed

echo "== oude images van DS weg, behalve het huidige, het vorige, de pin en wat in gebruik is"
reset_docker
IMAGES_TABLE="sha256:nu onlyoffice/documentserver 10.0.0.1 <none>
sha256:nu mirror.gcr.io/onlyoffice/documentserver 10.0.0.1 sha256:nu
sha256:vorig onlyoffice/documentserver 9.9.0 <none>
sha256:pin mirror.gcr.io/onlyoffice/documentserver <none> sha256:pin
sha256:gebruikt onlyoffice/documentserver 9.8.0 <none>
sha256:oud onlyoffice/documentserver 9.4.0.1 <none>
sha256:oud2 mirror.gcr.io/onlyoffice/documentserver <none> sha256:oud2"
USED_IDS="sha256:gebruikt"
office_cleanup sha256:nu sha256:vorig sha256:pin >/dev/null
removed=$({ grep '^docker image rm' "$LOG" || true; } | sort | tr '\n' ' ')
check "alleen de twee oude namen" test "$removed" = "docker image rm mirror.gcr.io/onlyoffice/documentserver@sha256:oud2 docker image rm onlyoffice/documentserver:9.4.0.1 "

echo "== curl | bash: geen commando leest de rest van het script van stdin"
# Bij `curl … | bash` leest bash het script van stdin. Een `docker run` of `compose exec` die
# stdin openhoudt, eet de rest van het script op; dan stopt het zonder melding.
readers=$(grep -nE '(docker run|compose\[@\]\}" exec) ' "$install_sh" | grep -v '</dev/null' || true)
check "elke docker run en compose exec krijgt </dev/null" test -z "$readers"
[ -z "$readers" ] || printf '      %s\n' "$readers"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails controle(s) mislukt."
  exit 1
fi
echo "Alle controles geslaagd."
