#!/usr/bin/env bash
# De container analyse met de echte compose (bouwontwerp fase 6, §3.11, X2 "De tests"; X2a):
# compose.yaml + compose.analyse.yaml, en compose.analyse-limits.yaml als docker die grenzen kan
# geven, zoals install.sh. Met de echte modellen van models.lock.
#
#   ANALYSE_TEST_IMAGE=platform:ci bash infra/single/tests/test_analyse_container.sh
#
#   ANALYSE_TEST_IMAGE    het image van de app (standaard platform:local)
#   ANALYSE_TEST_MODELLEN een map met de modellen van models.lock; wat ontbreekt of niet klopt,
#                         haalt het script daar zelf (curl, de grootte en de sha256 van de lock).
#                         Leeg: een tijdelijke map, die aan het eind weggaat
#   ANALYSE_TEST_OUT      een map voor analyse.txt (de regels goed en FOUT, voor "=== kort ===" in
#                         CI); leeg: nergens
#   ANALYSE_TEST_COMPOSE  nog een compose-bestand, als laatste (lokaal: bijv. een lagere nofile);
#                         leeg in CI
#
# Het image is alleen in CI te bouwen (job analyse-image); daar draait dit script. Het kijkt ook
# als root naar /proc van de host (sudo -n, zoals op een runner van GitHub): de omgeving en de
# grenzen van pid 1 en de kinderen.
#
# Het controleert:
# - zonder sleutel stopt de worker met code 2 en de reden, en zegt de app down (key_missing);
# - met sleutel: de container wordt healthy (de healthcheck via `env -i`), en analyse-status in
#   de app zegt "Analyse: ok", beide modellen "klopt", de grenzen en de opdrachten;
# - docker inspect: de afscherming van compose.analyse.yaml (user analyse, network none,
#   read_only, cap_drop ALL, no-new-privileges, pids_limit 64, geen init, de tmpfs, /models
#   alleen lezen, geen /keys) en de grenzen; de sleutel alleen in de omgeving van platform en
#   analyse;
# - in de container (uid 10003, via `env -i`): touch /x faalt, /data is leeg, /keys bestaat niet,
#   /models is niet schrijfbaar, alleen lo en geen verbinding naar buiten, de zelftest (--selftest)
#   is ok, en /proc/1/environ, fd en mem (en die van elk kind) geven EACCES;
# - vanuit de app (uid 10001, app.analyse.client): caps; faces met zes previews als memfd (ruis,
#   verloop en een landschap: ok en 0 gezichten; 6 MP: too_big; een JPEG: unreadable; een kapotte
#   WebP met een merkteken: image_bad) en een lege uit; een verkeerde sleutel geeft handshake;
#   embed_text met de vraag "geheimwoord7" geeft model_missing (X3b: de opdracht is gebouwd, maar
#   deze test zet SigLIP niet neer);
# - een proces uit python-tekst is vanaf de exec niet dumpbaar, een uit python wel;
# - als root op de host: pid 1 en elk kind zijn niet-dumpbaar, CapEff 0, NoNewPrivs 1, de sleutel
#   niet in de cmdline en niet in de omgeving van een kind; pid 1 start uit python-tekst (dan
#   vanaf de exec niet dumpbaar: review golf 5, veilig F6), zonder onnxruntime en numpy in zijn
#   geheugen; er zijn precies twee kinderen: werk (uit python-tekst, met nice 19 en RLIMIT_DATA
#   1 GiB) en zoeken (X3b: de proef vroeg embed_text; uit python-tekst, nice 0 en RLIMIT_DATA
#   1,5 GiB, zonder einde), allebei met onnxruntime geladen;
# - de kinderen in de echte container: werk SIGKILL (bij zijn PID) → pid 1 blijft, de volgende
#   opdracht werkt met een nieuw kind; na 8 opdrachten is werk weg, en de negende start een
#   nieuw (ANALYSE_CHILD_BATCHES);
# - analyse stoppen → de app zegt down; weer starten → ok;
# - de omzetter (profiel omzetten) start niet zonder OMZET_BRON en OMZET_UIT; met de kleine
#   vorm van X3a (siglip_klein.py) zet hij twee keer om, met dezelfde vijf regels (golf 5);
# - docker logs van alle containers: geen sleutel, geen merkteken, geen "geheimwoord7" (K1-16).
#
# De uitvoer noemt de sleutel nooit (mask). Het project, de volumes en de map gaan weg aan het
# eind, ook bij een fout. Exit 1 als een controle faalt.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SINGLE="$(dirname "$HERE")"
ROOT="$(dirname "$(dirname "$SINGLE")")"
LOCK="$ROOT/api/app/analyse/models.lock"
IMAGE="${ANALYSE_TEST_IMAGE:-platform:local}"
MODELLEN="${ANALYSE_TEST_MODELLEN:-}"
OUT="${ANALYSE_TEST_OUT:-}"
PROJECT="analysetest-$$"
SOCK=/run/analyse/worker.sock
CLEAN_PATH="PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
VRAAG=geheimwoord7
FAILED=0
KEY=""

umask 077
WORK="$(mktemp -d "${TMPDIR:-/tmp}/analysetest.XXXXXX")"
MERK="merkteken-x2a-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
if [ -n "$OUT" ]; then
  mkdir -p "$OUT"
  : > "$OUT/analyse.txt"
fi

compose() {
  docker compose --project-name "$PROJECT" --project-directory "$SINGLE" \
    --env-file "$WORK/.env" "${FILES[@]}" "$@"
}

# Een opdracht in de container analyse, via `env -i`: zonder de omgeving van de container (met de
# sleutel), zoals de healthcheck
in_analyse() { compose exec -T analyse /usr/bin/env -i "$CLEAN_PATH" "$@"; }
in_platform() { compose exec -T platform "$@"; }
# Als root op de host (de runner van GitHub heeft sudo zonder wachtwoord)
als_root() { if [ "$(id -u)" = 0 ]; then "$@"; else sudo -n "$@"; fi; }

# Iets dat de sleutel zou kunnen bevatten, gaat hier doorheen voor het in het log komt
mask() {
  if [ -n "$KEY" ]; then sed "s/$KEY/<sleutel>/g"; else cat; fi
}

regel() {
  echo "$*" | mask
  if [ -n "$OUT" ]; then echo "$*" | mask >> "$OUT/analyse.txt"; fi
}
goed() { regel "goed: $*"; }
fout() {
  regel "FOUT: $*" >&2
  FAILED=1
}

FILES=()
cleanup() {
  local rc=$?
  if [ "${#FILES[@]}" -gt 0 ]; then
    if [ "$FAILED" != 0 ] || [ "$rc" != 0 ]; then
      echo "=== de laatste regels van de containers ===" >&2
      compose logs --no-color --tail 60 2>&1 | mask >&2 || true
    fi
    compose --profile omzetten down -v --remove-orphans --timeout 10 > /dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
  if [ "$rc" = 0 ] && [ "$FAILED" != 0 ]; then rc=1; fi
  exit "$rc"
}
trap cleanup EXIT

wait_for() {
  local seconds="$1"
  shift
  local i
  for ((i = 0; i < seconds; i++)); do
    if "$@" > /dev/null 2>&1; then return 0; fi
    sleep 1
  done
  return 1
}

# --- voorbereiding ----------------------------------------------------------------------------

docker image inspect "$IMAGE" > /dev/null || {
  echo "het image $IMAGE is er niet (ANALYSE_TEST_IMAGE)" >&2
  exit 1
}
als_root true || {
  echo "dit script kijkt als root naar /proc (sudo -n)" >&2
  exit 1
}

# De modellen van gezichten in models.lock (alleen de regels met een url; "-" maakt de host zelf,
# X3). De regels van zoeken op beeld (siglip2-b32-256/…) haalt de stap BEELD van X3b, niet deze
# test: 1,5 GB aan bronnen (review golf 5, uitrol F3). Een download is onvertrouwd: eerst naar een
# .part, dan de grootte en de sha256, en pas dan mv
if [ -z "$MODELLEN" ]; then MODELLEN="$WORK/modellen"; fi
mkdir -p "$MODELLEN"
chmod 0755 "$MODELLEN"
sha_van() { sha256sum "$1" | cut -d' ' -f1; }
gehaald=0
while read -r sha bytes naam url _; do
  case "$sha" in '' | '#'*) continue ;; esac
  case "$naam" in siglip2-b32-256/*) continue ;; esac
  [ "$url" != "-" ] || continue
  doel="$MODELLEN/$naam"
  if [ -f "$doel" ] && [ ! -L "$doel" ] && [ "$(stat -c %s "$doel")" = "$bytes" ] && [ "$(sha_van "$doel")" = "$sha" ]; then
    continue
  fi
  mkdir -p "$(dirname "$doel")"
  part="$(dirname "$doel")/.$(basename "$doel").part"
  rm -f "$part"
  curl -fsSL --retry 3 --connect-timeout 20 --max-time 600 --max-filesize "$bytes" -o "$part" "$url" < /dev/null || {
    rm -f "$part"
    echo "kon $naam niet halen" >&2
    exit 1
  }
  if [ "$(stat -c %s "$part")" != "$bytes" ] || [ "$(sha_van "$part")" != "$sha" ]; then
    rm -f "$part"
    echo "$naam: de grootte of de sha256 klopt niet met models.lock" >&2
    exit 1
  fi
  chmod 0644 "$part"
  mv -f "$part" "$doel"
  gehaald=$((gehaald + 1))
done < "$LOCK"
echo "de modellen in $MODELLEN ($gehaald gehaald)"
mkdir -m 0700 "$WORK/sleutels"

# Het project zonder poorten naar buiten en met een eigen volume voor /data, op het image van de
# test
cat > "$WORK/test.yaml" << EOF
services:
  platform:
    image: $IMAGE
    pull_policy: never
    ports: !reset []
    volumes:
      - platform-data:/data
  analyse:
    image: $IMAGE
  analyse-omzetten:
    image: $IMAGE
volumes:
  platform-data:
EOF

FILES=(-f "$SINGLE/compose.yaml" -f "$SINGLE/compose.analyse.yaml")
LIMITS=0
if [ "$(docker info --format '{{.MemoryLimit}} {{.CPUCfsQuota}}' 2> /dev/null)" = "true true" ]; then
  FILES+=(-f "$SINGLE/compose.analyse-limits.yaml")
  LIMITS=1
fi
FILES+=(-f "$WORK/test.yaml")
if [ -n "${ANALYSE_TEST_COMPOSE:-}" ]; then FILES+=(-f "$ANALYSE_TEST_COMPOSE"); fi

env_file() {
  {
    echo "DATA_ROOT=/niet-gebruikt"
    echo "ANALYSE_WORKER_KEY=$1"
    echo "MODELLEN_ROOT=$MODELLEN"
    echo "SLEUTELS_ROOT=$WORK/sleutels"
  } > "$WORK/.env"
}

health_ok() {
  in_platform python -I -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).status == 200 else 1)"
}
status() { in_platform python -m app.cli analyse-status "$@" 2>&1 | tr -d '\r' || true; }

# --- zonder sleutel ---------------------------------------------------------------------------
# De worker stopt met code 2 (en docker probeert het steeds weer); de app start wel en zegt down
# met de reden

env_file ""
compose config -q
compose up -d --no-build --pull never > "$WORK/up.txt" 2>&1 || {
  fout "compose up zonder sleutel: $(tail -n 3 "$WORK/up.txt")"
  exit 1
}
wait_for 90 health_ok || {
  fout "de app start niet zonder sleutel"
  exit 1
}
exit_code() { docker inspect --format '{{.State.ExitCode}}' "$(compose ps -aq analyse)"; }
analyse_code_2() { [ "$(exit_code)" = 2 ]; }
if wait_for 30 analyse_code_2 && compose logs --no-color analyse 2>&1 | grep -q "PLATFORM_ANALYSE_WORKER_KEY ontbreekt of is te kort"; then
  goed "zonder sleutel stopt de worker met code 2, met de reden"
else
  fout "zonder sleutel: de worker stopte met code $(exit_code)"
fi
zonder="$(status)"
if [ "$(head -n 1 <<< "$zonder")" = "Analyse: down (PLATFORM_ANALYSE_WORKER_KEY ontbreekt of is korter dan 32 tekens)" ]; then
  goed "zonder sleutel zegt de app: $(head -n 1 <<< "$zonder")"
else
  fout "zonder sleutel zegt de app: $zonder"
fi
compose down -v --remove-orphans --timeout 10 > /dev/null 2>&1

# --- starten ----------------------------------------------------------------------------------

KEY="$(python3 -I -c 'import secrets; print(secrets.token_hex(32))')"
env_file "$KEY"
compose config -q
compose up -d --no-build --pull never > "$WORK/up.txt" 2>&1 || {
  fout "compose up: $(tail -n 3 "$WORK/up.txt" | mask)"
  exit 1
}
wait_for 90 health_ok || {
  fout "de app start niet"
  exit 1
}
health() { docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{end}}' "$(compose ps -q analyse)"; }
healthy() { [ "$(health)" = healthy ]; }
if wait_for 150 healthy && in_platform test -S "$SOCK"; then
  goed "analyse is healthy (de healthcheck: hello zonder sleutel, via env -i), en de app ziet $SOCK"
else
  fout "analyse wordt niet healthy: $(health)"
  exit 1
fi

# --- analyse-status in de app -----------------------------------------------------------------

stand="$(status --wait 30)"
models="$(grep -c '^  model .*: klopt$' <<< "$stand" || true)"
if [ "$LIMITS" = 1 ]; then grenzen="  grenzen: 1 cpus, 3072 MB geheugen"; else grenzen="  grenzen: geen (de container heeft geen cgroup-grenzen)"; fi
opdrachten="$(sed -n 's/^  opdrachten: //p' <<< "$stand" | tr -d ' ' | tr ',' '\n' | sort | tr '\n' ' ')"
if [ "$(head -n 1 <<< "$stand")" = "Analyse: ok" ] && [ "$models" = 2 ] && grep -qxF "$grenzen" <<< "$stand" \
  && [ "$opdrachten" = "cancel caps embed_image embed_text faces forget group rank " ] && [ "$(wc -l <<< "$stand" | tr -d ' ')" = 5 ]; then
  goed "analyse-status: $(tr '\n' ';' <<< "$stand")"
else
  fout "analyse-status: $(tr '\n' ';' <<< "$stand")"
fi

# --- de afscherming volgens docker ------------------------------------------------------------

cat > "$WORK/inspect.py" << 'PY'
import json, os, sys

key = os.environ["ANALYSE_TEST_KEY"]
limits = os.environ["ANALYSE_TEST_LIMITS"] == "1"
modellen = os.environ["ANALYSE_TEST_MODELLEN"]
analyse, platform = json.load(sys.stdin)
problems = []


def want(name, got, expected):
    if got != expected:
        problems.append(f"{name}: {got!r} in plaats van {expected!r}")


h, c = analyse["HostConfig"], analyse["Config"]
want("user", c.get("User"), "analyse")
want("network_mode", h.get("NetworkMode"), "none")
want("read_only", h.get("ReadonlyRootfs"), True)
want("cap_drop", h.get("CapDrop"), ["ALL"])
want("cap_add", h.get("CapAdd") or [], [])
want("privileged", h.get("Privileged"), False)
want("security_opt", h.get("SecurityOpt"), ["no-new-privileges:true"])
want("pids_limit", h.get("PidsLimit"), 64)
want("init (geen docker-init: die heeft de sleutel in zijn omgeving)", h.get("Init") or False, False)
want("restart", (h.get("RestartPolicy") or {}).get("Name"), "unless-stopped")
ulimits = {u["Name"]: [u["Soft"], u["Hard"]] for u in h.get("Ulimits") or []}
want("ulimits", ulimits, {"core": [0, 0], "nofile": [256, 256]})
options = {k: set(v.split(",")) for k, v in (h.get("Tmpfs") or {}).items()}
want("tmpfs", sorted(options), ["/data", "/tmp"])
want("tmpfs /data", sorted(options.get("/data", set()) & {"mode=0700", "uid=10003", "gid=10003"}),
     ["gid=10003", "mode=0700", "uid=10003"])
want("tmpfs /tmp", "mode=1777" in options.get("/tmp", set()), True)
mounts = {m["Destination"]: m for m in analyse.get("Mounts") or [] if m.get("Type") != "tmpfs"}
want("mounts (geen /keys, geen anoniem volume op /data)", sorted(mounts), ["/models", "/run/analyse"])
want("/models alleen lezen", (mounts.get("/models") or {}).get("RW"), False)
want("/models van MODELLEN_ROOT", (mounts.get("/models") or {}).get("Source"), modellen)
want("/run/analyse een volume", (mounts.get("/run/analyse") or {}).get("Type"), "volume")
want("stop_grace_period", c.get("StopTimeout"), 5)
want("healthcheck", (c.get("Healthcheck") or {}).get("Test", [])[:3], ["CMD", "/usr/bin/env", "-i"])
env = dict(e.split("=", 1) for e in c.get("Env") or [])
want("OPENBLAS_NUM_THREADS", env.get("OPENBLAS_NUM_THREADS"), "1")
want("OMP_NUM_THREADS", env.get("OMP_NUM_THREADS"), "1")
if limits:
    want("mem_limit", h.get("Memory"), 3 * 1024**3)
    want("memswap_limit", h.get("MemorySwap"), 3 * 1024**3)
    want("cpus", h.get("NanoCpus"), 10**9)
    want("cpu_shares", h.get("CpuShares"), 256)
pc = platform["Config"]
penv = dict(e.split("=", 1) for e in pc.get("Env") or [])
want("PLATFORM_ANALYSE_ENABLED", penv.get("PLATFORM_ANALYSE_ENABLED"), "true")
want("PLATFORM_ANALYSE_WORKER", penv.get("PLATFORM_ANALYSE_WORKER"), "/run/analyse/worker.sock")
want("PLATFORM_FACES_KEYS", penv.get("PLATFORM_FACES_KEYS"), "/keys")
pmounts = sorted(m["Destination"] for m in platform.get("Mounts") or [])
want("mounts van platform", pmounts, ["/data", "/keys", "/run/analyse"])
for name, container in (("analyse", analyse), ("platform", platform)):
    found = container["Config"].get("Env") or []
    if f"PLATFORM_ANALYSE_WORKER_KEY={key}" not in found:
        problems.append(f"{name}: de sleutel staat niet in de omgeving")
    rest = json.loads(json.dumps(container))
    rest["Config"].pop("Env", None)
    if key in json.dumps(rest):
        problems.append(f"{name}: de sleutel staat buiten de omgeving (command, healthcheck …)")
for p in problems:
    print(p)
sys.exit(1 if problems else 0)
PY
if docker inspect "$(compose ps -q analyse)" "$(compose ps -q platform)" \
  | ANALYSE_TEST_KEY="$KEY" ANALYSE_TEST_LIMITS="$LIMITS" ANALYSE_TEST_MODELLEN="$MODELLEN" \
    python3 -I "$WORK/inspect.py" > "$WORK/inspect.txt" 2>&1; then
  goed "docker inspect: de afscherming van compose.analyse.yaml (grenzen: $LIMITS)"
else
  fout "docker inspect: $(mask < "$WORK/inspect.txt" | tr '\n' ';')"
fi

# --- in de container --------------------------------------------------------------------------

uid="$(in_analyse id -u | tr -d '\r' || true)"
if [ "$uid" = 10003 ]; then goed "id -u is 10003"; else fout "id -u is $uid"; fi
if in_analyse touch /x > /dev/null 2>&1; then fout "touch /x lukte"; else goed "touch /x faalt (alleen lezen)"; fi
data="$(in_analyse ls -A /data 2>&1 | tr -d '\r' || true)"
if [ -z "$data" ]; then goed "/data is leeg"; else fout "/data is niet leeg: $data"; fi
if in_analyse test -e /keys; then fout "/keys bestaat in analyse"; else goed "/keys bestaat niet in analyse"; fi
cat > "$WORK/binnen.py" << 'PY'
import errno, json, os, socket, sys

uit = {}
try:
    os.close(os.open("/models/nieuw", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644))
    uit["nieuw bestand in /models"] = "lukte"
except OSError as exc:
    uit["nieuw bestand in /models"] = errno.errorcode.get(exc.errno, str(exc.errno))
models = sorted(n for n in os.listdir("/models") if n.endswith(".onnx"))
uit["modellen"] = models
for name in models:
    try:
        os.close(os.open(f"/models/{name}", os.O_WRONLY))
        uit[f"{name} schrijven"] = "lukte"
    except OSError as exc:
        uit[f"{name} schrijven"] = errno.errorcode.get(exc.errno, str(exc.errno))
uit["netwerk"] = [name for _, name in socket.if_nameindex()]
s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
s.settimeout(3)
try:
    s.connect(("1.1.1.1", 443))
    uit["verbinding naar buiten"] = "lukte"
except OSError as exc:
    uit["verbinding naar buiten"] = errno.errorcode.get(exc.errno, type(exc).__name__)
finally:
    s.close()
print(json.dumps(uit, sort_keys=True))
fout = (
    uit["nieuw bestand in /models"] == "lukte"
    or any(uit[f"{n} schrijven"] == "lukte" for n in models)
    or uit["netwerk"] != ["lo"]
    or uit["verbinding naar buiten"] == "lukte"
    or len(models) != 2
)
sys.exit(1 if fout else 0)
PY
if in_analyse python -I - < "$WORK/binnen.py" > "$WORK/binnen.txt" 2>&1; then
  goed "in analyse: /models alleen lezen, alleen lo, geen verbinding naar buiten: $(tail -n 1 "$WORK/binnen.txt")"
else
  fout "in analyse: $(mask < "$WORK/binnen.txt" | tail -n 3)"
fi

# De zelftest zoals de worker hem bij de start draait, maar als eigen proces (JSON op stdout)
BOOT="$(compose config --format json | python3 -I -c '
import json, sys
command = json.load(sys.stdin)["services"]["analyse"]["command"]
print(command[command.index("-c") + 1])
')"
in_analyse python -I -c "$BOOT" /srv --selftest --models /models > "$WORK/zelftest.json" 2>&1 || true
cat > "$WORK/zelftest_kort.py" << 'PY'
import json, sys

try:
    d = json.loads(open(sys.argv[1]).read())
except ValueError:
    print("geen JSON")
    sys.exit(1)
names = [c["name"] for c in d.get("checks", [])]
print(f"ok {d.get('ok')}, sandbox {d.get('sandbox')}, {len(names)} controles"
      + (f", problemen {d.get('problems')}" if d.get("problems") else ""))
sys.exit(0 if d.get("ok") is True and d.get("sandbox") == "on" and len(names) >= 10 else 1)
PY
if kort="$(python3 -I "$WORK/zelftest_kort.py" "$WORK/zelftest.json" 2>&1)"; then
  goed "de zelftest in de container: $kort"
else
  fout "de zelftest in de container: $kort $(mask < "$WORK/zelftest.json" | tail -n 5 | tr '\n' ' ')"
fi
# pid 1 start uit python-tekst (review golf 5, veilig F6). Een proces uit die kopie (0711 van
# root) is vanaf de exec niet dumpbaar, nog vóór een eigen prctl; uit python (leesbaar) wel.
# PR_GET_DUMPABLE is 3. Of pid 1 er echt uit start, ziet procs_kort.py hieronder (argv[0])
# De kernel geeft een programma dat het proces niet mag lezen bij de exec de waarde van
# fs.suid_dumpable (fs/exec.c, begin_new_exec): 0 (uit) of 2 (alleen root; Ubuntu, dus de
# runners van CI). Beide zijn voor dezelfde uid niet dumpbaar: /proc/<pid> wordt van root en
# ptrace mag niet; alleen 1 (SUID_DUMP_USER) is dumpbaar. Staat de host op 1, dan beschermt de
# exec niet en is dit FOUT
DUMP='import ctypes;print(ctypes.CDLL(None).prctl(3,0,0,0,0))'
uit_tekst="$(in_analyse /opt/venv/bin/python-tekst -I -c "$DUMP" 2>&1 | tr -d '\r' || true)"
uit_python="$(in_analyse python -I -c "$DUMP" 2>&1 | tr -d '\r' || true)"
suid_dumpable="$(in_analyse cat /proc/sys/fs/suid_dumpable 2>&1 | tr -d '\r' || true)"
if { [ "$suid_dumpable" = 0 ] || [ "$suid_dumpable" = 2 ]; } \
  && [ "$uit_tekst" = "$suid_dumpable" ] && [ "$uit_python" = 1 ]; then
  goed "een proces uit python-tekst is vanaf de exec niet dumpbaar ($uit_tekst, fs.suid_dumpable $suid_dumpable), uit python wel (1)"
else
  fout "dumpbaar vanaf de exec: python-tekst \"$uit_tekst\", python \"$uit_python\", fs.suid_dumpable \"$suid_dumpable\" (verwacht: gelijk aan fs.suid_dumpable, en die 0 of 2; python 1)"
fi

# --- vanuit de app ----------------------------------------------------------------------------

cat > "$WORK/app.py" << 'PY'
import asyncio, io, os, random, sys

sys.path.insert(0, "/srv")
from PIL import Image, ImageDraw  # noqa: E402

from app.analyse import client, wire  # noqa: E402
from app.analyse.wire import Op  # noqa: E402
from app.config import get_settings  # noqa: E402

MODE, MERK, VRAAG = sys.argv[1], sys.argv[2].encode(), sys.argv[3]
problems = []


def check(name, ok, detail=""):
    print(("goed: " if ok else "FOUT: ") + name + (f" ({detail})" if detail else ""), flush=True)
    if not ok:
        problems.append(name)


def webp(img, **kw):
    buf = io.BytesIO()
    img.save(buf, "WEBP", quality=90, **kw)
    return buf.getvalue()


def ruis(w, h, seed):
    return Image.frombytes("RGB", (w, h), random.Random(seed).randbytes(w * h * 3))


def verloop(w, h):
    x = Image.linear_gradient("L").rotate(90).resize((w, h))
    y = Image.linear_gradient("L").resize((w, h))
    return Image.merge("RGB", (x, y, Image.blend(x, y, 0.5)))


def landschap(w, h):
    img = Image.new("RGB", (w, h), (120, 170, 230))
    draw = ImageDraw.Draw(img)
    draw.ellipse([w * 0.7, h * 0.08, w * 0.82, h * 0.26], fill=(255, 230, 120))
    draw.polygon([(0, h * 0.6), (w * 0.3, h * 0.35), (w * 0.6, h * 0.6)], fill=(110, 110, 120))
    draw.rectangle([0, h * 0.6, w, h], fill=(60, 140, 60))
    return img


async def faces(settings, previews):
    fds = [wire.sealed_memfd("preview", p) for p in previews]
    out = wire.out_memfd("uit")
    try:
        data = await client.call(settings, Op.FACES, wire.FacesArgs(), fds=[*fds, out])
        return wire.FacesData.model_validate(data), os.fstat(out).st_size
    finally:
        wire.close_all([*fds, out])


async def proef(settings):
    caps = await client.caps(settings)
    check("caps: beide modellen ok, de opdrachten van X2a, X2b en X3b, geen beeld zonder SigLIP",
          [m.state for m in caps.models] == ["ok", "ok"] and caps.beeld is None
          and sorted(caps.built) == ["cancel", "caps", "embed_image", "embed_text", "faces", "forget", "group", "rank"],
          f"{[(m.name, m.state) for m in caps.models]}, {caps.built}, grenzen {caps.limits}")
    jpeg = io.BytesIO()
    landschap(800, 600).save(jpeg, "JPEG")
    heel = webp(landschap(1024, 683))
    kapot = heel[: len(heel) // 2] + MERK  # een WebP die halverwege ophoudt, met het merkteken
    previews = [webp(ruis(1024, 683, 1)), webp(verloop(1023, 767)), webp(landschap(683, 1024)),
                webp(Image.new("RGB", (3000, 2000), (90, 90, 90))), jpeg.getvalue(), kapot]
    data, size = await faces(settings, previews)
    got = [(p.code, p.w, p.h, p.n) for p in data.photos]
    want = [("ok", 1024, 683, 0), ("ok", 1023, 767, 0), ("ok", 683, 1024, 0),
            ("too_big", 0, 0, 0), ("unreadable", 0, 0, 0), ("image_bad", 0, 0, 0)]
    check("faces met zes previews als memfd: ok met 0 gezichten, too_big, unreadable, image_bad; uit leeg",
          got == want and size == 0, f"{got}, uit {size} bytes")
    try:
        await client.caps(settings.model_copy(update={"analyse_worker_key": "0" * 64}))
        check("een verkeerde sleutel", False, "caps lukte")
    except client.AnalyseDown as exc:
        check("een verkeerde sleutel geeft handshake", exc.reason == "handshake", exc.reason)
    try:
        out = wire.out_memfd("uit")
        try:
            await client.call(settings, Op.EMBED_TEXT, wire.EmbedTextArgs(text=VRAAG), fds=[out])
        finally:
            os.close(out)
        check("embed_text", False, "lukte")
    except client.AnalyseFailed as exc:
        check("embed_text zonder SigLIP is model_missing", exc.code == "model_missing", exc.code)


async def herhaal(settings, n):
    codes = []
    for i in range(n):
        data, _ = await faces(settings, [webp(ruis(640, 480, 100 + i))])
        codes.append(data.photos[0].code)
    check(f"{n} keer faces", codes == ["ok"] * n, str(codes))


async def main():
    settings = get_settings()
    if MODE == "proef":
        await proef(settings)
    else:
        await herhaal(settings, int(MODE))


asyncio.run(main())
sys.exit(1 if problems else 0)
PY
app() {
  local name="$1"
  shift
  if in_platform python -I - "$@" "$MERK" "$VRAAG" < "$WORK/app.py" > "$WORK/$name.txt" 2>&1; then
    rc=0
  else
    rc=1
  fi
  { grep -E "^(goed|FOUT): " "$WORK/$name.txt" || true; } | while IFS= read -r line; do regel "$line"; done
  if [ "$rc" != 0 ]; then
    mask < "$WORK/$name.txt" | tail -n 15 >&2
    fout "vanuit de app ($name)"
  fi
}
app proef proef

# --- pid 1 en de kinderen, als root op de host ------------------------------------------------

# Per proces: niet-dumpbaar (dan is /proc/<pid>/environ van root), CapEff, NoNewPrivs, of de
# sleutel in de cmdline of de omgeving staat, nice, RLIMIT_DATA, onnxruntime of numpy in de maps.
# Nooit de inhoud van de cmdline of de omgeving: alleen ja of nee
cat > "$WORK/procs.py" << 'PY'
import json, os, sys

pid1 = int(sys.argv[1])
key = ""
for line in open(sys.argv[2]):
    if line.startswith("ANALYSE_WORKER_KEY="):
        key = line.split("=", 1)[1].strip()
assert len(key) == 64
raw = key.encode()


def read(path):
    with open(path, "rb") as f:
        return f.read()


def field(status, name):
    for line in status.splitlines():
        if line.startswith(name + ":"):
            return line.split(":", 1)[1].split()
    return []


def info(pid):
    status = read(f"/proc/{pid}/status").decode()
    argv = read(f"/proc/{pid}/cmdline").split(b"\0")
    stat = read(f"/proc/{pid}/stat").decode()
    after = stat[stat.rindex(")") + 2:].split()
    limits = {}
    for line in read(f"/proc/{pid}/limits").decode().splitlines()[1:]:
        if line.startswith("Max data size"):
            limits["data"] = line.split()[3]
    maps = read(f"/proc/{pid}/maps")
    naam = argv[5].decode() if len(argv) > 5 and argv[5] in (b"werk", b"zoeken") else "pid1"
    return {
        "pid": pid,
        "nspid": int(field(status, "NSpid")[-1]),
        "naam": naam,
        "uid": field(status, "Uid"),
        "dumpbaar": os.stat(f"/proc/{pid}/environ").st_uid != 0,
        "CapEff": field(status, "CapEff")[0],
        "NoNewPrivs": field(status, "NoNewPrivs")[0],
        "sleutel in cmdline": raw in b"\0".join(argv),
        "sleutel in environ": raw in read(f"/proc/{pid}/environ"),
        "python-tekst": os.path.basename(argv[0].decode()) == "python-tekst",
        "nice": int(after[16]),
        "data": limits.get("data"),
        "onnxruntime of numpy": b"onnxruntime" in maps or b"numpy" in maps,
    }


kids = []
for task in os.listdir(f"/proc/{pid1}/task"):
    kids += [int(p) for p in read(f"/proc/{pid1}/task/{task}/children").split()]
print(json.dumps({"pid1": info(pid1), "kinderen": [info(k) for k in sorted(kids)]}))
PY
pid1() { docker inspect --format '{{.State.Pid}}' "$(compose ps -q analyse)"; }
procs() { als_root python3 -I "$WORK/procs.py" "$(pid1)" "$WORK/.env"; }
procs > "$WORK/procs.json"
cat > "$WORK/procs_kort.py" << 'PY'
import json, sys

d = json.load(open(sys.argv[1]))
fout = []
p1, kids = d["pid1"], d["kinderen"]
# De grenzen van CHILDREN in app/analyse/wire.py. zoeken leeft sinds X3b na de embed_text van de
# proef, zonder einde (max_ops en max_age_s None); werk tot ANALYSE_CHILD_BATCHES opdrachten
SPEC = {"werk": (19, str(1024**3), "1 GiB"), "zoeken": (0, str(3 * 1024**3 // 2), "1,5 GiB")}
if p1["nspid"] != 1:
    fout.append(f"pid 1 is {p1['nspid']}")
if sorted(k["naam"] for k in kids) != sorted(SPEC):
    fout.append(f"kinderen: {[k['naam'] for k in kids]}")
for p in [p1, *kids]:
    n = p["naam"]
    if p["uid"] != ["10003"] * 4:
        fout.append(f"{n}: uid {p['uid']}")
    if p["dumpbaar"]:
        fout.append(f"{n}: dumpbaar")
    if p["CapEff"] != "0000000000000000" or p["NoNewPrivs"] != "1":
        fout.append(f"{n}: CapEff {p['CapEff']}, NoNewPrivs {p['NoNewPrivs']}")
    if p["sleutel in cmdline"]:
        fout.append(f"{n}: de sleutel in de cmdline")
if p1["onnxruntime of numpy"]:
    fout.append("pid 1 heeft onnxruntime of numpy geladen")
if not p1["python-tekst"]:
    fout.append("pid 1 start niet uit python-tekst (dan dumpbaar tot zijn prctl)")
for k in kids:
    n = k["naam"]
    nice, data, _ = SPEC.get(n, (None, None, None))
    if k["sleutel in environ"]:
        fout.append(f"{n}: de sleutel in de omgeving")
    if not k["python-tekst"] or k["nice"] != nice or k["data"] != data:
        fout.append(f"{n}: python-tekst {k['python-tekst']}, nice {k['nice']}, RLIMIT_DATA {k['data']}")
    if not k["onnxruntime of numpy"]:
        fout.append(f"{n} heeft onnxruntime niet geladen")
print("; ".join(fout) if fout else (
    "pid 1 en de kinderen ("
    + ", ".join(f"{k['naam']} pid {k['nspid']}" for k in kids)
    + " in de container) niet-dumpbaar, CapEff 0, NoNewPrivs 1, de sleutel niet in de cmdline en "
    "niet in de omgeving van een kind; pid 1 uit python-tekst, zonder onnxruntime en numpy; "
    + "; ".join(f"{n} uit python-tekst, nice {v[0]}, RLIMIT_DATA {v[2]}" for n, v in SPEC.items())))
sys.exit(1 if fout else 0)
PY
if kort="$(python3 -I "$WORK/procs_kort.py" "$WORK/procs.json" 2>&1)"; then
  goed "als root op de host: $kort"
else
  fout "als root op de host: $kort"
fi

# Een tweede proces van analyse in de container (uid 10003) kan /proc/<pid> van pid 1 en de
# kinderen (werk en zoeken)
# niet lezen: alleen EACCES
nspids="$(python3 -I -c 'import json,sys; d=json.load(open(sys.argv[1])); print(" ".join(str(p["nspid"]) for p in [d["pid1"], *d["kinderen"]]))' "$WORK/procs.json")"
cat > "$WORK/peek.py" << 'PY'
import errno, os, sys

uit = {}
for pid in sys.argv[1:]:
    for what in ("environ", "mem", "fd"):
        try:
            if what == "fd":
                os.listdir(f"/proc/{pid}/fd")
            else:
                with open(f"/proc/{pid}/{what}", "rb") as f:
                    f.read(1)
            uit[f"{pid}/{what}"] = "geopend"
        except OSError as exc:
            uit[f"{pid}/{what}"] = errno.errorcode.get(exc.errno, str(exc.errno))
codes = sorted(set(uit.values()))
print(f"{len(uit)} pogingen, {codes}")
sys.exit(0 if codes == ["EACCES"] and len(uit) == 3 * (len(sys.argv) - 1) else 1)
PY
# shellcheck disable=SC2086 # nspids: losse getallen
if in_analyse python -I - $nspids < "$WORK/peek.py" > "$WORK/peek.txt" 2>&1; then
  goed "uid 10003 in de container leest /proc/<pid>/environ, mem en fd van pid 1, werk en zoeken niet: $(tail -n 1 "$WORK/peek.txt")"
else
  fout "/proc van pid 1 en de kinderen: $(mask < "$WORK/peek.txt" | tail -n 3)"
fi

# --- de kinderen in de echte container --------------------------------------------------------

werk_pid() { python3 -I -c 'import json,sys; print(" ".join(str(k["pid"]) for k in json.load(open(sys.argv[1]))["kinderen"] if k["naam"] == "werk"))' "$1"; }
pid1_voor="$(pid1)"
w1="$(werk_pid "$WORK/procs.json")"
if [ -n "$w1" ]; then als_root kill -KILL "$w1"; fi
geen_werk() { procs > "$WORK/procs-nu.json" && [ -z "$(werk_pid "$WORK/procs-nu.json")" ]; }
wait_for 10 geen_werk || true
app een 1
procs > "$WORK/procs2.json"
w2="$(werk_pid "$WORK/procs2.json")"
if [ "$(pid1)" = "$pid1_voor" ] && [ -n "$w1" ] && [ -n "$w2" ] && [ "$w2" != "$w1" ] \
  && [ "$(docker inspect --format '{{.RestartCount}}' "$(compose ps -q analyse)")" = 0 ]; then
  goed "werk SIGKILL: pid 1 bleef, de volgende opdracht kreeg een nieuw kind"
else
  fout "werk SIGKILL: pid 1 $pid1_voor → $(pid1), werk $w1 → $w2"
fi
app zeven 7
procs > "$WORK/procs3.json"
w3="$(werk_pid "$WORK/procs3.json")"
app negende 1
procs > "$WORK/procs4.json"
w4="$(werk_pid "$WORK/procs4.json")"
if [ -z "$w3" ] && [ -n "$w4" ] && [ "$w4" != "$w2" ] && [ "$w4" != "$w1" ]; then
  goed "na 8 opdrachten is werk weg, de negende start een nieuw kind"
else
  fout "na 8 opdrachten: werk na de achtste '$w3', na de negende '$w4' (daarvoor $w2)"
fi

# --- stoppen en weer starten ------------------------------------------------------------------

compose stop analyse > /dev/null 2>&1
gestopt="$(status | head -n 1)"
compose start analyse > /dev/null 2>&1
weer="$(status --wait 60 | head -n 1)"
case "$gestopt" in
  "Analyse: down ("*)
    if [ "$weer" = "Analyse: ok" ]; then
      goed "analyse gestopt: \"$gestopt\"; weer gestart: \"$weer\""
    else
      fout "analyse weer gestart: $weer"
    fi
    ;;
  *) fout "analyse gestopt: $gestopt" ;;
esac

# --- de omzetter zonder OMZET_BRON en OMZET_UIT -----------------------------------------------

if compose --profile omzetten run --rm --no-deps analyse-omzetten < /dev/null > "$WORK/omzet.txt" 2>&1; then
  fout "de omzetter startte zonder OMZET_BRON en OMZET_UIT"
else
  goed "de omzetter start niet zonder OMZET_BRON en OMZET_UIT: $(mask < "$WORK/omzet.txt" | tail -n 1)"
fi

# --- de omzetter met de kleine vorm van X3a (golf 5) ------------------------------------------
# siglip_klein.py (alleen de standaardbibliotheek) schrijft vijf bronbestanden met de namen van
# het echte model; de dienst zet ze twee keer om (uid 10004, /bron alleen lezen, geen netwerk):
# vijf regels `sha256 bytes siglip2-b32-256/naam -`, twee keer dezelfde, de licentie byte voor byte
mkdir -p "$WORK/omzet"
python3 -I -c 'import sys, pathlib; sys.path.insert(0, sys.argv[1]); from tests_analyse import siglip_klein; siglip_klein.schrijf_bron(pathlib.Path(sys.argv[2]))' \
  "$ROOT/api" "$WORK/omzet/bron"
chmod 755 "$WORK/omzet/bron" && chmod 644 "$WORK/omzet/bron"/*
klein_ok=1
for n in 1 2; do
  mkdir "$WORK/omzet/uit$n" && als_root chown 10004:10003 "$WORK/omzet/uit$n"
  if ! OMZET_BRON="$WORK/omzet/bron" OMZET_UIT="$WORK/omzet/uit$n" compose --profile omzetten run --rm --no-deps \
    analyse-omzetten < /dev/null > "$WORK/omzet/out$n" 2> "$WORK/omzet/err$n"; then
    klein_ok=0
    fout "de omzetter met de kleine vorm (keer $n): $(mask < "$WORK/omzet/err$n" | tail -n 1)"
  fi
done
licentie="$(sha256sum < "$ROOT/api/app/analyse/licenties/siglip2-Apache-2.0.txt" | cut -d' ' -f1)"
namen="$(sed -E 's/^[0-9a-f]{64} [1-9][0-9]* (siglip2-b32-256\/[A-Za-z0-9._-]+) -$/\1/' "$WORK/omzet/out1" | tr '\n' ' ')"
eigenaar="$(als_root stat -c '%u:%g' "$WORK/omzet/uit1/beeld.int8.onnx" 2> /dev/null || echo weg)"
if [ "$klein_ok" = 1 ] && cmp -s "$WORK/omzet/out1" "$WORK/omzet/out2" &&
  [ "$namen" = "siglip2-b32-256/beeld.int8.onnx siglip2-b32-256/tekst.int8.onnx siglip2-b32-256/tokenizer.bin siglip2-b32-256/siglip2-Apache-2.0.txt siglip2-b32-256/meta.json " ] &&
  grep -qx "$licentie [0-9]* siglip2-b32-256/siglip2-Apache-2.0.txt -" "$WORK/omzet/out1" && [ "$eigenaar" = 10004:10003 ]; then
  goed "de omzetter met de kleine vorm: twee keer dezelfde vijf regels, van 10004:10003; $(tail -n 1 "$WORK/omzet/err1")"
elif [ "$klein_ok" = 1 ]; then
  fout "de omzetter met de kleine vorm: $(tr '\n' ' ' < "$WORK/omzet/out1") (eigenaar $eigenaar)"
fi
als_root rm -rf "$WORK/omzet"

# --- de logs ----------------------------------------------------------------------------------

compose logs --no-color > "$WORK/logs.txt" 2>&1 || true
gevonden=""
if grep -qF "$KEY" "$WORK/logs.txt"; then gevonden="$gevonden sleutel"; fi
if grep -qF "$MERK" "$WORK/logs.txt"; then gevonden="$gevonden merkteken"; fi
if grep -qF "$VRAAG" "$WORK/logs.txt"; then gevonden="$gevonden $VRAAG"; fi
if [ -z "$gevonden" ]; then
  goed "docker logs ($(wc -l < "$WORK/logs.txt" | tr -d ' ') regels): geen sleutel, geen merkteken, geen $VRAAG"
else
  fout "docker logs bevatten:$gevonden"
fi

if [ "$FAILED" = 0 ]; then echo "Alle controles van de container analyse geslaagd."; fi
