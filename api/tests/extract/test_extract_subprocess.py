"""De stap text door de mediaworker (bouwontwerp fase 6, Z2; Keuze 17 en 20).

Een worker in dit proces (server.Worker, zoals in test_media_worker_core.py) met de echte
opdracht text: per batch één kind (`python -I -c BOOT`) dat de blobs leest en de uitkomsten op
de pipe naar de app schrijft. De pomp van de analyse kiest de batch, step.py stuurt hem.

- RLIMIT: het kind draait met RLIMIT_CPU, RLIMIT_DATA, RLIMIT_FSIZE 0 en nice 19; een blob die
  het kind over RLIMIT_CPU duwt, wordt failed too_slow zonder nieuwe poging (S15), net als een
  blob die de klok per blob (30 s CPU, hier kleiner) opmaakt;
- een kapot bestand (een PDF die geen PDF is, een zip-bom) wordt failed, de rest van de batch
  gaat door, en de app en de worker merken er niets van;
- een kind dat crasht (SIGSEGV) of onzin op de pipe schrijft: de rest van de batch wordt
  gehalveerd tot de fout bij één blob ligt; de andere vijftien worden gewoon gelezen.

Een kind dat crasht, is hier een BOOT met een paar regels extra (vóór text_child): plain.read
stuurt zichzelf SIGSEGV als de blob met CRASH begint, of frame.write maakt een veld kapot.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import analysis, jobs
from app.analysis import Step
from app.db import get_engine
from app.extract import MAX_BYTES, batch
from app.extract import step as extract_step
from app.media import client
from app.media.worker import run as R
from app.media.worker.ops import text as ops_text
from app.media.worker.server import Config, Worker
from app.models import Analysis, Blob, BlobText
from app.office.convert import pdf_path
from app.search import hooks
from app.storage import BlobStore
from tests.search import fixtures

pytestmark = pytest.mark.anyio

CAPS = {"protocol": 1, "ffmpeg": "test", "ffprobe": "test", "mode": "local", "sandbox": "off"}
KEY = "de-sleutel-van-de-tekst-test-" * 2
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
RTF_TRAAG = b"{\\rtf1 " + b"{\\*\\x a}" * 1_000_000 + b"}"  # 8 MB, ~2,6 s CPU, geen tekst

CRASH = (
    "import os,signal\n"
    "from app.extract import plain\n"
    "_read=plain.read\n"
    "def _crash(f,out):\n"
    "    if f.read(5)==b'CRASH':os.kill(os.getpid(),signal.SIGSEGV)\n"
    "    f.seek(0)\n"
    "    return _read(f,out)\n"
    "plain.read=_crash\n"
)
ONZIN = (
    "from app.extract import frame\n"
    "_write=frame.write\n"
    "def _onzin(fd,i,fields,text):\n"
    "    if text.startswith('ONZIN'):fields=dict(fields,state='gekraakt')\n"
    "    return _write(fd,i,fields,text)\n"
    "frame.write=_onzin\n"
)


def boot(extra: str) -> str:
    """BOOT van ops/text.py met extra regels vlak vóór text_child (na de prctl)."""
    anchor = "from app.render import text_child\n"
    assert anchor in ops_text.BOOT
    return ops_text.BOOT.replace(anchor, extra + anchor)


@pytest.fixture(autouse=True)
def _de_stap_text():
    analysis.reset()
    extract_step.register()
    yield
    analysis.reset()
    hooks.reset()
    extract_step.register()


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
def aan(settings, worker):
    return settings.model_copy(update={"media_enabled": True})


@pytest.fixture
def kinderen(monkeypatch) -> list[int]:
    """De pids van de kinderen die de opdracht text startte."""
    pids: list[int] = []
    real = R.spawn_handshake

    def spawn(*args, **kw):
        child, sock = real(*args, **kw)
        pids.append(child.pid)
        return child, sock

    monkeypatch.setattr(R, "spawn_handshake", spawn)
    return pids


def maker(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


async def put(settings, data: bytes, mime: str | None, *, size: int | None = None) -> str:
    sha = hashlib.sha256(data).hexdigest()
    path = BlobStore(settings.blob_dir).path(sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    async with maker(settings)() as db:
        db.add(Blob(sha256=sha, size=len(data) if size is None else size, mime=mime))
        await db.flush()
        await analysis.queue(db, sha, [Step.TEXT], owner=None)
        await db.commit()
    return sha


async def rows(settings) -> dict[str, Analysis]:
    async with maker(settings)() as db:
        found = (await db.scalars(sa.select(Analysis).where(Analysis.step == "text"))).all()
    return {row.blob_sha: row for row in found}


async def texts(settings) -> dict[str, BlobText]:
    async with maker(settings)() as db:
        return {t.blob_sha: t for t in (await db.scalars(sa.select(BlobText))).all()}


def state(row: Analysis) -> tuple:
    return row.state, row.tries, row.code


# --- een gewone batch --------------------------------------------------------------------------


async def test_een_batch_van_zestien_door_de_worker(aan, kinderen):
    plain = [await put(aan, f"Document {i} met woord{i}".encode(), "text/plain") for i in range(13)]
    html = await put(aan, b"<html><body><p>Een htmlpagina</p></body></html>", "text/html")
    rtf = await put(aan, rb"{\rtf1{\info{\title Het verslag}}Een rtfbestand\par}", "text/rtf")
    pdf = await put(aan, fixtures.pdf_tekst(1, tekst="pdfwoord"), "application/pdf")
    assert await analysis.pump(aan, "index") == 16
    assert len(kinderen) == 1  # één kind voor de hele batch (één eigenaar)
    found, bodies = await rows(aan), await texts(aan)
    assert all(state(found[s]) == ("done", 0, None) for s in [*plain, html, rtf, pdf])
    assert bodies[plain[7]].body == "Document 7 met woord7"
    assert bodies[html].body == "Een htmlpagina"
    assert (bodies[rtf].body, bodies[rtf].meta) == ("Een rtfbestand", "Het verslag")
    assert "pdfwoord" in bodies[pdf].body and bodies[pdf].meta.startswith("Verslag met tekst")
    assert not any(t.truncated for t in bodies.values())


async def test_per_eigenaar_een_eigen_kind(aan, kinderen):
    """Een kind dat een kwaadaardig bestand las, krijgt nooit de fd's van een ander."""
    from app.models import User

    async with maker(aan)() as db:
        eva = User(email="eva@example.nl", display_name="Eva", password_hash="x")
        bram = User(email="bram@example.nl", display_name="Bram", password_hash="x")
        db.add_all([eva, bram])
        await db.commit()
    shas = [await put(aan, f"tekst {i}".encode(), "text/plain") for i in range(4)]
    async with maker(aan)() as db:
        await db.execute(
            sa.update(Analysis).where(Analysis.blob_sha.in_(shas[:2])).values(owner_id=eva.id)
        )
        await db.execute(
            sa.update(Analysis).where(Analysis.blob_sha.in_(shas[2:])).values(owner_id=bram.id)
        )
        await db.commit()
    assert await analysis.pump(aan, "index") == 4
    assert len(kinderen) == 2
    assert all(r.state == "done" for r in (await rows(aan)).values())


async def test_oud_kantoorformaat_leest_de_pdf_van_onlyoffice(aan):
    doc = await put(aan, b"\xd0\xcf\x11\xe0 een oud doc", "application/msword")
    zonder = await put(aan, b"\xd0\xcf\x11\xe0 nog geen pdf", "application/msword")
    target = pdf_path(BlobStore(aan.blob_dir), doc)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(fixtures.pdf_tekst(1, tekst="omgezetwoord"))
    async with maker(aan)() as db:
        await db.execute(
            sa.update(Blob).where(Blob.sha256 == doc).values(pdf_state="ready", pdf_version=1)
        )
        await db.commit()
    await analysis.pump(aan, "index")
    found = await rows(aan)
    assert state(found[doc]) == ("done", 0, None)
    assert found[zonder].state == "skip"  # de haak na de omzetting zet hem opnieuw klaar
    assert "omgezetwoord" in (await texts(aan))[doc].body


async def test_te_groot_is_skip_zonder_kind(aan, kinderen):
    sha = await put(aan, b"groot", "text/plain", size=MAX_BYTES + 1)
    await analysis.pump(aan, "index")
    assert (await rows(aan))[sha].state == "skip"
    assert kinderen == []


# --- RLIMIT en de klok -------------------------------------------------------------------------


def _child_of_ours() -> int | None:
    me = str(os.getpid())
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if stat.rsplit(")", 1)[1].split()[1] == me and b"text_child" in cmdline:
            return int(entry.name)
    return None


def _limits_of(pid: int) -> tuple[dict[str, list[str]], int]:
    """De grenzen (zacht, hard) en de nice-waarde van een proces, uit /proc."""
    limits = {
        line[:26].strip(): line[26:].split()[:2]
        for line in Path(f"/proc/{pid}/limits").read_text().splitlines()[1:]
    }
    nice = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[16])
    return limits, nice


async def test_rlimit_cpu_maakt_too_slow_zonder_nieuwe_poging(aan, monkeypatch):
    monkeypatch.setattr(extract_step, "LIMITS", {"child_cpu_s": 1, "blob_cpu_s": 60.0})
    slow = await put(aan, RTF_TRAAG, "text/rtf")
    pump = asyncio.ensure_future(analysis.pump(aan, "index"))
    pid = None
    deadline = time.monotonic() + 10
    while pid is None and time.monotonic() < deadline and not pump.done():
        pid = _child_of_ours()
        await asyncio.sleep(0.01)
    assert pid is not None, "geen kind gezien"
    limits, nice = _limits_of(pid)
    await pump
    assert limits["Max cpu time"] == ["1", "2"]
    assert limits["Max data size"] == [str(768 << 20)] * 2
    assert limits["Max file size"] == ["0", "0"]
    assert nice == 19
    assert state((await rows(aan))[slow]) == ("failed", 1, "too_slow")
    # De app en de worker gaan gewoon door
    ok = await put(aan, b"daarna gewoon tekst", "text/plain")
    monkeypatch.setattr(extract_step, "LIMITS", {})
    await analysis.pump(aan, "index")
    assert state((await rows(aan))[ok]) == ("done", 0, None)
    assert (await client.caps()).procs == 0


async def test_rlimit_cpu_rechtstreeks(aan, tmp_path):
    """batch.via_worker zegt rc −24 (SIGXCPU) en too_slow; geen uitkomst voor de blob."""
    path = tmp_path / "traag.rtf"
    path.write_bytes(RTF_TRAAG)
    fd = batch.open_blob(path)
    try:
        ran = await batch.via_worker([fd], ["rtf"], max_chars=1000, child_cpu_s=1, blob_cpu_s=60)
    finally:
        os.close(fd)
    assert (ran.found, ran.clean, ran.too_slow) == ([], False, True)
    assert ran.rc in (-24, -9)


async def test_de_klok_per_blob_maakt_too_slow(aan, monkeypatch):
    monkeypatch.setattr(extract_step, "LIMITS", {"blob_cpu_s": 0.3})
    slow = await put(aan, RTF_TRAAG, "text/rtf")
    ok = await put(aan, b"na de trage", "text/plain")
    await analysis.pump(aan, "index")
    found = await rows(aan)
    assert state(found[slow]) == ("failed", 1, "too_slow")
    assert state(found[ok]) == ("done", 0, None)


# --- kapotte bestanden en kinderen -------------------------------------------------------------


async def test_kapotte_bestanden_stoppen_zonder_de_app_te_raken(aan, kinderen):
    rommel = await put(aan, b"%PDF-1.4 dit is geen pdf", "application/pdf")
    bom = await put(aan, fixtures.make("kantoor/zip-bom.docx").read_bytes(), DOCX)
    goed = await put(aan, b"een gewoon bestand", "text/plain")
    await analysis.pump(aan, "index")
    found = await rows(aan)
    assert state(found[rommel]) == ("wait", 1, None)  # later nog een poging
    assert state(found[bom]) == ("failed", 1, "zip_limit")  # geen nieuwe poging
    assert state(found[goed]) == ("done", 0, None)
    assert len(kinderen) == 1  # een fout van één blob stopt de rest niet


async def test_een_crash_in_een_batch_van_16_wordt_gevonden_door_te_halveren(
    aan, kinderen, monkeypatch
):
    monkeypatch.setattr(ops_text, "BOOT", boot(CRASH))
    shas = [await put(aan, f"blob {i} leest goed".encode(), "text/plain") for i in range(15)]
    crash = await put(aan, b"CRASH en verder niets", "text/plain")
    assert await analysis.pump(aan, "index") == 16
    found = await rows(aan)
    assert state(found[crash]) == ("wait", 1, None)  # zonder code, met een nieuwe poging
    assert all(state(found[s]) == ("done", 0, None) for s in shas)
    assert len(await texts(aan)) == 15
    # Halveren: hooguit twee kinderen per stap van 16 naar 1
    assert 2 <= len(kinderen) <= 1 + 2 * 4
    assert (await client.caps()).procs == 0  # de worker leeft en heeft niets meer lopen

    # Na drie pogingen is hij failed
    for _ in range(2):
        await zet_nu(aan, crash)
        await analysis.pump(aan, "index")
    assert state((await rows(aan))[crash]) == ("failed", 3, None)


async def zet_nu(settings, sha: str) -> None:
    async with maker(settings)() as db:
        await db.execute(
            sa.update(Analysis)
            .where(Analysis.blob_sha == sha)
            .values(queued_at=analysis.FRONT_END + (analysis.now() - analysis.FRONT_END) / 2)
        )
        await db.commit()


async def test_onzin_op_de_pipe_wordt_niet_geloofd(aan, monkeypatch):
    monkeypatch.setattr(ops_text, "BOOT", boot(ONZIN))
    shas = [await put(aan, f"tekst {i}".encode(), "text/plain") for i in range(5)]
    bad = await put(aan, b"ONZIN uit een gekraakt kind", "text/plain")
    await analysis.pump(aan, "index")
    found = await rows(aan)
    assert state(found[bad]) == ("wait", 1, None)
    assert all(state(found[s]) == ("done", 0, None) for s in shas)
    assert bad not in await texts(aan)


# --- de worker druk, weg of uit ----------------------------------------------------------------


async def test_zonder_worker_later_opnieuw(settings, tmp_path):
    aan_zonder = settings.model_copy(update={"media_enabled": True})
    client.bind(tmp_path / "nergens.sock", KEY)
    try:
        sha = await put(aan_zonder, b"tekst", "text/plain")
        with pytest.raises(jobs.Defer):
            await analysis.pump(aan_zonder, "index")
    finally:
        client.bind(None)
    assert state((await rows(settings))[sha]) == ("wait", 0, None)


async def test_met_media_uit_de_terugval_zonder_worker(settings, tmp_path):
    """Met media uit vraagt de stap de worker niets: de terugval (het renderproces van de app,
    test_extract_terugval.py) leest de tekst, ook als er een socket van een worker staat."""
    client.bind(tmp_path / "nergens.sock", KEY)
    try:
        sha = await put(settings, b"tekst zonder media", "text/plain")
        assert await analysis.pump(settings, "index") == 1
    finally:
        client.bind(None)
    assert state((await rows(settings))[sha]) == ("done", 0, None)
    assert (await texts(settings))[sha].body == "tekst zonder media"
