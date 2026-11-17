#!/bin/sh
# Platform terug: eigen commando in ClusterForge (Commando's), na een
# Platform bijwerken dat slecht uitpakte. Draai het op het hele cluster, als
# root, tijdslimiet 3600 s, "Doorgaan bij een fout" uit. De nodes zonder VIP
# eerst, de nodes met een VIP als laatste. Een VIP is daar elk VIP van het
# cluster, ook dat van de database: heeft een andere node dat, dan komt die ná
# de node met het VIP van het platform. Moet de database terug, dan doet die
# node dan nog niets (NOG NIET), de laatste node vraagt om nog een run (NOG NIET
# KLAAR), en de tweede run maakt het af. Elke node meldt daarvoor in
# $DATA/.cf-platform-bijwerken welk image hij heeft.
#
# Op elke node: cf-platform:vorige wordt weer cf-platform:local (het image
# dat er was, blijft als cf-platform:teruggezet). Op de node met het VIP
# daarna ook de database en een herstart.
#
# De database: de app migreert bij de start alleen vooruit (Alembic
# upgrade head) en een oudere app start niet op een nieuwer schema. Kent het
# vorige image de revisie van de database niet, dan moet de database terug
# naar de back-up van vóór de update ($EIGEN/voor-update.dump, gemaakt door
# Platform bijwerken). Wat daarna in de database kwam, is dan weg. Dat doet
# dit commando alleen met MET_DATABASE=ja hieronder; anders zegt het wat er
# zou gebeuren en verandert het niets, op geen enkele node.
#
# Stopt het halverwege terwijl de database al (misschien) terug is, dan
# blijft de markering opzij en het platform uit. Nog een keer draaien kijkt
# naar de database en maakt het af.
MET_DATABASE=nee
# Een andere back-up dan die van vóór de update (pg_dump -Fc), alleen als je
# weet wat je doet; leeg is $EIGEN/voor-update.dump.
DUMP=
set -u
LIB=/usr/local/lib/clusterforge
CTL=$LIB/platform-ctl.sh
DATA=/srv/platform-data
ACTIVE=$DATA/.cf-platform-active
OPZIJ=$DATA/.cf-platform-active.terug
ENV=/etc/clusterforge/platform.env
BACKUPS=/var/backups/cf-platform
EIGEN=/var/lib/cf-platform/bijwerken
LOCK=/run/cf-platform-bijwerken.lock
WACHT=900
GEDEELD=$DATA/.cf-platform-bijwerken

say() { echo "== $*"; }
note() { echo "   $*"; }
# Wordt gezet zodra de nodes zonder VIP al terug kunnen zijn; elke fout
# daarna zegt dan hoe je ze weer gelijk zet. Staat de markering opzij, dan
# zegt herstel_markering wat er moet gebeuren.
HINT=
# Waar de database is bij het terugzetten: geen (markering niet opzij), voor
# (ongewijzigd), bezig (terugzetten loopt of uitkomst nog niet gelezen),
# teruggezet (database terug, image nog niet), klaar (allebei terug).
STAND=geen
fail() {
  echo "GEWEIGERD/FOUT: $*" >&2
  [ -n "$HINT" ] && [ "$STAND" = geen ] && echo "   $HINT" >&2
  exit 1
}
kort() { printf '%.12s' "${1:-onbekend}"; }
env_get() { sed -n "s/^$1=//p" "$ENV" 2>/dev/null | tail -n 1; }
ctl_get() { sed -n "s/^$1='\\(.*\\)'\$/\\1/p" "$CTL" | head -n 1; }
has_ip() { ip -o addr show | grep -q " $1/"; }
img_id() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null; }
lopend_id() {
  c=$(docker ps -q --filter label=com.docker.compose.project=cf-platform \
    --filter label=com.docker.compose.service=platform --filter status=running | head -n 1)
  [ -n "$c" ] && docker inspect -f '{{.Image}}' "$c" 2>/dev/null
}
platform_hier() {
  systemctl is-active --quiet cf-platform && return 0
  [ -n "$(docker ps -q --filter label=com.docker.compose.project=cf-platform --filter status=running)" ]
}
app_versie() {
  curl -fsS --max-time 4 "http://$1:${POORT}/api/v1/info" 2>/dev/null |
    grep '"mode": *"cluster"' | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p'
}
rev_van() { [ -n "$1" ] && awk -v id="$1" '$1 == id { r = $2 } END { if (r != "") print r }' "$EIGEN/gebouwd" 2>/dev/null; }
schrijf() { printf '%s\n' "$2" >"$1.nieuw" && mv -f "$1.nieuw" "$1"; }
# De nieuwste Alembic-revisie die de code in image $1 kent (zonder netwerk).
alembic_head() {
  docker run --rm --network none "$1" python -c \
    'from alembic.script import ScriptDirectory; print(ScriptDirectory("migrations").get_current_head())' 2>/dev/null | tail -n 1
}
# De database uit PLATFORM_DATABASE_URL, zonder iets af te drukken.
db_delen() {
  url=$(env_get PLATFORM_DATABASE_URL)
  rest=${url#*://}; cred=${rest%%@*}; hostdb=${rest#*@}
  DBU=${cred%%:*}; DBP=${cred#*:}
  DBH=${hostdb%%/*}; DBN=${hostdb#*/}; DBN=${DBN%%\?*}
  DBPORT=${DBH##*:}; DBH=${DBH%:*}
  [ "$DBPORT" = "$DBH" ] && DBPORT=5432
  [ -n "$DBU" ] && [ -n "$DBH" ] && [ -n "$DBN" ]
}
psql_db() { PGPASSWORD=$DBP PGCONNECT_TIMEOUT=10 psql -X -h "$DBH" -p "$DBPORT" -U "$DBU" -d "$DBN" "$@"; }
db_revisie() { psql_db -tA -c 'SELECT version_num FROM alembic_version' 2>/dev/null | head -n 1; }
dump_revisie() {
  pg_restore -a -t alembic_version -f - "$1" 2>/dev/null | awk 'f { print; exit } /^COPY .*alembic_version/ { f = 1 }'
}
wacht_op_app() {
  t=0; versie=""
  while [ "$t" -lt "$WACHT" ]; do
    systemctl is-failed --quiet cf-platform && break
    versie=$(app_versie 127.0.0.1); [ -n "$versie" ] && return 0
    sleep 5; t=$((t + 5))
  done
  return 1
}
# --- samen met de andere nodes (zoals in Platform bijwerken) -----------------
eigen_naam() {
  for paar in $(env_get PLATFORM_CLUSTER_NODES | tr ',' ' '); do
    adres=${paar#*=}
    if [ -n "$adres" ] && has_ip "$adres"; then echo "${paar%%=*}"; return; fi
  done
  hostname
}
meld() { mkdir -p "$GEDEELD" && schrijf "$GEDEELD/node-$NAAM" "$1 $(date -u +%Y-%m-%dT%H:%M:%SZ)"; }
nog_niet() {
  w=""
  for paar in $(env_get PLATFORM_CLUSTER_NODES | tr ',' ' '); do
    n=${paar%%=*}
    [ -n "$n" ] && [ "$n" != "$NAAM" ] || continue
    case "$(cat "$GEDEELD/node-$n" 2>/dev/null)" in "$1 "*) ;; *) w="$w${w:+ }$n" ;; esac
  done
  echo "$w"
}
wacht_op() {
  # shellcheck disable=SC2086
  mkdir -p "$GEDEELD" && { echo "$2"; printf '%s\n' $3; } >"$GEDEELD/$1.nieuw" && mv -f "$GEDEELD/$1.nieuw" "$GEDEELD/$1"
}
# Wat de nodes $1 zelf het laatst meldden (voor de uitvoer).
meldingen() { for n in $1; do note "$n meldde: $(cat "$GEDEELD/node-$n" 2>/dev/null || echo niets)"; done; }
gewacht() {
  f=$GEDEELD/$1
  [ "$(head -n 1 "$f" 2>/dev/null)" = "$2" ] && sed 1d "$f" | grep -qxF "$NAAM" || return 1
  { echo "$2"; sed 1d "$f" | grep -vxF "$NAAM" || true; } >"$f.nieuw" && mv -f "$f.nieuw" "$f" || return 1
  [ "$(wc -l <"$f")" -le 1 ] || return 1
  rm -f "$f"
}
# Waar staat de database na een poging: teruggezet (revisie van het vorige
# image), voor (nog de revisie van het begin: één transactie, dus niets
# veranderd) of onbekend. Een paar keer proberen als hij niet te lezen is.
db_stand() {
  i=0
  while :; do
    r=$(db_revisie)
    [ "$r" = "$doel_head" ] && { echo teruggezet; return; }
    [ -n "$r" ] && [ "$r" = "$db_rev" ] && { echo voor; return; }
    i=$((i + 1))
    [ -z "$r" ] && [ "$i" -lt 4 ] || break
    sleep 5
  done
  echo onbekend
}
# Bij elke afloop terwijl de markering opzij staat. Alleen als de database
# zeker niet veranderd is (voor) of als ook het image terug is (klaar), komt
# de markering terug en start cf-platform. Anders zou het nieuwe image een
# teruggezette database weer vooruit migreren: dan blijft het platform uit.
herstel_markering() {
  [ -e "$OPZIJ" ] || return 0
  [ "$STAND" = bezig ] && STAND=$(db_stand)
  case "$STAND" in
    voor | klaar)
      mv -f "$OPZIJ" "$ACTIVE" && systemctl reset-failed cf-platform 2>/dev/null
      systemctl start --no-block cf-platform 2>/dev/null
      echo "   markering teruggezet; cf-platform start weer (journalctl -u cf-platform)" >&2
      [ "$STAND" = klaar ] || [ -z "$HINT" ] || echo "   $HINT" >&2 ;;
    teruggezet)
      echo "   De database is al teruggezet (revisie $doel_head), het image op deze node nog niet." >&2
      echo "   De markering blijft opzij en het platform blijft uit: het nieuwe image zou de database" >&2
      echo "   weer vooruit migreren. Draai Platform terug nog een keer; dat maakt het af." >&2 ;;
    *)
      echo "   Of de database al is teruggezet, is niet te lezen. De markering blijft opzij en het" >&2
      echo "   platform blijft uit. Draai Platform terug nog een keer met MET_DATABASE=ja zodra" >&2
      echo "   PostgreSQL bereikbaar is; dat kijkt naar de database en maakt het af." >&2 ;;
  esac
}

# --- controles (zoals Platform bijwerken) ---------------------------------
[ "$(id -u)" = 0 ] || fail "draai dit als root"
for f in "$CTL" "$ENV"; do [ -e "$f" ] || fail "$f ontbreekt"; done
for t in docker curl ip flock mountpoint; do command -v "$t" >/dev/null || fail "$t ontbreekt"; done
mkdir -p "$EIGEN" && chmod 0700 "$EIGEN"
exec 9>"$LOCK"
flock -n 9 || fail "Platform bijwerken of Platform terug loopt al op deze node"
VIP=$(ctl_get VIP)
[ -n "$VIP" ] || fail "VIP niet te lezen uit $CTL"
POORT=$(env_get PORT); POORT=${POORT:-80}
VIPHIER=nee; has_ip "$VIP" && VIPHIER=ja
NAAM=$(eigen_naam)
say "$(hostname): $([ "$VIPHIER" = ja ] && echo "heeft het VIP van het platform ($VIP)" || echo "zonder het VIP van het platform")"
# De node met het VIP komt als laatste: faalt het hier vóór het image terug
# is, dan kunnen de andere nodes al het vorige image hebben. Wacht die node
# op deze (hieronder, NOG NIET), dan geldt dat ook voor een fout hier.
if [ "$VIPHIER" = ja ]; then
  HINT="De nodes zonder VIP hebben mogelijk al het vorige image; het platform hier draait nog de nieuwe versie. Draai Platform bijwerken om ze weer gelijk te zetten."
elif sed 1d "$GEDEELD/wacht-terug" 2>/dev/null | grep -qxF "$NAAM"; then
  HINT="De node met het VIP van het platform wacht op deze node en heeft nog niets gedaan; andere nodes hebben mogelijk al het vorige image. Draai Platform bijwerken om ze weer gelijk te zetten."
fi
if ! systemctl is-active --quiet cf-platform-mount || ! mountpoint -q "$DATA"; then
  fail "$DATA is niet gemount (journalctl -u cf-platform-mount); eerst dat oplossen"
fi
# Staat de markering opzij, dan is een eerdere Platform terug halverwege
# gestopt (het platform is uit); deze run maakt hem af.
HERVAT=nee
if [ -e "$OPZIJ" ]; then
  [ -e "$ACTIVE" ] && fail "$OPZIJ en $ACTIVE bestaan allebei: een eerdere Platform terug is halverwege gestopt en de markering is daarna met de hand teruggezet? Haal $OPZIJ weg als het platform goed draait"
  HERVAT=ja
  note "een eerdere Platform terug is halverwege gestopt (de markering staat opzij, het platform is uit); deze run maakt hem af"
fi
if [ "$VIPHIER" = nee ] && platform_hier; then
  fail "cf-platform draait op deze node zonder het VIP; stop het hier eerst (systemctl stop cf-platform)"
fi
lopen=""; platform_hier && lopen=$(hostname)
for paar in $(env_get PLATFORM_CLUSTER_NODES | tr ',' ' '); do
  naam=${paar%%=*}; adres=${paar#*=}
  if [ -z "$adres" ] || has_ip "$adres"; then continue; fi
  [ -n "$(app_versie "$adres")" ] && lopen="$lopen${lopen:+ }$naam"
done
# shellcheck disable=SC2086
set -- $lopen
[ "$#" -le 1 ] || fail "het platform draait op $# nodes tegelijk ($lopen); eerst oplossen"

# --- wat gaat terug -------------------------------------------------------
doel=$(img_id cf-platform:vorige)
[ -n "$doel" ] || fail "cf-platform:vorige ontbreekt op deze node (nooit Platform bijwerken gedraaid, of weggehaald met Docker, Opruimen). Terug kan dan alleen door in ClusterForge de vorige commit als Versie te kiezen."
lokaal=$(img_id cf-platform:local)
doel_rev=$(rev_van "$doel"); [ -n "$doel_rev" ] || doel_rev=$(cat "$EIGEN/vorige.rev" 2>/dev/null)
doel_head=$(alembic_head cf-platform:vorige)
[ -n "$doel_head" ] || fail "de Alembic-revisie van cf-platform:vorige is niet te lezen"
say "Versies"
note "nu cf-platform:local: $(kort "$(rev_van "$lokaal")"), image $(kort "${lokaal#sha256:}"), Alembic $(alembic_head cf-platform:local)"
note "terug naar cf-platform:vorige: $(kort "$doel_rev"), image $(kort "${doel#sha256:}"), Alembic $doel_head"
lopend=$(lopend_id); [ -n "$lopend" ] && note "draait hier: image $(kort "${lopend#sha256:}"), app $(app_versie 127.0.0.1)"

# Moet de database terug? Elke node kijkt, zodat een weigering de run stopt
# vóór er iets verandert.
db_rev=""
if command -v psql >/dev/null && db_delen; then db_rev=$(db_revisie); fi
if [ -z "$db_rev" ]; then
  [ "$VIPHIER" = ja ] && fail "de revisie van de database is niet te lezen (psql, PostgreSQL via het VIP van de database)"
  db_rev=$(alembic_head cf-platform:local)
  note "database niet gelezen (geen psql?); aangenomen: revisie $db_rev"
fi
note "database: Alembic $db_rev"
DB_TERUG=nee; [ "$db_rev" != "$doel_head" ] && DB_TERUG=ja
if [ "$DB_TERUG" = ja ] && [ "$MET_DATABASE" != ja ]; then
  say "NIETS GEDAAN: het vorige image start niet op deze database"
  note "De database staat op revisie $db_rev, het vorige image kent tot $doel_head."
  note "Platform terug moet dan ook de database terugzetten naar de back-up van vóór de"
  note "update (op de node met het VIP: $EIGEN/voor-update.dump). Alles wat daarna in de"
  note "database is geschreven, is dan weg; bestanden die sindsdien zijn geüpload staan"
  note "nog in $DATA maar de app kent ze niet meer, en wat sindsdien is gewist, mist."
  note "Wil je dat: zet bovenaan dit commando MET_DATABASE=ja en draai het opnieuw."
  if [ "$HERVAT" = ja ]; then
    note "Let op: de markering staat nog opzij van een eerdere Platform terug, dus het platform is"
    note "uit. Wil je toch door met de nieuwe versie: mv $OPZIJ $ACTIVE (op één node)."
  fi
  exit 1
fi

# --- image terug ----------------------------------------------------------
image_terug() {
  if [ "$lokaal" = "$doel" ]; then
    say "cf-platform:local is al het vorige image"
    return 0
  fi
  if [ -n "$lokaal" ]; then docker tag cf-platform:local cf-platform:teruggezet || fail "docker tag"; fi
  docker tag cf-platform:vorige cf-platform:local || fail "docker tag"
  schrijf "$EIGEN/teruggezet" "van=$(rev_van "$lokaal") naar=$doel_rev $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  say "cf-platform:local is weer het vorige image ($(kort "$doel_rev")); het andere heet nu cf-platform:teruggezet"
}
if [ "$VIPHIER" = nee ] || { ! [ -f "$ACTIVE" ] && [ "$HERVAT" = nee ]; }; then
  image_terug
  meld "terug $doel_head $doel_rev" ||
    fail "$GEDEELD/node-$NAAM schrijven mislukt; zonder die melding zet de node met het VIP de database niet terug"
  say "Klaar op deze node (geen VIP of geen overstap): alleen het image."
  if gewacht wacht-terug "terug $doel_head"; then
    echo "NOG NIET KLAAR: de node met het VIP van het platform wachtte op deze node en heeft nog niets gedaan; nu heeft elke andere node het vorige image: draai Platform terug nog een keer." >&2
    exit 1
  fi
  exit 0
fi

# --- database terug (alleen de node met het VIP) ----------------------------
if [ "$DB_TERUG" = ja ]; then
  for t in pg_dump pg_restore psql; do command -v "$t" >/dev/null || fail "$t ontbreekt (postgresql-client)"; done
  dump=${DUMP:-$EIGEN/voor-update.dump}
  [ -s "$dump" ] || fail "geen back-up van vóór de update ($dump)"
  d_rev=$(dump_revisie "$dump")
  [ "$d_rev" = "$doel_head" ] ||
    fail "de back-up $dump staat op revisie ${d_rev:-onbekend}, niet op $doel_head; niets veranderd aan de database"
  note "back-up: $dump van $(date -r "$dump" '+%Y-%m-%d %H:%M'), revisie $d_rev"
  # Pas de database terugzetten als elke andere node het vorige image heeft.
  # Anders kan een node die ClusterForge hierna draait (met het VIP van de
  # database) nog weigeren, en dan draait hier het oude platform op de oude
  # database terwijl die node het nieuwe image houdt.
  wacht=$(nog_niet "terug $doel_head")
  if [ -n "$wacht" ]; then
    wacht_op wacht-terug "terug $doel_head" "$wacht" || fail "$GEDEELD/wacht-terug schrijven mislukt"
    say "NOG NIET: het vorige image staat nog niet als cf-platform:local op $wacht"
    meldingen "$wacht"
    note "Deze node heeft het VIP van het platform en zet de database pas terug als elke andere"
    note "node het vorige image heeft. ClusterForge draait $wacht na deze node (een ander VIP,"
    note "zoals dat van de database). Hier is niets veranderd; draai Platform terug na deze run"
    note "nog een keer. Zegt geen node daarna NOG NIET KLAAR, dan meldde een node een ander vorig"
    note "image (een andere Alembic-revisie, zie boven)."
    if [ "$HERVAT" = ja ]; then
      note "Let op: de markering staat nog opzij van een eerdere Platform terug, dus het platform is"
      note "uit tot die tweede run."
    fi
    exit 0
  fi
  rm -f "$GEDEELD/wacht-terug" "$GEDEELD/wacht-bijwerken"
  say "Platform op alle nodes tegenhouden (markering opzij) en hier stoppen"
  STAND=voor
  trap herstel_markering EXIT
  trap 'fail "afgebroken (Annuleren of de tijdslimiet van ClusterForge)"' INT TERM HUP
  if [ "$HERVAT" = nee ]; then mv "$ACTIVE" "$OPZIJ" || fail "markering opzij zetten mislukt"; fi
  systemctl stop cf-platform
  systemctl reset-failed cf-platform 2>/dev/null
  t=0; while platform_hier && [ "$t" -lt 150 ]; do sleep 2; t=$((t + 2)); done
  platform_hier && fail "cf-platform stopt niet"
  say "Eerst een back-up van de database zoals hij nu is"
  "$CTL" db-backup || fail "back-up mislukt; niets veranderd aan de database"
  # shellcheck disable=SC2012
  nu=$(ls -1t "$BACKUPS"/platform-*.dump 2>/dev/null | head -n 1)
  [ -n "$nu" ] && { ln -f "$nu" "$EIGEN/voor-terug.dump" 2>/dev/null || cp -f "$nu" "$EIGEN/voor-terug.dump"; }
  say "Database terugzetten (één transactie: alles of niets)"
  rm -f "$EIGEN/pg_restore.rc"
  # Vanaf hier kan de database terug zijn: alleen wat we daarna lezen telt.
  STAND=bezig
  {
    pg_restore --no-owner --no-privileges --single-transaction -f - "$dump"
    echo $? >"$EIGEN/pg_restore.rc"
  } | awk '
      z { print; next }
      { buf = buf $0 "\n" }
      $0 == "BEGIN;" { printf "%s", buf; print "DROP OWNED BY CURRENT_USER;"; z = 1; next }
      NR > 500 { exit 3 }' |
    psql_db -q -v ON_ERROR_STOP=1 >/dev/null 2>"$EIGEN/terug.log"
  prc=$?
  rc=$(cat "$EIGEN/pg_restore.rc" 2>/dev/null)
  STAND=$(db_stand)
  case "$STAND" in
    teruggezet) ;;
    voor)
      grep -m 1 'ERROR' "$EIGEN/terug.log" | cut -c1-200 >&2
      fail "database terugzetten mislukt (pg_restore ${rc:-?}, psql $prc); de database is ongewijzigd (revisie $db_rev). Details (kan gegevens bevatten): $EIGEN/terug.log" ;;
    *)
      grep -m 1 'ERROR' "$EIGEN/terug.log" | cut -c1-200 >&2
      fail "database terugzetten: de uitkomst is niet te lezen (pg_restore ${rc:-?}, psql $prc). Details (kan gegevens bevatten): $EIGEN/terug.log" ;;
  esac
  say "Database staat op revisie $doel_head"
  image_terug
  STAND=klaar
  meld "terug $doel_head $doel_rev" || note "let op: $GEDEELD/node-$NAAM schrijven mislukt"
  herstel_markering
  trap - EXIT INT TERM HUP
  HINT=
else
  HINT=
  rm -f "$GEDEELD/wacht-terug" "$GEDEELD/wacht-bijwerken"
  say "De database hoeft niet terug (revisie $db_rev)"
  image_terug
  meld "terug $doel_head $doel_rev" || note "let op: $GEDEELD/node-$NAAM schrijven mislukt"
  if [ "$HERVAT" = ja ]; then
    mv -f "$OPZIJ" "$ACTIVE" || fail "markering terugzetten mislukt ($OPZIJ)"
    note "markering teruggezet"
  fi
  systemctl reset-failed cf-platform 2>/dev/null
  [ "$(lopend_id)" = "$doel" ] || systemctl restart cf-platform
fi

say "Wachten tot de app antwoordt (hoogstens $WACHT s)"
wacht_op_app || fail "de app antwoordt niet (journalctl -u cf-platform; platform-ctl.sh status)"
say "Klaar: app $versie, image $(kort "$doel_rev")"
case "$doel_rev" in
  [0-9a-f]*[0-9a-f]) note "Zet in ClusterForge (cluster, Wijzigen, Versie) de versie op $doel_rev," ;;
  *) note "De commit van het vorige image is onbekend; zet in ClusterForge de Versie op de vorige commit," ;;
esac
note "anders bouwt de volgende uitrol of Platform bijwerken de nieuwere versie weer."
"$CTL" status --stil >/dev/null 2>&1 || true
