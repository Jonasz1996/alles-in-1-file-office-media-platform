"""Previews: per soort bestand (in het eigen proces en los), de taak met opruimen en
gelijktijdigheid, ondertekende adressen en de API."""

import asyncio
import io
import json
import shutil
import tarfile
import time
import uuid
import wave
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from PIL import Image, features
from sqlalchemy import event, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import events, jobs, previews, render
from app.db import get_engine
from app.models import Blob, Job, Node
from app.storage import BlobStore

needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")), reason="ffmpeg niet geïnstalleerd"
)


def picture(fmt: str, size=(64, 48), mode="RGB", color="red", **save) -> bytes:
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, fmt, **save)
    return buf.getvalue()


def jpeg_rotated(size=(4000, 3000)) -> bytes:
    """Liggend opgeslagen, met EXIF "90° draaien": rechtop is hij 3000 x 4000."""
    exif = Image.Exif()
    exif[0x0112] = 6
    return picture("JPEG", size, exif=exif)


def pdf_file(path: Path, pages=2) -> Path:
    import pypdfium2 as pdfium

    doc = pdfium.PdfDocument.new()
    for _ in range(pages):
        doc.new_page(595, 842)  # A4 in punten
    doc.save(str(path))
    doc.close()
    return path


def wav_with_cover(path: Path) -> Path:
    from mutagen.id3 import APIC, TIT2, TPE1
    from mutagen.wave import WAVE

    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 8000 * 2)  # 2 seconden stilte
    audio = WAVE(path)
    audio.add_tags()
    audio.tags.add(TIT2(encoding=3, text="Liedje"))
    audio.tags.add(TPE1(encoding=3, text="Jonas"))
    cover = picture("PNG", (600, 600), color="blue")
    audio.tags.add(APIC(encoding=3, mime="image/png", type=3, desc="hoes", data=cover))
    audio.save()
    return path


def epub_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip")
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
            '<rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
        )
        z.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="3.0">'
            '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:title>Het boek</dc:title>'
            '</metadata><manifest><item id="c" href="images/voor%20kant.png" '
            'media-type="image/png" properties="cover-image"/></manifest></package>',
        )
        z.writestr("OEBPS/images/voor kant.png", picture("PNG", (300, 450), color="green"))
    return buf.getvalue()


def zip_bytes(count: int) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i in range(count):
            z.writestr(f"map/{i}.txt", "x" * i)
    return buf.getvalue()


def tar_gz_bytes(count: int) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for i in range(count):
            info = tarfile.TarInfo(f"bestand-{i}.txt")
            info.size = 3
            t.addfile(info, io.BytesIO(b"abc"))
    return buf.getvalue()


def make_video(path: Path, size="640x360", seconds=2) -> Path:
    import subprocess

    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i"]
        + [f"testsrc=duration={seconds}:size={size}:rate=10", "-pix_fmt", "yuv420p", "-y"]
        + [str(path)],
        check=True,
    )
    return path


def run_render(tmp_path: Path, data: bytes | Path, name: str, mime=None, max_pixels=10**8):
    """render.render in dit proces; geeft (antwoord, afmetingen per gemaakte webp)."""
    source = data if isinstance(data, Path) else tmp_path / "bron"
    if not isinstance(data, Path):
        source.write_bytes(data)
    out, work = tmp_path / "out", tmp_path / "work"
    shutil.rmtree(out, ignore_errors=True)
    shutil.rmtree(work, ignore_errors=True)
    out.mkdir()
    work.mkdir()
    request = {
        "source": str(source),
        "out": str(out),
        "work": str(work),
        "mime": mime,
        "name": name,
        "max_pixels": max_pixels,
    }
    result = render.render(request)
    made = {}
    for f in out.iterdir():
        with Image.open(f) as img:
            assert img.format == "WEBP"
            made[int(f.stem)] = img.size
    assert sorted(made) == sorted(result["sizes"])
    return result, made


def test_foto_rechtop_en_niet_vergroot(tmp_path):
    result, made = run_render(tmp_path, jpeg_rotated(), "foto.jpg")
    assert result["state"] == "ready"
    assert result["meta"] == {"width": 3000, "height": 4000, "animated": False}
    assert made == {1024: (768, 1024), 256: (192, 256)}

    # Kleiner dan een thumbnail: alleen 256.webp, op de eigen grootte
    result, made = run_render(
        tmp_path, picture("PNG", (100, 50), "RGBA", (0, 0, 255, 128)), "a.png"
    )
    assert result["meta"] == {"width": 100, "height": 50, "animated": False}
    assert made == {256: (100, 50)}
    # Tussen 256 en 1024: de grote is het origineel
    _, made = run_render(tmp_path, picture("GIF", (500, 300), "P"), "a.gif")
    assert made == {1024: (500, 300), 256: (256, 154)}


def test_inhoud_telt_niet_de_naam(tmp_path):
    result, made = run_render(tmp_path, picture("PNG", (300, 300)), "notities.txt")
    assert result["state"] == "ready" and 256 in made
    result, _ = run_render(tmp_path, b"gewoon tekst\n", "foto.jpg")
    assert result["state"] == "ready" and result["meta"]["text"] == "gewoon tekst\n"


@pytest.mark.parametrize(
    ("fmt", "check"),
    [("WEBP", "webp"), ("BMP", None), ("TIFF", None), ("ICO", None), ("AVIF", "avif")],
)
def test_andere_afbeeldingen(tmp_path, fmt, check):
    if check and not features.check(check):
        pytest.skip(f"Pillow zonder {check}")
    result, made = run_render(tmp_path, picture(fmt, (128, 128)), f"a.{fmt.lower()}")
    assert result["state"] == "ready" and made == {256: (128, 128)}


def test_heic(tmp_path):
    pillow_heif = pytest.importorskip("pillow_heif")
    buf = io.BytesIO()
    try:
        pillow_heif.from_pillow(Image.new("RGB", (640, 480), "red")).save(buf, quality=50)
    except Exception as e:  # noqa: BLE001 - zonder encoder kan het testbestand niet gemaakt
        pytest.skip(f"Geen HEIC-encoder: {e}")
    result, made = run_render(tmp_path, buf.getvalue(), "IMG_0001.HEIC")
    assert result["meta"] == {"width": 640, "height": 480, "animated": False}
    assert made == {1024: (640, 480), 256: (256, 192)}  # niet groter dan 1024: geen 2048


def test_kapotte_en_te_grote_afbeeldingen_geen_preview(tmp_path):
    assert run_render(tmp_path, b"\xff\xd8\xff" + b"\x00" * 500, "kapot.jpg")[0]["state"] == "none"
    png = picture("PNG", (100, 100))
    assert run_render(tmp_path, png, "a.png", max_pixels=1000)[0]["state"] == "none"  # "bom"
    # Tussen 1x en 2x de grens geeft Pillow zelf alleen een waarschuwing: ook geen preview
    assert run_render(tmp_path, png, "b.png", max_pixels=6000)[0]["state"] == "none"
    assert run_render(tmp_path, png, "c.png", max_pixels=10_000)[0]["state"] == "ready"
    truncated = picture("PNG", (400, 400), color="white")[:200]
    assert run_render(tmp_path, truncated, "half.png")[0]["state"] == "none"


def test_pdf_eerste_pagina(tmp_path):
    result, made = run_render(tmp_path, pdf_file(tmp_path / "a.pdf", pages=3), "a.pdf")
    assert result["state"] == "ready" and result["meta"] == {"pages": 3}
    assert made[1024] == (724, 1024) and made[256] == (181, 256)
    result, _ = run_render(tmp_path, b"%PDF-1.7\nonzin", "kapot.pdf")
    assert result["state"] == "none"


def test_tekst_eerste_regels(tmp_path):
    result, _ = run_render(tmp_path, "".join(f"regel {i}\n" for i in range(100)).encode(), "a.md")
    assert result["meta"]["text"].count("\n") == 50 and result["meta"]["truncated"]
    # 4 KB, zonder een half teken aan het eind
    long = ("é" * 3000).encode()
    result, _ = run_render(tmp_path, long, "a.txt")
    text = result["meta"]["text"]
    assert text == "é" * 2048 and result["meta"]["truncated"]
    result, _ = run_render(tmp_path, b"kort", "a.txt")
    assert result["meta"] == {"text": "kort", "truncated": False, "encoding": "utf-8"}
    result, _ = run_render(tmp_path, b'{"a": 1}', "a.json", mime="application/json")
    assert result["meta"]["text"] == '{"a": 1}'


def test_archieven(tmp_path):
    result, _ = run_render(tmp_path, zip_bytes(300), "a.zip")
    meta = result["meta"]
    assert len(meta["entries"]) == 200 and meta["count"] == 300 and meta["truncated"]
    assert meta["entries"][5] == {"name": "map/5.txt", "size": 5}
    result, _ = run_render(tmp_path, tar_gz_bytes(250), "a.tar.gz")
    assert len(result["meta"]["entries"]) == 200 and result["meta"]["truncated"]
    assert result["meta"]["entries"][0] == {"name": "bestand-0.txt", "size": 3}
    result, _ = run_render(tmp_path, b"\x1f\x8b" + b"\x00" * 50, "kapot.tgz")
    assert result["state"] == "none"


def test_trage_tar_stopt_op_tijd(tmp_path, monkeypatch):
    monkeypatch.setattr(render, "LIST_SECONDS", 0.3)
    real = tarfile.TarFile.next

    def slow(self):
        time.sleep(0.02)
        return real(self)

    monkeypatch.setattr(tarfile.TarFile, "next", slow)
    start = time.monotonic()
    result, _ = run_render(tmp_path, tar_gz_bytes(100), "a.tgz")
    assert time.monotonic() - start < 2
    assert result["meta"]["truncated"] and 0 < len(result["meta"]["entries"]) < 100


def test_audio_met_hoes(tmp_path):
    result, made = run_render(tmp_path, wav_with_cover(tmp_path / "a.wav"), "a.wav")
    assert result["meta"] == {"duration": 2.0, "title": "Liedje", "artist": "Jonas"}
    assert made == {1024: (600, 600), 256: (256, 256)}


def test_epub_hoes(tmp_path):
    result, made = run_render(tmp_path, epub_bytes(), "boek.epub")
    assert result["meta"] == {"title": "Het boek"}
    assert made == {1024: (300, 450), 256: (171, 256)}


def test_geen_preview_voor_kapotte_office_svg_en_onbekend(tmp_path):
    docx = io.BytesIO()
    with zipfile.ZipFile(docx, "w") as z:
        z.writestr("[Content_Types].xml", "<Types/>")  # geen word/document.xml
    assert run_render(tmp_path, docx.getvalue(), "brief.docx")[0]["state"] == "none"
    svg = b'<svg xmlns="http://www.w3.org/2000/svg"></svg>'
    assert run_render(tmp_path, svg, "logo.svg")[0]["state"] == "none"
    assert run_render(tmp_path, svg, "logo", mime="image/svg+xml")[0]["state"] == "none"
    assert run_render(tmp_path, bytes(range(256)) * 4, "data.bin")[0]["state"] == "none"
    assert run_render(tmp_path, b"Rar!\x1a\x07\x00", "a.rar")[0]["state"] == "none"


@needs_ffmpeg
def test_video_beeld_en_duur(tmp_path):
    result, made = run_render(tmp_path, make_video(tmp_path / "film.mp4", "1920x1080"), "film.mp4")
    meta = result["meta"]
    assert {k: meta[k] for k in ("duration", "width", "height")} == {
        "duration": 2.0,
        "width": 1920,
        "height": 1080,
    }
    assert meta["video"]["codec"] in ("h264", "mpeg4") and meta["container"].startswith("mov")
    assert made == {1024: (1024, 576), 256: (256, 144)}
    result, made = run_render(tmp_path, make_video(tmp_path / "klein.webm", "160x120"), "a.webm")
    assert result["meta"]["width"] == 160 and made == {256: (160, 120)}


def test_video_zonder_ffmpeg_geen_preview(tmp_path, monkeypatch):
    monkeypatch.setattr(render.shutil, "which", lambda _name: None)
    head = b"\x00\x00\x00\x18ftypisom" + b"\x00" * 100
    assert run_render(tmp_path, head, "film.mp4")[0]["state"] == "none"


def test_soort_bepalen():
    assert render.kind_of("image/jpeg", b"", "x") == "image"
    assert render.kind_of("image/svg+xml", b"<svg", "x.svg") == ""
    assert render.kind_of("video/mp4", b"", "x") == "video"
    docx = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    assert render.kind_of(docx, b"", "x.docx") == "office"
    assert render.kind_of("application/msword", b"", "x.doc") == ""  # doc, xls, ppt: niet
    odt = b"PK\x03\x04" + b"\x00" * 26 + b"mimetypeapplication/vnd.oasis.opendocument.text"
    assert render.kind_of(None, odt, "x") == "office"
    assert render.kind_of("text/csv", b"", "x.csv") == "text"
    assert render.kind_of(None, b"BMW-onderhoud\n", "notities") == "text"  # geen BMP
    assert render.kind_of(None, b"II*\x00" + b"\x00" * 20, "IMG.CR2") == ""  # RAW
    assert render.container(b"\x00\x00\x00\x18ftypisom") == "mov"
    assert render.container(b"#EXTM3U\nfile:///etc/passwd") is None  # nooit een afspeellijst
    assert render._rotation({"side_data_list": [{"rotation": -90}]}) == 270


# --- de taak en de API ---------------------------------------------------------------


@pytest.fixture
def hub(monkeypatch):
    fresh = events.Hub()
    monkeypatch.setattr(events, "hub", fresh)
    return fresh


@pytest.fixture
def maker(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


async def root_id(client, headers):
    return (await client.get("/api/v1/files/root", headers=headers)).json()["folder"]["id"]


async def upload(client, headers, name, data: bytes, parent=None):
    parent = parent or await root_id(client, headers)
    r = await client.put(
        f"/api/v1/files/{parent}/upload", params={"name": name}, content=data, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()


async def preview_of(client, headers, *node_ids):
    r = await client.post("/api/v1/previews", json={"node_ids": list(node_ids)}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def login_as(client, admin_headers, email):
    new = {"email": email, "display_name": "Ander", "password": "lang-genoeg-1"}
    await client.post("/api/v1/users", json=new, headers=admin_headers)
    r = await client.post("/api/v1/auth/login", json={"email": email, "password": new["password"]})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def sha_of(node) -> str:
    return node["etag"].strip('"')


@pytest.mark.anyio
async def test_upload_geeft_thumbnail(client, admin, settings, hub):
    headers, tokens = admin
    node = await upload(client, headers, "foto.jpg", jpeg_rotated((1600, 1200)))
    assert (await preview_of(client, headers, node["id"]))[node["id"]] == {
        "state": "pending",
        "thumb": None,
        "large": None,
        "meta": {},
    }
    with hub.subscribe(uuid.UUID(tokens["user"]["id"])) as sub:
        assert await jobs.run_pending(settings) == 1
        ready = await asyncio.wait_for(anext(aiter(sub)), 1)
    assert ready == {
        "type": "preview.ready",
        "data": {"sha": sha_of(node), "node_ids": [node["id"]]},
    }

    info = (await preview_of(client, headers, node["id"]))[node["id"]]
    assert info["state"] == "ready"
    assert info["meta"] == {"width": 1200, "height": 1600, "animated": False, "sizes": [256, 1024]}
    assert "/256.webp?" in info["thumb"] and "/1024.webp?" in info["large"]
    r = await client.get(info["thumb"])  # zonder Authorization, zoals een <img>
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/webp"
    assert r.headers["cache-control"] == "private, max-age=86400, immutable"
    with Image.open(io.BytesIO(r.content)) as img:
        assert img.size == (192, 256)
    assert list((previews.root(BlobStore(settings.blob_dir)) / ".tmp").iterdir()) == []


@pytest.mark.anyio
async def test_ondertekend_adres(client, admin, settings):
    headers, _ = admin
    node = await upload(client, headers, "a.png", picture("PNG", (300, 300)))
    await jobs.run_pending(settings)
    thumb = (await preview_of(client, headers, node["id"]))[node["id"]]["thumb"]
    path, query = thumb.split("?")
    params = dict(p.split("=", 1) for p in query.split("&"))
    sha = sha_of(node)
    assert (await client.get(thumb)).status_code == 200
    bad = {
        "andere handtekening": f"{path}?exp={params['exp']}&sig={'A' * 43}",
        "later verlopen": f"{path}?exp={int(params['exp']) + 3600}&sig={params['sig']}",
        "andere grootte": f"/api/v1/previews/{sha}/1024.webp?{query}",
        "geen handtekening": path,
        "geen ascii": f"{path}?exp={params['exp']}&sig=é",
    }
    for reason, url in bad.items():
        assert (await client.get(url)).status_code == 403, reason
    secret = previews.url_key(settings)
    past = int(time.time()) - 10
    expired = previews.url(secret, sha, 256, past)
    assert (await client.get(expired)).status_code == 403
    assert (await client.get(f"/api/v1/previews/{sha}/512.webp?{query}")).status_code == 404
    missing = previews.url(secret, "0" * 64, 256, previews.expiry())
    assert (await client.get(missing)).status_code == 404
    assert (await client.get(f"/api/v1/previews/..%2F..%2Fx/256.webp?{query}")).status_code == 404


def test_verloop_afgerond_op_zes_uur():
    period = 6 * 3600
    base = 1_000_000 * period
    assert previews.expiry(base) == base + period  # precies op de grens
    assert previews.expiry(base + 1) == base + 2 * period
    assert previews.expiry(base + period - 1) == base + 2 * period
    # Binnen een blok van 6 uur hetzelfde adres: de browser kan het cachen
    assert len({previews.expiry(base + s) for s in range(1, period, 600)}) == 1


@pytest.mark.anyio
async def test_alleen_eigen_bestanden(client, admin, settings):
    headers, _ = admin
    other = await login_as(client, headers, "ander@example.nl")
    mine = await upload(client, headers, "a.png", picture("PNG"))
    folder = (await client.get("/api/v1/files/root", headers=headers)).json()["folder"]["id"]
    theirs = await upload(client, other, "b.png", picture("PNG", color="blue"))
    result = await preview_of(client, headers, mine["id"], theirs["id"], folder, str(uuid.uuid4()))
    assert list(result) == [mine["id"]]
    r = await client.post(
        "/api/v1/previews",
        json={"node_ids": [str(uuid.uuid4()) for _ in range(501)]},
        headers=headers,
    )
    assert r.status_code == 422
    assert (await client.post("/api/v1/previews", json={"node_ids": []})).status_code == 401


@pytest.mark.anyio
async def test_veel_bestanden_in_een_paar_query_s(client, admin, settings, maker):
    headers, tokens = admin
    node = await upload(client, headers, "a.png", picture("PNG"))
    async with maker() as session:
        copies = [
            Node(
                parent_id=uuid.UUID(tokens["user"]["root_id"]),
                owner_id=uuid.UUID(tokens["user"]["id"]),
                name=f"kopie {i}.png",
                kind="file",
                size=10,
                blob_id=sha_of(node),
            )
            for i in range(499)
        ]
        session.add_all(copies)
        await session.commit()
    ids = [str(n.id) for n in copies]
    statements = []
    engine = get_engine(settings).sync_engine

    def count(_conn, _cursor, statement, *_args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        result = await preview_of(client, headers, node["id"], *ids)
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert len(result) == 500
    assert len(statements) <= 8, statements  # geen query per bestand


@pytest.mark.anyio
async def test_zelfde_inhoud_een_preview_voor_iedereen(client, admin, settings, maker, hub):
    headers, tokens = admin
    other = await login_as(client, headers, "ander@example.nl")
    data = picture("PNG", (400, 400))
    a = await upload(client, headers, "a.png", data)
    b = await upload(client, headers, "b.png", data)
    c = await upload(client, other, "c.png", data)
    async with maker() as session:
        assert await session.scalar(select(func.count()).select_from(Job)) == 1
    other_id = (await client.get("/api/v1/auth/me", headers=other)).json()["id"]
    with (
        hub.subscribe(uuid.UUID(tokens["user"]["id"])) as mine,
        hub.subscribe(uuid.UUID(other_id)) as theirs,
    ):
        await jobs.run_pending(settings)
        got_mine = await asyncio.wait_for(anext(aiter(mine)), 1)
        got_theirs = await asyncio.wait_for(anext(aiter(theirs)), 1)
    # Ieder hoort alleen over de eigen bestanden
    assert sorted(got_mine["data"]["node_ids"]) == sorted([a["id"], b["id"]])
    assert got_theirs["data"]["node_ids"] == [c["id"]]
    previews_dir = previews.root(BlobStore(settings.blob_dir))
    assert [p.name for p in previews_dir.glob("*/*")] == [sha_of(a)]


@pytest.mark.anyio
async def test_opruimen_neemt_de_preview_mee(client, admin, settings):
    headers, _ = admin
    node = await upload(client, headers, "a.png", picture("PNG", (300, 300)))
    await jobs.run_pending(settings)
    directory = previews.directory(BlobStore(settings.blob_dir), sha_of(node))
    assert (directory / "256.webp").exists()
    await client.delete(f"/api/v1/files/{node['id']}", headers=headers)
    assert (await client.delete(f"/api/v1/trash/{node['id']}", headers=headers)).status_code == 204
    assert not directory.exists()


@pytest.mark.anyio
async def test_opgeruimd_tijdens_het_maken_laat_niets_achter(client, admin, settings, monkeypatch):
    """De blob verdwijnt terwijl de preview gemaakt wordt: dan ook geen previewmap (die zou
    anders voor altijd blijven staan)."""
    headers, _ = admin
    node = await upload(client, headers, "a.png", picture("PNG", (300, 300)))
    real = previews.render

    async def render_and_purge(settings_, request, cwd):
        result = await real(settings_, request, cwd)
        await client.delete(f"/api/v1/files/{node['id']}", headers=headers)
        assert (
            await client.delete(f"/api/v1/trash/{node['id']}", headers=headers)
        ).status_code == 204
        return result

    monkeypatch.setattr(previews, "render", render_and_purge)
    assert await jobs.run_pending(settings) == 1
    blobs = BlobStore(settings.blob_dir)
    assert not previews.directory(blobs, sha_of(node)).exists()
    assert list((previews.root(blobs) / ".tmp").iterdir()) == []
    maker = async_sessionmaker(get_engine(settings))
    async with maker() as session:
        assert (await session.scalars(select(Job.status))).all() == ["done"]


@pytest.mark.anyio
async def test_bron_weg_voor_het_maken_is_geen_fout(client, admin, settings, maker):
    headers, _ = admin
    node = await upload(client, headers, "a.png", picture("PNG"))
    await client.delete(f"/api/v1/files/{node['id']}", headers=headers)
    await client.delete(f"/api/v1/trash/{node['id']}", headers=headers)
    assert await jobs.run_pending(settings) == 1
    async with maker() as session:
        assert (await session.scalars(select(Job.status))).all() == ["done"]


@pytest.mark.anyio
async def test_crash_van_het_voorbeeldproces(client, admin, settings, maker, monkeypatch, hub):
    """Een bestand dat de decoder laat crashen raakt de server niet; na 3 pogingen failed."""
    headers, tokens = admin
    node = await upload(client, headers, "boos.png", picture("PNG"))
    original = previews.CHILD
    monkeypatch.setattr(
        previews, "CHILD", "import os, signal; os.kill(os.getpid(), signal.SIGSEGV)"
    )
    with hub.subscribe(uuid.UUID(tokens["user"]["id"])) as sub:
        # Drie pogingen, met de wachttijd ertussen (de klok een dag vooruit)
        assert await jobs.run_pending(settings, datetime.now(UTC) + timedelta(days=1)) == 3
        ready = await asyncio.wait_for(anext(aiter(sub)), 1)
    assert ready["type"] == "preview.ready"
    async with maker() as session:
        job = (await session.scalars(select(Job))).one()
        assert job.status == "failed" and job.attempts == 3
        assert job.error == "Het voorbeeldproces stopte onverwacht (signaal 11)"
    info = (await preview_of(client, headers, node["id"]))[node["id"]]
    assert info["state"] == "failed" and info["thumb"] is None

    # Opnieuw vanaf /beheer, nu zonder crash
    monkeypatch.setattr(previews, "CHILD", original)
    r = await client.post(f"/api/v1/admin/jobs/{job.id}/retry", headers=headers)
    assert r.status_code == 204
    await jobs.run_pending(settings)
    assert (await preview_of(client, headers, node["id"]))[node["id"]]["state"] == "ready"


def process_gone(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except FileNotFoundError:
        return True
    return state in ("Z", "X")  # dood (een zombie wacht alleen nog op opruimen)


@pytest.mark.anyio
async def test_te_lang_stopt_ook_ffmpeg(client, admin, settings, maker, monkeypatch, tmp_path):
    """Na de tijdslimiet gaat het voorbeeldproces dood, en wat het zelf startte ook."""
    headers, _ = admin
    await upload(client, headers, "traag.png", picture("PNG"))
    pids = tmp_path / "pids"
    child = (
        "import os, subprocess, time; p = subprocess.Popen(['sleep', '60']); "
        f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{p.pid}}'); time.sleep(60)"
    )
    monkeypatch.setattr(previews, "CHILD", child)
    settings.job_timeout = 1
    start = time.monotonic()
    await jobs.run_pending(settings)
    assert time.monotonic() - start < 5
    async with maker() as session:
        job = (await session.scalars(select(Job))).one()
    assert job.error == "Duurde langer dan 1 seconden" and job.status == "queued"
    # De werkmap gaat ook na de tijdslimiet weg (en niet pas bij de volgende start)
    tmp = previews.root(BlobStore(settings.blob_dir)) / ".tmp"
    assert list(tmp.iterdir()) == []
    for pid in map(int, pids.read_text().split()):
        for _ in range(50):
            if process_gone(pid):
                break
            await asyncio.sleep(0.05)
        assert process_gone(pid), pid


@pytest.mark.anyio
async def test_geen_geheimen_naar_het_voorbeeldproces(settings, monkeypatch, tmp_path):
    monkeypatch.setenv("PLATFORM_JWT_SECRET", "zeer-geheim")
    seen = tmp_path / "env.json"
    monkeypatch.setattr(
        previews,
        "CHILD",
        f"import json, os; open({str(seen)!r}, 'w').write(json.dumps(dict(os.environ))); "
        "print(json.dumps({'state': 'none', 'meta': {}, 'sizes': []}))",
    )
    result = await previews.render(settings, {}, tmp_path)
    assert result["state"] == "none"
    env = json.loads(seen.read_text())
    assert not any(k.startswith("PLATFORM_") for k in env)
    assert "zeer-geheim" not in seen.read_text()


@pytest.mark.anyio
async def test_preview_in_het_eigen_proces(client, admin, settings):
    """Het echte voorbeeldproces, met de geheugengrens, voor een paar soorten."""
    headers, _ = admin
    nodes = [
        await upload(client, headers, "foto.jpg", jpeg_rotated()),
        await upload(
            client, headers, "doc.pdf", pdf_file(Path(settings.data_dir) / "a.pdf").read_bytes()
        ),
        await upload(
            client,
            headers,
            "lied.wav",
            wav_with_cover(Path(settings.data_dir) / "a.wav").read_bytes(),
        ),
        await upload(client, headers, "notities.txt", b"hallo\nwereld\n"),
        await upload(client, headers, "map.zip", zip_bytes(3)),
        await upload(
            client, headers, "brief.docx", b"PK\x03\x04" + b"\x00" * 26 + b"[Content_Types].xml"
        ),
    ]
    assert await jobs.run_pending(settings) == len(nodes)
    info = await preview_of(client, headers, *(n["id"] for n in nodes))
    foto, doc, lied, tekst, archief, brief = (info[n["id"]] for n in nodes)
    assert foto["meta"]["width"] == 3000 and foto["thumb"] and foto["large"]
    assert doc["meta"]["pages"] == 2 and doc["thumb"]
    assert lied["thumb"] and tekst["state"] == "ready" and archief["state"] == "ready"
    assert tekst["thumb"] is None and tekst["large"] is None
    # De lijst is licht: geen tekst, archiefinhoud of tags; die geeft de viewer
    for light in info.values():
        assert set(light["meta"]) <= set(previews.LIGHT), light
    detail = {}
    for n in nodes:
        r = await client.get(f"/api/v1/files/{n['id']}/preview", headers=headers)
        assert r.status_code == 200, r.text
        detail[n["id"]] = r.json()
    lied, tekst, archief = (detail[n["id"]] for n in nodes[2:5])
    assert lied["meta"]["title"] == "Liedje"
    assert tekst["meta"]["text"] == "hallo\nwereld\n" and tekst["meta"]["encoding"] == "utf-8"
    assert [e["name"] for e in archief["meta"]["entries"]] == [
        "map/0.txt",
        "map/1.txt",
        "map/2.txt",
    ]
    assert brief["state"] == "none"


@needs_ffmpeg
@pytest.mark.anyio
async def test_video_in_het_eigen_proces(client, admin, settings):
    headers, _ = admin
    film = make_video(Path(settings.data_dir) / "film.mp4").read_bytes()
    node = await upload(client, headers, "film.mp4", film)
    await jobs.run_pending(settings)
    info = (await preview_of(client, headers, node["id"]))[node["id"]]
    assert info["meta"] == {"duration": 2.0, "width": 640, "height": 360, "sizes": [256, 1024]}


@pytest.mark.anyio
async def test_bestaande_inhoud_krijgt_bij_het_starten_een_preview(client, admin, settings, maker):
    headers, _ = admin
    node = await upload(client, headers, "oud.png", picture("PNG", (300, 300)))
    async with maker() as session:
        # Alsof het van vóór deze versie is: geen preview en geen taak
        await session.execute(update(Blob).values(preview=None))
        await session.execute(Job.__table__.delete())
        await session.commit()
    stray = previews.root(BlobStore(settings.blob_dir)) / ".tmp" / "achtergebleven"
    stray.mkdir(parents=True)
    assert await previews.enqueue_missing(settings) == 1
    assert await previews.enqueue_missing(settings) == 0  # niet dubbel
    assert not stray.exists()
    assert (await preview_of(client, headers, node["id"]))[node["id"]]["state"] == "pending"
    assert await jobs.run_pending(settings) == 1
    assert (await preview_of(client, headers, node["id"]))[node["id"]]["state"] == "ready"
    # Een dubbele taak voor iets dat al klaar is, doet niets
    async with maker() as session:
        await jobs.enqueue(session, previews.TYPE, {"sha": sha_of(node)})
        await session.commit()
    assert await jobs.run_pending(settings) == 1


def test_toegangslog_zonder_handtekening_of_token(tmp_path):
    import logging

    from app import serve
    from app.config import Settings

    serve._config(Settings(data_dir=tmp_path), 0)  # uvicorn zet hier zijn logging op
    access = logging.getLogger("uvicorn.access")
    assert access.filters
    path = f"/api/v1/previews/{'a' * 64}/256.webp?exp=1&sig=GEHEIM"
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:5", "GET", path, "1.1", 200),
        None,
    )
    assert all(f.filter(record) for f in access.filters)
    assert "GEHEIM" not in record.getMessage() and "sig=***" in record.getMessage()
    record.args = ("1.2.3.4:5", "GET", "/api/v1/download/x?token=GEHEIM&inline=true", "1.1", 200)
    for f in access.filters:
        f.filter(record)
    assert record.getMessage().endswith('/api/v1/download/x?token=***&inline=true HTTP/1.1" 200')
