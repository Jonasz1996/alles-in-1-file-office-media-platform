#!/usr/bin/env bash
# Zoeken en foto's in install.sh (bouwontwerp fase 6, Z7; Keuze 29, 52 en 53, §3.10). Twee delen:
#
#   bash infra/single/tests/test_install_search.sh             de functies
#   bash infra/single/tests/test_install_search.sh --stappen   de update en terug, echt
#
# Beide draaien in de job infra van CI; --stappen ook in de sandbox.
#
# De functies: install.sh met INSTALL_LIB=1, zoals test_install_media.sh. Er wordt niets
# geïnstalleerd: docker, curl, install en chown zijn nepfuncties. De reservekopie draait wel echt:
# de nep-docker voert bij `compose run … python -I -c <tekst>` die tekst uit met python3 -I, met
# /data op DATA_ROOT. Het controleert:
# - PLAATSEN uit de omgeving wint van .env; een foute waarde stopt install.sh op de eerste regel;
#   .env krijgt PLAATSEN=aan als hij ontbreekt; een versie zonder plaatsnamen (0.7.1) bewaart
#   alleen de wens; een foute waarde in .env stopt het script vóór er iets verandert;
# - compose.yaml geeft PLAATSEN door als PLATFORM_PLACES (standaard aan);
# - de download van de twee bronnen (Keuze 29) van een nepnetwerk: via .part, alleen met de
#   juiste grootte en sha256, rechten 640; een download die faalt (geen netwerk, een halve, een
#   verkeerde sha256) is niet fataal, laat niets achter en de volgende run probeert het opnieuw;
#   staan ze er al, dan haalt het niets; PLAATSEN=uit haalt niets;
# - de reservekopie (Keuze 52): met het image dat er nog draait, vóór office stopt en vóór de
#   nieuwe app; de tekst is precies api/app/backup.py van de nieuwe broncode; rechten 600, zonder
#   gezichten; een mislukte kopie (compose run faalt, of de kopie zelf) stopt het script vóór er
#   iets verandert; na twee runs op 0010 staat de kopie van 0009 er nog; een eerste installatie
#   en een versie zonder backup.py (0.7.1) slaan hem over;
# - geo-load in de draaiende app alleen als de bronnen net kwamen en de app niet opnieuw startte;
# - de eindtekst van §3.10 stap 7, met de plaatsnamen en de reservekopie;
# - de weg terug naar 0.7.1 en weer vooruit in §3.10, infra/single/README.md en
#   docs/runbooks/zoeken.md: precies de commando's, in die volgorde, met de volledige hash;
# - de voorcontrole van --stappen (met een nep-docker; er wordt niets gebouwd): een werkmap die
#   de test weigert, blijft zoals hij was, en het blok Samenvatting staat altijd als laatste.
#
# De installatietest (--stappen) staat onder stappen() hieronder, met zijn eigen uitleg.
#
# De variabelen en functies komen uit install.sh (shellcheck volgt de bron niet in CI):
# shellcheck disable=SC2034,SC2153,SC2154
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
top=$(cd "$here/../../.." && pwd)
install_sh=$here/../install.sh

# De weg terug (§3.10): de laatste commit vóór Z0, met 0.7.1 en migratie 0009. Alleen docs
# verschillen van 245d87a, de laatste commit met code van 0.7.1.
ROLLBACK_SHA=80d5bb8ce9cd937534f06a5bc5e4f54ec8509ad1
ROLLBACK_HEAD=0009
BRANCH_NAME=claude/stappenplan-uitwerken-lu4uv3
SEARCH_LINE="Zoeken op naam werkt meteen. De inhoud wordt nu op de achtergrond gelezen; volg het op /beheer → Zoeken en foto's."

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

# Een kleine database zoals die van de app: een alembic-head, een gebruiker, en een tabel face
# (X2) met merktekens die nooit in een reservekopie mogen staan
make_db() {
  python3 -I - "$1" "$2" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
c.execute("PRAGMA journal_mode = WAL")
c.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
c.execute("INSERT INTO alembic_version VALUES (?)", (sys.argv[2],))
c.execute('CREATE TABLE "user" (id INTEGER PRIMARY KEY, email TEXT)')
c.execute("""INSERT INTO "user" (email) VALUES ('eva@example.nl')""")
c.execute("CREATE TABLE face (id INTEGER PRIMARY KEY, data BLOB)")
c.executemany("INSERT INTO face (data) VALUES (?)", [(b"MERKTEKEN-GEZICHT-" * 300,)] * 3)
c.commit()
c.close()
PY
}
# Eén waarde uit een database (alleen lezen): de head, of een telling
db_get() {
  python3 -I -c 'import sqlite3, sys
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
print(c.execute(sys.argv[2]).fetchone()[0])' "$1" "$2"
}
db_sql() { python3 -I -c 'import sqlite3, sys; c = sqlite3.connect(sys.argv[1]); c.executescript(sys.argv[2]); c.close()' "$1" "$2"; }

# ============================================================================================
# Deel 1: de functies
# ============================================================================================

# shellcheck disable=SC2016  # check … eval '…': eval werkt de uitdrukking pas zelf uit
functies() {
  local work want out code gek name
  work=$(mktemp -d)
  # shellcheck disable=SC2064  # work staat nu vast
  trap "rm -rf '$work'" EXIT

  export PLATFORM_ROOT=$work/root INSTALL_LIB=1
  unset OFFICE OFFICE_IMAGE MEDIA_SANDBOX PLAATSEN
  # shellcheck source=/dev/null
  . "$install_sh"
  mkdir -p "$PLATFORM_ROOT" "$SRC/infra/single" "$SRC/api/app/geo"

  has_line() { grep -qxF "$1" "$ENV_FILE"; }
  value() { sed -n "s/^$1=//p" "$ENV_FILE"; }
  mode() { stat -c %a "$1"; }
  # De .env van 0.7.1: zonder PLAATSEN
  fase5_env() {
    cat >"$ENV_FILE" <<ENV
DATA_ROOT=$work/data
NODE_NAME=node1
PORT=80
HTTPS_PORT=443
SECURE_COOKIES=false
OFFICE=uit
MEDIA_SANDBOX=aan
ENV
    chmod 644 "$ENV_FILE"
  }
  # De broncode van deze versie (met plaatsnamen en de reservekopie) of van 0.7.1 (zonder)
  with_places() { : >"$SRC/api/app/geo/load.py"; }
  without_places() { rm -f "$SRC/api/app/geo/load.py"; }
  with_backup() { cp "$top/api/app/backup.py" "$SRC/api/app/backup.py"; }
  without_backup() { rm -f "$SRC/api/app/backup.py"; }
  with_places
  with_backup

  echo "== want_places: vóór het lezen van .env, alleen aan of uit"
  check "zonder PLAATSEN: leeg" test "$(want_places)" = ""
  check "PLAATSEN=aan" test "$(PLAATSEN=aan want_places)" = aan
  check "PLAATSEN=uit" test "$(PLAATSEN=uit want_places)" = uit
  for gek in ja Aan " uit" off 1; do
    if out=$(PLAATSEN=$gek want_places 2>&1); then
      fout "want_places weigert '$gek'"
    else
      check "want_places weigert '$gek' met uitleg" grep -qF "PLAATSEN moet aan of uit zijn (nu: $gek)" <<<"$out"
    fi
  done

  echo "== PLAATSEN=ja stopt install.sh op de eerste regel, vóór er iets verandert"
  local fake_bin=$work/bin envs env
  mkdir -p "$fake_bin"
  for name in apt-get dpkg docker git systemctl curl install chown mountpoint id hostname; do
    printf '#!/bin/sh\necho "%s $*" >>"%s"\nexit 99\n' "$name" "$work/calls.log" >"$fake_bin/$name"
    chmod +x "$fake_bin/$name"
  done
  fase5_env
  for env in "PLAATSEN=ja" "OFFICE=aan PLAATSEN=nee"; do
    read -r -a envs <<<"$env"
    : >"$work/calls.log"
    cp "$ENV_FILE" "$work/env.voor"
    code=0
    out=$(env "${envs[@]}" INSTALL_LIB='' PLATFORM_ROOT="$PLATFORM_ROOT" PATH="$fake_bin:$PATH" bash "$install_sh" 2>&1) || code=$?
    check "$env: exit 1" test "$code" = 1
    check "$env: met uitleg" grep -qF "PLAATSEN moet aan of uit zijn" <<<"$out"
    check "$env: geen enkel commando aangeroepen" test ! -s "$work/calls.log"
    check "$env: .env onveranderd" cmp -s "$ENV_FILE" "$work/env.voor"
  done

  # --- wat instellingen buiten .env doet, nagebootst (geen root nodig) ---------------------
  # install -d maakt de mappen echt (met hun rechten), zodat de reservekopie en de download er
  # kunnen schrijven; -o en -g kan alleen root
  # shellcheck disable=SC2329  # install.sh roept ze aan
  install() {
    printf 'install %s\n' "$*" >>"$work/install.log"
    local m="" dirs=()
    [ "${1-}" = -d ] || return 1
    shift
    while [ $# -gt 0 ]; do
      case "$1" in
        -m) m=$2 && shift 2 ;;
        -o | -g) shift 2 ;;
        *) dirs+=("$1") && shift ;;
      esac
    done
    mkdir -p "${dirs[@]}" 2>/dev/null || return 1
    [ -z "$m" ] || command chmod "$m" "${dirs[@]}"
  }
  # shellcheck disable=SC2329
  chown() { printf 'chown %s\n' "$*" >>"$work/install.log"; }
  # Als 10001 in een map (setpriv kan alleen root): hier als de gebruiker van de test, in die map.
  # De echte, als root met setpriv, staat onder "als root" hieronder en in --stappen (stap 4)
  # shellcheck disable=SC2329
  as_app_in() {
    printf 'als 10001 in %s: %s\n' "$1" "${*:2}" >>"$work/install.log"
    local dir=$1
    shift
    (cd -P -- "$dir" && "$@")
  }
  # shellcheck disable=SC2329
  mountpoint() { return 1; }
  # shellcheck disable=SC2329
  hostname() { echo 127.0.0.1; }

  echo "== instellingen: PLAATSEN=aan erbij, een gevraagde waarde wint, de rest blijft"
  fase5_env
  instellingen "" "" "" >/dev/null
  check "PLAATSEN=aan erbij (een .env van 0.7.1)" has_line "PLAATSEN=aan"
  check "precies acht regels: de rest staat er nog" test "$(wc -l <"$ENV_FILE")" = 8
  check "chmod 600" test "$(mode "$ENV_FILE")" = 600
  instellingen "" "" uit >/dev/null
  check "PLAATSEN=uit gevraagd: vervangt aan" has_line "PLAATSEN=uit"
  instellingen "" "" "" >/dev/null
  check "zonder vraag blijft uit staan" has_line "PLAATSEN=uit"
  instellingen "" "" aan >/dev/null
  check "PLAATSEN=aan gevraagd" has_line "PLAATSEN=aan"
  check "nog steeds één regel PLAATSEN" test "$(grep -c '^PLAATSEN=' "$ENV_FILE")" = 1
  want=$(PLAATSEN=uit want_places)
  instellingen "" "" "$want" >/dev/null
  check "PLAATSEN=uit uit de omgeving wint van aan in .env" test "$PLAATSEN" = uit
  check "een .env die eindigt zonder regeleinde: PLAATSEN op een eigen regel" eval '
    fase5_env; printf "NOG=iets" >>"$ENV_FILE"; instellingen "" "" "" >/dev/null
    has_line "NOG=iets" && has_line "PLAATSEN=aan"'
  without_places
  fase5_env
  instellingen "" "" uit >/dev/null
  check "een versie zonder plaatsnamen (0.7.1) bewaart de wens PLAATSEN=uit" has_line "PLAATSEN=uit"
  fase5_env
  instellingen "" "" "" >/dev/null
  check "een versie zonder plaatsnamen en zonder wens: niets in .env" not grep -q '^PLAATSEN=' "$ENV_FILE"
  with_places
  check ".env.example heeft precies één regel PLAATSEN=aan" test "$(grep -c '^PLAATSEN=' "$here/../.env.example")-$(grep -cx 'PLAATSEN=aan' "$here/../.env.example")" = 1-1

  echo "== check_places: een foute waarde in .env stopt het script vóór er iets verandert"
  local check_out
  PLAATSEN=ja
  code=0
  check_out=$(check_places) || code=$?
  check "PLAATSEN=ja in .env: return 1" test "$code" = 1
  check "met uitleg" grep -qF "PLAATSEN in $ENV_FILE moet aan of uit zijn (nu: ja)" <<<"$check_out"
  for PLAATSEN in aan uit ""; do
    check "PLAATSEN='$PLAATSEN' in .env is goed" check_places
  done
  PLAATSEN=ja
  without_places
  check "een versie zonder plaatsnamen kijkt er niet naar" check_places
  with_places
  unset PLAATSEN

  echo "== compose.yaml: PLAATSEN wordt PLATFORM_PLACES, standaard aan"
  if ! command docker compose version >/dev/null 2>&1; then
    fout "docker compose ontbreekt: PLATFORM_PLACES is niet gecontroleerd"
  else
    places_in_config() {
      command docker compose --project-directory "$here/.." --env-file "$1" -f "$here/../compose.yaml" \
        config --format json 2>/dev/null | python3 -I -c 'import json, sys
print(json.load(sys.stdin)["services"]["platform"]["environment"].get("PLATFORM_PLACES"))'
    }
    cp "$here/../.env.example" "$work/single.env"
    check "met .env.example: aan" test "$(places_in_config "$work/single.env")" = aan
    sed -i 's/^PLAATSEN=aan$/PLAATSEN=uit/' "$work/single.env"
    check "PLAATSEN=uit: uit" test "$(places_in_config "$work/single.env")" = uit
    sed -i '/^PLAATSEN=/d' "$work/single.env"
    check "zonder PLAATSEN (een .env van 0.7.1): aan" test "$(places_in_config "$work/single.env")" = aan
  fi

  echo "== de bronnen: twee regels, gelijk aan tools/geo/haal.py en api/app/geo/load.py"
  out=$(geo_bronnen | python3 -I -c '
import re, sys
lines = sys.stdin.read().splitlines()
pat = r"[A-Za-z0-9._-]+ [1-9][0-9]* [0-9a-f]{64} https://[^ /]+/[^ ]+"
print("ok" if len(lines) == 2 and all(re.fullmatch(pat, line) for line in lines) else repr(lines))')
  check "naam, bytes, sha256 en een https-adres ($out)" test "$out" = ok
  # De CI haalt ze vooraf met tools/geo/haal.py (--stappen); api/tests/test_install_geo.py
  # vergelijkt ze met api/app/geo/load.py
  out=$(geo_bronnen | python3 -I -c '
import ast, sys
tree = ast.parse(open(sys.argv[1], encoding="utf-8").read())
found = [ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and any(getattr(t, "id", "") == "BRONNEN" for t in n.targets)]
want = [" ".join((name, str(size), sha, url)) for name, url, size, sha in found[0]] if found else []
got = sys.stdin.read().splitlines()
print("ok" if got == want else "install.sh %r, haal.py %r" % (got, want))' "$top/tools/geo/haal.py")
  check "precies de bronnen van tools/geo/haal.py ($out)" test "$out" = ok

  # --- het nepnetwerk: twee kleine bronnen in plaats van de echte -------------------------
  mkdir -p "$work/net"
  printf 'landen van de installatietest\n' >"$work/net/landen.geojson"
  printf 'plaatsen van de installatietest\n' >"$work/net/plaatsen.tar.gz"
  for n in landen.geojson plaatsen.tar.gz; do
    printf '%s %s %s https://bron.invalid/pad/%s\n' "$n" "$(stat -c %s "$work/net/$n")" \
      "$(sha256sum "$work/net/$n" | cut -d' ' -f1)" "$n"
  done >"$work/bronnen"
  # shellcheck disable=SC2329  # geo_download roept ze aan
  geo_bronnen() { cat "$work/bronnen"; }
  # NET: aan, uit (geen verbinding), half (de verbinding valt weg na de helft), race (de app zet
  # terwijl curl loopt een symlink naar een bestand van de host op <bron>.part in de map geo) of
  # de naam van een bron die verkeerd binnenkomt (dezelfde grootte, een andere inhoud)
  NET=aan
  # shellcheck disable=SC2329
  curl() {
    printf 'curl %s\n' "$*" >>"$work/curl.log"
    local url=${!#} target="" prev="" a
    for a in "$@"; do
      [ "$prev" != -o ] || target=$a
      prev=$a
    done
    [ -n "$target" ] || return 2
    case "$NET" in
      uit) return 6 ;;
      race)
        ln -sfn "$work/host/bestand" "$geo_dir/${url##*/}.part"
        cp "$work/net/${url##*/}" "$target"
        ;;
      half)
        head -c 5 "$work/net/${url##*/}" >"$target"
        return 18
        ;;
      "${url##*/}") tr '[:lower:]' '[:upper:]' <"$work/net/${url##*/}" >"$target" ;;
      *) cp "$work/net/${url##*/}" "$target" ;;
    esac
  }
  geo() {
    : >"$work/curl.log"
    : >"$work/install.log"
    # Zoals main: set -e aan, en geo_download niet in een if, && of ||
    (
      set -e
      geo_download
      printf 'GEO_STATE=%s\n' "$GEO_STATE"
    ) >"$work/geo.out" 2>&1 || true
    sed -n 's/^GEO_STATE=//p' "$work/geo.out"
  }
  said_geo() { grep -qxF -- "$1" "$work/geo.out"; }
  geo_dir=$work/data/geo
  no_part() { [ -z "$(find "$geo_dir" -name '*.part' 2>/dev/null)" ]; }
  same_as_net() { cmp -s "$geo_dir/$1" "$work/net/$1"; }
  DATA_ROOT=$work/data
  rm -rf "$DATA_ROOT"
  mkdir -p "$DATA_ROOT" # zoals instellingen: DATA_ROOT is er altijd vóór de download

  echo "== de download (Keuze 29): niet fataal, en de volgende run probeert het opnieuw"
  PLAATSEN=uit
  check "PLAATSEN=uit: uit" test "$(geo)" = uit
  check "PLAATSEN=uit: met die regel" said_geo "Plaatsnamen: uit (PLAATSEN=uit); er wordt niets opgehaald."
  check "PLAATSEN=uit: geen curl" test ! -s "$work/curl.log"
  check "PLAATSEN=uit: geen map geo" test ! -e "$geo_dir"
  unset PLAATSEN
  NET=uit
  check "geen netwerk: mislukt, en het script loopt door" test "$(geo)" = mislukt
  check "geen netwerk: Plaatsnamen: niet geladen" said_geo "Plaatsnamen: niet geladen. Het platform werkt verder zonder; de volgende run probeert het opnieuw."
  check "geen netwerk: per bron een regel" test "$(grep -c '^Kon .* niet ophalen, of de sha256 klopt niet (https://bron.invalid/pad/' "$work/geo.out")" = 2
  check "geen netwerk: de map geo, 750, door 10001 zelf gemaakt" eval 'grep -qxF "als 10001 in $DATA_ROOT: mkdir -m 750 -- geo" "$work/install.log" && [ "$(mode "$geo_dir")" = 750 ]'
  check "geen netwerk: geen bestand en geen .part" test -z "$(ls -A "$geo_dir")"
  NET=half
  check "een halve download: mislukt" test "$(geo)" = mislukt
  check "een halve download: geen .part over" no_part
  check "een halve download: niets op zijn plek" test -z "$(ls -A "$geo_dir")"
  NET=landen.geojson
  check "een verkeerde sha256: mislukt" test "$(geo)" = mislukt
  check "een verkeerde sha256: die bron staat er niet" test ! -e "$geo_dir/landen.geojson"
  check "een verkeerde sha256: de andere wel" same_as_net plaatsen.tar.gz
  check "een verkeerde sha256: geen .part over" no_part
  NET=aan
  check "de volgende run met netwerk: nieuw" test "$(geo)" = nieuw
  check "alleen de ontbrekende bron gehaald" eval '[ "$(grep -c "^curl " "$work/curl.log")" = 1 ] && grep -q "pad/landen.geojson$" "$work/curl.log"'
  check "beide bronnen op hun plek, heel" eval 'same_as_net landen.geojson && same_as_net plaatsen.tar.gz'
  check "rechten 640" test "$(stat -c %a "$geo_dir/landen.geojson" "$geo_dir/plaatsen.tar.gz" | sort -u)" = 640
  check "door 10001 zelf neergezet, root schrijft niet in de map geo" eval 'grep -qF "als 10001 in $geo_dir: sh -c " "$work/install.log" && ! grep -qF "$geo_dir/" "$work/install.log"'
  check "geen .part over" no_part
  check "met die regel" said_geo "Plaatsnamen: de bronnen staan klaar in $geo_dir; de app laadt ze bij de start (±1 minuut)."
  : >"$work/curl.log"
  rm -f "$geo_dir"/*
  geo >/dev/null
  check "curl met -f, --max-filesize en de grootte van de bron" grep -qE -- "-fsSL --retry 2 --connect-timeout 20 --max-time 600 --max-filesize $(sed -n 's/^landen.geojson \([0-9]*\) .*/\1/p' "$work/bronnen") -o [^ ]+/landen.geojson https://bron.invalid/pad/landen.geojson$" "$work/curl.log"
  out=$(sed -n 's/.* -o \([^ ]*\)\/landen.geojson .*/\1/p' "$work/curl.log")
  check "curl schrijft in een eigen tijdelijke map buiten DATA_ROOT ($out), die daarna weg is" eval '[ -n "$out" ] && [ "${out#"$DATA_ROOT"}" = "$out" ] && [ ! -e "$out" ]'
  check "nog een run: klaar" test "$(geo)" = klaar
  check "nog een run: geen curl" test ! -s "$work/curl.log"
  check "nog een run: met die regel" said_geo "Plaatsnamen: de bronnen staan er al ($geo_dir)."
  printf 'kapot\n' >"$geo_dir/plaatsen.tar.gz"
  check "een kapotte bron op schijf: opnieuw gehaald" eval '[ "$(geo)" = nieuw ] && same_as_net plaatsen.tar.gz'
  without_places
  check "een versie zonder plaatsnamen: niets" eval '[ "$(geo)" = "" ] && [ ! -s "$work/curl.log" ] && [ "$(cat "$work/geo.out")" = GEO_STATE= ]'
  with_places
  rm -rf "$geo_dir"
  : >"$geo_dir"
  check "waar de map moet komen, staat een bestand: mislukt, niet fataal" test "$(geo)" = mislukt
  check "het bestand blijft" test -f "$geo_dir"
  check "geen curl" test ! -s "$work/curl.log"
  rm -f "$geo_dir"

  echo "== een symlink van de app in DATA_ROOT volgt het script nooit (review golf 4, veilig F2)"
  # DATA_ROOT is van de app: een app die gekraakt is, kan er symlinks neerzetten. Een map en twee
  # bestanden van de host (buiten DATA_ROOT) moeten blijven zoals ze waren
  local host=$work/host
  mkdir -p "$host/map"
  printf 'van de host\n' >"$host/bestand"
  printf 'ook van de host\n' >"$host/ander"
  command chmod 600 "$host/bestand" "$host/ander"
  command chmod 755 "$host/map"
  host_same() {
    [ "$(cat "$host/bestand")" = "van de host" ] && [ "$(cat "$host/ander")" = "ook van de host" ] &&
      [ "$(stat -c %a "$host/bestand" "$host/ander" "$host/map" | tr '\n' ' ')" = "600 600 755 " ] &&
      [ -z "$(ls -A "$host/map")" ]
  }
  NET=aan
  ln -s "$host/map" "$geo_dir"
  check "de map geo is een symlink: mislukt, niet fataal" test "$(geo)" = mislukt
  check "de map geo is een symlink: met uitleg" said_geo "$geo_dir is geen gewone map maar een symlink of een bestand; dit script raakt hem niet aan."
  check "de map geo is een symlink: geen curl, de map van de host blijft" eval '[ ! -s "$work/curl.log" ] && host_same'
  rm -f "$geo_dir"
  geo >/dev/null
  rm -f "$geo_dir"/*
  ln -s "$host/ander" "$geo_dir/plaatsen.tar.gz"
  NET=race
  check "een symlink op de .part terwijl curl loopt, en een bron als symlink: nieuw" test "$(geo)" = nieuw
  check "de bestanden van de host blijven zoals ze waren" host_same
  check "beide bronnen echte bestanden, heel, 640" eval '[ ! -L "$geo_dir/landen.geojson" ] && [ ! -L "$geo_dir/plaatsen.tar.gz" ] &&
    same_as_net landen.geojson && same_as_net plaatsen.tar.gz &&
    [ "$(stat -c %a "$geo_dir/landen.geojson" "$geo_dir/plaatsen.tar.gz" | sort -u)" = 640 ]'
  check "geen .part over (ook de symlink van de app niet)" eval 'no_part && [ ! -L "$geo_dir/landen.geojson.part" ]'
  NET=aan
  ln -s "$host/map" "$work/data/media"
  code=0
  out=$(media_dirs) || code=$?
  check "media_dirs: de map media is een symlink: return 1, met uitleg" eval '[ "$code" = 1 ] && grep -qxF "$work/data/media is geen gewone map maar een symlink of een bestand; dit script raakt hem niet aan." <<<"$out"'
  check "media_dirs: de map van de host blijft" host_same
  rm -f "$work/data/media"
  : >"$work/install.log"
  mkdir -p "$work/data/import"
  command chmod 700 "$work/data/import"
  code=0
  media_dirs >/dev/null || code=$?
  check "media_dirs: een nieuwe map maakt 10001, een bestaande krijgt chown -h en chmod van 10001" eval '[ "$code" = 0 ] &&
    [ "$(cat "$work/install.log")" = "als 10001 in $work/data: mkdir -m 750 -- media
chown -h 10001:10001 $work/data/import
als 10001 in $work/data/import: chmod 750 ." ] && [ "$(mode "$work/data/media") $(mode "$work/data/import")" = "750 750" ]'
  rm -rf "$work/data/media" "$work/data/import"

  # --- als root, met de echte setpriv -----------------------------------------------------
  # Hierboven doet as_app_in alles als de gebruiker van de test. Hier draait een stuk als root
  # (in CI met sudo -n), met de echte functies van install.sh: de symlinks van "de app" wijzen
  # naar een map en bestanden van root buiten DATA_ROOT, en die moeten blijven zoals ze waren
  echo "== als root, met de echte setpriv: een symlink van de app geeft root niets (review golf 4, veilig F2)"
  local root_run=() root_out line n_root=0
  if [ "$(id -u)" = 0 ]; then
    root_run=(bash -s)
  elif sudo -n true 2>/dev/null; then
    root_run=(sudo -n bash -s)
  fi
  if [ "${#root_run[@]}" = 0 ]; then
    fout "als root: geen root en geen sudo -n, dus deze controles liepen niet"
  else
    root_out=$("${root_run[@]}" "$install_sh" 2>&1 <<'ALS_ROOT'
INSTALL_SH=$1
R=$(mktemp -d)
trap 'rm -rf "$R"' EXIT
export INSTALL_LIB=1 PLATFORM_ROOT=$R/root
unset PLAATSEN
# shellcheck source=/dev/null
. "$INSTALL_SH"
set +e
c() {
  local name=$1
  shift
  if "$@"; then echo "ok    als root: $name"; else echo "FOUT  als root: $name"; fi
}
DATA_ROOT=$R/data host=$R/host
mkdir -p "$DATA_ROOT" "$host/map" "$R/net" "$SRC/api/app/geo"
: >"$SRC/api/app/geo/load.py"
chown 10001:10001 "$DATA_ROOT" && chmod 750 "$DATA_ROOT"
printf 'van de host\n' >"$host/bestand"
printf 'ook van de host\n' >"$host/ander"
chmod 600 "$host/bestand" "$host/ander"
chmod 755 "$host/map"
host_same() {
  [ "$(cat "$host/bestand")" = "van de host" ] && [ "$(cat "$host/ander")" = "ook van de host" ] &&
    [ "$(stat -c '%u %a' "$host/bestand" "$host/ander" "$host/map" | tr '\n' ' ')" = "0 600 0 600 0 755 " ] &&
    [ -z "$(ls -A "$host/map")" ]
}
ln -s "$host/map" "$DATA_ROOT/backup"
c "de map backup is een symlink: app_dir weigert" eval '! app_dir 700 "$DATA_ROOT/backup" >/dev/null'
c "as_app_in in een symlink naar een map van root (de race): weigert, schrijft niets" eval '! as_app_in "$DATA_ROOT/backup" touch x'
c "de map van de host blijft (eigenaar, rechten, leeg)" host_same
rm -f "$DATA_ROOT/backup"
c "een nieuwe map backup: van 10001, 700" eval 'app_dir 700 "$DATA_ROOT/backup" && [ "$(stat -c "%u:%g %a" "$DATA_ROOT/backup")" = "10001:10001 700" ]'
mkdir -m 755 "$DATA_ROOT/import"
c "media_dirs: nieuw (media) en van root (import): beide van 10001, 750" eval 'media_dirs && [ "$(stat -c "%u:%g %a" "$DATA_ROOT/media" "$DATA_ROOT/import" | sort -u)" = "10001:10001 750" ]'
printf 'landen\n' >"$R/net/landen.geojson"
printf 'plaatsen\n' >"$R/net/plaatsen.tar.gz"
geo_bronnen() {
  local n
  for n in landen.geojson plaatsen.tar.gz; do
    printf '%s %s %s https://bron.invalid/%s\n' "$n" "$(stat -c %s "$R/net/$n")" "$(sha256sum "$R/net/$n" | cut -d' ' -f1)" "$n"
  done
}
# De app zet terwijl curl loopt een symlink op <bron>.part in de map geo
curl() {
  local url=${!#} target="" prev="" a
  for a in "$@"; do
    [ "$prev" != -o ] || target=$a
    prev=$a
  done
  ln -sfn "$host/bestand" "$DATA_ROOT/geo/${url##*/}.part"
  cp "$R/net/${url##*/}" "$target"
}
app_dir 750 "$DATA_ROOT/geo"
ln -s "$host/ander" "$DATA_ROOT/geo/plaatsen.tar.gz"
geo_download >/dev/null
c "de download met symlinks van de app: nieuw" test "$GEO_STATE" = nieuw
c "de bestanden van de host blijven (inhoud, eigenaar root, 600)" host_same
c "beide bronnen echte bestanden van 10001, 640, heel" eval '[ ! -L "$DATA_ROOT/geo/landen.geojson" ] && [ ! -L "$DATA_ROOT/geo/plaatsen.tar.gz" ] &&
  cmp -s "$DATA_ROOT/geo/landen.geojson" "$R/net/landen.geojson" && cmp -s "$DATA_ROOT/geo/plaatsen.tar.gz" "$R/net/plaatsen.tar.gz" &&
  [ "$(stat -c "%u:%g %a" "$DATA_ROOT/geo/landen.geojson" "$DATA_ROOT/geo/plaatsen.tar.gz" | sort -u)" = "10001:10001 640" ]'
c "geen .part over" test -z "$(find "$DATA_ROOT/geo" -name "*.part")"
ALS_ROOT
    ) || true
    while IFS= read -r line; do
      case "$line" in
        "ok    "*) ok "${line#ok    }" && n_root=$((n_root + 1)) ;;
        "FOUT  "*) fout "${line#FOUT  }" && n_root=$((n_root + 1)) ;;
        *) printf '      %s\n' "$line" ;;
      esac
    done <<<"$root_out"
    check "als root: alle 9 controles liepen ($n_root)" test "$n_root" = 9
  fi

  # --- de nep-Docker voor reservekopie en main ---------------------------------------------
  # De staat staat in variabelen; run_main draait main in een subshell en geeft de staat aan het
  # eind terug (ook na exit 1). compose run met de reservekopie voert de tekst echt uit.
  LOG=$work/docker.log
  APP=""          # de id van platform:local
  CID=""          # de container van de app (leeg: er draait geen app)
  BUILDS=0
  RECREATE=1      # maakt up -d --build een nieuwe container (0: alles bleef gelijk)
  RUN_RC=0        # wat compose run zelf teruggeeft (0: de tekst draait)
  MIGRATE=""      # de head die de nieuwe app bij de start zet
  OFFICE_CT=""    # een container van DS die draait (0.7.1 met office aan, nu OFFICE=uit)
  GEOLOAD_RC=0
  docker() {
    local args=("$@") a shown=()
    for a in "${args[@]}"; do
      case "$a" in *$'\n'*) shown+=("<tekst>") ;; *) shown+=("$a") ;; esac
    done
    printf 'docker %s\n' "${shown[*]}" >>"$LOG"
    case "$1" in
      image) [ "$2" != inspect ] || [ -z "$APP" ] || printf '%s\n' "$APP" ;;
      ps)
        case "$*" in
          *service=platform*) [ -z "$CID" ] || echo "$CID" ;;
          *service=office*) [ -z "$OFFICE_CT" ] || echo "$OFFICE_CT" ;;
        esac
        ;;
      inspect)
        case "$*" in
          *State.Running*) echo true ;;
          *Image*) echo sha256:office-oud ;;
        esac
        ;;
      compose)
        case "${shown[*]}" in
          *" run --rm --no-deps -T platform python -I -c <tekst> /data/platform.db /data/backup")
            printf 'run met image %s, tekst %s\n' "${APP:-geen}" "$(printf '%s' "${args[${#args[@]} - 3]}" | sha256sum | cut -c1-64)" >>"$LOG"
            if [ "$RUN_RC" != 0 ]; then
              echo "Error response from daemon: de nep-docker faalt" >&2
              return "$RUN_RC"
            fi
            python3 -I -c "${args[${#args[@]} - 3]}" "$DATA_ROOT/platform.db" "$DATA_ROOT/backup" |
              sed "s|^$DATA_ROOT/|/data/|"
            return "${PIPESTATUS[0]}"
            ;;
          *" up -d --build --remove-orphans platform")
            BUILDS=$((BUILDS + 1))
            APP=sha256:app-$BUILDS
            if [ "$RECREATE" = 1 ] || [ -z "$CID" ]; then CID=c-app-$BUILDS; fi
            if [ -n "$MIGRATE" ] && [ -f "$DATA_ROOT/platform.db" ]; then
              db_sql "$DATA_ROOT/platform.db" "UPDATE alembic_version SET version_num = '$MIGRATE'"
            fi
            ;;
          *" exec -T platform python -m app.cli geo-load") return "$GEOLOAD_RC" ;;
          *" exec -T platform python -m app.cli media-status --sessions") return 2 ;;
        esac
        ;;
    esac
  }
  line_of() { grep -nF -- "$1" "$LOG" | head -n 1 | cut -d: -f1; }
  before() {
    local a b
    a=$(line_of "$1") b=$(line_of "$2")
    [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]
  }
  logged() { grep -qF -- "$1" "$LOG"; }
  said() { grep -qxF -- "$1" "$work/main.out"; }
  said_part() { grep -qF -- "$1" "$work/main.out"; }

  echo "== reservekopie: de tekst van backup.py, met het image dat draait"
  DATA_ROOT=$work/data
  rm -rf "$DATA_ROOT"
  mkdir -p "$DATA_ROOT"
  compose_files 0
  : >"$LOG"
  code=0
  out=$(reservekopie) || code=$?
  check "nog geen database: overgeslagen, return 0" eval '[ "$code" = 0 ] && [ "$out" = "Reservekopie: nog geen database (eerste installatie)." ]'
  check "nog geen database: Docker niet aangeroepen" test ! -s "$LOG"
  make_db "$DATA_ROOT/platform.db" 0009
  without_backup
  code=0
  out=$(reservekopie) || code=$?
  check "een versie zonder backup.py (0.7.1): overgeslagen, return 0" eval '[ "$code" = 0 ] && [ "$out" = "Reservekopie: deze versie van het platform kent hem nog niet; overgeslagen." ]'
  check "zonder backup.py: Docker niet aangeroepen" test ! -s "$LOG"
  with_backup
  APP=sha256:app-oud
  : >"$work/install.log"
  code=0
  reservekopie >"$work/kopie.out" 2>&1 || code=$?
  local copies
  copies=$(ls "$DATA_ROOT/backup")
  check "gelukt: return 0" test "$code" = 0
  check "één kopie platform-0009-<tijd>.db" eval '[[ $copies =~ ^platform-0009-[0-9]{8}T[0-9]{6}\.[0-9]{3}Z\.db$ ]]'
  check "met de uitvoer Reservekopie: <pad>" grep -qxF "Reservekopie: $DATA_ROOT/backup/$copies" "$work/kopie.out"
  check "gemaakt met het image dat draait" grep -qF "run met image sha256:app-oud," "$LOG"
  check "de tekst is precies api/app/backup.py van de nieuwe broncode" grep -qF "tekst $(printf '%s' "$(cat "$SRC/api/app/backup.py")" | sha256sum | cut -c1-64)" "$LOG"
  check "de map backup: 700, door 10001 zelf gemaakt" eval 'grep -qxF "als 10001 in $DATA_ROOT: mkdir -m 700 -- backup" "$work/install.log" && [ "$(mode "$DATA_ROOT/backup")" = 700 ]'
  check "de kopie: 600" test "$(mode "$DATA_ROOT/backup/$copies")" = 600
  check "de kopie: zonder gezichten, ook niet in de bytes" eval '[ "$(db_get "$DATA_ROOT/backup/$copies" "SELECT count(*) FROM face")" = 0 ] && ! grep -q MERKTEKEN "$DATA_ROOT/backup/$copies"'
  check "de kopie: de gebruiker en de head" eval '[ "$(db_get "$DATA_ROOT/backup/$copies" "SELECT count(*) FROM \"user\"")" = 1 ] && [ "$(db_get "$DATA_ROOT/backup/$copies" "SELECT version_num FROM alembic_version")" = 0009 ]'
  check "de database zelf heeft zijn gezichten nog" test "$(db_get "$DATA_ROOT/platform.db" "SELECT count(*) FROM face")" = 3
  RUN_RC=125
  code=0
  reservekopie >"$work/kopie.out" 2>&1 || code=$?
  check "compose run faalt: return 1" test "$code" = 1
  check "compose run faalt: met uitleg" grep -qF "De reservekopie van de database lukte niet (de melding staat hierboven). Er is nog niets" "$work/kopie.out"
  check "compose run faalt: de kopie van daarvoor blijft, verder niets" test "$(ls "$DATA_ROOT/backup")" = "$copies"
  RUN_RC=0
  db_sql "$DATA_ROOT/platform.db" "CREATE TRIGGER face_ad AFTER DELETE ON face BEGIN SELECT RAISE(ABORT, 'installatietest'); END;"
  code=0
  reservekopie >"$work/kopie.out" 2>&1 || code=$?
  check "de kopie zelf faalt (stap 3): return 1" test "$code" = 1
  check "de kopie zelf faalt: de melding van de kopie" grep -qxF "Reservekopie mislukt: installatietest" "$work/kopie.out"
  check "de kopie zelf faalt: geen .part en geen halve kopie" test "$(ls -A "$DATA_ROOT/backup")" = "$copies"
  db_sql "$DATA_ROOT/platform.db" "DROP TRIGGER face_ad;"
  mv "$DATA_ROOT/backup" "$work/backup.bewaard"
  : >"$DATA_ROOT/backup"
  : >"$LOG"
  code=0
  reservekopie >"$work/kopie.out" 2>&1 || code=$?
  check "de map backup kan er niet komen: return 1, met uitleg, zonder compose run" eval \
    '[ "$code" = 1 ] && grep -qF "De reservekopie van de database lukte niet" "$work/kopie.out" && [ ! -s "$LOG" ]'
  rm -f "$DATA_ROOT/backup"
  ln -s "$host/map" "$DATA_ROOT/backup"
  : >"$LOG"
  code=0
  reservekopie >"$work/kopie.out" 2>&1 || code=$?
  check "de map backup is een symlink: return 1, met uitleg, zonder compose run" eval \
    '[ "$code" = 1 ] && grep -qxF "$DATA_ROOT/backup is geen gewone map maar een symlink of een bestand; dit script raakt hem niet aan." "$work/kopie.out" && [ ! -s "$LOG" ]'
  check "de map backup is een symlink: de map van de host blijft" host_same
  rm -f "$DATA_ROOT/backup"
  mv "$work/backup.bewaard" "$DATA_ROOT/backup"

  echo "== main: de volgorde, een mislukte kopie stopt alles, en na twee runs op 0010 de 0009"
  # Alles van main wat de machine raakt, nagebootst; instellingen, de download, de reservekopie
  # en de eindtekst zijn echt
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
  run_main() {
    : >"$LOG"
    : >"$work/curl.log"
    (
      trap 'printf "STAAT app=%s cid=%s\n" "$APP" "$CID"' EXIT
      set -e
      main
    ) >"$work/main.out" 2>&1 || true
    local state
    state=$(sed -n 's/^STAAT //p' "$work/main.out")
    APP=$(sed -n 's/^app=\([^ ]*\) .*/\1/p' <<<"$state")
    CID=$(sed -n 's/.* cid=\(.*\)$/\1/p' <<<"$state")
    BUILDS=${APP#sha256:app-}
    [[ $BUILDS =~ ^[0-9]+$ ]] || BUILDS=0
  }
  unset OFFICE MEDIA_SANDBOX PLAATSEN
  db=""
  backups() { find "$DATA_ROOT/backup" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | sort | tr '\n' ' ' | sed 's/ $//'; }
  local script_main="curl -fsSL https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform/main/infra/single/install.sh"

  # A. De eerste installatie: nog geen .env, geen database, geen app
  rm -rf "$PLATFORM_ROOT/.env" "$work/data" "$PLATFORM_ROOT/data"
  APP="" CID="" OFFICE_CT="" NET=aan RECREATE=1 MIGRATE=""
  run_main
  DATA_ROOT=$PLATFORM_ROOT/data/platform
  db=$DATA_ROOT/platform.db
  check "A: eerste installatie: Klaar." said_part "Klaar. Het platform draait op"
  check "A: geen reservekopie (nog geen database)" said "Reservekopie: nog geen database (eerste installatie)."
  check "A: geen compose run" not logged " run --rm --no-deps"
  check "A: PLAATSEN=aan in de nieuwe .env" has_line "PLAATSEN=aan"
  check "A: de bronnen gehaald" said "Plaatsnamen: de bronnen staan klaar in $DATA_ROOT/geo; de app laadt ze bij de start (±1 minuut)."
  check "A: geen geo-load (er draaide nog geen app)" not logged "geo-load"
  check "A: de eindtekst van §3.10 stap 7" said "$SEARCH_LINE"
  check "A: Plaatsnamen: aan, met hoe het uit gaat" eval 'said "Plaatsnamen: aan." && said "  Uit: $script_main | PLAATSEN=uit bash"'
  check "A: geen regel over een reservekopie" not said_part "Reservekopie van vóór deze run"
  # Het runbook zet eerst DATA_ROOT uit .env en gebruikt dan "$DATA_ROOT", nooit /tank zelf: zonder
  # mount point /tank staat de data op de rootdisk (review golf 4, contract F3). De regel van het
  # runbook, op de .env die main net schreef (hier zonder /tank)
  local runbook=$top/docs/runbooks/zoeken.md line got
  line=$(grep -m1 -F "DATA_ROOT=\$(sed -n 's/^DATA_ROOT=//p' /opt/platform/.env)" "$runbook" || true)
  got=$(bash -c "${line//\/opt\/platform\/.env/$PLATFORM_ROOT/.env}" 2>&1 || true)
  check "A: de regel van het runbook geeft DATA_ROOT uit .env ($DATA_ROOT)" eval '[ -n "$line" ] && [ "$got" = "$DATA_ROOT" ]'
  got=$(awk '/^```/ { code = !code; next } code && /\/tank/' "$runbook")
  check "A: geen commando in het runbook met /tank (${got:-geen})" test -z "$got"
  check "A: de README's geven bij de reservekopie ook het pad zonder /tank" eval 'grep -qF "/opt/platform/data/platform/backup" "$top/README.md" &&
    grep -qF "/opt/platform/data/platform/backup/platform-0009-<tijd>.db" "$here/../README.md"'

  # B. Bijwerken vanaf 0.7.1: de database op 0009, de oude app en DS draaien
  make_db "$db" 0009
  APP=sha256:app-1 CID=c-app-1 BUILDS=1 OFFICE_CT=c-office MIGRATE=0010
  run_main
  local first0009
  first0009=$(backups)
  check "B: Klaar." said_part "Klaar. Het platform draait op"
  check "B: eerst open documenten opslaan, dan de kopie" before "office-checkpoint" " run --rm --no-deps -T platform"
  check "B: de kopie vóór DS stopt" before " run --rm --no-deps -T platform" "docker stop -t 60 c-office"
  check "B: de kopie vóór de nieuwe app" before " run --rm --no-deps -T platform" "up -d --build --remove-orphans platform"
  check "B: met het image van 0.7.1 (dat er nog draait)" logged "run met image sha256:app-1,"
  check "B: één kopie platform-0009-<tijd>.db" eval '[[ $first0009 =~ ^platform-0009-[0-9]{8}T[0-9]{6}\.[0-9]{3}Z\.db$ ]]'
  check "B: rechten 600, de map 700" eval '[ "$(mode "$DATA_ROOT/backup/$first0009")" = 600 ] && [ "$(mode "$DATA_ROOT/backup")" = 700 ]'
  check "B: zonder gezichten" eval '[ "$(db_get "$DATA_ROOT/backup/$first0009" "SELECT count(*) FROM face")" = 0 ] && ! grep -q MERKTEKEN "$DATA_ROOT/backup/$first0009"'
  check "B: de eindtekst noemt de kopie" said "Reservekopie van vóór deze run: $DATA_ROOT/backup/$first0009"
  check "B: de database staat nu op 0010, met zijn gezichten" eval '[ "$(db_get "$db" "SELECT version_num FROM alembic_version")" = 0010 ] && [ "$(db_get "$db" "SELECT count(*) FROM face")" = 3 ]'
  check "B: de bronnen stonden er al: geen curl, geen geo-load" eval '[ ! -s "$work/curl.log" ] && ! logged geo-load'

  # C. Een mislukte reservekopie stopt het script vóór elke wijziging: compose run faalt, en de
  # kopie zelf faalt
  local before_sha app_c cid_c variant
  for variant in "compose run faalt" "de kopie zelf faalt"; do
    if [ "$variant" = "compose run faalt" ]; then
      RUN_RC=1
    else
      RUN_RC=0
      db_sql "$db" "CREATE TRIGGER face_ad AFTER DELETE ON face BEGIN SELECT RAISE(ABORT, 'installatietest'); END;"
    fi
    before_sha=$(sha256sum "$db" | cut -d' ' -f1)
    app_c=$APP cid_c=$CID MIGRATE=0011
    run_main
    check "C ($variant): exit 1, geen Klaar." not said_part "Klaar."
    check "C ($variant): met uitleg" said_part "De reservekopie van de database lukte niet"
    check "C ($variant): geen nieuwe app, DS niet gestopt, geen image weg" eval '! logged "up -d" && ! logged "docker stop" && ! logged "image rm"'
    check "C ($variant): dezelfde app en hetzelfde image" eval '[ "$APP" = "$app_c" ] && [ "$CID" = "$cid_c" ]'
    check "C ($variant): de database onveranderd" test "$(sha256sum "$db" | cut -d' ' -f1)" = "$before_sha"
    check "C ($variant): geen nieuwe kopie en geen .part" test "$(backups)" = "$first0009"
  done
  check "C (de kopie zelf faalt): de melding van de kopie" said "Reservekopie mislukt: installatietest"
  db_sql "$db" "DROP TRIGGER face_ad;"
  RUN_RC=0 MIGRATE=""

  # D. Twee runs op 0010: de kopie van 0009 blijft, van 0010 alleen de nieuwste
  local copy1 copy2
  run_main
  copy1=$(sed -n 's/^Reservekopie: //p' "$work/main.out")
  check "D: de eerste run op 0010: een kopie platform-0010" eval '[[ ${copy1##*/} =~ ^platform-0010- ]] && [ -f "$copy1" ]'
  check "D: met het image van de vorige run" logged "run met image sha256:app-2,"
  run_main
  copy2=$(sed -n 's/^Reservekopie: //p' "$work/main.out")
  check "D: de tweede run: een nieuwere kopie van 0010" eval '[[ ${copy2##*/} =~ ^platform-0010- ]] && [[ ${copy2##*/} > ${copy1##*/} ]]'
  check "D: na twee runs op 0010 staat de kopie van 0009 er nog, van 0010 alleen de nieuwste" test "$(backups)" = "$first0009 ${copy2##*/}"

  # E. PLAATSEN=uit (in de omgeving), en daarna blijft het uit tot PLAATSEN=aan
  export PLAATSEN=uit
  run_main
  unset PLAATSEN
  check "E: PLAATSEN=uit: geen curl" test ! -s "$work/curl.log"
  check "E: PLAATSEN=uit: de regel bij de download" said "Plaatsnamen: uit (PLAATSEN=uit); er wordt niets opgehaald."
  check "E: PLAATSEN=uit: de eindtekst zegt hoe het aan gaat" eval 'said "Plaatsnamen: uit (PLAATSEN=uit)." && said "  Aan: $script_main | PLAATSEN=aan bash"'
  check "E: PLAATSEN=uit in .env" has_line "PLAATSEN=uit"
  check "E: de reservekopie loopt gewoon" said_part "Reservekopie: $DATA_ROOT/backup/platform-0010-"
  rm -f "$DATA_ROOT"/geo/*
  run_main
  check "E: de run daarna zonder PLAATSEN: blijft uit, geen curl" eval '[ ! -s "$work/curl.log" ] && said "Plaatsnamen: uit (PLAATSEN=uit)."'
  export PLAATSEN=aan
  RECREATE=1
  run_main
  unset PLAATSEN
  check "E: PLAATSEN=aan: weer aan, de bronnen gehaald" eval 'has_line "PLAATSEN=aan" && said "Plaatsnamen: aan." && [ "$(grep -c "^curl " "$work/curl.log")" = 2 ]'
  check "E: de app startte opnieuw: geen geo-load (de app laadt ze bij de start)" not logged "geo-load"

  # F. Een download die faalt: niet fataal; de volgende run haalt ze, en laadt ze in de app als
  # die niet opnieuw startte
  rm -f "$DATA_ROOT"/geo/*
  NET=uit
  run_main
  check "F: geen netwerk: het script loopt door tot Klaar." said_part "Klaar. Het platform draait op"
  check "F: de eindtekst: niet geladen, en hoe opnieuw" eval 'said "Plaatsnamen: niet geladen (de download lukte niet)." && said "  Opnieuw proberen: $script_main | bash"'
  check "F: geen geo-load" not logged "geo-load"
  NET=aan RECREATE=0
  local cid_f=$CID
  run_main
  check "F: de volgende run haalt ze" said "Plaatsnamen: de bronnen staan klaar in $DATA_ROOT/geo; de app laadt ze bij de start (±1 minuut)."
  check "F: dezelfde container (niets anders veranderde)" test "$CID" = "$cid_f"
  check "F: dan geo-load in de draaiende app, na de app" before "up -d --build --remove-orphans platform" "exec -T platform python -m app.cli geo-load"
  check "F: met die regel" said "Plaatsnamen laden (±1 minuut)…"
  check "F: Plaatsnamen: aan." said "Plaatsnamen: aan."
  rm -f "$DATA_ROOT"/geo/*
  GEOLOAD_RC=1
  run_main
  check "F: geo-load faalt: alleen een waarschuwing, het script loopt door" eval 'said "Let op: plaatsnamen laden lukte niet (zie hierboven); het platform werkt verder." && said_part "Klaar."'
  GEOLOAD_RC=0 RECREATE=1

  # G. Een foute PLAATSEN in .env: stop vóór de kopie en vóór de app
  set_key PLAATSEN ja
  run_main
  check "G: PLAATSEN=ja in .env: stop met uitleg" said "PLAATSEN in $ENV_FILE moet aan of uit zijn (nu: ja). Zet het goed en draai dit"
  check "G: geen kopie, geen app, geen curl" eval '! logged " run --rm" && ! logged "up -d" && [ ! -s "$work/curl.log" ]'
  set_key PLAATSEN aan

  # H. Terug naar 0.7.1 met het script van nu (zonder plaatsnamen en zonder backup.py): niets
  # van zoeken, en de wens blijft in .env
  without_places
  without_backup
  run_main
  check "H: 0.7.1: geen stap Plaatsnamen, geen curl" eval '! said "== Plaatsnamen" && [ ! -s "$work/curl.log" ]'
  check "H: 0.7.1: de reservekopie overgeslagen" said "Reservekopie: deze versie van het platform kent hem nog niet; overgeslagen."
  check "H: 0.7.1: geen regel over zoeken in de eindtekst" eval '! said "$SEARCH_LINE" && ! said_part "Plaatsnamen"'
  check "H: 0.7.1: PLAATSEN blijft in .env" has_line "PLAATSEN=aan"
  with_places
  with_backup

  echo "== geen deel van de database in de uitvoer, en elke curl, compose run en exec krijgt </dev/null"
  check "geen merkteken van de database in de uitvoer van main" not grep -q MERKTEKEN "$work/main.out"
  out=$(grep -nE '(docker run|compose\[@\]\}" (exec|run)) |curl -fsSL --retry' "$install_sh" | grep -v '</dev/null' || true)
  check "elke docker run, compose run, compose exec en curl van de bronnen krijgt </dev/null" test -z "$out"

  # De weg terug naar 0.7.1 (§3.10, Keuze 53): het bouwontwerp, de README en het runbook geven
  # Jonas precies deze commando's, in die volgorde, met de volledige hash; --stappen draait ze.
  # Het script komt van de commit zelf (niet van de branch: dan draait het oude script met het
  # oude image), en in een bestand vóór er iets stopt. Daarna weer vooruit met de branch.
  echo
  echo "== terug naar 0.7.1 en weer vooruit: de commando's van §3.10 in het ontwerp, de README en het runbook"
  out=$(python3 -I - "$ROLLBACK_SHA" "$ROLLBACK_HEAD" "$BRANCH_NAME" "$top/docs/fase-6/bouwontwerp.md" \
    "$here/../README.md" "$top/docs/runbooks/zoeken.md" <<'PY'
import re
import sys

SHA, HEAD, BRANCH = sys.argv[1:4]
RAW = "https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform"
FILE = "/root/platform-install-0.7.1.sh"
WANT = [
    ("git fetch", re.escape("git -C /opt/platform/src fetch -q origin")),
    ("cat-file met de hash", re.escape("git -C /opt/platform/src cat-file -e '%s^{commit}' && echo " % SHA)),
    ("curl van de commit -o " + FILE, re.escape("curl -fsSL %s/%s/infra/single/install.sh -o %s && echo " % (RAW, SHA, FILE))),
    ("dc stop", re.escape("dc stop platform media")),
    # Eerst een verse kopie op de head van nu: lukt de downgrade niet, dan is die het vangnet
    # zonder verlies (review golf 4, uitrol F5)
    ("db-backup", re.escape("dc run --rm --no-deps platform python -m app.cli db-backup")),
    ("db-downgrade " + HEAD, re.escape("dc run --rm --no-deps platform python -m app.cli db-downgrade %s" % HEAD)),
    ("bash < " + FILE, re.escape("PLATFORM_BRANCH=%s PLATFORM_SRC_REF=%s bash < %s" % (BRANCH, SHA, FILE))),
    (
        "weer vooruit met de branch",
        re.escape("curl -fsSL %s/%s/infra/single/install.sh | PLATFORM_BRANCH=%s bash" % (RAW, BRANCH, BRANCH)),
    ),
]
problems = []
for path in sys.argv[4:]:
    name = path.rsplit("/", 1)[-1]
    text = open(path, encoding="utf-8").read()
    if name == "bouwontwerp.md":  # alleen §3.10
        start = text.find("\n### 3.10 ")
        end = text.find("\n### 3.11 ", start)
        if start < 0 or end < 0:
            problems.append("bouwontwerp.md: geen §3.10")
            continue
        text = text[start:end]
    lines = [line.strip() for line in text.splitlines()]
    at = -1
    for label, pat in WANT:
        hits = [i for i, line in enumerate(lines) if i > at and re.match(pat + "(?:$| |(?<=echo ))", line)]
        if not hits:
            problems.append("%s mist (of niet in volgorde): %s" % (name, label))
            continue
        at = hits[0]
    for i, line in enumerate(lines):
        if "PLATFORM_SRC_REF=" in line and "| " in line and "bash" in line.split("PLATFORM_SRC_REF=", 1)[1]:
            problems.append("%s r. %d: PLATFORM_SRC_REF met curl | bash, niet met het bestand" % (name, i + 1))
print("; ".join(problems) or "ok")
PY
  ) || out="(geen uitvoer)"
  check "de commando's van §3.10 met de volledige hash, in het ontwerp, de README en het runbook ($out)" test "$out" = ok
  check "§3.10: de downgrade naar $ROLLBACK_HEAD, niet meer 0008" eval '! sed -n "/^### 3.10 /,/^### 3.11 /p" "$top/docs/fase-6/bouwontwerp.md" | grep -qE "db-downgrade 0008|platform-0008-"'

  # De trap van --stappen staat vóór de voorcontrole (de samenvatting komt er altijd) en haalt
  # WORK weg, als root. Een map die de test weigert, mag daarbij nooit verdwijnen. Alleen de
  # voorcontrole draait: een nep-docker zonder project en zonder images, een nep-ss, een kleine
  # git-repo en INSTALL_TEST_FROM die er niet in staat; elke run stopt vóór er iets gebouwd wordt
  echo
  echo "== --stappen: een werkmap die de test weigert, blijft; zijn eigen werkmap gaat weg"
  local nep=$work/stappen own
  mkdir -p "$nep/bin" "$nep/cwd/relatief" "$nep/nietleeg" "$nep/leeg"
  cat >"$nep/bin/docker" <<'NEP'
#!/usr/bin/env bash
case "$1" in
  info) if [ "${2-}" = --format ]; then echo "$NEP_ROOT"; fi ;;
  image) exit 1 ;;
esac
exit 0
NEP
  printf '#!/bin/sh\nexit 0\n' >"$nep/bin/ss"
  chmod +x "$nep/bin/docker" "$nep/bin/ss"
  echo bewaar >"$nep/nietleeg/bewaar.txt"
  echo bewaar >"$nep/cwd/relatief/bewaar.txt"
  echo bewaar >"$nep/bestand"
  git -c init.defaultBranch=main init -q "$nep/repo"
  git -C "$nep/repo" -c user.name=test -c user.email=test@invalid commit -q --allow-empty -m test
  vooraf() {
    (cd "$nep/cwd" && env -u INSTALL_LIB -u PLATFORM_ROOT -u INSTALL_TEST_DIR -u INSTALL_TEST_LOGS \
      -u INSTALL_TEST_TO PATH="$nep/bin:$PATH" NEP_ROOT="$nep" INSTALL_TEST_NEED_GB=0 \
      INSTALL_TEST_REPO="$nep/repo" INSTALL_TEST_FROM=0000000000000000000000000000000000000000 \
      INSTALL_TEST_GEO="$nep/leeg" "$@" bash "$here/test_install_search.sh" --stappen 2>&1)
  }
  stopped_with() { [ "$code" = 1 ] && grep -qxF "STOP  $1" <<<"$out"; }
  empty_dir() { [ -d "$1" ] && [ -z "$(ls -A "$1")" ]; }
  gone() { [ -n "$1" ] && [ ! -e "$1" ]; }
  summary_last() { [ "$(printf '%s\n' "$out" | tail -n 1)" = "=== einde samenvatting ===" ] && grep -qxF "afgebroken in stap vooraf (exit 1): de stappen daarna liepen niet" <<<"$out"; }
  code=0
  out=$(vooraf INSTALL_TEST_DIR="$nep/nietleeg") || code=$?
  check "niet leeg: STOP, exit 1" stopped_with "$nep/nietleeg is niet leeg"
  check "niet leeg: de map en zijn bestand blijven" grep -qxF bewaar "$nep/nietleeg/bewaar.txt"
  check "niet leeg: de samenvatting als laatste" summary_last
  code=0
  out=$(vooraf INSTALL_TEST_DIR=relatief) || code=$?
  check "relatief pad: STOP, exit 1" stopped_with "INSTALL_TEST_DIR moet een absoluut pad zijn"
  check "relatief pad: ./relatief en zijn bestand blijven" grep -qxF bewaar "$nep/cwd/relatief/bewaar.txt"
  check "relatief pad: de samenvatting als laatste" summary_last
  code=0
  out=$(vooraf INSTALL_TEST_DIR="$nep/bestand") || code=$?
  check "een bestand: STOP, exit 1" stopped_with "$nep/bestand is geen map"
  check "een bestand: het blijft" grep -qxF bewaar "$nep/bestand"
  code=0
  out=$(vooraf INSTALL_TEST_DIR="$nep/leeg") || code=$?
  check "lege map: de test gebruikt hem" grep -qF "werkmap $nep/leeg)" <<<"$out"
  check "lege map: STOP pas bij de versie" stopped_with "versie 0000000000000000000000000000000000000000 staat niet in $nep/repo"
  check "lege map: hij blijft, leeg" empty_dir "$nep/leeg"
  code=0
  out=$(vooraf) || code=$?
  own=$(sed -n 's/^== Voorbereiden (.*, werkmap \(\/tmp\/install-search\.[^)]*\))$/\1/p' <<<"$out")
  check "eigen werkmap: STOP pas bij de versie" stopped_with "versie 0000000000000000000000000000000000000000 staat niet in $nep/repo"
  check "eigen werkmap: gemaakt (${own:-geen}) en daarna weg" gone "$own"
  check "eigen werkmap: de samenvatting als laatste" summary_last
  code=0
  out=$(vooraf INSTALL_TEST_GEO="$nep/nietleeg" INSTALL_TEST_FROM="$(git -C "$nep/repo" rev-parse HEAD)" \
    INSTALL_TEST_TO="$(git -C "$nep/repo" rev-parse HEAD)") || code=$?
  check "bronnen van plaatsen die niet kloppen: STOP vóór er iets gebeurt" stopped_with "INSTALL_TEST_GEO ($nep/nietleeg) heeft de twee bronnen van plaatsen niet (tools/geo/haal.py --check)"

  einde
}

# ============================================================================================
# Deel 2: de update en terug (--stappen)
# ============================================================================================

# De installatietest van Z7 (bouwontwerp fase 6, §3.10 en Keuze 52, 53), zoals
# test_install_media.sh: de echte install.sh in een container met Debian 12 (de "LXC"), met de
# docker-socket en de docker-cli van de host, en de werkmap op hetzelfde absolute pad. Office
# blijft uit (OFFICE=uit; office over een update bewijst test_install_update.sh). De stappen:
#
#   | Stap | Wat | Wat de test nakijkt |
#   |---|---|---|
#   | 1 | 0.7.1 installeren met het script van 80d5bb8; een bestand uploaden | schema 0009, geen map backup |
#   | 2 | bijwerken naar deze versie (de branch), zonder netwerk naar de bronnen | de kopie platform-0009 met het image van 0.7.1, vóór de nieuwe app; 600 en 10001; schema 0010; "niet geladen" |
#   | 3 | nog een update (een commit erbovenop), maar de kopie faalt (een trigger op een tabel face) | exit 1; dezelfde container, hetzelfde image, schema 0010, geen nieuwe kopie en geen .part |
#   | 4 | opnieuw, met netwerk, en een symlink van de app op <bron>.part naar een bestand van root | de bronnen gehaald; het bestand van root blijft, geen .part en geen symlink; geo-load in de draaiende app; places.loaded; de kopie van 0010 met het image van deze versie |
#   | 5 | de commit erbovenop met PLAATSEN=uit, dan weer aan | na twee en drie runs op 0010: de 0009 staat er nog, van 0010 alleen de nieuwste; geen download; PLATFORM_PLACES=uit |
#   | 6 | terug naar 0.7.1: precies de stappen van §3.10 | git fetch en cat-file; dc stop; db-downgrade 0009 (met "ja"); het script van 80d5bb8 met PLATFORM_SRC_REF; schema 0009, de app van 0.7.1, het bestand heel |
#   | 7 | weer vooruit: het script van de branch, zonder PLATFORM_SRC_REF | schema 0010; een nieuwe kopie van 0009, gemaakt met het image van 0.7.1; plaatsen geladen |
#
# - De bronnen van plaatsen komen nooit echt van internet: een shim van curl in de runner geeft ze
#   uit $WORK/net (een kopie van INSTALL_TEST_GEO, standaard PLATFORM_GEO_TEST_SRC; in CI haalt
#   tools/geo/haal.py ze vooraf), of faalt zoals zonder netwerk. Elke andere curl is de echte.
# - Een shim van docker in de runner schrijft elke aanroep van compose op (het werkwoord, nooit de
#   tekst van de reservekopie), en bij de reservekopie de id van platform:local: zo ziet de test
#   met welk image elke kopie gemaakt is, en dat die vóór `up` kwam.
# - Het script van 0.7.1 komt uit de commit zelf (`git show 80d5bb8:infra/single/install.sh`),
#   zoals Jonas het met curl van die commit haalt; de functies hierboven pinnen die regel in de
#   docs. Stap 1 en 6 draaien dat bestand, met `bash -s <` zoals `bash < bestand`.
# - Images, de sandbox (geen Debian-spiegel, de proxy, de grens van open bestanden) en het
#   opruimen gaan zoals in test_install_media.sh; het blok "=== Samenvatting" staat altijd als
#   laatste in het log.
#
# Instellingen (alle optioneel): INSTALL_TEST_DIR, _REPO, _FROM (standaard 80d5bb8), _TO
# (standaard HEAD), _PORT (18680), _HTTPS_PORT (18643), _LOGS, _CA, _UV, _NEED_GB (8) en _GEO.
# shellcheck disable=SC2016  # check … eval '…': eval werkt de uitdrukking pas zelf uit
stappen() {
  REPO_SRC=${INSTALL_TEST_REPO:-$top}
  FROM=${INSTALL_TEST_FROM:-$ROLLBACK_SHA}
  TO=${INSTALL_TEST_TO:-$(git -C "$REPO_SRC" rev-parse HEAD)}
  PORT=${INSTALL_TEST_PORT:-18680}
  HTTPS_PORT=${INSTALL_TEST_HTTPS_PORT:-18643}
  LOGS_OUT=${INSTALL_TEST_LOGS:-}
  EXTRA_CA=${INSTALL_TEST_CA-}
  if [ -z "${INSTALL_TEST_CA+x}" ] && [ -f /root/.ccr/ca-bundle.crt ]; then EXTRA_CA=/root/.ccr/ca-bundle.crt; fi
  UV_FROM=${INSTALL_TEST_UV:-ghcr}
  NEED_GB=${INSTALL_TEST_NEED_GB:-8}
  GEO_SRC=${INSTALL_TEST_GEO:-${PLATFORM_GEO_TEST_SRC:-}}
  PROJECT=platform
  RUNNER_NAME=install-search-runner-$$
  RUNNER_IMG=install-search-runner:$$
  BORROWED=installatietest-bewaard/python:3.12-slim-$$
  # WORK blijft leeg tot de werkmap van de test is: de trap hieronder haalt WORK weg (als root)
  runner_built=0 borrowed=0 own_project=0 work_existed=0 WORK=""
  created=() maybe=() SUMMARY=() APP_IDS=() PRE_PLATFORM=""
  STAGE=vooraf ENDED=0
  trap stappen_opruimen EXIT
  trap 'exit 130' INT TERM

  stop() {
    echo "STOP  $1" >&2
    exit 1
  }
  have() { docker image inspect "$1" >/dev/null 2>&1; }
  # De images die de test maakte en nog zijn (zie test_install_media.sh): APP_IDS en het label
  # van het project, nooit een image dat vóór de test al de naam platform had
  own_images() {
    local id
    {
      docker images -a -q --no-trunc --filter "label=com.docker.compose.project=$PROJECT"
      for id in "${APP_IDS[@]}"; do docker image inspect --format '{{.Id}}' "$id" 2>/dev/null || true; done
    } | sed '/^$/d' | sort -u | comm -23 - <(printf '%s\n' "$PRE_PLATFORM" | sort -u)
  }
  app_images() {
    local id
    {
      own_images
      for id in "${APP_IDS[@]}"; do docker image inspect --format '{{.Id}}' "$id" 2>/dev/null || true; done
      docker images -q --no-trunc platform | sort -u | comm -23 - <(printf '%s\n' "$PRE_PLATFORM" | sort -u)
    } | sed '/^$/d' | sort -u
  }
  note_app() {
    local id
    if id=$(docker image inspect --format '{{.Id}}' platform:local 2>/dev/null); then APP_IDS+=("$id"); fi
  }
  as_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo -n "$@"; fi; }

  # --- vooraf: niets van een ander raken ----------------------------------------------------
  local tool
  for tool in docker git python3 ss curl sha256sum; do
    command -v "$tool" >/dev/null || stop "$tool ontbreekt"
  done
  docker info >/dev/null 2>&1 || stop "Docker draait niet"
  [ -z "$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")" ] ||
    stop "er is al een compose-project '$PROJECT'; deze test zou het raken"
  [ -z "$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")" ] ||
    stop "er zijn al volumes van een project '$PROJECT'"
  [ -z "$(docker network ls -q --filter "label=com.docker.compose.project=$PROJECT")" ] ||
    stop "er zijn al netwerken van een project '$PROJECT'"
  ! have platform:local || stop "er is al een image platform:local"
  PRE_PLATFORM=$(docker images -q --no-trunc platform | sort -u)
  [ -z "$(app_images)" ] || stop "er zijn al images van een project '$PROJECT' (label com.docker.compose.project)"
  local p
  for p in "$PORT" "$HTTPS_PORT"; do
    [ -z "$(ss -Hltn "sport = :$p")" ] || stop "poort $p is bezet"
  done
  free_gb() { df --output=avail -B1G "$(docker info --format '{{.DockerRootDir}}')" | tail -n 1 | tr -d ' '; }
  [ "$(free_gb)" -ge "$NEED_GB" ] || stop "te weinig ruimte: $(free_gb) GB vrij voor Docker, de test vraagt er $NEED_GB"

  # De werkmap, zoals test_install_media.sh: een map die de test weigert, blijft zoals hij was
  local dir entries
  if [ -n "${INSTALL_TEST_DIR:-}" ]; then
    dir=$INSTALL_TEST_DIR
    case "$dir" in /*) ;; *) stop "INSTALL_TEST_DIR moet een absoluut pad zijn" ;; esac
    if [ -e "$dir" ] || [ -L "$dir" ]; then
      [ -d "$dir" ] || stop "$dir is geen map"
      entries=$(ls -A "$dir") || stop "kan $dir niet lezen"
      [ -z "$entries" ] || stop "$dir is niet leeg"
      work_existed=1
    fi
    mkdir -p "$dir"
  else
    dir=$(mktemp -d /tmp/install-search.XXXXXX)
  fi
  WORK=$(cd "$dir" && pwd -P)
  ROOT=$WORK/platform
  own_project=1
  mkdir -p "$WORK/logs" "$WORK/shim/bin" "$WORK/shim/log" "$WORK/build" "$WORK/net" "$WORK/bestand"

  # --- de broncode: 0.7.1, deze versie, en een commit erbovenop (stap 3 en 5) ----------------
  echo "== Voorbereiden (${FROM:0:12} -> ${TO:0:12}, werkmap $WORK)"
  git clone -q --bare "$REPO_SRC" "$WORK/repo.git"
  local c
  for c in "$FROM" "$TO"; do
    git -C "$WORK/repo.git" cat-file -e "$c^{commit}" 2>/dev/null || stop "versie $c staat niet in $REPO_SRC"
  done
  # De bronnen van plaatsen (de echte twee: de app bouwt er places.sqlite van)
  if [ -z "$GEO_SRC" ] || ! python3 -I "$top/tools/geo/haal.py" --naar "$GEO_SRC" --check >/dev/null 2>&1; then
    stop "INSTALL_TEST_GEO (${GEO_SRC:-leeg}) heeft de twee bronnen van plaatsen niet (tools/geo/haal.py --check)"
  fi
  local blob tree
  GIT_INDEX_FILE=$WORK/index git -C "$WORK/repo.git" read-tree "$TO"
  blob=$(printf 'installatietest: een update erbovenop\n' | git -C "$WORK/repo.git" hash-object -w --stdin)
  GIT_INDEX_FILE=$WORK/index git -C "$WORK/repo.git" update-index --add --cacheinfo "100644,$blob,api/app/installatietest.txt"
  tree=$(GIT_INDEX_FILE=$WORK/index git -C "$WORK/repo.git" write-tree)
  TO2=$(GIT_AUTHOR_NAME=installatietest GIT_AUTHOR_EMAIL=test@invalid GIT_COMMITTER_NAME=installatietest \
    GIT_COMMITTER_EMAIL=test@invalid git -C "$WORK/repo.git" commit-tree "$tree" -p "$TO" -m "installatietest: een update erbovenop")
  git -C "$WORK/repo.git" update-ref refs/heads/installatietest-van "$FROM"
  git -C "$WORK/repo.git" update-ref refs/heads/installatietest-naar "$TO"
  git -C "$WORK/repo.git" update-ref refs/heads/installatietest-naar2 "$TO2"
  NEW_SH=$WORK/install.sh OLD_SH=$WORK/platform-install-0.7.1.sh
  git -C "$WORK/repo.git" show "$TO:infra/single/install.sh" >"$NEW_SH"
  git -C "$WORK/repo.git" show "$FROM:infra/single/install.sh" >"$OLD_SH"
  printf '[safe]\n\tdirectory = %s\n' "$WORK/repo.git" >"$WORK/gitconfig"
  local n
  while read -r n; do cp "$GEO_SRC/$n" "$WORK/net/$n"; done < <(sed -n 's/^\([^ ]*\) [0-9]* [0-9a-f]* https:.*/\1/p' <(sed -n '/^geo_bronnen()/,/^}/p' "$NEW_SH"))
  [ "$(find "$WORK/net" -type f | wc -l)" = 2 ] || stop "de bronnen in $NEW_SH en INSTALL_TEST_GEO passen niet bij elkaar"

  # --- images (zoals test_install_media.sh) ----------------------------------------------
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
# Een leeg pakket met deze naam, voor een container zonder Debian-spiegel. Verdere argumenten:
# lege bestanden die het pakket meebrengt (ffmpeg: /usr/bin/ffmpeg en /usr/bin/ffprobe)
set -e
name=$1
shift
d=$(mktemp -d)
mkdir -p "$d/DEBIAN"
for f in "$@"; do
  mkdir -p "$d$(dirname "$f")"
  : >"$d$f"
  chmod 755 "$d$f"
done
printf 'Package: %s\nVersion: 99:0-installatietest\nArchitecture: all\nMaintainer: installatietest <test@invalid>\nDescription: leeg pakket voor de installatietest\n' "$name" >"$d/DEBIAN/control"
dpkg-deb --build "$d" "/tmp/$name.deb" >/dev/null
dpkg -i "/tmp/$name.deb" >/dev/null
rm -rf "$d" "/tmp/$name.deb"
SH

  echo "== Basisimages (van mirror.gcr.io; uv van ghcr.io)"
  pull_new mirror.gcr.io/library/python:3.12-slim
  pull_new mirror.gcr.io/library/node:22-alpine
  apt_ok=0
  if docker run --rm mirror.gcr.io/library/python:3.12-slim \
    sh -c 'apt-get update >/dev/null 2>&1; ls /var/lib/apt/lists | grep -q _Packages' </dev/null; then
    apt_ok=1
  fi
  local ca=""
  if [ -n "$EXTRA_CA" ]; then
    [ -f "$EXTRA_CA" ] || stop "INSTALL_TEST_CA wijst niet naar een bestand"
    cp "$EXTRA_CA" "$WORK/build/extra-ca.crt"
    ca=1
    echo "  de builds vertrouwen ook $EXTRA_CA (de proxy van deze omgeving)"
  fi
  [ "$apt_ok" = 1 ] || echo "  geen Debian-spiegel bereikbaar: lege pakketten ffmpeg en tesseract (en unattended-upgrades)"
  if [ "$apt_ok" = 1 ] && [ -z "$ca" ]; then
    name_as mirror.gcr.io/library/python:3.12-slim python:3.12-slim
  else
    if have python:3.12-slim; then
      docker tag python:3.12-slim "$BORROWED"
      borrowed=1
      docker image rm python:3.12-slim >/dev/null
      echo "  python:3.12-slim van een ander bewaard als $BORROWED (komt aan het eind terug)"
    fi
    {
      echo "FROM mirror.gcr.io/library/python:3.12-slim"
      if [ -n "$ca" ]; then
        echo "COPY extra-ca.crt /etc/ssl/installatietest-ca.crt"
        echo "ENV SSL_CERT_FILE=/etc/ssl/installatietest-ca.crt PIP_CERT=/etc/ssl/installatietest-ca.crt UV_SYSTEM_CERTS=1"
      fi
      if [ "$apt_ok" = 0 ]; then
        echo "COPY leeg-pakket.sh /tmp/leeg-pakket.sh"
        # tesseract met nld, eng en osd (X1, fase 6): de Dockerfile zet /usr/bin/tesseract op 0711
        echo "RUN sh /tmp/leeg-pakket.sh ffmpeg /usr/bin/ffmpeg /usr/bin/ffprobe \\"
        echo "    && sh /tmp/leeg-pakket.sh tesseract-ocr /usr/bin/tesseract \\"
        echo "    && for p in tesseract-ocr-nld tesseract-ocr-eng tesseract-ocr-osd; do sh /tmp/leeg-pakket.sh \$p; done \\"
        echo "    && rm /tmp/leeg-pakket.sh"
      fi
    } >"$WORK/build/python.Dockerfile"
    docker build -q -t python:3.12-slim -f "$WORK/build/python.Dockerfile" "$WORK/build" >/dev/null
    created+=(python:3.12-slim)
  fi
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
  if ! have docker/dockerfile:1; then
    pull_new mirror.gcr.io/docker/dockerfile:1
    name_as mirror.gcr.io/docker/dockerfile:1 docker/dockerfile:1
  fi
  for c in mirror.gcr.io/library/hello-world hello-world; do
    have "$c" || maybe+=("$c")
  done
  echo "  vrij voor Docker: $(free_gb) GB"
  SANDBOX_COMPOSE=""
  if ! docker run --rm --ulimit nofile=65536:65536 mirror.gcr.io/library/python:3.12-slim true </dev/null >/dev/null 2>&1; then
    local nofile
    nofile=$(sed -n 's/^Max open files *[0-9]* *\([0-9]*\).*/\1/p' "/proc/$(pidof dockerd | cut -d' ' -f1)/limits" 2>/dev/null || true)
    [ -n "$nofile" ] || nofile=$(ulimit -Hn)
    SANDBOX_COMPOSE=$WORK/build/sandbox-nofile.yaml
    printf '# Alleen voor test_install_search.sh: dockerd geeft hier hooguit %s open bestanden\nservices:\n  platform:\n    ulimits:\n      nofile: {soft: %s, hard: %s}\n' \
      "$nofile" "$nofile" "$nofile" >"$SANDBOX_COMPOSE"
    echo "  dockerd geeft hier geen 65536 open bestanden: de app krijgt er in deze test $nofile"
  fi

  # --- de shims in de runner: docker (het logboek van compose) en curl (het nepnetwerk) -----
  cat >"$WORK/shim/bin/docker" <<'SHIM'
#!/usr/bin/env bash
# Elke aanroep van compose komt in $SHIM_LOG/compose.log: het werkwoord en wat erna komt, met
# <tekst> in plaats van de tekst van de reservekopie. Bij de reservekopie ook de id van
# platform:local in runs.log. Compose krijgt het extra bestand van de sandbox na de laatste -f.
real=/usr/bin/docker
if [ "${1-}" = compose ]; then
  args=("$@") at=-1 i=1 shown=()
  for j in "${!args[@]}"; do
    if [ "${args[j]}" = -f ] && [[ ${args[j + 1]-} == *.yaml ]]; then at=$((j + 1)); fi
  done
  while [ "$i" -lt "${#args[@]}" ]; do
    case "${args[i]}" in
      --project-directory | --env-file | -f | -p) i=$((i + 2)) ;;
      *) break ;;
    esac
  done
  for a in "${args[@]:i}"; do
    case "$a" in *$'\n'*) shown+=("<tekst>") ;; *) shown+=("$a") ;; esac
  done
  printf 'compose %s\n' "${shown[*]}" >>"$SHIM_LOG/compose.log"
  case "${shown[*]}" in
    "run --rm --no-deps -T platform python -I -c <tekst> "*)
      "$real" image inspect --format '{{.Id}}' platform:local >>"$SHIM_LOG/runs.log" 2>/dev/null ||
        echo geen >>"$SHIM_LOG/runs.log"
      ;;
  esac
  if [ -n "${SHIM_COMPOSE_EXTRA:-}" ] && [ "$at" -ge 0 ]; then
    exec "$real" "${args[@]:0:at+1}" -f "$SHIM_COMPOSE_EXTRA" "${args[@]:at+1}"
  fi
fi
exec "$real" "$@"
SHIM
  cat >"$WORK/shim/bin/curl" <<'SHIM'
#!/usr/bin/env bash
# Het nepnetwerk voor de twee bronnen van plaatsen (Keuze 29): uit $SHIM_NET, of zonder netwerk
# als $SHIM_NET/aan er niet is. Elke andere curl is de echte.
url=${!#}
case "$url" in
  https://raw.githubusercontent.com/nvkelso/* | https://files.pythonhosted.org/*)
    echo "$url" >>"$SHIM_LOG/curl.log"
    out="" prev=""
    for a in "$@"; do
      [ "$prev" != -o ] || out=$a
      prev=$a
    done
    if [ ! -f "$SHIM_NET/aan" ]; then
      echo "curl: (6) Could not resolve host (installatietest: geen netwerk)" >&2
      exit 6
    fi
    exec cp "$SHIM_NET/${url##*/}" "$out"
    ;;
esac
exec /usr/bin/curl "$@"
SHIM
  chmod 755 "$WORK/shim/bin/docker" "$WORK/shim/bin/curl"
  : >"$WORK/shim/log/compose.log"
  : >"$WORK/shim/log/runs.log"
  : >"$WORK/shim/log/curl.log"

  # --- hulpjes ------------------------------------------------------------------------------
  cid() {
    docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" \
      --filter "label=com.docker.compose.service=$1" | head -n 1
  }
  running() { [ -n "$1" ] && [ "$(docker inspect --format '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }
  env_value() { as_root sed -n "s/^$1=//p" "$ROOT/.env"; }
  src_head() { as_root git -c safe.directory='*' -C "$ROOT/src" rev-parse HEAD; }
  container_env() { docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$1" | sed -n "s/^$2=//p"; }
  app_up() { curl -fs -o /dev/null -m 5 "http://127.0.0.1:$PORT/health"; }
  api() {
    INSTALL_API_URL="http://127.0.0.1:$PORT" INSTALL_API_STATE="$WORK/sessie.json" \
      python3 -I "$top/e2e/install_api.py" "$@"
  }
  said() { grep -qxF -- "$2" "$WORK/logs/$1.log"; }
  said_part() { grep -qF -- "$2" "$WORK/logs/$1.log"; }
  db_rev() {
    docker exec "$(cid platform)" python -c 'import sqlite3
c = sqlite3.connect("file:/data/platform.db?mode=ro", uri=True)
print(c.execute("select version_num from alembic_version").fetchone()[0])' </dev/null 2>/dev/null
  }
  # SQL in de draaiende app, als de gebruiker van de app (10001)
  app_sql() {
    docker exec "$(cid platform)" python -I -c 'import sqlite3, sys
c = sqlite3.connect("/data/platform.db", timeout=30)
c.executescript(sys.argv[1])
c.close()' "$1" </dev/null
  }
  head_rev() {
    local last
    last=$(git -C "$WORK/repo.git" ls-tree --name-only "$1" api/migrations/versions/ |
      grep -E '^api/migrations/versions/[0-9]{4}_[^/]*\.py$' | sort | tail -n 1)
    [ -n "$last" ] || return 1
    git -C "$WORK/repo.git" show "$1:$last" | sed -n 's/^revision = "\([0-9A-Za-z_]*\)"$/\1/p'
  }
  app_images_text() {
    local id tags out=()
    for id in $(app_images); do
      tags=$(docker image inspect --format '{{join .RepoTags ","}}' "$id" 2>/dev/null) || continue
      out+=("${tags:-<none>} ${id:7:12}")
    done
    local IFS=,
    echo "${out[*]}"
  }
  one_app_image() {
    local id
    id=$(docker image inspect --format '{{.Id}}' platform:local 2>/dev/null) || return 1
    [ -n "$id" ] && [ "$(app_images)" = "$id" ]
  }
  BACKUP_DIR=$ROOT/data/platform/backup
  GEO_DIR=$ROOT/data/platform/geo
  # De kopieën in de map backup, op naam (dus op tijd per head)
  backups() { as_root find "$BACKUP_DIR" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | sort | tr '\n' ' ' | sed 's/ $//'; }
  is_copy() { [[ $1 =~ ^platform-$2-[0-9]{8}T[0-9]{6}\.[0-9]{3}Z\.db$ ]]; }
  # Eigenaar en rechten
  owner_mode() { as_root stat -c '%u:%g %a' "$1"; }
  # Wat de app zelf schrijft, is van zijn gebruiker in het image: uid 10001 met de groep die
  # useradd --system hem gaf (in deze images 999). Gevraagd aan de app die draait
  app_user() {
    local c
    c=$(cid platform) && [ -n "$c" ] || return 1
    printf '%s:%s\n' "$(docker exec "$c" id -u)" "$(docker exec "$c" id -g)"
  }
  app_file() {
    local who
    who=$(app_user) && [ "${who%%:*}" = 10001 ] && [ "$(owner_mode "$1")" = "$who $2" ]
  }
  # Een kopie op de host, alleen lezen: head, integrity_check en het aantal gebruikers
  copy_facts() {
    as_root python3 -I -c 'import sqlite3, sys
c = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
head = c.execute("select version_num from alembic_version").fetchall()
ok = c.execute("pragma integrity_check").fetchone()[0]
users = c.execute("select count(*) from \"user\"").fetchone()[0]
print(f"{head[0][0] if len(head) == 1 else head} {ok} {users}")' "$BACKUP_DIR/$1"
  }
  # Wat de shims in de laatste run schreven
  in_step() { sed -n "/^== $1\$/,/^== /p" "$WORK/shim/log/$2" | sed '1d;/^== /d'; }
  places_loaded() { api get /api/v1/admin/search places.loaded 2>/dev/null; }
  wait_places() {
    local end=$((SECONDS + $1))
    until [ "$(places_loaded)" = "$2" ]; do
      [ "$SECONDS" -lt "$end" ] || return 1
      sleep 3
    done
  }
  net() { if [ "$1" = aan ]; then : >"$WORK/net/aan"; else rm -f "$WORK/net/aan"; fi; }
  # Eén run van een script (dit of dat van 0.7.1) in de runner; het script komt via stdin
  step() {
    local name=$1 script=$2 rc=0 kv envs=() f
    shift 2
    for kv in "$@"; do envs+=(-e "$kv"); done
    for f in compose.log runs.log curl.log; do echo "== $name" >>"$WORK/shim/log/$f"; done
    echo
    echo "== $name: $(basename "$script") $*"
    timeout 1800 docker run -i --rm --name "$RUNNER_NAME" --network host \
      -v /var/run/docker.sock:/var/run/docker.sock \
      -v "$(command -v docker)":/usr/bin/docker:ro \
      -v /usr/libexec/docker/cli-plugins:/usr/libexec/docker/cli-plugins:ro \
      -v "$WORK:$WORK" \
      -e PLATFORM_REPO="$WORK/repo.git" -e PLATFORM_ROOT="$ROOT" -e OFFICE=uit \
      -e GIT_CONFIG_GLOBAL="$WORK/gitconfig" -e SHIM_LOG="$WORK/shim/log" -e SHIM_NET="$WORK/net" \
      -e PORT="$PORT" -e HTTPS_PORT="$HTTPS_PORT" -e SHIM_COMPOSE_EXTRA="$SANDBOX_COMPOSE" \
      -e PATH="$WORK/shim/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
      "${envs[@]}" "$RUNNER_IMG" bash -s <"$script" >"$WORK/logs/$name.log" 2>&1 || rc=$?
    note_app
    for f in compose.log runs.log curl.log; do echo "== einde" >>"$WORK/shim/log/$f"; done
    if [ "$rc" != 0 ]; then
      echo "install.sh gaf $rc; de laatste regels:"
      tail -n 30 "$WORK/logs/$name.log"
    fi
    sed -n 's/^\(Reservekopie.*\)/  \1/p; s/^\(Plaatsnamen.*\)/  \1/p; s/^Let op: \(.*\)/  Let op: \1/p' "$WORK/logs/$name.log"
    return "$rc"
  }
  # De functie dc uit infra/single/README.md, met de paden van deze test
  dc() {
    local d=$ROOT/src/infra/single
    local f=(-f "$d/compose.yaml")
    if docker container inspect platform-office-1 >/dev/null 2>&1; then
      f+=(-f "$d/compose.office.yaml")
      if [ "$(docker info --format '{{.MemoryLimit}} {{.CPUCfsQuota}}')" = "true true" ]; then
        f+=(-f "$d/compose.office-limits.yaml")
      fi
    fi
    if docker container inspect platform-media-1 >/dev/null 2>&1; then
      f+=(-f "$d/compose.media.yaml")
      if [ "$(docker info --format '{{.MemoryLimit}} {{.CPUCfsQuota}}')" = "true true" ]; then
        f+=(-f "$d/compose.media-limits.yaml")
      fi
    elif [ -f "$d/compose.media-local.yaml" ] && as_root grep -qx 'MEDIA_SANDBOX=uit' "$ROOT/.env"; then
      f+=(-f "$d/compose.media-local.yaml")
    fi
    [ -z "$SANDBOX_COMPOSE" ] || f+=(-f "$SANDBOX_COMPOSE")
    as_root docker compose --project-directory "$d" --env-file "$ROOT/.env" "${f[@]}" "$@"
  }
  # De volgorde in het logboek van compose van één stap: $2 komt vóór $3
  compose_before() {
    local log a b
    log=$(in_step "$1" compose.log)
    a=$(grep -nF -- "$2" <<<"$log" | head -n 1 | cut -d: -f1)
    b=$(grep -nF -- "$3" <<<"$log" | head -n 1 | cut -d: -f1)
    [ -n "$a" ] && [ -n "$b" ] && [ "$a" -lt "$b" ]
  }
  # Het image waarmee de reservekopie in die stap gemaakt is (precies één run)
  run_image() {
    local runs
    runs=$(in_step "$1" runs.log)
    [ "$(grep -c . <<<"$runs")" = 1 ] && printf '%s\n' "$runs"
  }
  image_id() { docker image inspect --format '{{.Id}}' platform:local 2>/dev/null; }

  local schema_head file_id file_sha root app1 app2 app_vorige app3 app4 cid4 media3 copy0009 copy0010 rev
  schema_head=$(head_rev "$TO") || stop "geen migraties gevonden in $TO (api/migrations/versions)"
  printf 'Een bestand dat de update en de weg terug overleeft (installatietest van zoeken).\n' >"$WORK/bestand/notities.txt"
  file_sha=$(sha256sum "$WORK/bestand/notities.txt" | cut -d' ' -f1)

  # --- 1. 0.7.1 installeren, met het script van 0.7.1 ---------------------------------------
  STAGE=1
  step 1-van "$OLD_SH" PLATFORM_BRANCH=installatietest-van || stop "de installatie van 0.7.1 (${FROM:0:12}) lukte niet"
  check "1: de app draait op poort $PORT" app_up
  check "1: de broncode staat op ${FROM:0:12}" test "$(src_head)" = "$(git -C "$WORK/repo.git" rev-parse "$FROM^{commit}")"
  check "1: het schema staat op $ROLLBACK_HEAD (0.7.1)" test "$(db_rev)" = "$ROLLBACK_HEAD"
  check "1: nog geen map backup" eval '! as_root test -e "$BACKUP_DIR"'
  app1=$(image_id)
  check "1: een beheerder aangemaakt, met 2FA aan" api setup
  root=$(api get /api/v1/auth/me root_id) || stop "1: /auth/me gaf geen map"
  file_id=$(api upload "$root" notities.txt "$WORK/bestand/notities.txt") || stop "1: het bestand uploaden lukte niet"
  check "1: het bestand staat erin" test "$(api sha "$file_id")" = "$file_sha"

  # --- 2. bijwerken naar deze versie, zonder netwerk naar de bronnen ------------------------
  STAGE=2
  net uit
  step 2-bijwerken "$NEW_SH" PLATFORM_BRANCH=installatietest-naar || fout "2: install.sh gaf een fout"
  check "2: de app draait" app_up
  check "2: het schema staat op $schema_head (gezien: $(db_rev))" test "$(db_rev)" = "$schema_head"
  check "2: de broncode staat op ${TO:0:12}" test "$(src_head)" = "$(git -C "$WORK/repo.git" rev-parse "$TO^{commit}")"
  check "2: de reservekopie met het image van 0.7.1 (${app1:7:12})" test "$(run_image 2-bijwerken)" = "$app1"
  check "2: de kopie vóór de nieuwe app (compose run, dan up)" compose_before 2-bijwerken "run --rm --no-deps -T platform python -I -c <tekst> /data/platform.db /data/backup" "up -d --build --remove-orphans platform"
  copy0009=$(backups)
  check "2: één kopie platform-$ROLLBACK_HEAD-<tijd>.db (gezien: $copy0009)" is_copy "$copy0009" "$ROLLBACK_HEAD"
  check "2: de kopie: 600, van de gebruiker van de app (uid 10001, zoals platform.db)" app_file "$BACKUP_DIR/$copy0009" 600
  check "2: de map backup: 700, van 10001" test "$(owner_mode "$BACKUP_DIR")" = "10001:10001 700"
  check "2: de kopie is heel: $ROLLBACK_HEAD, integrity ok, de beheerder (gezien: $(copy_facts "$copy0009"))" test "$(copy_facts "$copy0009")" = "$ROLLBACK_HEAD ok 1"
  check "2: install.sh noemt de kopie, ook in de eindtekst" eval 'said 2-bijwerken "Reservekopie: $BACKUP_DIR/$copy0009" && said 2-bijwerken "Reservekopie van vóór deze run: $BACKUP_DIR/$copy0009"'
  check "2: beide bronnen geprobeerd, geen netwerk: niet geladen" eval '[ "$(in_step 2-bijwerken curl.log | grep -c .)" = 2 ] && said 2-bijwerken "Plaatsnamen: niet geladen (de download lukte niet)."'
  check "2: geen bron en geen .part in de map geo" test -z "$(as_root ls -A "$GEO_DIR")"
  check "2: de eindtekst van §3.10 stap 7" said 2-bijwerken "$SEARCH_LINE"
  check "2: PLAATSEN=aan in .env en PLATFORM_PLACES=aan in de app" eval '[ "$(env_value PLAATSEN)" = aan ] && [ "$(container_env "$(cid platform)" PLATFORM_PLACES)" = aan ]'
  check "2: /api/v1/admin/search: plaatsen niet geladen" test "$(places_loaded)" = false
  check "2: het bestand is er nog, heel" test "$(api sha "$file_id")" = "$file_sha"
  check "2: één image van de app, platform:local (gezien: $(app_images_text))" one_app_image
  app2=$(image_id)

  # --- 3. een update waarbij de reservekopie faalt: het script stopt vóór elke wijziging -----
  STAGE=3
  app_sql "CREATE TABLE face (id INTEGER PRIMARY KEY, data BLOB); INSERT INTO face (data) VALUES (x'00');
    CREATE TRIGGER face_ad AFTER DELETE ON face BEGIN SELECT RAISE(ABORT, 'installatietest'); END;" ||
    fout "3: de trigger in de database zetten lukte niet"
  local cid2 media2 rc3=0
  cid2=$(cid platform) media2=$(cid media)
  step 3-faalt "$NEW_SH" PLATFORM_SRC_REF="$TO2" || rc3=$?
  check "3: install.sh stopt met exit 1" test "$rc3" = 1
  check "3: de melding van de kopie en van install.sh" eval 'said 3-faalt "Reservekopie mislukt: installatietest" && said_part 3-faalt "De reservekopie van de database lukte niet"'
  check "3: geen up, en dus geen Klaar." eval '! in_step 3-faalt compose.log | grep -q "^compose up " && ! said_part 3-faalt "Klaar."'
  check "3: dezelfde app draait, op hetzelfde image" eval '[ "$(cid platform)" = "$cid2" ] && running "$cid2" && [ "$(image_id)" = "$app2" ] && app_up'
  check "3: dezelfde worker draait" eval '[ "$(cid media)" = "$media2" ] && running "$media2"'
  check "3: het schema staat nog op $schema_head" test "$(db_rev)" = "$schema_head"
  check "3: geen nieuwe kopie en geen .part (gezien: $(backups))" test "$(backups)" = "$copy0009"
  app_sql "DROP TABLE face;" || fout "3: de tabel face weghalen lukte niet"

  # --- 4. opnieuw, met netwerk: de bronnen, en geo-load in de draaiende app ------------------
  STAGE=4
  net aan
  # De app zet een symlink op <bron>.part in de map geo, naar een bestand van root buiten DATA_ROOT
  # (review golf 4, veilig F2): install.sh als root volgt hem niet
  # shellcheck disable=SC2016  # $1 en $2 zijn van sh -c
  as_root sh -c 'printf "van root\n" >"$1" && chmod 600 "$1" && ln -sfn "$1" "$2" && chown -h 10001:10001 "$2"' sh \
    "$WORK/van-root" "$GEO_DIR/ne_10m_admin_0_countries.geojson.part" || fout "4: de symlink van de app zetten lukte niet"
  step 4-opnieuw "$NEW_SH" PLATFORM_BRANCH=installatietest-naar || fout "4: install.sh gaf een fout"
  check "4: de app draait" app_up
  check "4: de bronnen gehaald (van het nepnetwerk)" eval '[ "$(in_step 4-opnieuw curl.log | grep -c .)" = 2 ] && said_part 4-opnieuw "Plaatsnamen: de bronnen staan klaar in "'
  check "4: beide bronnen heel, 640, van 10001" eval 'python3 -I "$top/tools/geo/haal.py" --naar "$WORK/net" --check >/dev/null &&
    as_root cmp -s "$GEO_DIR/ne_10m_admin_0_countries.geojson" "$WORK/net/ne_10m_admin_0_countries.geojson" &&
    as_root cmp -s "$GEO_DIR/reverse_geocoder-1.5.1.tar.gz" "$WORK/net/reverse_geocoder-1.5.1.tar.gz" &&
    [ "$(as_root stat -c "%u:%g %a" "$GEO_DIR/ne_10m_admin_0_countries.geojson" "$GEO_DIR/reverse_geocoder-1.5.1.tar.gz" | sort -u)" = "10001:10001 640" ]'
  check "4: de symlink van de app op de .part: het bestand van root blijft (inhoud, root, 600), geen .part en geen symlink" eval '
    [ "$(as_root cat "$WORK/van-root")" = "van root" ] && [ "$(as_root stat -c "%u %a" "$WORK/van-root")" = "0 600" ] &&
    [ -z "$(as_root find "$GEO_DIR" -name "*.part" -o -type l)" ]'
  check "4: dezelfde app (niets veranderde), dus geo-load in de draaiende app" eval '[ "$(cid platform)" = "$cid2" ] && said 4-opnieuw "Plaatsnamen laden (±1 minuut)…" && compose_before 4-opnieuw "up -d --build" "exec -T platform python -m app.cli geo-load"'
  check "4: /api/v1/admin/search: plaatsen geladen" wait_places 300 true
  check "4: de kopie van $schema_head met het image van deze versie (${app2:7:12})" test "$(run_image 4-opnieuw)" = "$app2"
  copy0010=$(backups)
  copy0010=${copy0010#"$copy0009 "}
  check "4: nu de kopie van $ROLLBACK_HEAD en één van $schema_head (gezien: $(backups))" eval 'is_copy "$copy0010" "$schema_head" && [ "$(backups)" = "$copy0009 $copy0010" ]'
  check "4: de eindtekst: Plaatsnamen: aan." said 4-opnieuw "Plaatsnamen: aan."

  # --- 5. de update erbovenop, met PLAATSEN=uit en dan weer aan ------------------------------
  STAGE=5
  # Het image van de vorige run is platform:local zoals stap 4 het achterliet. Met de opslag van
  # containerd (Docker 29) kan dat een andere id zijn dan app2, ook als er niets veranderde: de
  # id is die van de manifest-lijst, met de provenance van die build erin. Compose liet de app
  # dan draaien (zelfde config), en de kopie hoort met dit image te gaan
  app_vorige=$(image_id)
  step 5-uit "$NEW_SH" PLATFORM_SRC_REF="$TO2" PLAATSEN=uit || fout "5: install.sh (PLAATSEN=uit) gaf een fout"
  check "5: de app draait" app_up
  check "5: PLAATSEN=uit: geen download" test -z "$(in_step 5-uit curl.log)"
  check "5: PLAATSEN=uit in .env, PLATFORM_PLACES=uit in de app" eval '[ "$(env_value PLAATSEN)" = uit ] && [ "$(container_env "$(cid platform)" PLATFORM_PLACES)" = uit ]'
  check "5: de eindtekst: uit, en hoe het aan gaat" eval 'said 5-uit "Plaatsnamen: uit (PLAATSEN=uit)." && said_part 5-uit "| PLAATSEN=aan bash"'
  check "5: /api/v1/admin/search: plaatsen uit" test "$(places_loaded)" = false
  check "5: de kopie met het image van de vorige run (${app_vorige:7:12})" test "$(run_image 5-uit)" = "$app_vorige"
  local newest
  newest=$(backups)
  newest=${newest#"$copy0009 "}
  check "5: na twee runs op $schema_head staat de kopie van $ROLLBACK_HEAD er nog, van $schema_head alleen de nieuwste (gezien: $(backups))" \
    eval 'is_copy "$newest" "$schema_head" && [[ $newest > $copy0010 ]] && [ "$(backups)" = "$copy0009 $newest" ]'
  app3=$(image_id)
  check "5: het image is nieuw (de commit erbovenop), één image van de app (gezien: $(app_images_text))" eval '[ "$app3" != "$app2" ] && one_app_image'
  step 5-aan "$NEW_SH" PLATFORM_SRC_REF="$TO2" PLAATSEN=aan || fout "5: install.sh (PLAATSEN=aan) gaf een fout"
  check "5: weer aan: de bronnen stonden er al, geen download" eval '[ -z "$(in_step 5-aan curl.log)" ] && said_part 5-aan "Plaatsnamen: de bronnen staan er al"'
  check "5: weer aan: plaatsen geladen" wait_places 300 true
  copy0010=$(backups)
  copy0010=${copy0010#"$copy0009 "}
  check "5: na drie runs op $schema_head: nog steeds de $ROLLBACK_HEAD en de nieuwste $schema_head (gezien: $(backups))" \
    eval 'is_copy "$copy0010" "$schema_head" && [[ $copy0010 > $newest ]] && [ "$(backups)" = "$copy0009 $copy0010" ]'
  check "5: het bestand is er nog, heel" test "$(api sha "$file_id")" = "$file_sha"

  # --- 6. terug naar 0.7.1: precies de stappen van §3.10 -------------------------------------
  STAGE=6
  echo
  echo "== 6: terug naar 0.7.1 (§3.10)"
  media3=$(cid media)
  if as_root env GIT_CONFIG_GLOBAL="$WORK/gitconfig" git -C "$ROOT/src" fetch -q origin &&
    as_root git -C "$ROOT/src" cat-file -e "$FROM^{commit}"; then
    ok "6.1: git fetch en cat-file -e '${FROM:0:12}^{commit}' (het script van 0.7.1: $OLD_SH)"
  else
    fout "6.1: de commit van 0.7.1 is er niet"
  fi
  if dc stop platform media >"$WORK/logs/6-stop.log" 2>&1; then ok "6.2: dc stop platform media"; else fout "6.2: dc stop platform media"; fi
  # Een verse kopie op $schema_head vóór de downgrade (review golf 4, uitrol F5): die van
  # $ROLLBACK_HEAD is van de dag van de update, en wie 0.8.0 vers installeerde, heeft er geen
  if dc run --rm --no-deps -T platform python -m app.cli db-backup </dev/null >"$WORK/logs/6-kopie.log" 2>&1; then
    ok "6.3: dc run --rm --no-deps platform python -m app.cli db-backup"
  else
    fout "6.3: db-backup"
    tail -n 20 "$WORK/logs/6-kopie.log"
  fi
  vers=$(backups)
  vers=${vers#"$copy0009 "}
  check "6.3: een verse kopie van $schema_head (de vorige vervangen), en de $ROLLBACK_HEAD blijft (gezien: $(backups))" \
    eval 'is_copy "$vers" "$schema_head" && [[ $vers > $copy0010 ]] && [ "$(backups)" = "$copy0009 $vers" ]'
  check "6.3: het commando zegt waar de kopie staat" grep -qxE "De reservekopie staat in /data/backup/$vers \([0-9.]+ MB\)\." "$WORK/logs/6-kopie.log"
  check "6.3: de verse kopie is heel: $schema_head, integrity ok (gezien: $(copy_facts "$vers"))" test "$(copy_facts "$vers")" = "$schema_head ok 1"
  check "6.3: de kopie: 600, van de gebruiker van de app (uid 10001, zoals platform.db)" app_file "$BACKUP_DIR/$vers" 600
  copy0010=$vers
  if printf 'ja\n' | dc run --rm --no-deps -T platform python -m app.cli db-downgrade "$ROLLBACK_HEAD" >"$WORK/logs/6-downgrade.log" 2>&1; then
    ok "6.4: dc run --rm --no-deps platform python -m app.cli db-downgrade $ROLLBACK_HEAD (met \"ja\")"
  else
    fout "6.4: db-downgrade $ROLLBACK_HEAD"
    tail -n 20 "$WORK/logs/6-downgrade.log"
  fi
  # Zonder terminal (-T, "ja" uit een pipe) komt de Enter van het antwoord niet in de uitvoer:
  # de uitkomst staat op de regel van de vraag
  check "6.4: het commando zegt wat weg is, vraagt om \"ja\", en zegt dat het schema op $ROLLBACK_HEAD staat" eval \
    'grep -qF "Weg zijn daarna: de zoekindex" "$WORK/logs/6-downgrade.log" && grep -qxF "Typ \"ja\" om door te gaan: De database staat nu op revisie $ROLLBACK_HEAD." "$WORK/logs/6-downgrade.log"'
  step 6-terug "$OLD_SH" PLATFORM_BRANCH=installatietest-van PLATFORM_SRC_REF="$FROM" || fout "6.5: het script van 0.7.1 gaf een fout"
  check "6: de app draait" app_up
  check "6: de broncode staat op ${FROM:0:12}" test "$(src_head)" = "$(git -C "$WORK/repo.git" rev-parse "$FROM^{commit}")"
  check "6: het schema staat op $ROLLBACK_HEAD" test "$(db_rev)" = "$ROLLBACK_HEAD"
  check "6: de app is 0.7.1 (app.cli wel, app.backup en /api/v1/admin/search niet)" eval \
    'docker exec "$(cid platform)" python -c "import app.cli" </dev/null >/dev/null 2>&1 &&
    ! docker exec "$(cid platform)" python -c "import app.backup" </dev/null >/dev/null 2>&1 && ! api get /api/v1/admin/search >/dev/null 2>&1'
  check "6: de worker draait weer (een nieuwe container)" eval 'running "$(cid media)" && [ "$(cid media)" != "$media3" ]'
  check "6: het bestand is er nog, heel" test "$(api sha "$file_id")" = "$file_sha"
  check "6: de kopieën en de bronnen blijven staan" eval '[ "$(backups)" = "$copy0009 $copy0010" ] && [ "$(as_root ls "$GEO_DIR" | grep -c "\.\(geojson\|tar\.gz\)$")" = 2 ]'
  check "6: één image van de app, platform:local (gezien: $(app_images_text))" one_app_image
  app4=$(image_id)
  cid4=$(cid platform)

  # --- 7. weer vooruit: het script van de branch, zonder PLATFORM_SRC_REF --------------------
  STAGE=7
  step 7-vooruit "$NEW_SH" PLATFORM_BRANCH=installatietest-naar || fout "7: install.sh gaf een fout"
  check "7: de app draait (een nieuwe container)" eval 'app_up && [ "$(cid platform)" != "$cid4" ]'
  check "7: de broncode staat op ${TO:0:12}" test "$(src_head)" = "$(git -C "$WORK/repo.git" rev-parse "$TO^{commit}")"
  rev=$(db_rev)
  check "7: het schema staat op $schema_head (gezien: $rev)" test "$rev" = "$schema_head"
  check "7: de reservekopie met het image van 0.7.1 (${app4:7:12})" test "$(run_image 7-vooruit)" = "$app4"
  newest=$(backups)
  newest=${newest%" $copy0010"}
  check "7: een nieuwe kopie van $ROLLBACK_HEAD (per head de nieuwste), en de $schema_head blijft (gezien: $(backups))" \
    eval 'is_copy "$newest" "$ROLLBACK_HEAD" && [[ $newest > $copy0009 ]] && [ "$(backups)" = "$newest $copy0010" ]'
  check "7: de nieuwe kopie is heel: $ROLLBACK_HEAD, integrity ok (gezien: $(copy_facts "$newest"))" test "$(copy_facts "$newest")" = "$ROLLBACK_HEAD ok 1"
  check "7: de kopie: 600, van de gebruiker van de app (uid 10001, zoals platform.db)" app_file "$BACKUP_DIR/$newest" 600
  check "7: plaatsen geladen" wait_places 300 true
  check "7: het bestand is er nog, heel" test "$(api sha "$file_id")" = "$file_sha"
  check "7: één image van de app, platform:local (gezien: $(app_images_text))" one_app_image
  SUMMARY+=("reservekopieën aan het eind: $(backups)")
  SUMMARY+=("images van de kopie per stap: 2=${app1:7:12} (0.7.1), 4=${app2:7:12}, 5=${app2:7:12}, 7=${app4:7:12} (0.7.1)")
  ENDED=1
  einde
}

stappen_opruimen() {
  local rc=$? ids ref i
  set +e
  trap - EXIT INT TERM
  echo
  echo "== Opruimen"
  docker rm -f "$RUNNER_NAME" >/dev/null 2>&1
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
    for ref in $(own_images); do docker image rm -f "$ref" >/dev/null 2>&1; done
    if have platform:local; then docker image rm platform:local >/dev/null 2>&1; fi
  fi
  if [ -n "$LOGS_OUT" ] && [ -d "$WORK/logs" ]; then
    cp "$WORK/shim/log/"*.log "$WORK/logs/" 2>/dev/null
    for ref in "$WORK/logs/compose.log" "$WORK/logs/runs.log" "$WORK/logs/curl.log"; do
      [ ! -f "$ref" ] || mv "$ref" "${ref%.log}-shim.log"
    done
    mkdir -p "$LOGS_OUT" && cp -r "$WORK/logs/." "$LOGS_OUT/" && echo "Logs: $LOGS_OUT"
  fi
  if [ -n "$WORK" ] && [ -d "$WORK" ]; then
    # De data van de app is van 10001 en de rest van root: als root weg (in CI met sudo)
    if [ "$work_existed" = 1 ]; then as_root find "$WORK" -mindepth 1 -delete; else as_root rm -rf "$WORK"; fi
  fi
  for ref in "${maybe[@]}"; do
    if have "$ref"; then docker image rm "$ref" >/dev/null || echo "kon $ref niet weghalen"; fi
  done
  for ((i = ${#created[@]} - 1; i >= 0; i--)); do
    docker image rm "${created[i]}" >/dev/null 2>&1 || echo "kon ${created[i]} niet weghalen"
  done
  if [ "$borrowed" = 1 ]; then
    docker tag "$BORROWED" python:3.12-slim && docker image rm "$BORROWED" >/dev/null &&
      echo "python:3.12-slim heeft zijn eigen image weer."
  fi
  if [ "$own_project" = 1 ]; then
    ids="$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")$(own_images)"
    if [ -n "$ids" ] || have platform:local || { [ "$runner_built" = 1 ] && have "$RUNNER_IMG"; } || { [ "$borrowed" = 1 ] && have "$BORROWED"; }; then
      echo "LET OP: er is nog iets van de test over"
      rc=1
    else
      echo "Alles van de test is weg (containers, volumes, netwerken, images, de werkmap)."
    fi
  fi
  # Altijd, ook na een STOP of een afgebroken stap: de stap Samenvatting van CI leest dit blok
  echo
  echo "=== Samenvatting (test_install_search.sh --stappen) ==="
  [ "${#SUMMARY[@]}" = 0 ] || printf '%s\n' "${SUMMARY[@]}"
  echo "controles: $oks goed, $fails fout"
  if [ "$ENDED" != 1 ]; then
    echo "afgebroken in stap $STAGE (exit $rc): de stappen daarna liepen niet"
  fi
  echo "=== einde samenvatting ==="
  exit "$rc"
}

case "${1-}" in
  "") functies ;;
  --stappen) stappen ;;
  *)
    echo "Gebruik: $0 [--stappen]" >&2
    exit 2
    ;;
esac
