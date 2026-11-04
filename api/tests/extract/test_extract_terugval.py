"""De stap text met media uit: de terugval (bouwontwerp fase 6, Z2; Keuze 17).

Zonder mediaworker leest het renderproces van de app de tekst (previews.render_text: een kind
van de forkserver, of hier in de tests een eigen proces), met batches tot 50 blobs via hun pad,
RLIMIT_AS 1 GiB, RLIMIT_CPU en nice 19. De uitkomsten staan in een bestand in een eigen werkmap;
de app leest ze één voor één (frame.read_file) en vertrouwt ze net zo weinig als die van de
pipe.

Ook de haken van Z2 stap 2: de haak na de omzetting van OnlyOffice (oude formaten), de titels
uit de probe (media), search.ready naar wie het bestand ziet, en de upload (files.add_ref) die
de stap klaarzet als de rijen draaien.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import analysis, events, jobs, previews
from app.analysis import Step
from app.db import get_engine
from app.extract import step as extract_step
from app.models import Analysis, Blob, BlobText, MediaProbe, Node, User
from app.office.convert import pdf_path
from app.search import hooks
from app.search.types import EVENT_SEARCH_READY
from app.storage import BlobStore
from tests import test_files
from tests.search import fixtures

pytestmark = pytest.mark.anyio

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


def child(extra: str) -> str:
    """previews.CHILD met extra regels vlak vóór render.main (in het renderproces)."""
    anchor = "from app.render import main"
    assert anchor in previews.CHILD
    return previews.CHILD.replace(anchor, f"exec({extra!r}); {anchor}")


@pytest.fixture(autouse=True)
def _de_stap_text():
    analysis.reset()
    hooks.reset()
    extract_step.register()
    yield
    analysis.reset()
    hooks.reset()
    extract_step.register()


@pytest.fixture
def processen(monkeypatch) -> list[float]:
    """Wanneer elk renderproces startte (zonder forkserver in de tests: een eigen proces)."""
    started: list[float] = []
    real = previews._spawn

    async def spawn(request, cwd):
        started.append(time.monotonic())
        return await real(request, cwd)

    monkeypatch.setattr(previews, "_spawn", spawn)
    return started


def maker(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


async def put(settings, data: bytes, mime: str | None, *, owner=None) -> str:
    sha = hashlib.sha256(data).hexdigest()
    path = BlobStore(settings.blob_dir).path(sha)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    async with maker(settings)() as db:
        db.add(Blob(sha256=sha, size=len(data), mime=mime))
        await db.flush()
        await analysis.queue(db, sha, [Step.TEXT], owner=owner)
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


def _render_child() -> int | None:
    """Het renderproces van dit proces (CHILD op de opdrachtregel)."""
    me = str(__import__("os").getpid())
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if stat.rsplit(")", 1)[1].split()[1] == me and b"app.render import main" in cmdline:
            return int(entry.name)
    return None


def _limits_of(pid: int) -> tuple[dict[str, list[str]], int]:
    limits = {
        line[:26].strip(): line[26:].split()[:2]
        for line in Path(f"/proc/{pid}/limits").read_text().splitlines()[1:]
    }
    nice = int(Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[16])
    return limits, nice


# --- de batch ------------------------------------------------------------------------------------


async def test_een_batch_van_vijftig_in_een_renderproces(settings, processen):
    assert not settings.media_enabled
    assert extract_step.batch_size(settings) == extract_step.FALLBACK_BATCH == 50
    assert extract_step.in_sandbox(settings) is False  # de melding op /beheer (Z7)
    aan = settings.model_copy(update={"media_enabled": True})
    assert extract_step.batch_size(aan) == settings.text_batch == 16
    assert extract_step.in_sandbox(aan) is True
    plain = [
        await put(settings, f"Stuk {i} met woord{i}".encode(), "text/plain") for i in range(47)
    ]
    html = await put(settings, b"<html><body><p>Een htmlpagina</p></body></html>", "text/html")
    pdf = await put(settings, fixtures.pdf_tekst(1, tekst="pdfwoord"), "application/pdf")
    docx = await put(
        settings,
        fixtures.docx("kantoorwoord"),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    extra = await put(settings, b"de eenenvijftigste", "text/plain")
    assert await analysis.pump(settings, "index") == 50
    assert len(processen) == 1  # één renderproces voor de hele batch
    found, bodies = await rows(settings), await texts(settings)
    assert sum(r.state == "done" for r in found.values()) == 50
    assert bodies[plain[31]].body == "Stuk 31 met woord31"
    assert bodies[html].body == "Een htmlpagina"
    assert "pdfwoord" in bodies[pdf].body
    assert "kantoorwoord" in bodies[docx].body
    # De 51e in de volgende batch
    assert await analysis.pump(settings, "index") == 1
    assert (await rows(settings))[extra].state == "done"
    # De werkmap is weg
    assert list((previews.root(BlobStore(settings.blob_dir)) / ".tmp").iterdir()) == []


async def test_de_grenzen_van_het_renderproces(settings, monkeypatch):
    """RLIMIT_CPU (zacht 1, hard 2), RLIMIT_AS 1 GiB en nice 19; de blob wordt too_slow zonder
    nieuwe poging (S15), en de app gaat gewoon door."""
    monkeypatch.setattr(extract_step, "LIMITS", {"child_cpu_s": 1, "blob_cpu_s": 60.0})
    slow = await put(settings, RTF_TRAAG, "text/rtf")
    pump = asyncio.ensure_future(analysis.pump(settings, "index"))
    pid = None
    deadline = time.monotonic() + 10
    while pid is None and time.monotonic() < deadline and not pump.done():
        pid = _render_child()
        await asyncio.sleep(0.01)
    assert pid is not None, "geen renderproces gezien"
    limits: dict[str, list[str]] = {}
    nice = 0
    while time.monotonic() < deadline:  # de grenzen zet main() kort na de start
        limits, nice = _limits_of(pid)
        if limits["Max cpu time"] != ["unlimited", "unlimited"] and nice == 19:
            break
        await asyncio.sleep(0.01)
    await pump
    assert limits["Max cpu time"] == ["1", "2"]
    assert limits["Max address space"][0] == str(previews.TEXT_MEMORY_MB << 20)
    assert nice == previews.TEXT_NICE == 19
    assert state((await rows(settings))[slow]) == ("failed", 1, "too_slow")
    monkeypatch.setattr(extract_step, "LIMITS", {})
    ok = await put(settings, b"daarna gewoon tekst", "text/plain")
    await analysis.pump(settings, "index")
    assert state((await rows(settings))[ok]) == ("done", 0, None)


async def test_de_klok_per_blob_maakt_too_slow(settings, monkeypatch):
    monkeypatch.setattr(extract_step, "LIMITS", {"blob_cpu_s": 0.3})
    slow = await put(settings, RTF_TRAAG, "text/rtf")
    ok = await put(settings, b"na de trage", "text/plain")
    await analysis.pump(settings, "index")
    found = await rows(settings)
    assert state(found[slow]) == ("failed", 1, "too_slow")
    assert state(found[ok]) == ("done", 0, None)


async def test_de_wandklok_stopt_het_renderproces(settings, monkeypatch):
    """Duurt een renderproces langer dan FALLBACK_WALL_S (hier 0,3 s), dan gaat het met alles
    wat het startte dood; de blob wordt too_slow zonder nieuwe poging."""
    monkeypatch.setattr(extract_step, "FALLBACK_WALL_S", 0.3)
    monkeypatch.setattr(extract_step, "LIMITS", {"child_cpu_s": 60, "blob_cpu_s": 60.0})
    slow = await put(settings, RTF_TRAAG, "text/rtf")
    started = time.monotonic()
    await analysis.pump(settings, "index")
    assert time.monotonic() - started < 10
    assert state((await rows(settings))[slow]) == ("failed", 1, "too_slow")
    await asyncio.sleep(0.1)
    assert _render_child() is None


# --- kapotte kinderen ----------------------------------------------------------------------------


async def test_een_crash_wordt_gevonden_door_te_halveren(settings, monkeypatch, processen):
    monkeypatch.setattr(previews, "CHILD", child(CRASH))
    shas = [await put(settings, f"blob {i} leest goed".encode(), "text/plain") for i in range(15)]
    crash = await put(settings, b"CRASH en verder niets", "text/plain")
    assert await analysis.pump(settings, "index") == 16
    found = await rows(settings)
    assert state(found[crash]) == ("wait", 1, None)  # zonder code, met een nieuwe poging
    assert all(state(found[s]) == ("done", 0, None) for s in shas)
    assert len(await texts(settings)) == 15
    assert 2 <= len(processen) <= 1 + 2 * 4


async def test_onzin_in_het_bestand_wordt_niet_geloofd(settings, monkeypatch):
    monkeypatch.setattr(previews, "CHILD", child(ONZIN))
    shas = [await put(settings, f"tekst {i}".encode(), "text/plain") for i in range(5)]
    bad = await put(settings, b"ONZIN uit een gekraakt proces", "text/plain")
    await analysis.pump(settings, "index")
    found = await rows(settings)
    assert state(found[bad]) == ("wait", 1, None)
    assert all(state(found[s]) == ("done", 0, None) for s in shas)
    assert bad not in await texts(settings)


# --- de haken ------------------------------------------------------------------------------------


async def test_officepdf_text(settings):
    """Een doc zonder PDF is skip; na de omzetting (previews.announce na de thumbnails van de
    PDF, register_ready) staat hij weer klaar en komt de tekst uit de PDF."""
    doc = await put(settings, b"\xd0\xcf\x11\xe0 een oud doc", "application/msword")
    await analysis.pump(settings, "index")
    assert (await rows(settings))[doc].state == "skip"
    target = pdf_path(BlobStore(settings.blob_dir), doc)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(fixtures.pdf_tekst(1, tekst="omgezetwoord"))
    async with maker(settings)() as db:
        await db.execute(
            sa.update(Blob).where(Blob.sha256 == doc).values(pdf_state="ready", pdf_version=1)
        )
        await db.commit()
    await previews.announce(maker(settings), doc)
    assert state((await rows(settings))[doc]) == ("wait", 0, None)
    await analysis.pump(settings, "index")
    assert state((await rows(settings))[doc]) == ("done", 0, None)
    assert "omgezetwoord" in (await texts(settings))[doc].body
    # Nog een announce (een nieuwe thumbnail) zet een klare stap niet opnieuw klaar
    await previews.announce(maker(settings), doc)
    assert (await rows(settings))[doc].state == "done"


async def test_officepdf_text_ook_bij_de_backfill(settings):
    """Kwam de haak niet (de thumbnails mislukten), dan zet de backfill bij het starten een doc
    met een klare PDF weer klaar."""
    doc = await put(settings, b"\xd0\xcf\x11\xe0 nog een doc", "application/msword")
    await analysis.pump(settings, "index")
    assert (await rows(settings))[doc].state == "skip"
    target = pdf_path(BlobStore(settings.blob_dir), doc)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(fixtures.pdf_tekst(1, tekst="laterwoord"))
    async with maker(settings)() as db:
        await db.execute(
            sa.update(Blob).where(Blob.sha256 == doc).values(pdf_state="ready", pdf_version=1)
        )
        await db.commit()
    await analysis.backfill_job(settings, {"after": ""})
    assert (await rows(settings))[doc].state == "wait"
    await analysis.pump(settings, "index")
    assert "laterwoord" in (await texts(settings))[doc].body


async def test_titels_uit_de_probe(settings):
    """Kent mutagen het formaat niet (hier: geen echt audiobestand), dan komen titel, artiest en
    album uit de probe van de worker; zonder probe is er niets (none)."""
    met = await put(settings, b"\x1aE\xdf\xa3 geen echte mkv", "video/x-matroska")
    zonder = await put(settings, b"\x1aE\xdf\xa3 ook geen mkv", "video/x-matroska")
    async with maker(settings)() as db:
        db.add(
            MediaProbe(
                blob_sha=met,
                version=1,
                state="ready",
                tags={"title": "De zomerfilm", "artist": "Iemand", "comment": "niet dit"},
            )
        )
        await db.commit()
    await analysis.pump(settings, "index")
    found, bodies = await rows(settings), await texts(settings)
    assert state(found[met]) == ("done", 0, None)
    assert bodies[met].meta == "De zomerfilm\nIemand"
    assert bodies[met].body == ""
    assert found[zonder].state == "none"
    assert zonder not in bodies


async def test_search_ready_naar_de_eigenaar(settings, monkeypatch):
    sent: list[tuple[list, str, dict]] = []
    monkeypatch.setattr(
        events, "publish", lambda users, kind, data: sent.append((list(users), kind, data))
    )
    sha = await put(settings, b"tekst voor de viewer", "text/plain")
    async with maker(settings)() as db:
        eva = User(email="eva@example.nl", display_name="Eva", password_hash="x")
        db.add(eva)
        await db.flush()
        node = Node(owner_id=eva.id, name="a.txt", kind="file", blob_id=sha, size=20)
        db.add(node)
        await db.commit()
    await analysis.pump(settings, "index")
    assert sent == [([eva.id], EVENT_SEARCH_READY, {"nodes": [str(node.id)], "step": "text"})]


# --- de upload (files.add_ref → search.hooks.blob_added) -----------------------------------------


async def upload(client, headers, name: str, data: bytes) -> dict:
    node = await test_files.root(client, headers)
    r = await test_files.put(client, headers, node["folder"]["id"], name, data)
    assert r.status_code == 201, r.text
    return r.json()


async def test_een_upload_zonder_rijen_wacht_op_de_backfill(settings, client, admin):
    headers, _ = admin
    await upload(client, headers, "los.txt", b"een upload zonder rijen")
    await asyncio.sleep(0.05)
    assert await rows(settings) == {}
    await analysis.backfill_job(settings, {"after": ""})
    assert [r.state for r in (await rows(settings)).values()] == ["wait"]


async def test_een_upload_zet_de_tekst_klaar(settings, client, admin):
    headers, _ = admin
    runner = jobs.Runner(settings, "index")
    await runner.start()
    try:
        node = await upload(client, headers, "notitie.txt", b"een woord uit de upload")
        deadline = time.monotonic() + 30
        found: dict[str, Analysis] = {}
        while time.monotonic() < deadline:
            found = await rows(settings)
            if found and all(r.state == "done" for r in found.values()):
                break
            await asyncio.sleep(0.05)
    finally:
        await runner.stop()
    [row] = found.values()
    assert row.state == "done"
    async with maker(settings)() as db:
        owner = await db.scalar(sa.select(Node.owner_id).where(Node.id == uuid.UUID(node["id"])))
    assert row.owner_id == owner
    assert (await texts(settings))[row.blob_sha].body == "een woord uit de upload"
