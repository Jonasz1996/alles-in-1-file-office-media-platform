"""PDF via de conversie van DS (O3, Keuze 25 en 26), met de nep-DS in dit proces.

De nep-DS praat via ASGI met de interne app (de bron ophalen) en de app via ASGI met de nep-DS
(/converter en de uitvoer). De taken draaien met jobs.run_pending, zonder achtergrondlus.
"""

import asyncio
import contextlib
import io
import os
import struct
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import httpx
import pypdfium2
import pytest
from fastapi import FastAPI
from sqlalchemy import func, select, update

from app import jobs, previews
from app.config import Settings, get_settings
from app.main import app, internal_app
from app.mime import DOCX
from app.models import Blob, Job, Node, OfficeSession, User, Version
from app.office import convert, engine, grants, health, inspect, keys, runtime, sessions
from app.office.oo import client as ds_client
from app.office.oo import convert as oo_convert
from app.office.state import OfficeConfig, office_policy, office_state
from app.storage import BlobStore
from tests.factories import code_of, create_blob, create_file, maker, params_of
from tests.fake_docserver import FakeConfig, create_app, protocol
from tests.test_files import mkdir, root
from tests.test_office import docx_bytes, jpeg, odt_bytes, pptx_bytes, xlsx_bytes
from tests.test_office_inspect import ZEROS, central_offsets, local_offset
from tests.test_office_routes import office_settings
from tests.test_previews import picture
from tests.test_public import new_link, open_link, tus_create, tus_send
from tests.test_shares import person, share_with
from tests.test_viewer import listing, upload

MB = 1024**2
OLE = inspect.OLE_MAGIC + bytes(504)  # de kop van OLE2: genoeg voor mime.py en inspect
RTF = b"{\\rtf1\\ansi hallo}"


class Recording(httpx.AsyncBaseTransport):
    """Het verkeer van de app naar DS, met de host van elk verzoek."""

    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.hosts: list[str] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.hosts.append(request.url.host)
        return await self.inner.handle_async_request(request)


@dataclass
class Office:
    settings: Settings
    fake: Any  # tests.fake_docserver.app.FakeDocServer
    fake_app: FastAPI
    transport: Recording
    tasks: list[asyncio.Task] = field(default_factory=list)

    @property
    def conversions(self):
        return self.fake.conversions

    def log(self, *kinds: str) -> list[dict]:
        return [e for e in self.fake.entries if not kinds or e["kind"] in kinds]

    def converted(self) -> list[str]:
        """De keys waarvoor DS een conversie kreeg (getekend), elk één keer: opnieuw vragen
        (async) is dezelfde conversie."""
        found = [e["key"] for e in self.log("convert") if e.get("signed")]
        return list(dict.fromkeys(found))

    def background(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self.tasks.append(task)
        return task


@pytest.fixture
async def office(client, settings, monkeypatch):
    """Office aan met de nep-DS, zoals de lifespan het zou binden; DS gezond."""
    s = office_settings(settings)
    app.dependency_overrides[get_settings] = lambda: s
    runtime.bind(app, s)
    fake_app = create_app(
        FakeConfig(secret=s.office_secret, transport=httpx.ASGITransport(app=internal_app))
    )
    transport = Recording(httpx.ASGITransport(app=fake_app))
    ds_client.transport = transport
    office_state(app).health = "ok"
    monkeypatch.setattr(oo_convert, "POLL_SECONDS", 0.01)
    convert.ASKS.reset()
    inspect.forget()
    grants.clear()
    convert._converting.clear()
    convert._waiting.clear()
    convert._asked.clear()
    world = Office(s, fake_app.state.fake, fake_app, transport)
    yield world
    if world.conversions.hold is not None:
        world.conversions.hold.set()
    for task in world.tasks:
        task.cancel()
    for task in world.tasks:
        with contextlib.suppress(BaseException):
            await task
    for task in list(convert._tasks):
        task.cancel()
    await fake_app.state.fake.aclose()
    await ds_client.close()
    ds_client.transport = None
    runtime.unbind()
    engine.reset()
    convert.ASKS.reset()
    convert._converting.clear()
    convert._waiting.clear()
    convert._asked.clear()
    inspect.forget()
    grants.clear()
    for name in ("office", "office_policy"):
        if hasattr(app.state, name):
            delattr(app.state, name)


async def until(check: Callable[[], bool], seconds: float = 10.0) -> None:
    """Wacht (begrensd) tot check() waar is."""
    for _ in range(int(seconds / 0.01)):
        if check():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("de voorwaarde werd niet waar")


async def blob_of(s: Settings, node_id: str) -> Blob:
    async with maker(s)() as session:
        node = await session.get(Node, uuid.UUID(str(node_id)))
        return await session.get(Blob, node.blob_id)


async def office_jobs(s: Settings, status: str | None = None) -> list[Job]:
    async with maker(s)() as session:
        query = select(Job).where(Job.type == convert.TYPE).order_by(Job.created_at)
        if status:
            query = query.where(Job.status == status)
        return list((await session.scalars(query)).all())


async def run_later(s: Settings, seconds: float = 600) -> int:
    """De taken die nu en tot seconds vanaf nu klaarstaan."""
    return await jobs.run_pending(s, jobs.now() + timedelta(seconds=seconds))


def pdf_pages(path) -> int:
    document = pypdfium2.PdfDocument(str(path))
    try:
        return len(document)
    finally:
        document.close()


def ole(size: int = 4096) -> bytes:
    return (OLE + bytes(size))[:size]


def docx_with_pages(pages: int, word: str = "Eerste") -> bytes:
    """Een docx met pages - 1 pagina-einden: de nep-DS geeft dan zoveel pagina's."""
    breaks = '<w:p><w:r><w:br w:type="page"/></w:r></w:p>' * (pages - 1)
    return docx_bytes(f"<w:p><w:r><w:t>{word}</w:t></w:r></w:p>{breaks}")


def replace_part(data: bytes, name: str, content: bytes) -> bytes:
    """Dezelfde zip met één onderdeel vervangen."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(data)) as source,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as target,
    ):
        for info in source.infolist():
            target.writestr(info, content if info.filename == name else source.read(info))
    return out.getvalue()


def odt_bomb(megabytes: int = 1024) -> bytes:
    """Een odt van een paar MB met megabytes nullen erin (uitgepakt een gigabyte)."""
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as archive:
        archive.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
        archive.writestr("content.xml", "<office:document-content/>")
        with archive.open("Pictures/nul.bin", "w") as target:
            for _ in range(megabytes):
                target.write(ZEROS)
    return out.getvalue()


def lying_docx() -> bytes:
    """Een docx waarvan word/document.xml zegt 10 bytes te zijn, maar 5000 bytes uitpakt."""
    data = bytearray(docx_bytes("<w:p><w:r><w:t>" + "x" * 5000 + "</w:t></w:r></w:p>"))
    for central in central_offsets(bytes(data)):
        name_length = struct.unpack_from("<H", data, central + 28)[0]
        if bytes(data[central + 46 : central + 46 + name_length]) == b"word/document.xml":
            local = local_offset(bytes(data), central)
            struct.pack_into("<I", data, central + 24, 10)
            struct.pack_into("<I", data, local + 22, 10)
    return bytes(data)


def pptx_with_photos(count: int = 8, megabytes: int = 10) -> bytes:
    """Een pptx met count opgeslagen foto's van megabytes MB: groot, maar geen bom."""
    out = io.BytesIO(pptx_bytes(["<p:sp/>"]))
    with zipfile.ZipFile(out, "a") as archive:
        for n in range(count):
            info = zipfile.ZipInfo(f"ppt/media/image{n}.jpeg")
            info.compress_type = zipfile.ZIP_STORED
            archive.writestr(info, b"\xff\xd8\xff\xe0" + os.urandom(megabytes * MB))
    return out.getvalue()


async def ask(client, headers, node_id: str, expect: int = 202, **params) -> httpx.Response:
    r = await client.post(f"/api/v1/office/files/{node_id}/convert", headers=headers, params=params)
    assert r.status_code == expect, r.text
    return r


async def trash_and_purge(client, headers, node_id: str) -> None:
    assert (await client.delete(f"/api/v1/files/{node_id}", headers=headers)).status_code == 204
    r = await client.post("/api/v1/trash/purge", json={"ids": [node_id]}, headers=headers)
    assert r.status_code == 200, r.text


# --- omzetten na een upload ----------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("name", "make", "pages", "doc"),
    [
        ("brief.docx", lambda: docx_with_pages(3), 3, True),
        ("cijfers.xlsx", xlsx_bytes, 2, True),  # twee bladen
        ("dia.pptx", lambda: pptx_bytes(["<p:sp/>", "<p:sp/>"]), 2, True),
        ("tekst.odt", odt_bytes, 1, True),
        ("oud.doc", ole, 1, False),
        ("oud.xls", ole, 1, False),
        ("oud.ppt", ole, 1, False),
        ("opmaak.rtf", lambda: RTF, 1, False),
    ],
)
async def test_omzetten_geeft_een_pdf_en_thumbnails(client, admin, office, name, make, pages, doc):
    jonas, _ = admin
    s = office.settings
    node = await upload(client, jonas, name, make())
    await jobs.run_pending(s)
    blob = await blob_of(s, node["id"])
    assert blob.pdf_state == "ready" and blob.pdf_version == keys.PDF_VERSION
    blobs = BlobStore(s.blob_dir)
    path = convert.pdf_path(blobs, blob.sha256)
    assert path.is_file() and pdf_pages(path) == pages
    # De thumbnails komen nu uit pagina 1 van de PDF; doc, xls, ppt en rtf hebben zo voor het
    # eerst een preview (B21 heeft er geen)
    meta = blob.preview_meta or {}
    assert {256, 1024} <= set(meta["sizes"]) and meta["pages"] == pages
    assert blob.preview == "ready"
    target = previews.directory(blobs, blob.sha256)
    assert (target / "256.webp").is_file() and (target / "1024.webp").is_file()
    # doc.json van B21 blijft (de eenvoudige weergave)
    assert previews.doc_path(blobs, blob.sha256).is_file() == doc
    # DS kreeg de inhoud en een neutrale titel, nooit de naam van het bestand
    assert office.converted() == [keys.convert_key(blob.sha256, 1)]
    source = office.log("convert_source")
    assert len(source) == 1 and source[0]["http"] == 200 and source[0]["digest"]
    assert source[0]["bytes"] == blob.size
    assert all(name not in str(e) for e in office.log())
    assert set(office.transport.hosts) == {"office"}
    # Eén taak, klaar
    assert [j.status for j in await office_jobs(s)] == ["done"]


@pytest.mark.anyio
async def test_dezelfde_inhoud_in_twee_bestanden_is_een_conversie(client, admin, office):
    jonas, _ = admin
    s = office.settings
    data = docx_with_pages(2)
    first = await upload(client, jonas, "a.docx", data)
    second = await upload(client, jonas, "b.docx", data)
    await jobs.run_pending(s)
    assert (await blob_of(s, first["id"])).pdf_state == "ready"
    assert (await blob_of(s, second["id"])).pdf_state == "ready"
    assert len(office.converted()) == 1 and office.conversions.started == 1
    assert len(await office_jobs(s)) == 1


@pytest.mark.anyio
async def test_een_key_per_inhoud(client, admin, office):
    """DS geeft bij dezelfde key het oude resultaat, ook voor een ander bestand (de nep-DS doet
    dat ook, zie test_fake_docserver). Onze key bevat de sha, dus elk bestand zijn eigen PDF."""
    jonas, _ = admin
    s = office.settings
    two = await upload(client, jonas, "twee.docx", docx_with_pages(2))
    four = await upload(client, jonas, "vier.docx", docx_with_pages(4))
    await jobs.run_pending(s)
    blobs = BlobStore(s.blob_dir)
    two_blob, four_blob = await blob_of(s, two["id"]), await blob_of(s, four["id"])
    assert pdf_pages(convert.pdf_path(blobs, two_blob.sha256)) == 2
    assert pdf_pages(convert.pdf_path(blobs, four_blob.sha256)) == 4
    assert sorted(office.converted()) == sorted(
        [keys.convert_key(two_blob.sha256), keys.convert_key(four_blob.sha256)]
    )


@pytest.mark.anyio
async def test_na_onze_tijdslimiet_dezelfde_key(client, admin, office, monkeypatch):
    """DS werkt na onze tijdslimiet door: de volgende poging vraagt dezelfde key en krijgt dan
    het resultaat, zonder tweede conversie."""
    jonas, _ = admin
    s = office.settings
    monkeypatch.setattr(s, "office_convert_timeout", 0.3)
    office.conversions.configure({"hold": True})
    node = await upload(client, jonas, "traag.docx", docx_with_pages(2))
    await jobs.run_pending(s)
    blob = await blob_of(s, node["id"])
    key = keys.convert_key(blob.sha256)
    assert blob.pdf_state == "pending"
    [job] = await office_jobs(s)
    assert (job.status, job.attempts) == ("queued", 1) and "(code 1)" in job.error
    office.conversions.configure({"hold": False})
    await until(lambda: office.conversions.cache[key].done.is_set())
    await run_later(s, 60)
    assert (await blob_of(s, node["id"])).pdf_state == "ready"
    assert office.converted() == [key] and office.conversions.started == 1


@pytest.mark.anyio
@pytest.mark.parametrize("code", [-2, -4])
async def test_een_nieuwe_key_pas_na_min_2_of_min_4(client, admin, office, code):
    jonas, _ = admin
    s = office.settings
    office.conversions.configure({"fail": [code]})
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await jobs.run_pending(s)
    blob = await blob_of(s, node["id"])
    assert blob.pdf_state == "pending"
    done, queued = await office_jobs(s)
    assert done.status == "done" and queued.status == "queued"
    assert queued.payload["n"] == 2 and queued.attempts == 0
    assert queued.run_after.replace(tzinfo=None) >= (
        jobs.now() + timedelta(seconds=convert.NEW_KEY_SECONDS - 5)
    ).replace(tzinfo=None)
    await run_later(s, 60)
    assert (await blob_of(s, node["id"])).pdf_state == "ready"
    assert office.converted() == [
        keys.convert_key(blob.sha256, 1),
        keys.convert_key(blob.sha256, 2),
    ]


@pytest.mark.anyio
async def test_hooguit_drie_nieuwe_keys(client, admin, office):
    jonas, _ = admin
    s = office.settings
    office.conversions.configure({"fail": [-4, -4, -4, -4, -4]})
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await run_later(s)
    blob = await blob_of(s, node["id"])
    assert blob.pdf_state == "failed" and blob.pdf_version == keys.PDF_VERSION
    assert office.converted() == [keys.convert_key(blob.sha256, n) for n in (1, 2, 3, 4)]
    last = (await office_jobs(s))[-1]
    assert last.status == "failed" and "(code -4)" in last.error
    # Een verzoek zegt waarom
    r = await ask(client, jonas, node["id"], expect=502)
    assert code_of(r) == "office_convert_failed" and params_of(r) == {"code": -4}


RETRY = "/api/v1/admin/office/conversions/retry"


@pytest.mark.anyio
async def test_mislukte_opnieuw_krijgt_eigen_keys(client, admin, office):
    """Na -4 op alle vier keys (DS kon de bron even niet ophalen) vraagt "Mislukte opnieuw" op
    /beheer andere keys: of DS een fout onder de key bewaart, maakt dan niet uit (-4 bewaart
    hij niet, gemeten; -1, -2, -3 en -6 niet gemeten, en de nep-DS bewaart die). Een -4 in die
    ronde geeft een nieuwe key binnen dezelfde ronde."""
    jonas, _ = admin
    s = office.settings
    office.conversions.configure({"fail": [-4, -4, -4, -4]})
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await run_later(s)
    blob = await blob_of(s, node["id"])
    assert blob.pdf_state == "failed"
    before = office.converted()
    assert before == [keys.convert_key(blob.sha256, n) for n in (1, 2, 3, 4)]

    office.conversions.configure({"fail": [-4]})  # nog één keer, daarna is de storing voorbij
    r = await client.post(RETRY, headers=jonas)
    assert r.status_code == 200 and r.json() == {"queued": 1}
    assert (await blob_of(s, node["id"])).pdf_state == "pending"
    await run_later(s)
    assert (await blob_of(s, node["id"])).pdf_state == "ready"
    first, second = office.converted()[len(before) :]
    assert first.startswith(f"{keys.convert_key(blob.sha256)}-r") and first not in before
    assert second == f"{first}-a2"


@pytest.mark.anyio
async def test_mislukte_opnieuw_met_ds_weg_blijft_failed(client, admin, office):
    """Is DS niet gezond als de taak van "Mislukte opnieuw" aan de beurt is, dan gaat de inhoud
    terug naar failed, niet naar leeg: de inhaalronde zou anders de oude key nemen en de oude
    fout terugkrijgen. Met DS weg weigert de knop zelf (503)."""
    jonas, _ = admin
    s = office.settings
    office.conversions.configure({"fail": [-3]})
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await run_later(s)
    assert (await blob_of(s, node["id"])).pdf_state == "failed"
    r = await client.post(RETRY, headers=jonas)
    assert r.json() == {"queued": 1}
    tried = len(office.converted())
    office_state(app).health = "down"
    await run_later(s)
    assert (await blob_of(s, node["id"])).pdf_state == "failed"
    assert len(office.converted()) == tried
    r = await client.post(RETRY, headers=jonas)
    assert r.status_code == 503 and code_of(r) == "office_unavailable"
    assert (await blob_of(s, node["id"])).pdf_state == "failed"
    office_state(app).health = "ok"
    office_policy(app).config = OfficeConfig(enabled=False)
    r = await client.post(RETRY, headers=jonas)
    assert r.status_code == 503 and code_of(r) == "office_off"
    assert (await blob_of(s, node["id"])).pdf_state == "failed"


# --- de invoercontrole ---------------------------------------------------------------------


@pytest.mark.anyio
async def test_een_bom_en_een_liegende_zip_komen_nooit_bij_ds(client, admin, office):
    jonas, _ = admin
    s = office.settings
    bomb = await upload(client, jonas, "bom.odt", odt_bomb())
    lying = await upload(client, jonas, "liegt.docx", lying_docx())
    await jobs.run_pending(s)
    for node in (bomb, lying):
        r = await ask(client, jonas, node["id"], expect=422)
        assert code_of(r) == "office_unsafe"
        assert (await blob_of(s, node["id"])).pdf_state == "unsafe"
    # De nep-DS zag niets: geen conversie, geen enkel verzoek
    assert office.log() == [] and office.transport.hosts == []
    # En ook later niet vanzelf
    await convert.start_backfill(s)
    await run_later(s)
    assert office.log() == []


@pytest.mark.anyio
async def test_een_pptx_met_80_mb_fotos_gaat_wel(client, admin, office):
    jonas, _ = admin
    s = office.settings
    data = pptx_with_photos()
    assert len(data) > 80 * MB
    node = await upload(client, jonas, "fotos.pptx", data)
    await jobs.run_pending(s)
    # Boven office_auto_convert_bytes (20 MB): alleen op verzoek
    assert (await blob_of(s, node["id"])).pdf_state == "ask"
    assert office.converted() == []
    r = await ask(client, jonas, node["id"])
    assert r.json() == {"pdf_state": "pending"}
    await jobs.run_pending(s)
    assert (await blob_of(s, node["id"])).pdf_state == "ready"
    [source] = office.log("convert_source")
    assert source["bytes"] == len(data)


@pytest.mark.anyio
async def test_te_groot_voor_een_verzoek(client, admin, office, monkeypatch):
    jonas, _ = admin
    s = office.settings
    monkeypatch.setattr(s, "office_ole_max_bytes", 4000)
    node = await upload(client, jonas, "groot.doc", ole(5000))
    await jobs.run_pending(s)
    # De taak liet de invoercontrole het zeggen (boven 4000 bytes: unsafe)
    assert (await blob_of(s, node["id"])).pdf_state == "unsafe"
    async with maker(s)() as session:
        await session.execute(update(Blob).values(pdf_state="ask"))
        await session.commit()
    r = await ask(client, jonas, node["id"], expect=413)
    assert code_of(r) == "office_too_large"
    assert office.converted() == []


@pytest.mark.anyio
async def test_een_trage_controle_is_niet_blijvend_unsafe(client, admin, office, monkeypatch):
    """De tijdgrens van de invoercontrole (of een leesfout) zegt iets over de machine, niet over
    het bestand: de taak telt het als een gewone poging (failed na twee), niet als unsafe. Dan
    zet de knop Mislukte opnieuw op /beheer hem terug, en komt de PDF er alsnog."""
    jonas, _ = admin
    s = office.settings
    monkeypatch.setattr(inspect, "SECONDS", -1)  # een drukke machine: altijd te laat
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await run_later(s)
    assert (await blob_of(s, node["id"])).pdf_state == "failed"
    [job] = await office_jobs(s, "failed")
    assert job.attempts == 2 and "invoercontrole" in job.error
    assert office.converted() == []  # DS zag niets
    monkeypatch.setattr(inspect, "SECONDS", 20.0)  # zonder inspect.forget: niets bewaard
    r = await client.post(RETRY, headers=jonas)
    assert r.status_code == 200 and r.json() == {"queued": 1}
    await run_later(s)
    assert (await blob_of(s, node["id"])).pdf_state == "ready"


@pytest.mark.anyio
async def test_een_trage_controle_bij_een_verzoek_is_503(client, admin, office, monkeypatch):
    """Op verzoek: 503 office_unavailable (zo opnieuw proberen), en de inhoud wordt niet
    unsafe; de volgende vraag kijkt opnieuw en zet hem om."""
    jonas, _ = admin
    s = office.settings
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))  # nog geen taak gedaan
    monkeypatch.setattr(inspect, "SECONDS", -1)
    r = await ask(client, jonas, node["id"], expect=503)
    assert code_of(r) == "office_unavailable"
    assert (await blob_of(s, node["id"])).pdf_state is None
    monkeypatch.setattr(inspect, "SECONDS", 20.0)
    r = await ask(client, jonas, node["id"])
    assert r.json() == {"pdf_state": "pending"}
    await run_later(s)
    assert (await blob_of(s, node["id"])).pdf_state == "ready"


# --- uploads via een link en verzoeken -----------------------------------------------------


async def link_upload(client, headers, folder: str, name: str, data: bytes) -> None:
    link = await new_link(client, headers, folder, permission="upload")
    key = (await open_link(client, link))["key"]
    r = await tus_create(client, key, name, len(data))
    assert r.status_code == 201, r.text
    r = await tus_send(client, key, r.headers["location"], 0, data)
    assert r.status_code == 204, r.text


@pytest.mark.anyio
async def test_een_upload_via_een_link_wacht_op_een_verzoek(client, admin, office):
    jonas, _ = admin
    s = office.settings
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    folder = await mkdir(client, jonas, (await root(client, jonas))["folder"]["id"], "Inbox")
    await share_with(client, jonas, folder, user_id=anna_id, permission="read")
    await link_upload(client, jonas, folder, "van-buiten.docx", docx_with_pages(2))
    await link_upload(client, jonas, folder, "ook.docx", docx_with_pages(3))
    await run_later(s)
    children = (await client.get(f"/api/v1/files/{folder}/children", headers=jonas)).json()
    nodes = {c["name"]: c["id"] for c in children["children"]}
    for node_id in nodes.values():
        assert (await blob_of(s, node_id)).pdf_state == "ask"
    # Niet vanzelf, ook niet in de inhaalronde
    await convert.start_backfill(s)
    await run_later(s)
    assert office.converted() == [] and office.log() == []
    # Jonas (eigenaar) en Anna (alleen lezen) mogen het vragen
    for headers, name in ((jonas, "van-buiten.docx"), (anna, "ook.docx")):
        r = await ask(client, headers, nodes[name])
        assert r.json() == {"pdf_state": "pending"}
        await jobs.run_pending(s)
        assert (await blob_of(s, nodes[name])).pdf_state == "ready"
        r = await ask(client, headers, nodes[name])
        assert r.json() == {"pdf_state": "ready"}
    assert len(office.converted()) == 2


@pytest.mark.anyio
async def test_de_elfde_vraag_in_tien_minuten(client, admin, office):
    jonas, _ = admin
    s = office.settings
    anna, anna_id = await person(client, jonas, "anna@example.nl")
    folder = await mkdir(client, jonas, (await root(client, jonas))["folder"]["id"], "Inbox")
    await share_with(client, jonas, folder, user_id=anna_id, permission="read")
    await link_upload(client, jonas, folder, "a.docx", docx_with_pages(2))
    await jobs.run_pending(s)
    node = (await client.get(f"/api/v1/files/{folder}/children", headers=jonas)).json()
    node_id = node["children"][0]["id"]
    office_state(app).health = "down"
    for _ in range(3):  # wat niet doorgaat (DS weg), telt niet mee
        assert code_of(await ask(client, jonas, node_id, expect=503)) == "office_unavailable"
    office_state(app).health = "ok"
    for _ in range(10):
        await ask(client, jonas, node_id)
    r = await ask(client, jonas, node_id, expect=429)
    assert code_of(r) == "rate_limited"
    # Per gebruiker: Anna mag nog
    await ask(client, anna, node_id)
    # Er staat één taak klaar, vooraan
    [job] = await office_jobs(s)
    assert job.status == "queued" and job.run_after.replace(tzinfo=None) < (
        jobs.now().replace(tzinfo=None)
    )


@pytest.mark.anyio
async def test_een_eerdere_versie_alleen_op_verzoek(client, admin, office):
    jonas, _ = admin
    s = office.settings
    parent = (await root(client, jonas))["folder"]["id"]
    first = await upload(client, jonas, "brief.docx", docx_with_pages(2, "Oud"), parent)
    await jobs.run_pending(s)
    old_sha = (await blob_of(s, first["id"])).sha256
    # De inhoud vervangen: de oude wordt een eerdere versie (met een PDF)
    await upload(
        client, jonas, "brief.docx", docx_with_pages(3, "Nieuw"), parent, conflict="replace"
    )
    await jobs.run_pending(s)
    assert len(office.converted()) == 2
    # Een eerdere versie die nog geen PDF had (zoals van vóór office), krijgt er vanzelf geen:
    # niet na een nieuwe preview, en niet in de inhaalronde
    convert.pdf_path(BlobStore(s.blob_dir), old_sha).unlink()
    async with maker(s)() as session:
        version = await session.scalar(select(Version).where(Version.blob_id == old_sha))
        await session.execute(
            update(Blob)
            .where(Blob.sha256 == old_sha)
            .values(pdf_state=None, pdf_version=0, render_version=0)
        )
        await jobs.enqueue(session, previews.TYPE, {"sha": old_sha}, key=f"preview:{old_sha}")
        await session.commit()
    convert_jobs = len(await office_jobs(s))
    await jobs.run_pending(s)
    await convert.start_backfill(s)
    await run_later(s)
    assert len(await office_jobs(s)) == convert_jobs
    async with maker(s)() as session:
        assert (await session.get(Blob, old_sha)).pdf_state is None
    # Op verzoek wel (met ?version=)
    r = await ask(client, jonas, first["id"], version=str(version.id))
    assert r.json() == {"pdf_state": "pending"}
    await jobs.run_pending(s)
    async with maker(s)() as session:
        assert (await session.get(Blob, old_sha)).pdf_state == "ready"
    assert convert.pdf_path(BlobStore(s.blob_dir), old_sha).is_file()
    assert len(await office_jobs(s)) == convert_jobs + 1


# --- vastlopen, gezondheid en wachten ------------------------------------------------------


@pytest.mark.anyio
async def test_kill_tijdens_een_conversie(client, admin, office):
    """Een bestand dat DS laat vastlopen, krijgt hooguit twee kansen; daarna failed en een
    mislukte taak op /beheer, en nooit meer vanzelf."""
    jonas, _ = admin
    s = office.settings
    office.conversions.configure({"hold": True})
    node = await upload(client, jonas, "zwaar.docx", docx_with_pages(2))
    task = office.background(jobs.run_pending(s))
    await until(lambda: office.log("convert_source"))
    await office.fake.kill()
    await task
    blob = await blob_of(s, node["id"])
    assert blob.pdf_state == "pending"
    [job] = await office_jobs(s)
    assert (job.status, job.attempts) == ("queued", 1) and "(code 2)" in job.error
    # De gezondheidslus zag het intussen; de tweede kans telt dan toch
    office_state(app).health = "down"
    await run_later(s, 60)
    blob = await blob_of(s, node["id"])
    assert blob.pdf_state == "failed"
    [job] = await office_jobs(s)
    assert (job.status, job.attempts) == ("failed", 2)
    async with maker(s)() as session:
        assert [j.id for j in await jobs.failed(session)] == [job.id]
    # DS terug: de inhaalronde en een nieuwe preview laten hem staan
    await office.fake.restart()
    office_state(app).health = "ok"
    async with maker(s)() as session:
        await session.execute(
            update(Blob).where(Blob.sha256 == blob.sha256).values(render_version=0)
        )
        await jobs.enqueue(session, previews.TYPE, {"sha": blob.sha256}, key="preview:opnieuw")
        await session.commit()
    await convert.start_backfill(s)
    await run_later(s)
    assert (await blob_of(s, node["id"])).pdf_state == "failed"
    assert office.converted() == [keys.convert_key(blob.sha256)]
    assert len(await office_jobs(s)) == 1


@pytest.mark.anyio
async def test_een_herstart_van_ds_kost_geen_pogingen(client, admin, office):
    """DS gaat onderuit tijdens een conversie (een update van de container). De app kijkt dan
    meteen of DS nog gezond is: wat op het slot wacht, gaat zonder poging terug in plaats van
    elk een poging te verliezen tegen een DS die weg is. De conversie die liep, wacht met zijn
    tweede poging tot DS terug is. Daarna is alles gewoon klaar, niets is mislukt."""
    jonas, _ = admin
    s = office.settings
    office_state(app).ever_ok = True  # DS draaide al: weg is down, niet starting
    office.conversions.configure({"hold": True})
    first = await upload(client, jonas, "een.docx", docx_with_pages(2))
    await jobs.run_one(s)  # de preview
    task = office.background(jobs.run_one(s))  # de conversie loopt
    await until(lambda: office.log("convert_source"))
    others = [
        await upload(client, jonas, f"rij{n}.docx", docx_with_pages(2, f"Rij{n}")) for n in range(3)
    ]
    await jobs.run_pending(s)  # de previews; de conversies wachten op het slot
    await office.fake.kill()
    await task
    assert office_state(app).health == "down"
    async with maker(s)() as session:  # alles wat wacht is nu aan de beurt
        await session.execute(
            update(Job).where(Job.type == convert.TYPE).values(run_after=jobs.now())
        )
        await session.commit()
    await jobs.run_pending(s)
    assert (await blob_of(s, first["id"])).pdf_state == "pending"
    for node in others:
        assert (await blob_of(s, node["id"])).pdf_state is None  # terug, zonder poging
    async with maker(s)() as session:
        assert await jobs.failed(session) == []
    [job] = [j for j in await office_jobs(s) if j.status == "queued"]
    assert job.attempts == 1  # de tweede poging wacht
    # DS terug: de tweede poging, en de inhaalronde neemt de rest
    office.conversions.configure({"hold": False})
    await office.fake.restart()
    await health.check_once(app)
    assert office_state(app).healthy
    await asyncio.gather(*list(convert._tasks))
    await run_later(s)
    for node in [first, *others]:
        assert (await blob_of(s, node["id"])).pdf_state == "ready"
    async with maker(s)() as session:
        assert await jobs.failed(session) == []


@pytest.mark.anyio
async def test_lang_weg_na_een_mislukte_poging_is_failed(client, admin, office):
    """Is DS na een mislukte poging al langer dan tien minuten weg, dan wacht de tweede poging
    niet meer: failed, zoals een bestand dat DS laat vastlopen."""
    jonas, _ = admin
    s = office.settings
    office_state(app).ever_ok = True
    office.conversions.configure({"hold": True})
    node = await upload(client, jonas, "zwaar.docx", docx_with_pages(2))
    task = office.background(jobs.run_pending(s))
    await until(lambda: office.log("convert_source"))
    await office.fake.kill()
    await task
    assert office_state(app).health == "down"
    office_state(app).down_since = jobs.now() - timedelta(minutes=11)
    await run_later(s, 60)
    assert (await blob_of(s, node["id"])).pdf_state == "failed"
    [job] = await office_jobs(s)
    assert (job.status, job.attempts) == ("failed", 2)
    # De viewer en /beheer noemen code 2 (DS antwoordde niet), niet een onbekende 0
    assert "(code 2)" in job.error
    office_state(app).health = "ok"
    r = await ask(client, jonas, node["id"], expect=502)
    assert code_of(r) == "office_convert_failed" and params_of(r) == {"code": 2}


@pytest.mark.anyio
async def test_ds_niet_gezond_dan_geen_poging(client, admin, office):
    jonas, _ = admin
    s = office.settings
    office_state(app).health = "down"
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await jobs.run_pending(s)
    blob = await blob_of(s, node["id"])
    assert blob.pdf_state is None
    [job] = await office_jobs(s)
    assert (job.status, job.attempts) == ("done", 1)
    assert office.log() == [] and office.transport.hosts == []
    # Weer gezond (de gezondheidslus): de inhaalronde neemt hem terug
    await health.check_once(app)
    assert office_state(app).healthy
    await asyncio.gather(*list(convert._tasks))
    await run_later(s)
    assert (await blob_of(s, node["id"])).pdf_state == "ready"
    assert office.converted() == [keys.convert_key(blob.sha256)]


async def set_session_state(s: Settings, key: str, state: str) -> None:
    async with maker(s)() as session:
        await session.execute(
            update(OfficeSession).where(OfficeSession.key == key).values(state=state)
        )
        await session.commit()


@pytest.mark.anyio
async def test_een_sessie_die_opent_of_opslaat_laat_de_conversie_wachten(client, admin, office):
    """Met de echte sessies van O2: een sessie die opent (pending, de config is uitgegeven) of
    opslaat (saving, de laatste is weg en DS slaat op) laat de conversie wachten, zonder poging.
    Een open sessie (iemand bewerkt) en een dichte niet."""
    jonas, _ = admin
    s = office.settings
    edited = await upload(client, jonas, "bewerkt.docx", docx_with_pages(1, "Bewerkt"))
    r = await client.post(f"/api/v1/office/files/{edited['id']}/session", json={}, headers=jonas)
    assert r.status_code == 200, r.text
    key = r.json()["key"]
    async with maker(s)() as session:
        assert (await sessions.find_by_key(session, key)).state == "pending"
        assert await sessions.busy(session)
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    photo = await upload(client, jonas, "foto.png", picture("PNG", (300, 200)))
    before = jobs.now()
    await jobs.run_pending(s)
    # De foto kreeg zijn preview; de conversies wachten, zonder poging
    assert (await blob_of(s, photo["id"])).preview == "ready"
    assert (await blob_of(s, node["id"])).pdf_state == "pending"
    waiting = await office_jobs(s)
    assert len(waiting) == 2
    for job in waiting:
        assert (job.status, job.attempts) == ("queued", 0)
        wait = job.run_after.replace(tzinfo=None) - before.replace(tzinfo=None)
        assert timedelta(seconds=4) < wait < timedelta(seconds=30)
    assert office.converted() == []
    # De laatste bewerker is weg en DS slaat op: nog steeds wachten
    await set_session_state(s, key, "saving")
    await run_later(s, 10)
    assert (await blob_of(s, node["id"])).pdf_state == "pending"
    assert [(j.status, j.attempts) for j in await office_jobs(s)] == [("queued", 0)] * 2
    assert office.converted() == []
    # Open (iemand bewerkt, er wordt niets geopend of opgeslagen): de conversie loopt
    await set_session_state(s, key, "open")
    async with maker(s)() as session:
        assert not await sessions.busy(session)
    await run_later(s, 10)
    assert (await blob_of(s, node["id"])).pdf_state == "ready"
    assert (await blob_of(s, edited["id"])).pdf_state == "ready"
    assert [(j.status, j.attempts) for j in await office_jobs(s)] == [("done", 1)] * 2
    assert len(office.converted()) == 2


@pytest.mark.anyio
async def test_een_tweede_conversie_wacht_op_het_slot(client, admin, office):
    jonas, _ = admin
    s = office.settings
    office.conversions.configure({"hold": True})
    first = await upload(client, jonas, "een.docx", docx_with_pages(2))
    second = await upload(client, jonas, "twee.docx", docx_with_pages(3))
    # Eerst de previews; dan de eerste conversie, die blijft lopen
    await jobs.run_one(s)
    await jobs.run_one(s)
    task = office.background(jobs.run_one(s))
    await until(lambda: office.log("convert_source"))
    # De tweede komt over 5 s terug, zonder poging
    assert await jobs.run_one(s)
    waiting = [j for j in await office_jobs(s) if j.status == "queued"]
    assert len(waiting) == 1 and waiting[0].attempts == 0
    assert waiting[0].run_after.replace(tzinfo=None) > (jobs.now() + timedelta(seconds=3)).replace(
        tzinfo=None
    )
    # Een previewtaak voor een foto loopt intussen gewoon
    photo = await upload(client, jonas, "foto.png", picture("PNG", (300, 200)))
    assert await jobs.run_one(s)
    assert (await blob_of(s, photo["id"])).preview == "ready"
    office.conversions.configure({"hold": False})
    await task
    assert (await blob_of(s, first["id"])).pdf_state == "ready"
    assert (await blob_of(s, second["id"])).pdf_state == "pending"
    await run_later(s, 10)
    assert (await blob_of(s, second["id"])).pdf_state == "ready"
    assert len(office.converted()) == 2


@pytest.mark.anyio
async def test_het_slot_staat_niet_stil(client, admin, office, monkeypatch):
    """Komt het slot vrij, dan is de conversie die erop wacht meteen aan de beurt, niet pas na
    de wachttijd. Wachten op het slot doet geen invoercontrole: die komt pas als hij omzet."""
    jonas, _ = admin
    s = office.settings
    checked: list[str] = []
    check = inspect.check

    async def counting(settings, path, ext, sha):
        checked.append(sha)
        return await check(settings, path, ext, sha)

    monkeypatch.setattr(inspect, "check", counting)
    office.conversions.configure({"hold": True})
    first = await upload(client, jonas, "een.docx", docx_with_pages(2))
    await jobs.run_one(s)  # de preview
    task = office.background(jobs.run_one(s))  # de eerste conversie blijft lopen
    await until(lambda: office.log("convert_source"))
    second = await upload(client, jonas, "twee.docx", docx_with_pages(3))
    await jobs.run_pending(s)  # de preview; de conversie wacht op het slot
    sha = (await blob_of(s, second["id"])).sha256
    [waiting] = [j for j in await office_jobs(s) if j.status == "queued"]
    assert waiting.run_after.replace(tzinfo=None) > jobs.now().replace(tzinfo=None)
    assert sha not in checked
    office.conversions.configure({"hold": False})
    await task
    assert (await blob_of(s, first["id"])).pdf_state == "ready"
    # Nu meteen, niet pas over een paar seconden
    assert await jobs.run_pending(s) == 1
    assert (await blob_of(s, second["id"])).pdf_state == "ready"
    assert checked.count(sha) == 1


@pytest.mark.anyio
async def test_gevraagd_gaat_voor_ook_na_uitstel(client, admin, office):
    """Een conversie op verzoek is de volgende zodra het slot vrijkomt, ook als een worker hem
    al oppakte terwijl het slot bezet was (en hij dus moest wachten)."""
    jonas, _ = admin
    s = office.settings
    office.conversions.configure({"hold": True})
    await upload(client, jonas, "een.docx", docx_with_pages(2))
    await jobs.run_one(s)  # de preview
    task = office.background(jobs.run_one(s))  # de eerste conversie blijft lopen
    await until(lambda: office.log("convert_source"))
    for n in range(5):
        await upload(client, jonas, f"rij{n}.docx", docx_with_pages(2, f"Rij{n}"))
    wanted = await upload(client, jonas, "gevraagd.docx", docx_with_pages(2, "Gevraagd"))
    await jobs.run_pending(s)  # de previews; de conversies wachten op het slot
    await ask(client, jonas, wanted["id"])
    await jobs.run_pending(s)  # opgepakt terwijl het slot nog bezet is: hij wacht ook
    office.conversions.configure({"hold": False})
    await task
    await run_later(s, 120)
    blob = await blob_of(s, wanted["id"])
    assert blob.pdf_state == "ready"
    order = office.converted()
    assert len(order) == 7
    assert order.index(keys.convert_key(blob.sha256)) == 1


@pytest.mark.anyio
async def test_een_upload_gaat_voor_de_inhaalronde(client, admin, office):
    """Wachten ze allebei op het slot, dan krijgt een nieuwe upload het eerder dan de inhaalronde,
    ook als die er al langer op wacht."""
    jonas, _ = admin
    s = office.settings
    office.conversions.configure({"hold": True})
    await upload(client, jonas, "een.docx", docx_with_pages(2))
    await jobs.run_one(s)  # de preview
    task = office.background(jobs.run_one(s))  # de eerste conversie blijft lopen
    await until(lambda: office.log("convert_source"))
    async with maker(s)() as session:
        owner = await session.scalar(select(User))
        for n in range(3):
            node = await create_file(
                session,
                owner,
                name=f"oud{n}.docx",
                data=docx_with_pages(2, f"Oud{n}"),
                mime=DOCX,
                blobs=BlobStore(s.blob_dir),
            )
            (await session.get(Blob, node.blob_id)).preview = "ready"
        await session.commit()
    await convert.start_backfill(s)
    await step(s)
    async with maker(s)() as session:  # de inhaalronde is aan de beurt, en wacht op het slot
        await session.execute(
            update(Job).where(Job.type == convert.TYPE).values(run_after=jobs.now())
        )
        await session.commit()
    await jobs.run_pending(s)
    new = await upload(client, jonas, "nieuw.docx", docx_with_pages(2, "Nieuw"))
    await jobs.run_pending(s)  # de preview; de conversie wacht op het slot
    office.conversions.configure({"hold": False})
    await task
    await run_later(s, 120)
    blob = await blob_of(s, new["id"])
    assert blob.pdf_state == "ready"
    order = office.converted()
    assert len(order) == 5
    assert order.index(keys.convert_key(blob.sha256)) == 1


@pytest.mark.anyio
async def test_een_rij_conversies_loopt_door(client, admin, office, monkeypatch):
    """Met de echte workers: vijf conversies in de rij zijn na elkaar klaar, zonder dat het slot
    tussendoor seconden leeg staat (vroeger één per WAIT_SECONDS)."""
    jonas, _ = admin
    s = office.settings

    async def no_thumbnails(_settings, _sha):
        return False  # alleen het omzetten telt hier

    monkeypatch.setattr(convert, "thumbnails", no_thumbnails)
    nodes = [
        await upload(client, jonas, f"d{n}.docx", docx_with_pages(2, f"Doc{n}")) for n in range(5)
    ]
    convert._converting.add("0" * 64)  # het slot bezet: de previews lopen, de conversies wachten
    await jobs.run_pending(s)
    convert._converting.clear()
    assert len(await office_jobs(s, "queued")) == 5
    async with maker(s)() as session:
        await session.execute(
            update(Job).where(Job.type == convert.TYPE).values(run_after=jobs.now())
        )
        await session.commit()

    async def states() -> list[str | None]:
        return [(await blob_of(s, node["id"])).pdf_state for node in nodes]

    runner = jobs.Runner(s)
    started = asyncio.get_running_loop().time()
    await runner.start()
    try:
        for _ in range(500):
            if await states() == ["ready"] * 5:
                break
            await asyncio.sleep(0.01)
    finally:
        await runner.stop()
    took = asyncio.get_running_loop().time() - started
    assert await states() == ["ready"] * 5, f"na {took:.1f} s"
    assert took < convert.WAIT_SECONDS


# --- foutcodes en adressen -----------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("code", "state", "shown"),
    [
        (-3, "failed", -3),
        (-7, "failed", -7),
        (-9, "failed", -9),
        (-10, "failed", -10),
        (-5, "none", -5),
    ],
)
async def test_blijvende_fouten(client, admin, office, code, state, shown):
    jonas, _ = admin
    s = office.settings
    office.conversions.configure({"fail": [code]})
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await run_later(s)
    blob = await blob_of(s, node["id"])
    assert blob.pdf_state == state
    [job] = await office_jobs(s)
    assert job.status == "failed" and f"(code {code})" in job.error
    r = await ask(client, jonas, node["id"], expect=502)
    assert code_of(r) == "office_convert_failed" and params_of(r) == {"code": shown}
    assert len(office.converted()) == 1  # geen tweede poging


@pytest.mark.anyio
async def test_min_1_is_een_gewone_poging(client, admin, office):
    jonas, _ = admin
    s = office.settings
    office.conversions.configure({"fail": [-1]})
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await jobs.run_pending(s)
    assert (await blob_of(s, node["id"])).pdf_state == "pending"
    [job] = await office_jobs(s)
    assert (job.status, job.attempts) == ("queued", 1) and "(code -1)" in job.error


@pytest.mark.anyio
async def test_min_8_geeft_de_melding(client, admin, office):
    """DS weigert ons token: geen poging, en meteen de grote controle van health.py, die de
    melding zet (misconfigured). Met het goede geheim komt alles terug."""
    jonas, _ = admin
    s = office.settings
    right = office.fake.secret
    office.fake.secret = "een-ander-geheim-van-minstens-32-tekens"
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await jobs.run_pending(s)
    blob = await blob_of(s, node["id"])
    assert blob.pdf_state is None
    state = office_state(app)
    assert state.health == "misconfigured" and state.problem == health.SECRET_PROBLEM
    assert [e["signed"] for e in office.log("convert")] == [False]
    assert office.log("convert_source") == []
    office.fake.secret = right
    await health.check_once(app)
    assert state.healthy
    await asyncio.gather(*list(convert._tasks))
    await run_later(s)
    assert (await blob_of(s, node["id"])).pdf_state == "ready"


@pytest.mark.anyio
async def test_een_vreemde_host_volgen_we_nooit(client, admin, office):
    """fileUrl wijst naar een andere host: de app haalt de PDF bij PLATFORM_OFFICE_URL (alleen
    pad en query) en spreekt die host nooit aan."""
    jonas, _ = admin
    s = office.settings
    office.conversions.configure({"host": "elders.example:9999"})
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await jobs.run_pending(s)
    assert (await blob_of(s, node["id"])).pdf_state == "ready"
    assert set(office.transport.hosts) == {"office"}


@pytest.mark.anyio
async def test_een_adres_van_een_andere_key_geweigerd(client, admin, office):
    jonas, _ = admin
    s = office.settings
    office.conversions.configure({"wrong_key": True})
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await run_later(s)
    blob = await blob_of(s, node["id"])
    assert blob.pdf_state == "failed"
    [job] = await office_jobs(s)
    assert job.status == "failed" and f"(code {oo_convert.BAD_RESULT})" in job.error
    assert office.log("convert_file") == []  # nooit opgehaald
    assert not convert.pdf_path(BlobStore(s.blob_dir), blob.sha256).exists()


@pytest.mark.anyio
async def test_geen_pdf_terug(client, admin, office):
    jonas, _ = admin
    s = office.settings
    office.conversions.configure({"not_pdf": True})
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await run_later(s)
    blob = await blob_of(s, node["id"])
    assert blob.pdf_state == "failed"
    assert not convert.pdf_path(BlobStore(s.blob_dir), blob.sha256).exists()


@pytest.mark.anyio
async def test_een_grant_verloopt(client, admin, office, monkeypatch):
    jonas, _ = admin
    s = office.settings
    monkeypatch.setattr(grants, "TTL", 0.0)  # al verlopen als DS hem ophaalt
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await jobs.run_pending(s)
    blob = await blob_of(s, node["id"])
    [source] = office.log("convert_source")
    assert source["http"] == 404  # DS kreeg -4 en vraagt een nieuwe key
    assert blob.pdf_state == "pending"
    monkeypatch.setattr(grants, "TTL", 600.0)
    await run_later(s, 60)
    assert (await blob_of(s, node["id"])).pdf_state == "ready"
    assert office.converted()[-1] == keys.convert_key(blob.sha256, 2)


@pytest.mark.anyio
async def test_de_bron_alleen_met_het_token_van_ds(client, admin, office):
    jonas, _ = admin
    s = office.settings
    data = docx_with_pages(2)
    node = await upload(client, jonas, "brief.docx", data)
    sha = (await blob_of(s, node["id"])).sha256
    grant = grants.grant(sha)
    path = f"/internal/office/src/{grant}"
    url = f"{s.office_app_url}{path}"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=internal_app), base_url=s.office_app_url
    ) as internal:
        assert (await internal.get(path)).status_code == 401
        other = protocol.download_token(
            s.office_secret, f"{s.office_app_url}/internal/office/src/anders"
        )
        r = await internal.get(path, headers={"Authorization": f"Bearer {other}"})
        assert r.status_code == 401
        token = protocol.download_token(s.office_secret, url)
        r = await internal.get(path, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200 and r.content == data
        assert r.headers["repr-digest"].startswith("sha-256=:")
        grants.clear()
        r = await internal.get(path, headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 404
    # Niet op de gewone poort
    assert (await client.get(path)).status_code == 404


# --- opruimen en caches --------------------------------------------------------------------


@pytest.mark.anyio
async def test_een_nieuwe_preview_houdt_de_pdf(client, admin, office):
    jonas, _ = admin
    s = office.settings
    node = await upload(client, jonas, "brief.docx", docx_with_pages(3))
    await jobs.run_pending(s)
    blob = await blob_of(s, node["id"])
    blobs = BlobStore(s.blob_dir)
    path = convert.pdf_path(blobs, blob.sha256)
    before = path.read_bytes()
    async with maker(s)() as session:
        await session.execute(
            update(Blob).where(Blob.sha256 == blob.sha256).values(render_version=0)
        )
        await jobs.enqueue(session, previews.TYPE, {"sha": blob.sha256}, key="preview:opnieuw")
        await session.commit()
    await jobs.run_pending(s)
    after = await blob_of(s, node["id"])
    assert after.pdf_state == "ready" and path.read_bytes() == before
    assert {256, 1024} <= set(after.preview_meta["sizes"]) and after.preview_meta["pages"] == 3
    assert (previews.directory(blobs, blob.sha256) / "256.webp").is_file()
    assert office.converted() == [keys.convert_key(blob.sha256)]  # geen tweede conversie
    assert [j.status for j in await office_jobs(s)] == ["done", "done"]


@pytest.mark.anyio
async def test_de_blob_verdwijnt_tijdens_het_omzetten(client, admin, office):
    jonas, _ = admin
    s = office.settings
    office.conversions.configure({"hold": True})
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    sha = None
    task = office.background(jobs.run_pending(s))
    await until(lambda: office.log("convert_source"))
    sha = (await blob_of(s, node["id"])).sha256
    await trash_and_purge(client, jonas, node["id"])
    async with maker(s)() as session:
        assert await session.get(Blob, sha) is None
    office.conversions.configure({"hold": False})
    await task
    blobs = BlobStore(s.blob_dir)
    assert office.log("convert_file")  # de PDF kwam wel binnen
    assert not convert.pdf_path(blobs, sha).exists()
    assert not previews.directory(blobs, sha).exists()
    assert list((convert.office_dir(blobs) / ".tmp").iterdir()) == []


@pytest.mark.anyio
async def test_opruimen_haalt_de_pdf_weg(client, admin, office):
    jonas, _ = admin
    s = office.settings
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await jobs.run_pending(s)
    sha = (await blob_of(s, node["id"])).sha256
    path = convert.pdf_path(BlobStore(s.blob_dir), sha)
    assert path.is_file()
    await trash_and_purge(client, jonas, node["id"])
    assert not path.exists()


@pytest.mark.anyio
async def test_een_nieuw_adres_voor_de_thumbnails(client, admin, office):
    """Een pptx met een ingebedde thumbnail heeft al een preview (B21). Na de conversie komen
    256 en 1024 uit de PDF, met een ander adres: anders hield de browser de oude."""
    jonas, _ = admin
    s = office.settings
    office_state(app).health = "down"  # eerst zonder conversie
    data = replace_part(pptx_bytes(), "docProps/thumbnail.jpeg", jpeg((400, 300)))
    node = await upload(client, jonas, "dia.pptx", data)
    await jobs.run_pending(s)
    old = (await listing(client, jonas, node["id"]))[node["id"]]
    assert old["state"] == "ready" and "v=" not in old["thumb"]
    old_thumb = (await client.get(old["thumb"])).content
    office_state(app).health = "ok"
    await convert.start_backfill(s)
    await run_later(s)
    new = (await listing(client, jonas, node["id"]))[node["id"]]
    assert f"v={keys.PDF_VERSION}" in new["thumb"] and f"v={keys.PDF_VERSION}" in new["large"]
    assert new["thumb"] != old["thumb"] and new["large"] != old["large"]
    r = await client.get(new["thumb"])
    assert r.status_code == 200 and r.content != old_thumb


@pytest.mark.anyio
async def test_een_hogere_pdf_version(client, admin, office, monkeypatch):
    """Een nieuwe PDF_VERSION: de inhaalronde zet opnieuw om (een nieuwe key), de paginacache
    gaat leeg, de ETag en info.json dragen de nieuwe versie, en het aantal pagina's klopt."""
    jonas, _ = admin
    s = office.settings
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await jobs.run_pending(s)
    doc = (await client.get(f"/api/v1/files/{node['id']}/document", headers=jonas)).json()
    assert (doc["kind"], doc["source"], doc["pages"]) == ("pdf", "office", 2)
    page = doc["page_url"].replace("{n}", "2").replace("{w}", "640")
    r = await client.get(page)
    assert r.status_code == 200 and r.headers["etag"].endswith('-p1"')
    old_page = r.content
    monkeypatch.setattr(keys, "PDF_VERSION", 2)
    office.conversions.configure({"pages": 5})
    await convert.start_backfill(s)
    await run_later(s)
    blob = await blob_of(s, node["id"])
    assert (blob.pdf_state, blob.pdf_version) == ("ready", 2)
    assert office.converted()[-1] == f"p2-{blob.sha256}"
    doc = (await client.get(f"/api/v1/files/{node['id']}/document", headers=jonas)).json()
    assert doc["pages"] == 5
    r = await client.get(doc["page_url"].replace("{n}", "2").replace("{w}", "640"))
    assert r.status_code == 200 and r.headers["etag"].endswith('-p2"')
    assert r.content != old_page
    r = await client.get(doc["page_url"].replace("{n}", "5").replace("{w}", "640"))
    assert r.status_code == 200
    thumb = (await listing(client, jonas, node["id"]))[node["id"]]["thumb"]
    assert "v=2" in thumb


# --- de inhaalronde ------------------------------------------------------------------------


async def step(s: Settings) -> Job:
    """Eén stuk van de inhaalronde: de taak die klaarstaat, zoals jobs.claim en execute."""
    async with maker(s)() as session:
        job = await session.scalar(
            select(Job).where(Job.type == convert.BACKFILL, Job.status == "queued")
        )
        job.status = "running"
        await session.commit()
    await convert.backfill(s, job.payload)
    async with maker(s)() as session:
        await session.execute(update(Job).where(Job.id == job.id).values(status="done"))
        await session.commit()
    return job


async def convert_queued(s: Settings, keep: int = 0) -> None:
    """Alsof de conversies die klaarstaan liepen (op keep na): hun taken op done."""
    async with maker(s)() as session:
        ids = (
            await session.scalars(
                select(Job.id)
                .where(Job.type == convert.TYPE, Job.status == "queued")
                .order_by(Job.created_at)
            )
        ).all()
        done = ids[: max(0, len(ids) - keep)]
        await session.execute(update(Job).where(Job.id.in_(done)).values(status="done"))
        await session.commit()


@pytest.mark.anyio
async def test_de_inhaalronde_wacht_tot_de_rij_korter_is(client, admin, office, monkeypatch):
    """Het volgende stuk komt pas als er minder dan een stuk aan conversies klaarstaat. Zo
    staat na het installeren nooit de hele bibliotheek in de rij."""
    monkeypatch.setattr(convert, "BATCH", 3)
    s = office.settings
    async with maker(s)() as session:
        owner = await session.scalar(select(User))
        for n in range(8):
            node = await create_file(
                session, owner, name=f"d{n}.docx", data=b"docx %d" % n, mime=DOCX
            )
            (await session.get(Blob, node.blob_id)).preview = "ready"
        await session.commit()
    assert await convert.start_backfill(s)
    first = await step(s)
    assert len(await office_jobs(s, "queued")) == 3
    # Drie in de rij: geen nieuw stuk, maar dezelfde plek over 60 s opnieuw
    before = jobs.now().replace(tzinfo=None)
    waited = await step(s)
    assert waited.payload != first.payload
    assert len(await office_jobs(s, "queued")) == 3
    async with maker(s)() as session:
        again = await session.scalar(
            select(Job).where(Job.type == convert.BACKFILL, Job.status == "queued")
        )
    assert again.payload == waited.payload
    assert again.run_after.replace(tzinfo=None) - before >= timedelta(seconds=59)
    # Nog één in de rij: wel het volgende stuk
    await convert_queued(s, keep=1)
    await step(s)
    assert len(await office_jobs(s, "queued")) == 4
    await convert_queued(s)
    await step(s)
    assert len(await office_jobs(s)) == 8
    async with maker(s)() as session:
        assert (
            await session.scalar(
                select(func.count()).where(Job.type == convert.BACKFILL, Job.status == "queued")
            )
        ) == 0


@pytest.mark.anyio
async def test_de_inhaalronde_in_stukken(client, admin, office):
    s = office.settings
    async with maker(s)() as session:
        owner = await session.scalar(select(User))
        for n in range(450):
            node = await create_file(
                session, owner, name=f"d{n}.docx", data=b"docx %d" % n, mime=DOCX
            )
            blob = await session.get(Blob, node.blob_id)
            blob.preview = "ready"
        # Niet: ask, unsafe, none, een preview die nog loopt, alleen een eerdere versie
        for n, (state, preview) in enumerate(
            [("ask", "ready"), ("unsafe", "ready"), ("none", "ready"), (None, None)]
        ):
            node = await create_file(
                session, owner, name=f"x{n}.docx", data=b"anders %d" % n, mime=DOCX
            )
            blob = await session.get(Blob, node.blob_id)
            blob.preview, blob.pdf_state = preview, state
        old = await create_blob(session, b"alleen een versie", mime=DOCX)
        old.preview = "ready"
        await session.commit()
        old_sha = old.sha256
    started = jobs.now().replace(tzinfo=None)
    assert await convert.start_backfill(s)
    assert not await convert.start_backfill(s)  # er staat er al een
    counts, times, ran = [], [], []
    for _ in range(3):
        ran.append(jobs.now().replace(tzinfo=None))
        job = await step(s)
        times.append(job.run_after.replace(tzinfo=None))
        counts.append(len(await office_jobs(s)))
        await convert_queued(s)  # zo is er plek voor het volgende stuk
    assert counts == [200, 400, 450]
    async with maker(s)() as session:
        assert (await session.scalar(select(func.count()).where(Job.type == convert.BACKFILL))) == 3
        assert (
            await session.scalar(
                select(func.count()).where(Job.type == convert.BACKFILL, Job.status == "queued")
            )
        ) == 0
        pending = await session.scalar(
            select(func.count()).select_from(Blob).where(Blob.pdf_state == "pending")
        )
        assert pending == 450
        assert (await session.get(Blob, old_sha)).pdf_state is None
        office_runs = (
            await session.scalars(select(Job.run_after).where(Job.type == convert.TYPE))
        ).all()
    # Elk volgend stuk pas 60 s nadat het vorige liep; de conversies van een stuk ook pas na
    # 60 s (nieuwe uploads gaan voor)
    assert times[0] <= started + timedelta(seconds=5)
    for previous, chained in zip(ran, times[1:], strict=False):
        assert chained - previous >= timedelta(seconds=59)
    assert min(t.replace(tzinfo=None) for t in office_runs) >= started + timedelta(seconds=59)


@pytest.mark.anyio
async def test_de_inhaalronde_maakt_groot_ask(client, admin, office):
    s = office.settings
    async with maker(s)() as session:
        owner = await session.scalar(select(User))
        node = await create_file(session, owner, name="groot.docx", data=b"groot", mime=DOCX)
        blob = await session.get(Blob, node.blob_id)
        blob.preview, blob.size = "ready", s.office_auto_convert_bytes + 1
        await session.commit()
        sha = blob.sha256
    await convert.start_backfill(s)
    await step(s)
    async with maker(s)() as session:
        assert (await session.get(Blob, sha)).pdf_state == "ask"
    assert await office_jobs(s) == []


@pytest.mark.anyio
async def test_zonder_office_niets_klaargezet(client, admin, settings):
    jonas, _ = admin
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await jobs.run_pending(settings)
    assert (await blob_of(settings, node["id"])).pdf_state is None
    assert await office_jobs(settings) == []
    assert not await convert.start_backfill(settings)
    await convert.backfill(settings, {"after": ""})
    assert await office_jobs(settings) == []
    r = await client.post(f"/api/v1/office/files/{node['id']}/convert", headers=jonas)
    assert r.status_code == 503 and code_of(r) == "office_off"


@pytest.mark.anyio
async def test_uitgezet_op_beheer(client, admin, office):
    jonas, _ = admin
    s = office.settings
    office_policy(app).config = OfficeConfig(enabled=False)
    node = await upload(client, jonas, "brief.docx", docx_with_pages(2))
    await jobs.run_pending(s)
    assert (await blob_of(s, node["id"])).pdf_state is None
    assert await office_jobs(s) == []
    r = await ask(client, jonas, node["id"], expect=503)
    assert code_of(r) == "office_off"
    await convert.start_backfill(s)
    await run_later(s)
    assert await office_jobs(s) == [] and office.log() == []


@pytest.mark.anyio
async def test_bij_het_starten_weg_wat_een_crash_liet_staan(tmp_path, monkeypatch):
    """Stopte het proces midden in een conversie of in het ophalen van een opgeslagen document
    (een kill, OOM, de stroom weg), dan bleef het tussenbestand staan, tot 512 MB per keer, en
    niets ruimde het op. Bij het starten gaat het nu weg; de PDF's zelf blijven."""
    from tests.test_rate_limits import started

    blobs = BlobStore(Settings(data_dir=tmp_path).blob_dir)
    stray = [convert.office_dir(blobs) / ".tmp" / "tmpabc" / "out.pdf", blobs.tmp / "tmpdef"]
    for path in stray:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"%PDF-1.7 half")
    pdf = convert.pdf_path(blobs, "a" * 64)
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF-1.7")
    async with started(tmp_path, monkeypatch):
        assert [path for path in stray if path.exists()] == []
    assert pdf.is_file()
