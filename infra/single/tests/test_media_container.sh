#!/usr/bin/env bash
# De mediaworker in zijn echte container (bouwontwerp fase 5, §2 M1; Keuze 11, 12 en 47): wat
# J1 van de spike deed, nu met het image van de app en de echte compose (compose.yaml +
# compose.media.yaml, en compose.media-limits.yaml als docker die grenzen kan geven).
#
#   MEDIA_TEST_IMAGE=platform:ci bash infra/single/tests/test_media_container.sh
#
#   MEDIA_TEST_IMAGE   het image van de app (standaard platform:local)
#   MEDIA_TEST_FFMPEG  het begin van de versie die de zelftest eist (standaard 7.1.)
#   MEDIA_TEST_OUT     een map voor selftest.json (de artifact van CI); leeg: nergens
#   MEDIA_TEST_COMPOSE nog een compose-bestand, als laatste (lokaal: bijv. een lagere nofile
#                      voor een sandbox die 65536 niet geeft); leeg in CI
#
# Het controleert:
# - zonder MEDIA_WORKER_KEY stopt de worker met code 2, en zegt de app (media-status) dat hij
#   niet bereikbaar is, met de reden;
# - de container start met precies de afscherming van compose.media.yaml (docker inspect), en
#   de sleutel staat alleen in de omgeving, niet in command of healthcheck;
# - de zelftest (--selftest) in de container: ok en sandbox "on";
# - id -u is 10002, `touch /x` faalt, /data is leeg, een verbinding met 1.1.1.1:53 faalt;
# - een proces in de container kan /proc/<worker>/fd en /proc/<worker>/environ niet lezen;
# - vanuit de app (uid 10001): /run/media is 2750 van media:platform, de socket 0660 met groep
#   platform, de app kan er niets in maken (V2); caps zegt afgeschermd, en een probe, meta, een
#   frame en een preview (previews.render, zonder ffmpeg in PATH) van een eigen bestand (0600)
#   lukken met de fd vanuit de platform-container;
# - media-status zegt "afgeschermd";
# - de worker gestopt: de app meldt binnen 30 s (de lus van health.py, +1 s) dat hij niet
#   bereikbaar is; weer gestart: media-status zegt weer "afgeschermd".
#
# De sleutel van hello maakt dit script zelf (64 hex), in een .env met modus 0600 in een eigen
# tijdelijke map; hij komt nooit in de uitvoer. Het project, de volumes en de map gaan weg aan
# het eind, ook bij een fout. Exit 1 als een controle faalt.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SINGLE="$(dirname "$HERE")"
IMAGE="${MEDIA_TEST_IMAGE:-platform:local}"
FFMPEG="${MEDIA_TEST_FFMPEG:-7.1.}"
OUT="${MEDIA_TEST_OUT:-}"
PROJECT="mediatest-$$"
SOCK=/run/media/worker.sock
CLEAN_PATH="PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
DOWN_LIMIT=31 # INTERVAL van health.py (30 s) plus 1 s voor de verbinding en het log
FAILED=0
KEY=""

umask 077
WORK="$(mktemp -d "${TMPDIR:-/tmp}/mediatest.XXXXXX")"

compose() {
  docker compose --project-name "$PROJECT" --project-directory "$SINGLE" \
    --env-file "$WORK/.env" "${FILES[@]}" "$@"
}

# Een opdracht in de worker-container, via `env -i`: zonder de omgeving van de container (met de
# sleutel), zoals de healthcheck
in_media() { compose exec -T media /usr/bin/env -i "$CLEAN_PATH" "$@"; }
in_platform() { compose exec -T platform "$@"; }

goed() { echo "goed: $*"; }
fout() {
  echo "FOUT: $*" >&2
  FAILED=1
}

# Iets dat de sleutel zou kunnen bevatten, gaat hier doorheen voor het in het log komt
mask() {
  if [ -n "$KEY" ]; then sed "s/$KEY/<sleutel>/g"; else cat; fi
}

cleanup() {
  local rc=$?
  if [ "$FAILED" != 0 ] || [ "$rc" != 0 ]; then
    echo "=== de laatste regels van de containers ===" >&2
    compose logs --no-color --tail 80 2>&1 | mask >&2 || true
  fi
  compose down -v --remove-orphans --timeout 10 > /dev/null 2>&1 || true
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
  echo "het image $IMAGE is er niet (MEDIA_TEST_IMAGE)" >&2
  exit 1
}

# Het project zonder poorten naar buiten en met een eigen volume voor /data (in plaats van
# DATA_ROOT), op het image van de test
cat > "$WORK/test.yaml" << EOF
services:
  platform:
    image: $IMAGE
    pull_policy: never
    ports: !reset []
    volumes:
      - platform-data:/data
  media:
    image: $IMAGE
volumes:
  platform-data:
EOF

FILES=(-f "$SINGLE/compose.yaml" -f "$SINGLE/compose.media.yaml")
LIMITS=0
if [ "$(docker info --format '{{.MemoryLimit}} {{.CPUCfsQuota}}' 2> /dev/null)" = "true true" ]; then
  FILES+=(-f "$SINGLE/compose.media-limits.yaml")
  LIMITS=1
fi
FILES+=(-f "$WORK/test.yaml")
if [ -n "${MEDIA_TEST_COMPOSE:-}" ]; then FILES+=(-f "$MEDIA_TEST_COMPOSE"); fi

health_ok() {
  in_platform python -I -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).status == 200 else 1)"
}

# --- zonder sleutel ---------------------------------------------------------------------------
# De worker stopt met code 2 (en docker probeert het steeds weer), de app start wel en zegt dat
# de worker niet bereikbaar is, met de reden (media_worker_down)

printf 'DATA_ROOT=/niet-gebruikt\n' > "$WORK/.env"
compose up -d --no-build --pull never > "$WORK/up.txt" 2>&1 || {
  fout "compose up zonder sleutel: $(tail -n 3 "$WORK/up.txt")"
  exit 1
}
wait_for 90 health_ok || {
  fout "de app start niet zonder sleutel"
  exit 1
}
exit_code() { docker inspect --format '{{.State.ExitCode}}' "$(compose ps -aq media)"; }
media_code_2() { [ "$(exit_code)" = 2 ]; }
if wait_for 30 media_code_2 && compose logs --no-color media 2>&1 | grep -q "PLATFORM_MEDIA_WORKER_KEY ontbreekt"; then
  goed "zonder sleutel stopt de worker met code 2, met de reden"
else
  fout "zonder sleutel: de worker stopte met code $(exit_code)"
fi
status="$(in_platform python -m app.cli media-status 2>&1 | tr -d '\r' || true)"
if grep -q "niet bereikbaar (PLATFORM_MEDIA_WORKER_KEY ontbreekt" <<< "$status"; then
  goed "zonder sleutel zegt de app: $status"
else
  fout "zonder sleutel zegt de app: $status"
fi
compose down -v --remove-orphans --timeout 10 > /dev/null 2>&1

KEY="$(python3 -I -c 'import secrets; print(secrets.token_hex(32))')"
printf 'DATA_ROOT=/niet-gebruikt\nMEDIA_WORKER_KEY=%s\n' "$KEY" > "$WORK/.env"
compose config -q

# BOOT uit compose zelf: zo start de test de worker precies zoals compose.media.yaml
BOOT="$(compose config --format json | python3 -I -c '
import json, sys
command = json.load(sys.stdin)["services"]["media"]["command"]
print(command[command.index("-c") + 1])
')"

# --- starten ----------------------------------------------------------------------------------

compose up -d --no-build --pull never
wait_for 90 health_ok || {
  fout "de app start niet"
  exit 1
}
wait_for 60 in_platform test -S "$SOCK" || {
  fout "de socket $SOCK komt er niet"
  exit 1
}
goed "de app en de worker draaien (limieten: $LIMITS)"

# --- de afscherming volgens docker ------------------------------------------------------------

cat > "$WORK/inspect.py" << 'PY'
import json, os, sys

key = os.environ["MEDIA_TEST_KEY"]
limits = os.environ["MEDIA_TEST_LIMITS"] == "1"
media, platform = json.load(sys.stdin)
problems = []


def want(name, got, expected):
    if got != expected:
        problems.append(f"{name}: {got!r} in plaats van {expected!r}")


h, c = media["HostConfig"], media["Config"]
want("user", c.get("User"), "media")
want("network_mode", h.get("NetworkMode"), "none")
want("read_only", h.get("ReadonlyRootfs"), True)
want("cap_drop", h.get("CapDrop"), ["ALL"])
want("cap_add", h.get("CapAdd") or [], [])
want("privileged", h.get("Privileged"), False)
want("security_opt", h.get("SecurityOpt"), ["no-new-privileges:true"])
want("pids_limit", h.get("PidsLimit"), 256)
want("init (geen docker-init: die laat de sleutel lezen)", h.get("Init") or False, False)
ulimits = {u["Name"]: [u["Soft"], u["Hard"]] for u in h.get("Ulimits") or []}
want("ulimit core", ulimits.get("core"), [0, 0])
want("ulimit nofile", ulimits.get("nofile"), [1024, 1024])
tmpfs = set(h.get("Tmpfs") or {})
tmpfs |= {m["Target"] for m in h.get("Mounts") or [] if m.get("Type") == "tmpfs"}
want("tmpfs", sorted(tmpfs), ["/data", "/tmp"])
volumes = sorted(m["Destination"] for m in media.get("Mounts") or [] if m.get("Type") != "tmpfs")
want("volumes (geen anoniem volume op /data)", volumes, ["/run/media"])
want("stop_grace_period media", c.get("StopTimeout"), 5)
want("healthcheck", (c.get("Healthcheck") or {}).get("Test", [])[:3], ["CMD", "/usr/bin/env", "-i"])
if limits:
    want("mem_limit", h.get("Memory"), 3 * 1024**3)
    want("memswap_limit", h.get("MemorySwap"), 3 * 1024**3)
    want("cpus", h.get("NanoCpus"), 3 * 10**9)
pc = platform["Config"]
want("stop_grace_period platform", pc.get("StopTimeout"), 30)
env = dict(e.split("=", 1) for e in pc.get("Env") or [])
want("PLATFORM_MEDIA_ENABLED", env.get("PLATFORM_MEDIA_ENABLED"), "true")
want("PLATFORM_MEDIA_WORKER", env.get("PLATFORM_MEDIA_WORKER"), "/run/media/worker.sock")
for name, container in (("media", media), ("platform", platform)):
    found = container["Config"].get("Env") or []
    if f"PLATFORM_MEDIA_WORKER_KEY={key}" not in found:
        problems.append(f"{name}: de sleutel staat niet in de omgeving")
    rest = json.loads(json.dumps(container))
    rest["Config"].pop("Env", None)
    if key in json.dumps(rest):
        problems.append(f"{name}: de sleutel staat buiten de omgeving (command, healthcheck …)")
for p in problems:
    print(p)
sys.exit(1 if problems else 0)
PY
if docker inspect "$(compose ps -q media)" "$(compose ps -q platform)" \
  | MEDIA_TEST_KEY="$KEY" MEDIA_TEST_LIMITS="$LIMITS" python3 -I "$WORK/inspect.py" > "$WORK/inspect.txt" 2>&1; then
  goed "docker inspect: de afscherming van compose.media.yaml"
else
  fout "docker inspect: $(mask < "$WORK/inspect.txt")"
fi

# --- in de worker-container -------------------------------------------------------------------

uid="$(in_media id -u | tr -d '\r' || true)"
if [ "$uid" = 10002 ]; then goed "id -u is 10002"; else fout "id -u is $uid"; fi
if in_media touch /x > /dev/null 2>&1; then fout "touch /x lukte"; else goed "touch /x faalt (alleen lezen)"; fi
data="$(in_media ls -A /data 2>&1 | tr -d '\r' || true)"
if [ -z "$data" ]; then goed "/data is leeg"; else fout "/data is niet leeg: $data"; fi
if in_media python -I -c 'import socket; socket.create_connection(("1.1.1.1", 53), timeout=3)' > /dev/null 2>&1; then
  fout "een verbinding met 1.1.1.1:53 lukte"
else
  goed "geen netwerk: een verbinding met 1.1.1.1:53 faalt"
fi

cat > "$WORK/peek.py" << 'PY'
import json, os, sys

me = os.getpid()
workers = []
for name in os.listdir("/proc"):
    if not name.isdigit() or int(name) == me:
        continue
    try:
        with open(f"/proc/{name}/cmdline", "rb") as f:
            argv = f.read().split(b"\0")
    except OSError:
        continue
    if os.path.basename(argv[0]).startswith(b"python") and b"--socket" in argv:
        if b"--check" not in argv and b"--selftest" not in argv:
            if any(b"app.media.worker" in a for a in argv):
                workers.append(int(name))
seen = {"workers": workers}
for pid in workers:
    with open(f"/proc/{pid}/status") as f:
        seen["uid"] = next(r.split()[1] for r in f if r.startswith("Uid:"))
    for what in ("fd", "environ"):
        try:
            if what == "fd":
                os.listdir(f"/proc/{pid}/fd")
            else:
                with open(f"/proc/{pid}/environ", "rb") as f:
                    f.read(16)
            seen[what] = "open"
        except PermissionError:
            seen[what] = "dicht"
print(json.dumps(seen))
# de worker is pid 1 (geen init: docker-init zou de omgeving van de container, met de sleutel,
# leesbaar laten voor elk proces van media)
ok = workers == [1] and seen.get("uid") == "10002"
sys.exit(0 if ok and seen.get("fd") == "dicht" and seen.get("environ") == "dicht" else 1)
PY
if in_media python -I - < "$WORK/peek.py" > "$WORK/peek.txt" 2>&1; then
  goed "een proces in de container leest /proc/<worker>/fd en environ niet: $(cat "$WORK/peek.txt")"
else
  fout "/proc/<worker>: $(mask < "$WORK/peek.txt")"
fi

# De zelftest van Keuze 11 en §2 M1 (selftest.full): de harde poort
if in_media python -I -c "$BOOT" /srv --selftest --socket "$SOCK" --ffmpeg "$FFMPEG" \
  > "$WORK/selftest.json" 2> "$WORK/selftest.err"; then
  rc=0
else
  rc=$?
fi
if [ -n "$OUT" ]; then
  mkdir -p "$OUT"
  mask < "$WORK/selftest.json" > "$OUT/selftest.json"
fi
if python3 -I -c '
import json, sys
report = json.load(open(sys.argv[1]))
for c in report["checks"]:
    print(("  goed " if c["ok"] else "  FOUT ") + c["name"] + (": " + c["detail"] if c["detail"] else ""))
sys.exit(0 if report["ok"] and report["sandbox"] == "on" else 1)
' "$WORK/selftest.json" && [ "$rc" = 0 ]; then
  goed "de zelftest: ok, sandbox on"
else
  fout "de zelftest (code $rc): $(tail -n 3 "$WORK/selftest.err" | mask)"
fi

# --- vanuit de app ----------------------------------------------------------------------------

cat > "$WORK/app.py" << 'PY'
import asyncio, grp, os, shutil, stat, subprocess, sys, tempfile
from pathlib import Path

sys.path.insert(0, "/srv")
from app.media import client  # noqa: E402
from app.media.wire import Op  # noqa: E402
from app.media.worker import args as A  # noqa: E402

SOCK = Path(sys.argv[1])
problems = []


def check(name, ok, detail=""):
    print(("goed: " if ok else "FOUT: ") + name + (f" ({detail})" if detail and not ok else ""))
    if not ok:
        problems.append(name)


platform = grp.getgrnam("platform").gr_gid
check("de app is uid 10001", os.getuid() == 10001, os.getuid())
info = os.stat(SOCK.parent)
mode = stat.S_IMODE(info.st_mode)
check("/run/media is 2750 van media:platform",
      (mode, info.st_uid, info.st_gid) == (0o2750, 10002, platform),
      f"{mode:04o} {info.st_uid}:{info.st_gid}")
info = os.stat(SOCK)
mode = stat.S_IMODE(info.st_mode)
check("de socket is 0660 met groep platform",
      stat.S_ISSOCK(info.st_mode) and mode == 0o660 and info.st_gid == platform,
      f"{mode:04o} gid {info.st_gid}")
try:
    (SOCK.parent / "van-de-app").write_bytes(b"")
    check("de app kan niets maken in /run/media", False, "het lukte")
except PermissionError:
    check("de app kan niets maken in /run/media", True)

film = Path("/tmp/m1-test.mp4")
subprocess.run(
    ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
     "testsrc2=size=320x240:rate=25", "-f", "lavfi", "-i", "sine=frequency=440", "-t", "2",
     "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(film)],
    check=True,
)
os.chmod(film, 0o600)  # zoals een blob: alleen de app kan hem openen


def read_all(fd):
    chunks = []
    try:
        while chunk := os.read(fd, 65536):
            chunks.append(chunk)
    finally:
        os.close(fd)
    return b"".join(chunks)


async def main():
    client.bind(SOCK, os.environ["PLATFORM_MEDIA_WORKER_KEY"])
    caps = await client.caps()
    check("caps: een container, afgeschermd", (caps.mode, caps.sandbox) == ("container", "on"),
          f"{caps.mode}, {caps.sandbox}")
    check("caps: niets ontbreekt", caps.ffmpeg is not None and not caps.missing,
          f"ffmpeg {caps.ffmpeg}, mist {caps.missing}")
    probe = await client.probe(film)
    kinds = sorted(s.type for s in probe.streams)
    check("probe van een bestand van de app (0600)",
          probe.state == "ready" and kinds == ["audio", "video"] and probe.duration_ms,
          f"{probe.state} {kinds} {probe.duration_ms}")
    job, _ = await client.call(Op.META, A.MetaArgs(), source=film)
    async with job:
        meta = await job.result()
    video = next((s for s in meta.get("streams", []) if s.get("codec_type") == "video"), None)
    check("meta", meta.get("demuxer") == "mov" and video is not None, str(meta)[:200])
    args = A.FrameArgs(demuxer="mov", video_index=video["index"] if video else 0, at_s=0.5, width=320)
    job, (r,) = await client.call(Op.FRAME, args, source=film, outputs=1)
    async with job:
        data = await asyncio.to_thread(read_all, r)
        result = await job.result()
    check("frame: een JPEG over een pipe van de app", data[:3] == b"\xff\xd8\xff"
          and result.get("frames") == 1, f"{len(data)} bytes, {result}")

    # Een preview zoals de taak hem maakt (previews.render: het renderproces vraagt de worker),
    # met een PATH zonder ffmpeg: het beeld kan alleen van de worker komen
    from app import previews
    from app.config import get_settings

    settings = get_settings()
    work = Path(tempfile.mkdtemp(prefix="m1-preview-"))
    out, scratch = work / "out", work / "work"
    out.mkdir()
    scratch.mkdir()
    request = {"source": str(film), "out": str(out), "work": str(scratch), "mime": "video/mp4",
               "name": film.name, "max_pixels": settings.preview_max_pixels,
               "memory_mb": settings.preview_memory_mb}
    os.environ["PATH"] = "/nergens"
    result = await previews.render(settings, request, scratch)
    sizes = sorted(p.name for p in out.glob("*.webp"))
    check("een preview van een video via de worker (zonder ffmpeg in PATH)",
          result.get("state") == "ready" and "256.webp" in sizes, f"{result.get('state')} {sizes}")
    shutil.rmtree(work, ignore_errors=True)


asyncio.run(main())
film.unlink()
sys.exit(1 if problems else 0)
PY
if in_platform python -I - "$SOCK" < "$WORK/app.py" > "$WORK/app.txt" 2>&1; then
  cat "$WORK/app.txt"
  goed "vanuit de app: map, socket, caps, probe, meta, frame en een preview"
else
  mask < "$WORK/app.txt" >&2
  fout "vanuit de app"
fi

status="$(in_platform python -m app.cli media-status --wait 30 2>&1 | tr -d '\r' || true)"
if grep -q "afgeschermd" <<< "$status"; then
  goed "media-status: $status"
else
  fout "media-status: $(mask <<< "$status")"
fi

# --- de worker weg, en weer terug -------------------------------------------------------------

since="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
compose stop media > /dev/null 2>&1
stopped="$(date +%s)"
seen=""
while [ $(($(date +%s) - stopped)) -le $((DOWN_LIMIT + 5)) ]; do
  if compose logs --no-color --since "$since" platform 2>&1 | grep -q "mediaworker niet bereikbaar"; then
    seen="$(($(date +%s) - stopped))"
    break
  fi
  sleep 1
done
if [ -n "$seen" ] && [ "$seen" -le "$DOWN_LIMIT" ]; then
  goed "de worker gestopt: de app meldt het na $seen s"
else
  fout "de worker gestopt: de app meldde het niet binnen $DOWN_LIMIT s (${seen:-niet gezien})"
fi

compose start media > /dev/null 2>&1
status="$(in_platform python -m app.cli media-status --wait 60 2>&1 | tr -d '\r' || true)"
if grep -q "afgeschermd" <<< "$status"; then
  goed "weer gestart: $status"
else
  fout "weer gestart: $(mask <<< "$status")"
fi

if [ "$FAILED" = 0 ]; then
  echo "test_media_container: alles goed"
else
  echo "test_media_container: er faalde iets" >&2
fi
