#!/usr/bin/env bash
# De analyse in install.sh (bouwontwerp fase 6, X2a; §3.11 "Waar install.sh ze haalt", Keuze 56–58,
# 68; K1-05, K1-06):
#
#   bash infra/single/tests/test_install_analyse.sh
#
# Draait in de job infra van CI. De test draait als root (in CI start hij zichzelf opnieuw met
# sudo -n): de mappen, de eigenaars en de symlinks van "de app" (uid 10001, met de echte setpriv)
# zijn echt. Docker is een nepfunctie met een logboek; curl is echt, tegen een eigen HTTP-server op
# 127.0.0.1 met nepmodellen en een eigen models.lock. Het controleert:
# - ANALYSE_STANDAARD is uit (tot 0.8.1, I2); ANALYSE uit de omgeving: alleen aan of uit, een
#   andere waarde stopt install.sh op de eerste regel;
# - .env: zonder ANALYSE, of met uit, komt er niets bij; bij aan ANALYSE=aan, een sleutel van
#   64 hex (één keer, nooit vervangen, ook niet na uit en weer aan) en de twee mappen; een lege
#   sleutel vullen zet hem in de argv van geen enkel proces (een PATH met nep-commando's), en
#   set_key neemt de waarde letterlijk;
# - main zonder ANALYSE doet precies wat install.sh van 0.8.0 doet (dezelfde uitvoer, dezelfde
#   aanroepen van Docker, dezelfde .env, geen nieuwe map), en met ANALYSE=uit dezelfde aanroepen,
#   .env en mappen;
# - de mappen (K1-06): MODELLEN_ROOT 0755 van root, SLEUTELS_ROOT 0700 van 10001; weigert een pad
#   in DATA_ROOT (of DATA_ROOT erin), een symlink, een map die niet van root is of die anderen
#   mogen beschrijven, een relatief pad, . of .., en de ene map in de andere; dan niets
#   aangemaakt en het doel niet aangeraakt;
# - de modellen: met de juiste grootte en sha256, 0644 van root, via een eigen map onder
#   MODELLEN_ROOT die daarna weg is; curl met --retry 3 en --max-filesize; een verkeerde sha256,
#   een te groot bestand, geen server of te weinig ruimte is niet fataal en laat niets achter; een
#   tweede run haalt niets; een gewijzigd model wordt opnieuw gehaald (en --force-recreate); een
#   *.onnx die niet meer in de lock staat gaat weg; url "-" wordt niet gehaald; de regels van
#   zoeken op beeld (siglip2-b32-256/…) haalt het niet, die zijn van de stap BEELD van X3b, en een
#   omzetting die er al staat, blijft; de licenties staan ernaast; een symlink die "de app" (10001) of iemand anders op de plek van een model of
#   een map zet, raakt het script niet aan;
# - main met aan: de stap, compose.analyse.yaml (en de grenzen) bij elke compose-aanroep, de
#   container met --no-deps, daarna analyse-status --wait 20, en de eindtekst; een start die
#   faalt of een stand die niet ok is, alleen een waarschuwing;
# - ANALYSE=uit na aan: geen download, geen container, --remove-orphans haalt hem weg, de
#   modellen en de sleutels blijven, en het script zegt waar de modellen staan;
# - terug naar 0.8.0 (het script van 59e0250) en weer vooruit: de sleutels, de modellen en .env
#   blijven, en vooruit wordt er niets opnieuw gehaald. Wat db-downgrade 0010 in de database doet
#   (de tabellen van 0011 en 0012 weg), bewijst api/tests/search/test_migration_0012.py.
#
# De variabelen en functies komen uit install.sh (shellcheck volgt de bron niet in CI):
# shellcheck disable=SC2034,SC2153,SC2154,SC2016
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
top=$(cd "$here/../../.." && pwd)
install_sh=$here/../install.sh
# 0.8.0: de laatste commit vóór A0 (zoals test_install_search.sh)
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

# Het script van 0.8.0, uit git (de job infra haalt de hele geschiedenis op)
if ! git -c safe.directory='*' -C "$top" show "$BACK080_SHA:infra/single/install.sh" >"$work/install-080.sh" 2>/dev/null; then
  fout "install.sh van 0.8.0 ($BACK080_SHA) niet in git: haal de hele geschiedenis op"
  einde
fi

# --- het nepnetwerk: een HTTP-server op 127.0.0.1 met de modellen ------------------------------
net=$work/net
mkdir -p "$net/map"
head -c 3000 /dev/urandom >"$net/a.onnx"
head -c 5000 /dev/urandom >"$net/map/b.onnx"
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
python3 -I "$work/server.py" "$net" "$work/port" "$work/http.log" &
server_pid=$!
for _ in $(seq 1 50); do [ -s "$work/port" ] && break; sleep 0.1; done
port=$(cat "$work/port")
base=http://127.0.0.1:$port
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

# De broncode: alleen wat de analyse nodig heeft (geen office, media of plaatsnamen); de eigen
# models.lock wijst naar het nepnetwerk. c.onnx maakt de host zelf (url "-", zoals X3)
LOCK=$SRC/api/app/analyse/models.lock
write_lock() {
  cat >"$LOCK" <<LOCK
# nepmodellen van test_install_analyse.sh
$(sha "$net/a.onnx") $(size "$net/a.onnx") a.onnx $base/a.onnx
$(sha "$net/map/b.onnx") $(size "$net/map/b.onnx") map/b.onnx $base/map/b.onnx  # met een opmerking
$(printf c | sha256sum | cut -d' ' -f1) 1 c.onnx -
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
without_analyse() { rm -f "$SRC/infra/single/compose.analyse.yaml" "$SRC/infra/single/compose.analyse-limits.yaml"; }

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

# Alles van main wat de machine raakt, nagebootst; instellingen, de mappen, de download en de
# eindtekst zijn echt. De nep-docker schrijft elke aanroep in $LOG
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
  MAIN_RC=0
  (
    unset ANALYSE ANALYSE_WORKER_KEY MODELLEN_ROOT SLEUTELS_ROOT DATA_ROOT
    for a in "$@"; do export "${a?}"; done
    # shellcheck source=/dev/null
    . "$script"
    fakes
    set -e
    main
  ) >"$work/main.out" 2>&1 || MAIN_RC=$?
}
said() { grep -qxF -- "$1" "$work/main.out"; }
said_part() { grep -qF -- "$1" "$work/main.out"; }
logged() { grep -qF -- "$1" "$LOG"; }
# De bestanden onder PLATFORM_ROOT (zonder de broncode) met eigenaar en rechten, en de inhoud van .env
tree() { (cd "$PLATFORM_ROOT" && find . -path ./src -prune -o -printf '%p %u:%g %m %y\n' | sort); }
models_sum() { (cd "$1" && find . -type f -printf '%p %u %m\n' -exec sha256sum {} \; | sort); }

# ============================================================================================
echo "== ANALYSE_STANDAARD en want_analyse"
check "ANALYSE_STANDAARD=uit (tot 0.8.1; I2 zet hem op aan)" test "$(grep -c '^ANALYSE_STANDAARD=uit$' "$install_sh")" = 1
check "zonder ANALYSE: leeg" test "$(want_analyse)" = ""
check "ANALYSE=aan" test "$(ANALYSE=aan want_analyse)" = aan
check "ANALYSE=uit" test "$(ANALYSE=uit want_analyse)" = uit
for gek in ja Aan " uit" off 1; do
  if out=$(ANALYSE=$gek want_analyse 2>&1); then
    fout "want_analyse weigert '$gek'"
  else
    check "want_analyse weigert '$gek' met uitleg" grep -qF "ANALYSE moet aan of uit zijn (nu: $gek)" <<<"$out"
  fi
done

echo "== ANALYSE=ja stopt install.sh op de eerste regel, vóór er iets verandert"
fresh
fake_bin=$work/bin
mkdir -p "$fake_bin"
for name in apt-get dpkg docker git systemctl curl install chown mountpoint id hostname; do
  printf '#!/bin/sh\necho "%s $*" >>"%s"\nexit 99\n' "$name" "$work/calls.log" >"$fake_bin/$name"
  chmod +x "$fake_bin/$name"
done
cp "$ENV_FILE" "$work/env.voor"
: >"$work/calls.log"
code=0
out=$(env ANALYSE=ja INSTALL_LIB='' PLATFORM_ROOT="$PLATFORM_ROOT" PATH="$fake_bin:$PATH" bash "$install_sh" 2>&1) || code=$?
check "exit 1, met uitleg" eval '[ "$code" = 1 ] && grep -qF "ANALYSE moet aan of uit zijn (nu: ja)" <<<"$out"'
check "geen enkel commando aangeroepen" test ! -s "$work/calls.log"
check ".env onveranderd" cmp -s "$ENV_FILE" "$work/env.voor"

# ============================================================================================
echo "== instellingen: zonder ANALYSE of met uit komt er niets bij; bij aan de sleutel één keer"
fresh
cp "$ENV_FILE" "$work/env.voor"
instellingen "" "" "" "" >/dev/null
check "zonder ANALYSE: .env byte voor byte gelijk" cmp -s "$ENV_FILE" "$work/env.voor"
instellingen "" "" "" uit >/dev/null
check "ANALYSE=uit gevraagd zonder regel: nog steeds gelijk (geen nieuwe regel)" cmp -s "$ENV_FILE" "$work/env.voor"
instellingen "" "" "" aan >/dev/null
key=$(value ANALYSE_WORKER_KEY)
check "aan: ANALYSE=aan" has_line "ANALYSE=aan"
check "aan: een sleutel van 64 hex" eval '[[ $key =~ ^[0-9a-f]{64}$ ]]'
check "aan: MODELLEN_ROOT en SLEUTELS_ROOT buiten DATA_ROOT" eval 'has_line "MODELLEN_ROOT=$PLATFORM_ROOT/modellen" && has_line "SLEUTELS_ROOT=$PLATFORM_ROOT/sleutels/gezichten"'
check "aan: de sleutel verschilt van die van media en office (die zijn er hier niet)" test "$(grep -c "$key" "$ENV_FILE")" = 1
check "aan: precies vier regels erbij, de rest staat er nog" eval '[ "$(wc -l <"$ENV_FILE")" = 12 ] && [ "$(head -n 8 "$ENV_FILE")" = "$(cat "$work/env.voor")" ]'
check "chmod 600" test "$(stat -c %a "$ENV_FILE")" = 600
instellingen "" "" "" "" >/dev/null
check "zonder vraag: blijft aan, dezelfde sleutel" eval 'has_line "ANALYSE=aan" && [ "$(value ANALYSE_WORKER_KEY)" = "$key" ]'
instellingen "" "" "" uit >/dev/null
check "uit gevraagd: ANALYSE=uit, de sleutel blijft" eval 'has_line "ANALYSE=uit" && [ "$(value ANALYSE_WORKER_KEY)" = "$key" ]'
instellingen "" "" "" "" >/dev/null
check "zonder vraag blijft uit staan" has_line "ANALYSE=uit"
instellingen "" "" "" aan >/dev/null
check "weer aan: dezelfde sleutel (nooit vervangen)" eval 'has_line "ANALYSE=aan" && [ "$(value ANALYSE_WORKER_KEY)" = "$key" ]'
check "één regel per sleutel" eval '[ "$(grep -c "^ANALYSE=" "$ENV_FILE")" = 1 ] && [ "$(grep -c "^ANALYSE_WORKER_KEY=" "$ENV_FILE")" = 1 ] && [ "$(grep -c "^MODELLEN_ROOT=" "$ENV_FILE")" = 1 ]'
check "een lege sleutel (zoals .env.example) wordt gevuld" eval '
  set_key ANALYSE_WORKER_KEY ""; instellingen "" "" "" "" >/dev/null; [[ $(value ANALYSE_WORKER_KEY) =~ ^[0-9a-f]{64}$ ]]'
# Een lege sleutel vullen gaat via set_key: de sleutel komt nooit in de argv van een ander proces
# (sed -i had hem in zijn argv; review golf 5, veilig F5). Een PATH met een nep-sed, -awk, -mv, …
# die hun argv opschrijven en dan het echte commando starten.
shim=$work/shim
mkdir -p "$shim"
for c in sed awk gawk mawk perl python3 grep tail head cat cp mv mktemp chmod chown tee dd od tr install ed; do
  real=$(command -v "$c") || continue
  cat >"$shim/$c" <<SH
#!/bin/sh
printf '%s\n' "$c \$*" >>"$work/argv.log"
exec "$real" "\$@"
SH
  chmod 755 "$shim/$c"
done
set_key ANALYSE_WORKER_KEY ""
: >"$work/argv.log"
PATH=$shim:$PATH instellingen "" "" "" "" >/dev/null
key=$(value ANALYSE_WORKER_KEY)
check "de lege sleutel gevuld via de nep-PATH (64 hex)" eval '[[ $key =~ ^[0-9a-f]{64}$ ]]'
check "de nep-PATH werkte (grep en mv staan in het logboek)" eval 'grep -q "^grep " "$work/argv.log" && grep -q "^mv " "$work/argv.log"'
check "de sleutel staat in de argv van geen enkel proces" not grep -qF "$key" "$work/argv.log"
printf 'ANALYSE_X=1\nANALYSE=aan\nLAATSTE=zonder' >"$work/set_key.env"
chmod 640 "$work/set_key.env"
check "set_key: letterlijk (| & \\), alleen die sleutel, rechten en een laatste regel zonder regeleinde blijven" eval '
  ENV_FILE=$work/set_key.env set_key ANALYSE "a|b&c\\d" && ENV_FILE=$work/set_key.env set_key LAATSTE "nu" &&
  [ "$(cat "$work/set_key.env"; echo .)" = "$(printf "ANALYSE_X=1\nANALYSE=a|b&c\\\\d\nLAATSTE=nu.")" ] &&
  [ "$(stat -c %a "$work/set_key.env")" = 640 ] && [ -z "$(ls "$work" | grep "^set_key.env.")" ]'
base_env
check "met ANALYSE_STANDAARD=aan (I2): een ontbrekende ANALYSE wordt aan" eval '
  ANALYSE_STANDAARD=aan; instellingen "" "" "" "" >/dev/null; ANALYSE_STANDAARD=uit; has_line "ANALYSE=aan" && [ -n "$(value ANALYSE_WORKER_KEY)" ]'
base_env
without_analyse
instellingen "" "" "" "" >/dev/null
check "een versie zonder analyse (0.8.0) zonder vraag: niets" cmp -s "$ENV_FILE" "$work/env.voor"
instellingen "" "" "" aan >/dev/null
check "een versie zonder analyse bewaart de wens aan, zonder sleutel" eval 'has_line "ANALYSE=aan" && ! grep -q "^ANALYSE_WORKER_KEY=" "$ENV_FILE"'
make_src

echo "== compose_files: het vierde argument"
fakes
compose_files 0 0 0 1
check "aan, met grenzen: compose.analyse.yaml en de grenzen, als laatste" eval '[ "${compose[*]: -4}" = "-f $SRC/infra/single/compose.analyse.yaml -f $SRC/infra/single/compose.analyse-limits.yaml" ]'
LIMITS="false false"
compose_files 0 0 0 1
check "aan, zonder grenzen: alleen compose.analyse.yaml" eval '[ "${compose[*]: -2}" = "-f $SRC/infra/single/compose.analyse.yaml" ] && [[ "${compose[*]}" != *analyse-limits* ]]'
LIMITS="true true"
compose_files 0 0 0 0
check "uit: geen bestand van de analyse" eval '[[ "${compose[*]}" != *analyse* ]]'
compose_files 0 0 0
check "zonder vierde argument (zoals 0.8.0): geen bestand van de analyse" eval '[[ "${compose[*]}" != *analyse* ]]'

# ============================================================================================
echo "== analyse_dirs: de mappen buiten DATA_ROOT (K1-06)"
fresh
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
check "nieuw: MODELLEN_ROOT 0755 van root, SLEUTELS_ROOT 0700 van 10001" eval 'analyse_dirs >/dev/null &&
  [ "$(owner_mode "$MODELLEN_ROOT")" = "0:0 755" ] && [ "$(owner_mode "$SLEUTELS_ROOT")" = "10001:10001 700" ] &&
  [ "$(owner_mode "$PLATFORM_ROOT/sleutels")" = "0:0 755" ]'
check "nog een keer: hetzelfde" eval 'analyse_dirs >/dev/null && [ "$(owner_mode "$SLEUTELS_ROOT")" = "10001:10001 700" ]'
rmdir "$SLEUTELS_ROOT"
mkdir -m 755 "$SLEUTELS_ROOT"
check "een SLEUTELS_ROOT van root (Docker maakte hem): wordt van 10001, 0700" eval 'analyse_dirs >/dev/null && [ "$(owner_mode "$SLEUTELS_ROOT")" = "10001:10001 700" ]'
# weigert: elke keer niets aangemaakt
refuses() {
  local why=$1 m=$2 s=$3 out
  rm -rf "$work/nieuw"
  out=$(MODELLEN_ROOT=$m SLEUTELS_ROOT=$s analyse_dirs 2>&1) && return 1
  grep -qF -- "$why" <<<"$out" && [ ! -e "$work/nieuw" ] && host_same
}
check "MODELLEN_ROOT in DATA_ROOT: weigert" refuses "ligt in DATA_ROOT ($DATA_ROOT)" "$DATA_ROOT/modellen" "$work/nieuw/s"
check "SLEUTELS_ROOT in DATA_ROOT: weigert" refuses "ligt in DATA_ROOT" "$work/nieuw/m" "$DATA_ROOT/sleutels"
check "DATA_ROOT in MODELLEN_ROOT: weigert" refuses "DATA_ROOT ($DATA_ROOT) ligt in $work" "$work" "$work/nieuw/s"
check "een relatief pad: weigert" refuses "is geen absoluut pad" "modellen" "$work/nieuw/s"
check "met ..: weigert" refuses "zonder . of .. of //" "$work/nieuw/../modellen" "$work/nieuw/s"
check "de ene in de andere: weigert" refuses "ligt in MODELLEN_ROOT" "$work/nieuw/m" "$work/nieuw/m/s"
ln -s "$host/map" "$work/link"
check "een deel van het pad is een symlink: weigert, de map van de host blijft leeg" refuses "$work/link is een symlink" "$work/link/modellen" "$work/nieuw/s"
check "MODELLEN_ROOT zelf een symlink: weigert" refuses "$work/link is een symlink" "$work/link" "$work/nieuw/s"
mkdir -m 777 "$work/open"
check "een map die iedereen mag beschrijven (zonder sticky bit): weigert" refuses "mag door anderen dan root beschreven worden" "$work/open/modellen" "$work/nieuw/s"
mkdir -m 1777 "$work/sticky"
check "met de sticky bit (zoals /tmp): mag" eval 'MODELLEN_ROOT=$work/sticky/m SLEUTELS_ROOT=$work/sticky/s analyse_dirs >/dev/null && [ "$(owner_mode "$work/sticky/m")" = "0:0 755" ]'
# Als 10001: een map van de app buiten DATA_ROOT, met een symlink van de app op de plek van een
# model en van de map van de sleutels
mkdir -m 755 "$work/app"
chown 10001:10001 "$work/app"
setpriv --reuid=10001 --regid=10001 --clear-groups -- sh -c 'mkdir "$1/modellen" && ln -s "$2/bestand" "$1/modellen/a.onnx" && ln -s "$2/map" "$1/sleutels"' sh "$work/app" "$host"
check "10001 zette een symlink op een model in zijn eigen map: weigert (niet van root)" refuses "$work/app is niet van root" "$work/app/modellen" "$work/nieuw/s"
check "10001 zette de map van de sleutels als symlink: weigert" refuses "$work/app is niet van root" "$work/nieuw/m" "$work/app/sleutels"
check "de symlinks van 10001 staan er nog, onaangeraakt" eval '[ "$(readlink "$work/app/modellen/a.onnx")" = "$host/bestand" ] && [ "$(readlink "$work/app/sleutels")" = "$host/map" ]'

# ============================================================================================
echo "== modellen_ophalen: van het nepnetwerk, met grootte en sha256"
fresh
analyse_dirs >/dev/null
: >"$work/curl.log"
: >"$work/http.log"
modellen_ophalen >"$work/m.out" 2>&1
check "beide modellen geladen, c.onnx (url -) niet gehaald" eval '[ "$(grep -c "^curl " "$work/curl.log")" = 2 ] && ! grep -q c.onnx "$work/http.log"'
check "MODELS_CHANGED=1, niets mislukt" eval '[ "$MODELS_CHANGED" = 1 ] && [ -z "$MODELS_FAILED" ]'
check "de bytes kloppen" eval 'cmp -s "$MODELLEN_ROOT/a.onnx" "$net/a.onnx" && cmp -s "$MODELLEN_ROOT/map/b.onnx" "$net/map/b.onnx"'
check "0644 van root, de map 0755 van root" eval '[ "$(owner_mode "$MODELLEN_ROOT/a.onnx")" = "0:0 644" ] && [ "$(owner_mode "$MODELLEN_ROOT/map/b.onnx")" = "0:0 644" ] && [ "$(owner_mode "$MODELLEN_ROOT/map")" = "0:0 755" ]'
check "curl met -f, --retry 3, --max-filesize <bytes> en een eigen map onder MODELLEN_ROOT" grep -qE -- "^curl -fsSL --retry 3 --connect-timeout 20 --max-time 600 --max-filesize $(size "$net/a.onnx") -o $MODELLEN_ROOT/\.ophalen\.[A-Za-z0-9]+/model $base/a.onnx$" "$work/curl.log"
check "de tijdelijke map is weg" test -z "$(find "$MODELLEN_ROOT" -name '.ophalen.*')"
# Drie licenties sinds golf 5: YuNet en SFace (X2a) en SigLIP 2 (X3a), K1-13
check "de drie licenties ernaast, 0644 van root, gelijk aan api/app/analyse/licenties" eval 'for f in "$top"/api/app/analyse/licenties/*.txt; do cmp -s "$f" "$MODELLEN_ROOT/${f##*/}" && [ "$(owner_mode "$MODELLEN_ROOT/${f##*/}")" = "0:0 644" ] || exit 1; done; [ "$(cd "$MODELLEN_ROOT" && echo *.txt)" = "sface-Apache-2.0.txt siglip2-Apache-2.0.txt yunet-MIT.txt" ]'
check "met de uitvoer" eval 'grep -qxF "Model geladen: a.onnx" "$work/m.out" && grep -qxF "Model geladen: map/b.onnx" "$work/m.out"'
: >"$work/curl.log"
modellen_ophalen >"$work/m.out" 2>&1
check "tweede run: niets gehaald, MODELS_CHANGED=0, geen uitvoer" eval '[ ! -s "$work/curl.log" ] && [ "$MODELS_CHANGED" = 0 ] && [ ! -s "$work/m.out" ]'
check "elke curl van de modellen krijgt </dev/null" test -z "$(grep -n 'curl -fsSL --retry 3' "$install_sh" | grep -v '</dev/null')"

echo "== een model dat niet klopt, te groot is, of niet te halen: niet fataal, niets achter"
cp "$net/a.onnx" "$work/a.goed"
head -c 3000 /dev/urandom >"$net/a.onnx"  # dezelfde grootte, een andere sha256
rm -f "$MODELLEN_ROOT/a.onnx"
code=0
modellen_ophalen >"$work/m.out" 2>&1 || code=$?
check "verkeerde sha256: return 0, met de melding van §3.11" eval '[ "$code" = 0 ] && grep -qxF "Let op: a.onnx kon niet geladen worden; gezichten wachten tot de volgende installatie." "$work/m.out"'
check "verkeerde sha256: niet neergezet, geen tijdelijke map, b blijft" eval '[ ! -e "$MODELLEN_ROOT/a.onnx" ] && [ -z "$(find "$MODELLEN_ROOT" -name ".ophalen.*")" ] && cmp -s "$MODELLEN_ROOT/map/b.onnx" "$net/map/b.onnx" && [ "$MODELS_FAILED" = a.onnx ]'
head -c 9000 /dev/urandom >"$net/a.onnx"  # groter dan de lock zegt
: >"$work/curl.log"
modellen_ophalen >"$work/m.out" 2>&1
check "te groot: --max-filesize stopt curl, niet neergezet" eval '[ ! -e "$MODELLEN_ROOT/a.onnx" ] && grep -qF "Let op: a.onnx kon niet geladen worden" "$work/m.out" && grep -qF -- "--max-filesize 3000 " "$work/curl.log"'
cp "$work/a.goed" "$net/a.onnx"
kill "$server_pid"
wait "$server_pid" 2>/dev/null
code=0
modellen_ophalen >"$work/m.out" 2>&1 || code=$?
check "geen server: return 0, niet neergezet" eval '[ "$code" = 0 ] && [ ! -e "$MODELLEN_ROOT/a.onnx" ] && [ "$MODELS_FAILED" = a.onnx ]'
rm -f "$work/port"
python3 -I "$work/server.py" "$net" "$work/port" "$work/http.log" &
server_pid=$!
for _ in $(seq 1 50); do [ -s "$work/port" ] && break; sleep 0.1; done
port=$(cat "$work/port")
base=http://127.0.0.1:$port
write_lock
# shellcheck disable=SC2329  # modellen_ophalen roept hem aan
df() { printf 'Avail\n1000\n'; }
: >"$work/curl.log"
modellen_ophalen >"$work/m.out" 2>&1
check "te weinig ruimte: niets gehaald, met uitleg" eval '[ ! -s "$work/curl.log" ] && grep -qF "Let op: te weinig ruimte in $MODELLEN_ROOT" "$work/m.out" && grep -qxF "Let op: a.onnx kon niet geladen worden; gezichten wachten tot de volgende installatie." "$work/m.out"'
unset -f df
modellen_ophalen >"$work/m.out" 2>&1
check "daarna wel: a geladen, map/b stond er al" eval 'cmp -s "$MODELLEN_ROOT/a.onnx" "$net/a.onnx" && [ "$(grep -c "^Model geladen" "$work/m.out")" = 1 ]'

echo "== symlinks op de plek van een model of een map: niet aangeraakt (K1-06)"
rm -f "$MODELLEN_ROOT/a.onnx"
ln -s "$host/bestand" "$MODELLEN_ROOT/a.onnx"
rm -rf "$MODELLEN_ROOT/map"
ln -s "$host/map" "$MODELLEN_ROOT/map"
: >"$work/curl.log"
code=0
modellen_ophalen >"$work/m.out" 2>&1 || code=$?
check "return 0; beide mislukt, met uitleg" eval '[ "$code" = 0 ] && [ "$MODELS_FAILED" = "a.onnx map/b.onnx" ] &&
  grep -qxF "$MODELLEN_ROOT/a.onnx is een symlink of geen gewoon bestand; dit script raakt het niet aan." "$work/m.out" &&
  grep -qxF "$MODELLEN_ROOT/map is een symlink of geen map; dit script raakt het niet aan." "$work/m.out"'
check "het bestand en de map van de host blijven, de symlinks ook" eval 'host_same && [ -L "$MODELLEN_ROOT/a.onnx" ] && [ -L "$MODELLEN_ROOT/map" ]'
rm -f "$MODELLEN_ROOT/a.onnx" "$MODELLEN_ROOT/map"

echo "== oude modellen weg, gewijzigde opnieuw"
modellen_ophalen >/dev/null 2>&1
printf 'oud' >"$MODELLEN_ROOT/oud.onnx"
mkdir -p "$MODELLEN_ROOT/x"
printf 'oud' >"$MODELLEN_ROOT/x/ook-oud.onnx"
printf 'blijft' >"$MODELLEN_ROOT/notitie.txt"
modellen_ophalen >"$work/m.out" 2>&1
check "*.onnx die niet in de lock staan: weg, met uitvoer; een ander bestand blijft" eval '[ ! -e "$MODELLEN_ROOT/oud.onnx" ] && [ ! -e "$MODELLEN_ROOT/x/ook-oud.onnx" ] &&
  [ -e "$MODELLEN_ROOT/notitie.txt" ] && grep -qxF "Oud model weg: oud.onnx" "$work/m.out" && [ "$MODELS_CHANGED" = 1 ]'
printf 'c' >"$MODELLEN_ROOT/c.onnx"
modellen_ophalen >/dev/null 2>&1
check "het model van url - (de host maakt het) blijft staan" test -e "$MODELLEN_ROOT/c.onnx"
head -c 3000 /dev/urandom >"$net/a.onnx"
write_lock
: >"$work/curl.log"
modellen_ophalen >/dev/null 2>&1
check "een nieuw model in de lock: opnieuw gehaald, MODELS_CHANGED=1" eval 'cmp -s "$MODELLEN_ROOT/a.onnx" "$net/a.onnx" && [ "$(grep -c "^curl " "$work/curl.log")" = 1 ] && [ "$MODELS_CHANGED" = 1 ]'
printf '0000 1 ../weg.onnx %s/a.onnx\n' "$base" >>"$LOCK"
modellen_ophalen >"$work/m.out" 2>&1
check "een regel die niet klopt (..): overgeslagen, niets buiten MODELLEN_ROOT" eval 'grep -qF "Let op: een regel van models.lock klopt niet" "$work/m.out" && [ ! -e "$PLATFORM_ROOT/weg.onnx" ]'
write_lock

echo "== de regels van zoeken op beeld (siglip2-b32-256/…): niet hier, maar in de stap BEELD"
# Review golf 5, uitrol F3: met de regels van SigLIP in models.lock haalt modellen_ophalen (de
# gezichten) de bronnen niet (1,5 GB); die zijn van de stap BEELD van X3b (Keuze 84). Een
# omzetting die er al staat, blijft staan.
mkdir -p "$net/siglip2-b32-256/bron"
head -c 4000 /dev/urandom >"$net/siglip2-b32-256/bron/model.safetensors"
printf 'beeld' >"$work/beeld.int8.onnx"
{
  printf '%s %s siglip2-b32-256/bron/model.safetensors %s/siglip2-b32-256/bron/model.safetensors\n' \
    "$(sha "$net/siglip2-b32-256/bron/model.safetensors")" "$(size "$net/siglip2-b32-256/bron/model.safetensors")" "$base"
  printf '%s 5 siglip2-b32-256/beeld.int8.onnx -\n' "$(sha "$work/beeld.int8.onnx")"
} >>"$LOCK"
mkdir -m 0755 "$MODELLEN_ROOT/siglip2-b32-256"
install -m 0644 "$work/beeld.int8.onnx" "$MODELLEN_ROOT/siglip2-b32-256/beeld.int8.onnx"
: >"$work/curl.log"
: >"$work/http.log"
modellen_ophalen >"$work/m.out" 2>&1
check "de bron van SigLIP: geen curl, geen map bron, niets mislukt of veranderd, geen uitvoer" eval '[ ! -s "$work/curl.log" ] && [ ! -s "$work/http.log" ] && [ ! -e "$MODELLEN_ROOT/siglip2-b32-256/bron" ] &&
  [ -z "$MODELS_FAILED" ] && [ "$MODELS_CHANGED" = 0 ] && [ ! -s "$work/m.out" ]'
check "de omzetting die er al staat, blijft staan" cmp -s "$MODELLEN_ROOT/siglip2-b32-256/beeld.int8.onnx" "$work/beeld.int8.onnx"
rm -rf "$MODELLEN_ROOT/siglip2-b32-256" "$net/siglip2-b32-256" "$work/beeld.int8.onnx"
write_lock

# ============================================================================================
echo "== main zonder ANALYSE: precies wat 0.8.0 doet"
fresh
run_main "$work/install-080.sh"
cp "$work/main.out" "$work/main.080"
cp "$LOG" "$work/docker.080"
cp "$ENV_FILE" "$work/env.080"
tree >"$work/tree.080"
check "0.8.0 loopt (exit 0, Klaar.)" eval '[ "$MAIN_RC" = 0 ] && said_part "Klaar. Het platform draait op"'
fresh
run_main "$install_sh"
check "deze versie: exit 0" test "$MAIN_RC" = 0
check "dezelfde uitvoer" cmp -s "$work/main.out" "$work/main.080"
check "dezelfde aanroepen van Docker" cmp -s "$LOG" "$work/docker.080"
check "dezelfde .env" cmp -s "$ENV_FILE" "$work/env.080"
check "dezelfde mappen en bestanden (geen modellen, geen sleutels)" eval 'tree | cmp -s - "$work/tree.080"'
check "geen curl" test ! -s "$work/curl.log"
check "nergens analyse" eval '! grep -qi analyse "$work/main.out" "$LOG" "$ENV_FILE"'

echo "== main met ANALYSE=uit (uit de omgeving): dezelfde aanroepen, .env en mappen"
fresh
run_main "$install_sh" ANALYSE=uit
check "exit 0" test "$MAIN_RC" = 0
check "dezelfde aanroepen van Docker als 0.8.0" cmp -s "$LOG" "$work/docker.080"
check "dezelfde .env (geen nieuwe regel)" cmp -s "$ENV_FILE" "$work/env.080"
check "dezelfde mappen en bestanden" eval 'tree | cmp -s - "$work/tree.080"'
check "geen curl" test ! -s "$work/curl.log"
check "de uitvoer: alleen de regels over de analyse erbij" eval 'grep -vE "^(== Analyse \(gezichten\)|Analyse: uit \(ANALYSE=uit in .*\)|Gezichten \(de container analyse\): uit \(ANALYSE=uit\)\.|  Aan: .*ANALYSE=aan bash)$" "$work/main.out" | sed "/^$/N;/^\n$/D" | cmp -s - <(sed "/^$/N;/^\n$/D" "$work/main.080")'

# ============================================================================================
echo "== main met ANALYSE=aan: de eerste keer"
fresh
run_main "$install_sh" ANALYSE=aan
key=$(value ANALYSE_WORKER_KEY)
dc_files="-f $SRC/infra/single/compose.yaml -f $SRC/infra/single/compose.analyse.yaml -f $SRC/infra/single/compose.analyse-limits.yaml"
check "exit 0, Klaar." eval '[ "$MAIN_RC" = 0 ] && said_part "Klaar. Het platform draait op"'
check ".env: aan, een sleutel, de mappen" eval 'has_line ANALYSE=aan && [[ $key =~ ^[0-9a-f]{64}$ ]] && has_line "MODELLEN_ROOT=$PLATFORM_ROOT/modellen"'
check "de stap en de stand" eval 'said "== Analyse (gezichten)" && said "Analyse: aan (modellen in $PLATFORM_ROOT/modellen)"'
check "de modellen geladen" eval 'cmp -s "$PLATFORM_ROOT/modellen/a.onnx" "$net/a.onnx" && cmp -s "$PLATFORM_ROOT/modellen/map/b.onnx" "$net/map/b.onnx"'
check "de map van de sleutels: 0700 van 10001" test "$(owner_mode "$PLATFORM_ROOT/sleutels/gezichten")" = "10001:10001 700"
check "de app met compose.analyse.yaml en de grenzen, met --remove-orphans" logged "docker compose --project-directory $SRC/infra/single --env-file $ENV_FILE $dc_files up -d --build --remove-orphans platform"
check "daarna de container: --no-deps, --force-recreate (nieuwe modellen)" logged "$dc_files up -d --no-deps --force-recreate analyse"
check "daarna analyse-status --wait 20" logged "$dc_files exec -T platform python -m app.cli analyse-status --wait 20"
check "de volgorde: app, container, stand" eval '[ "$(grep -n "" "$LOG" | grep -E "up -d --build|up -d --no-deps|analyse-status" | cut -d: -f2- | sed -E "s/.* (up -d --build|up -d --no-deps|analyse-status).*/\1/" | tr "\n" ,)" = "up -d --build,up -d --no-deps,analyse-status," ]'
check "de stand in de uitvoer" said "Analyse: ok"
check "de eindtekst" eval 'said "Gezichten (de container analyse): aan." && said_part "  Uit: curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/main/infra/single/install.sh | ANALYSE=uit bash"'
check "geen Let op" not said_part "Let op: "
models_sum "$PLATFORM_ROOT/modellen" >"$work/modellen.voor"
setpriv --reuid=10001 --regid=10001 --clear-groups -- sh -c 'umask 077 && printf geheim >"$1/eigenaar-1.key"' sh "$PLATFORM_ROOT/sleutels/gezichten"

echo "== nog een keer zonder ANALYSE: niets opnieuw, geen --force-recreate, dezelfde sleutel"
run_main "$install_sh"
check "exit 0, geen curl" eval '[ "$MAIN_RC" = 0 ] && [ ! -s "$work/curl.log" ]'
check "de container zonder --force-recreate" eval 'logged "$dc_files up -d --no-deps analyse" && ! logged "force-recreate"'
check "dezelfde sleutel" test "$(value ANALYSE_WORKER_KEY)" = "$key"
check "zonder grenzen (geen cgroup-controllers): geen compose.analyse-limits.yaml" eval 'LIMITS="false false" run_main "$install_sh"; ! logged analyse-limits && logged "-f $SRC/infra/single/compose.analyse.yaml up -d --no-deps analyse"'

echo "== de container start niet, of de stand is niet ok: alleen een waarschuwing"
UP_RC=1
run_main "$install_sh"
check "start faalt: exit 0, Let op, geen analyse-status" eval '[ "$MAIN_RC" = 0 ] && said "Let op: de container analyse kon niet starten (de melding van Docker staat hierboven); het platform werkt verder zonder gezichten." && ! logged analyse-status'
check "start faalt: de eindtekst zegt het, met opnieuw proberen" eval 'said "Gezichten (de container analyse): aan, maar de container analyse kon niet starten (de melding van Docker staat hierboven)." && said_part "  Opnieuw proberen: "'
UP_RC=0
STATUS_OUT="Analyse: models_missing (map/b.onnx)"
run_main "$install_sh"
check "de stand is niet ok: exit 0 en Let op" eval '[ "$MAIN_RC" = 0 ] && said "Analyse: models_missing (map/b.onnx)" && said "Let op: de analyse meldt een probleem (zie hierboven); het platform werkt verder."'
STATUS_OUT="Analyse: ok"

echo "== een model dat niet te halen is: de container start toch (models_missing)"
mv "$net/map/b.onnx" "$work/b.weg"
rm -f "$PLATFORM_ROOT/modellen/map/b.onnx"
run_main "$install_sh"
check "exit 0, Let op met de naam, de container start (zonder --force-recreate: er kwam niets nieuws)" eval '[ "$MAIN_RC" = 0 ] && said "Let op: map/b.onnx kon niet geladen worden; gezichten wachten tot de volgende installatie." && logged "$dc_files up -d --no-deps analyse"'
check "de eindtekst: aan, maar niet alle modellen" said "Gezichten (de container analyse): aan, maar niet alle modellen zijn er (map/b.onnx); gezichten wachten tot de volgende installatie."
mv "$work/b.weg" "$net/map/b.onnx"
run_main "$install_sh"
check "de volgende run haalt hem wel, met --force-recreate" eval 'cmp -s "$PLATFORM_ROOT/modellen/map/b.onnx" "$net/map/b.onnx" && logged "$dc_files up -d --no-deps --force-recreate analyse"'

echo "== een map die niet klopt: deze run zonder analyse, het platform start"
sed -i "s|^MODELLEN_ROOT=.*|MODELLEN_ROOT=$work/data/modellen|" "$ENV_FILE"
run_main "$install_sh"
check "exit 0, de reden, geen bestand van de analyse in compose, geen container" eval '[ "$MAIN_RC" = 0 ] && said_part "ligt in DATA_ROOT" &&
  said "Analyse: uit (de mappen voor de analyse kloppen niet (zie hierboven); het platform draait deze keer zonder)" && ! logged compose.analyse.yaml && ! logged "up -d --no-deps"'
check "niets in DATA_ROOT aangemaakt" test ! -e "$work/data/modellen"
sed -i "s|^MODELLEN_ROOT=.*|MODELLEN_ROOT=$PLATFORM_ROOT/modellen|" "$ENV_FILE"
sed -i "s|^ANALYSE=.*|ANALYSE=ja|" "$ENV_FILE"
run_main "$install_sh"
check "ANALYSE=ja in .env: uit, met uitleg, geen container" eval '[ "$MAIN_RC" = 0 ] && said "Analyse: uit (ANALYSE in $ENV_FILE moet aan of uit zijn (nu: ja); de analyse blijft uit)" && ! logged compose.analyse.yaml'
sed -i "s|^ANALYSE=.*|ANALYSE=aan|" "$ENV_FILE"

echo "== ANALYSE=uit na aan: geen download, geen container, de modellen en sleutels blijven"
run_main "$install_sh" ANALYSE=uit
check "exit 0; ANALYSE=uit in .env, dezelfde sleutel" eval '[ "$MAIN_RC" = 0 ] && has_line ANALYSE=uit && [ "$(value ANALYSE_WORKER_KEY)" = "$key" ]'
check "geen curl, geen bestand van de analyse; de app met --remove-orphans (haalt de container weg)" eval '[ ! -s "$work/curl.log" ] && ! logged analyse && logged "up -d --build --remove-orphans platform"'
check "de modellen en de sleutel per eigenaar blijven" eval 'models_sum "$PLATFORM_ROOT/modellen" | cmp -s - "$work/modellen.voor" && [ "$(cat "$PLATFORM_ROOT/sleutels/gezichten/eigenaar-1.key")" = geheim ]'
check "de eindtekst zegt waar de modellen staan, en hoe het weer aan gaat" eval 'said "Gezichten (de container analyse): uit (ANALYSE=uit)." && said "  De modellen blijven in $PLATFORM_ROOT/modellen tot je die map weggooit." && said_part "| ANALYSE=aan bash"'
run_main "$install_sh"
check "de run daarna zonder ANALYSE: blijft uit" eval 'has_line ANALYSE=uit && ! logged analyse && [ ! -s "$work/curl.log" ]'
run_main "$install_sh" ANALYSE=aan
check "weer aan: dezelfde sleutel, niets opnieuw gehaald, de container weer" eval 'has_line ANALYSE=aan && [ "$(value ANALYSE_WORKER_KEY)" = "$key" ] && [ ! -s "$work/curl.log" ] && logged "up -d --no-deps analyse"'

echo "== terug naar 0.8.0 (het script van $BACK080_SHA) en weer vooruit"
cp "$ENV_FILE" "$work/env.voor"
run_main "$work/install-080.sh"
check "0.8.0: exit 0, Klaar." eval '[ "$MAIN_RC" = 0 ] && said_part "Klaar. Het platform draait op"'
check "0.8.0: geen bestand van de analyse; --remove-orphans haalt de container weg" eval '! logged analyse && logged "up -d --build --remove-orphans platform"'
check "0.8.0: .env, de modellen en de sleutels blijven" eval 'cmp -s "$ENV_FILE" "$work/env.voor" && models_sum "$PLATFORM_ROOT/modellen" | cmp -s - "$work/modellen.voor" &&
  [ "$(cat "$PLATFORM_ROOT/sleutels/gezichten/eigenaar-1.key")" = geheim ] && [ "$(owner_mode "$PLATFORM_ROOT/sleutels/gezichten")" = "10001:10001 700" ]'
run_main "$install_sh"
check "vooruit: dezelfde sleutel, niets gehaald, de container weer, zonder --force-recreate" eval '[ "$MAIN_RC" = 0 ] && [ "$(value ANALYSE_WORKER_KEY)" = "$key" ] && [ ! -s "$work/curl.log" ] &&
  logged "$dc_files up -d --no-deps analyse" && ! logged force-recreate'

einde
