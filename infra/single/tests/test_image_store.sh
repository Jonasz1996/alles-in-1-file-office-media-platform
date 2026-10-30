#!/usr/bin/env bash
# Het image van office op de klassieke image-opslag van Docker, met een echte dockerd (review van
# fase 4, uitrol-1; bouwontwerp §5.1, het risico "klassieke image-opslag"). Alleen in de sandbox,
# als root, ±1 minuut; niet in CI:
#
#   bash infra/single/tests/test_image_store.sh
#
# Waarom: Docker van vóór 29, of met de snapshotter van containerd uit, heeft de klassieke opslag
# (driver overlay2). Daar is na een pull van mirror.gcr.io en `docker tag` de tag er wel, maar
# geen naam `repo:tag@digest` onder de naam van Docker Hub. Gaf install.sh compose zo'n naam, dan
# haalde compose het image van Docker Hub; weigerde Docker Hub (429), dan stopte het script na
# het vervangen van de app. De sandbox zelf heeft de opslag van containerd, waar het wel lukt;
# test_install_env.sh bootst de klassieke opslag na met een nep-docker, en deze test doet het echt.
#
# Hoe: een eigen dockerd met --feature containerd-snapshotter=false en --storage-driver overlay2,
# met een eigen werkmap (data-root, exec-root, socket), een eigen namespace in zijn eigen
# containerd, een eigen bridge, geen iptables en geen daemon.json. De Docker van de host, zijn
# containers en docker0 blijven onaangeroerd (nooit --bridge none: dat haalt docker0 weg).
# hello-world speelt DS:
# 1. van mirror.gcr.io, op digest (dockerd met de proxy van de omgeving);
# 2. dockerd opnieuw, met een proxy die niet bestaat: geen enkel register is nog te bereiken;
# 3. office_image van install.sh (INSTALL_LIB=1) geeft een naam, en die is er lokaal;
# 4. compose up -d --no-deps office met die naam start de container zonder iets op te halen, en
#    een losse compose up -d met de standaard van compose.office.yaml ook, zonder hem opnieuw te
#    maken (zoals B2 van test_install_update.sh);
# 5. de controle van het runbook (RepoDigests) en een pin uit RepoDigests werken op deze opslag.
# Aan het eind (ook bij een fout of Ctrl+C) gaan dockerd, de bridge en de werkmap weg.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
install_sh=$here/../install.sh
compose_office=$here/../compose.office.yaml
BRIDGE=imgstore0
SUBNET=10.213.9.1/24
DEAD_PROXY=http://127.0.0.1:9

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
stop() {
  echo "STOP  $1" >&2
  exit 1
}
have() { docker image inspect "$1" >/dev/null 2>&1; }

# --- vooraf ----------------------------------------------------------------------------------

[ "$(id -u)" = 0 ] || stop "alleen als root (een eigen dockerd en bridge)"
for tool in dockerd docker ip ss; do command -v "$tool" >/dev/null || stop "$tool ontbreekt"; done
! ip link show "$BRIDGE" >/dev/null 2>&1 || stop "er is al een netwerkkaart $BRIDGE"
[ -z "$(ss -Hltn 'sport = :9')" ] || stop "poort 9 is bezet; de dode proxy moet dicht zijn"
# Kort pad: de sockets van containerd onder exec-root mogen niet langer zijn dan ±100 tekens
WORK=$(mktemp -d /tmp/imgstore.XXXXXX)
bridge_made=0
export DOCKER_HOST=unix://$WORK/d.sock
unset DOCKER_CONTEXT
echo '{}' >"$WORK/daemon.json"

start_dockerd() {
  HTTPS_PROXY=$1 https_proxy=$1 HTTP_PROXY=$1 http_proxy=$1 NO_PROXY="" no_proxy="" \
    dockerd --config-file "$WORK/daemon.json" --data-root "$WORK/data" --exec-root "$WORK/exec" \
    --pidfile "$WORK/dockerd.pid" -H "$DOCKER_HOST" \
    --containerd-namespace imgstore --containerd-plugins-namespace imgstore-plugins \
    --feature containerd-snapshotter=false --storage-driver overlay2 \
    --bridge "$BRIDGE" --iptables=false --ip6tables=false --ip-masq=false --userland-proxy=false \
    >>"$WORK/dockerd.log" 2>&1 </dev/null &
  for _ in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then return 0; fi
    sleep 1
  done
  tail -n 20 "$WORK/dockerd.log" >&2
  return 1
}

stop_dockerd() {
  local pid
  pid=$(cat "$WORK/dockerd.pid" 2>/dev/null || true)
  [ -n "$pid" ] || return 0
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 30); do
    kill -0 "$pid" 2>/dev/null || return 0
    sleep 1
  done
  echo "dockerd ($pid) stopt niet" >&2
  return 1
}

opruimen() {
  local rc=$? m
  set +e
  if [ -S "$WORK/d.sock" ]; then
    docker compose --project-directory "$WORK/compose" -f "$WORK/compose/compose.yaml" down >/dev/null 2>&1
  fi
  stop_dockerd
  # Wat dockerd na een harde fout nog gekoppeld liet, alleen onder de eigen werkmap
  for m in $(findmnt -rn -o TARGET | grep "^$WORK/" | sort -r); do umount "$m" 2>/dev/null; done
  [ "$bridge_made" = 0 ] || ip link delete "$BRIDGE" 2>/dev/null
  rm -rf "$WORK"
  if [ -e "$WORK" ] || ip link show "$BRIDGE" >/dev/null 2>&1; then
    echo "LET OP: er is nog iets van de test over ($WORK of $BRIDGE)"
    rc=1
  fi
  exit "$rc"
}
trap opruimen EXIT
trap 'exit 130' INT TERM

ip link add "$BRIDGE" type bridge
bridge_made=1
ip addr add "$SUBNET" dev "$BRIDGE"
ip link set "$BRIDGE" up

# --- 1. hello-world van mirror.gcr.io, op digest ---------------------------------------------

echo "== 1. Een eigen dockerd met de klassieke opslag; hello-world van mirror.gcr.io"
start_dockerd "${HTTPS_PROXY:-${https_proxy:-}}" || stop "dockerd start niet"
check "de opslag is de klassieke (overlay2)" test "$(docker info --format '{{.Driver}}')" = overlay2
docker pull -q mirror.gcr.io/library/hello-world:latest >/dev/null || stop "hello-world niet op te halen van mirror.gcr.io"
digest=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' mirror.gcr.io/library/hello-world:latest |
  sed -n 's|^mirror.gcr.io/library/hello-world@||p' | head -n 1)
[ -n "$digest" ] || stop "geen digest voor hello-world"
# Zoals install.sh: het image alleen op digest, van de mirror. (De tag weg haalt hier het hele
# image weg, met zijn digest; dus daarna opnieuw, op digest.)
docker image rm mirror.gcr.io/library/hello-world:latest >/dev/null
docker pull -q "mirror.gcr.io/library/hello-world@$digest" >/dev/null || stop "hello-world niet op digest op te halen"
check "alleen onder de naam van de mirror, op digest" have "mirror.gcr.io/library/hello-world@$digest"
check "(geen tag)" test "$(docker image inspect --format '{{len .RepoTags}}' "mirror.gcr.io/library/hello-world@$digest")" = 0

# --- 2. Geen register meer --------------------------------------------------------------------

echo "== 2. dockerd opnieuw, zonder register (Docker Hub weigert)"
stop_dockerd || stop "dockerd stopt niet"
start_dockerd "$DEAD_PROXY" || stop "dockerd start niet opnieuw"
if docker pull -q "library/hello-world@$digest" >/dev/null 2>&1; then stop "Docker Hub is toch bereikbaar"; fi
ok "Docker Hub is niet te bereiken"

# --- 3. office_image van install.sh -----------------------------------------------------------

echo "== 3. office_image van install.sh, met hello-world in de rol van DS"
export PLATFORM_ROOT=$WORK/root INSTALL_LIB=1
unset OFFICE OFFICE_IMAGE
# shellcheck source=/dev/null
. "$install_sh"
ds_repo=$OFFICE_REPO ds_tag=$OFFICE_TAG ds_digest=$OFFICE_DIGEST
OFFICE_REPO=library/hello-world OFFICE_TAG=t-office OFFICE_DIGEST=$digest
if img=$(office_image); then ok "office_image lukt (via de mirror)"; else fout "office_image lukt (via de mirror)"; fi
echo "      naam voor compose: ${img:-geen}"
check "die naam is er lokaal" have "$img"

# --- 4. compose -------------------------------------------------------------------------------

echo "== 4. compose up met die naam, en een losse compose up met de standaard van compose"
# shellcheck disable=SC2016  # de $ hoort bij de tekst van compose
default=$(sed -n 's/.*\${OFFICE_IMAGE:-\([^}]*\)}.*/\1/p' "$compose_office")
grep -qE '^ +pull_policy: missing$' "$compose_office" || stop "compose.office.yaml heeft geen pull_policy: missing meer; pas deze test aan"
default=${default//$ds_digest/$digest}
default=${default//$ds_repo:$ds_tag/$OFFICE_REPO:$OFFICE_TAG}
default=${default//$ds_repo/$OFFICE_REPO}
echo "      standaard van compose (met hello-world): $default"
mkdir -p "$WORK/compose"
cat >"$WORK/compose/compose.yaml" <<YAML
name: imgstore
services:
  office:
    image: \${OFFICE_IMAGE:-$default}
    pull_policy: missing
    network_mode: none
YAML
dc=(docker compose --project-directory "$WORK/compose" -f "$WORK/compose/compose.yaml")
if OFFICE_IMAGE=$img "${dc[@]}" up -d --no-deps office >"$WORK/up1.log" 2>&1; then
  ok "zoals main: compose up -d --no-deps office lukt"
else
  fout "zoals main: compose up -d --no-deps office lukt"
  sed 's/^/      /' "$WORK/up1.log" | tail -n 5
fi
check "niets opgehaald" bash -c "! grep -qi pull '$WORK/up1.log'"
first=$("${dc[@]}" ps -aq office)
check "de container is er" test -n "$first"
same_container() { [ -n "$first" ] && [ "$("${dc[@]}" ps -aq office)" = "$first" ]; }
if env -u OFFICE_IMAGE "${dc[@]}" up -d office >"$WORK/up2.log" 2>&1; then
  ok "een losse compose up -d (de standaard van compose) lukt"
else
  fout "een losse compose up -d (de standaard van compose) lukt"
  sed 's/^/      /' "$WORK/up2.log" | tail -n 5
fi
check "ook dan niets opgehaald" bash -c "! grep -qi pull '$WORK/up2.log'"
check "en niet opnieuw gemaakt" same_container

# --- 5. Het runbook ---------------------------------------------------------------------------

echo "== 5. De controle van het runbook en een pin voor terugrollen"
digests=$(docker image inspect --format '{{range .RepoDigests}}{{println .}}{{end}}' "$OFFICE_REPO:$OFFICE_TAG" 2>/dev/null || true)
check "RepoDigests van de tag noemt de digest van install.sh" grep -q "@$digest$" <<<"$digests"
pin=$(grep "@$digest$" <<<"$digests" | head -n 1 || true)
check "een pin uit RepoDigests is er lokaal" have "$pin"
if got=$(OFFICE_IMAGE=$pin office_image) && [ "$got" = "$pin" ]; then
  ok "office_image neemt die pin zonder register"
else
  fout "office_image neemt die pin zonder register"
fi

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails controle(s) mislukt."
  exit 1
fi
echo "Alle controles geslaagd."
