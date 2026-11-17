#!/usr/bin/env bash
# Zoeken op beeld in install.sh (bouwontwerp fase 6, X3b; §3.11 "Waar install.sh ze haalt", Keuze
# 73 en 84; K1-05, K1-06, K2-19):
#
#   bash infra/single/tests/test_install_beeld.sh
#
# Draait in de job infra van CI. De test draait als root (in CI start hij zichzelf opnieuw met
# sudo -n): de mappen, de eigenaars en de symlinks zijn echt, en de omzetter schrijft echt als uid
# 10004 (setpriv). Docker is een nepfunctie met een logboek, behalve bij `docker compose config`,
# dat de echte compose-bestanden controleert; curl is echt, tegen een eigen HTTP-server op
# 127.0.0.1 met een kleine nep-bron en een eigen models.lock (nep-URL's). De nep-omzetter ziet de
# bron en de uitvoermap alleen zoals de container ze ziet: via de map zelf (een fd, zoals een
# bind-mount), als 10004. Het controleert:
# - want_beeld: alleen aan of uit; een andere waarde stopt install.sh op de eerste regel;
# - .env: zonder ANALYSE (de standaard aan, sinds 0.8.1) of met aan komt BEELD=aan erbij, één
#   keer, als laatste van vijf nieuwe regels; met uit, of met ANALYSE_STANDAARD=uit (zo was het
#   tot en met 0.8.0) en zonder ANALYSE, komt er geen regel BEELD bij (ook niet met BEELD=uit of
#   aan gevraagd); een gevraagde BEELD vervangt hem, de eerste keer aan met BEELD=uit geeft
#   BEELD=uit; een versie zonder de regels van zoeken op beeld in models.lock zet geen BEELD;
#   .env.example heeft alleen `# BEELD=aan`;
# - de regels van models.lock (ook de echte: vijf bronnen, vijf bestanden van de uitkomst en de
#   ruimte die dat vraagt); een regel die niet klopt, haalt niets;
# - BEELD=uit haalt niets en zet niets om (geen curl, geen omzetter, geen map);
# - de eerste keer: elke bron met curl --max-filesize, grootte en sha256, in een eigen map van
#   root onder MODELLEN_ROOT (0755, de bestanden 0644: 10004 kan ze lezen); de uitvoermap leeg en
#   van 10004 (0700); de omzetter via compose met --profile omzetten, `run --rm --no-deps -T`,
#   zonder stdin en zonder de sleutel in zijn omgeving; daarna het model in MODELLEN_ROOT/
#   siglip2-b32-256 (0755 van root, de bestanden 0644 van root, gelijk aan de uitkomst), alleen
#   de bestanden van de lock, en de bron en de tijdelijke map weg (ook die van een afgebroken run);
# - een tweede run slaat een goed model over; een nieuwe uitkomst in de lock zet opnieuw om en
#   vervangt de map in één keer;
# - een verkeerde sha256 of een te groot bestand (bron of uitkomst), geen server, een omzetter die
#   faalt, een uitkomst die een symlink, een fifo of er niet is: geweigerd, niet fataal, niets
#   achter, en een oud model blijft staan;
# - te weinig ruimte (df op MODELLEN_ROOT en op DATA_ROOT, K2-19): niets gehaald, niet fataal;
# - een doel dat een symlink of geen map is, of een bestand erin dat een symlink is: geweigerd,
#   zonder het te raken (K1-06);
# - main met ANALYSE_STANDAARD=uit en zonder ANALYSE doet precies wat install.sh van 0.8.0 doet,
#   ook met de regels van zoeken op beeld in models.lock; met ANALYSE=uit dezelfde aanroepen en
#   mappen, de .env van 0.8.0 met alleen ANALYSE=uit erbij (met ANALYSE_STANDAARD=uit dezelfde
#   .env), en niets over beeld;
# - main zonder ANALYSE op een .env van 0.8.0 (de update naar 0.8.1): de stap BEELD loopt, en de
#   eindtekst zegt dat elke gebruiker gezichten en zoeken op beeld zelf aanzet; de eerste keer met
#   BEELD=uit: BEELD=uit in .env, geen stap, alleen het model van gezichten, en het blijft uit;
# - main met aan: de stap na de app en vóór de container analyse, die met --force-recreate start
#   als het model nieuw is; de eindtekst; een omzetting die mislukt of te weinig ruimte: exit 0,
#   Klaar., de container start toch; BEELD=uit: geen stap, het model blijft; terug naar 0.8.0 en
#   weer vooruit: het model blijft en er wordt niets opnieuw gehaald;
# - analyse-omzetten in de echte compose-bestanden (compose config, K1-05): geen netwerk, geen
#   variabele met KEY, geen analyse-run, geen /models, de bron alleen-lezen, alleen in het profiel
#   omzetten, en de sleutel nergens in de dienst.
#
# De variabelen en functies komen uit install.sh (shellcheck volgt de bron niet in CI):
# shellcheck disable=SC2034,SC2153,SC2154,SC2016
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
top=$(cd "$here/../../.." && pwd)
install_sh=$here/../install.sh
# 0.8.0: de laatste commit vóór A0 (zoals test_install_analyse.sh)
BACK080_SHA=59e0250ba63d31c41b92f7ec8430446810db8a74

if [ "$(id -u)" != 0 ]; then
  if sudo -n true 2>/dev/null; then
    exec sudo -n bash "$0" "$@"
  fi
  echo "FOUT  deze test draait als root (of met sudo -n): de eigenaars en symlinks zijn echt"
  exit 1
fi

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
einde() {
  echo
  if [ "$fails" -gt 0 ]; then
    echo "$fails controle(s) mislukt ($oks goed)."
    exit 1
  fi
  echo "Alle $oks controles geslaagd."
}

work=$(mktemp -d)
server_pid=""
opruimen() {
  [ -z "$server_pid" ] || kill "$server_pid" 2>/dev/null || true
  rm -rf "$work"
}
trap opruimen EXIT
chmod 755 "$work"
umask 022

# Het script van 0.8.0, uit git (de job infra haalt de hele geschiedenis op)
if ! git -c safe.directory='*' -C "$top" show "$BACK080_SHA:infra/single/install.sh" >"$work/install-080.sh" 2>/dev/null; then
  fout "install.sh van 0.8.0 ($BACK080_SHA) niet in git: haal de hele geschiedenis op"
  einde
fi

# --- het nepnetwerk: een HTTP-server op 127.0.0.1 met een gezichtsmodel en de nep-bron ---------
M=siglip2-b32-256
net=$work/net
mkdir -p "$net/$M/bron"
head -c 3000 /dev/urandom >"$net/a.onnx"
head -c 6000 /dev/urandom >"$net/$M/bron/model.safetensors"
printf '{"nep": "config"}\n' >"$net/$M/bron/config.json"
# Wat de nep-omzetter schrijft (de uitkomst van X3a heeft vijf bestanden; drie is genoeg)
uitkomst=$work/uitkomst
mkdir -p "$uitkomst"
head -c 4000 /dev/urandom >"$uitkomst/beeld.int8.onnx"
printf '{"nep": "meta"}\n' >"$uitkomst/meta.json"
cp "$top/api/app/analyse/licenties/siglip2-Apache-2.0.txt" "$uitkomst/"
cat >"$work/server.py" <<'PY'
import http.server, sys
root, portfile, log = sys.argv[1:4]
class H(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=root, **k)
    def log_message(self, fmt, *args):
        with open(log, "a") as f:
            f.write(self.path + "\n")
s = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
with open(portfile + ".tmp", "w") as f:
    f.write(str(s.server_address[1]))
import os
os.replace(portfile + ".tmp", portfile)
s.serve_forever()
PY
start_server() {
  rm -f "$work/port"
  python3 -I "$work/server.py" "$net" "$work/port" "$work/http.log" &
  server_pid=$!
  for _ in $(seq 1 50); do [ -s "$work/port" ] && break; sleep 0.1; done
  port=$(cat "$work/port")
  base=http://127.0.0.1:$port
}
start_server
export no_proxy=127.0.0.1 NO_PROXY=127.0.0.1
unset http_proxy HTTP_PROXY ALL_PROXY all_proxy

sha() { sha256sum "$1" | cut -d' ' -f1; }
size() { stat -c %s "$1"; }

# --- install.sh als bibliotheek ---------------------------------------------------------------
export PLATFORM_ROOT=$work/root INSTALL_LIB=1
unset OFFICE OFFICE_IMAGE MEDIA_SANDBOX PLAATSEN ANALYSE ANALYSE_WORKER_KEY MODELLEN_ROOT SLEUTELS_ROOT BEELD
# shellcheck source=/dev/null
. "$install_sh"
set +e
set -uo pipefail

LOG=$work/docker.log
has_line() { grep -qxF "$1" "$ENV_FILE"; }
value() { sed -n "s/^$1=//p" "$ENV_FILE"; }
owner_mode() { stat -c '%u:%g %a' "$1"; }

# De broncode: alleen wat de analyse nodig heeft; de eigen models.lock wijst naar het nepnetwerk:
# een model van gezichten, twee bronnen van zoeken op beeld en de drie bestanden van de uitkomst
LOCK=$SRC/api/app/analyse/models.lock
write_lock() {
  cat >"$LOCK" <<LOCK
# nepmodellen van test_install_beeld.sh
$(sha "$net/a.onnx") $(size "$net/a.onnx") a.onnx $base/a.onnx
# zoeken op beeld
$(sha "$net/$M/bron/model.safetensors") $(size "$net/$M/bron/model.safetensors") $M/bron/model.safetensors $base/$M/bron/model.safetensors
$(sha "$net/$M/bron/config.json") $(size "$net/$M/bron/config.json") $M/bron/config.json $base/$M/bron/config.json  # met een opmerking
$(sha "$uitkomst/beeld.int8.onnx") $(size "$uitkomst/beeld.int8.onnx") $M/beeld.int8.onnx -
$(sha "$uitkomst/meta.json") $(size "$uitkomst/meta.json") $M/meta.json -
$(sha "$uitkomst/siglip2-Apache-2.0.txt") $(size "$uitkomst/siglip2-Apache-2.0.txt") $M/siglip2-Apache-2.0.txt -
LOCK
}
make_src() {
  rm -rf "$SRC"
  mkdir -p "$SRC/infra/single" "$SRC/api/app/analyse"
  cp "$top/infra/single/compose.yaml" "$top/infra/single/compose.analyse.yaml" \
    "$top/infra/single/compose.analyse-limits.yaml" "$SRC/infra/single/"
  cp -r "$top/api/app/analyse/licenties" "$SRC/api/app/analyse/"
  write_lock
}

# Een .env zoals die van Jonas: zonder ANALYSE
base_env() {
  mkdir -p "$PLATFORM_ROOT"
  cat >"$ENV_FILE" <<ENV
DATA_ROOT=$work/data
NODE_NAME=node1
PORT=80
HTTPS_PORT=443
SECURE_COOKIES=false
OFFICE=uit
MEDIA_SANDBOX=aan
PLAATSEN=aan
ENV
  chmod 600 "$ENV_FILE"
}
fresh() {
  rm -rf "$PLATFORM_ROOT" "$work/data"
  mkdir -p "$PLATFORM_ROOT" "$work/data"
  chown 10001:10001 "$work/data"
  chmod 750 "$work/data"
  make_src
  base_env
  DATA_ROOT=$work/data
  MODELLEN_ROOT=$PLATFORM_ROOT/modellen
  SLEUTELS_ROOT=$PLATFORM_ROOT/sleutels/gezichten
}

# shellcheck disable=SC2329  # install.sh roept ze aan
curl() {
  printf 'curl %s\n' "$*" >>"$work/curl.log"
  command curl "$@"
}

# Een bestand en een map van de host: een symlink ernaar mag het script niet volgen
host=$work/host
mkdir -p "$host/map"
printf 'van de host\n' >"$host/bestand"
chmod 600 "$host/bestand"
chmod 755 "$host" "$host/map"
host_same() {
  [ "$(cat "$host/bestand")" = "van de host" ] &&
    [ "$(stat -c '%u %a' "$host/bestand" "$host/map" | tr '\n' ' ')" = "0 600 0 755 " ] &&
    [ -z "$(ls -A "$host/map")" ]
}

# De nep-omzetter (de dienst analyse-omzetten, Keuze 84 stap 5). Schrijft in omzet.log wat hij
# kreeg: OMZET_BRON en OMZET_UIT, de eigenaar en rechten van de bron en de uitvoermap, hoeveel er
# al in de uitvoermap stond, of stdin leeg was en of de sleutel in zijn omgeving stond. Daarna,
# als 10004 en alleen via de twee mappen zelf (fd 3 en 4, zoals de bind-mounts van de container):
# leest hij elke bron (de sha256 in omzet.gelezen) en schrijft hij de uitkomst, of iets wat niet
# klopt (OMZET_MODE). OMZET_RC != 0: een bron die niet klopt (exit 2 van de echte), niets geschreven.
OMZET_RC=0 OMZET_MODE=goed
omzetter() {
  local key in
  key=$(value ANALYSE_WORKER_KEY)
  {
    printf 'bron=%s uit=%s\n' "${OMZET_BRON-}" "${OMZET_UIT-}"
    stat -c 'stat %n %u:%g %a %F' -- "$OMZET_BRON" "$OMZET_BRON"/* "$OMZET_UIT"
    printf 'al in uit: %s\n' "$(find "$OMZET_UIT" -mindepth 1 -printf . | wc -c)"
    in=$(cat)
    printf 'stdin: %s\n' "${#in}"
    if [ -n "$key" ] && env | grep -qF -- "$key"; then echo "sleutel in de omgeving"; fi
  } >>"$work/omzet.log" 2>&1
  if [ "$OMZET_RC" != 0 ]; then
    echo "omzetten: de bron klopt niet" >&2
    return "$OMZET_RC"
  fi
  setpriv --reuid=10004 --regid=10003 --clear-groups -- sh -c '
    set -e
    for f in /proc/self/fd/3/*; do sha256sum <"$f" | cut -d" " -f1; done >"$4"
    cp "$1"/* /proc/self/fd/4/
    case $2 in
      verkeerd) head -c "$(stat -c %s "$1/beeld.int8.onnx")" /dev/urandom >/proc/self/fd/4/beeld.int8.onnx ;;
      groot) printf x >>/proc/self/fd/4/beeld.int8.onnx ;;
      symlink) rm /proc/self/fd/4/meta.json && ln -s "$3/bestand" /proc/self/fd/4/meta.json ;;
      fifo) rm /proc/self/fd/4/meta.json && mkfifo /proc/self/fd/4/meta.json ;;
      weg) rm /proc/self/fd/4/meta.json ;;
      extra) printf extra >/proc/self/fd/4/extra.onnx ;;
    esac
  ' sh "$uitkomst" "$OMZET_MODE" "$host" "/proc/self/fd/5/omzet.gelezen" 3<"$OMZET_BRON" 4<"$OMZET_UIT" 5<"$work/gelezen"
}
# Waar de omzetter zijn gelezen sha256's kwijt kan (10004 schrijft daar via fd 5)
mkdir -m 0700 "$work/gelezen"
chown 10004:10003 "$work/gelezen"
gelezen() { cat "$work/gelezen/omzet.gelezen" 2>/dev/null; }
bron_shas() { printf '%s\n' "$(sha "$net/$M/bron/config.json")" "$(sha "$net/$M/bron/model.safetensors")"; }

# Alles van main wat de machine raakt, nagebootst; instellingen, de mappen, de downloads, de
# stap BEELD en de eindtekst zijn echt. De nep-docker schrijft elke aanroep in $LOG
LIMITS="true true" UP_RC=0 STATUS_OUT="Analyse: ok"
fakes() {
  # shellcheck disable=SC2329
  check_host() { :; }
  # shellcheck disable=SC2329
  pakketten() { :; }
  # shellcheck disable=SC2329
  docker_setup() { :; }
  # shellcheck disable=SC2329
  broncode() { :; }
  # shellcheck disable=SC2329
  wait_health() { :; }
  # shellcheck disable=SC2329
  mountpoint() { return 1; }
  # shellcheck disable=SC2329
  hostname() { echo 127.0.0.1; }
  # shellcheck disable=SC2329
  docker() {
    printf 'docker %s\n' "$*" >>"$LOG"
    case "$1" in
      info) echo "$LIMITS" ;;
      image) echo sha256:app ;;
      compose)
        case "$*" in
          *" --profile omzetten run --rm --no-deps -T analyse-omzetten")
            omzetter
            return
            ;;
          *" up -d --no-deps analyse" | *" up -d --no-deps --force-recreate analyse") return "$UP_RC" ;;
          *" exec -T platform python -m app.cli analyse-status --wait 20") printf '%s\n' "$STATUS_OUT" ;;
          *" exec "*) return 1 ;;
        esac
        ;;
    esac
    return 0
  }
}
# main in een eigen shell: run_main <script> [VAR=waarde]…; zet MAIN_RC, uitvoer in main.out
run_main() {
  local script=$1
  shift
  : >"$LOG"
  : >"$work/curl.log"
  : >"$work/omzet.log"
  (
    unset ANALYSE ANALYSE_WORKER_KEY MODELLEN_ROOT SLEUTELS_ROOT DATA_ROOT BEELD
    for a in "$@"; do export "${a?}"; done
    # shellcheck source=/dev/null
    . "$script"
    # TEST_STANDAARD=uit: zoals tot en met 0.8.0 (ANALYSE_STANDAARD=uit), alleen in deze shell
    # shellcheck disable=SC2030
    [ -z "${TEST_STANDAARD-}" ] || ANALYSE_STANDAARD=$TEST_STANDAARD
    fakes
    set -e
    main
  ) >"$work/main.out" 2>&1 </dev/null
  MAIN_RC=$?
}
said() { grep -qxF -- "$1" "$work/main.out"; }
said_part() { grep -qF -- "$1" "$work/main.out"; }
logged() { grep -qF -- "$1" "$LOG"; }
omzet_calls() { grep -c -- '--profile omzetten run' "$LOG"; }
# De bestanden onder PLATFORM_ROOT (zonder de broncode) met eigenaar en rechten
tree() { (cd "$PLATFORM_ROOT" && find . -path ./src -prune -o -printf '%p %u:%g %m %y\n' | sort); }
models_sum() { (cd "$1" && find . -type f -printf '%p %u %m\n' -exec sha256sum {} \; | sort); }
no_tmp() { [ -z "$(find "$MODELLEN_ROOT" -mindepth 1 -maxdepth 1 -name '.beeld.*')" ] && [ -z "$(find "$MODELLEN_ROOT" -name bron)" ]; }
# Het model in MODELLEN_ROOT/M zoals de lock het zegt: precies de drie bestanden, gelijk aan de
# uitkomst, 0644 van root, de map 0755 van root
model_ok() {
  local t=$MODELLEN_ROOT/$M f
  [ "$(owner_mode "$t")" = "0:0 755" ] || return 1
  [ "$(cd "$t" && find . -mindepth 1 -printf '%p\n' | sort | tr '\n' ' ')" = "./beeld.int8.onnx ./meta.json ./siglip2-Apache-2.0.txt " ] || return 1
  for f in "$uitkomst"/*; do
    cmp -s "$f" "$t/${f##*/}" && [ "$(owner_mode "$t/${f##*/}")" = "0:0 644" ] || return 1
  done
}
# beeld_model zoals main hem aanroept: de analyse aan, compose met de bestanden van de analyse,
# stdin met "de rest van het script" (bij curl … | bash)
beeld() {
  : >"$LOG"
  : >"$work/curl.log"
  : >"$work/omzet.log"
  : >"$work/http.log"
  rm -f "$work/gelezen/omzet.gelezen"
  MODELS_CHANGED=0
  beeld_model >"$work/b.out" 2>&1 <<<"echo de rest van het script"
}
b_said() { grep -qxF -- "$1" "$work/b.out"; }
b_part() { grep -qF -- "$1" "$work/b.out"; }

# ============================================================================================
echo "== want_beeld"
check "zonder BEELD: leeg" test "$(want_beeld)" = ""
check "BEELD=aan" test "$(BEELD=aan want_beeld)" = aan
check "BEELD=uit" test "$(BEELD=uit want_beeld)" = uit
for gek in ja Aan " uit" off 1; do
  if out=$(BEELD=$gek want_beeld 2>&1); then
    fout "want_beeld weigert '$gek'"
  else
    check "want_beeld weigert '$gek' met uitleg" grep -qF "BEELD moet aan of uit zijn (nu: $gek)" <<<"$out"
  fi
done

echo "== BEELD=ja stopt install.sh op de eerste regel, vóór er iets verandert"
fresh
fake_bin=$work/bin
mkdir -p "$fake_bin"
for name in apt-get dpkg docker git systemctl curl install chown mountpoint id hostname df mktemp setpriv; do
  printf '#!/bin/sh\necho "%s $*" >>"%s"\nexit 99\n' "$name" "$work/calls.log" >"$fake_bin/$name"
  chmod +x "$fake_bin/$name"
done
cp "$ENV_FILE" "$work/env.voor"
: >"$work/calls.log"
code=0
out=$(env BEELD=ja ANALYSE=aan INSTALL_LIB='' PLATFORM_ROOT="$PLATFORM_ROOT" PATH="$fake_bin:$PATH" bash "$install_sh" 2>&1) || code=$?
check "exit 1, met uitleg" eval '[ "$code" = 1 ] && grep -qF "BEELD moet aan of uit zijn (nu: ja)" <<<"$out"'
check "geen enkel commando aangeroepen" test ! -s "$work/calls.log"
check ".env onveranderd" cmp -s "$ENV_FILE" "$work/env.voor"

# ============================================================================================
echo "== instellingen: BEELD alleen met de analyse aan"
fresh
cp "$ENV_FILE" "$work/env.voor"
check "beeld_supported: compose.analyse.yaml en de regels van $M in models.lock" beeld_supported
{
  cat "$work/env.voor"
  echo ANALYSE=uit
} >"$work/env.uit"
# Sinds 0.8.1 (ANALYSE_STANDAARD=aan): een .env van 0.8.0 zonder ANALYSE krijgt de analyse aan
instellingen "" "" "" "" "" >/dev/null
check "zonder ANALYSE (een .env van 0.8.0, de standaard): ANALYSE=aan, de sleutel, de twee mappen en BEELD=aan, als laatste van vijf nieuwe regels" eval 'has_line ANALYSE=aan &&
  [[ $(value ANALYSE_WORKER_KEY) =~ ^[0-9a-f]{64}$ ]] && has_line "MODELLEN_ROOT=$PLATFORM_ROOT/modellen" && has_line "SLEUTELS_ROOT=$PLATFORM_ROOT/sleutels/gezichten" &&
  [ "$(wc -l <"$ENV_FILE")" = 13 ] && [ "$(tail -n 1 "$ENV_FILE")" = BEELD=aan ] && [ "$(head -n 8 "$ENV_FILE")" = "$(cat "$work/env.voor")" ]'
base_env
instellingen "" "" "" "" uit >/dev/null
check "zonder ANALYSE (aan), BEELD=uit gevraagd: BEELD=uit, één regel" eval 'has_line ANALYSE=aan && has_line BEELD=uit && [ "$(grep -c "^BEELD=" "$ENV_FILE")" = 1 ]'
base_env
instellingen "" "" "" uit aan >/dev/null
check "ANALYSE=uit gevraagd (zonder regel), BEELD=aan: alleen ANALYSE=uit, geen regel BEELD" cmp -s "$ENV_FILE" "$work/env.uit"
# Met ANALYSE_STANDAARD=uit (zo was het tot en met 0.8.0)
base_env
ANALYSE_STANDAARD=uit
instellingen "" "" "" "" "" >/dev/null
check "met ANALYSE_STANDAARD=uit, zonder ANALYSE: .env byte voor byte gelijk" cmp -s "$ENV_FILE" "$work/env.voor"
instellingen "" "" "" "" uit >/dev/null
check "met ANALYSE_STANDAARD=uit, zonder ANALYSE, BEELD=uit gevraagd: geen regel" cmp -s "$ENV_FILE" "$work/env.voor"
instellingen "" "" "" "" aan >/dev/null
check "met ANALYSE_STANDAARD=uit, zonder ANALYSE, BEELD=aan gevraagd: geen regel" cmp -s "$ENV_FILE" "$work/env.voor"
instellingen "" "" "" uit aan >/dev/null
check "met ANALYSE_STANDAARD=uit, ANALYSE=uit gevraagd (zonder regel), BEELD=aan: geen regel" cmp -s "$ENV_FILE" "$work/env.voor"
ANALYSE_STANDAARD=aan
base_env
instellingen "" "" "" aan "" >/dev/null
check "ANALYSE=aan: BEELD=aan erbij, als laatste van vijf nieuwe regels" eval 'has_line BEELD=aan && [ "$(wc -l <"$ENV_FILE")" = 13 ] &&
  [ "$(tail -n 1 "$ENV_FILE")" = BEELD=aan ] && [ "$(head -n 8 "$ENV_FILE")" = "$(cat "$work/env.voor")" ]'
check "chmod 600" test "$(stat -c %a "$ENV_FILE")" = 600
instellingen "" "" "" "" uit >/dev/null
check "BEELD=uit gevraagd: BEELD=uit" has_line BEELD=uit
instellingen "" "" "" "" "" >/dev/null
check "zonder vraag blijft uit staan" has_line BEELD=uit
instellingen "" "" "" aan "" >/dev/null
check "ANALYSE=aan gevraagd zonder BEELD: uit blijft (add_key vervangt niets)" has_line BEELD=uit
instellingen "" "" "" "" aan >/dev/null
check "BEELD=aan gevraagd: aan; één regel" eval 'has_line BEELD=aan && [ "$(grep -c "^BEELD=" "$ENV_FILE")" = 1 ]'
instellingen "" "" "" uit uit >/dev/null
check "ANALYSE=uit en BEELD=uit samen: allebei uit (de regel stond er)" eval 'has_line ANALYSE=uit && has_line BEELD=uit'
base_env
instellingen "" "" "" aan uit >/dev/null
check "de eerste keer aan met BEELD=uit: BEELD=uit, één regel" eval 'has_line BEELD=uit && [ "$(grep -c "^BEELD=" "$ENV_FILE")" = 1 ]'
base_env
grep -v "^[0-9a-f]* [0-9]* $M/" "$LOCK" >"$work/lock.zonder"
cp "$work/lock.zonder" "$LOCK"
check "zonder de regels van $M in models.lock: beeld_supported is nee" not beeld_supported
instellingen "" "" "" aan "" >/dev/null
check "een versie zonder zoeken op beeld (golf 5): ANALYSE=aan zonder BEELD" eval 'has_line ANALYSE=aan && ! grep -q "^BEELD=" "$ENV_FILE"'
base_env
instellingen "" "" "" "" "" >/dev/null
check "een versie zonder zoeken op beeld, zonder ANALYSE (de standaard): ANALYSE=aan zonder BEELD" eval 'has_line ANALYSE=aan && ! grep -q "^BEELD=" "$ENV_FILE"'
write_lock
rm -f "$SRC/infra/single/compose.analyse.yaml"
check "zonder compose.analyse.yaml (0.8.0): beeld_supported is nee" not beeld_supported
make_src

echo "== .env.example: BEELD alleen als opmerking"
check "# BEELD=aan staat erin, geen regel BEELD zonder #" eval 'grep -qxF "# BEELD=aan" "$top/infra/single/.env.example" && ! grep -q "^BEELD" "$top/infra/single/.env.example"'

# ============================================================================================
echo "== beeld_regels: de echte models.lock en regels die niet kloppen"
saved_src=$SRC
SRC=$top
check "de echte lock: vijf bronnen (met url), vijf bestanden van de uitkomst (url -)" eval 'beeld_regels >/dev/null && [ "${#BEELD_BRON[@]}" = 5 ] && [ "${#BEELD_UIT[@]}" = 5 ]'
check "de echte lock: de bronnen van huggingface.co op een vaste commit" eval 'for l in "${BEELD_BRON[@]}"; do [[ ${l##* } =~ ^https://huggingface\.co/google/siglip2-base-patch32-256/resolve/[0-9a-f]{40}/ ]] || exit 1; done'
check "de echte lock: de uitkomst zoals X3a hem maakt" test "$(for l in "${BEELD_UIT[@]}"; do read -r _ _ b <<<"$l"; echo "$b"; done | sort | tr '\n' ' ')" = "beeld.int8.onnx meta.json siglip2-Apache-2.0.txt tekst.int8.onnx tokenizer.bin "
# De uitkomst is die van VERSIE 3 van de omzetter (G6-2): beeld.int8.onnx, tekst.int8.onnx,
# tokenizer.bin, meta.json en de licentie, met de groottes uit models.lock.
check "de echte lock: de ruimte is de bron plus twee keer de uitkomst (±2,3 GB)" eval '[ "$BEELD_NODIG" = $((1541884183 + 2 * (102609072 + 284248004 + 12414446 + 2376 + 10174))) ]'
SRC=$saved_src
check "de nep-lock: twee bronnen, drie bestanden" eval 'beeld_regels >/dev/null && [ "${#BEELD_BRON[@]}" = 2 ] && [ "${#BEELD_UIT[@]}" = 3 ]'
bad_lock() {
  local why=$1 out
  write_lock
  printf '%s\n' "$2" >>"$LOCK"
  out=$(beeld_regels 2>&1) && return 1
  write_lock
  grep -qF -- "$why" <<<"$out"
}
z=$(printf 'x' | sha256sum | cut -d' ' -f1)
check "een bron zonder url: geweigerd" bad_lock "Let op: een regel van models.lock klopt niet ($M/bron/x.json)" "$z 1 $M/bron/x.json -"
check "een uitkomst met een url: geweigerd" bad_lock "klopt niet ($M/x.onnx)" "$z 1 $M/x.onnx $base/x.onnx"
check "een naam met ..: geweigerd" bad_lock "klopt niet ($M/../x.onnx)" "$z 1 $M/../x.onnx -"
check "een map dieper: geweigerd" bad_lock "klopt niet ($M/a/b/x.onnx)" "$z 1 $M/a/b/x.onnx -"
check "een grootte 0: geweigerd" bad_lock "klopt niet ($M/x.onnx)" "$z 0 $M/x.onnx -"
check "een sha256 met hoofdletters: geweigerd" bad_lock "klopt niet ($M/x.onnx)" "${z^^} 1 $M/x.onnx -"
check "iets achter de url zonder #: geweigerd" bad_lock "klopt niet ($M/x.onnx)" "$z 1 $M/x.onnx - meer"
grep -v " -$" "$LOCK" >"$work/lock.bron"
cp "$work/lock.bron" "$LOCK"
check "alleen bronnen, geen uitkomst: geweigerd" eval '! out=$(beeld_regels 2>&1) && [ "$out" = "Let op: models.lock heeft niet alle regels van $M (bronnen en uitkomst)." ]'
write_lock

# ============================================================================================
echo "== beeld_model: BEELD=uit haalt niets en zet niets om"
fresh
analyse_dirs >/dev/null
fakes
compose_files 0 0 0 1
BEELD=uit beeld
check "BEELD_STATE=uit, geen uitvoer" eval '[ "$BEELD_STATE" = uit ] && [ ! -s "$work/b.out" ]'
check "geen curl, geen HTTP, geen omzetter, geen Docker" eval '[ ! -s "$work/curl.log" ] && [ ! -s "$work/http.log" ] && [ ! -s "$work/omzet.log" ] && [ ! -s "$LOG" ]'
check "geen map: MODELLEN_ROOT is leeg" test -z "$(ls -A "$MODELLEN_ROOT")"
BEELD=ja beeld
check "BEELD=ja in .env: ongeldig, met uitleg, niets gehaald" eval '[ "$BEELD_STATE" = ongeldig ] && b_said "Let op: BEELD in $ENV_FILE moet aan of uit zijn (nu: ja); er wordt niets opgehaald." &&
  [ ! -s "$work/curl.log" ] && [ ! -s "$LOG" ] && [ -z "$(ls -A "$MODELLEN_ROOT")" ]'

echo "== beeld_model: de eerste keer"
mkdir -p "$MODELLEN_ROOT/.beeld.oud123/bron"
printf 'half' >"$MODELLEN_ROOT/.beeld.oud123/bron/model.safetensors"
# shellcheck disable=SC2329  # beeld_ruimte roept hem aan
df() {
  printf '%s\n' "$*" >>"$work/df.log"
  command df "$@"
}
: >"$work/df.log"
BEELD=aan beeld
unset -f df
check "BEELD_STATE=nieuw, MODELS_CHANGED=1" eval '[ "$BEELD_STATE" = nieuw ] && [ "$MODELS_CHANGED" = 1 ]'
check "het model staat er: drie bestanden gelijk aan de uitkomst, 0644 van root, de map 0755 van root" model_ok
check "de bron, de uitvoermap en de map van de afgebroken run zijn weg" no_tmp
check "niets van 10004 in MODELLEN_ROOT" test -z "$(find "$MODELLEN_ROOT" -user 10004)"
check "de ruimte via df op MODELLEN_ROOT en op DATA_ROOT (K2-19)" eval 'grep -qxF -- "--output=avail -B1 -- $MODELLEN_ROOT" "$work/df.log" && grep -qxF -- "--output=avail -B1 -- $DATA_ROOT" "$work/df.log"'
check "curl per bron met --retry 3, --max-filesize <bytes> en een tijdelijk bestand in de eigen map" eval '[ "$(grep -c "^curl " "$work/curl.log")" = 2 ] &&
  grep -qE -- "^curl -fsSL --retry 3 --connect-timeout 20 --max-time [0-9]+ --max-filesize 6000 -o $MODELLEN_ROOT/\.beeld\.[A-Za-z0-9]{6}/bron/\.deel $base/$M/bron/model\.safetensors$" "$work/curl.log" &&
  grep -qE -- "--max-filesize $(size "$net/$M/bron/config.json") -o .*/bron/\.deel $base/$M/bron/config\.json$" "$work/curl.log"'
check "de omzetter één keer, met compose, --profile omzetten en run --rm --no-deps -T" eval '[ "$(omzet_calls)" = 1 ] &&
  grep -qxF "docker ${compose[*]:1} --profile omzetten run --rm --no-deps -T analyse-omzetten" "$LOG"'
check "OMZET_BRON en OMZET_UIT: bron en uit in een map .beeld.* van MODELLEN_ROOT" grep -qE "^bron=$MODELLEN_ROOT/\.beeld\.[A-Za-z0-9]{6}/bron uit=$MODELLEN_ROOT/\.beeld\.[A-Za-z0-9]{6}/uit$" "$work/omzet.log"
check "de bron: een map 0755 en bestanden 0644, van root (10004 kan ze lezen, niet schrijven)" eval '[ "$(grep "^stat .*/bron" "$work/omzet.log" | cut -d" " -f3- | sort | tr "\n" ,)" = "0:0 644 regular file,0:0 644 regular file,0:0 755 directory," ]'
check "de uitvoermap: van 10004, 0700, leeg" eval 'grep -qE "^stat .*/uit 10004:10003 700 directory$" "$work/omzet.log" && grep -qxF "al in uit: 0" "$work/omzet.log"'
check "de omzetter las als 10004 precies de bronnen (sha256)" eval '[ "$(gelezen | sort)" = "$(bron_shas | sort)" ]'
check "de omzetter kreeg geen stdin (</dev/null) en de sleutel niet in zijn omgeving" eval 'grep -qxF "stdin: 0" "$work/omzet.log" && ! grep -q "sleutel in de omgeving" "$work/omzet.log"'
check "de uitvoer" eval 'b_said "Bron opgehaald: $M/bron/model.safetensors" && b_said "Bron opgehaald: $M/bron/config.json" &&
  b_said "Het model omzetten (een paar minuten, zonder netwerk)…" && b_said "Zoeken op beeld: het model staat klaar ($MODELLEN_ROOT/$M)." && ! b_part "Let op"'
models_sum "$MODELLEN_ROOT/$M" >"$work/model.voor"

echo "== een tweede run slaat een goed model over"
BEELD=aan beeld
check "klaar, MODELS_CHANGED blijft 0, één regel uitvoer" eval '[ "$BEELD_STATE" = klaar ] && [ "$MODELS_CHANGED" = 0 ] && [ "$(cat "$work/b.out")" = "Zoeken op beeld: het model staat er al ($MODELLEN_ROOT/$M)." ]'
check "geen curl, geen omzetter, geen df" eval '[ ! -s "$work/curl.log" ] && [ "$(omzet_calls)" = 0 ] && [ ! -s "$work/omzet.log" ]'
check "het model onveranderd" eval 'models_sum "$MODELLEN_ROOT/$M" | cmp -s - "$work/model.voor"'

echo "== een nieuwe uitkomst in de lock: opnieuw omgezet, de map in één keer vervangen"
head -c 4100 /dev/urandom >"$uitkomst/beeld.int8.onnx"
write_lock
BEELD=aan beeld
check "nieuw, MODELS_CHANGED=1, het nieuwe model, geen tijdelijke map" eval '[ "$BEELD_STATE" = nieuw ] && [ "$MODELS_CHANGED" = 1 ] && model_ok && no_tmp'
models_sum "$MODELLEN_ROOT/$M" >"$work/model.voor"

# Een mislukking: return 0, de melding, niets achter, het model van daarvoor onveranderd
failed_clean() {
  [ "$BEELD_STATE" = mislukt ] && [ "$MODELS_CHANGED" = 0 ] && b_said "$BEELD_MISLUKT" && no_tmp &&
    models_sum "$MODELLEN_ROOT/$M" | cmp -s - "$work/model.voor"
}
echo "== een bron met een verkeerde sha256, te groot, of geen server: niets omgezet"
head -c 4200 /dev/urandom >"$uitkomst/beeld.int8.onnx"  # een nieuwe uitkomst: het oude model is niet meer goed
write_lock
cp "$net/$M/bron/model.safetensors" "$work/model.goed"
head -c 6000 /dev/urandom >"$net/$M/bron/model.safetensors"  # dezelfde grootte, een andere sha256
code=0
BEELD=aan beeld || code=$?
check "verkeerde sha256: return 0, met uitleg, de omzetter niet aangeroepen" eval '[ "$code" = 0 ] && failed_clean && [ "$(omzet_calls)" = 0 ] &&
  b_said "Let op: kon $M/bron/model.safetensors niet ophalen, of de grootte of de sha256 klopt niet ($base/$M/bron/model.safetensors)."'
head -c 9000 /dev/urandom >"$net/$M/bron/model.safetensors"  # groter dan de lock zegt
BEELD=aan beeld
check "te groot: --max-filesize stopt curl, niets omgezet" eval 'failed_clean && [ "$(omzet_calls)" = 0 ] && grep -qF -- "--max-filesize 6000 " "$work/curl.log"'
cp "$work/model.goed" "$net/$M/bron/model.safetensors"
kill "$server_pid"
wait "$server_pid" 2>/dev/null
BEELD=aan beeld
check "geen server: return 0, niets omgezet" eval 'failed_clean && [ "$(omzet_calls)" = 0 ]'
start_server
write_lock

echo "== de omzetter faalt, of zijn uitkomst klopt niet: geweigerd, het oude model blijft"
OMZET_RC=2
BEELD=aan beeld
check "de omzetter faalt (exit 2): de melding, niets overgenomen" eval 'failed_clean && b_said "Let op: de omzetting lukte niet (de melding staat hierboven)." && b_said "omzetten: de bron klopt niet"'
OMZET_RC=0
for mode in verkeerd groot; do
  OMZET_MODE=$mode
  BEELD=aan beeld
  check "uitkomst $mode: geweigerd na de controle van grootte en sha256" eval 'failed_clean && b_said "Let op: beeld.int8.onnx van de omzetter klopt niet met models.lock (grootte of sha256); niet overgenomen."'
done
for mode in symlink fifo weg; do
  OMZET_MODE=$mode
  BEELD=aan beeld
  check "uitkomst $mode: geen gewoon bestand, niet overgenomen" eval 'failed_clean && b_said "Let op: meta.json van de omzetter is geen gewoon bestand; niet overgenomen."'
done
check "de symlink van de omzetter: het bestand van de host onaangeraakt" host_same
OMZET_MODE=extra
BEELD=aan beeld
check "een extra bestand van de omzetter: niet overgenomen, de rest wel" eval '[ "$BEELD_STATE" = nieuw ] && model_ok && no_tmp && [ ! -e "$MODELLEN_ROOT/$M/extra.onnx" ]'
OMZET_MODE=goed
models_sum "$MODELLEN_ROOT/$M" >"$work/model.voor"

echo "== te weinig ruimte (K2-19): niets gehaald, niet fataal"
head -c 4300 /dev/urandom >"$uitkomst/beeld.int8.onnx"
write_lock
beeld_regels >/dev/null
nodig=$((BEELD_NODIG + BEELD_MARGE))
# shellcheck disable=SC2329  # beeld_ruimte roept hem aan
df() {
  printf '%s\n' "$*" >>"$work/df.log"
  case "${*: -1}" in
    "$MODELLEN_ROOT") printf 'Avail\n%s\n' "$DF_MODELLEN" ;;
    "$DATA_ROOT") printf 'Avail\n%s\n' "$DF_DATA" ;;
    *) printf 'Avail\n999999999999\n' ;;
  esac
}
DF_MODELLEN=$((nodig - 1)) DF_DATA=999999999999
: >"$work/df.log"
BEELD=aan beeld
check "MODELLEN_ROOT één byte te weinig: geen curl, de melding met de MB" eval 'failed_clean && [ ! -s "$work/curl.log" ] && [ "$(omzet_calls)" = 0 ] &&
  b_said "Let op: te weinig ruimte in $MODELLEN_ROOT voor het model van zoeken op beeld ($((nodig / 1000000)) MB nodig, met de bron)."'
DF_MODELLEN=$nodig DF_DATA=$((BEELD_DATA - 1))
: >"$work/df.log"
BEELD=aan beeld
check "DATA_ROOT te weinig: geen curl, de melding" eval 'failed_clean && [ ! -s "$work/curl.log" ] &&
  b_said "Let op: te weinig ruimte in $DATA_ROOT voor de vectoren en de cache van zoeken op beeld (1000 MB nodig; ±360 MB per 100.000 foto'"'"'s)." &&
  [ "$(cat "$work/df.log")" = "$(printf -- "--output=avail -B1 -- %s\n--output=avail -B1 -- %s" "$MODELLEN_ROOT" "$DATA_ROOT")" ]'
DF_MODELLEN=garbage DF_DATA=999999999999
BEELD=aan beeld
check "df zonder getal: telt als te weinig" eval 'failed_clean && [ ! -s "$work/curl.log" ]'
DF_MODELLEN=$nodig DF_DATA=$BEELD_DATA
BEELD=aan beeld
check "precies genoeg: wel omgezet" eval '[ "$BEELD_STATE" = nieuw ] && model_ok && no_tmp'
unset -f df
saved_src=$SRC
SRC=$top
DF_MODELLEN=1000 DF_DATA=999999999999
# shellcheck disable=SC2329  # beeld_ruimte roept hem aan
df() { printf 'Avail\n%s\n' "$DF_MODELLEN"; }
beeld_regels >/dev/null
out=$(beeld_ruimte 2>&1)
check "met de echte lock: 2440 MB nodig (±2,5 GB, Keuze 84)" test "$out" = "Let op: te weinig ruimte in $MODELLEN_ROOT voor het model van zoeken op beeld (2440 MB nodig, met de bron)."
unset -f df
SRC=$saved_src
beeld_regels >/dev/null
models_sum "$MODELLEN_ROOT/$M" >"$work/model.voor"

echo "== een doel dat een symlink of geen map is, of een bestand erin dat een symlink is (K1-06)"
mv "$MODELLEN_ROOT/$M" "$work/model.bewaard"
ln -s "$host/map" "$MODELLEN_ROOT/$M"
BEELD=aan beeld
check "het doel is een symlink naar een map van de host: geweigerd, niets gehaald" eval '[ "$BEELD_STATE" = mislukt ] && [ ! -s "$work/curl.log" ] && [ "$(omzet_calls)" = 0 ] &&
  b_said "$MODELLEN_ROOT/$M is een symlink of geen map; dit script raakt het niet aan." && b_said "$BEELD_MISLUKT"'
check "de symlink en de map van de host onaangeraakt" eval '[ "$(readlink "$MODELLEN_ROOT/$M")" = "$host/map" ] && host_same'
rm "$MODELLEN_ROOT/$M"
printf 'een bestand' >"$MODELLEN_ROOT/$M"
BEELD=aan beeld
check "het doel is een bestand: geweigerd, onaangeraakt" eval '[ "$BEELD_STATE" = mislukt ] && [ ! -s "$work/curl.log" ] && [ "$(cat "$MODELLEN_ROOT/$M")" = "een bestand" ]'
rm "$MODELLEN_ROOT/$M"
mv "$work/model.bewaard" "$MODELLEN_ROOT/$M"
rm "$MODELLEN_ROOT/$M/meta.json"
ln -s "$host/bestand" "$MODELLEN_ROOT/$M/meta.json"
BEELD=aan beeld
check "een bestand van het model is een symlink: geweigerd, niets gehaald" eval '[ "$BEELD_STATE" = mislukt ] && [ ! -s "$work/curl.log" ] &&
  b_said "$MODELLEN_ROOT/$M/meta.json is een symlink of geen gewoon bestand; dit script raakt het niet aan."'
check "de symlink en het bestand van de host onaangeraakt" eval '[ "$(readlink "$MODELLEN_ROOT/$M/meta.json")" = "$host/bestand" ] && host_same'
rm "$MODELLEN_ROOT/$M/meta.json"
BEELD=aan beeld
check "zonder de symlink: weer omgezet" eval '[ "$BEELD_STATE" = nieuw ] && model_ok && no_tmp'
check "elke curl en de omzetter krijgen </dev/null" eval '[ -z "$(grep -n "curl -fsSL --retry 3" "$install_sh" | grep -v "</dev/null")" ] && grep -qF "run --rm --no-deps -T analyse-omzetten </dev/null" "$install_sh"'

# ============================================================================================
echo "== main met ANALYSE_STANDAARD=uit en zonder ANALYSE (met de regels van zoeken op beeld in models.lock): precies 0.8.0"
fresh
run_main "$work/install-080.sh"
cp "$work/main.out" "$work/main.080"
cp "$LOG" "$work/docker.080"
cp "$ENV_FILE" "$work/env.080"
tree >"$work/tree.080"
check "0.8.0 loopt (exit 0, Klaar.)" eval '[ "$MAIN_RC" = 0 ] && said_part "Klaar. Het platform draait op"'
for b in "" BEELD=aan BEELD=uit; do
  fresh
  run_main "$install_sh" TEST_STANDAARD=uit ${b:+"$b"}
  check "deze versie met ANALYSE_STANDAARD=uit${b:+ en $b}: exit 0, dezelfde uitvoer, Docker, .env en mappen, geen curl" eval '[ "$MAIN_RC" = 0 ] && cmp -s "$work/main.out" "$work/main.080" && cmp -s "$LOG" "$work/docker.080" &&
    cmp -s "$ENV_FILE" "$work/env.080" && tree | cmp -s - "$work/tree.080" && [ ! -s "$work/curl.log" ] && [ ! -s "$work/omzet.log" ]'
done

echo "== main met ANALYSE_STANDAARD=uit en ANALYSE=uit: niets over beeld"
fresh
run_main "$install_sh" TEST_STANDAARD=uit ANALYSE=uit BEELD=aan
check "exit 0, dezelfde aanroepen van Docker, .env en mappen als 0.8.0, geen curl" eval '[ "$MAIN_RC" = 0 ] && cmp -s "$LOG" "$work/docker.080" && cmp -s "$ENV_FILE" "$work/env.080" &&
  tree | cmp -s - "$work/tree.080" && [ ! -s "$work/curl.log" ]'
check "nergens beeld" eval '! grep -qi beeld "$work/main.out" "$LOG" "$ENV_FILE"'

echo "== main met ANALYSE=uit (de standaard aan): dezelfde aanroepen en mappen als 0.8.0, alleen ANALYSE=uit erbij, niets over beeld"
{
  cat "$work/env.080"
  echo ANALYSE=uit
} >"$work/env.080.uit"
for b in "" BEELD=aan BEELD=uit; do
  fresh
  run_main "$install_sh" ANALYSE=uit ${b:+"$b"}
  check "ANALYSE=uit${b:+ en $b}: exit 0, dezelfde aanroepen van Docker en mappen als 0.8.0, de .env van 0.8.0 met alleen ANALYSE=uit, geen curl, geen omzetter" eval '[ "$MAIN_RC" = 0 ] &&
    cmp -s "$LOG" "$work/docker.080" && cmp -s "$ENV_FILE" "$work/env.080.uit" && tree | cmp -s - "$work/tree.080" && [ ! -s "$work/curl.log" ] && [ ! -s "$work/omzet.log" ]'
  check "ANALYSE=uit${b:+ en $b}: nergens beeld" eval '! grep -qi beeld "$work/main.out" "$LOG" "$ENV_FILE"'
done

# ============================================================================================
dc_files="-f $SRC/infra/single/compose.yaml -f $SRC/infra/single/compose.analyse.yaml -f $SRC/infra/single/compose.analyse-limits.yaml"
dc="docker compose --project-directory $SRC/infra/single --env-file $ENV_FILE $dc_files"
eindtekst_gezichten="Gezichten (de container analyse): aan; elke gebruiker zet het zelf aan (Account → Foto's)."
eindtekst_beeld="Zoeken op beeld: het model staat klaar; elke gebruiker zet het zelf aan."
echo "== main zonder ANALYSE op een .env van 0.8.0 (de update naar 0.8.1): de stap BEELD loopt"
for b in "" BEELD=aan; do
  fresh
  run_main "$install_sh" ${b:+"$b"}
  check "zonder ANALYSE${b:+, met $b}: exit 0, Klaar., ANALYSE=aan en BEELD=aan in .env" eval '[ "$MAIN_RC" = 0 ] && said_part "Klaar. Het platform draait op" && has_line ANALYSE=aan && has_line BEELD=aan'
  check "zonder ANALYSE${b:+, met $b}: de stap BEELD, het model staat er (curl: het model van gezichten en de twee bronnen; de omzetter één keer)" eval 'said "== Zoeken op beeld (het model)" &&
    said "Zoeken op beeld: het model staat klaar ($MODELLEN_ROOT/$M)." && model_ok && no_tmp && [ "$(grep -c "^curl " "$work/curl.log")" = 3 ] && [ "$(omzet_calls)" = 1 ]'
  check "zonder ANALYSE${b:+, met $b}: de container analyse met --force-recreate" logged "$dc_files up -d --no-deps --force-recreate analyse"
  check "zonder ANALYSE${b:+, met $b}: de eindtekst van gezichten en van zoeken op beeld" eval 'said "$eindtekst_gezichten" && said "$eindtekst_beeld"'
  check "zonder ANALYSE${b:+, met $b}: geen Let op" not said_part "Let op: "
done

echo "== main de eerste keer met BEELD=uit (de analyse aan): BEELD=uit in .env, alleen het model van gezichten"
fresh
run_main "$install_sh" BEELD=uit
check "exit 0, Klaar.; ANALYSE=aan en BEELD=uit in .env" eval '[ "$MAIN_RC" = 0 ] && said_part "Klaar. Het platform draait op" && has_line ANALYSE=aan && has_line BEELD=uit'
check "geen stap, geen omzetter, geen map van zoeken op beeld; alleen a.onnx gehaald" eval '! said_part "== Zoeken op beeld" && [ "$(omzet_calls)" = 0 ] && [ ! -e "$MODELLEN_ROOT/$M" ] &&
  [ "$(grep -c "^curl " "$work/curl.log")" = 1 ] && grep -qE " $base/a\.onnx$" "$work/curl.log"'
check "de container analyse start; de eindtekst: gezichten aan, zoeken op beeld uit" eval 'logged "$dc_files up -d --no-deps --force-recreate analyse" && said "$eindtekst_gezichten" &&
  said "Zoeken op beeld: uit (BEELD=uit); er wordt niets opgehaald of omgezet." && ! said "$eindtekst_beeld"'
run_main "$install_sh"
check "de run daarna zonder BEELD: blijft uit, geen stap, niets gehaald" eval '[ "$MAIN_RC" = 0 ] && has_line BEELD=uit && ! said_part "== Zoeken op beeld" && [ "$(omzet_calls)" = 0 ] && [ ! -s "$work/curl.log" ]'

echo "== main met ANALYSE=aan: de eerste keer"
fresh
run_main "$install_sh" ANALYSE=aan
check "exit 0, Klaar." eval '[ "$MAIN_RC" = 0 ] && said_part "Klaar. Het platform draait op"'
check ".env: BEELD=aan" has_line BEELD=aan
check "de stap, en het model staat er" eval 'said "== Zoeken op beeld (het model)" && said "Zoeken op beeld: het model staat klaar ($MODELLEN_ROOT/$M)." && model_ok && no_tmp'
check "de omzetter met dezelfde compose-bestanden als de app" logged "$dc --profile omzetten run --rm --no-deps -T analyse-omzetten"
check "de volgorde: app, omzetter, container (--force-recreate), stand" eval '[ "$(grep -E "up -d --build|omzetten run|up -d --no-deps|analyse-status" "$LOG" | sed -E "s/.* (up -d --build|omzetten run|up -d --no-deps --force-recreate|up -d --no-deps|analyse-status).*/\1/" | tr "\n" ,)" = "up -d --build,omzetten run,up -d --no-deps --force-recreate,analyse-status," ]'
check "de stap na Media en vóór Analyse starten" eval '[ "$(grep -E "^== (Platform bouwen|Zoeken op beeld|Analyse starten)" "$work/main.out" | cut -c4-12 | tr "\n" ,)" = "Platform ,Zoeken op,Analyse s," ]'
check "de eindtekst" eval 'said "$eindtekst_beeld" && said "$eindtekst_gezichten" &&
  said "  Uit (geen download meer; het model blijft staan): curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/main/infra/single/install.sh | BEELD=uit bash"'
check "geen Let op" not said_part "Let op: "
check "de sleutel nergens in de uitvoer of de aanroepen, en niet in de omgeving van de omzetter" eval '! grep -qF "$(value ANALYSE_WORKER_KEY)" "$work/main.out" "$LOG" "$work/omzet.log" &&
  grep -q "^bron=" "$work/omzet.log" && ! grep -q "sleutel in de omgeving" "$work/omzet.log"'
models_sum "$MODELLEN_ROOT" >"$work/modellen.voor"

echo "== nog een keer: niets opnieuw, geen --force-recreate"
run_main "$install_sh"
check "exit 0, geen curl, geen omzetter, het model staat er al" eval '[ "$MAIN_RC" = 0 ] && [ ! -s "$work/curl.log" ] && [ "$(omzet_calls)" = 0 ] && said "Zoeken op beeld: het model staat er al ($MODELLEN_ROOT/$M)."'
check "de container zonder --force-recreate" eval 'logged "$dc_files up -d --no-deps analyse" && ! logged force-recreate'

echo "== BEELD=uit: geen stap, niets gehaald, het model blijft"
run_main "$install_sh" BEELD=uit
check "exit 0; BEELD=uit in .env" eval '[ "$MAIN_RC" = 0 ] && has_line BEELD=uit'
check "geen stap, geen omzetter, geen curl" eval '! said_part "== Zoeken op beeld" && [ "$(omzet_calls)" = 0 ] && [ ! -s "$work/curl.log" ]'
check "de eindtekst: uit, en hoe het weer aan gaat" eval 'said "Zoeken op beeld: uit (BEELD=uit); er wordt niets opgehaald of omgezet." && said_part "| BEELD=aan bash"'
check "de modellen blijven staan" eval 'models_sum "$MODELLEN_ROOT" | cmp -s - "$work/modellen.voor"'
run_main "$install_sh"
check "de run daarna zonder BEELD: blijft uit" eval 'has_line BEELD=uit && ! said_part "== Zoeken op beeld" && [ "$(omzet_calls)" = 0 ]'
run_main "$install_sh" BEELD=aan
check "weer aan: het model staat er al, niets gehaald" eval 'has_line BEELD=aan && said "Zoeken op beeld: het model staat er al ($MODELLEN_ROOT/$M)." && [ ! -s "$work/curl.log" ]'
sed -i "s|^BEELD=.*|BEELD=ja|" "$ENV_FILE"
run_main "$install_sh"
check "BEELD=ja in .env: exit 0, uit met uitleg, niets gehaald" eval '[ "$MAIN_RC" = 0 ] && said "Let op: BEELD in $ENV_FILE moet aan of uit zijn (nu: ja); er wordt niets opgehaald." &&
  said "Zoeken op beeld: uit, want BEELD in $ENV_FILE moet aan of uit zijn (nu: ja)." && [ "$(omzet_calls)" = 0 ]'
sed -i "s|^BEELD=.*|BEELD=aan|" "$ENV_FILE"

echo "== een omzetting die mislukt: exit 0, Klaar., de container start toch"
rm -rf "${MODELLEN_ROOT:?}/$M"
OMZET_RC=2
run_main "$install_sh"
check "exit 0, Klaar., de melding" eval '[ "$MAIN_RC" = 0 ] && said_part "Klaar. Het platform draait op" && said "Let op: de omzetting lukte niet (de melding staat hierboven)."'
check "de container analyse start (zonder --force-recreate: er kwam niets nieuws)" eval 'logged "$dc_files up -d --no-deps analyse" && ! logged force-recreate && logged analyse-status'
check "de eindtekst: niet geladen, en opnieuw proberen" eval '[ "$(grep -cxF "$BEELD_MISLUKT" "$work/main.out")" = 2 ] && said_part "  Opnieuw proberen: curl -fsSL "'
check "geen model, geen tijdelijke map" eval '[ ! -e "$MODELLEN_ROOT/$M" ] && no_tmp'
OMZET_RC=0
# shellcheck disable=SC2329  # beeld_ruimte roept hem aan
df() { printf 'Avail\n1000\n'; }
run_main "$install_sh"
check "te weinig ruimte: exit 0, Klaar., de melding, de container start" eval '[ "$MAIN_RC" = 0 ] && said_part "Klaar. Het platform draait op" && said_part "Let op: te weinig ruimte in $MODELLEN_ROOT voor het model van zoeken op beeld" &&
  [ "$(omzet_calls)" = 0 ] && logged "up -d --no-deps analyse"'
unset -f df
run_main "$install_sh"
check "de volgende run: omgezet, met --force-recreate" eval '[ "$MAIN_RC" = 0 ] && model_ok && no_tmp && logged "$dc_files up -d --no-deps --force-recreate analyse"'
models_sum "$MODELLEN_ROOT" >"$work/modellen.voor"

echo "== terug naar 0.8.0 (het script van $BACK080_SHA) en weer vooruit"
cp "$ENV_FILE" "$work/env.voor"
run_main "$work/install-080.sh"
check "0.8.0: exit 0, Klaar., geen omzetter; .env en de modellen blijven" eval '[ "$MAIN_RC" = 0 ] && said_part "Klaar. Het platform draait op" && [ "$(omzet_calls)" = 0 ] &&
  cmp -s "$ENV_FILE" "$work/env.voor" && models_sum "$MODELLEN_ROOT" | cmp -s - "$work/modellen.voor"'
run_main "$install_sh"
check "vooruit: het model staat er al, niets gehaald, zonder --force-recreate" eval '[ "$MAIN_RC" = 0 ] && said "Zoeken op beeld: het model staat er al ($MODELLEN_ROOT/$M)." &&
  [ ! -s "$work/curl.log" ] && ! logged force-recreate'

# ============================================================================================
echo "== analyse-omzetten in de echte compose-bestanden (compose config, K1-05)"
real_env=$work/single.env
key=$(hex)
{
  sed -e "s/^OFFICE_JWT_SECRET=$/OFFICE_JWT_SECRET=$(hex)/" -e "s/^OFFICE_LINK_SECRET=$/OFFICE_LINK_SECRET=$(hex)/" \
    -e "s/^MEDIA_WORKER_KEY=$/MEDIA_WORKER_KEY=$(hex)/" "$top/infra/single/.env.example"
  echo "ANALYSE_WORKER_KEY=$key"
  echo "MODELLEN_ROOT=$work/modellen-echt"
  echo "SLEUTELS_ROOT=$work/sleutels-echt"
  echo "BEELD=aan"
} >"$real_env"
if ! command docker compose version >/dev/null 2>&1; then
  fout "docker compose ontbreekt: analyse-omzetten is niet gecontroleerd"
else
  for limits in 0 1; do
    files=(-f "$top/infra/single/compose.yaml" -f "$top/infra/single/compose.analyse.yaml")
    [ "$limits" = 0 ] || files+=(-f "$top/infra/single/compose.analyse-limits.yaml")
    if OMZET_BRON=$work/x/bron OMZET_UIT=$work/x/uit command docker compose --project-directory "$top/infra/single" --env-file "$real_env" "${files[@]}" \
      --profile omzetten config --format json >"$work/config.json" 2>"$work/config.err"; then
      out=$(KEY=$key BRON=$work/x/bron UIT=$work/x/uit python3 -I - "$work/config.json" <<'PY'
import json, os, sys

c = json.load(open(sys.argv[1]))
s = c["services"]["analyse-omzetten"]
fouten = []
if s.get("network_mode") != "none":
    fouten.append(f"network_mode {s.get('network_mode')}")
if [k for k in (s.get("environment") or {}) if "KEY" in k.upper()]:
    fouten.append("een variabele met KEY")
if os.environ["KEY"] in json.dumps(s):
    fouten.append("de sleutel in de dienst")
vols = s.get("volumes") or []
if [v for v in vols if v.get("source") == "analyse-run" or v.get("type") == "volume"]:
    fouten.append("een volume (analyse-run)")
if [v for v in vols if str(v.get("target", "")).startswith("/models")]:
    fouten.append("/models")
if [v for v in vols if str(v.get("target", "")).startswith("/run/analyse")]:
    fouten.append("/run/analyse")
binds = {v["target"]: v for v in vols if v.get("type") == "bind"}
b, u = binds.get("/bron"), binds.get("/uit")
if not b or b.get("source") != os.environ["BRON"] or not b.get("read_only"):
    fouten.append(f"/bron niet alleen-lezen uit OMZET_BRON: {b}")
if not u or u.get("source") != os.environ["UIT"]:
    fouten.append(f"/uit niet uit OMZET_UIT: {u}")
for v in (b, u):
    if v and (v.get("bind") or {}).get("create_host_path") is not False:
        fouten.append(f"create_host_path niet false: {v['target']}")
if s.get("profiles") != ["omzetten"]:
    fouten.append(f"profiles {s.get('profiles')}")
if s.get("user") != "omzetten" or not s.get("read_only"):
    fouten.append("user of read_only")
if s.get("secrets") or s.get("depends_on"):
    fouten.append("secrets of depends_on")
print("; ".join(fouten) or "ok")
PY
)
      check "grenzen $limits: geen netwerk, geen KEY, geen analyse-run, geen /models, /bron alleen-lezen ($out)" test "$out" = ok
    else
      fout "compose config met --profile omzetten (grenzen $limits): $(head -c 300 "$work/config.err")"
    fi
  done
  command docker compose --project-directory "$top/infra/single" --env-file "$real_env" -f "$top/infra/single/compose.yaml" \
    -f "$top/infra/single/compose.analyse.yaml" config --services >"$work/services" 2>/dev/null
  check "zonder --profile omzetten: geen analyse-omzetten (een gewone up start hem niet)" eval 'grep -qx analyse "$work/services" && ! grep -qx analyse-omzetten "$work/services"'
fi

einde
