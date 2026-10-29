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
# - /data is een tmpfs van 0700 van media (10002), /tmp een tmpfs van 1777 (ook in docker
#   inspect: de opties van de tmpfs);
# - een proces in de container kan /proc/<worker>/fd en /proc/<worker>/environ niet lezen;
# - vanuit de app (uid 10001): /run/media is 2750 van media:platform, de socket 0660 met groep
#   platform, de app kan er niets in maken (V2); caps zegt afgeschermd, en een probe, meta, een
#   frame en een preview (previews.render, zonder ffmpeg in PATH) van een eigen bestand (0600)
#   lukken met de fd vanuit de platform-container;
# - een blob van de app (0600 van 10001) en een pipe van de app, over SCM_RIGHTS naar een proces
#   van media dat wel dumpbaar is (de tweede laag van Keuze 11, los van PR_SET_DUMPABLE; S3 van
#   de spike, review golf 1 K1): de fd is CLOEXEC, het pad van de app bestaat daar niet,
#   schrijven geeft EBADF, opnieuw openen via /proc/self/fd en een kind via /proc/<pid>/fd
#   lukken niet (een eigen bestand wel: de controle), een stuk midden in de blob heeft de hash
#   van de app, de pipe is van de app, en de blob bleef gelijk;
# - de stroom (M4): S3 punt 17 van de spike (een remux in de worker van een fd van de app naar
#   fMP4 op een pipe van de app; de boxen met de knipper van de app), een transcode op het
#   rooster van 2 s, en R12: tijdens die transcode één ffmpeg in de container van media en geen
#   in die van de app;
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
# de opties van de tmpfs (zoals S3 van de spike): /data alleen van media, /tmp 1777
options = {k: set(v.split(",")) for k, v in (h.get("Tmpfs") or {}).items()}
want("tmpfs /data", sorted(options.get("/data", set()) & {"mode=0700", "uid=10002", "gid=10002"}),
     ["gid=10002", "mode=0700", "uid=10002"])
want("tmpfs /tmp", "mode=1777" in options.get("/tmp", set()), True)
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
cat > "$WORK/tmpfs.py" << 'PY'
import json, os, stat, sys

kinds = {}
with open("/proc/self/mountinfo") as f:
    for line in f:
        parts = line.split()
        kinds[parts[4]] = parts[parts.index("-") + 1]
seen = {}
for path in ("/data", "/tmp"):
    info = os.stat(path)
    seen[path] = [kinds.get(path), f"{stat.S_IMODE(info.st_mode):04o}", info.st_uid, info.st_gid]
print(json.dumps(seen))
ok = seen["/data"] == ["tmpfs", "0700", 10002, 10002] and seen["/tmp"][:2] == ["tmpfs", "1777"]
sys.exit(0 if ok else 1)
PY
if in_media python -I - < "$WORK/tmpfs.py" > "$WORK/tmpfs.txt" 2>&1; then
  goed "/data is een tmpfs van 0700 van media, /tmp een tmpfs van 1777: $(cat "$WORK/tmpfs.txt")"
else
  fout "de tmpfs in de worker: $(mask < "$WORK/tmpfs.txt")"
fi
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

# --- een fd van de app bij een proces van media dat dumpbaar is (review golf 1, K1) -------------
# Keuze 11 heeft twee lagen: de worker is niet dumpbaar (PR_SET_DUMPABLE 0, hierboven en in de
# zelftest), en een blob is 0600 van de app. Dit toetst de tweede laag los van de eerste, zoals
# S3 van de spike: een proces van media dat wel dumpbaar is (python is leesbaar; het staat voor
# een worker die PR_SET_DUMPABLE kwijt is), krijgt van de app een blob en een pipe over een
# eigen socket in /run/media, met wire.send en wire.receive zoals de worker.

K1_SOCK=/run/media/k1-proef.sock
cat > "$WORK/k1_media.py" << 'PY'
import asyncio, contextlib, ctypes, errno, hashlib, json, os, socket, stat, subprocess, sys
import tempfile

sys.path.insert(0, "/srv")
from app.media import wire  # noqa: E402

PATH = sys.argv[1]
PR_GET_DUMPABLE = 3
# Een kind (zelfde uid) opent een fd van zijn ouder via /proc: de blob van de app, en een
# eigen bestand van media (de controle: de ouder is dumpbaar, dus dat lukt wel)
LOOK = r"""
import json, os, sys
pid, fds = sys.argv[1], sys.argv[2:]
out = {}
for name, fd in zip(("blob", "eigen"), fds):
    try:
        os.close(os.open(f"/proc/{pid}/fd/{fd}", os.O_RDONLY | os.O_NONBLOCK))
        out[name] = "open"
    except OSError as e:
        out[name] = type(e).__name__
print(json.dumps(out))
"""


def err(e):
    return errno.errorcode.get(e.errno, str(e.errno))


async def main():
    libc = ctypes.CDLL(None, use_errno=True)
    seen = {"uid": os.getuid(), "dumpbaar": libc.prctl(PR_GET_DUMPABLE, 0, 0, 0, 0)}
    with contextlib.suppress(FileNotFoundError):
        os.unlink(PATH)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
    srv.bind(PATH)
    os.chmod(PATH, 0o660)  # groep platform (de map is setgid)
    srv.listen(1)
    srv.setblocking(False)
    loop = asyncio.get_running_loop()
    try:
        conn, _ = await asyncio.wait_for(loop.sock_accept(srv), 60)
        conn.setblocking(False)
        message, fds = await asyncio.wait_for(wire.receive(conn, max_fds=2), 30)
        blob, pipe = fds
        info = os.fstat(blob)
        link = os.readlink(f"/proc/self/fd/{blob}")
        seen["blob"] = {
            "uid": info.st_uid, "modus": f"{stat.S_IMODE(info.st_mode):04o}",
            "cloexec": not os.get_inheritable(blob), "pad_hier": os.path.exists(link),
        }
        part = os.pread(blob, int(message["lengte"]), int(message["begin"]))
        seen["sha_stuk"] = hashlib.sha256(part).hexdigest()
        try:
            os.write(blob, b"x")
            seen["schrijven"] = "geschreven"
        except OSError as e:
            seen["schrijven"] = err(e)
        seen["heropen"] = {}
        for name, flag in (("O_RDONLY", os.O_RDONLY), ("O_WRONLY", os.O_WRONLY)):
            try:
                os.close(os.open(f"/proc/self/fd/{blob}", flag | os.O_CLOEXEC))
                seen["heropen"][name] = "open"
            except OSError as e:
                seen["heropen"][name] = err(e)
        with tempfile.TemporaryFile() as own:
            done = subprocess.run(
                [sys.executable, "-I", "-c", LOOK, str(os.getpid()), str(blob), str(own.fileno())],
                capture_output=True, text=True, timeout=20, check=False,
            )
        seen["kind"] = json.loads(done.stdout) if done.returncode == 0 else done.stderr[-200:]
        info = os.fstat(pipe)
        seen["pipe"] = {"uid": info.st_uid, "fifo": stat.S_ISFIFO(info.st_mode)}
        os.set_blocking(pipe, True)
        os.write(pipe, b"van media\n")
        wire.close_all(fds)
        await wire.send(conn, {"ev": "result", "data": seen})
        conn.close()
    finally:
        srv.close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(PATH)
    print(json.dumps(seen))
    blob_ok = seen["blob"] == {"uid": 10001, "modus": "0600", "cloexec": True, "pad_hier": False}
    ok = (
        seen["uid"] == 10002 and seen["dumpbaar"] == 1 and blob_ok
        and seen["schrijven"] == "EBADF"
        and seen["heropen"] == {"O_RDONLY": "EACCES", "O_WRONLY": "EACCES"}
        and seen["kind"] == {"blob": "PermissionError", "eigen": "open"}
        and seen["pipe"] == {"uid": 10001, "fifo": True}
    )
    sys.exit(0 if ok else 1)


asyncio.run(main())
PY
cat > "$WORK/k1_app.py" << 'PY'
import asyncio, hashlib, json, os, socket, sys, tempfile

sys.path.insert(0, "/srv")
from app.media import wire  # noqa: E402

PATH = sys.argv[1]
BEGIN, LENGTH = 300_000, 65_536


def sha(path, begin=0, length=None):
    with open(path, "rb") as f:
        f.seek(begin)
        return hashlib.sha256(f.read() if length is None else f.read(length)).hexdigest()


async def main():
    fd, path = tempfile.mkstemp(dir="/data", prefix="k1-blob-")  # 0600 van de app, zoals een blob
    os.write(fd, os.urandom(1024 * 1024))
    os.close(fd)
    before = sha(path)
    blob = os.open(path, os.O_RDONLY | os.O_CLOEXEC)
    r, w = os.pipe2(os.O_CLOEXEC)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM | socket.SOCK_CLOEXEC)
    sock.setblocking(False)
    try:
        await asyncio.get_running_loop().sock_connect(sock, PATH)
        await wire.send(sock, {"begin": BEGIN, "lengte": LENGTH}, [blob, w])
        os.close(w)
        w = -1
        reply, extra = await asyncio.wait_for(wire.receive(sock, max_fds=0), 60)
        wire.close_all(extra)
        piped = b""
        while chunk := os.read(r, 4096):
            piped += chunk
    finally:
        sock.close()
        os.close(blob)
        os.close(r)
        if w >= 0:
            os.close(w)
    seen = reply.get("data") or {}
    out = {
        "hash_stuk": seen.get("sha_stuk") == sha(path, BEGIN, LENGTH),
        "pipe": piped == b"van media\n",
        "blob_gelijk": sha(path) == before,
    }
    os.unlink(path)
    print(json.dumps(out))
    sys.exit(0 if all(out.values()) else 1)


asyncio.run(main())
PY
in_media python -I - "$K1_SOCK" < "$WORK/k1_media.py" > "$WORK/k1_media.txt" 2>&1 &
k1_pid=$!
if wait_for 30 in_platform test -S "$K1_SOCK" \
  && in_platform python -I - "$K1_SOCK" < "$WORK/k1_app.py" > "$WORK/k1_app.txt" 2>&1; then
  k1_app=0
else
  k1_app=1
fi
if wait "$k1_pid"; then k1_media=0; else k1_media=1; fi
if [ "$k1_app" = 0 ] && [ "$k1_media" = 0 ]; then
  goed "een blob van de app (0600) bij een proces van media dat dumpbaar is: CLOEXEC, geen pad, EBADF, niet heropenbaar, niet via /proc; hash, pipe en blob kloppen"
  echo "  media: $(cat "$WORK/k1_media.txt")"
  echo "  app: $(cat "$WORK/k1_app.txt")"
else
  fout "een blob van de app bij een proces van media: media $(mask < "$WORK/k1_media.txt"), app $(mask < "$WORK/k1_app.txt" 2> /dev/null)"
fi

# --- de stroom (M4): S3 punt 17 en R12 -----------------------------------------------------------
# S3 punt 17 van de spike, nu met stream van M4: de remux in de worker leest de fd van de app en
# schrijft fMP4 naar een pipe van de app; de app knipt met zijn eigen knipper (fmp4.Cutter) en
# kijkt naar de boxen: één init met beeld en geluid, een fragment per GOP met een keyframe
# vooraan, op mediatijd. Daarna een transcode op het rooster van 2 s, en R12 (§4.6): zolang die
# loopt (de app leest de pipe nog niet, dus ffmpeg wacht erop) draait er één ffmpeg in de
# container van media en geen in die van de app (docker top, en /proc in de app zelf).

R12_HOLD=/tmp/r12-klaar
cat > "$WORK/m4.py" << 'PY'
import asyncio, os, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, "/srv")
from app.media import client, fmp4, grid  # noqa: E402
from app.media.wire import Op  # noqa: E402
from app.media.worker import args as A  # noqa: E402

SOCK, HOLD = Path(sys.argv[1]), Path(sys.argv[2])
problems = []


def check(name, ok, detail=""):
    print(("goed: " if ok else "FOUT: ") + name + (f" ({detail})" if detail else ""), flush=True)
    if not ok:
        problems.append(name)


# Een film zoals de spike: Matroska, H.264 met B-frames en een keyframe per 2 s, en AAC
film = Path("/tmp/m4-film.mkv")
subprocess.run(
    ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i",
     "testsrc2=size=320x240:rate=25", "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000",
     "-t", "6", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-bf", "2",
     "-x264-params", "keyint=50:min-keyint=50:scenecut=0", "-c:a", "aac", "-shortest", str(film)],
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


def cut(data):
    """Met de knipper van de app (fmp4.Cutter, begrensd): de init en de fragmenten."""
    cutter = fmp4.Cutter()
    items = cutter.feed(data)
    cutter.close()
    inits = [i for i in items if isinstance(i, fmp4.Init)]
    frags = [i for i in items if isinstance(i, fmp4.Fragment) and i.video_start_ms is not None]
    return inits, frags


def ffmpeg_here():
    """De ffmpeg-processen die deze container ziet (/proc: alleen de eigen pid-namespace)."""
    found = []
    for entry in Path("/proc").iterdir():
        if entry.name.isdigit():
            try:
                if (entry / "comm").read_text().strip() == "ffmpeg":
                    found.append(int(entry.name))
            except OSError:
                continue
    return found


async def stream(args, *, hold=False):
    job, (r,) = await client.call(Op.STREAM, args, source=film, outputs=1)
    async with job:
        if hold:
            # R12: de app leest de pipe nog niet; ffmpeg loopt in de worker en wacht op de pipe
            print("R12: loopt", flush=True)
            for _ in range(900):
                if HOLD.exists():
                    break
                await asyncio.sleep(0.1)
            here = ffmpeg_here()
            check("R12: tijdens de transcode geen ffmpeg in de container van de app",
                  HOLD.exists() and not here, f"{len(here)} ffmpeg, teken {HOLD.exists()}")
        began = time.monotonic()
        data = await asyncio.to_thread(read_all, r)
        result = await job.result()
    return data, result, time.monotonic() - began


async def main():
    client.bind(SOCK, os.environ["PLATFORM_MEDIA_WORKER_KEY"])
    probe = await client.probe(film)
    video, audio = probe.video(), probe.audio_streams()[0]
    common = dict(
        demuxer=A.demuxer_of(probe.container), source_start_s=round(probe.start_ms / 1000, 3),
        video_index=video.index, audio_index=audio.index, audio="copy",
    )
    # S3 punt 17: de remux in de worker leest de fd en schrijft fMP4 naar een pipe van de app
    data, result, took = await stream(A.StreamArgs(mode="remux", video="copy", **common))
    inits, frags = cut(data)
    starts = [f.video_start_ms for f in frags]
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    detail = (f"{len(data)} bytes in {took:.2f} s, {len(inits)} init, starts {starts}, "
              f"trun {sorted({f.trun_version for f in frags})}, {result}")
    check("S3 punt 17: remux van de fd naar fMP4 op een pipe van de app (init met beeld en "
          "geluid, een fragment per GOP met een keyframe vooraan, op mediatijd)",
          len(inits) == 1 and inits[0].video is not None and inits[0].audio is not None
          and len(frags) == 3 and all(f.sync for f in frags) and 0 <= starts[0] <= 100
          and all(abs(g - 2000) <= 40 for g in gaps), detail)
    # De transcode op het rooster van 2 s, en R12: ffmpeg draait alleen in de worker
    layout = grid.transcode_layout(probe)
    encode = A.VideoEncode(codec="libx264", width=320, height=240, preset="veryfast", level="3.0",
                           maxrate_k=800)
    args = A.StreamArgs(mode="transcode", video=encode, grid="2s", **common)
    data, result, took = await stream(args, hold=True)
    inits, frags = cut(data)
    starts = [f.video_start_ms for f in frags]
    on = [layout.segment_of(t + 1) for t in starts]
    check("de transcode: een segment per 2 s van het rooster, elk met een keyframe vooraan",
          len(inits) == 1 and on == list(range(layout.count)) and all(f.sync for f in frags)
          and abs(starts[0] - layout.grid.origin_ms) <= 40,
          f"{len(data)} bytes, starts {starts}, segmenten {on} van {layout.count}, {result}")


asyncio.run(main())
film.unlink()
sys.exit(1 if problems else 0)
PY
in_platform python -I - "$SOCK" "$R12_HOLD" < "$WORK/m4.py" > "$WORK/m4.txt" 2>&1 &
m4_pid=$!
r12_running() { grep -q "R12: loopt" "$WORK/m4.txt"; }
# docker top filtert op de pid's van de container; het heeft de kolom pid nodig
ffmpeg_in() { docker top "$1" -eo pid,comm 2> /dev/null | awk 'NR > 1 && $2 == "ffmpeg"' | wc -l | tr -d ' '; }
if wait_for 120 r12_running; then
  n_media="$(ffmpeg_in "$(compose ps -q media)")"
  n_app="$(ffmpeg_in "$(compose ps -q platform)")"
  if [ "$n_media" = 1 ] && [ "$n_app" = 0 ]; then
    goed "R12: tijdens een transcode één ffmpeg in de container van media, geen in die van de app (docker top)"
  else
    fout "R12: ffmpeg in media $n_media, in de app $n_app (docker top)"
  fi
else
  fout "R12: de transcode liep niet: $(mask < "$WORK/m4.txt" | tail -n 5)"
fi
in_platform touch "$R12_HOLD" || true
if wait "$m4_pid"; then m4_rc=0; else m4_rc=1; fi
if [ "$m4_rc" = 0 ]; then
  cat "$WORK/m4.txt"
  goed "de stroom (M4): S3 punt 17 (remux naar een pipe van de app) en een transcode op het rooster"
else
  mask < "$WORK/m4.txt" >&2
  fout "de stroom (M4)"
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
