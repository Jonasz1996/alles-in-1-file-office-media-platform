#!/usr/bin/env bash
# Zoeken op beeld: de echte bron van SigLIP 2 in de dienst analyse-omzetten, zoals install.sh hem op
# de LXC omzet (x3a.md, Open 9; I2). Laadt de functies van install.sh (INSTALL_LIB=1) en draait
# beeld_model met de echte compose-bestanden (compose_files met de analyse aan: compose.yaml,
# compose.analyse.yaml, en compose.analyse-limits.yaml als docker die grenzen kan geven):
#
#   OMZETTER_IMAGE=platform:ci bash infra/single/tests/test_omzetter_echt.sh
#
#   OMZETTER_IMAGE    het image van de app (standaard platform:local); analyse-omzetten krijgt het
#                     via een eigen compose-bestand, zoals in test_analyse_container.sh
#   OMZETTER_OUT      een map voor omzetter.txt (de regels goed en FOUT, voor "=== kort ===" in CI);
#                     leeg: nergens
#   OMZETTER_SRC      de boom met infra/single en api/app/analyse/models.lock (standaard deze
#                     checkout). Alleen voor een proef zonder huggingface.co: een eigen lock voor de
#                     vervanger op ware grootte (api/tests_analyse/siglip_vol.py) op een eigen server
#
# Draait in de job analyse-image, na de bouw van platform:ci, als een wijziging het omzetten kan
# raken (de poort daar). beeld_model doet precies wat het op de LXC doet: de vijf bronnen van
# huggingface.co (curl met --max-filesize, de grootte en de sha256 van models.lock) in een map van
# root onder MODELLEN_ROOT; de omzetting in analyse-omzetten (uid 10004, read_only, network none,
# /tmp 64 MB, /bron alleen lezen, en de grenzen); elk bestand van de uitkomst gekopieerd en
# nagekeken op grootte en sha256 van models.lock; het model in één rename op zijn plek; de bron weg.
# Daarna kijkt dit script zelf:
# - de dienst zoals compose hem start (user, read_only, network, /tmp, cap_drop, grenzen);
# - BEELD_STATE is nieuw, en de omzetter zei "klaar" (zijn tijd en maxrss gaan in de samenvatting);
# - MODELLEN_ROOT/siglip2-b32-256 heeft precies de bestanden van de uitkomst in models.lock, elk een
#   gewoon bestand van root (0644) met de grootte en de sha256 van de lock;
# - geen bron en geen .beeld.* achter in MODELLEN_ROOT.
# df vooraf, na beeld_model en na het opruimen. MODELLEN_ROOT, DATA_ROOT en het compose-project gaan
# weg aan het eind, ook bij een fout. Root nodig (chown 10004 en de mappen van root, zoals
# install.sh); anders start het zichzelf opnieuw met sudo -n. Exit 1 als een controle faalt.
#
# De variabelen en functies komen uit install.sh (shellcheck volgt de bron niet in CI):
# shellcheck disable=SC2034,SC2153,SC2154
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
top=$(cd "$here/../../.." && pwd)
install_sh=$here/../install.sh

if [ "$(id -u)" != 0 ]; then
  if sudo -n true 2>/dev/null; then
    exec sudo -n env "OMZETTER_IMAGE=${OMZETTER_IMAGE:-}" "OMZETTER_OUT=${OMZETTER_OUT:-}" \
      "OMZETTER_SRC=${OMZETTER_SRC:-}" bash "$0" "$@"
  fi
  echo "FOUT: deze test draait als root (of met sudo -n): chown 10004 en de mappen van root, zoals install.sh" >&2
  exit 1
fi

IMAGE=${OMZETTER_IMAGE:-platform:local}
OUT=${OMZETTER_OUT:-}
BOOM=${OMZETTER_SRC:-$top}
FAILED=0

umask 022
WERK=$(mktemp -d "${TMPDIR:-/tmp}/omzettertest.XXXXXX")
chmod 0755 "$WERK"
if [ -n "$OUT" ]; then
  mkdir -p "$OUT"
  : >"$OUT/omzetter.txt"
fi

regel() {
  echo "$*"
  if [ -n "$OUT" ]; then echo "$*" >>"$OUT/omzetter.txt"; fi
}
goed() { regel "goed: $*"; }
fout() {
  regel "FOUT: $*" >&2
  FAILED=1
}

docker_root=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
[ -n "$docker_root" ] && [ -d "$docker_root" ] || docker_root=/
schijf() {
  echo "df ($1):"
  df -h -- "$WERK" "$docker_root" 2>/dev/null || df -h -- "${TMPDIR:-/tmp}" "$docker_root"
}

compose=()
cleanup() {
  local rc=$?
  if [ "${#compose[@]}" -gt 0 ]; then
    "${compose[@]}" --profile omzetten down --remove-orphans --timeout 10 >/dev/null 2>&1 || true
  fi
  rm -rf -- "$WERK"
  echo "df (na het opruimen):"
  df -h -- "${TMPDIR:-/tmp}" "$docker_root" || true
  if [ "$rc" = 0 ] && [ "$FAILED" != 0 ]; then rc=1; fi
  exit "$rc"
}
trap cleanup EXIT

docker image inspect "$IMAGE" >/dev/null 2>&1 || {
  fout "het image $IMAGE is er niet (OMZETTER_IMAGE)"
  exit 1
}
[ -f "$BOOM/api/app/analyse/models.lock" ] && [ -f "$BOOM/infra/single/compose.analyse.yaml" ] || {
  fout "$BOOM heeft geen api/app/analyse/models.lock of infra/single/compose.analyse.yaml (OMZETTER_SRC)"
  exit 1
}

# --- install.sh als bibliotheek, met eigen mappen ----------------------------------------------
# Zoals op de LXC na analyse_dirs: MODELLEN_ROOT een map van root (0755), en een DATA_ROOT. Het
# compose-project heeft een eigen naam (COMPOSE_PROJECT_NAME gaat boven `name: platform`)
export PLATFORM_ROOT=$WERK/platform INSTALL_LIB=1
unset OFFICE OFFICE_IMAGE MEDIA_SANDBOX PLAATSEN ANALYSE ANALYSE_WORKER_KEY MODELLEN_ROOT SLEUTELS_ROOT BEELD
# shellcheck source=/dev/null
. "$install_sh"
set -euo pipefail
SRC=$BOOM
ENV_FILE=$WERK/.env
MODELLEN_ROOT=$WERK/modellen
DATA_ROOT=$WERK/data
SLEUTELS_ROOT=$WERK/sleutels
BEELD=aan
export COMPOSE_PROJECT_NAME=omzettertest-$$
mkdir -m 0755 -- "$MODELLEN_ROOT" "$DATA_ROOT"
mkdir -m 0700 -- "$SLEUTELS_ROOT"
cat >"$ENV_FILE" <<ENV
DATA_ROOT=$DATA_ROOT
ANALYSE=aan
MODELLEN_ROOT=$MODELLEN_ROOT
SLEUTELS_ROOT=$SLEUTELS_ROOT
BEELD=aan
ENV
chmod 600 "$ENV_FILE"
cat >"$WERK/test.yaml" <<YAML
services:
  analyse-omzetten:
    image: $IMAGE
YAML

compose_files 0 0 0 1
compose+=(-f "$WERK/test.yaml")

# --- de dienst zoals compose hem start ----------------------------------------------------------
# Uit de echte bestanden (met dit image): wat de omzetter mag en niet mag. Zonder OMZET_BRON en
# OMZET_UIT wijzen de twee mappen naar /nonexistent; dat doet hier niet ter zake
if dienst=$("${compose[@]}" --profile omzetten config --format json 2>"$WERK/config.err" |
  python3 -I -c '
import json, sys
s = json.load(sys.stdin)["services"]["analyse-omzetten"]
tmp = [t for t in s.get("tmpfs", []) if t.split(":")[0] == "/tmp"]
mem = s.get("mem_limit")
print(" ".join([
    "user", str(s.get("user")),
    "read_only" if s.get("read_only") else "SCHRIJFBAAR",
    "network", str(s.get("network_mode")),
    "tmpfs", ",".join(tmp) or "GEEN",
    "cap_drop", ",".join(s.get("cap_drop", [])) or "GEEN",
    ",".join(s.get("security_opt", [])) or "GEEN-security_opt",
    "mem_limit", str(int(mem) // 2**20) + "MiB" if mem else "geen",
    "cpus", str(s.get("cpus", "geen")),
]))
'); then
  case "$dienst" in
    "user omzetten read_only network none tmpfs /tmp:size=64m,mode=1777 cap_drop ALL no-new-privileges:true "*)
      goed "de dienst analyse-omzetten: $dienst" ;;
    *) fout "de dienst analyse-omzetten is niet afgeschermd zoals compose.analyse.yaml zegt: $dienst" ;;
  esac
else
  fout "compose config: $(tail -n 2 "$WERK/config.err" | tr '\n' ' ')"
  exit 1
fi

# --- beeld_model, zoals install.sh het na de start van de app doet ------------------------------
schijf vooraf
begin=$(date +%s)
{
  beeld_model
  echo "omzettertest: BEELD_STATE=$BEELD_STATE"
} 2>&1 | tee "$WERK/beeld.txt" || true
duur=$(($(date +%s) - begin))
state=$(sed -n 's/^omzettertest: BEELD_STATE=//p' "$WERK/beeld.txt" | tail -n 1)
klaar=$(grep -E '^omzetten: klaar in ' "$WERK/beeld.txt" | tail -n 1 || true)
schijf "na beeld_model"
if [ "$state" = nieuw ] && [ -n "$klaar" ]; then
  goed "beeld_model: het model staat klaar (BEELD_STATE nieuw, ${duur} s met ophalen en nakijken); ${klaar#omzetten: }"
else
  fout "beeld_model: BEELD_STATE ${state:-leeg}, niet nieuw (${duur} s); de laatste regels: $(grep -v '^omzettertest: ' "$WERK/beeld.txt" | tail -n 3 | tr '\n' ' ')"
fi

# --- de uitkomst, opnieuw nagekeken tegen models.lock ------------------------------------------
lock=$SRC/api/app/analyse/models.lock
doel=$MODELLEN_ROOT/$BEELD_MAP
verwacht=()
slecht=()
while read -r sha bytes naam url _; do
  case "$sha" in '' | '#'*) continue ;; esac
  case "$naam" in "$BEELD_MAP"/bron/*) continue ;; "$BEELD_MAP"/*) ;; *) continue ;; esac
  [ "$url" = - ] || continue
  base=${naam#"$BEELD_MAP"/}
  verwacht+=("$base")
  f=$doel/$base
  if [ -L "$f" ] || [ ! -f "$f" ]; then
    slecht+=("$base ontbreekt of is geen gewoon bestand")
    continue
  fi
  echt_sha=$(sha256sum <"$f" | cut -d' ' -f1)
  echt_bytes=$(stat -c %s -- "$f")
  eigen=$(stat -c '%u:%g %a' -- "$f")
  if [ "$echt_bytes" != "$bytes" ] || [ "$echt_sha" != "$sha" ]; then
    slecht+=("$base: $echt_bytes bytes, sha256 $echt_sha; models.lock zegt $bytes en $sha")
  elif [ "$eigen" != "0:0 644" ]; then
    slecht+=("$base: eigenaar en rechten $eigen in plaats van 0:0 644")
  else
    echo "  $echt_sha $echt_bytes $naam (gelijk aan models.lock)"
  fi
done <"$lock"
er=$(find "$doel" -mindepth 1 -maxdepth 1 -printf '%f\n' 2>/dev/null | sort || true)
lijst=$(if [ "${#verwacht[@]}" -gt 0 ]; then printf '%s\n' "${verwacht[@]}" | sort; fi)
if [ "${#verwacht[@]}" = 0 ]; then
  fout "models.lock heeft geen uitkomst van $BEELD_MAP (regels met url -)"
elif [ ! -d "$doel" ]; then
  fout "MODELLEN_ROOT/$BEELD_MAP staat er niet: beeld_model zette geen model neer"
elif [ "${#slecht[@]}" -gt 0 ]; then
  fout "de uitkomst klopt niet met models.lock: ${slecht[*]}"
elif [ "$er" != "$lijst" ]; then
  fout "in $BEELD_MAP staat iets anders dan de uitkomst van models.lock: $(echo "$er" | tr '\n' ' ')"
elif [ "$(stat -c '%u:%g %a' -- "$doel")" != "0:0 755" ]; then
  fout "$BEELD_MAP is $(stat -c '%u:%g %a' -- "$doel"), niet een map van root (0755)"
else
  goed "de uitkomst: ${#verwacht[@]} bestanden, precies die van models.lock, elk met zijn grootte en sha256, van root (0644)"
fi
# Ook na een mislukte omzetting: beeld_model ruimt zijn map (.beeld.*, met de bron) altijd op
rest=$(find "$MODELLEN_ROOT" -mindepth 1 -maxdepth 1 ! -name "$BEELD_MAP" -printf '%f ' || true)
if [ -n "$rest" ]; then
  fout "in MODELLEN_ROOT staat nog: $rest"
elif [ -e "$doel/bron" ] || [ -L "$doel/bron" ]; then
  fout "in $BEELD_MAP staat nog een bron"
else
  goed "geen bron en geen tijdelijke map achter in MODELLEN_ROOT"
fi

if [ "$FAILED" = 0 ]; then echo "Alle controles van de omzetter met de echte bron geslaagd."; fi
