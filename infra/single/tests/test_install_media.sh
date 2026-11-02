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
# - de voorcontrole van --stappen (met een nep-docker; er wordt niets gebouwd): een
#   INSTALL_TEST_DIR die de test weigert (niet leeg, relatief, een bestand) blijft zoals hij was,
#   een lege blijft leeg, de eigen werkmap onder /tmp gaat weg, en het blok Samenvatting staat
#   altijd als laatste.
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
# Het bestand van stap 2 van "De belastingtest" in docs/runbooks/media.md, precies dat blok (de
# functies controleren het met compose config, de installatietest gebruikt het; review golf 5, S2)
belastingtest_override() {
  awk '/^ *cat >\/root\/belastingtest\.yaml <<.EOF.$/ { on = 1; next }
       on && /^ *EOF$/ { exit }
       on { sub(/^   /, ""); print }' "$top/docs/runbooks/media.md"
}

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
        if "PLATFORM_MEDIA_LOADTEST" in (w.get("environment") or {}):
            problems.append("media: de belastingtest staat standaard aan")
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
    # De belastingtest (review golf 5, S2): het bestand uit het runbook zet hem aan in de app én
    # in de worker. Alleen in de app weigert de worker synth, en de test faalt altijd
    LIMITS=1
    compose_files 0 1 0
    files=("${compose[@]:1}")
    belastingtest_override >"$work/belastingtest.yaml"
    command docker "${files[@]}" -f "$work/belastingtest.yaml" config --format json \
      2>"$work/config.err" >"$work/config.json" || true
    out=$(python3 -I - "$work/config.json" <<'PY'
import json, sys

try:
    s = json.load(open(sys.argv[1]))["services"]
except (OSError, ValueError, KeyError) as e:
    print("geen config (%s)" % type(e).__name__)
    raise SystemExit
missing = [
    n for n in ("platform", "media")
    if ((s.get(n) or {}).get("environment") or {}).get("PLATFORM_MEDIA_LOADTEST") != "1"
]
print("ok" if not missing else "zonder PLATFORM_MEDIA_LOADTEST: " + ", ".join(missing))
PY
    ) || out="(geen uitvoer van compose config)"
    check "de belastingtest uit het runbook: aan in platform en media ($out)" test "$out" = ok
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

  # Terug naar 0.6.0 (integratie deel 3): het runbook en de README geven Jonas precies de
  # commando's van Keuze 47, in die volgorde, met de volledige hash (stap 4 van --stappen draait
  # ze). De tag v0.6.0 is een keuze van de lead: de commando's werken zonder, en een tekst die hem
  # noemt, zegt "als die er is". Het script komt vóór het stoppen in een bestand, en stap 4 draait
  # dat bestand (review van de integratie, C2/K2): `curl … | bash` met een adres dat er niet is
  # (main zolang 0.7.0 op de branch staat), eindigt met 0 zonder iets te doen, en dan stond de app
  # al stil met het schema op 0007. Het adres mag main of de branch zijn; met de branch hoort
  # PLATFORM_BRANCH erbij, met main niet.
  echo
  echo "== terug naar 0.6.0: de commando's van Keuze 47 in het runbook en de README"
  out=$(python3 -I - "$top/docs/runbooks/media.md" "$here/../README.md" <<'PY'
import re
import sys

SHA = "263abd74d914a9811901e7570ed410866364f4b5"
BRANCH = "claude/stappenplan-uitwerken-lu4uv3"
RAW = "https://raw.githubusercontent.com/Jonasz1996/alles-in-1-file-office-media-platform"
FILE = "/root/platform-install.sh"
WANT = [
    ("git fetch", re.escape("git -C /opt/platform/src fetch -q origin")),
    ("cat-file met de hash", re.escape("git -C /opt/platform/src cat-file -e '%s^{commit}' && echo " % SHA)),
    (
        "curl -o " + FILE,
        re.escape("curl -fsSL %s/" % RAW)
        + "(?P<ref>main|"
        + re.escape(BRANCH)
        + ")"
        + re.escape("/infra/single/install.sh -o %s && echo " % FILE),
    ),
    ("dc stop", re.escape("dc stop platform media")),
    ("db-downgrade", re.escape("dc run --rm --no-deps platform python -m app.cli db-downgrade 0007")),
    (
        "bash < " + FILE + " met de hash",
        "(?P<pre>(PLATFORM_BRANCH=\\S+ )?)" + re.escape("PLATFORM_SRC_REF=%s bash < %s" % (SHA, FILE)),
    ),
]
problems = []
for path in sys.argv[1:]:
    name = path.rsplit("/", 2)[-1]
    lines = [line.strip() for line in open(path, encoding="utf-8")]
    at = -1
    found = {}
    for label, pat in WANT:
        hits = [(i, m) for i, line in enumerate(lines) if i > at for m in [re.match(pat + "(?:$| |(?<=echo ))", line)] if m]
        if not hits:
            problems.append("%s mist (of niet in volgorde): %s" % (name, label))
            continue
        at, m = hits[0]
        found.update({k: v for k, v in m.groupdict().items() if v is not None})
    want_pre = "" if found.get("ref") == "main" else "PLATFORM_BRANCH=%s " % BRANCH
    if "ref" in found and "pre" in found and found["pre"] != want_pre:
        problems.append("%s: PLATFORM_BRANCH past niet bij het adres (%s)" % (name, found["ref"]))
    for line in lines:
        if "v0.6.0" in line and "als die er is" not in line:
            problems.append("%s noemt v0.6.0 zonder 'als die er is'" % name)
        if "PLATFORM_SRC_REF=" in line and "| " in line and "bash" in line.split("PLATFORM_SRC_REF=", 1)[1]:
            problems.append("%s: PLATFORM_SRC_REF met curl | bash, niet met het bestand" % name)
print("; ".join(problems) or "ok")
PY
  ) || out="(geen uitvoer)"
  check "de commando's van Keuze 47 met de volledige hash, in het runbook en de README ($out)" test "$out" = ok

  # De nooduitgang zonder afscherming (review van de integratie, S1): `MEDIA_SANDBOX=uit bash`
  # staat in de docs alleen waar binnen 8 regels "tijdelijk" staat, dus nooit als los voorbeeld
  # van "een instelling erbij".
  echo
  echo "== MEDIA_SANDBOX=uit alleen bij de nooduitgang, met 'tijdelijk'"
  out=$(python3 -I - "$top/README.md" "$here/../README.md" "$top/docs/runbooks/media.md" "$top/docs/fase-5/README.md" <<'PY'
import os
import sys

problems = []
for path in sys.argv[1:]:
    name = os.path.relpath(path, os.path.dirname(sys.argv[1]))
    lines = open(path, encoding="utf-8").read().splitlines()
    for i, line in enumerate(lines):
        if "MEDIA_SANDBOX=uit bash" in line and not any("tijdelijk" in x for x in lines[i : i + 9]):
            problems.append("%s r. %d zonder 'tijdelijk'" % (name, i + 1))
print("; ".join(problems) or "ok")
PY
  ) || out="(geen uitvoer)"
  check "MEDIA_SANDBOX=uit in de docs alleen met een waarschuwing ($out)" test "$out" = ok

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

  # De trap van --stappen staat vóór de voorcontrole (de samenvatting komt er altijd) en haalt
  # WORK weg, als root. Een map die de test weigert, mag daarbij nooit verdwijnen (review van
  # 1bf5b18: INSTALL_TEST_DIR=<niet leeg> werd na de STOP gewist). Alleen de voorcontrole draait:
  # een nep-docker zonder project en zonder images, een nep-ss, een kleine git-repo en
  # INSTALL_TEST_FROM die er niet in staat; elke run stopt vóór er iets gebouwd wordt
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
      "$@" bash "$here/test_install_media.sh" --stappen 2>&1)
  }
  # STOP met die tekst en exit 1; het blok staat als laatste, met de stap waarin de test stopte
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
  # Een lege map die al bestond: de test gebruikt hem en laat hem leeg achter (STOP bij de versie)
  code=0
  out=$(vooraf INSTALL_TEST_DIR="$nep/leeg") || code=$?
  check "lege map: de test gebruikt hem" grep -qF "werkmap $nep/leeg)" <<<"$out"
  check "lege map: STOP pas bij de versie" stopped_with "versie 0000000000000000000000000000000000000000 staat niet in $nep/repo"
  check "lege map: hij blijft, leeg" empty_dir "$nep/leeg"
  # Zonder INSTALL_TEST_DIR: een eigen map onder /tmp, en die is daarna weg
  code=0
  out=$(vooraf) || code=$?
  own=$(sed -n 's/^== Voorbereiden (.*, werkmap \(\/tmp\/install-media\.[^)]*\))$/\1/p' <<<"$out")
  check "eigen werkmap: STOP pas bij de versie" stopped_with "versie 0000000000000000000000000000000000000000 staat niet in $nep/repo"
  check "eigen werkmap: gemaakt (${own:-geen}) en daarna weg" gone "$own"
  check "eigen werkmap: de samenvatting als laatste" summary_last

  einde
}

# ============================================================================================
# Deel 2: de installatietest (--stappen)
# ============================================================================================

# De installatietest (bouwontwerp fase 5, §2 M10, "De installatietest"), zoals
# test_install_update.sh van fase 4: de echte install.sh in een container met Debian 12 (de
# "LXC"), met de docker-socket en de docker-cli van de host, en de werkmap op hetzelfde absolute
# pad. Office blijft uit (OFFICE=uit): dat bewijst test_install_update.sh. Per omgeving:
#
#   | Stap | Sandbox | CI (media-image) |
#   |---|---|---|
#   | 1. 0.6.0 installeren (263abd7), een film uploaden | ja | ja |
#   | 2. bijwerken: 0008 vanzelf, de app en de worker draaien, office zoals in 0.6.0, media raakt niets aan (geen bibliotheek, import of provider) | ja; de film komt nog heel terug, /beheer toont media_ffmpeg_missing, play 503 media_ffmpeg_missing | ja; plus een transcode via curl en docker top, de rooktest van de belastingtest |
#   | 2b. nog een keer bijwerken (een commit erbovenop) | één image platform:local, de oude id weg | idem |
#   | 3. MEDIA_SANDBOX=uit: de container weg, de lokale worker | ja | ja |
#   | 4. terug naar 0.6.0 met precies de commando's van Keuze 47 | ja; schema 0007 en de app 0.6.0 | — (INSTALL_TEST_ROLLBACK=0) |
#
# - Kan een container geen Debian-spiegel bereiken (de sandbox), dan krijgt python:3.12-slim een
#   leeg pakket ffmpeg, met lege /usr/bin/ffmpeg en /usr/bin/ffprobe (het Dockerfile zet die op
#   0711; uitvoeren geeft ENOEXEC, dus "ffmpeg ontbreekt"), zoals test_install_update.sh:53–55.
#   Wat ffmpeg nodig heeft (de transcode, docker top, de belastingtest), bewijst CI.
# - Staat er al een python:3.12-slim van een ander, dan leent de test die naam: het image krijgt
#   even een tweede naam, en aan het eind (ook bij een fout) weer zijn eigen naam. Het image zelf
#   blijft altijd.
# - De tweede update (2b) is een commit die de test zelf maakt, alleen in zijn eigen kale kloon:
#   een leeg bestand in api/app, zodat het image verandert terwijl de worker nog op het vorige
#   draait (R18 van de review).
# - De film maakt de ffmpeg van de host (of, zonder, die van het image van 0.6.0): 120 s
#   640×360 H.264 met 5 Mbit/s en AAC. De transcode vraagt een profiel met hooguit 1000 kbit/s.
# - Geen geheim in een log: de login van de beheerder staat alleen in de werkmap (sessie.json,
#   600); de test kijkt na dat MEDIA_WORKER_KEY in geen enkele log van install.sh staat.
# - De images van de app: elk image dat platform:local in deze test was (de test onthoudt het na
#   elke run van install.sh), en wat compose met het label van het project platform bouwde
#   (compose 2.38 in CI bouwt via bake en zet dat label niet; nieuwere wel). Ook een oud image
#   dat zijn naam kwijt is (<none>, zoals bij de klassieke opslag van Docker in CI; met de opslag
#   van containerd, hier, is dat image meteen weg). Wat vóór de test al de naam platform had
#   (PRE_PLATFORM; in CI bouwt de job media-image eerst platform:ci), is van een ander: de test
#   telt het niet en ruimt het niet op. Elk ander image met de naam platform dat tijdens de test
#   verschijnt, telt wel mee voor "één image van de app" (strenger dan de oude telling), maar de
#   opruiming laat het staan: het kan van een ander zijn.
# - Het blok "=== Samenvatting" staat altijd als laatste in het log (de stap Samenvatting van CI
#   leest het), ook als de test afbrak: met het aantal controles en de stap waarin hij stopte.
#   Een werkmap die de test weigert (INSTALL_TEST_DIR niet leeg, geen map of geen absoluut pad),
#   blijft daarbij zoals hij was; de functies hierboven controleren dat.
# - De sid van play (een sleutel in het pad, PlayOut) staat in geen log: logs/2-play.json heeft
#   <sid> waar de sid stond; het antwoord zelf staat alleen in de werkmap.
#
# Instellingen (alle optioneel), zoals test_install_update.sh: INSTALL_TEST_DIR, _REPO, _FROM
# (standaard 263abd74d914a9811901e7570ed410866364f4b5), _TO (standaard HEAD), _PORT (18580),
# _HTTPS_PORT (18543), _LOGS, _CA, _UV, _NEED_GB (standaard 8), en INSTALL_TEST_ROLLBACK (1 = stap
# 4 ook; CI zet 0).
# shellcheck disable=SC2016  # check … eval '…': eval werkt de uitdrukking pas zelf uit
stappen() {
  REPO_SRC=${INSTALL_TEST_REPO:-$top}
  FROM=${INSTALL_TEST_FROM:-263abd74d914a9811901e7570ed410866364f4b5}
  TO=${INSTALL_TEST_TO:-$(git -C "$REPO_SRC" rev-parse HEAD)}
  PORT=${INSTALL_TEST_PORT:-18580}
  HTTPS_PORT=${INSTALL_TEST_HTTPS_PORT:-18543}
  LOGS_OUT=${INSTALL_TEST_LOGS:-}
  EXTRA_CA=${INSTALL_TEST_CA-}
  if [ -z "${INSTALL_TEST_CA+x}" ] && [ -f /root/.ccr/ca-bundle.crt ]; then EXTRA_CA=/root/.ccr/ca-bundle.crt; fi
  UV_FROM=${INSTALL_TEST_UV:-ghcr}
  NEED_GB=${INSTALL_TEST_NEED_GB:-8}
  ROLLBACK=${INSTALL_TEST_ROLLBACK:-1}
  PROJECT=platform
  RUNNER_NAME=install-media-runner-$$
  RUNNER_IMG=install-media-runner:$$
  BORROWED=installatietest-bewaard/python:3.12-slim-$$
  # WORK blijft leeg tot de werkmap van de test is: de trap hieronder haalt WORK weg (als root)
  runner_built=0 borrowed=0 own_project=0 work_existed=0 WORK=""
  created=() maybe=() SUMMARY=() APP_IDS=() PRE_PLATFORM=""
  STAGE=vooraf ENDED=0
  # Vanaf hier altijd de samenvatting aan het eind, ook bij een STOP vóór er iets gebeurde
  trap stappen_opruimen EXIT
  trap 'exit 130' INT TERM

  stop() {
    echo "STOP  $1" >&2
    exit 1
  }
  have() { docker image inspect "$1" >/dev/null 2>&1; }
  # De images die de test maakte en nog zijn, ook zonder naam (zie boven): APP_IDS en het label
  # van het project, maar nooit een image dat vóór de test al de naam platform had (PRE_PLATFORM,
  # in CI platform:ci; ook niet als een build toevallig dezelfde id gaf). Dit haalt de opruiming
  # weg. Een inspect van een id dat weg is, print een lege regel: die valt weg
  own_images() {
    local id
    {
      docker images -a -q --no-trunc --filter "label=com.docker.compose.project=$PROJECT"
      for id in "${APP_IDS[@]}"; do docker image inspect --format '{{.Id}}' "$id" 2>/dev/null || true; done
    } | sed '/^$/d' | sort -u | comm -23 - <(printf '%s\n' "$PRE_PLATFORM" | sort -u)
  }
  # Wat "één image van de app" telt: own_images, de ids van platform:local (ook als die vóór de
  # test al bestonden), en elk image met de naam platform (welke tag ook) dat er vóór de test niet
  # was. Strenger dan de oude telling van `docker images platform`, zonder platform:ci. De
  # opruiming haalt alleen own_images weg: een nieuw platform:<iets> kan van een ander zijn
  app_images() {
    local id
    {
      own_images
      for id in "${APP_IDS[@]}"; do docker image inspect --format '{{.Id}}' "$id" 2>/dev/null || true; done
      docker images -q --no-trunc platform | sort -u | comm -23 - <(printf '%s\n' "$PRE_PLATFORM" | sort -u)
    } | sed '/^$/d' | sort -u
  }
  # platform:local onthouden, na elke run van install.sh
  note_app() {
    local id
    if id=$(docker image inspect --format '{{.Id}}' platform:local 2>/dev/null); then APP_IDS+=("$id"); fi
  }
  # Wat install.sh in de runner als root maakte (.env 600, de data van 10001, de broncode), leest
  # de test op de host als root: in CI met sudo
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

  # De werkmap. WORK krijgt pas een waarde als de map van de test is: een map die de test
  # weigert (geen absoluut pad, geen map, niet leeg), blijft zoals hij was. Een lege map die al
  # bestond, blijft ook; alleen wat de test erin zette, gaat weg
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
    dir=$(mktemp -d /tmp/install-media.XXXXXX)
  fi
  WORK=$(cd "$dir" && pwd -P)
  ROOT=$WORK/platform
  own_project=1
  mkdir -p "$WORK/logs" "$WORK/shim/bin" "$WORK/build" "$WORK/film"

  # --- de broncode: beide versies, en een tweede versie erbovenop (2b) ----------------------
  echo "== Voorbereiden (${FROM:0:12} -> ${TO:0:12}, werkmap $WORK)"
  git clone -q --bare "$REPO_SRC" "$WORK/repo.git"
  local c
  for c in "$FROM" "$TO"; do
    git -C "$WORK/repo.git" cat-file -e "$c^{commit}" 2>/dev/null || stop "versie $c staat niet in $REPO_SRC"
  done
  local blob tree
  GIT_INDEX_FILE=$WORK/index git -C "$WORK/repo.git" read-tree "$TO"
  blob=$(printf 'installatietest: de tweede update\n' | git -C "$WORK/repo.git" hash-object -w --stdin)
  GIT_INDEX_FILE=$WORK/index git -C "$WORK/repo.git" update-index --add --cacheinfo "100644,$blob,api/app/installatietest.txt"
  tree=$(GIT_INDEX_FILE=$WORK/index git -C "$WORK/repo.git" write-tree)
  TO2=$(GIT_AUTHOR_NAME=installatietest GIT_AUTHOR_EMAIL=test@invalid GIT_COMMITTER_NAME=installatietest \
    GIT_COMMITTER_EMAIL=test@invalid git -C "$WORK/repo.git" commit-tree "$tree" -p "$TO" -m "installatietest: de tweede update")
  # Elke versie op een eigen ref: een fetch in de kloon van install.sh vindt ze dan ook
  git -C "$WORK/repo.git" update-ref refs/heads/installatietest-van "$FROM"
  git -C "$WORK/repo.git" update-ref refs/heads/installatietest-naar "$TO"
  git -C "$WORK/repo.git" update-ref refs/heads/installatietest-naar2 "$TO2"
  git -C "$WORK/repo.git" show "$TO:infra/single/install.sh" >"$WORK/install.sh"
  # install.sh draait in de runner als root, maar de kale kloon is van de gebruiker van de test
  # (in CI de runner, niet root). git weigert dan te klonen ("dubious ownership"). Deze ene map
  # vertrouwen via een eigen globale config: GIT_CONFIG_GLOBAL gaat, anders dan -c of
  # GIT_CONFIG_COUNT, ook mee naar upload-pack van een lokale kloon. Op de LXC bestaat dit niet:
  # daar komt de broncode van GitHub.
  printf '[safe]\n\tdirectory = %s\n' "$WORK/repo.git" >"$WORK/gitconfig"

  # --- images (zoals test_install_update.sh) -----------------------------------------------
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
# lege bestanden die het pakket meebrengt (ffmpeg: /usr/bin/ffmpeg en /usr/bin/ffprobe, die het
# Dockerfile van fase 5 op 0711 zet; uitvoeren geeft ENOEXEC, dus "ffmpeg ontbreekt")
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
  if [ "$apt_ok" = 1 ]; then
    FFMPEG=echt
  else
    FFMPEG=leeg
    echo "  geen Debian-spiegel bereikbaar: een leeg pakket ffmpeg (en unattended-upgrades)"
  fi
  if [ "$apt_ok" = 1 ] && [ -z "$ca" ]; then
    name_as mirror.gcr.io/library/python:3.12-slim python:3.12-slim
  else
    if have python:3.12-slim; then
      # Van een ander: even een tweede naam, aan het eind weer de eigen naam (stappen_opruimen)
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
        echo "RUN sh /tmp/leeg-pakket.sh ffmpeg /usr/bin/ffmpeg /usr/bin/ffprobe && rm /tmp/leeg-pakket.sh"
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
  # Geeft dockerd minder dan de 65536 open bestanden van compose.yaml (de sandbox), dan krijgt
  # elke compose-aanroep één extra bestand met de grens die dockerd wel geeft (alleen de app)
  SANDBOX_COMPOSE=""
  if ! docker run --rm --ulimit nofile=65536:65536 mirror.gcr.io/library/python:3.12-slim true </dev/null >/dev/null 2>&1; then
    local nofile
    nofile=$(sed -n 's/^Max open files *[0-9]* *\([0-9]*\).*/\1/p' "/proc/$(pidof dockerd | cut -d' ' -f1)/limits" 2>/dev/null || true)
    [ -n "$nofile" ] || nofile=$(ulimit -Hn)
    SANDBOX_COMPOSE=$WORK/build/sandbox-nofile.yaml
    printf '# Alleen voor test_install_media.sh: dockerd geeft hier hooguit %s open bestanden\nservices:\n  platform:\n    ulimits:\n      nofile: {soft: %s, hard: %s}\n' \
      "$nofile" "$nofile" "$nofile" >"$SANDBOX_COMPOSE"
    echo "  dockerd geeft hier geen 65536 open bestanden: de app krijgt er in deze test $nofile"
  fi

  # Alleen in de runner: compose krijgt het extra bestand van de sandbox na de laatste -f
  cat >"$WORK/shim/bin/docker" <<'SHIM'
#!/usr/bin/env bash
real=/usr/bin/docker
if [ "${1-}" = compose ] && [ -n "${SHIM_COMPOSE_EXTRA:-}" ]; then
  args=("$@") at=-1
  for i in "${!args[@]}"; do
    if [ "${args[i]}" = -f ] && [[ ${args[i + 1]-} == *.yaml ]]; then at=$((i + 1)); fi
  done
  if [ "$at" -ge 0 ]; then exec "$real" "${args[@]:0:at+1}" -f "$SHIM_COMPOSE_EXTRA" "${args[@]:at+1}"; fi
fi
exec "$real" "$@"
SHIM
  chmod 755 "$WORK/shim/bin/docker"

  # --- hulpjes ------------------------------------------------------------------------------
  cid() {
    docker ps -aq --filter "label=com.docker.compose.project=$PROJECT" \
      --filter "label=com.docker.compose.service=$1" | head -n 1
  }
  running() { [ -n "$1" ] && [ "$(docker inspect --format '{{.State.Running}}' "$1" 2>/dev/null)" = true ]; }
  env_value() { as_root sed -n "s/^$1=//p" "$ROOT/.env"; }
  src_head() { as_root git -c safe.directory='*' -C "$ROOT/src" rev-parse HEAD; }
  container_env() { docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$1" | sed -n "s/^$2=//p"; }
  has_env() { docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$1" | grep -q "^$2="; }
  same_key() { [ "$(container_env "$1" PLATFORM_MEDIA_WORKER_KEY | sha256sum)" = "$(env_value MEDIA_WORKER_KEY | sha256sum)" ]; }
  app_up() { curl -fs -o /dev/null -m 5 "http://127.0.0.1:$PORT/health"; }
  api() {
    INSTALL_API_URL="http://127.0.0.1:$PORT" INSTALL_API_STATE="$WORK/sessie.json" \
      python3 -I "$top/e2e/install_api.py" "$@"
  }
  said() { grep -qF -- "$2" "$WORK/logs/$1.log"; }
  db_rev() {
    docker exec "$(cid platform)" python -c 'import sqlite3
c = sqlite3.connect("file:/data/platform.db?mode=ro", uri=True)
print(c.execute("select version_num from alembic_version").fetchone()[0])' </dev/null 2>/dev/null
  }
  # Wat app_images vindt, leesbaar (naam en het begin van de id), voor de naam van een controle
  app_images_text() {
    local id tags out=()
    for id in $(app_images); do
      tags=$(docker image inspect --format '{{join .RepoTags ","}}' "$id" 2>/dev/null) || continue
      out+=("${tags:-<none>} ${id:7:12}")
    done
    local IFS=,
    echo "${out[*]}"
  }
  # Precies één image van de app, en dat is platform:local (dat er dus moet zijn)
  one_app_image() {
    local id
    id=$(docker image inspect --format '{{.Id}}' platform:local 2>/dev/null) || return 1
    [ -n "$id" ] && [ "$(app_images)" = "$id" ]
  }
  image_gone() { ! docker image inspect "$1" >/dev/null 2>&1; }
  # Eén run van install.sh in de runner; het script komt via stdin (zoals curl | bash)
  step() {
    local name=$1 rc=0 kv envs=()
    shift
    for kv in "$@"; do envs+=(-e "$kv"); done
    echo
    echo "== $name: install.sh $*"
    timeout 1800 docker run -i --rm --name "$RUNNER_NAME" --network host \
      -v /var/run/docker.sock:/var/run/docker.sock \
      -v "$(command -v docker)":/usr/bin/docker:ro \
      -v /usr/libexec/docker/cli-plugins:/usr/libexec/docker/cli-plugins:ro \
      -v "$WORK:$WORK" \
      -e PLATFORM_REPO="$WORK/repo.git" -e PLATFORM_ROOT="$ROOT" -e OFFICE=uit \
      -e GIT_CONFIG_GLOBAL="$WORK/gitconfig" \
      -e PORT="$PORT" -e HTTPS_PORT="$HTTPS_PORT" -e SHIM_COMPOSE_EXTRA="$SANDBOX_COMPOSE" \
      -e PATH="$WORK/shim/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
      "${envs[@]}" "$RUNNER_IMG" bash -s <"$WORK/install.sh" >"$WORK/logs/$name.log" 2>&1 || rc=$?
    note_app
    if [ "$rc" != 0 ]; then
      echo "install.sh gaf $rc; de laatste regels:"
      tail -n 30 "$WORK/logs/$name.log"
    fi
    sed -n 's/^\(Media: .*\)/  \1/p; s/^Let op: \(.*\)/  Let op: \1/p' "$WORK/logs/$name.log"
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
  wait_healthy() {
    local end=$((SECONDS + $2))
    until [ "$(docker inspect --format '{{.State.Health.Status}}' "$1" 2>/dev/null)" = healthy ]; do
      [ "$SECONDS" -lt "$end" ] || return 1
      sleep 2
    done
  }
  # ffmpeg in een container volgens docker top (de kolom pid is nodig voor het filter)
  ffmpeg_in() { docker top "$1" -eo pid,comm 2>/dev/null | awk 'NR > 1 && $2 == "ffmpeg"' | wc -l | tr -d ' '; }
  # Een profiel met H.264 en AAC via MSE, en hooguit 1000 kbit/s: de film (5 Mbit/s) wordt een
  # transcode (reden bitrate), zoals in de belastingtest. De argumenten: lijsten van strings
  # (JSON), zoals candidates en item van GET …/codecs
  profile() {
    python3 -I -c '
import json, sys
strings = list(dict.fromkeys(s for a in sys.argv[1:] for s in json.loads(a)))
def plays(s):
    low = s.lower()
    return low.startswith(("avc1.", "mp4a.40.")) and not low.startswith("avc1.6e")
codecs = {s: ({"mse": True, "file": True, "smooth": True, "power": True} if plays(s) else {"mse": False, "file": False}) for s in strings}
print(json.dumps({"profile": {"v": 1, "base": "web", "client": {"kind": "web", "name": "installatietest"}, "max": {"height": 1080, "bitrate_kbps": 1000}, "codecs": codecs}}))
' "$@"
  }

  # --- 1. 0.6.0 installeren, een film uploaden --------------------------------------------
  STAGE=1
  step 1-van PLATFORM_SRC_REF="$FROM" || stop "de installatie van $FROM lukte niet"
  check "1: de app draait op poort $PORT" app_up
  check "1: de broncode staat op ${FROM:0:12}" test "$(src_head)" = "$(git -C "$WORK/repo.git" rev-parse "$FROM^{commit}")"
  check "1: het schema staat op 0007 (0.6.0)" test "$(db_rev)" = 0007
  check "1: geen container media" test -z "$(cid media)"
  check "1: install.sh zegt dat deze versie geen media heeft" said 1-van "Media: uit, want deze versie van het platform heeft nog geen media."
  check "1: .env zonder media" test -z "$(env_value MEDIA_WORKER_KEY)"
  local app1 film root film_id film_sha
  app1=$(docker image inspect --format '{{.Id}}' platform:local)
  check "1: een beheerder aangemaakt, met 2FA aan" api setup
  film=$WORK/film/film.mp4
  local lavfi=(-hide_banner -loglevel error -f lavfi -i "testsrc2=size=640x360:rate=25" -f lavfi -i "sine=frequency=440:sample_rate=48000" -t 120 -c:v libx264 -preset ultrafast -b:v 5M -maxrate 5M -bufsize 10M -pix_fmt yuv420p -g 50 -c:a aac -b:a 128k -movflags +faststart)
  if command -v ffmpeg >/dev/null && ffmpeg "${lavfi[@]}" -y "$film" </dev/null; then
    :
  else
    # Zonder ffmpeg op de host: die van het image van 0.6.0 (in CI echt)
    docker run --rm --user 0 --entrypoint ffmpeg -v "$WORK/film:/film" platform:local "${lavfi[@]}" -y /film/film.mp4 </dev/null ||
      stop "de film maken lukte niet (geen ffmpeg op de host, en niet in het image)"
  fi
  film_sha=$(sha256sum "$film" | cut -d' ' -f1)
  root=$(api get /api/v1/auth/me root_id) || stop "1: /auth/me gaf geen map"
  film_id=$(api upload "$root" film.mp4 "$film") || stop "1: de film uploaden lukte niet"
  check "1: de film staat erin" test "$(api sha "$film_id")" = "$film_sha"
  # Office staat in deze test uit (OFFICE=uit, zie boven); wat 0.6.0 erover zegt, moet na de
  # update hetzelfde zijn (office aan over een update: test_install_update.sh)
  local office1
  office1=$(api get /api/v1/office/status state 2>"$WORK/logs/1-office.err") || office1="(geen antwoord)"
  check "1: /api/v1/office/status zegt off (OFFICE=uit; gezien: $office1)" test "$office1" = off

  # --- 2. bijwerken naar deze versie -----------------------------------------------------
  STAGE=2
  step 2-bijwerken PLATFORM_SRC_REF="$TO" || fout "2: install.sh gaf een fout"
  check "2: de app draait" app_up
  check "2: het schema staat op 0008" test "$(db_rev)" = 0008
  local app media key app2
  app=$(cid platform) media=$(cid media)
  check "2: de container media draait" running "$media"
  check "2: de worker wordt gezond (healthcheck via env -i)" wait_healthy "$media" 120
  check "2: de worker draait als media (10002), zonder netwerk" test \
    "$(docker inspect --format '{{.Config.User}} {{.HostConfig.NetworkMode}} {{.HostConfig.ReadonlyRootfs}}' "$media")" = "media none true"
  check "2: de app praat met de worker over de socket" test "$(container_env "$app" PLATFORM_MEDIA_WORKER)" = /run/media/worker.sock
  key=$(env_value MEDIA_WORKER_KEY)
  check "2: MEDIA_WORKER_KEY in .env, 64 hex-tekens, .env 600" eval '[[ $key =~ ^[0-9a-f]{64}$ ]] && [ "$(as_root stat -c %a "$ROOT/.env")" = 600 ]'
  check "2: de app en de worker hebben dezelfde sleutel" eval 'same_key "$app" && same_key "$media"'
  check "2: de sleutel staat in geen enkele log van install.sh" not grep -rqF -- "$key" "$WORK/logs"
  check "2: MEDIA_SANDBOX=aan en MAX_UPLOAD_BYTES in .env" eval '[ "$(env_value MEDIA_SANDBOX)" = aan ] && [ "$(env_value MAX_UPLOAD_BYTES)" = 53687091200 ]'
  check "2: de app krijgt MAX_UPLOAD_BYTES" test "$(container_env "$app" PLATFORM_MAX_UPLOAD_BYTES)" = 53687091200
  check "2: de mappen media en import, van 10001, 750" test \
    "$(as_root stat -c '%u:%g %a' "$ROOT/data/platform/media" "$ROOT/data/platform/import" | sort -u)" = "10001:10001 750"
  check "2: de eindtekst zegt Media: aan (afgeschermd)" said 2-bijwerken "Media: aan (afgeschermd)."
  check "2: de oude app (0.6.0) is weg (zijn id)" image_gone "$app1"
  check "2: één image van de app, platform:local (gezien: $(app_images_text))" one_app_image
  check "2: de film is er nog, heel" test "$(api sha "$film_id")" = "$film_sha"
  check "2: /beheer: de worker in zijn container" test "$(api get /api/v1/admin/media worker.mode)" = container
  # Office werkt zoals in 0.6.0: dezelfde staat op /api/v1/office/status (hier off)
  local office2
  office2=$(api get /api/v1/office/status state 2>"$WORK/logs/2-office.err") || office2="(geen antwoord)"
  check "2: office zoals in 0.6.0 (/api/v1/office/status: $office2)" test "$office2" = "$office1"
  # Media raakt niets aan tot Jonas het zelf gebruikt (de standaarden van de integratie, en de
  # beslissingskaarten zonder antwoord): geen bibliotheek, geen import, geen sleutel van TMDB en
  # MusicBrainz uit. De film in de hoofdmap is gewoon een bestand, in geen enkele bibliotheek
  local inert
  inert=$(api get /api/v1/admin/media 2>"$WORK/logs/2-beheer.err" | python3 -I -c '
import json, sys
try:
    m = json.load(sys.stdin)
except ValueError:
    print("geen JSON")
    raise SystemExit
problems = []
if m.get("libraries"):
    problems.append("bibliotheken %d" % len(m["libraries"]))
if m.get("import"):
    problems.append("een import")
if any(p.get("key_set") for p in m.get("providers", []) if p.get("name") == "tmdb"):
    problems.append("een sleutel van TMDB")
if (m.get("config") or {}).get("musicbrainz") is not False:
    problems.append("musicbrainz %r" % (m.get("config") or {}).get("musicbrainz"))
print("; ".join(problems) or "ok")
') || inert="(geen antwoord)"
  check "2: media raakt niets aan: geen bibliotheek, geen import, providers uit ($inert)" test "$inert" = ok
  check "2: de film staat in geen bibliotheek (GET /api/v1/media/libraries is leeg)" test "$(api count /api/v1/media/libraries)" = 0
  if [ "$FFMPEG" = leeg ]; then
    check "2: media-status na de start: ffmpeg ontbreekt" said 2-bijwerken "ffmpeg ontbreekt)"
    check "2: /beheer toont media_ffmpeg_missing" api notice media_ffmpeg_missing
    # Afspelen vraagt eerst een probe, en die vraagt ffprobe: zonder werkende ffprobe geeft play
    # een nette weigering, nooit een 500. Een lege ffprobe (ENOEXEC) is sinds de integratie
    # (8940cac, m10.md "Naar de integratie" punt 5) net als een ontbrekende 503
    # media_ffmpeg_missing, en die probe wordt niet bewaard; vroeger 409 media_unavailable.
    # Met het veld mode: geeft play onverwacht toch 200, dan staat alleen de mode in het log,
    # niet de sid (PlayOut: een sleutel in het pad, nooit in een log)
    api post "/api/v1/media/$film_id/play" "$(profile "[]")" mode >"$WORK/logs/2-play-zonder-ffmpeg.log" 2>&1 || true
    check "2: afspelen zonder ffmpeg: 503 media_ffmpeg_missing (de speler biedt Downloaden)" grep -Eq \
      'gaf 503: .*media_ffmpeg_missing' "$WORK/logs/2-play-zonder-ffmpeg.log"
    SUMMARY+=("afspelen zonder ffmpeg: $(grep -Eo 'gaf [0-9]+' "$WORK/logs/2-play-zonder-ffmpeg.log" | head -n 1) $(grep -Eo 'media_[a-z_]+' "$WORK/logs/2-play-zonder-ffmpeg.log" | head -n 1)")
  else
    check "2: media-status na de start: afgeschermd, met ffmpeg" said 2-bijwerken "Media: aan (afgeschermd, ffmpeg "
    check "2: /beheer: geen melding over de worker" eval '! api notice media_ffmpeg_missing && ! api notice media_worker_down'
    # De zelftest staat in worker.selftest (Keuze 45; app/media/types.py, WorkerView). Na de
    # update start de app vóór de worker: de lus van health.py vraagt bij "down" na 1, 2, 4 …
    # hooguit 30 s opnieuw, dus de zelftest kan er even null zijn. Hooguit 60 s wachten
    local selftest
    selftest=$(api wait-field /api/v1/admin/media worker.selftest 60) || selftest="geen"
    # Geen JSON (wait-field gaf niets: "geen") is gewoon nee, zonder traceback
    check "2: /beheer: de zelftest zegt afgeschermd (worker.selftest: $selftest)" python3 -I -c '
import json, sys
try:
    seen = json.loads(sys.argv[1])
except ValueError:
    seen = None
sys.exit(0 if seen == {"ok": True, "sandbox": "on", "problems": []} else 1)
' "$selftest"
    stap_transcode
    stap_belastingtest
  fi
  docker exec "$(cid platform)" python -m app.cli media-status </dev/null >"$WORK/logs/2-media-status.log" 2>&1 || true
  sed 's/^/  media-status: /' "$WORK/logs/2-media-status.log"
  SUMMARY+=("media-status na de update: $(head -n 1 "$WORK/logs/2-media-status.log")")
  [ "$FFMPEG" = leeg ] || check "2: media-status zegt afgeschermd" grep -qF "Media: aan (afgeschermd, ffmpeg " "$WORK/logs/2-media-status.log"

  # --- 2b. nog een keer bijwerken: het oude image weg, ook al draaide de worker erop ---------
  STAGE=2b
  app2=$(docker image inspect --format '{{.Id}}' platform:local)
  check "2b: de worker draait op het image van de app" test "$(docker inspect --format '{{.Image}}' "$(cid media)")" = "$app2"
  step 2b-nogmaals PLATFORM_SRC_REF="$TO2" || fout "2b: install.sh gaf een fout"
  check "2b: de app draait" app_up
  check "2b: na twee updates één image van de app, platform:local (gezien: $(app_images_text))" one_app_image
  check "2b: de oude id is weg" image_gone "$app2"
  check "2b: de worker draait op het nieuwe image" test "$(docker inspect --format '{{.Image}}' "$(cid media)")" = "$(docker image inspect --format '{{.Id}}' platform:local)"
  check "2b: de sleutel bleef dezelfde" test "$(env_value MEDIA_WORKER_KEY)" = "$key"

  # --- 3. MEDIA_SANDBOX=uit: de container weg, de lokale worker ------------------------------
  STAGE=3
  step 3-uit PLATFORM_SRC_REF="$TO2" MEDIA_SANDBOX=uit || fout "3: install.sh gaf een fout"
  check "3: de app draait" app_up
  check "3: geen container media meer" test -z "$(cid media)"
  app=$(cid platform)
  check "3: de app heeft media, zonder PLATFORM_MEDIA_WORKER" eval \
    '[ "$(container_env "$app" PLATFORM_MEDIA_ENABLED)" = true ] && ! has_env "$app" PLATFORM_MEDIA_WORKER'
  check "3: MEDIA_SANDBOX=uit in .env, de sleutel blijft" eval '[ "$(env_value MEDIA_SANDBOX)" = uit ] && [ "$(env_value MEDIA_WORKER_KEY)" = "$key" ]'
  check "3: install.sh zegt: zonder afscherming" said 3-uit "Media: aan, zonder afscherming (MEDIA_SANDBOX=uit"
  check "3: /beheer: de lokale worker" test "$(api get /api/v1/admin/media worker.mode)" = local
  check "3: de film is er nog, heel" test "$(api sha "$film_id")" = "$film_sha"
  # Weer afgeschermd, voor stap 4 (Jonas rolt terug vanaf de gewone stand)
  step 3-aan PLATFORM_SRC_REF="$TO2" MEDIA_SANDBOX=aan || fout "3: install.sh (weer aan) gaf een fout"
  check "3: weer aan: de container media draait" running "$(cid media)"

  # --- 4. terug naar 0.6.0, met precies de commando's van Keuze 47 --------------------------
  STAGE=4
  if [ "$ROLLBACK" = 1 ]; then
    echo
    echo "== 4: terug naar 0.6.0 (docs/runbooks/media.md, Keuze 47)"
    # 0. de commit van 0.6.0 is er, vóór er iets stopt
    if as_root git -C "$ROOT/src" fetch -q origin && as_root git -C "$ROOT/src" cat-file -e "$FROM^{commit}"; then
      ok "4.0: git fetch en cat-file -e '${FROM:0:12}^{commit}'"
    else
      fout "4.0: de commit van 0.6.0 is er niet"
    fi
    # 1. de app en de worker stoppen
    if dc stop platform media >"$WORK/logs/4-stop.log" 2>&1; then ok "4.1: dc stop platform media"; else fout "4.1: dc stop platform media"; fi
    # 2. het schema terug, met het image van nu (het commando vraagt om "ja")
    if printf 'ja\n' | dc run --rm --no-deps -T platform python -m app.cli db-downgrade 0007 >"$WORK/logs/4-downgrade.log" 2>&1; then
      ok "4.2: dc run --rm --no-deps platform python -m app.cli db-downgrade 0007 (met \"ja\")"
    else
      fout "4.2: db-downgrade 0007"
      tail -n 20 "$WORK/logs/4-downgrade.log"
    fi
    check "4.2: het commando zegt dat het schema op 0007 staat" grep -qF "De database staat nu op revisie 0007." "$WORK/logs/4-downgrade.log"
    # 3. het script van nu, met de commit van 0.6.0. step draait het als `bash -s < install.sh`,
    #    zoals de docs het bestand uit stap 1 (`bash < /root/platform-install.sh`); dat bestand
    #    halen kan hier niet (geen GitHub), en de docs-controle in het functiedeel pint de regels
    step 4-terug PLATFORM_SRC_REF="$FROM" || fout "4.3: install.sh gaf een fout"
    check "4: de app draait" app_up
    check "4: de broncode staat op ${FROM:0:12}" test "$(src_head)" = "$(git -C "$WORK/repo.git" rev-parse "$FROM^{commit}")"
    check "4: het schema staat op 0007" test "$(db_rev)" = 0007
    check "4: de app is 0.6.0 (geen /api/v1/admin/media)" eval '! api get /api/v1/admin/media >/dev/null 2>&1'
    check "4: geen container media (--remove-orphans)" test -z "$(cid media)"
    check "4: install.sh zegt waarom media uit is" said 4-terug "Media: uit, want deze versie van het platform heeft nog geen media."
    check "4: de film is er nog, heel" test "$(api sha "$film_id")" = "$film_sha"
    check "4: de mappen media en import en het volume media-run blijven" eval \
      'as_root test -d "$ROOT/data/platform/media" && as_root test -d "$ROOT/data/platform/import" && docker volume inspect platform_media-run >/dev/null 2>&1'
    check "4: één image van de app, platform:local (gezien: $(app_images_text))" one_app_image
  else
    echo
    echo "== 4: terug naar 0.6.0 overgeslagen (INSTALL_TEST_ROLLBACK=0)"
  fi
  ENDED=1
  einde
}

# Alleen met echte ffmpeg (CI): een transcode via curl, en tijdens die transcode docker top (R12)
# shellcheck disable=SC2016  # check … eval '…': eval werkt de uitdrukking pas zelf uit
stap_transcode() {
  local cands item sid code n_app n_media i seen=0 app_ffmpeg=0 tops="" what rc=0
  # Wachten tot de worker niets meer doet (de analyse van de film), zodat docker top alleen de
  # transcode ziet
  for i in $(seq 1 60); do
    [ "$(ffmpeg_in "$(cid media)")" = 0 ] && break
    sleep 1
  done
  # Elke stap die faalt, zegt waarom (install_api.py: de status en het begin van de body) en
  # stopt alleen de transcode, niet de test: stap 2b en 3 en de samenvatting lopen door
  if ! cands=$(api get "/api/v1/media/$film_id/codecs" candidates 2>"$WORK/logs/2-codecs.err") ||
    ! item=$(api get "/api/v1/media/$film_id/codecs" item 2>>"$WORK/logs/2-codecs.err"); then
    fout "2: GET /api/v1/media/<film>/codecs:$(head -c 400 "$WORK/logs/2-codecs.err" | tr '\n' ' ')"
    return 0
  fi
  # Het antwoord van play heeft de sid (PlayOut: een sleutel in het pad, nooit in een log). Het
  # staat daarom in de werkmap, niet in logs/ (dat gaat in CI mee als artefact); in logs/ komt
  # 2-play.json met <sid> waar de sid stond
  if ! api post "/api/v1/media/$film_id/play" "$(profile "$cands" "$item")" \
    >"$WORK/play.json" 2>"$WORK/logs/2-play.err"; then
    fout "2: play:$(head -c 400 "$WORK/logs/2-play.err" | tr '\n' ' ')"
    return 0
  fi
  # De sid, en 2-play.json zonder de sid. Geen JSON-object (een 204): dat in één regel
  if ! sid=$(python3 -I -c '
import json, sys
raw = open(sys.argv[1], "rb").read()
try:
    d = json.loads(raw)
except ValueError:
    d = None
if not isinstance(d, dict):
    sys.exit("geen JSON-object: %r" % raw[:300])
sid = d.get("sid") or ""
def scrub(v):
    if isinstance(v, str):
        return v.replace(sid, "<sid>") if sid else v
    if isinstance(v, list):
        return [scrub(x) for x in v]
    if isinstance(v, dict):
        return {k: scrub(x) for k, x in v.items()}
    return v
with open(sys.argv[2], "w") as f:
    json.dump(scrub(d), f)
print(sid)
' "$WORK/play.json" "$WORK/logs/2-play.json" 2>>"$WORK/logs/2-play.err"); then
    fout "2: play: $(tail -n 1 "$WORK/logs/2-play.err")"
    return 0
  fi
  # Wat play koos
  what=$(python3 -I -c '
import json, sys
d = json.load(open(sys.argv[1]))
print("mode %s, redenen %s" % (d.get("mode"), ",".join(d.get("reasons") or []) or "geen"))
sys.exit(0 if d.get("mode") == "transcode" and "bitrate" in (d.get("reasons") or []) else 1)
' "$WORK/play.json") || rc=$?
  check "2: play kiest transcode, reden bitrate (gezien: $what)" test "$rc" = 0
  if [ -z "$sid" ]; then
    fout "2: play gaf geen sid"
    return 0
  fi
  code=$(curl -s -o "$WORK/film/0.m4s" -w '%{http_code}' -m 60 "http://127.0.0.1:$PORT/api/v1/stream/$sid/0.m4s") || true
  check "2: GET segment 0 via curl: 200 met een fragment (moof) (gezien: ${code:-geen antwoord}, $(wc -c <"$WORK/film/0.m4s" 2>/dev/null || echo 0) bytes)" \
    eval '[ "$code" = 200 ] && grep -q moof "$WORK/film/0.m4s"'
  for i in $(seq 1 10); do
    n_media=$(ffmpeg_in "$(cid media)") n_app=$(ffmpeg_in "$(cid platform)")
    tops="$tops $n_media/$n_app"
    [ "$n_app" = 0 ] || app_ffmpeg=1
    if [ "$n_media" = 1 ] && [ "$n_app" = 0 ]; then seen=1; fi
    sleep 1
  done
  check "2: R12: tijdens de transcode één ffmpeg in media (docker top per seconde, media/app:$tops)" test "$seen" = 1
  check "2: R12: nooit ffmpeg in de container van de app" test "$app_ffmpeg" = 0
  curl -s -o /dev/null -X DELETE -m 10 "http://127.0.0.1:$PORT/api/v1/stream/$sid" || true
  SUMMARY+=("transcode via curl: $what; segment 0 ${code:-geen antwoord}; docker top per seconde, media/app:$tops")
}

# Alleen met echte ffmpeg (CI): de rooktest van de belastingtest, zoals het runbook hem op de LXC
# laat draaien: het bestand uit het runbook (PLATFORM_MEDIA_LOADTEST=1 in de app én in de worker;
# review golf 5, S2), dan app.cli media-loadtest
stap_belastingtest() {
  local override=$WORK/belastingtest.yaml rc=0
  belastingtest_override >"$override"
  if ! dc -f "$override" up -d media platform >"$WORK/logs/2-belastingtest-aan.log" 2>&1; then
    fout "2: de belastingtest aanzetten (dc -f … up -d media platform)"
    return 0
  fi
  # Uit de instellingen van de container, zonder exec: een exec in media krijgt de sleutel van
  # hello in zijn omgeving (het runbook: nooit zonder env -i). grep -q print niets, ook die niet.
  loadtest_in_media() {
    docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$(cid media)" \
      </dev/null | grep -qx 'PLATFORM_MEDIA_LOADTEST=1'
  }
  check "2: de belastingtest staat aan in de worker (PLATFORM_MEDIA_LOADTEST in media)" loadtest_in_media
  check "2: de worker is weer gezond" wait_healthy "$(cid media)" 120
  local end=$((SECONDS + 60))
  until app_up; do
    [ "$SECONDS" -lt "$end" ] || break
    sleep 2
  done
  timeout 900 docker exec "$(cid platform)" python -m app.cli media-loadtest --sessies 1 --duur 30 \
    --json /tmp/belastingtest.json </dev/null >"$WORK/logs/2-belastingtest.log" 2>&1 || rc=$?
  docker exec "$(cid platform)" cat /tmp/belastingtest.json </dev/null >"$WORK/logs/2-belastingtest.json" 2>/dev/null || true
  # Bij een fout ook waarom: de regel van fase b en de eerste fout van een speler (een pad met
  # een sid erin wordt <sid>; de sid hoort in geen log)
  local why=""
  if [ "$rc" != 0 ]; then
    why=$(python3 -I -c '
import json, re, sys
d = json.load(open(sys.argv[1]))
out = [s for s in d.get("summary") or [] if s.startswith("fase b")][:1]
out += [p["error"] for ph in (d.get("phases") or {}).values() if isinstance(ph, dict)
        for p in ph.get("players") or [] if p.get("error")][:1]
out += [d["error"]] if isinstance(d.get("error"), str) else []
print(re.sub(r"stream/[^/\s]+", "stream/<sid>", "; ".join(out))[:400])
' "$WORK/logs/2-belastingtest.json" 2>/dev/null) || why="geen uitkomst, zie 2-belastingtest.log"
  fi
  check "2: de rooktest van de belastingtest (1 sessie, 30 s): exit $rc${why:+ ($why)}" test "$rc" = 0
  SUMMARY+=("belastingtest (1 sessie, 30 s): exit $rc${why:+ ($why)}")
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
    # Alleen de images van de test (own_images) en de naam platform:local; een ander image met
    # de naam platform (in CI platform:ci) blijft
    for ref in $(own_images); do docker image rm -f "$ref" >/dev/null 2>&1; done
    if have platform:local; then docker image rm platform:local >/dev/null 2>&1; fi
  fi
  if [ -n "$LOGS_OUT" ] && [ -d "$WORK/logs" ]; then
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
  echo "=== Samenvatting (test_install_media.sh --stappen) ==="
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
