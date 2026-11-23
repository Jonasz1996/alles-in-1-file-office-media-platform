#!/usr/bin/env bash
# De twee opgeslagen commando's van ClusterForge (infra/cluster/platform-bijwerken.sh en
# platform-terug.sh) op een nagebootst cluster van drie nodes (docs/runbooks/cluster.md).
#
#   bash infra/cluster/tests/test_cluster_scripts.sh
#   CF_TEMPLATE=<ClusterForge>/internal/templates/builtin/media-platform/1.0.0 \
#     bash infra/cluster/tests/test_cluster_scripts.sh      # ook de afspraken met de template
#
# Er draait geen Docker, systemd, keepalived of PostgreSQL. Elke node is een map met nep-
# gereedschap (docker, systemctl, ip, curl, psql, pg_dump, pg_restore, mountpoint, id, hostname,
# df, sleep, git) en PATH bevat alleen die map: wat een script nodig heeft en hier niet staat,
# valt meteen op. De scripts zelf draaien ongewijzigd, op de paden na (LIB, DATA, ENV, STATE,
# BACKUPS, EIGEN, LOCK en WACHT bovenaan worden mappen van de test), met dash als dat er is
# (zoals /bin/sh op Debian). ClusterForge draait een commando node voor node, de nodes zonder
# VIP eerst, en stopt bij de eerste node die faalt ("Verder op de volgende nodes als het op een
# node mislukt" uit); draai_cluster doet dat na. Een VIP is daar elk VIP van het cluster, ook dat
# van de database (de primary van Patroni), en de volgorde onder de VIP-houders blijft die van de
# nodes (updates.Order, een stabiele sortering).
#
# Het nagebootste platform: een image heeft een Alembic-head en een versie. Start cf-platform
# (alleen met de markering, zoals ConditionPathExists in de unit), dan migreert de app de
# database vooruit naar zijn head; staat de database verder dan het image kent, dan start het
# niet. Een dump bewaart de revisie en de "rijen" van de database; psql zet een terugzet alleen
# door bij COMMIT (EOF midden in de transactie: de server rolt terug).
#
# De controles zijn vaak bash -c '...' _ "$x": de $ in de enkele quotes hoort bij die binnenste bash.
# shellcheck disable=SC2016
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(cd "$here/../../.." && pwd)
bijwerken_sh=$here/../platform-bijwerken.sh
terug_sh=$here/../platform-terug.sh
work=$(mktemp -d)
# CLUSTER_TEST_BEWAAR=1 laat de map met de uitvoer van elke run staan (om na te kijken).
if [ -n "${CLUSTER_TEST_BEWAAR:-}" ]; then echo "--    uitvoer in $work"; else trap 'rm -rf "$work"' EXIT; fi
SH=$(command -v dash || echo /bin/sh)

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
bevat() { grep -qF -- "$2" "$1"; }
bevat_niet() { ! grep -qF -- "$2" "$1"; }
is() { [ "$1" = "$2" ] || { printf '      verwacht "%s", kreeg "%s"\n' "$2" "$1"; return 1; }; }

# --- statische controles ---------------------------------------------------------------------

for s in "$bijwerken_sh" "$terug_sh"; do
  n=${s##*/}
  check "sh -n $n" sh -n "$s"
  if command -v dash >/dev/null; then check "dash -n $n" dash -n "$s"; fi
  if command -v shellcheck >/dev/null; then
    check "shellcheck $n" shellcheck "$s"
  else
    echo "--    shellcheck ontbreekt hier; de job infra in CI draait het wel"
  fi
done

# Wat de scripts van de app verwachten: /api/v1/info met version en mode (cluster), en de
# migraties in het image onder /srv/migrations zonder ENTRYPOINT (alembic_head in
# platform-terug.sh draait python -c in /srv).
check "/api/v1/info geeft version en mode" awk '/^class Info\(BaseModel\):$/ { f = 1; next }
  f && /^    version: str$/ { v = 1 } f && /^    mode: str$/ { m = 1 } f && /^$/ { exit }
  END { exit !(v && m) }' "$repo/api/app/main.py"
check "PLATFORM_MODE kent cluster" grep -q 'mode: Literal\["standalone", "cluster"\]' "$repo/api/app/config.py"
check "image: migraties in /srv/migrations" \
  bash -c 'grep -q "^WORKDIR /srv$" "$1" && grep -q "^COPY api/migrations ./migrations$" "$1"' _ "$repo/Dockerfile"
check "image: geen ENTRYPOINT" bash -c '! grep -q "^ENTRYPOINT" "$1"' _ "$repo/Dockerfile"

# De afspraken met de template media-platform 1.0.0 van ClusterForge (niet in deze repo; alleen
# met CF_TEMPLATE). De nep-platform-ctl.sh en -platform-src.sh hieronder volgen precies dit.
if [ -n "${CF_TEMPLATE:-}" ]; then
  T=$CF_TEMPLATE
  ctl_t=$T/files/platform-ctl.sh.tmpl
  src_t=$T/files/platform-src.sh.tmpl
  check "template: VIP en REF in platform-ctl.sh" \
    bash -c 'grep -qx "VIP='"'"'{{ .params.vip }}'"'"'" "$1" && grep -qx "REF='"'"'{{ .params.platform_ref }}'"'"'" "$1"' _ "$ctl_t"
  check "template: REF en STATE in platform-src.sh" \
    bash -c 'grep -qx "REF='"'"'{{ .params.platform_ref }}'"'"'" "$1" && grep -qx "STATE=/var/lib/clusterforge/platform-image.rev" "$1"' _ "$src_t"
  check "template: paden in platform-ctl.sh" bash -c 'for l in "DATA=/srv/platform-data" "ACTIVE=\$DATA/.cf-platform-active" \
    "ENV=/etc/clusterforge/platform.env" "BACKUPS=/var/backups/cf-platform"; do grep -qxF "$l" "$1" || exit 1; done' _ "$ctl_t"
  check "template: subcommando's unchanged, restart-if-changed, db-backup, status" \
    bash -c 'for c in "  unchanged)" "  restart-if-changed)" "  db-backup) db_backup ;;" "  status) shift; status \"\$@\" ;;"; do
      grep -qxF "$c" "$1" || exit 1; done' _ "$ctl_t"
  check "template: back-up als \$BACKUPS/platform-*.dump met pg_dump -Fc en PGPASSWORD" \
    bash -c 'grep -q "^  out=\$BACKUPS/platform-.*\.dump$" "$1" && grep -q "PGPASSWORD=\$pass pg_dump -Fc " "$1"' _ "$ctl_t"
  check "template: --check in platform-src.sh" grep -q '^if \[ "${1:-}" = --check \]; then$' "$src_t"
  check "template: bestanden op de node" bash -c 'for p in /usr/local/lib/clusterforge/platform-ctl.sh \
    /usr/local/lib/clusterforge/platform-src.sh /etc/clusterforge/platform.env; do grep -q "^ *path: $p$" "$1" || exit 1; done' _ "$T/template.yaml"
  check "template: platform.env (PORT, database, nodes)" bash -c 'grep -qx "PORT=80" "$1" &&
    grep -q "^PLATFORM_DATABASE_URL=postgresql+asyncpg://{{ .params.db_user }}:{{ .params.db_password }}@{{ .params.db_vip }}:5432/{{ .params.db_name }}$" "$1" &&
    grep -q "^PLATFORM_CLUSTER_NODES={{ range" "$1"' _ "$T/files/platform.env.tmpl"
  check "template: cf-platform start alleen met de markering" \
    grep -qx 'ConditionPathExists=/srv/platform-data/.cf-platform-active' "$T/files/cf-platform.service.tmpl"
  check "template: clustermodus en image cf-platform:local" bash -c 'grep -q "PLATFORM_MODE: cluster" "$1" &&
    grep -q "image: cf-platform:local" "$1"' _ "$T/files/platform-cluster.yaml.tmpl"
else
  echo "--    CF_TEMPLATE niet gezet: de afspraken met de template van ClusterForge niet nagekeken"
fi

# --- het nep-gereedschap ---------------------------------------------------------------------

W=$work/wereld          # wat alle nodes delen: de database, de "git-repo", de gedeelde datamap
fakes=$work/nep
common=$work/gewoon
mkdir -p "$fakes" "$common"

for t in awk basename cat chmod cksum cp cut date dirname flock grep head ln ls mkdir mv rm sed \
  sort tail tr touch wc; do
  p=$(command -v "$t") || { echo "nodig voor de test: $t" >&2; exit 2; }
  ln -s "$p" "$common/$t"
done
# setsid: voor de run die Annuleren nadoet (een eigen procesgroep, zoals de agent van ClusterForge).
command -v setsid >/dev/null || { echo "nodig voor de test: setsid" >&2; exit 2; }

cat >"$fakes/docker" <<'EOF'
#!/bin/sh
# Nep-docker: een tag is een bestand <node>/images/<naam> met het image-id erin.
N=$CFT_NODE W=$CFT_WORLD
echo "docker $*" >>"$N/log"
echo "docker $*" >>"$W/../aanroepen.log"
sleutel() { printf '%s' "$1" | tr ':' '_'; }
id_van() {
  case "$1" in
    sha256:*) [ -e "$W/heads/$(sleutel "$1")" ] && echo "$1" ;;
    cf-platform:*) [ -s "$N/images/${1#cf-platform:}" ] && cat "$N/images/${1#cf-platform:}" ;;
    *) return 1 ;;
  esac
}
for a; do laatste=$a; done
case "$1" in
  image)
    case "$2" in
      inspect) id=$(id_van "$laatste") || { echo "Error: No such image: $laatste" >&2; exit 1; }; echo "$id" ;;
      rm) rm -f "$N/images/${3#cf-platform:}"; echo "Untagged: $3" ;;
      *) echo "nep-docker: onbekend: $*" >&2; exit 98 ;;
    esac ;;
  tag)
    [ ! -e "$N/tag_faalt" ] || exit 1
    id=$(id_van "$2") || { echo "Error: No such image: $2" >&2; exit 1; }
    case "$3" in cf-platform:*) echo "$id" >"$N/images/${3#cf-platform:}" ;; *) exit 98 ;; esac ;;
  ps) if [ -e "$N/active" ]; then echo "c0ffee$(basename "$N")"; fi ;;
  inspect) cat "$N/running" ;;
  run)
    case " $* " in
      *" app.cli db-downgrade "*)
        # Platform terug met MET_DATABASE=downgrade: het nieuwe image zet het schema terug; de
        # rijen blijven. Alleen het image dat de huidige revisie van de database kent, kan dat.
        case " $* " in *" --rm --network host --env-file "*) ;; *) echo "nep-docker: downgrade zonder --rm --network host --env-file" >&2; exit 97 ;; esac
        for a; do case "$a" in cf-platform:*) img=$a; break ;; esac; done
        id=$(id_van "$img") || exit 125
        doel=$(echo " $* " | sed -n 's/.* db-downgrade \([^ ]*\) .*/\1/p')
        if [ -e "$W/downgrade_breekt" ]; then echo "nep: downgrade mislukt" >&2; exit 1; fi
        head=$(cat "$W/heads/$(sleutel "$id")"); db=$(cat "$W/db/rev")
        [ "$db" \> "$head" ] && { echo "Revisie $db onbekend" >&2; exit 1; }
        echo "$doel" >"$W/db/rev"
        echo "De database staat nu op revisie $doel."
        exit 0 ;;
      *" --rm --network none "*) ;;
      *) echo "nep-docker: run zonder --rm --network none" >&2; exit 97 ;;
    esac
    for a; do case "$a" in cf-platform:*|sha256:*) img=$a; break ;; esac; done
    id=$(id_van "$img") || exit 125
    cat "$W/heads/$(sleutel "$id")" ;;
  *) echo "nep-docker: onbekend: $*" >&2; exit 98 ;;
esac
EOF

cat >"$fakes/systemctl" <<'EOF'
#!/bin/sh
# Nep-systemctl voor cf-platform en cf-platform-mount. Starten: alleen met de markering
# (ConditionPathExists), dan migreert de app de database naar de head van zijn image; kent het
# image de revisie van de database niet, dan faalt de unit. Bij de start komt de vingerafdruk
# (platform.env en het image) in <node>/hash, zoals platform-ctl.sh up dat in /run doet.
N=$CFT_NODE W=$CFT_WORLD
echo "systemctl $*" >>"$N/log"
echo "systemctl $*" >>"$W/../aanroepen.log"
cmd=$1; shift
while [ "$#" -gt 0 ]; do case "$1" in -*) shift ;; *) break ;; esac; done
unit=${1:-}
sleutel() { printf '%s' "$1" | tr ':' '_'; }
stop() {
  if [ -e "$N/stopt_niet" ]; then echo "stop genegeerd" >>"$N/log"; return 0; fi
  if [ -e "$W/data/.cf-platform-active" ]; then m=aan; else m=opzij; fi
  echo "gestopt; markering $m" >>"$N/log"
  rm -f "$N/active" "$N/running" "$N/hash"
}
start() {
  [ -e "$W/data/.cf-platform-active" ] || return 0
  rm -f "$N/failed"
  id=$(cat "$N/images/local"); k=$(sleutel "$id")
  head=$(cat "$W/heads/$k"); db=$(cat "$W/db/rev")
  if [ "$db" -gt "$head" ]; then
    echo "app $id: database op $db, image kent tot $head" >>"$N/log"; touch "$N/failed"; return 0
  fi
  if [ "$db" -lt "$head" ]; then echo "$head" >"$W/db/rev"; fi
  if [ -e "$W/crash/$k" ]; then touch "$N/failed"; return 0; fi
  echo "$id" >"$N/running"; touch "$N/active"
  { cat "$N/root/etc/platform.env"; echo "$id"; } | cksum >"$N/hash"
}
case "$cmd $unit" in
  "is-active cf-platform-mount") [ ! -e "$N/mount_down" ] ;;
  "is-active cf-platform") [ -e "$N/active" ] ;;
  "is-failed cf-platform") [ -e "$N/failed" ] ;;
  "start cf-platform") [ -e "$N/active" ] || start ;;
  "restart cf-platform") stop; start ;;
  "stop cf-platform") stop ;;
  "reset-failed cf-platform") rm -f "$N/failed" ;;
  *) echo "nep-systemctl: onbekend: $cmd $unit" >&2; exit 98 ;;
esac
EOF

cat >"$fakes/ip" <<'EOF'
#!/bin/sh
[ "$*" = "-o addr show" ] || { echo "nep-ip: onbekend: $*" >&2; exit 98; }
echo "1: lo    inet 127.0.0.1/8 scope host lo\\       valid_lft forever preferred_lft forever"
while read -r a; do
  echo "2: eth0    inet $a brd 10.0.0.255 scope global eth0\\       valid_lft forever preferred_lft forever"
done <"$CFT_NODE/ips"
EOF

cat >"$fakes/curl" <<'EOF'
#!/bin/sh
# Nep-curl: alleen /api/v1/info op poort 80 van een node (of 127.0.0.1), zoals FastAPI het geeft.
N=$CFT_NODE W=$CFT_WORLD
for a; do url=$a; done
echo "curl $*" >>"$N/log"
echo "curl $*" >>"$W/../aanroepen.log"
hp=${url#http://}; hp=${hp%%/*}; host=${hp%:*}; poort=${hp##*:}; pad=/${url#http://*/}
doel=""
if [ "$host" = 127.0.0.1 ]; then doel=$N; else
  for d in "$W"/nodes/*; do if [ "$(cat "$d/adres")" = "$host" ]; then doel=$d; fi; done
fi
if [ -z "$doel" ] || [ "$poort" != 80 ] || [ "$pad" != /api/v1/info ] || [ ! -e "$doel/active" ]; then
  echo "curl: (7) Failed to connect to $host port $poort" >&2; exit 7
fi
id=$(cat "$doel/running")
printf '{"name":"Platform","version":"%s","environment":"production","node":"%s","mode":"cluster"}' \
  "$(cat "$W/versions/$(printf '%s' "$id" | tr ':' '_')")" "$(basename "$doel")"
EOF

cat >"$fakes/mountpoint" <<'EOF'
#!/bin/sh
[ "$1" = -q ] && [ "$2" = "$CFT_WORLD/data" ] && [ ! -e "$CFT_NODE/mount_down" ]
EOF

cat >"$fakes/id" <<'EOF'
#!/bin/sh
[ "$1" = -u ] || exit 98
if [ -e "$CFT_NODE/niet_root" ]; then echo 1000; else echo 0; fi
EOF

cat >"$fakes/hostname" <<'EOF'
#!/bin/sh
basename "$CFT_NODE"
EOF

cat >"$fakes/df" <<'EOF'
#!/bin/sh
echo "Filesystem 1024-blocks Used Available Capacity Mounted on"
echo "/dev/nep 104857600 1000 $(cat "$CFT_NODE/df_vrij_k" 2>/dev/null || echo 52428800) 1% /var/lib/docker"
EOF

printf '#!/bin/sh\nexit 0\n' >"$fakes/sleep"
printf '#!/bin/sh\necho "git $*" >>"$CFT_NODE/log"\n' >"$fakes/git"

cat >"$fakes/pg-gemeen.sh" <<'EOF'
# Gedeeld door nep-pg_dump, -pg_restore en -psql: de verbinding (adres en PGPASSWORD).
verbinding() {
  h=""; p=""; u=""; d=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      -h) h=$2; shift 2 ;; -p) p=$2; shift 2 ;; -U) u=$2; shift 2 ;; -d) d=$2; shift 2 ;;
      *) shift ;;
    esac
  done
  [ "$h $p $u $d" = "$(cat "$W/db/adres")" ] || { echo "${0##*/}: error: connection to server failed" >&2; exit 2; }
  [ "${PGPASSWORD:-}" = "$(cat "$W/db/wachtwoord")" ] || { echo "${0##*/}: error: password authentication failed" >&2; exit 2; }
}
EOF

cat >"$fakes/pg_dump" <<'EOF'
#!/bin/sh
W=$CFT_WORLD
echo "pg_dump $*" >>"$W/pg-args.log"
echo "pg_dump $*" >>"$W/../aanroepen.log"
echo "pg_dump $*" >>"$CFT_NODE/log"
. "$W/../nep/pg-gemeen.sh"
verbinding "$@"
[ ! -e "$W/pg_dump_faalt" ] || { echo "pg_dump: error: query failed" >&2; exit 1; }
uit=""; fc=""
while [ "$#" -gt 0 ]; do case "$1" in -Fc) fc=1; shift ;; -f) uit=$2; shift 2 ;; *) shift ;; esac; done
[ -n "$fc" ] && [ -n "$uit" ] || { echo "pg_dump: nep: -Fc en -f verwacht" >&2; exit 2; }
printf 'PGDMP-NEP\nrev=%s\nrijen=%s\n' "$(cat "$W/db/rev")" "$(cat "$W/db/rijen")" >"$uit"
EOF

cat >"$fakes/pg_restore" <<'EOF'
#!/bin/sh
W=$CFT_WORLD
echo "pg_restore $*" >>"$W/pg-args.log"
echo "pg_restore $*" >>"$W/../aanroepen.log"
for a; do dump=$a; done
grep -qx 'PGDMP-NEP' "$dump" 2>/dev/null || { echo "pg_restore: error: input file does not appear to be a valid archive" >&2; exit 1; }
rev=$(sed -n 's/^rev=//p' "$dump"); rijen=$(sed -n 's/^rijen=//p' "$dump")
case " $* " in
  *" -a -t alembic_version -f - "*)
    printf -- '--\n-- PostgreSQL database dump\n--\n\nSET statement_timeout = 0;\n\nCOPY public.alembic_version (version_num) FROM stdin;\n%s\n\\.\n\n' "$rev" ;;
  *" --no-owner --no-privileges --single-transaction -f - "*)
    printf -- '--\n-- PostgreSQL database dump\n--\n\nSET statement_timeout = 0;\nBEGIN;\n\nCREATE TABLE public.alembic_version (version_num character varying(32) NOT NULL);\n'
    if [ -e "$W/herstel_breekt" ]; then echo "pg_restore: error: could not read from input file: end of file" >&2; exit 1; fi
    printf -- '-- NEP-HERSTEL rev=%s rijen=%s\nCOMMIT;\n' "$rev" "$rijen" ;;
  *) echo "pg_restore: nep: onbekende aanroep: $*" >&2; exit 2 ;;
esac
EOF

cat >"$fakes/psql" <<'EOF'
#!/bin/sh
W=$CFT_WORLD
echo "psql $*" >>"$W/pg-args.log"
echo "psql $*" >>"$W/../aanroepen.log"
. "$W/../nep/pg-gemeen.sh"
verbinding "$@"
case "$*" in
  *"-c SELECT version_num FROM alembic_version"*)
    [ ! -e "$W/select_faalt" ] || { echo "psql: error: connection to server failed: timeout expired" >&2; exit 2; }
    cat "$W/db/rev"; exit 0 ;;
esac
case " $* " in *" -v ON_ERROR_STOP=1 "*) ;; *) echo "psql: nep: zonder ON_ERROR_STOP" >&2; exit 2 ;; esac
cat >"$W/db/laatste.sql"
# select_faalt_na: na deze poging is de database niet meer te lezen (verbinding weg).
[ ! -e "$W/select_faalt_na" ] || touch "$W/select_faalt"
# Annuleren of de tijdslimiet van ClusterForge: SIGTERM naar de hele procesgroep van het commando.
if [ -e "$W/psql_term" ]; then kill -TERM 0; exit 143; fi
if [ -e "$W/psql_fout" ]; then echo "psql:<stdin>:9: ERROR:  must be owner of table nep" >&2; exit 3; fi
# Geen COMMIT: de verbinding sluit midden in de transactie en de server rolt alles terug.
grep -qx 'COMMIT;' "$W/db/laatste.sql" || exit 0
sed -n 's/^-- NEP-HERSTEL rev=\([^ ]*\) rijen=.*$/\1/p' "$W/db/laatste.sql" >"$W/db/rev"
sed -n 's/^-- NEP-HERSTEL rev=[^ ]* rijen=\(.*\)$/\1/p' "$W/db/laatste.sql" >"$W/db/rijen"
# psql_term_na: Annuleren vlak ná de COMMIT.
if [ -e "$W/psql_term_na" ]; then kill -TERM 0; exit 143; fi
EOF
chmod 0755 "$fakes"/*

# --- het cluster -----------------------------------------------------------------------------

VIP=10.0.0.100
DBVIP=10.0.0.200
WACHTWOORD=Nep-Geheim-7f3a9c2e1d
NODES="n1 n2 n3"
adres_van() { case "$1" in n1) echo 10.0.0.11 ;; n2) echo 10.0.0.12 ;; n3) echo 10.0.0.13 ;; esac; }

# De versies in de nep-"git-repo": ref, commit, image-id, Alembic-head, versie van de app.
# v082b is een fix zonder migratie; v083 migreert en start daarna niet.
rev_van_ref() { awk -v r="$1" '$1 == r { print $2 }' "$W/repo" | head -n 1; }
id_van_ref() { awk -v r="$1" '$1 == r { print $3 }' "$W/repo" | head -n 1; }
versie() {
  local ref=$1 rev=$2 id=$3 head=$4 app=$5
  printf '%s %s %s\n%s %s %s\n' "$ref" "$rev" "$id" "$rev" "$rev" "$id" >>"$W/repo"
  echo "$head" >"$W/heads/${id/:/_}"
  echo "$app" >"$W/versions/${id/:/_}"
}

# Herschrijft de paden bovenaan een script naar de mappen van node $3. Elke regel moet precies
# één keer vervangen zijn, en er mag geen echt pad van de node meer in staan.
herschrijf() {
  local bron=$1 doel=$2 r=$3/root var
  sed -e "s#^LIB=/usr/local/lib/clusterforge\$#LIB=$r/lib#" \
    -e "s#^DATA=/srv/platform-data\$#DATA=$W/data#" \
    -e "s#^ENV=/etc/clusterforge/platform.env\$#ENV=$r/etc/platform.env#" \
    -e "s#^STATE=/var/lib/clusterforge/platform-image.rev\$#STATE=$r/state/platform-image.rev#" \
    -e "s#^BACKUPS=/var/backups/cf-platform\$#BACKUPS=$r/backups#" \
    -e "s#^EIGEN=/var/lib/cf-platform/bijwerken\$#EIGEN=$r/eigen#" \
    -e "s#^LOCK=/run/cf-platform-bijwerken.lock\$#LOCK=$r/run/lock#" \
    -e "s#^WACHT=900\$#WACHT=30#" "$bron" >"$doel"
  for var in LIB DATA ENV BACKUPS EIGEN LOCK; do
    [ "$(grep -c "^$var=$work/" "$doel")" = 1 ] || { echo "herschrijf: $var in ${bron##*/}" >&2; return 1; }
  done
  grep -q "^WACHT=30$" "$doel" || return 1
  if grep -q '^STATE=' "$bron"; then grep -q "^STATE=$work/" "$doel" || return 1; fi
  ! grep -v '^ *#' "$doel" | grep -vF "=$work/" |
    grep -E '/usr/local/lib|/srv/platform-data|/etc/clusterforge|/var/lib/clusterforge|/var/backups|/var/lib/cf-platform|/run/' >&2
}

maak_node() {
  local naam=$1 N=$W/nodes/$1 r
  r=$N/root
  mkdir -p "$N/bin" "$N/images" "$N/cmd" "$r/lib" "$r/etc" "$r/state" "$r/backups" "$r/eigen" "$r/run"
  adres_van "$naam" >"$N/adres"
  echo "$(adres_van "$naam")/24" >"$N/ips"
  : >"$N/log"
  ln -s "$common"/* "$N/bin/"
  for t in docker systemctl ip curl mountpoint id hostname df sleep git pg_dump pg_restore psql; do
    ln -s "$fakes/$t" "$N/bin/$t"
  done
  cat >"$r/etc/platform.env" <<ENV
NODE_NAME=$naam
PORT=80
PLATFORM_DATABASE_URL=postgresql+asyncpg://platform:$WACHTWOORD@10.0.0.200:5432/platform
PLATFORM_CLUSTER_NODES=n1=10.0.0.11,n2=10.0.0.12,n3=10.0.0.13
ANALYSE=uit
ENV
  # De nep-platform-src.sh: dezelfde REF-regel en hetzelfde --check als de template (code op
  # de versie en cf-platform:local bestaat; niet of dat image bij die code hoort).
  cat >"$r/lib/platform-src.sh" <<SRC
#!/bin/sh
# Beheerd door ClusterForge (cluster test). Nep van platform-src.sh.
set -eu
REPO='https://example.invalid/platform.git'
REF='v081'
STATE=$r/state/platform-image.rev
SRC
  cat >>"$r/lib/platform-src.sh" <<'SRC'
W=$CFT_WORLD N=$CFT_NODE
rev=$(awk -v r="$REF" '$1 == r { print $2 }' "$W/repo" | head -n 1)
id=$(awk -v r="$REF" '$1 == r { print $3 }' "$W/repo" | head -n 1)
[ -n "$rev" ] || { echo "fatal: couldn't find remote ref $REF" >&2; exit 1; }
if [ "${1:-}" = --check ]; then
  [ "$(cat "$STATE" 2>/dev/null)" = "$rev" ] && docker image inspect cf-platform:local >/dev/null 2>&1
  exit $?
fi
echo "nep-build van $REF ($rev)"
echo "build $REF" >>"$N/log"
[ ! -e "$N/build_faalt" ] || { echo "ERROR: failed to solve: process did not complete successfully" >&2; exit 1; }
echo "$id" >"$N/images/local"
echo "$rev" >"$STATE"
echo "platform $REF op $rev gebouwd"
SRC
  # De nep-platform-ctl.sh: dezelfde VIP- en REF-regels als de template, en unchanged,
  # restart-if-changed, db-backup en status zoals daar (db_backup bijna letterlijk). Zoals
  # fingerprint() in de template telt platform.env mee, niet alleen het image.
  cat >"$r/lib/platform-ctl.sh" <<CTL
#!/bin/sh
# Beheerd door ClusterForge (cluster test). Nep van platform-ctl.sh.
set -u
DATA=$W/data
ACTIVE=\$DATA/.cf-platform-active
ENV=$r/etc/platform.env
VIP='$VIP'
CLUSTER='test'
REF='v081'
BACKUPS=$r/backups
STATE=$r/state/platform-image.rev
CTL
  cat >>"$r/lib/platform-ctl.sh" <<'CTL'
N=$CFT_NODE
env_get() { sed -n "s/^$1=//p" "$ENV" | tail -n 1; }
say() { echo "== $*"; }
fail() { echo "FOUT: $*" >&2; exit 1; }
zelfde() {
  [ "$(cat "$N/hash" 2>/dev/null)" = "$({ cat "$ENV"; docker image inspect -f '{{.Id}}' cf-platform:local 2>/dev/null; } | cksum)" ]
}
db_backup() {
  url=$(env_get PLATFORM_DATABASE_URL)
  rest=${url#*://}; cred=${rest%%@*}; hostdb=${rest#*@}
  user=${cred%%:*}; pass=${cred#*:}
  host=${hostdb%%/*}; db=${hostdb#*/}; port=${host##*:}; host=${host%:*}
  [ "$port" = "$host" ] && port=5432
  command -v pg_dump >/dev/null || fail "pg_dump ontbreekt"
  mkdir -p "$BACKUPS" && chmod 0700 "$BACKUPS"
  rev=$(cut -c1-12 "$STATE" 2>/dev/null)
  # Een volgnummer erbij: twee back-ups in dezelfde seconde (hier gewoon) krijgen anders één naam.
  n=$(($(cat "$N/dumps" 2>/dev/null || echo 0) + 1)); echo "$n" >"$N/dumps"
  out=$BACKUPS/platform-$(date +%Y%m%d-%H%M%S)-$n-${rev:-onbekend}.dump
  say "Back-up van de database $db naar $out"
  PGPASSWORD=$pass pg_dump -Fc -h "$host" -p "$port" -U "$user" -d "$db" -f "$out.deel" &&
    [ -s "$out.deel" ] && mv "$out.deel" "$out" || { rm -f "$out.deel"; fail "back-up mislukt"; }
  ls -lh "$out"
  # shellcheck disable=SC2012
  ls -1t "$BACKUPS"/platform-*.dump 2>/dev/null | tail -n +6 | while read -r f; do rm -f "$f"; done
}
case "${1:-}" in
  unchanged)
    systemctl is-active --quiet cf-platform || exit 0
    zelfde
    ;;
  restart-if-changed)
    systemctl is-active --quiet cf-platform || exit 0
    zelfde && exit 0
    db_backup
    systemctl restart cf-platform
    ;;
  db-backup) db_backup ;;
  status) exit 0 ;;
  *) echo "gebruik: $0 unchanged|restart-if-changed|db-backup|status" >&2; exit 2 ;;
esac
CTL
  chmod 0755 "$r/lib/platform-src.sh" "$r/lib/platform-ctl.sh"
  herschrijf "$bijwerken_sh" "$N/cmd/platform-bijwerken.sh" "$N"
  herschrijf "$terug_sh" "$N/cmd/platform-terug.sh" "$N"
}

# Een cluster met 0.8.1 (v081) dat al is overgestapt: n3 heeft het VIP en draait het platform, en
# heeft ook het VIP van de database.
nieuw_cluster() {
  rm -rf "$W"
  mkdir -p "$W/nodes" "$W/data" "$W/db" "$W/heads" "$W/versions" "$W/crash"
  : >"$W/repo"
  : >"$W/pg-args.log"
  versie v081 "$(printf 'a081%.0s' 1 2 3 4 5 6 7 8 9 10)" "sha256:$(printf 'a%.0s' $(seq 64))" 0012 0.8.1
  versie v082 "$(printf 'b082%.0s' 1 2 3 4 5 6 7 8 9 10)" "sha256:$(printf 'b%.0s' $(seq 64))" 0014 0.8.2
  versie v082b "$(printf 'c082%.0s' 1 2 3 4 5 6 7 8 9 10)" "sha256:$(printf 'c%.0s' $(seq 64))" 0014 0.8.2+fix
  versie v083 "$(printf 'd083%.0s' 1 2 3 4 5 6 7 8 9 10)" "sha256:$(printf 'd%.0s' $(seq 64))" 0015 0.8.3
  touch "$W/crash/$(id_van_ref v083 | tr ':' '_')"
  echo "10.0.0.200 5432 platform platform" >"$W/db/adres"
  echo "$WACHTWOORD" >"$W/db/wachtwoord"
  echo 0012 >"$W/db/rev"
  echo voor >"$W/db/rijen"
  local n
  for n in $NODES; do
    maak_node "$n"
    id_van_ref v081 >"$W/nodes/$n/images/local"
    rev_van_ref v081 >"$W/nodes/$n/root/state/platform-image.rev"
  done
  echo "$VIP/32" >>"$W/nodes/n3/ips"
  echo "$DBVIP/32" >>"$W/nodes/n3/ips"
  date -Iseconds >"$W/data/.cf-platform-active"
  id_van_ref v081 >"$W/nodes/n3/running"
  touch "$W/nodes/n3/active"
  gestart n3
}
# De vingerafdruk van wat er op node $1 draait, zoals de nep-systemctl die bij de start schrijft.
gestart() { { cat "$W/nodes/$1/root/etc/platform.env"; cat "$W/nodes/$1/running"; } | cksum >"$W/nodes/$1/hash"; }

# zet_vips <node met het VIP van het platform> <node met het VIP van de database>. Het platform
# draait daarna op de eerste (keepalived start het daar).
zet_vips() {
  local n
  for n in $NODES; do
    grep -vxF -e "$VIP/32" -e "$DBVIP/32" "$W/nodes/$n/ips" >"$W/nodes/$n/ips.nieuw" || true
    mv "$W/nodes/$n/ips.nieuw" "$W/nodes/$n/ips"
    if [ -e "$W/nodes/$n/active" ] && [ "$n" != "$1" ]; then
      mv "$W/nodes/$n/running" "$W/nodes/$1/running"
      rm -f "$W/nodes/$n/active" "$W/nodes/$n/hash"
      touch "$W/nodes/$1/active"
      gestart "$1"
    fi
  done
  echo "$VIP/32" >>"$W/nodes/$1/ips"
  echo "$DBVIP/32" >>"$W/nodes/$2/ips"
}

# Wijzigen, Versie, Toepassen: verandert alleen de REF-regel van de twee scripts (cf-update §1.1).
zet_versie() {
  local n
  for n in $NODES; do
    sed -i "s#^REF='.*'\$#REF='$1'#" "$W/nodes/$n/root/lib/platform-src.sh" "$W/nodes/$n/root/lib/platform-ctl.sh"
  done
}

# Een tag op een node (leeg als die er niet is), en wat er draait.
tag() { cat "$W/nodes/$1/images/$2" 2>/dev/null || true; }
draait() { if [ -e "$W/nodes/$1/active" ]; then cat "$W/nodes/$1/running"; fi; }
db_rev() { cat "$W/db/rev"; }
db_rijen() { cat "$W/db/rijen"; }
eigen() { echo "$W/nodes/$1/root/eigen"; }

# Het commando zoals het in ClusterForge staat, eventueel met MET_DATABASE=ja of DUMP=<pad>
# (Jonas past dan de tekst van het opgeslagen commando aan).
variant() { # variant <node> <script> [MET_DATABASE=ja] [DUMP=pad]
  local N=$W/nodes/$1 s=$2 v
  shift 2
  cp "$N/cmd/$s" "$N/cmd/run-$s"
  for v in "$@"; do
    case "$v" in
      MET_DATABASE=*)
        grep -qx 'MET_DATABASE=nee' "$N/cmd/run-$s" || return 1
        sed -i "s#^MET_DATABASE=nee\$#$v#" "$N/cmd/run-$s" ;;
      DUMP=*)
        grep -qx 'DUMP=' "$N/cmd/run-$s" || return 1
        sed -i "s#^DUMP=\$#$v#" "$N/cmd/run-$s" ;;
      *) return 1 ;;
    esac
  done
  echo "$N/cmd/run-$s"
}

OUT=$work/uitvoer
ALLES=$work/alle-uitvoer
mkdir -p "$OUT"
: >"$ALLES"
volg=0
# draai <node> <script> [variant...]: één node; de uitvoer staat daarna in $LOG.
draai() {
  local n=$1 s=$2 rc=0 script
  shift 2
  script=$(variant "$n" "$s" "$@") || { echo "test: variant $s $* lukt niet" >&2; exit 2; }
  volg=$((volg + 1))
  LOG=$OUT/$volg-$n-${s%.sh}.log
  local groep=()
  [ -z "${EIGEN_GROEP:-}" ] || groep=(setsid)
  "${groep[@]}" env -i CFT_NODE="$W/nodes/$n" CFT_WORLD="$W" PATH="$W/nodes/$n/bin" "$SH" "$script" >"$LOG" 2>&1 || rc=$?
  { echo "### $n $s $* (rc $rc)"; cat "$LOG"; } >>"$ALLES"
  return "$rc"
}
# draai_cluster <script> [variant...]: zoals ClusterForge, node voor node, de nodes met een VIP
# (van het platform of van de database) als laatste, in hun gewone volgorde, en stoppen na de
# eerste node die faalt. GEDRAAID: de nodes met hun rc.
draai_cluster() {
  local n rc volgorde="" zonder="" met=""
  for n in $NODES; do
    if grep -qxF -e "$VIP/32" -e "$DBVIP/32" "$W/nodes/$n/ips"; then met="$met $n"; else zonder="$zonder $n"; fi
  done
  volgorde="$zonder$met"
  GEDRAAID=""
  for n in $volgorde; do
    rc=0
    draai "$n" "$@" || rc=$?
    GEDRAAID="$GEDRAAID $n:$rc"
    LOG_VAN[$n]=$LOG
    [ "$rc" = 0 ] || return "$rc"
  done
}
declare -A LOG_VAN
# Een run die moet lukken om een scenario klaar te zetten.
moet() {
  draai_cluster "$@" || { echo "test: voorbereiding $* mislukt:$GEDRAAID" >&2; cat "$LOG" >&2; exit 2; }
}

A=$(printf 'sha256:%064d' 0 | tr 0 a)
B=$(printf 'sha256:%064d' 0 | tr 0 b)
C=$(printf 'sha256:%064d' 0 | tr 0 c)
REV_A=$(printf 'a081%.0s' 1 2 3 4 5 6 7 8 9 10)
REV_B=$(printf 'b082%.0s' 1 2 3 4 5 6 7 8 9 10)

# Niets veranderd na een weigering van Platform bijwerken op een cluster met 0.8.1: elke node
# heeft het image van 0.8.1, geen vorige, n3 draait 0.8.1, de database staat op 0012.
niets_veranderd() {
  local n
  for n in $NODES; do
    is "$(tag "$n" local)" "$A" || return 1
    is "$(tag "$n" vorige)" "" || return 1
    is "$(tag "$n" basis)" "" || return 1
  done
  is "$(draait n3)" "$A" && is "$(db_rev)" 0012 && [ -e "$W/data/.cf-platform-active" ] &&
    [ -z "$(ls "$W/nodes/n3/root/backups")" ]
}

# --- Platform bijwerken: weigeringen -----------------------------------------------------------

nieuw_cluster
zet_versie v082
touch "$W/nodes/n1/niet_root"
rc=0; draai n1 platform-bijwerken.sh || rc=$?
check "bijwerken: niet als root geweigerd" bash -c '[ "$1" != 0 ]' _ "$rc"
check "bijwerken: niet als root, de melding" bevat "$LOG" "draai dit als root"
check "bijwerken: niet als root, niets veranderd" niets_veranderd

nieuw_cluster
zet_versie v082
rm "$W/nodes/n1/root/lib/platform-ctl.sh"
rc=0; draai n1 platform-bijwerken.sh || rc=$?
check "bijwerken: zonder platform-ctl.sh geweigerd" bash -c '[ "$1" != 0 ] && grep -qF "ontbreekt: is de template media-platform op deze node uitgerold?" "$2"' _ "$rc" "$LOG"

nieuw_cluster
zet_versie v082
rm "$W/nodes/n1/bin/git"
rc=0; draai n1 platform-bijwerken.sh || rc=$?
check "bijwerken: zonder git geweigerd" bash -c '[ "$1" != 0 ] && grep -qF "git ontbreekt" "$2"' _ "$rc" "$LOG"
check "bijwerken: zonder git, niets veranderd" niets_veranderd

nieuw_cluster
zet_versie v082
exec {slot}>"$W/nodes/n1/root/run/lock"
flock -n "$slot"
rc=0; draai n1 platform-bijwerken.sh || rc=$?
exec {slot}>&-
check "bijwerken: slot bezet geweigerd" bash -c '[ "$1" != 0 ] && grep -qF "loopt al op deze node" "$2"' _ "$rc" "$LOG"
check "bijwerken: slot bezet, niets veranderd" niets_veranderd

nieuw_cluster
zet_versie v082
mv "$W/data/.cf-platform-active" "$W/data/.cf-platform-active.terug"
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken: halve Platform terug geweigerd op de eerste node" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:1" ] && grep -qF "een eerdere Platform terug is niet afgemaakt" "$3"' _ "$rc" "$GEDRAAID" "$LOG"

nieuw_cluster
zet_versie v082
touch "$W/nodes/n1/mount_down"
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken: mount weg geweigerd, de run stopt op n1" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:1" ] && grep -qF "is niet gemount" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
check "bijwerken: mount weg, niets veranderd" niets_veranderd

nieuw_cluster
zet_versie v082
id_van_ref v081 >"$W/nodes/n1/running"
touch "$W/nodes/n1/active"
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken: platform zonder VIP geweigerd" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:1" ] && grep -qF "cf-platform draait op deze node zonder het VIP" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
rm "$W/nodes/n1/active" "$W/nodes/n1/running"
check "bijwerken: platform zonder VIP, niets veranderd" niets_veranderd

nieuw_cluster
zet_versie v082
id_van_ref v081 >"$W/nodes/n2/running"
touch "$W/nodes/n2/active"
rc=0; draai n1 platform-bijwerken.sh || rc=$?
check "bijwerken: twee platformen (gezien vanaf n1) geweigerd" \
  bash -c '[ "$1" != 0 ] && grep -qF "het platform draait op 2 nodes tegelijk (n2 n3)" "$2"' _ "$rc" "$LOG"
mv "$W/nodes/n2/running" "$W/nodes/n1/running"
mv "$W/nodes/n2/active" "$W/nodes/n1/active"
rc=0; draai n3 platform-bijwerken.sh || rc=$?
check "bijwerken: twee platformen (gezien vanaf de VIP-houder) geweigerd" \
  bash -c '[ "$1" != 0 ] && grep -qF "het platform draait op 2 nodes tegelijk (n3 n1)" "$2"' _ "$rc" "$LOG"
rm "$W/nodes/n1/active" "$W/nodes/n1/running"
check "bijwerken: twee platformen, niets veranderd" niets_veranderd

nieuw_cluster
zet_versie v082
rm "$W/nodes/n3/bin/pg_dump"
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken: pg_dump ontbreekt op de VIP-houder geweigerd" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:0 n2:0 n3:1" ] && grep -qF "pg_dump ontbreekt; zonder back-up herstart platform-ctl.sh niet" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
check "bijwerken: pg_dump ontbreekt, de VIP-houder bouwt niet en draait 0.8.1" \
  bash -c '[ "$1" = "$2" ] && [ -z "$3" ] && [ "$4" = "$2" ] && [ "$5" = 0012 ]' _ "$(tag n3 local)" "$A" "$(tag n3 vorige)" "$(draait n3)" "$(db_rev)"
check "bijwerken: pg_dump ontbreekt, de nodes zonder VIP hebben alleen gebouwd" \
  bash -c '[ "$1" = "$3" ] && [ "$2" = "$4" ] && ! grep -q "systemctl \(re\)\?start" "$5"' _ \
  "$(tag n1 local)" "$(tag n1 vorige)" "$B" "$A" "$W/nodes/n1/log"

nieuw_cluster
zet_versie v082
touch "$W/nodes/n1/build_faalt"
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken: mislukte build stopt de run" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:1" ] && grep -qF "bouwen mislukt; cf-platform:local is nog het vorige image" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
check "bijwerken: mislukte build, niets veranderd (ook geen cf-platform:basis)" niets_veranderd

nieuw_cluster
zet_versie v082
touch "$W/pg_dump_faalt"
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken: mislukte back-up op de VIP-houder, niets gebouwd en geen herstart" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:0 n2:0 n3:1" ] && grep -qF "back-up van de database mislukt; er is niets gebouwd" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
check "bijwerken: mislukte back-up, 0.8.1 draait door op een ongewijzigde database" \
  bash -c '[ "$1" = "$2" ] && [ "$3" = 0012 ] && [ -z "$(ls "$4")" ]' _ "$(draait n3)" "$A" "$(db_rev)" "$W/nodes/n3/root/backups"
check "bijwerken: mislukte back-up, de VIP-houder heeft het nieuwe image niet" \
  is "$(tag n3 local) $(tag n3 vorige) $(tag n3 basis)" "$A  "
rm "$W/pg_dump_faalt"

# --- Platform bijwerken: gelukt ---------------------------------------------------------------

nieuw_cluster
rm "$W/nodes/n2/bin/pg_dump" "$W/nodes/n2/bin/pg_restore" "$W/nodes/n2/bin/psql"
zet_versie v082
for n in $NODES; do chmod 0755 "$(eigen "$n")"; done
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken: het hele cluster, n1 en n2 eerst, n3 (VIP) als laatste" is "$rc $GEDRAAID" "0  n1:0 n2:0 n3:0"
# In de eigen map staan harde links naar de dumps (pg_dump maakt ze 0644): alleen root mag erin.
check "bijwerken: de eigen map is 0700 op elke node" \
  is "$(stat -c %a "$(eigen n1)") $(stat -c %a "$(eigen n2)") $(stat -c %a "$(eigen n3)")" "700 700 700"
for n in n1 n2 n3; do
  check "bijwerken: $n heeft 0.8.2 als cf-platform:local en 0.8.1 als cf-platform:vorige" \
    bash -c '[ "$1" = "$3" ] && [ "$2" = "$4" ] && [ -z "$5" ] && [ "$(cat "$6/vorige.rev")" = "$7" ]' _ \
    "$(tag $n local)" "$(tag $n vorige)" "$B" "$A" "$(tag $n basis)" "$(eigen $n)" "$REV_A"
done
check "bijwerken: n1 bouwt alleen" bevat "${LOG_VAN[n1]}" "Geen VIP: alleen gebouwd"
check "bijwerken: n1 ziet het platform op n3" bevat "${LOG_VAN[n1]}" "het platform draait nu op: n3"
check "bijwerken: n2 zonder pg_dump krijgt een waarschuwing" bevat "${LOG_VAN[n2]}" "let op: pg_dump ontbreekt op deze node"
check "bijwerken: geen herstart op n1 en n2" \
  bash -c '! grep -q "^systemctl \(re\)\?start" "$1" "$2" && [ ! -e "$3" ]' _ "$W/nodes/n1/log" "$W/nodes/n2/log" "$W/nodes/n1/active"
check "bijwerken: n3 draait 0.8.2 en de database is gemigreerd" is "$(draait n3) $(db_rev)" "$B 0014"
check "bijwerken: de app antwoordt met 0.8.2" bevat "${LOG_VAN[n3]}" "== Klaar: app 0.8.2, code ${REV_B:0:12} (v082), vorige ${REV_A:0:12}"
check "bijwerken: voor-update.dump is de stand van vóór de migratie" \
  bash -c 'grep -qx "rev=0012" "$1/voor-update.dump" && grep -qx "dump=platform-.*\.dump van=$2 naar=$3" "$1/voor-update.info"' _ \
  "$(eigen n3)" "$REV_A" "$REV_B"
check "bijwerken: voor-update.dump is een harde link naar de back-up van platform-ctl.sh" \
  bash -c '[ "$(stat -c %h "$1/voor-update.dump")" = 2 ]' _ "$(eigen n3)"
check "bijwerken: twee back-ups, alleen op n3 (vóór het bouwen en vlak voor de herstart)" \
  bash -c '[ "$(ls "$1" | wc -l)" = 2 ] && [ -z "$(ls "$2")" ] && [ -z "$(ls "$3")" ]' _ \
  "$W/nodes/n3/root/backups" "$W/nodes/n1/root/backups" "$W/nodes/n2/root/backups"
check "bijwerken: de back-up op n3 komt vóór het bouwen (het nieuwe image kan daarna starten)" \
  awk '/^pg_dump /{ if (!d) d = NR } /^build /{ if (!b) b = NR } END { exit !(d && b && d < b) }' "$W/nodes/n3/log"
# shellcheck disable=SC2012  # zoals platform-bijwerken.sh: de nieuwste back-up
check "bijwerken: voor-update.dump is de laatste back-up, vlak voor de herstart" \
  grep -qx "dump=$(basename "$(ls -1t "$W/nodes/n3/root/backups"/platform-*.dump | head -n 1)") van=.*" "$(eigen n3)/voor-update.info"

# Tweede keer: niets te bouwen, niets te herstarten, geen nieuwe back-up.
for n in $NODES; do : >"$W/nodes/$n/log"; done
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken: tweede keer gelukt" is "$rc $GEDRAAID" "0  n1:0 n2:0 n3:0"
check "bijwerken: tweede keer niets gebouwd" \
  bash -c '! grep -q "^build " "$1" "$2" "$3"' _ "$W/nodes/n1/log" "$W/nodes/n2/log" "$W/nodes/n3/log"
check "bijwerken: tweede keer geen herstart en geen back-up" \
  bash -c '! grep -q "^systemctl \(re\)\?start" "$1" && [ "$(ls "$2" | wc -l)" = 2 ] && grep -qF "niets te herstarten" "$3"' _ \
  "$W/nodes/n3/log" "$W/nodes/n3/root/backups" "${LOG_VAN[n3]}"
check "bijwerken: tweede keer blijft 0.8.1 cf-platform:vorige" is "$(tag n1 vorige) $(tag n3 vorige)" "$A $A"

# De vijf-nieuwste-regel van platform-ctl.sh wist voor-update.dump niet.
for _ in 1 2 3 4 5 6; do
  env -i CFT_NODE="$W/nodes/n3" CFT_WORLD="$W" PATH="$W/nodes/n3/bin" "$SH" "$W/nodes/n3/root/lib/platform-ctl.sh" db-backup >/dev/null
done
check "bijwerken: voor-update.dump overleeft het opruimen van oude back-ups" \
  bash -c '[ "$(ls "$1" | wc -l)" = 5 ] && grep -qx "rev=0012" "$2/voor-update.dump"' _ "$W/nodes/n3/root/backups" "$(eigen n3)"

# Vóór de overstap (geen markering): alleen bouwen, ook op de node met het VIP.
nieuw_cluster
zet_versie v082
rm "$W/data/.cf-platform-active" "$W/nodes/n3/active" "$W/nodes/n3/running"
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken: vóór de overstap alleen bouwen" \
  bash -c '[ "$1" = 0 ] && grep -qF "de overstap is nog niet gedaan" "$2" && grep -qF "niets te herstarten" "$2"' _ "$rc" "$LOG"
check "bijwerken: vóór de overstap geen back-up en niets gestart" \
  bash -c '[ "$1" = "$2" ] && [ -z "$(ls "$3")" ] && [ ! -e "$4" ]' _ "$(tag n3 local)" "$B" "$W/nodes/n3/root/backups" "$W/nodes/n3/active"

# Na de overstap, maar cf-platform draait niet op de VIP-houder (gestopt of gecrasht): toch eerst
# een back-up, want keepalived (platform-ctl.sh check) start daarna het nieuwe image en de app
# migreert dan bij de start.
nieuw_cluster
zet_versie v082
rm "$W/nodes/n3/active" "$W/nodes/n3/running" "$W/nodes/n3/hash"
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken: cf-platform gestopt op de VIP-houder, gebouwd en niet gestart" \
  bash -c '[ "$1" = 0 ] && grep -qF "niets te herstarten" "$2" && [ "$3" = "$4" ] && [ ! -e "$5" ]' _ \
  "$rc" "$LOG" "$(tag n3 local)" "$B" "$W/nodes/n3/active"
check "bijwerken: cf-platform gestopt, toch een back-up van vóór het bouwen als voor-update.dump" \
  bash -c '[ "$(ls "$1" | wc -l)" = 1 ] && grep -qx "rev=0012" "$2/voor-update.dump" &&
    grep -qx "dump=platform-.*\.dump van=$3 naar=$4" "$2/voor-update.info"' _ "$W/nodes/n3/root/backups" "$(eigen n3)" "$REV_A" "$REV_B"
env -i CFT_NODE="$W/nodes/n3" CFT_WORLD="$W" PATH="$W/nodes/n3/bin" "$W/nodes/n3/bin/systemctl" start cf-platform
check "bijwerken: cf-platform gestopt, keepalived start daarna 0.8.2 en de database migreert" is "$(draait n3) $(db_rev)" "$B 0014"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
check "bijwerken: cf-platform gestopt, Platform terug kan daarna terug naar 0.8.1" is "$rc $(draait n3) $(db_rev)" "0 $A 0012"

# Een nieuwe versie die migreert en daarna niet start: Platform bijwerken zegt het.
nieuw_cluster
zet_versie v082
moet platform-bijwerken.sh
zet_versie v083
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken: de app antwoordt niet na de herstart" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:0 n2:0 n3:1" ] && grep -qF "de app antwoordt niet" "$3" && grep -qF "Terug: het commando Platform terug" "$3"' _ \
  "$rc" "$GEDRAAID" "$LOG"
check "bijwerken: v083 heeft de database al gemigreerd" is "$(db_rev) $(draait n3)" "0015 "

# Platform terug, met de database (de migratie van v083 kent 0.8.2 niet).
rc=0; draai_cluster platform-terug.sh || rc=$?
check "terug: zonder MET_DATABASE weigert de eerste node" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:1" ] && grep -qF "NIETS GEDAAN: het vorige image start niet op deze database" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug: na een update die niet start, met de database, gelukt" is "$rc $GEDRAAID" "0  n1:0 n2:0 n3:0"
check "terug: 0.8.2 draait weer op 0014" is "$(draait n3) $(db_rev)" "$B 0014"
check "terug: de app antwoordt met 0.8.2" bevat "$LOG" "== Klaar: app 0.8.2"

# --- Platform terug: weigeringen ---------------------------------------------------------------

# Een cluster na een gelukte update naar 0.8.2, met nieuwe gegevens erna.
na_update() {
  nieuw_cluster
  zet_versie v082
  moet platform-bijwerken.sh
  echo "voor+na" >"$W/db/rijen"
  for n in $NODES; do : >"$W/nodes/$n/log"; done
  : >"$W/pg-args.log"
}
# Niets veranderd na een weigering van Platform terug: alle nodes op 0.8.2, n3 draait het, de
# database en de markering zijn zoals na de update.
terug_niets_veranderd() {
  local n
  for n in $NODES; do
    is "$(tag "$n" local)" "$B" || return 1
    is "$(tag "$n" vorige)" "$A" || return 1
    is "$(tag "$n" teruggezet)" "" || return 1
  done
  is "$(draait n3)" "$B" && is "$(db_rev) $(db_rijen)" "0014 voor+na" && [ -e "$W/data/.cf-platform-active" ] &&
    [ ! -e "$W/data/.cf-platform-active.terug" ] && ! grep -q '^pg_restore --no-owner' "$W/pg-args.log"
}

# MET_DATABASE=downgrade (0.8.2): het nieuwe image zet het schema terug, de rijen blijven
na_update
rc=0; draai_cluster platform-terug.sh || rc=$?
check "terug: zonder MET_DATABASE noemt de weigering ook downgrade" bevat "$LOG" "MET_DATABASE=downgrade"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=downgrade || rc=$?
check "terug downgrade: gelukt in één run" is "$rc $GEDRAAID" "0  n1:0 n2:0 n3:0"
check "terug downgrade: 0.8.1 draait op 0012 en de rijen van na de update zijn er nog" \
  is "$(draait n3) $(db_rev) $(db_rijen)" "$A 0012 voor+na"
check "terug downgrade: geen pg_restore" bash -c '! grep -q "^pg_restore --no-owner" "$1"' _ "$W/pg-args.log"
check "terug downgrade: eerst een back-up van de database zoals hij was" bevat "$LOG" "Eerst een back-up van de database"
check "terug downgrade: de markering staat terug" bash -c '[ -e "$1" ] && [ ! -e "$2" ]' _ \
  "$W/data/.cf-platform-active" "$W/data/.cf-platform-active.terug"

na_update
touch "$W/downgrade_breekt"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=downgrade || rc=$?
rm -f "$W/downgrade_breekt"
check "terug downgrade: mislukt, de VIP-node zegt het" \
  bash -c '[ "$1" != 0 ] && grep -qF "db-downgrade mislukt" "$2" && grep -qF "MET_DATABASE=ja" "$2"' _ "$rc" "$LOG"
check "terug downgrade: mislukt, de database en de rijen zijn ongewijzigd en het platform draait weer" \
  is "$(db_rev) $(db_rijen) $(draait n3)" "0014 voor+na $B"

na_update
rc=0; draai_cluster platform-terug.sh MET_DATABASE=misschien || rc=$?
check "terug: een onbekende waarde voor MET_DATABASE wordt geweigerd" \
  bash -c '[ "$1" != 0 ] && grep -qF "kies nee, ja of downgrade" "$2"' _ "$rc" "$LOG"
check "terug: onbekende MET_DATABASE, niets veranderd" terug_niets_veranderd

na_update
touch "$W/nodes/n1/mount_down"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug: mount weg geweigerd, de run stopt op n1" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:1" ] && grep -qF "is niet gemount" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
check "terug: mount weg, niets veranderd" terug_niets_veranderd

na_update
touch "$W/data/.cf-platform-active.terug"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug: halve eerdere terugzet geweigerd" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:1" ] && grep -qF "een eerdere Platform terug is halverwege gestopt" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
rm "$W/data/.cf-platform-active.terug"
check "terug: halve eerdere terugzet, niets veranderd" terug_niets_veranderd

na_update
id_van_ref v082 >"$W/nodes/n1/running"
touch "$W/nodes/n1/active"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug: platform zonder VIP geweigerd" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:1" ] && grep -qF "cf-platform draait op deze node zonder het VIP" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
rm "$W/nodes/n1/active" "$W/nodes/n1/running"
check "terug: platform zonder VIP, niets veranderd" terug_niets_veranderd

na_update
for n in n1 n2; do id_van_ref v082 >"$W/nodes/$n/running"; done
touch "$W/nodes/n2/active"
rc=0; draai n1 platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug: twee platformen (gezien vanaf n1) geweigerd" \
  bash -c '[ "$1" != 0 ] && grep -qF "het platform draait op 2 nodes tegelijk (n2 n3)" "$2"' _ "$rc" "$LOG"
rm "$W/nodes/n2/active" "$W/nodes/n2/running"
touch "$W/nodes/n1/active"
rc=0; draai n3 platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug: twee platformen (gezien vanaf de VIP-houder) geweigerd" \
  bash -c '[ "$1" != 0 ] && grep -qF "het platform draait op 2 nodes tegelijk (n3 n1)" "$2"' _ "$rc" "$LOG"
rm "$W/nodes/n1/active" "$W/nodes/n1/running"
check "terug: twee platformen, niets veranderd" terug_niets_veranderd

na_update
rm "$W/nodes/n1/images/vorige"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug: zonder cf-platform:vorige (Docker, Opruimen) geweigerd" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:1" ] && grep -qF "cf-platform:vorige ontbreekt op deze node" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
id_van_ref v081 >"$W/nodes/n1/images/vorige"
check "terug: zonder cf-platform:vorige, niets veranderd" terug_niets_veranderd

na_update
rm "$W/nodes/n1/bin/psql"
rc=0; draai_cluster platform-terug.sh || rc=$?
check "terug: MET_DATABASE=nee terwijl de database terug moet: niets gedaan, op de eerste node" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:1" ] && grep -qF "NIETS GEDAAN" "$3" && grep -qF "MET_DATABASE=ja: de back-up van vóór de update terugzetten" "$3"' _ \
  "$rc" "$GEDRAAID" "$LOG"
check "terug: n1 zonder psql neemt de head van cf-platform:local aan" bevat "$LOG" "database niet gelezen (geen psql?); aangenomen: revisie 0014"
check "terug: MET_DATABASE=nee, niets veranderd" terug_niets_veranderd

na_update
rm "$W/nodes/n3/bin/psql"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug: de VIP-houder kan de database niet lezen: geweigerd met de hint" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:0 n2:0 n3:1" ] && grep -qF "de revisie van de database is niet te lezen" "$3" &&
    grep -qF "Draai Platform bijwerken om ze weer gelijk te zetten" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
check "terug: de VIP-houder kan de database niet lezen, daar niets veranderd" \
  bash -c '[ "$1" = "$3" ] && [ "$2" = "$3" ] && [ "$4" = "0014 voor+na" ] && [ -e "$5" ]' _ \
  "$(tag n3 local)" "$(draait n3)" "$B" "$(db_rev) $(db_rijen)" "$W/data/.cf-platform-active"
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "terug: Platform bijwerken zet de nodes daarna weer gelijk" \
  bash -c '[ "$1" = 0 ] && [ "$2" = "$4" ] && [ "$3" = "$4" ]' _ "$rc" "$(tag n1 local)" "$(tag n2 local)" "$B"

na_update
rm "$W/nodes/n3/bin/pg_dump"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug: pg_dump ontbreekt op de VIP-houder geweigerd" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:0 n2:0 n3:1" ] && grep -qF "pg_dump ontbreekt (postgresql-client)" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
check "terug: pg_dump ontbreekt, de database en het platform op n3 zijn niet geraakt" \
  bash -c '[ "$1" = "$3" ] && [ "$2" = "0014 voor+na" ] && [ -e "$4" ] && ! grep -q "^systemctl stop" "$5"' _ \
  "$(draait n3)" "$(db_rev) $(db_rijen)" "$B" "$W/data/.cf-platform-active" "$W/nodes/n3/log"

na_update
rm "$(eigen n3)/voor-update.dump"
rc=0; draai n3 platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug: zonder voor-update.dump geweigerd" \
  bash -c '[ "$1" != 0 ] && grep -qF "geen back-up van vóór de update" "$2"' _ "$rc" "$LOG"
check "terug: zonder voor-update.dump, niets veranderd" terug_niets_veranderd

na_update
env -i CFT_NODE="$W/nodes/n3" CFT_WORLD="$W" PATH="$W/nodes/n3/bin" "$SH" "$W/nodes/n3/root/lib/platform-ctl.sh" db-backup >/dev/null
# shellcheck disable=SC2012  # zoals platform-terug.sh: de nieuwste back-up
nieuwste=$(ls -1t "$W/nodes/n3/root/backups"/platform-*.dump | head -n 1)
rc=0; draai n3 platform-terug.sh MET_DATABASE=ja "DUMP=$nieuwste" || rc=$?
check "terug: een back-up op de verkeerde revisie geweigerd" \
  bash -c '[ "$1" != 0 ] && grep -qF "staat op revisie 0014, niet op 0012; niets veranderd aan de database" "$2"' _ "$rc" "$LOG"
check "terug: verkeerde back-up, niets veranderd" terug_niets_veranderd

na_update
touch "$W/nodes/n1/niet_root"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug: niet als root geweigerd, de run stopt op n1" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:1" ] && grep -qF "draai dit als root" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
rm "$W/nodes/n1/niet_root"
check "terug: niet als root, niets veranderd" terug_niets_veranderd

na_update
rm "$W/nodes/n1/bin/curl"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug: zonder curl geweigerd" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:1" ] && grep -qF "curl ontbreekt" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
check "terug: zonder curl, niets veranderd" terug_niets_veranderd

# Platform bijwerken loopt nog op de VIP-houder: hetzelfde slot.
na_update
exec {slot}>"$W/nodes/n3/root/run/lock"
flock -n "$slot"
rc=0; draai n3 platform-terug.sh MET_DATABASE=ja || rc=$?
exec {slot}>&-
check "terug: slot bezet geweigerd" bash -c '[ "$1" != 0 ] && grep -qF "loopt al op deze node" "$2"' _ "$rc" "$LOG"
check "terug: slot bezet, niets veranderd" terug_niets_veranderd

# --- Platform terug: gelukt -----------------------------------------------------------------

na_update
for n in $NODES; do chmod 0755 "$(eigen "$n")"; done
rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug: met de database, het hele cluster" is "$rc $GEDRAAID" "0  n1:0 n2:0 n3:0"
check "terug: de eigen map is 0700 op elke node (voor-terug.dump staat erin)" \
  is "$(stat -c %a "$(eigen n1)") $(stat -c %a "$(eigen n2)") $(stat -c %a "$(eigen n3)")" "700 700 700"
for n in n1 n2 n3; do
  check "terug: $n heeft 0.8.1 als cf-platform:local, 0.8.2 als cf-platform:teruggezet" \
    is "$(tag $n local) $(tag $n teruggezet)" "$A $B"
done
check "terug: de database staat weer op 0012 met alleen de gegevens van vóór de update" is "$(db_rev) $(db_rijen)" "0012 voor"
check "terug: n3 draait 0.8.1, de markering is terug" \
  bash -c '[ "$1" = "$2" ] && [ -e "$3/.cf-platform-active" ] && [ ! -e "$3/.cf-platform-active.terug" ]' _ "$(draait n3)" "$A" "$W/data"
check "terug: voor-terug.dump bewaart de nieuwere stand" \
  bash -c 'grep -qx "rev=0014" "$1/voor-terug.dump" && grep -qx "rijen=voor+na" "$1/voor-terug.dump"' _ "$(eigen n3)"
check "terug: DROP OWNED direct na BEGIN, in één transactie" \
  bash -c 'grep -A1 -x "BEGIN;" "$1" | tail -n 1 | grep -qx "DROP OWNED BY CURRENT_USER;" && [ "$(grep -cx "BEGIN;" "$1")" = 1 ]' _ "$W/db/laatste.sql"
check "terug: pg_restore zonder eigenaar en rechten, als één transactie" \
  grep -q '^pg_restore --no-owner --no-privileges --single-transaction -f - ' "$W/pg-args.log"
check "terug: markering opzij vóór het stoppen" grep -qx "gestopt; markering opzij" "$W/nodes/n3/log"
check "terug: de app antwoordt met 0.8.1 en de versie voor ClusterForge staat er" \
  bash -c 'grep -qF "== Klaar: app 0.8.1" "$1" && grep -qF "de versie op $2," "$1"' _ "${LOG_VAN[n3]}" "$REV_A"
check "terug: n1 heeft alleen het image gewisseld" bevat "${LOG_VAN[n1]}" "Klaar op deze node (geen VIP of geen overstap): alleen het image."

for n in $NODES; do : >"$W/nodes/$n/log"; done
: >"$W/pg-args.log"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug: tweede keer gelukt, zonder terugzet van de database" \
  bash -c '[ "$1" = 0 ] && grep -qF "cf-platform:local is al het vorige image" "$2" && grep -qF "De database hoeft niet terug (revisie 0012)" "$2" &&
    ! grep -q "^pg_restore --no-owner" "$3"' _ "$rc" "${LOG_VAN[n3]}" "$W/pg-args.log"
check "terug: tweede keer draait 0.8.1 door" is "$(draait n3) $(db_rev) $(db_rijen)" "$A 0012 voor"

rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken na terug: een teruggezet image wordt opnieuw gebouwd" \
  bash -c '[ "$1" = 0 ] && grep -qF "cf-platform:local is een teruggezet image" "$2" && grep -qF "cf-platform:local is een teruggezet image" "$3"' _ \
  "$rc" "${LOG_VAN[n1]}" "${LOG_VAN[n3]}"
check "bijwerken na terug: weer 0.8.2 met 0.8.1 als vorige, database gemigreerd" \
  is "$(tag n1 local) $(tag n3 vorige) $(draait n3) $(db_rev)" "$B $A $B 0014"

# Na terug een mislukte nieuwe build: het teruggezette image blijft, zonder cf-platform:basis.
na_update
moet platform-terug.sh MET_DATABASE=ja
touch "$W/nodes/n1/build_faalt"
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken na terug, mislukte build: geweigerd op n1" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:1" ] && grep -qF "bouwen mislukt; het platform is niet geraakt" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
check "bijwerken na terug, mislukte build: 0.8.1 blijft, geen cf-platform:basis" \
  is "$(tag n1 local) $(tag n1 basis) $(draait n3) $(db_rev)" "$A  $A 0012"

# Na een update alleen andere instellingen (platform.env), geen nieuwe versie: platform-ctl.sh
# herstart met een nieuwe back-up, maar voor-update.dump blijft die van vóór de update.
na_update
cp "$(eigen n3)/voor-update.info" "$work/info-voor"
voor_n=$(find "$W/nodes/n3/root/backups" -name 'platform-*.dump' | wc -l)
for n in $NODES; do sed -i 's/^ANALYSE=uit$/ANALYSE=aan/' "$W/nodes/$n/root/etc/platform.env"; done
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken: andere instellingen, n3 herstart met een nieuwe back-up" \
  bash -c '[ "$1" = 0 ] && grep -qx "systemctl restart cf-platform" "$2" &&
    [ "$(find "$3" -name "platform-*.dump" | wc -l)" = "$(($4 + 1))" ]' _ "$rc" "$W/nodes/n3/log" "$W/nodes/n3/root/backups" "$voor_n"
check "bijwerken: andere instellingen, voor-update.dump en .info blijven die van vóór de update" \
  bash -c 'grep -qx "rev=0012" "$1/voor-update.dump" && cmp -s "$1/voor-update.info" "$2"' _ "$(eigen n3)" "$work/info-voor"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
check "bijwerken: andere instellingen, Platform terug kan daarna nog naar 0.8.1" \
  is "$rc $(draait n3) $(db_rev) $(db_rijen)" "0 $A 0012 voor"

# Zonder database: een fix zonder migratie (v082b) en terug naar 0.8.2.
na_update
zet_versie v082b
moet platform-bijwerken.sh
for n in $NODES; do : >"$W/nodes/$n/log"; done
: >"$W/pg-args.log"
rc=0; draai_cluster platform-terug.sh || rc=$?
check "terug: zonder database (zelfde Alembic-head), het hele cluster" is "$rc $GEDRAAID" "0  n1:0 n2:0 n3:0"
check "terug: zonder database, 0.8.2 draait weer, de database is niet geraakt" \
  is "$(draait n3) $(db_rev) $(db_rijen)" "$B 0014 voor+na"
check "terug: zonder database geen pg_restore en geen back-up" \
  bash -c '! grep -q "^pg_restore --no-owner\|^pg_dump" "$1" && grep -q "^systemctl restart cf-platform$" "$2"' _ "$W/pg-args.log" "$W/nodes/n3/log"

# Zonder database, maar op de VIP-houder is cf-platform:vorige weg (Docker, Opruimen daar): de
# nodes zonder VIP zijn dan al terug, en de fout zegt hoe je ze weer gelijk zet.
na_update
zet_versie v082b
moet platform-bijwerken.sh
rm "$W/nodes/n3/images/vorige"
rc=0; draai_cluster platform-terug.sh || rc=$?
check "terug: zonder database, cf-platform:vorige weg op de VIP-houder: geweigerd met de hint" \
  bash -c '[ "$1" != 0 ] && [ "$2" = " n1:0 n2:0 n3:1" ] && grep -qF "cf-platform:vorige ontbreekt op deze node" "$3" &&
    grep -qF "Draai Platform bijwerken om ze weer gelijk te zetten" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "terug: daarna zet Platform bijwerken de nodes zonder VIP weer op de fix" \
  is "$rc $(tag n1 local) $(tag n2 local) $(draait n3)" "0 $C $C $C"

# Mislukte terugzet: de database blijft zoals hij was, de markering komt terug en het nieuwe
# image start weer; daarna zet Platform bijwerken de nodes zonder VIP weer gelijk.
# Ook: de back-up van nu mislukt, cf-platform stopt niet, en Annuleren (SIGTERM) midden in het
# terugzetten.
for fout_soort in herstel_breekt psql_fout pg_dump_faalt stopt_niet psql_term; do
  na_update
  case "$fout_soort" in
    pg_dump_faalt) melding="back-up mislukt; niets veranderd aan de database" ;;
    stopt_niet) melding="cf-platform stopt niet" ;;
    psql_term) melding="afgebroken" ;;
    *) melding="database terugzetten mislukt" ;;
  esac
  if [ "$fout_soort" = stopt_niet ]; then touch "$W/nodes/n3/stopt_niet"; else touch "$W/$fout_soort"; fi
  [ "$fout_soort" != psql_term ] || EIGEN_GROEP=1
  rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
  EIGEN_GROEP=
  check "terug: mislukte terugzet ($fout_soort) faalt op n3" \
    bash -c '[ "$1" != 0 ] && [ "$2" = " n1:0 n2:0 n3:1" ] && grep -qF "$4" "$3"' _ "$rc" "$GEDRAAID" "$LOG" "$melding"
  check "terug: mislukte terugzet ($fout_soort), database ongewijzigd" is "$(db_rev) $(db_rijen)" "0014 voor+na"
  check "terug: mislukte terugzet ($fout_soort), markering terug en het nieuwe image draait weer" \
    bash -c '[ -e "$1/.cf-platform-active" ] && [ ! -e "$1/.cf-platform-active.terug" ] && [ "$2" = "$3" ] && [ "$4" = "$3" ]' _ \
    "$W/data" "$(draait n3)" "$B" "$(tag n3 local)"
  check "terug: mislukte terugzet ($fout_soort), de hint" \
    bash -c 'grep -qF "markering teruggezet; cf-platform start weer" "$1" && grep -qF "Draai Platform bijwerken om ze weer gelijk te zetten" "$1"' _ "$LOG"
  case "$fout_soort" in
    pg_dump_faalt | stopt_niet)
      check "terug: mislukte terugzet ($fout_soort), geen terugzet en geen back-up van nu" \
        bash -c '! grep -q "^pg_restore --no-owner" "$1" && [ ! -e "$2/voor-terug.dump" ]' _ "$W/pg-args.log" "$(eigen n3)" ;;
    *)
      check "terug: mislukte terugzet ($fout_soort), de back-up van nu" grep -qx "rev=0014" "$(eigen n3)/voor-terug.dump" ;;
  esac
  rm -f "$W/$fout_soort" "$W/nodes/n3/stopt_niet"
  rc=0; draai_cluster platform-bijwerken.sh || rc=$?
  check "terug: mislukte terugzet ($fout_soort), Platform bijwerken zet alles weer op 0.8.2" \
    is "$rc $(tag n1 local) $(tag n2 local) $(draait n3) $(db_rev)" "0 $B $B $B 0014"
done

# Een fout of Annuleren na het terugzetten van de database, of een uitkomst die niet te lezen is.
# Staat de database (misschien) al op 0012, dan mag het nieuwe image niet starten: dat migreert de
# database weer vooruit. De markering blijft opzij en het platform uit; Platform bijwerken weigert
# dan, en Platform terug nog een keer kijkt naar de database en maakt het af.
#   select_faalt_na             COMMIT gelukt, daarna is de database niet te lezen
#   select_faalt_na+psql_fout   psql faalt vóór de COMMIT, daarna niet te lezen
#   tag_faalt                   COMMIT gelukt, docker tag faalt bij het wisselen van het image
#   psql_term_na                Annuleren (SIGTERM) vlak na de COMMIT
for fout_soort in select_faalt_na select_faalt_na+psql_fout tag_faalt psql_term_na; do
  na_update
  case "$fout_soort" in
    tag_faalt) touch "$W/nodes/n3/tag_faalt" ;;
    *) for f in ${fout_soort//+/ }; do touch "$W/$f"; done ;;
  esac
  case "$fout_soort" in
    select_faalt_na*) melding="is niet te lezen" ;;
    *) melding="De database is al teruggezet (revisie 0012)" ;;
  esac
  db_na="0012 voor"; [ "$fout_soort" != select_faalt_na+psql_fout ] || db_na="0014 voor+na"
  [ "$fout_soort" != psql_term_na ] || EIGEN_GROEP=1
  rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
  EIGEN_GROEP=
  check "terug: $fout_soort faalt op n3 en zegt wat er met de database is" \
    bash -c '[ "$1" != 0 ] && [ "$2" = " n1:0 n2:0 n3:1" ] && grep -qF "$4" "$3" && grep -qF "Draai Platform terug nog een keer" "$3"' _ \
    "$rc" "$GEDRAAID" "$LOG" "$melding"
  check "terug: $fout_soort, de database blijft zoals het terugzetten hem liet" is "$(db_rev) $(db_rijen)" "$db_na"
  check "terug: $fout_soort, de markering blijft opzij en het nieuwe image start niet" \
    bash -c '[ ! -e "$1/.cf-platform-active" ] && [ -e "$1/.cf-platform-active.terug" ] && [ ! -e "$2" ] &&
      ! grep -qF "markering teruggezet" "$3" && ! grep -q "^systemctl start" "$4"' _ "$W/data" "$W/nodes/n3/active" "$LOG" "$W/nodes/n3/log"
  rm -f "$W/select_faalt_na" "$W/select_faalt" "$W/psql_fout" "$W/psql_term_na" "$W/nodes/n3/tag_faalt"
  rc=0; draai_cluster platform-bijwerken.sh || rc=$?
  check "terug: $fout_soort, Platform bijwerken weigert zolang de markering opzij staat" \
    bash -c '[ "$1" != 0 ] && [ "$2" = " n1:1" ] && grep -qF "een eerdere Platform terug is niet afgemaakt" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
  if [ "$fout_soort" = select_faalt_na+psql_fout ]; then
    rc=0; draai_cluster platform-terug.sh || rc=$?
    check "terug: $fout_soort, zonder MET_DATABASE niets gedaan en de markering blijft opzij" \
      bash -c '[ "$1" != 0 ] && [ "$2" = " n1:1" ] && grep -qF "NIETS GEDAAN" "$3" && grep -qF "de markering staat nog opzij" "$3" &&
        [ -e "$4/.cf-platform-active.terug" ]' _ "$rc" "$GEDRAAID" "$LOG" "$W/data"
  fi
  rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
  check "terug: $fout_soort, Platform terug nog een keer maakt het af" \
    is "$rc$GEDRAAID|$(draait n3) $(db_rev) $(db_rijen)" "0 n1:0 n2:0 n3:0|$A 0012 voor"
  check "terug: $fout_soort, daarna de markering terug en de melding over de vorige run" \
    bash -c '[ -e "$1/.cf-platform-active" ] && [ ! -e "$1/.cf-platform-active.terug" ] && grep -qF "deze run maakt hem af" "$2"' _ \
    "$W/data" "$LOG"
done

# --- de twee VIP's op verschillende nodes ---------------------------------------------------------

# Het VIP van het platform op n1, dat van de database op n3: ClusterForge draait n2, n1, n3. De
# node met het VIP van het platform herstart pas als elke andere node de Versie heeft; anders doet
# hij niets, bouwt n3 daarna, en vraagt n3 om nog een run.
nieuw_cluster
zet_vips n1 n3
zet_versie v082
touch "$W/nodes/n3/build_faalt"
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken, VIP's gesplitst: ClusterForge draait n2, n1, n3; de build op n3 faalt" is "$rc$GEDRAAID" "1 n2:0 n1:0 n3:1"
check "bijwerken, VIP's gesplitst, n3 bouwt niet: n1 heeft nog niets gedaan, de database staat op 0012" \
  is "$(draait n1) $(tag n1 local) $(tag n1 vorige)|$(db_rev)|$(ls "$W/nodes/n1/root/backups")" "$A $A |0012|"
check "bijwerken, VIP's gesplitst: n1 zegt waarop hij wacht" bevat "${LOG_VAN[n1]}" "NOG NIET: de Versie is nog niet gebouwd op n3"
check "bijwerken, VIP's gesplitst: n1 zegt wat n3 meldde" bevat "${LOG_VAN[n1]}" "n3 meldde: niets"
rm "$W/nodes/n3/build_faalt"

nieuw_cluster
zet_vips n1 n3
zet_versie v082
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken, VIP's gesplitst: de eerste run stopt op n3 met NOG NIET KLAAR" \
  bash -c '[ "$1" = 1 ] && [ "$2" = " n2:0 n1:0 n3:1" ] && grep -qF "NOG NIET KLAAR" "$3" && grep -qF "draai Platform bijwerken nog een keer" "$3"' _ \
  "$rc" "$GEDRAAID" "$LOG"
check "bijwerken, VIP's gesplitst, eerste run: n2 en n3 gebouwd, n1 niet geraakt, database 0012" \
  is "$(tag n2 local) $(tag n3 local)|$(draait n1) $(tag n1 local)|$(db_rev)" "$B $B|$A $A|0012"
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken, VIP's gesplitst: de tweede run herstart op n1" is "$rc$GEDRAAID|$(draait n1) $(db_rev)" "0 n2:0 n1:0 n3:0|$B 0014"
check "bijwerken, VIP's gesplitst: voor-update.dump staat op n1 en is van vóór de migratie" grep -qx "rev=0012" "$(eigen n1)/voor-update.dump"
check "bijwerken, VIP's gesplitst: geen wachtlijst meer" bash -c '[ ! -e "$1/.cf-platform-bijwerken/wacht-bijwerken" ]' _ "$W/data"

# Platform terug met de database, zelfde VIP's: n1 zet de database pas terug als n2 en n3 het
# vorige image al hebben.
echo "voor+na" >"$W/db/rijen"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug, VIP's gesplitst: de eerste run stopt op n3 met NOG NIET KLAAR" \
  bash -c '[ "$1" = 1 ] && [ "$2" = " n2:0 n1:0 n3:1" ] && grep -qF "NOG NIET KLAAR" "$3" && grep -qF "draai Platform terug nog een keer" "$3"' _ \
  "$rc" "$GEDRAAID" "$LOG"
check "terug, VIP's gesplitst, eerste run: n2 en n3 terug, n1 en de database niet geraakt" \
  is "$(tag n2 local) $(tag n3 local)|$(draait n1) $(tag n1 local)|$(db_rev) $(db_rijen)" "$A $A|$B $B|0014 voor+na"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug, VIP's gesplitst: de tweede run zet de database terug op n1" \
  is "$rc$GEDRAAID|$(draait n1) $(db_rev) $(db_rijen)" "0 n2:0 n1:0 n3:0|$A 0012 voor"

# Zelfde VIP's na een update, maar op n3 is cf-platform:vorige weg: n3 weigert, en de database is
# dan nog niet geraakt.
nieuw_cluster
zet_vips n1 n3
zet_versie v082
draai_cluster platform-bijwerken.sh || true
moet platform-bijwerken.sh
echo "voor+na" >"$W/db/rijen"
rm "$W/nodes/n3/images/vorige"
rc=0; draai_cluster platform-terug.sh MET_DATABASE=ja || rc=$?
check "terug, VIP's gesplitst, geen cf-platform:vorige op n3: geweigerd op n3 met de hint" \
  bash -c '[ "$1" = 1 ] && [ "$2" = " n2:0 n1:0 n3:1" ] && grep -qF "cf-platform:vorige ontbreekt" "$3" &&
    grep -qF "Draai Platform bijwerken om ze weer gelijk te zetten" "$3"' _ "$rc" "$GEDRAAID" "$LOG"
check "terug, VIP's gesplitst, geen cf-platform:vorige op n3: database en platform op n1 niet geraakt" \
  is "$(draait n1) $(db_rev) $(db_rijen)" "$B 0014 voor+na"
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "terug, VIP's gesplitst: Platform bijwerken zet n2 daarna weer gelijk" is "$rc $(tag n2 local) $(draait n1)" "0 $B $B"

# Een node met een andere Versie (Toepassen nog niet overal): de node met het VIP doet niets en
# zegt wat die node meldde.
nieuw_cluster
zet_versie v082
sed -i "s#^REF='.*'\$#REF='v081'#" "$W/nodes/n2/root/lib/platform-src.sh" "$W/nodes/n2/root/lib/platform-ctl.sh"
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken, n2 op een andere Versie: n3 raakt niets aan" \
  is "$rc$GEDRAAID|$(draait n3) $(tag n3 local)|$(db_rev)|$(ls "$W/nodes/n3/root/backups")" "0 n1:0 n2:0 n3:0|$A $A|0012|"
check "bijwerken, n2 op een andere Versie: n3 zegt wat n2 meldde" bevat "${LOG_VAN[n3]}" "n2 meldde: bijgewerkt v081 "

# Andersom (database op n1, platform op n3): n3 komt als laatste, één run is genoeg.
nieuw_cluster
zet_vips n3 n1
zet_versie v082
rc=0; draai_cluster platform-bijwerken.sh || rc=$?
check "bijwerken, database-VIP op n1 en platform-VIP op n3: één run" is "$rc$GEDRAAID|$(draait n3) $(db_rev)" "0 n2:0 n1:0 n3:0|$B 0014"

# --- geen geheimen ------------------------------------------------------------------------------

check "geen wachtwoord in de uitvoer van de commando's" bevat_niet "$ALLES" "$WACHTWOORD"
check "geen wachtwoord in de bestanden van de commando's (terug.log, .info, .rev, de meldingen)" \
  bash -c 'ls "$1"/n1/root/eigen/* "$3"/node-n1 >/dev/null && ! grep -rqF "$2" "$1"/n*/root/eigen "$3"' _ \
  "$W/nodes" "$WACHTWOORD" "$W/data/.cf-platform-bijwerken"
check "geen wachtwoord op een opdrachtregel (docker, systemctl, curl, pg_dump, pg_restore, psql)" \
  bash -c 'grep -q "^pg_dump .* -f " "$1" && grep -q "^psql " "$1" && ! grep -qF "$2" "$1"' _ "$work/aanroepen.log" "$WACHTWOORD"

echo
echo "$oks goed, $fails fout"
[ "$fails" = 0 ]
