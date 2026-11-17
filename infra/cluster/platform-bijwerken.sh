#!/bin/sh
# Platform bijwerken: eigen commando in ClusterForge (Commando's), voor de
# template media-platform 1.0.0 zoals hij nu is. Draai het op het hele
# cluster, als root, tijdslimiet 3600 s, "Doorgaan bij een fout" uit.
# ClusterForge draait het node voor node, de nodes zonder VIP eerst; een
# weigering of fout stopt de run vóór de volgende node.
#
# Op elke node:
#   1. controles: mount van de bestanden, geen tweede platform, gereedschap
#   2. bouwt de versie die in ClusterForge staat (Wijzigen, Versie), als
#      code of image niet bij die versie horen (platform-src.sh)
#   3. bewaart het image dat er was als cf-platform:vorige (voor Platform terug)
#   4. alleen op de node met het VIP van het platform (na de overstap): een
#      back-up van de database vóór het bouwen, want keepalived of systemd kan
#      het nieuwe image daarna starten en de app migreert dan; daarna herstart
#      (platform-ctl.sh restart-if-changed, met nog een back-up) en wachten
#      tot de app antwoordt
# Het kiest nooit zelf een andere versie dan die in ClusterForge, en drukt
# nooit wachtwoorden of sleutels af.
set -u
LIB=/usr/local/lib/clusterforge
CTL=$LIB/platform-ctl.sh
SRCSH=$LIB/platform-src.sh
DATA=/srv/platform-data
ACTIVE=$DATA/.cf-platform-active
ENV=/etc/clusterforge/platform.env
STATE=/var/lib/clusterforge/platform-image.rev
BACKUPS=/var/backups/cf-platform
EIGEN=/var/lib/cf-platform/bijwerken
LOCK=/run/cf-platform-bijwerken.lock
WACHT=900

say() { echo "== $*"; }
note() { echo "   $*"; }
fail() { echo "GEWEIGERD/FOUT: $*" >&2; exit 1; }
kort() { printf '%.12s' "${1:-onbekend}"; }
env_get() { sed -n "s/^$1=//p" "$ENV" 2>/dev/null | tail -n 1; }
ctl_get() { sed -n "s/^$1='\\(.*\\)'\$/\\1/p" "$CTL" | head -n 1; }
has_ip() { ip -o addr show | grep -q " $1/"; }
img_id() { docker image inspect -f '{{.Id}}' "$1" 2>/dev/null; }
# Het image van de draaiende app-container van het platform hier, of leeg.
lopend_id() {
  c=$(docker ps -q --filter label=com.docker.compose.project=cf-platform \
    --filter label=com.docker.compose.service=platform --filter status=running | head -n 1)
  [ -n "$c" ] && docker inspect -f '{{.Image}}' "$c" 2>/dev/null
}
platform_hier() {
  systemctl is-active --quiet cf-platform && return 0
  [ -n "$(docker ps -q --filter label=com.docker.compose.project=cf-platform --filter status=running)" ]
}
# De versie van de app op adres $1 (leeg als daar geen platform van dit cluster antwoordt).
app_versie() {
  curl -fsS --max-time 4 "http://$1:${POORT}/api/v1/info" 2>/dev/null |
    grep '"mode": *"cluster"' | sed -n 's/.*"version": *"\([^"]*\)".*/\1/p'
}
# De rev van een image-id volgens eerdere runs (of leeg).
rev_van() { [ -n "$1" ] && awk -v id="$1" '$1 == id { r = $2 } END { if (r != "") print r }' "$EIGEN/gebouwd" 2>/dev/null; }
onthoud() { # onthoud <image-id> <rev>
  [ -n "$1" ] && [ -n "$2" ] || return 0
  [ "$(rev_van "$1")" = "$2" ] && return 0
  { tail -n 19 "$EIGEN/gebouwd" 2>/dev/null; echo "$1 $2 $(date -u +%Y-%m-%dT%H:%M:%SZ)"; } >"$EIGEN/gebouwd.nieuw" &&
    mv -f "$EIGEN/gebouwd.nieuw" "$EIGEN/gebouwd"
}
schrijf() { printf '%s\n' "$2" >"$1.nieuw" && mv -f "$1.nieuw" "$1"; }
# shellcheck disable=SC2012
nieuwste_dump() { ls -1t "$BACKUPS"/platform-*.dump 2>/dev/null | head -n 1; }
# De back-up van vóór deze versie, voor Platform terug. Een harde link: de
# vijf-nieuwste-regel van platform-ctl.sh wist hem dan niet.
bewaar_dump() {
  ln -f "$1" "$EIGEN/voor-update.dump" 2>/dev/null || cp -f "$1" "$EIGEN/voor-update.dump"
  schrijf "$EIGEN/voor-update.info" "dump=$(basename "$1") van=${basis_rev:-onbekend} naar=$nieuw_rev"
  note "back-up van vóór deze versie: $EIGEN/voor-update.dump"
}

# --- controles ------------------------------------------------------------
[ "$(id -u)" = 0 ] || fail "draai dit als root"
for f in "$CTL" "$SRCSH" "$ENV"; do
  [ -e "$f" ] || fail "$f ontbreekt: is de template media-platform op deze node uitgerold?"
done
for t in docker git curl ip flock mountpoint; do
  command -v "$t" >/dev/null || fail "$t ontbreekt"
done
mkdir -p "$EIGEN" && chmod 0700 "$EIGEN"
exec 9>"$LOCK"
flock -n 9 || fail "Platform bijwerken of Platform terug loopt al op deze node"

VIP=$(ctl_get VIP)
REF=$(ctl_get REF)
[ -n "$VIP" ] && [ -n "$REF" ] || fail "VIP of versie niet te lezen uit $CTL"
POORT=$(env_get PORT); POORT=${POORT:-80}
VIPHIER=nee; has_ip "$VIP" && VIPHIER=ja
say "$(hostname): $([ "$VIPHIER" = ja ] && echo "heeft het VIP van het platform ($VIP)" || echo "zonder het VIP van het platform")"

[ -e "$DATA/.cf-platform-active.terug" ] &&
  fail "$DATA/.cf-platform-active.terug bestaat: een eerdere Platform terug is niet afgemaakt (zie de uitvoer daarvan)"
if ! systemctl is-active --quiet cf-platform-mount || ! mountpoint -q "$DATA"; then
  fail "$DATA is niet gemount (journalctl -u cf-platform-mount); eerst dat oplossen"
fi
[ -f "$ACTIVE" ] || note "let op: de overstap is nog niet gedaan; er wordt alleen gebouwd"

# Draait het platform op meer dan één node? Hier zonder VIP is al fout; de
# andere nodes vragen we via http (PLATFORM_CLUSTER_NODES, geen geheim).
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
[ "$#" -le 1 ] || fail "het platform draait op $# nodes tegelijk ($lopen); eerst oplossen (keepalived, systemctl stop cf-platform op de node zonder VIP)"
if [ "$#" = 1 ]; then note "het platform draait nu op: $1"; else note "het platform draait nu op geen enkele node"; fi

if [ "$VIPHIER" = ja ] && [ -f "$ACTIVE" ]; then
  command -v pg_dump >/dev/null || fail "pg_dump ontbreekt; zonder back-up herstart platform-ctl.sh niet (postgresql-client)"
fi
command -v pg_dump >/dev/null || note "let op: pg_dump ontbreekt op deze node; neemt deze node later het VIP over, dan lukt een herstart na een update hier niet"
vrij=$(df -Pk /var/lib/docker 2>/dev/null | awk 'NR == 2 { print int($4 / 1048576) }')
[ "${vrij:-0}" -ge 5 ] || note "let op: maar ${vrij:-?} GiB vrij voor Docker; bouwen kan mislukken"

# --- versies vooraf -------------------------------------------------------
oud_rev=$(cat "$STATE" 2>/dev/null)
oud_id=$(img_id cf-platform:local)
lopend=$(lopend_id)
basis=${lopend:-$oud_id}
# Alleen een image zonder bekende rev krijgt die van platform-image.rev: na
# Platform terug is cf-platform:local het vorige image, met een andere rev.
if [ -n "$oud_id" ] && [ -n "$oud_rev" ] && [ -z "$(rev_van "$oud_id")" ]; then onthoud "$oud_id" "$oud_rev"; fi
local_rev=$(rev_van "$oud_id")
basis_rev=$(rev_van "$basis")
say "Versies vooraf"
note "versie in ClusterForge: $REF"
note "laatst gebouwd (platform-image.rev): $(kort "$oud_rev")"
note "cf-platform:local: $(kort "$local_rev"), image $(kort "${oud_id#sha256:}")"
[ -n "$lopend" ] && note "draait hier: image $(kort "${lopend#sha256:}") ($(kort "$basis_rev")), app $(app_versie 127.0.0.1)"
v=$(img_id cf-platform:vorige); [ -n "$v" ] && note "cf-platform:vorige: $(kort "$(rev_van "$v")"), image $(kort "${v#sha256:}")"

# --- bouwen ---------------------------------------------------------------
bouw=ja
if [ -n "$local_rev" ] && [ "$local_rev" != "$oud_rev" ]; then
  bouw=opnieuw
elif "$SRCSH" --check >/dev/null 2>&1; then
  bouw=nee
fi
# Op de node met het VIP, na de overstap: eerst een back-up. Is cf-platform:local
# eenmaal het nieuwe image, dan kan keepalived of systemd het starten (ook als het
# hier nu niet draait), en de app migreert dan bij de start.
dump_voor=""
if [ "$bouw" != nee ] && [ "$VIPHIER" = ja ] && [ -f "$ACTIVE" ]; then
  say "Back-up van de database vóór het bouwen (platform-ctl.sh db-backup)"
  voor=$(nieuwste_dump)
  "$CTL" db-backup || fail "back-up van de database mislukt; er is niets gebouwd en het platform is niet geraakt"
  dump_voor=$(nieuwste_dump)
  [ -n "$dump_voor" ] && [ "$dump_voor" != "$voor" ] ||
    fail "de back-up staat niet in $BACKUPS; er is niets gebouwd en het platform is niet geraakt"
fi
if [ -n "$basis" ]; then docker tag "$basis" cf-platform:basis || fail "docker tag"; fi
case "$bouw" in
  opnieuw)
    say "cf-platform:local is een teruggezet image ($(kort "$local_rev")); opnieuw bouwen: $REF"
    if ! "$SRCSH"; then
      docker image rm cf-platform:basis >/dev/null 2>&1
      fail "bouwen mislukt; het platform is niet geraakt"
    fi ;;
  nee) say "Code en image horen al bij $REF; niets te bouwen" ;;
  *)
    say "Bouwen: $REF (platform-src.sh)"
    if ! "$SRCSH"; then
      docker image rm cf-platform:basis >/dev/null 2>&1
      fail "bouwen mislukt; cf-platform:local is nog het vorige image en het platform is niet geraakt"
    fi ;;
esac
nieuw_rev=$(cat "$STATE" 2>/dev/null)
nieuw_id=$(img_id cf-platform:local)
onthoud "$nieuw_id" "$nieuw_rev"
# Alleen een nieuw image maakt een nieuwe voor-update.dump. Een herstart om
# andere instellingen (platform.env) laat die van de vorige update staan.
nieuw_vorige=nee
if [ -n "$basis" ] && [ -n "$nieuw_id" ] && [ "$nieuw_id" != "$basis" ]; then
  docker tag cf-platform:basis cf-platform:vorige || fail "cf-platform:vorige zetten mislukt"
  schrijf "$EIGEN/vorige.rev" "${basis_rev:-onbekend}"
  nieuw_vorige=ja
  say "Vorige image bewaard als cf-platform:vorige ($(kort "$basis_rev"))"
fi
docker image rm cf-platform:basis >/dev/null 2>&1
rm -f "$EIGEN/teruggezet"
if [ "$nieuw_vorige" = ja ] && [ -n "$dump_voor" ]; then bewaar_dump "$dump_voor"; fi

# --- herstarten, alleen op de node met het VIP --------------------------------
if [ "$VIPHIER" = nee ]; then
  say "Geen VIP: alleen gebouwd ($(kort "$nieuw_rev")). De node met het VIP herstart het platform."
  exit 0
fi
if ! [ -f "$ACTIVE" ] || ! systemctl is-active --quiet cf-platform; then
  say "cf-platform draait hier niet; niets te herstarten. Keepalived start het met het nieuwe image."
  exit 0
fi
if "$CTL" unchanged; then
  say "Het platform draait al met dit image en deze instellingen; niets te herstarten"
else
  voor=$(nieuwste_dump)
  say "Back-up van de database en herstart (platform-ctl.sh restart-if-changed)"
  "$CTL" restart-if-changed || fail "back-up of herstart mislukt; zonder back-up herstart het niet (het oude image draait dan nog)"
  # Deze back-up is van vlak voor de herstart: ook wat er tijdens het bouwen
  # nog in de database kwam, staat erin.
  dump=$(nieuwste_dump)
  if [ "$nieuw_vorige" = ja ] && [ -n "$dump" ] && [ "$dump" != "$voor" ]; then bewaar_dump "$dump"; fi
fi
say "Wachten tot de app antwoordt (hoogstens $WACHT s; migraties lopen bij de start)"
t=0; versie=""
while [ "$t" -lt "$WACHT" ]; do
  systemctl is-failed --quiet cf-platform && break
  versie=$(app_versie 127.0.0.1); [ -n "$versie" ] && break
  sleep 5; t=$((t + 5))
done
[ -n "$versie" ] || fail "de app antwoordt niet (journalctl -u cf-platform; platform-ctl.sh status). Terug: het commando Platform terug"
say "Klaar: app $versie, code $(kort "$nieuw_rev") ($REF), vorige $(kort "$(cat "$EIGEN/vorige.rev" 2>/dev/null)")"
"$CTL" status --stil >/dev/null 2>&1 || true
