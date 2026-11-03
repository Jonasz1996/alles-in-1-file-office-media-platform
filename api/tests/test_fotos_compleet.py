"""Elke foto en video krijgt een thumbnail en metadata, langs elke weg (docs/fotos-indexeren.md):
wat eerder stil zonder preview bleef, en hoe het alsnog komt.

- een JPEG boven de pixelgrens, een afgekapte JPEG, PSD, JPEG 2000 en camera-RAW (de
  ingebedde JPEG) krijgen nu een preview; een PNG boven de grens nog steeds niet (een "bom");
- dezelfde inhoud nog eens uploaden probeert een mislukte opnieuw;
- een preview waarvan het bestand op schijf ontbreekt, wordt opnieuw gemaakt (heal);
- "alles nalopen" (/beheer en app.cli previews-recheck) zet alles wat ontbreekt klaar, en
  /beheer telt hoe ver de foto's zijn;
- een video zonder ffmpeg in de worker komt terug zodra de worker gezond is (enqueue_later);
- een nieuwe RENDER_VERSION maakt wat eerder geen preview kreeg opnieuw, en herkent een RAW die
  eerder application/octet-stream heette;
- eerst thumbnails (preview.large pas daarna), en wat iemand bekijkt eerst (to_front).
"""

import asyncio
import dataclasses
import io
import shutil
import struct
import time
import types
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from PIL import Image
from sqlalchemy import event, func, select, update

from app import jobs, mime, photodate, previews, render
from app.db import get_engine
from app.models import Blob, Job, Node
from app.storage import BlobStore
from tests.factories import create_file, create_user, maker
from tests.test_previews import (
    login_as,
    picture,
    preview_of,
    sha_of,
    upload,
    zip_bytes,
)

pytestmark = pytest.mark.anyio


def photo(size=(1600, 1200), **save) -> bytes:
    """Een JPEG met wat structuur (een effen vlak comprimeert tot bijna niets)."""
    img = Image.linear_gradient("L").resize(size).convert("RGB")
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85, **save)
    return buf.getvalue()


def draw(tmp_path: Path, data: bytes, name: str, mime_type=None, *, first=False, pixels=10**8):
    """render.render in dit proces (zoals run_render in test_previews), met first."""
    source = tmp_path / "bron"
    source.write_bytes(data)
    out, work = tmp_path / "out", tmp_path / "work"
    for d in (out, work):
        shutil.rmtree(d, ignore_errors=True)
        d.mkdir()
    request = {"source": str(source), "out": str(out), "work": str(work), "mime": mime_type,
               "name": name, "max_pixels": pixels, "first": first}  # fmt: skip
    result = render.render(request)
    made = {}
    for f in out.iterdir():
        with Image.open(f) as img:
            made[int(f.stem)] = img.size
    assert sorted(made) == sorted(result["sizes"])
    return result, made


def exif_tiff(make="Nikon", taken="2019:07:01 12:30:00", orientation=1, magic=b"MM\x00*") -> bytes:
    """Een TIFF-kop met camera, draaiing en opnamedatum, zoals vooraan in een RAW."""
    exif = Image.Exif()
    exif[0x010F] = make
    exif[0x0112] = orientation
    exif.get_ifd(0x8769)[0x9003] = taken
    data = exif.tobytes()[6:]  # zonder "Exif\0\0"
    return magic + data[4:]


def psd(size=(40, 30)) -> bytes:
    """Een Photoshop-bestand met de hand (Pillow kan PSD lezen, niet schrijven): RGB, 8 bits,
    ongecomprimeerd."""
    w, h = size
    head = b"8BPS" + struct.pack(">H", 1) + b"\0" * 6 + struct.pack(">HIIHH", 3, h, w, 8, 3)
    planes = bytes([200]) * (w * h) + bytes([10]) * (w * h) * 2
    return head + struct.pack(">III", 0, 0, 0) + struct.pack(">H", 0) + planes


async def blob_of(settings, sha) -> Blob:
    async with maker(settings)() as session:
        return await session.get(Blob, sha)


async def set_blob(settings, sha, **values) -> None:
    async with maker(settings)() as session:
        await session.execute(update(Blob).where(Blob.sha256 == sha).values(**values))
        await session.commit()


async def queued(settings, type_=previews.TYPE) -> list[str]:
    async with maker(settings)() as session:
        rows = await session.scalars(
            select(Job.payload).where(Job.type == type_, Job.status == "queued")
        )
        return [p["sha"] for p in rows.all()]


# --- wat eerder geen preview kreeg ----------------------------------------------------------


def test_jpeg_boven_de_pixelgrens_krijgt_een_thumbnail(tmp_path):
    """Een panorama of een foto van 200 MP: draft decodeert hem kleiner, dus geen "bom"."""
    data = photo((4096, 3072))  # 12,6 MP, de grens hier 4 MP (zoals 400 MP tegen 100 MP)
    result, made = draw(tmp_path, data, "panorama.jpg", pixels=4_000_000)
    assert result["state"] == "ready" and result["meta"]["width"] == 4096
    assert made == {256: (256, 192), 1024: (1024, 768)}
    # De eerste ronde: alleen de thumbnail, en "more" voor preview.large
    result, made = draw(tmp_path, data, "panorama.jpg", first=True, pixels=4_000_000)
    assert result["more"] is True and made == {256: (256, 192)}
    # Meer dan JPEG_PIXELS keer de grens, of een PNG boven de grens: nog altijd niet
    assert draw(tmp_path, data, "a.jpg", pixels=3_000_000)[0]["state"] == "none"
    png = picture("PNG", (1600, 1200))
    assert draw(tmp_path, png, "a.png", pixels=1_000_000)[0]["state"] == "none"


def test_afgekapte_jpeg_krijgt_een_thumbnail(tmp_path):
    """Een afgebroken sync of kopie: wat ontbreekt wordt grijs, maar de foto staat erin."""
    data = photo((1600, 1200))
    cut = data[: len(data) * 2 // 3]
    result, made = draw(tmp_path, cut, "half.jpg")
    assert result["state"] == "ready" and made[256] == (256, 192)
    # Een afgekapte PNG blijft zonder (alleen JPEG mag afgekapt)
    png = picture("PNG", (300, 300))
    assert draw(tmp_path, png[:200], "half.png")[0]["state"] == "none"


def test_psd_en_jpeg_2000_krijgen_een_preview(tmp_path):
    """Groter dan 1024 ook 2048 (display): de browser kan ze zelf niet tonen, zoomen in de
    viewer gaat daarheen (web/src/viewer/media.ts browserShows)."""
    result, made = draw(tmp_path, psd((1400, 1050)), "ontwerp.psd", "image/vnd.adobe.photoshop")
    assert result["state"] == "ready" and made[256] == (256, 192)
    assert made[2048] == (1400, 1050)
    assert mime.sniff(psd(), "x") == "image/vnd.adobe.photoshop"
    jp2 = picture("JPEG2000", (1400, 1050))
    assert mime.sniff(jp2, "x.jp2") == "image/jp2"
    result, made = draw(tmp_path, jp2, "scan.jp2", "image/jp2")
    assert result["state"] == "ready" and made[256] == (256, 192)
    assert made[2048] == (1400, 1050)


@pytest.mark.parametrize(
    ("name", "magic", "found"),
    [
        ("DSC_0001.NEF", b"MM\x00*", "image/tiff"),  # CR2, NEF, ARW, DNG, PEF: TIFF
        ("P1010001.ORF", b"MMOR", "image/x-olympus-orf"),
    ],
)
def test_camera_raw_krijgt_de_ingebedde_jpeg(tmp_path, name, magic, found):
    """Een RAW zelf kan Pillow niet lezen; de JPEG die de camera erin zet wel. Datum, camera en
    draaiing komen uit de RAW (de ingebedde JPEG heeft ze vaak niet)."""
    head = exif_tiff(orientation=6, magic=magic)
    data = head + b"\0" * 1000 + photo((1600, 1200)) + b"\0" * 500 + picture("JPEG", (160, 120))
    assert mime.sniff(data[:8192], name) == found
    result, made = draw(tmp_path, data, name, found)
    assert result["state"] == "ready"
    meta = result["meta"]
    assert (meta["width"], meta["height"]) == (1200, 1600)  # gedraaid volgens de RAW
    assert meta["taken_at"].startswith("2019-07-01T12:30")
    assert meta["exif"]["make"] == "Nikon"
    assert made[256] == (192, 256) and made[1024] == (768, 1024)
    assert 2048 in made  # de browser kan een RAW niet tonen: ook de grote voor de viewer
    result, made = draw(tmp_path, data, name, found, first=True)
    assert result["more"] is True and set(made) == {256}


def test_raf_en_cr3_krijgen_de_ingebedde_jpeg(tmp_path):
    # Fujifilm: een eigen kop, de JPEG met zijn eigen EXIF
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9003] = "2021:03:04 05:06:07"
    raf = b"FUJIFILMCCD-RAW 0201FF383501" + b"\0" * 200 + photo((1200, 900), exif=exif)
    assert mime.sniff(raf, "DSCF0001.RAF") == "image/x-fuji-raf"
    result, made = draw(tmp_path, raf, "DSCF0001.RAF", "image/x-fuji-raf")
    assert result["state"] == "ready" and made[256] == (256, 192)
    assert result["meta"]["taken_at"].startswith("2021-03-04T05:06")
    # Canon CR3: ISO-boxen; datum en camera in CMT1/CMT2 in de uuid-box van Canon
    cmt1 = exif_tiff(make="Canon", magic=b"MM\x00*")
    # CMT2 is de Exif-IFD zelf, als IFD0 van een eigen TIFF
    sub = Image.Exif()
    sub[0x9003] = "2022:08:09 10:11:12"
    cmt2 = sub.tobytes()[6:]
    inner = b"".join(struct.pack(">I", 8 + len(d)) + k + d for k, d in
                     ((b"CMT1", cmt1), (b"CMT2", cmt2)))  # fmt: skip
    uuid_box = struct.pack(">I", 24 + len(inner)) + b"uuid" + render.CR3_UUID + inner
    moov = struct.pack(">I", 8 + len(uuid_box)) + b"moov" + uuid_box
    ftyp = struct.pack(">I", 24) + b"ftypcrx " + b"\0\0\0\x01" + b"crx isom"
    cr3 = ftyp + moov + b"\0" * 100 + photo((1600, 1200))
    assert mime.sniff(cr3, "IMG_0001.CR3") == "image/x-canon-cr3"
    result, made = draw(tmp_path, cr3, "IMG_0001.CR3", "image/x-canon-cr3")
    assert result["state"] == "ready" and made[256] == (256, 192)
    assert result["meta"]["taken_at"].startswith("2022-08-09T10:11")


# Koppen die een worker eerder bezighielden (review F2): FFD8 FFDA zonder einde, met een SOF
# erbij, FFD8 steeds opnieuw (elke kop springt 65 KB per segment), en lange ketens van markers
SOF = b"\xff\xc0\x00\x11\x08\x00\x10\x00\x10\x03\x01\x22\x00\x02\x11\x01\x03\x11\x01"
CRAFTED = {
    "kop zonder einde": b"\xff\xd8\xff\xda\x00\x08" + b"\0" * 6,
    "kop met SOF zonder einde": b"\xff\xd8" + SOF + b"\xff\xda\x00\x08" + b"\0" * 6,
    "FFD8 steeds opnieuw": b"\xff\xd8",
    "lange ketens": b"\xff\xd8" + b"\xff\x01" * 1000,
}


@pytest.mark.parametrize("unit", list(CRAFTED.values()), ids=list(CRAFTED))
def test_een_gemaakte_raw_houdt_de_worker_niet_bezig(tmp_path, unit):
    """Elke kop zocht eerder opnieuw tot het eind van het bestand naar FFD9, en een kop die niet
    deugde telde niet mee: 1 MB kostte 45 s, 4 MB een worker tot de tijdgrens van de taak (300
    s), drie pogingen lang. Nu een vaste grens aan het werk (render._Scan, RAW_STEPS)."""
    body = unit * (4 * 1024 * 1024 // len(unit))
    source = tmp_path / "DSCF0001.RAF"
    # Zonder einde: niets; met één FFD9 helemaal achteraan mag de eerste kop met SOF "een JPEG"
    # zijn (Pillow weigert die dan), als het maar snel gaat
    for end in (b"", b"\xff\xd9"):
        source.write_bytes(b"FUJIFILMCCD-RAW 0201" + body + end)
        started = time.perf_counter()
        found = render.embedded_jpeg(source)
        assert time.perf_counter() - started < 5
        assert found is None or (end and found.startswith(unit[:4]) and found.endswith(end))


def test_de_grens_laat_echte_raws_heel(tmp_path):
    """Een DNG met tegels: elke tegel een eigen verliesloze JPEG (SOF3) vóór de preview. Die
    tellen mee in RAW_STEPS, maar 3000 tegels laten ruim genoeg over; ook rommel erachter."""
    tile = b"\xff\xd8\xff\xc4\x00\x04\x00\x00\xff\xc3\x00\x0b" + b"\0" * 40
    preview = photo((1600, 1200))
    data = exif_tiff() + tile * 3000 + preview + CRAFTED["kop zonder einde"] * 50_000
    source = tmp_path / "IMG_0001.DNG"
    source.write_bytes(data)
    assert render.embedded_jpeg(source) == preview


# --- langs elke weg ---------------------------------------------------------------------------


async def test_dezelfde_inhoud_nog_eens_probeert_een_mislukte_opnieuw(client, admin, settings):
    """Eerder bleef een mislukte (of een foto zonder preview) zo: ontdubbelen deed alleen
    ref_count + 1."""
    headers, _ = admin
    data = picture("PNG", (300, 200))
    first = await upload(client, headers, "a.png", data)
    sha = sha_of(first)
    assert await jobs.run_pending(settings) == 1
    await set_blob(settings, sha, preview="failed")
    again = await upload(client, headers, "kopie.png", data)
    assert sha_of(again) == sha
    assert await queued(settings) == [sha]
    assert (await blob_of(settings, sha)).preview == "pending"
    await jobs.run_pending(settings)
    info = await preview_of(client, headers, first["id"], again["id"])
    assert info[first["id"]]["state"] == info[again["id"]]["state"] == "ready"
    # Een zip zonder preview (none) is geen foto: die blijft zo
    z = zip_bytes(2)
    await upload(client, headers, "a.zip", z)
    await jobs.run_pending(settings)
    await upload(client, headers, "b.zip", z)
    assert await queued(settings) == []


async def test_een_ontbrekend_bestand_wordt_opnieuw_gemaakt(client, admin, settings):
    """Eerder: voor altijd 404 (een halve back-up, een kapotte schijf)."""
    headers, _ = admin
    node = await upload(client, headers, "a.jpg", photo((1600, 1200)))
    await jobs.run_pending(settings)
    info = (await preview_of(client, headers, node["id"]))[node["id"]]
    folder = previews.directory(BlobStore(settings.blob_dir), sha_of(node))
    # Alleen de grote kwijt: de thumbnail blijft gewoon, de tweede ronde komt terug
    (folder / "1024.webp").unlink()
    assert (await client.get(info["large"])).status_code == 404
    assert await queued(settings, previews.LARGE_TYPE) == [sha_of(node)]
    assert (await client.get(info["thumb"])).status_code == 200
    await jobs.run_pending(settings)
    assert (await client.get(info["large"])).status_code == 200
    # De thumbnail kwijt: alles opnieuw
    (folder / "256.webp").unlink()
    assert (await client.get(info["thumb"])).status_code == 404
    assert await queued(settings) == [sha_of(node)]
    await jobs.run_pending(settings)
    assert (await client.get(info["thumb"])).status_code == 200


async def test_alles_nalopen_en_de_telling_op_beheer(client, admin, settings):
    headers, _ = admin
    nodes = {
        name: await upload(client, headers, f"{name}.png", picture("PNG", (300, 200), color=c))
        for name, c in (("goed", "red"), ("mislukt", "green"), ("geen", "blue"),
                        ("weg", "white"), ("half", "black"), ("wacht", "gray"))
    }  # fmt: skip
    archive = await upload(client, headers, "a.zip", zip_bytes(1))
    await jobs.run_pending(settings)
    sha = {name: sha_of(n) for name, n in nodes.items()}
    await set_blob(settings, sha["mislukt"], preview="failed")
    await set_blob(settings, sha["geen"], preview="none")
    await set_blob(settings, sha["wacht"], preview="pending")  # en geen taak
    shutil.rmtree(previews.directory(BlobStore(settings.blob_dir), sha["weg"]))
    await set_blob(settings, sha["half"], preview_meta={"sizes": [256], "partial": True})
    r = await client.get("/api/v1/admin/jobs", headers=headers)
    assert r.json()["photos"] == {"total": 6, "ready": 2, "partial": 1, "large_failed": 0,
                                  "waiting": 1, "failed": 1, "none": 1, "later": 0}  # fmt: skip

    r = await client.post("/api/v1/admin/jobs/recheck-previews", headers=headers)
    assert r.status_code == 200 and r.json() == {"started": True}
    r = await client.post("/api/v1/admin/jobs/recheck-previews", headers=headers)
    assert r.json() == {"started": False}  # er staat er al een
    await jobs.run_pending(settings)
    info = await preview_of(client, headers, *(n["id"] for n in nodes.values()), archive["id"])
    for name, node in nodes.items():
        assert info[node["id"]]["state"] == "ready", name
        assert (await client.get(info[node["id"]]["thumb"])).status_code == 200, name
    assert info[archive["id"]]["state"] == "ready"  # een zip heeft wel een preview (de lijst)
    r = await client.get("/api/v1/admin/jobs", headers=headers)
    assert r.json()["photos"] == {"total": 6, "ready": 6, "partial": 0, "large_failed": 0,
                                  "waiting": 0, "failed": 0, "none": 0, "later": 0}  # fmt: skip
    # Alleen voor beheerders
    other = await login_as(client, headers, "gewoon@example.nl")
    r = await client.post("/api/v1/admin/jobs/recheck-previews", headers=other)
    assert r.status_code == 403


async def test_alles_nalopen_kan_ook_vanaf_de_commandoregel(settings):
    assert await previews.enqueue_recheck(settings) is True
    assert await previews.enqueue_recheck(settings) is False  # er staat er al een
    async with maker(settings)() as session:
        count = select(func.count()).where(Job.type == previews.RECHECK, Job.status == "queued")
        assert await session.scalar(count) == 1


def test_de_cli_zegt_of_alles_nalopen_klaarstaat(monkeypatch, capsys):
    from app import cli

    answers = iter([True, False])

    async def enqueue(_settings):
        return next(answers)

    monkeypatch.setattr(previews, "enqueue_recheck", enqueue)
    monkeypatch.setattr(cli, "get_engine", lambda _s: _NoEngine())
    assert cli.main(["previews-recheck"]) == 0
    assert "staat klaar" in capsys.readouterr().out
    assert cli.main(["previews-recheck"]) == 0
    assert "liep al" in capsys.readouterr().out


class _NoEngine:
    async def dispose(self) -> None:
        return None


async def test_video_zonder_ffmpeg_komt_terug_als_de_worker_gezond_is(
    settings, tmp_path, monkeypatch
):
    """Media aan, de worker zonder ffmpeg: eerder "none" voor altijd; nu "later", en de
    housekeeping van media zet hem weer klaar."""
    from tests.test_previews_video_worker import FAKE_MP4, _NoFfmpeg

    monkeypatch.setattr(render, "Media", _NoFfmpeg)
    source = tmp_path / "film.mp4"
    source.write_bytes(FAKE_MP4)
    out = tmp_path / "out"
    out.mkdir()
    worker = {"socket": str(tmp_path / "w.sock"), "key": "k"}
    result = render.video(source, FAKE_MP4[:64], out, tmp_path, 10**8, worker)
    assert result == {"state": "none", "meta": {"later": True}, "sizes": []}

    async with maker(settings)() as session:
        owner = await create_user(session, "video@example.nl")
        later = await create_file(session, owner, name="later.mp4", data=b"1", mime="video/mp4")
        kapot = await create_file(session, owner, name="kapot.mp4", data=b"2", mime="video/mp4")
        foto = await create_file(session, owner, name="a.jpg", data=b"3", mime="image/jpeg")
        for node, meta in ((later, {"later": True}), (kapot, {}), (foto, {"later": True})):
            blob = await session.get(Blob, node.blob_id)
            blob.preview, blob.preview_meta = "none", meta
        await session.commit()
    assert await previews.enqueue_later(settings) == 1
    assert await queued(settings) == [later.blob_id]
    assert (await blob_of(settings, later.blob_id)).preview == "pending"
    assert (await blob_of(settings, kapot.blob_id)).preview == "none"  # onleesbaar blijft zo


async def test_een_nieuwe_versie_maakt_wat_geen_preview_kreeg_opnieuw(settings):
    """RENDER_VERSION 2: foto's zonder preview (none, failed), RAW (als TIFF opgeslagen) en een
    RAW die eerder application/octet-stream heette; die laatste komt ook op de tijdlijn."""
    blobs = BlobStore(settings.blob_dir)
    orf = exif_tiff(magic=b"MMOR") + b"\0" * 100 + photo((800, 600))
    when = datetime(2024, 5, 6, 7, 8, tzinfo=UTC)
    async with maker(settings)() as session:
        owner = await create_user(session, "oud@example.nl")
        made = {}
        for name, data, kind, state in (
            ("groot.jpg", photo((900, 600)), "image/jpeg", "none"),
            ("kapot.png", picture("PNG", (90, 60)), "image/png", "failed"),
            ("a.nef", exif_tiff() + photo((800, 600)), "image/tiff", "ready"),
            ("b.orf", orf, "application/octet-stream", "none"),
            ("tekst.txt", b"hallo", "text/plain", "ready"),
            ("ok.jpg", photo((300, 200)), "image/jpeg", "ready"),
        ):
            node = await create_file(session, owner, name=name, data=data, mime=kind, blobs=blobs)
            node.modified_at = when
            blob = await session.get(Blob, node.blob_id)
            blob.preview, blob.preview_meta, blob.render_version = state, {"sizes": []}, 1
            made[name] = node
        await session.commit()
    assert await previews.enqueue_outdated(settings) == 1
    assert await jobs.run_pending(settings) == 1  # de keten; het opnieuw maken wacht even
    again = set(await queued(settings))
    assert again == {made[n].blob_id for n in ("groot.jpg", "kapot.png", "a.nef", "b.orf")}
    assert (await blob_of(settings, made["ok.jpg"].blob_id)).render_version == 2
    orf_blob = await blob_of(settings, made["b.orf"].blob_id)
    assert orf_blob.mime == "image/x-olympus-orf"
    async with maker(settings)() as session:
        node = await session.get(Node, made["b.orf"].id)
        assert node.mime == "image/x-olympus-orf" and node.taken_day == 20240506
    later = jobs.now() + timedelta(seconds=previews.RERENDER_DELAY + 1)
    await jobs.run_pending(settings, at=later)
    for name in ("groot.jpg", "kapot.png", "a.nef", "b.orf"):
        blob = await blob_of(settings, made[name].blob_id)
        assert blob.preview == "ready" and 256 in blob.preview_meta["sizes"], name
    # De datum uit de RAW zelf: de tijdlijn schuift mee
    async with maker(settings)() as session:
        node = await session.get(Node, made["b.orf"].id)
        assert node.taken_day == 20190701


# --- eerst thumbnails, en wat iemand bekijkt eerst ---------------------------------------------


async def test_eerst_alle_thumbnails_dan_de_grote(client, admin, settings):
    headers, _ = admin
    nodes = [await upload(client, headers, f"{i}.jpg", photo((1600, 1200 + i))) for i in range(3)]
    order = []
    while job := await jobs.claim(settings):
        order.append(job.type)
        await jobs.execute(settings, job)
    assert order == [previews.TYPE] * 3 + [previews.LARGE_TYPE] * 3
    info = await preview_of(client, headers, *(n["id"] for n in nodes))
    assert all(i["meta"]["sizes"] == [256, 1024] for i in info.values())


async def test_wat_iemand_bekijkt_gaat_voor(client, admin, settings):
    """De tijdlijn, POST /previews en de viewer zetten de wachtende taken van wat ze tonen
    naar voren; de laatst bekeken eerst. Eerder: in volgorde van uploaden."""
    headers, _ = admin
    nodes = [await upload(client, headers, f"{i}.png", picture("PNG", (60, 40 + i)))
             for i in range(4)]  # fmt: skip
    await preview_of(client, headers, nodes[2]["id"])
    r = await client.get(f"/api/v1/files/{nodes[3]['id']}/preview", headers=headers)
    assert r.status_code == 200
    first = await jobs.claim(settings)
    second = await jobs.claim(settings)
    assert [first.payload["sha"], second.payload["sha"]] == [sha_of(nodes[3]), sha_of(nodes[2])]
    # De tijdlijn: hier alle vier (de twee die al lopen, blijven lopen)
    r = await client.get("/api/v1/photos", headers=headers)
    assert r.status_code == 200 and len(r.json()["items"]) == 4
    async with maker(settings)() as session:
        fronted = await session.scalar(
            select(func.count()).where(Job.status == "queued", Job.run_after < jobs.FRONT)
        )
        assert fronted == 2
        # Op /beheer telt een naar voren gehaalde taak gewoon vanaf zijn aanmaken
        oldest = await jobs.oldest_queued(session)
        assert oldest is not None and oldest > datetime.now(UTC) - timedelta(minutes=5)


async def test_wat_de_viewer_toont_gaat_voor_alle_thumbnails(client, admin, settings):
    """Een RAW (of een HEIC met ingebedde thumbnail) heeft na de eerste ronde alleen 256 px, en de
    browser kan het origineel niet tonen. Opent iemand hem in de viewer tijdens een import, dan
    gaat zijn preview.large (rank 1) voor de thumbnails die wachten (rank 0); eerder pas als die
    allemaal klaar waren (review F3). De tijdlijn en een map halen geen grote versies naar voren:
    die tonen alleen thumbnails, en hun thumbnails gaan voor."""
    headers, _ = admin
    raw = exif_tiff() + b"\0" * 1000 + photo((1600, 1200))
    node = await upload(client, headers, "DSC_0001.NEF", raw)
    assert await jobs.run_one(settings)  # eerste ronde: alleen 256
    async with maker(settings)() as session:
        blob = await session.get(Blob, sha_of(node))
        assert blob.preview_meta["partial"] is True and blob.preview_meta["sizes"] == [256]
    # De import: thumbnails wachten. De tijdlijn haalt alleen hun eerste ronde naar voren
    waiting = [await upload(client, headers, f"{i}.png", picture("PNG", (60, 40 + i)))
               for i in range(5)]  # fmt: skip
    assert (await client.get("/api/v1/photos", headers=headers)).status_code == 200
    async with maker(settings)() as session:
        fronted = (await session.scalars(select(Job.type).where(Job.run_after < jobs.FRONT))).all()
        assert sorted(fronted) == [previews.TYPE] * 5
        # Niets naar voren gehaald: eerst alle thumbnails, dan de grote (rank)
        await session.execute(update(Job).values(run_after=jobs.now() - timedelta(seconds=1)))
        await session.commit()
    first = await jobs.claim(settings, lane=previews.LANE)
    assert first.type == previews.TYPE
    async with maker(settings)() as session:
        await session.execute(update(Job).where(Job.id == first.id).values(status="queued"))
        await session.commit()
    # De viewer: de grote versie van deze ene foto meteen, vóór de thumbnails
    detail = (await client.get(f"/api/v1/files/{node['id']}/preview", headers=headers)).json()
    assert detail["meta"]["partial"] is True and detail["display"] is None
    claimed = await jobs.claim(settings, lane=previews.LANE)
    assert (claimed.type, claimed.payload["sha"]) == (previews.LARGE_TYPE, sha_of(node))
    await jobs.execute(settings, claimed)
    detail = (await client.get(f"/api/v1/files/{node['id']}/preview", headers=headers)).json()
    assert "partial" not in detail["meta"] and "/2048.webp?" in detail["display"]
    # Daarna gewoon de thumbnails
    rest = [(await jobs.claim(settings, lane=previews.LANE)).type for _ in waiting]
    assert rest == [previews.TYPE] * 5


async def test_een_mislukte_tweede_ronde_telt_niet_meer_als_onderweg(
    client, admin, settings, monkeypatch
):
    """Mislukt de tweede ronde ook na de laatste poging, dan blijft de thumbnail gewoon staan. Op
    /beheer telde hij eerder voor altijd bij "krijgt nog een grote versie", en de viewer bleef
    vragen. Nu staat hij apart (large_failed), en "Alles nalopen" probeert hem opnieuw."""
    headers, _ = admin
    raw = exif_tiff() + b"\0" * 1000 + photo((1600, 1200))
    node = await upload(client, headers, "DSC_0002.NEF", raw)
    assert await jobs.run_one(settings)  # eerste ronde: alleen 256
    real = jobs._kinds[previews.LARGE_TYPE]

    async def kapot(_settings, _payload):
        raise RuntimeError("kapot")

    monkeypatch.setitem(jobs._kinds, previews.LARGE_TYPE, dataclasses.replace(real, run=kapot))
    async with maker(settings)() as session:
        await session.execute(
            update(Job).where(Job.type == previews.LARGE_TYPE).values(max_attempts=1)
        )
        await session.commit()
    await jobs.run_pending(settings)
    async with maker(settings)() as session:
        job = await session.scalar(select(Job).where(Job.type == previews.LARGE_TYPE))
        assert job.status == "failed" and "kapot" in job.error
        blob = await session.get(Blob, sha_of(node))
        assert blob.preview == "ready" and blob.preview_meta["sizes"] == [256]
        assert blob.preview_meta["partial"] is True and blob.preview_meta["large_failed"] is True
    r = await client.get("/api/v1/admin/jobs", headers=headers)
    assert r.json()["photos"] == {"total": 1, "ready": 0, "partial": 0, "large_failed": 1,
                                  "waiting": 0, "failed": 0, "none": 0, "later": 0}  # fmt: skip
    detail = (await client.get(f"/api/v1/files/{node['id']}/preview", headers=headers)).json()
    assert detail["state"] == "ready" and detail["meta"]["large_failed"] is True
    assert (await client.get(detail["thumb"])).status_code == 200

    # Alles nalopen: weer in de rij, dus weer onderweg; nu lukt hij
    monkeypatch.setitem(jobs._kinds, previews.LARGE_TYPE, real)
    await previews.recheck(settings, {})
    assert await queued(settings, previews.LARGE_TYPE) == [sha_of(node)]
    r = await client.get("/api/v1/admin/jobs", headers=headers)
    assert (r.json()["photos"]["partial"], r.json()["photos"]["large_failed"]) == (1, 0)
    await jobs.run_pending(settings)
    async with maker(settings)() as session:
        blob = await session.get(Blob, sha_of(node))
        assert "partial" not in blob.preview_meta and "large_failed" not in blob.preview_meta
    r = await client.get("/api/v1/admin/jobs", headers=headers)
    assert r.json()["photos"]["ready"] == 1
    assert (r.json()["photos"]["partial"], r.json()["photos"]["large_failed"]) == (0, 0)


async def test_naar_voren_gaat_voor_een_lagere_rank(settings):
    async with maker(settings)() as session:
        for i in range(30):
            await jobs.enqueue(session, previews.TYPE, {"sha": f"{i:064x}"}, key=f"t:{i}")
        large = await jobs.enqueue(
            session, previews.LARGE_TYPE, {"sha": "f" * 64}, key=previews.large_key("f" * 64)
        )
        await session.commit()
        assert await jobs.to_front(session, [previews.large_key("f" * 64)]) == 1
        await session.commit()
    claimed = await jobs.claim(settings, lane=previews.LANE)
    assert claimed.id == large
    # Zonder naar voren: rank 0 eerst
    assert (await jobs.claim(settings, lane=previews.LANE)).type == previews.TYPE


async def test_naar_voren_laat_een_taak_die_later_moet_staan(settings):
    async with maker(settings)() as session:
        soon = await jobs.enqueue(session, previews.TYPE, {"sha": "a" * 64}, key="k:soon")
        wait = jobs.now() + timedelta(hours=1)
        await jobs.enqueue(session, previews.TYPE, {"sha": "b" * 64}, key="k:wait", run_after=wait)
        await session.commit()
        assert await jobs.to_front(session, ["k:soon", "k:wait", "k:onbekend"]) == 1
        await session.commit()
        job = await session.get(Job, soon)
        assert job.run_after.replace(tzinfo=UTC) < jobs.FRONT


async def test_naar_voren_schrijft_niet_bij_elke_keer_kijken(settings, monkeypatch):
    """Dezelfde foto's opnieuw tonen (de tijdlijn laadt opnieuw, POST /previews) schrijft niet
    steeds opnieuw: een taak die net naar voren ging, blijft staan (geen schrijfslot op SQLite
    bij elke GET /photos). Na FRONT_AGAIN mag hij weer, en een nieuwe taak gaat meteen."""
    start = jobs.now()
    monkeypatch.setattr(jobs, "now", lambda: start)
    async with maker(settings)() as session:
        await jobs.enqueue(session, previews.TYPE, {"sha": "a" * 64}, key="k:a")
        await session.commit()
        assert await jobs.to_front(session, ["k:a"]) == 1
        await session.commit()
        writes = []
        engine = get_engine(settings).sync_engine

        def record(conn, cursor, statement, *args):
            if statement.lstrip().upper().startswith("UPDATE"):
                writes.append(statement)

        event.listen(engine, "before_cursor_execute", record)
        try:
            later = start + timedelta(seconds=jobs.FRONT_AGAIN - 1)
            monkeypatch.setattr(jobs, "now", lambda: later)
            assert await jobs.to_front(session, ["k:a"]) == 0
            assert writes == []
            await jobs.enqueue(session, previews.TYPE, {"sha": "b" * 64}, key="k:b")
            await session.commit()
            assert await jobs.to_front(session, ["k:a", "k:b"]) == 1
            await session.commit()
            much_later = start + timedelta(seconds=jobs.FRONT_AGAIN + 1)
            monkeypatch.setattr(jobs, "now", lambda: much_later)
            assert await jobs.to_front(session, ["k:a"]) == 1
            await session.commit()
        finally:
            event.remove(engine, "before_cursor_execute", record)
        # De laatst bekeken eerst: a (opnieuw naar voren), dan b
        order = [(await jobs.claim(settings)).payload["sha"] for _ in range(2)]
        assert order == ["a" * 64, "b" * 64]


async def test_naar_voren_pas_na_het_antwoord(client, admin, settings, monkeypatch):
    """De tijdlijn, POST /previews en de viewer wachten niet op het naar voren zetten (dat
    schrijft, en wacht tijdens een import op de schrijflock van SQLite): eerst het antwoord,
    dan de rij. Mislukt het naar voren zetten, dan krijgt wie kijkt toch gewoon zijn foto's."""
    from app.main import app

    headers, _ = admin
    nodes = [await upload(client, headers, f"{i}.png", picture("PNG", (60, 40 + i)))
             for i in range(2)]  # fmt: skip
    order = []
    real = jobs.to_front

    async def front(session, keys):
        order.append("rij")
        return await real(session, keys)

    async def recording(scope, receive, send):
        async def sent(message):
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body"):
                order.append("antwoord")

        await app(scope, receive, sent)

    monkeypatch.setattr(jobs, "to_front", front)
    async with AsyncClient(transport=ASGITransport(app=recording), base_url="http://test") as c:
        for method, path, body in [
            ("GET", "/api/v1/photos", None),
            ("POST", "/api/v1/previews", {"node_ids": [nodes[0]["id"]]}),
            ("GET", f"/api/v1/files/{nodes[1]['id']}/preview", None),
        ]:
            order.clear()
            r = await c.request(method, path, headers=headers, json=body)
            assert r.status_code == 200, path
            assert order == ["antwoord", "rij"], path

        async def broken(session, keys):
            raise RuntimeError("database is locked")

        monkeypatch.setattr(jobs, "to_front", broken)
        r = await c.get("/api/v1/photos", headers=headers)
        assert r.status_code == 200 and len(r.json()["items"]) == 2
    # Naar voren ging het wel (bij de eerste GET /photos, daarna binnen FRONT_AGAIN niet weer)
    async with maker(settings)() as session:
        fronted = await session.scalar(
            select(func.count()).where(Job.status == "queued", Job.run_after < jobs.FRONT)
        )
    assert fronted == 2


async def test_video_wacht_niet_vijf_minuten_als_de_worker_er_weer_is(settings, monkeypatch):
    """Bij het starten liep een preview van een video soms al voordat de mediaworker luisterde:
    jobs.Defer, en dan pas na render.DEFER_DOWN (vijf minuten) weer (gemeten: drie video's
    bovenaan de tijdlijn bleven zo lang plaatshouder). Zodra de worker ok is, gaan ze meteen;
    wat om een andere reden later moet (geen video), blijft staan."""
    from app.media import health

    later = jobs.now() + timedelta(seconds=render.DEFER_DOWN)
    async with maker(settings)() as session:
        owner = await create_user(session, "worker@example.nl")
        video = await create_file(session, owner, name="v.mp4", data=b"v", mime="video/mp4")
        foto = await create_file(session, owner, name="f.png", data=b"f", mime="image/png")
        for node in (video, foto):
            await jobs.enqueue(
                session, previews.TYPE, {"sha": node.blob_id}, key=previews.key(node.blob_id),
                run_after=later,
            )  # fmt: skip
        await session.commit()
    answers = iter(["down", "ok", "ok"])
    pauses = []

    async def check(_app) -> str:
        return next(answers)

    async def pause(seconds: float) -> None:
        pauses.append(seconds)
        if len(pauses) > 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(health, "check", check)
    monkeypatch.setattr(health, "_pause", pause)
    with pytest.raises(asyncio.CancelledError):
        await health._loop(types.SimpleNamespace(state=types.SimpleNamespace()), settings)
    job = await jobs.claim(settings)
    assert job is not None and job.payload["sha"] == video.blob_id
    assert await jobs.claim(settings) is None  # de PNG wacht nog


async def test_preview_ready_zegt_of_de_datum_verschoof(client, admin, settings, monkeypatch):
    """dated: de opnamedatum uit de foto zet hem op een andere dag; de web-app leest dan de
    maanden opnieuw, anders werkt hij alleen de thumbnail bij."""
    from app import events

    hub = events.Hub()
    monkeypatch.setattr(events, "hub", hub)
    headers, tokens = admin
    exif = Image.Exif()
    exif.get_ifd(0x8769)[0x9003] = "2001:02:03 04:05:06"
    dated = await upload(client, headers, "oud.jpg", photo((300, 200), exif=exif))
    plain = await upload(client, headers, "nieuw.png", picture("PNG", (30, 20)))
    seen = []
    with hub.subscribe(uuid.UUID(tokens["user"]["id"])) as sub:
        await jobs.run_pending(settings)
        it = aiter(sub)
        for _ in range(2):
            seen.append(await anext(it))
    by_sha = {e["data"]["sha"]: e["data"] for e in seen}
    assert by_sha[sha_of(dated)].get("dated") is True
    assert "dated" not in by_sha[sha_of(plain)]
    async with maker(settings)() as session:
        node = await session.get(Node, uuid.UUID(dated["id"]))
        assert node.taken_day == photodate.day_of(datetime(2001, 2, 3))
