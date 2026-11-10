#!/usr/bin/env bash
# OCR in de worker-container (bouwontwerp fase 6, X1; Keuze 22 en 24): tesseract zit in het image
# van de app, maar draait alleen in de container van media. Met de echte compose (compose.yaml +
# compose.media.yaml, en compose.media-limits.yaml als docker die grenzen kan geven), zoals
# test_media_container.sh.
#
#   OCR_TEST_IMAGE=platform:ci bash infra/single/tests/test_ocr_container.sh
#
#   OCR_TEST_IMAGE    het image van de app (standaard platform:local)
#   OCR_TEST_OUT      een map voor ocr.txt (de regels goed en FOUT: de regel van X1 onder
#                     "=== kort ===" in CI) en handdruk_ocr.json; leeg: nergens
#   OCR_TEST_COMPOSE  nog een compose-bestand, als laatste (lokaal: bijv. een lagere nofile voor
#                     een sandbox die 65536 niet geeft); leeg in CI
#
# Het image is alleen in CI te bouwen (job media-image); daar draait dit script.
#
# Het controleert:
# - /usr/bin/tesseract is 0711 van root, net als nice, ionice en env (de keten van kind 2):
#   media en de app mogen hem uitvoeren, niet lezen;
# - tesseract kent eng, nld en osd (--list-langs), en leest als media een kleine gemaakte PGM
#   van stdin: de woorden komen terug;
# - vanuit de app (uid 10001): caps zegt ocr true, en een PNG van de app (0600, drie regels
#   tekst) gaat als fd naar de worker (Op.OCR): state done, de woorden, de maat, geen draaiing;
# - tesseract alleen in de worker-container: tijdens OCR-opdrachten van de app ziet docker top
#   tesseract in de container van media en nooit in die van de app;
# - de handdruk van ocr als media (api/tests/ocr/handdruk_ocr.py, nu met de echte keten van het
#   image en kind 1 uit python-tekst): een tweede proces van media kan /proc/<kind>/fd en
#   /proc/<kind>/mem van kind 1 en kind 2 niet openen (alleen EACCES), ook niet in de eerste
#   milliseconden; nooit de inhoud van de blob.
#
# De uitvoer noemt geen herkende tekst: alleen of de woorden van de test gevonden zijn, en
# tellers. De sleutel van hello maakt dit script zelf (64 hex, in een .env van 0600 in een eigen
# tijdelijke map); hij komt nooit in de uitvoer. Het project, de volumes en de map gaan weg aan
# het eind, ook bij een fout. Exit 1 als een controle faalt.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SINGLE="$(dirname "$HERE")"
TESTS="$(dirname "$(dirname "$SINGLE")")/api/tests" # handdruk_ocr.py en handdruk_tekst.py
IMAGE="${OCR_TEST_IMAGE:-platform:local}"
OUT="${OCR_TEST_OUT:-}"
PROJECT="ocrtest-$$"
SOCK=/run/media/worker.sock
STOP="/tmp/ocr-stop-$$" # in de container van de app: de OCR-opdrachten mogen stoppen
CLEAN_PATH="PATH=/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"
FAILED=0
KEY=""

umask 077
WORK="$(mktemp -d "${TMPDIR:-/tmp}/ocrtest.XXXXXX")"
if [ -n "$OUT" ]; then
  mkdir -p "$OUT"
  : > "$OUT/ocr.txt"
fi

compose() {
  docker compose --project-name "$PROJECT" --project-directory "$SINGLE" \
    --env-file "$WORK/.env" "${FILES[@]}" "$@"
}

# Een opdracht in de worker-container, via `env -i`: zonder de omgeving van de container (met de
# sleutel), zoals de healthcheck
in_media() { compose exec -T media /usr/bin/env -i "$CLEAN_PATH" "$@"; }
in_platform() { compose exec -T platform "$@"; }

# Iets dat de sleutel zou kunnen bevatten, gaat hier doorheen voor het in het log komt
mask() {
  if [ -n "$KEY" ]; then sed "s/$KEY/<sleutel>/g"; else cat; fi
}

regel() {
  echo "$*"
  if [ -n "$OUT" ]; then echo "$*" | mask >> "$OUT/ocr.txt"; fi
}
goed() { regel "goed: $*"; }
fout() {
  regel "FOUT: $*" >&2
  FAILED=1
}

cleanup() {
  local rc=$?
  if [ "$FAILED" != 0 ] || [ "$rc" != 0 ]; then
    echo "=== de laatste regels van de containers ===" >&2
    compose logs --no-color --tail 60 2>&1 | mask >&2 || true
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
  echo "het image $IMAGE is er niet (OCR_TEST_IMAGE)" >&2
  exit 1
}

# Het project zonder poorten naar buiten en met een eigen volume voor /data, op het image van de
# test. api/tests alleen te lezen in allebei: de app maakt er niets mee, de worker-container
# draait er de handdruk van ocr mee (zoals test_media_container.sh die van tekst)
cat > "$WORK/test.yaml" << EOF
services:
  platform:
    image: $IMAGE
    pull_policy: never
    ports: !reset []
    volumes:
      - platform-data:/data
      - "$TESTS:/srv/tests:ro"
  media:
    image: $IMAGE
    volumes:
      - "$TESTS:/srv/tests:ro"
volumes:
  platform-data:
EOF

FILES=(-f "$SINGLE/compose.yaml" -f "$SINGLE/compose.media.yaml")
if [ "$(docker info --format '{{.MemoryLimit}} {{.CPUCfsQuota}}' 2> /dev/null)" = "true true" ]; then
  FILES+=(-f "$SINGLE/compose.media-limits.yaml")
fi
FILES+=(-f "$WORK/test.yaml")
if [ -n "${OCR_TEST_COMPOSE:-}" ]; then FILES+=(-f "$OCR_TEST_COMPOSE"); fi

KEY="$(python3 -I -c 'import secrets; print(secrets.token_hex(32))')"
printf 'DATA_ROOT=/niet-gebruikt\nMEDIA_WORKER_KEY=%s\n' "$KEY" > "$WORK/.env"
compose config -q

health_ok() {
  in_platform python -I -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=3).status == 200 else 1)"
}

compose up -d --no-build --pull never > "$WORK/up.txt" 2>&1 || {
  fout "compose up: $(tail -n 3 "$WORK/up.txt" | mask)"
  exit 1
}
wait_for 90 health_ok || {
  fout "de app start niet"
  exit 1
}
wait_for 60 in_platform test -S "$SOCK" || {
  fout "de socket $SOCK komt er niet"
  exit 1
}

# --- het programma ----------------------------------------------------------------------------

modes="$(in_media stat -c '%n %U %a' /usr/bin/tesseract /usr/bin/nice /usr/bin/ionice /usr/bin/env 2>&1 | tr -d '\r' || true)"
if [ "$(awk '$2 == "root" && $3 == "711"' <<< "$modes" | wc -l | tr -d ' ')" = 4 ]; then
  goed "tesseract, nice, ionice en env zijn 0711 van root"
else
  fout "de modus van de keten: $(tr '\n' ';' <<< "$modes")"
fi
leesbaar=""
if in_media head -c 1 /usr/bin/tesseract > /dev/null 2>&1; then leesbaar="$leesbaar media"; fi
if in_platform head -c 1 /usr/bin/tesseract > /dev/null 2>&1; then leesbaar="$leesbaar app"; fi
if [ -z "$leesbaar" ]; then
  goed "media en de app kunnen /usr/bin/tesseract niet lezen"
else
  fout "/usr/bin/tesseract is te lezen voor:$leesbaar"
fi

versie="$(in_media tesseract --version 2>&1 | head -n 1 | tr -d '\r' || true)"
talen="$(in_media tesseract --list-langs 2>&1 | tail -n +2 | tr -d '\r' | sort | tr '\n' ' ' || true)"
ontbreekt=""
for taal in eng nld osd; do
  if ! grep -qw "$taal" <<< "$talen"; then ontbreekt="$ontbreekt $taal"; fi
done
if [ -z "$ontbreekt" ]; then
  goed "$versie; talen: $talen"
else
  fout "tesseract ($versie) mist:$ontbreekt (talen: $talen)"
fi

# Een kleine PGM, als media gemaakt (de lettervorm van Pillow zelf: in het image staat geen
# DejaVu) en op stdin van tesseract, zoals kind 2 hem krijgt
cat > "$WORK/pgm.py" << 'PY'
import io, os, re, subprocess, sys
from PIL import Image, ImageDraw, ImageFont

REGELS = ["De winkel aan de gracht", "verkoopt verse appels", "kassabon nummer 4711"]
font = ImageFont.load_default(size=44)
img = Image.new("L", (1240, 420), 255)
draw = ImageDraw.Draw(img)
for i, tekst in enumerate(REGELS):
    draw.text((60, 50 + i * 110), tekst, font=font, fill=0)
buf = io.BytesIO()
img.save(buf, "PPM")  # grijs: P5
pgm = buf.getvalue()
assert pgm[:3] == b"P5\n", pgm[:3]
done = subprocess.run(
    ["tesseract", "stdin", "stdout", "-l", "nld+eng", "--psm", "3"], input=pgm,
    capture_output=True, timeout=60,
    env={"PATH": os.environ["PATH"], "OMP_THREAD_LIMIT": "1", "LC_ALL": "C", "HOME": "/tmp"},
)
gevonden = set(re.findall(r"\w+", done.stdout.decode("utf-8", "replace").lower()))
verwacht = {w.lower() for r in REGELS for w in r.split()}
mist = sorted(verwacht - gevonden)
print(f"code {done.returncode}, {len(pgm)} bytes PGM, {len(verwacht) - len(mist)} van de "
      f"{len(verwacht)} woorden" + (f", mist {mist}" if mist else ""))
sys.exit(0 if done.returncode == 0 and not mist else 1)
PY
if in_media python -I - < "$WORK/pgm.py" > "$WORK/pgm.txt" 2>&1; then
  goed "tesseract als media op een gemaakte PGM: $(tail -n 1 "$WORK/pgm.txt")"
else
  fout "tesseract als media op een gemaakte PGM: $(tail -n 3 "$WORK/pgm.txt" | mask)"
fi

# --- vanuit de app, en docker top -------------------------------------------------------------

cat > "$WORK/app.py" << 'PY'
import asyncio, io, os, re, sys, time
from pathlib import Path

sys.path.insert(0, sys.argv[2])
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

from app.media import client  # noqa: E402
from app.media.wire import Op  # noqa: E402
from app.media.worker import args as A  # noqa: E402

SOCK, STOP = Path(sys.argv[1]), Path(sys.argv[3])
REGELS = ["De winkel aan de gracht", "verkoopt verse appels", "kassabon nummer 4711"]
SIZE = (1240, 420)
problems = []


def check(name, ok, detail=""):
    print(("goed: " if ok else "FOUT: ") + name + (f" ({detail})" if detail else ""), flush=True)
    if not ok:
        problems.append(name)


def png() -> Path:
    font = ImageFont.load_default(size=44)
    img = Image.new("L", SIZE, 255)
    draw = ImageDraw.Draw(img)
    for i, tekst in enumerate(REGELS):
        draw.text((60, 50 + i * 110), tekst, font=font, fill=0)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    path = Path(f"/tmp/ocr-test-{os.getpid()}.png")
    path.write_bytes(buf.getvalue())
    os.chmod(path, 0o600)  # zoals een blob: alleen de app kan hem openen
    return path


async def ocr(path):
    job, _ = await client.call(Op.OCR, A.OcrArgs(kind="image"), source=path)
    async with job:
        events = [e async for e in job.events()]
    words = [w for e in events[:-1] for w in e["data"]["words"]]
    return words, events[-1]["data"]


async def main():
    client.bind(SOCK, os.environ["PLATFORM_MEDIA_WORKER_KEY"])
    caps = await client.caps()
    check("caps zegt ocr", caps.ocr is True, f"ocr {caps.ocr}")
    path = png()
    try:
        began = time.monotonic()
        words, summary = await ocr(path)
        took = time.monotonic() - began
        found = {re.sub(r"\W", "", w[0]).lower() for w in words}
        verwacht = {w.lower() for r in REGELS for w in r.split()}
        mist = sorted(verwacht - found)
        page = (summary["width"], summary["height"], summary["source"], summary["rotate"])
        check("Op.OCR van een PNG van de app (0600) met de fd: done, de woorden, de maat",
              summary["state"] == "done" and summary["code"] is None and not mist
              and page == (*SIZE, list(SIZE), 0) and abs(summary["skew"]) <= 0.3,
              f"{summary['state']} {summary['code']} {summary['error']}, "
              f"{len(verwacht) - len(mist)} van de {len(verwacht)} woorden"
              + (f", mist {mist}" if mist else "")
              + f", {summary['width']}x{summary['height']}, skew {summary['skew']}, "
              f"conf {summary['conf']}, ms {summary['ms']}, {took:.2f} s")
        # Nu steeds weer, tot het script docker top heeft bekeken (of 90 s)
        print("OCR: herhaalt", flush=True)
        rounds, end = 0, time.monotonic() + 90
        while not STOP.exists() and time.monotonic() < end:
            _, summary = await ocr(path)
            rounds += 1
            if summary["state"] != "done":
                check("de herhaling", False, f"{summary['state']} {summary['code']}")
                break
        print(f"herhaald: {rounds}", flush=True)
    finally:
        path.unlink()


asyncio.run(main())
sys.exit(1 if problems else 0)
PY
in_platform rm -f "$STOP"
in_platform python -I - "$SOCK" /srv "$STOP" < "$WORK/app.py" > "$WORK/app.txt" 2>&1 &
app_pid=$!
herhaalt() { grep -q "^OCR: herhaalt" "$WORK/app.txt"; }
# docker top filtert op de pid's van de container; het heeft de kolom pid nodig
tesseract_in() { { docker top "$1" -eo pid,comm 2> /dev/null || true; } | awk 'NR > 1 && $2 == "tesseract"' | wc -l | tr -d ' '; }
if wait_for 120 herhaalt; then
  media_id="$(compose ps -q media)"
  app_id="$(compose ps -q platform)"
  keer=0
  in_media_gezien=0
  in_app_gezien=0
  start="$(date +%s)"
  while [ "$in_media_gezien" -lt 5 ] && [ $(($(date +%s) - start)) -lt 60 ]; do
    n_media="$(tesseract_in "$media_id")"
    n_app="$(tesseract_in "$app_id")"
    keer=$((keer + 1))
    if [ "$n_media" -ge 1 ]; then in_media_gezien=$((in_media_gezien + 1)); fi
    if [ "$n_app" -ge 1 ]; then in_app_gezien=$((in_app_gezien + 1)); fi
    sleep 0.2
  done
  if [ "$in_media_gezien" -ge 1 ] && [ "$in_app_gezien" = 0 ]; then
    goed "tesseract alleen in de worker-container: docker top zag hem $in_media_gezien van de $keer keer in media, nooit in de app"
  else
    fout "docker top: tesseract $in_media_gezien van de $keer keer in media, $in_app_gezien keer in de app"
  fi
else
  fout "de OCR vanuit de app liep niet: $(mask < "$WORK/app.txt" | tail -n 5)"
fi
in_platform touch "$STOP" || true
if wait "$app_pid"; then app_rc=0; else app_rc=1; fi
{ grep -E "^(goed|FOUT): " "$WORK/app.txt" || true; } | mask | while IFS= read -r line; do regel "$line"; done
if [ "$app_rc" = 0 ]; then
  goed "vanuit de app: caps en Op.OCR ($(grep '^herhaald: ' "$WORK/app.txt" || echo 'herhaald: ?'))"
else
  mask < "$WORK/app.txt" | tail -n 20 >&2
  fout "vanuit de app (code $app_rc)"
fi

# --- de handdruk van ocr, als media -----------------------------------------------------------

# Zoals de vormen "python-tekst" van handdruk_tekst.py in test_media_container.sh: de echte
# opdracht ocr door een worker als media (de kopie van python in /run/media: /tmp is noexec), met
# een tweede proces van media dat vanaf zijn start /proc/<kind>/fd en mem probeert
if in_media python -I /srv/tests/ocr/handdruk_ocr.py /srv --kopie-map /run/media \
  > "$WORK/handdruk_ocr.json" 2> "$WORK/handdruk_ocr.err"; then
  rc=0
else
  rc=$?
fi
if [ -n "$OUT" ]; then
  mask < "$WORK/handdruk_ocr.json" > "$OUT/handdruk_ocr.json"
fi
cat > "$WORK/handdruk_kort.py" << 'PY'
import json, sys

try:
    d = json.loads(open(sys.argv[1]).read().strip().splitlines()[-1])
except (ValueError, IndexError):
    print("geen uitvoer")
    sys.exit(1)
fout = []
if d.get("uid") != 10002:
    fout.append(f"uid {d.get('uid')}")
if d.get("keten") != "echt":
    fout.append(f"keten {d.get('keten')} (moet de echte zijn: 0711)")
if not d.get("python-tekst"):
    fout.append("geen python-tekst die media niet mag lezen")
LEEG = {"state": "none", "code": None, "error": None, "width": 300, "height": 200, "osd": True}
for v in ("kopie, traag", "kopie", "python-tekst, traag", "python-tekst"):
    r = d.get(v)
    if r is None:
        fout.append(f"{v}: ontbreekt")
        continue
    if r["aantal geopend"] or r["mem geopend"]:
        fout.append(f"{v}: geopend fd {r['aantal geopend']}, mem {r['mem geopend']}")
    # "weg": ENOENT of ESRCH terwijl /proc/<pid> niet meer bestond (handdruk_ocr.aanval_code)
    if {k for k in r["fouten"] if k != "weg"} != {"EACCES"}:
        fout.append(f"{v}: fouten {r['fouten']}")
    if r["samenvatting"] != LEEG:
        fout.append(f"{v}: {r['samenvatting']}")
r = d.get("leesbaar, traag", {})
if any(g["blob"] for g in r.get("geopend", [])):
    fout.append("leesbaar, traag: de blob via een fd")
print("; ".join(fout) if fout else (
    f"alleen EACCES, kind 1 uit de kopie en uit python-tekst, kind 2 de echte tesseract "
    f"(ptrace_scope {d.get('ptrace_scope')}; een leesbare python: geopend "
    f"{r.get('aantal geopend')}, mem {r.get('mem geopend')})"))
sys.exit(1 if fout else 0)
PY
if kort="$(python3 -I "$WORK/handdruk_kort.py" "$WORK/handdruk_ocr.json" 2>&1)" && [ "$rc" = 0 ]; then
  goed "de handdruk van ocr als media: $kort"
else
  fout "de handdruk van ocr als media (code $rc): $kort $(tail -n 3 "$WORK/handdruk_ocr.err" | mask)"
fi
if [ -n "$(in_media sh -c 'ls -d /run/media/handdruk-ocr-* 2> /dev/null' || true)" ]; then
  fout "de handdruk: de kopieën bleven achter in /run/media"
fi

if [ "$FAILED" = 0 ]; then
  regel "test_ocr_container: alles goed"
else
  regel "test_ocr_container: er faalde iets" >&2
fi
