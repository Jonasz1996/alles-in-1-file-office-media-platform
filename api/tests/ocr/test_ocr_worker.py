"""De opdracht ocr door een worker in dit proces (bouwontwerp fase 6, X1; Keuze 17, 22 en 24),
met een stand-in voor tesseract: een klein script dat vastlegt hoe het gestart werd (argv, de
omgeving, RLIMIT_DATA, nice) en wat het op stdin kreeg, en antwoordt wat de test vraagt. Zo
draait dit overal, ook zonder tesseract; de echte herkenning staat in test_ocr_real.py (marker
ocr) en in het contract (tests/media/worker_contract.py, ocr_vorm).

- zonder tesseract in de PATH: none met ocr_missing, en geen kind;
- kind 2 krijgt alleen de PGM van kind 1 op stdin (nooit de blob), met OMP_THREAD_LIMIT=1,
  RLIMIT_DATA 512 MB, nice 15 en een kale omgeving;
- de OSD alleen als de gemiddelde zekerheid onder 60 blijft, en de gedraaide ronde telt alleen
  als ze beter is; de vakjes dan terug in het beeld van kind 1;
- een kind 1 dat liegt over zijn PGM (een kop met een andere maat, te groot, een ander antwoord
  over de socket): ocr_bad_image, en kind 2 start niet;
- de grenzen: ocr_s voor elke herkenning (too_slow, het kind is weg), RLIMIT_CPU van kind 1
  (too_slow), te veel uitvoer, een fout van tesseract zonder de staart van stderr, en stoppen
  (de verbinding dicht) stopt elk kind.
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import json
import os
import socket
import sys
import time
from pathlib import Path

import pytest
from PIL import Image

from app.media import client, wire
from app.media.wire import Op
from app.media.worker import args as A
from app.media.worker import run as R
from app.media.worker.ops import ocr
from app.media.worker.server import Config, Worker
from app.ocr import pgm

pytestmark = pytest.mark.anyio

CAPS = {"protocol": 1, "ffmpeg": "test", "ffprobe": "test", "mode": "local", "sandbox": "off"}
KEY = "de-sleutel-van-de-ocr-test-" * 2
SIZE = (300, 200)
CALL = ["stdin", "stdout", "-l", "nld+eng", "--psm", "3", "tsv"]
OSD = ["stdin", "stdout", "--psm", "0"]

# De stand-in: kind 2 mag geen bestand schrijven (RLIMIT_FSIZE 0), dus meldt hij elke aanroep als
# datagram op de socket van de test (argv, de omgeving, de grenzen, de sha256 van stdin, en of
# een bestand schrijven lukte), en antwoordt uit plan.json: de TSV per maat van de PGM
# ("300 200"), of de OSD bij --psm 0
STANDIN = """#!{python} -I
import errno, hashlib, json, os, resource, socket, sys, time
d = {dir!r}
plan = json.load(open(os.path.join(d, "plan.json")))
data = sys.stdin.buffer.read()
argv = sys.argv[1:]
head = data[:32].split(b"\\n")
try:
    with open(os.path.join(d, "geschreven"), "wb") as f:
        f.write(b"tekst van de pagina")
    wrote = "ja"
except OSError as e:
    wrote = errno.errorcode.get(e.errno, str(e.errno))
record = {{
    "argv": argv, "env": sorted(os.environ), "omp": os.environ.get("OMP_THREAD_LIMIT"),
    "data": resource.getrlimit(resource.RLIMIT_DATA)[0], "nice": os.nice(0),
    "dims": head[1].decode("latin-1") if len(head) > 1 else "", "pid": os.getpid(),
    "sha": hashlib.sha256(data).hexdigest(), "size": len(data), "write": wrote,
}}
s = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
s.sendto(json.dumps(record).encode(), os.path.join(d, "l.sock"))
sys.stderr.write("GEHEIM uit de pagina\\n")
time.sleep(plan.get("sleep", 0))
if argv[2:4] == ["--psm", "0"]:
    sys.stdout.write(plan.get("osd", ""))
    sys.exit(plan.get("osd_rc", 0))
out = plan.get("tsv", {{}}).get(record["dims"], "")
sys.stdout.write(out * plan.get("repeat", 1))
sys.exit(plan.get("rc", 0))
"""

HEAD = "\t".join(
    ["level", "page_num", "block_num", "par_num", "line_num", "word_num", "left", "top", "width",
     "height", "conf", "text"]
)  # fmt: skip


def tsv(*words: tuple) -> str:
    """Een TSV van tesseract met deze woorden: (tekst, links, boven, breedte, hoogte, zekerheid,
    regel)."""
    rows = [HEAD, "1\t1\t0\t0\t0\t0\t0\t0\t300\t200\t-1\t"]
    for i, (text, x, y, w, h, conf, line) in enumerate(words):
        rows.append(f"5\t1\t1\t1\t{line}\t{i + 1}\t{x}\t{y}\t{w}\t{h}\t{conf}\t{text}")
    return "\n".join(rows) + "\n"


def gradient() -> Image.Image:
    """Een grijs beeld zonder tekstregels (elke rij gelijk): kind 1 zet het niet recht, dus
    de PGM is precies deze pixels."""
    w, h = SIZE
    return Image.frombytes("L", SIZE, bytes(x * 255 // (w - 1) for x in range(w)) * h)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Standin:
    def __init__(self, root: Path):
        self.dir = root / "tesseract"
        self.dir.mkdir()
        self.bin = root / "bin"
        self.bin.mkdir()
        exe = self.bin / "tesseract"
        exe.write_text(STANDIN.format(python=sys.executable, dir=str(self.dir)))
        exe.chmod(0o755)
        for name in ("env", "nice", "ionice"):
            (self.bin / name).symlink_to(f"/usr/bin/{name}")
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM | socket.SOCK_CLOEXEC)
        self.sock.bind(str(self.dir / "l.sock"))
        self.sock.setblocking(False)
        self._calls: list[dict] = []
        self.plan({})

    def plan(self, plan: dict) -> None:
        (self.dir / "plan.json").write_text(json.dumps(plan))
        self.calls()
        self._calls = []

    def calls(self) -> list[dict]:
        """De aanroepen tot nu (sinds het laatste plan), in volgorde."""
        while True:
            try:
                self._calls.append(json.loads(self.sock.recv(1 << 16)))
            except BlockingIOError:
                return list(self._calls)

    def close(self) -> None:
        self.sock.close()


@pytest.fixture
def standin(tmp_path, monkeypatch):
    """Alleen de stand-in en env, nice en ionice in de PATH (geen echte tesseract)."""
    s = Standin(tmp_path)
    monkeypatch.setenv("PATH", str(s.bin))
    yield s
    s.close()


@pytest.fixture
async def worker(tmp_path):
    config = Config(socket=tmp_path / "run" / "w.sock", max_procs=6, max_transcodes=2, key=KEY)
    w = Worker(config, dict(CAPS))
    await w.start()
    client.bind(config.socket, KEY)
    try:
        yield w
    finally:
        await w.stop()
        client.bind(None)


@pytest.fixture
def kind1(monkeypatch) -> list[int]:
    """De pids van kind 1 (run.spawn_handshake)."""
    pids: list[int] = []
    real = R.spawn_handshake

    def spawn(*args, **kw):
        child, sock = real(*args, **kw)
        pids.append(child.pid)
        return child, sock

    monkeypatch.setattr(R, "spawn_handshake", spawn)
    return pids


def boot(monkeypatch, extra: str) -> None:
    """BOOT van ops/ocr.py met extra regels vlak vóór ocr_child (na de prctl): een kind 1 dat
    zich anders gedraagt."""
    anchor = "from app.render import ocr_child\n"
    assert anchor in ocr.BOOT
    monkeypatch.setattr(ocr, "BOOT", ocr.BOOT.replace(anchor, extra + anchor))


def source(tmp_path: Path, img: Image.Image | None = None, name: str = "bron.png") -> Path:
    path = tmp_path / name
    buf = io.BytesIO()
    (img or gradient()).save(buf, "PNG")
    path.write_bytes(buf.getvalue())
    return path


async def run(path: Path, **args) -> tuple[list[dict], dict]:
    """Eén ocr: de stukken met woorden en de samenvatting."""
    job, _ = await client.call(Op.OCR, A.OcrArgs(kind="image", **args), source=path)
    async with job:
        events = [e async for e in job.events()]
    for event in events:
        assert event["ev"] == "result" and len(wire.encode(event)) <= wire.MAX_MESSAGE
    return [e["data"] for e in events[:-1]], events[-1]["data"]


def alive(pid: int) -> bool:
    try:
        text = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return text[text.rindex(")") + 2] != "Z"


async def free() -> None:
    for _ in range(100):
        caps = await client.caps()
        if caps.procs == 0 and not any(caps.in_use.values()):
            return
        await asyncio.sleep(0.05)
    raise AssertionError(f"de worker telt nog {caps.procs} processen, {caps.in_use}")


# --- zonder tesseract ------------------------------------------------------------------------


async def test_zonder_tesseract_ocr_missing_en_geen_kind(tmp_path, monkeypatch, worker, kind1):
    empty = tmp_path / "leeg"
    empty.mkdir()
    for name in ("env", "nice", "ionice"):
        (empty / name).symlink_to(f"/usr/bin/{name}")
    monkeypatch.setenv("PATH", str(empty))
    assert ocr.program() is None
    chunks, summary = await run(source(tmp_path))
    assert chunks == [] and summary == ocr._summary("none", "ocr_missing")
    assert kind1 == []  # ook kind 1 start niet
    await free()


# --- de keten --------------------------------------------------------------------------------


async def test_kind_2_krijgt_alleen_de_pgm_met_de_grenzen(tmp_path, standin, worker, kind1):
    words = [(f"woord{i}", i % 250, 10 + i // 25, 20, 9, 90 + i % 10, i // 25) for i in range(5000)]
    standin.plan({"tsv": {"300 200": tsv(("laag", 1, 1, 5, 5, 59.9, 0), *words)}})
    path = source(tmp_path)
    chunks, summary = await run(path)
    assert summary["state"] == "done" and summary["code"] is None and summary["error"] is None
    assert (summary["width"], summary["height"], summary["source"]) == (300, 200, [300, 200])
    assert summary["skew"] == 0.0 and summary["rotate"] == 0 and summary["osd"] is False
    assert set(summary["ms"]) == {"render", "deskew", "ocr"}
    # alle woorden ≥ 60 in stukken (meer dan één), in de volgorde van de TSV
    assert summary["parts"] == len(chunks) > 1
    assert [c["part"] for c in chunks] == list(range(len(chunks)))
    got = [w for c in chunks for w in c["words"]]
    assert got == [[t, x, y, w, h, line] for t, x, y, w, h, _c, line in words]
    assert summary["words"] == 5000 and summary["chars"] == len(ocr.text_of(got))
    # één herkenning: geen OSD bij een hoge zekerheid
    (call,) = standin.calls()
    assert call["argv"] == CALL
    assert call["omp"] == "1"
    assert call["data"] == 512 * 1024 * 1024
    assert call["nice"] == min(19, os.nice(0) + 15)
    assert call["env"] == ["HOME", "LC_ALL", "OMP_THREAD_LIMIT", "PATH"]
    # wat tesseract las, is de PGM van kind 1: de kop en dan precies de pixels, nooit de PNG
    pgm_bytes = pgm.header(*SIZE) + gradient().tobytes()
    assert (call["sha"], call["size"]) == (sha(pgm_bytes), len(pgm_bytes))
    # en een bestand schrijven kan hij niet (RLIMIT_FSIZE 0): de tekst blijft in de pijp
    assert call["write"] == "EFBIG"
    assert len(kind1) == 1 and not alive(kind1[0]) and not alive(call["pid"])
    await free()


async def test_geen_woord_boven_60_is_none(tmp_path, standin, worker):
    standin.plan(
        {"tsv": {"300 200": tsv(("vaag", 1, 1, 5, 5, 61, 0), ("vaag", 9, 1, 5, 5, 40, 0))}}
    )
    chunks, summary = await run(source(tmp_path), osd=False)
    assert summary["state"] == "done" and summary["words"] == 1 and summary["conf"] == 50.5
    standin.plan({"tsv": {"300 200": tsv(("vaag", 1, 1, 5, 5, 59.5, 0))}})
    chunks, summary = await run(source(tmp_path), osd=False)
    assert chunks == [] and summary["state"] == "none" and summary["code"] is None
    assert (summary["words"], summary["parts"], summary["chars"]) == (0, 0, 0)
    standin.plan({"tsv": {"300 200": tsv()}})
    chunks, summary = await run(source(tmp_path))
    assert summary["state"] == "none" and summary["conf"] == 0.0
    await free()


# --- de OSD alleen onder 60 ------------------------------------------------------------------

LOW = tsv(("ksav", 10, 10, 30, 8, 40, 0), ("tsoo", 50, 10, 30, 8, 30, 0))
TURNED = tsv(("Kenmerk", 10, 20, 30, 8, 85, 0), ("ocrwoordx", 50, 20, 40, 8, 95, 0))


async def test_osd_niet_bij_60_of_meer(tmp_path, standin, worker):
    for conf in (60, 75):
        standin.plan({"tsv": {"300 200": tsv(("woord", 1, 1, 5, 5, conf, 0))}, "osd": "Rotate: 90"})
        _, summary = await run(source(tmp_path))
        assert [c["argv"] for c in standin.calls()] == [CALL], conf
        assert summary["osd"] is False and summary["rotate"] == 0


async def test_osd_onder_60_zonder_draaiing(tmp_path, standin, worker):
    standin.plan({"tsv": {"300 200": LOW}, "osd": "Orientation in degrees: 0\nRotate: 0\n"})
    chunks, summary = await run(source(tmp_path))
    assert [c["argv"] for c in standin.calls()] == [CALL, OSD]
    assert summary["osd"] is True and summary["rotate"] == 0 and summary["state"] == "none"
    assert summary["conf"] == 35.0
    # osd uit (de app vraagt het per pagina): nooit
    standin.plan({"tsv": {"300 200": LOW}, "osd": "Rotate: 90\n"})
    _, summary = await run(source(tmp_path), osd=False)
    assert [c["argv"] for c in standin.calls()] == [CALL] and summary["osd"] is False
    # de OSD faalt (te weinig tekens): het blijft bij de eerste ronde
    standin.plan({"tsv": {"300 200": LOW}, "osd": "", "osd_rc": 1})
    _, summary = await run(source(tmp_path))
    assert [c["argv"] for c in standin.calls()] == [CALL, OSD]
    assert summary["osd"] is True and summary["rotate"] == 0 and summary["conf"] == 35.0
    await free()


async def test_osd_draait_en_de_vakjes_gaan_terug(tmp_path, standin, worker):
    """Rotate: 90: de worker draait de PGM met de klok mee (200 × 300) en herkent opnieuw; die
    ronde is beter, dus haar woorden tellen, met de vakjes terug in het beeld van kind 1."""
    standin.plan({"tsv": {"300 200": LOW, "200 300": TURNED}, "osd": "Rotate: 90\n"})
    chunks, summary = await run(source(tmp_path))
    calls = standin.calls()
    assert [c["argv"] for c in calls] == [CALL, OSD, CALL]
    assert [c["dims"] for c in calls] == ["300 200", "300 200", "200 300"]
    turned, w, h = pgm.rotate(gradient().tobytes(), *SIZE, 90)
    assert calls[2]["sha"] == sha(pgm.header(w, h) + turned)
    assert calls[1]["sha"] == calls[0]["sha"]  # de OSD op dezelfde PGM
    assert summary["osd"] is True and summary["rotate"] == 90 and summary["conf"] == 90.0
    assert (summary["width"], summary["height"]) == SIZE  # het beeld van kind 1
    words = [w for c in chunks for w in c["words"]]
    assert words == [
        ["Kenmerk", *pgm.unrotate_box((10, 20, 30, 8), *SIZE, 90), 0],
        ["ocrwoordx", *pgm.unrotate_box((50, 20, 40, 8), *SIZE, 90), 0],
    ]
    assert words[0][1:5] == [20, 160, 8, 30]
    # de gedraaide ronde is slechter: de eerste telt, zonder draaiing
    standin.plan(
        {"tsv": {"300 200": LOW, "200 300": tsv(("x", 1, 1, 2, 2, 20, 0))}, "osd": "Rotate: 270"}
    )
    chunks, summary = await run(source(tmp_path))
    assert len(standin.calls()) == 3 and summary["rotate"] == 0 and summary["conf"] == 35.0
    await free()


# --- een kind 1 dat liegt --------------------------------------------------------------------

LIEGT_MEER = "from app.ocr import pgm\npgm.header=lambda w,h:b'P5\\n%d %d\\n255\\n'%(w,h+1)\n"
LIEGT_MINDER = "from app.ocr import pgm\npgm.header=lambda w,h:b'P5\\n%d %d\\n255\\n'%(w,h-1)\n"
TE_GROOT = "from app.ocr import pgm\npgm.header=lambda w,h:b'P5\\n20000 20000\\n255\\n'\n"
GEEN_PGM = "from app.ocr import pgm\npgm.header=lambda w,h:b'P6\\n%d %d\\n255\\n'%(w,h)\n"
ANDER_ANTWOORD = (
    "import app.render as r\n_o=r.ocr_page\nr.ocr_page=lambda f,q,fd:dict(_o(f,q,fd),width=299)\n"
)
# een goede PGM, maar onzin over de socket (json.dumps van render geeft geen JSON)
ONZIN = (
    "import app.render as r, json\n"
    "r.json=type('J',(),{'loads':staticmethod(json.loads),'dumps':staticmethod(lambda a:'{x')})\n"
)


@pytest.mark.parametrize(
    "extra", [LIEGT_MEER, LIEGT_MINDER, TE_GROOT, GEEN_PGM, ANDER_ANTWOORD],
    ids=["kop zegt meer", "kop zegt minder", "kop te groot", "geen P5", "ander antwoord"],
)  # fmt: skip
async def test_een_pgm_die_niet_klopt_is_ocr_bad_image(
    tmp_path, standin, worker, kind1, monkeypatch, extra
):
    standin.plan({"tsv": {"300 200": TURNED}})
    boot(monkeypatch, extra)
    started = time.monotonic()
    chunks, summary = await run(source(tmp_path))
    assert chunks == [] and summary == ocr._summary("failed", "ocr_bad_image")
    assert standin.calls() == []  # tesseract kreeg niets
    assert time.monotonic() - started < 10
    assert len(kind1) == 1 and not alive(kind1[0])
    await free()


async def test_een_onleesbaar_antwoord_van_kind_1(tmp_path, standin, worker, monkeypatch):
    standin.plan({"tsv": {"300 200": TURNED}})
    boot(monkeypatch, ONZIN)
    chunks, summary = await run(source(tmp_path))
    assert chunks == [] and summary["state"] == "failed" and summary["code"] is None
    assert summary["error"].startswith("kind 1")
    assert standin.calls() == []
    await free()


async def test_een_bron_die_geen_beeld_is(tmp_path, standin, worker, kind1):
    path = tmp_path / "rommel.png"
    path.write_bytes(b"geen beeld " * 100)
    chunks, summary = await run(path)
    assert summary == ocr._summary("failed", error="UnidentifiedImageError")
    assert standin.calls() == [] and not alive(kind1[0])
    await free()


# --- de grenzen ------------------------------------------------------------------------------

REKENT = "import app.render as r\nr.ocr_page=lambda f,q,fd:[0 for _ in iter(int,1)]\n"


async def test_rlimit_cpu_van_kind_1_is_too_slow(tmp_path, standin, worker, kind1, monkeypatch):
    boot(monkeypatch, REKENT)
    started = time.monotonic()
    chunks, summary = await run(source(tmp_path), child_cpu_s=1)
    assert chunks == [] and summary == ocr._summary("failed", "too_slow")
    assert time.monotonic() - started < 15
    assert standin.calls() == [] and not alive(kind1[0])
    await free()


async def test_de_tijdsgrens_van_de_herkenning(tmp_path, standin, worker):
    standin.plan({"tsv": {"300 200": TURNED}, "sleep": 30})
    started = time.monotonic()
    chunks, summary = await run(source(tmp_path), ocr_s=0.5)
    assert time.monotonic() - started < 10
    assert chunks == [] and summary["state"] == "failed" and summary["code"] == "too_slow"
    assert (summary["width"], summary["height"]) == SIZE  # wat kind 1 wel deed
    (call,) = standin.calls()
    assert not alive(call["pid"])
    await free()


async def test_een_fout_of_te_veel_uitvoer_zonder_stderr(tmp_path, standin, worker):
    standin.plan({"tsv": {"300 200": TURNED}, "rc": 1})
    chunks, summary = await run(source(tmp_path))
    assert chunks == [] and summary["state"] == "failed"
    assert summary["error"] == "herkenning: code 1" and summary["code"] is None
    assert "GEHEIM" not in json.dumps(summary)
    big = tsv(*[("w" * 100, 1, 1, 2, 2, 90, 0)] * 100)
    standin.plan({"tsv": {"300 200": big}, "repeat": ocr.TSV_LIMIT // len(big) + 2})
    chunks, summary = await run(source(tmp_path))
    assert chunks == [] and summary["error"] == "herkenning: te veel uitvoer"
    await free()


async def test_stoppen_stopt_elk_kind(tmp_path, standin, worker, kind1, monkeypatch):
    # tijdens kind 2
    standin.plan({"tsv": {"300 200": TURNED}, "sleep": 30})
    job, _ = await client.call(Op.OCR, A.OcrArgs(kind="image"), source=source(tmp_path))
    for _ in range(200):
        if standin.calls():
            break
        await asyncio.sleep(0.05)
    (call,) = standin.calls()
    assert alive(call["pid"])
    await job.cancel()
    for _ in range(100):
        if not alive(call["pid"]):
            break
        await asyncio.sleep(0.05)
    assert not alive(call["pid"])
    await free()
    # tijdens kind 1
    boot(monkeypatch, REKENT)
    job, _ = await client.call(Op.OCR, A.OcrArgs(kind="image"), source=source(tmp_path))
    for _ in range(200):
        if len(kind1) == 2 and alive(kind1[1]):
            break
        await asyncio.sleep(0.05)
    await asyncio.sleep(0.3)
    assert alive(kind1[1])
    await job.cancel()
    for _ in range(100):
        if not alive(kind1[1]):
            break
        await asyncio.sleep(0.05)
    assert not alive(kind1[1])
    await free()
