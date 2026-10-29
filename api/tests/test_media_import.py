"""De import uit /data/import (Keuze 2, M2): app/media/importer.py.

Kopiëren; verplaatsen binnen één bestandssysteem (adopt, geen extra ruimte); EXDEV geeft kopiëren
en daarna de bron weg; ontdubbelen; quotum vol halverwege; een symlink en een fifo overgeslagen;
`../` buiten de importmap geweigerd; een bronmap die een symlink naar de datamap is, geeft
media_import_path; een bestand dat tijdens het tellen groeit, of jonger is dan 60 s, komt in
"overgeslagen" (in gebruik); een harde link wordt bij verplaatsen overgeslagen; een bestand dat de
app niet kan lezen komt in "overgeslagen"; nog een keer draaien doet niets.
"""

from __future__ import annotations

import contextlib
import errno
import os
import time
import uuid
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from app import jobs
from app.media import importer
from app.models import Blob, Job, Node, User, Version
from tests.test_files import mkdir, root
from tests.test_media_contract import media_app  # noqa: F401
from tests.test_media_sessions import maker
from tests.test_shares import person

pytestmark = pytest.mark.anyio

OLD = 3600  # zoveel seconden oud: ruim boven MIN_AGE_S


@pytest.fixture
async def world(media_app, client, admin):  # noqa: F811
    settings, _ = media_app
    headers, tokens = admin
    top = (await root(client, headers))["folder"]["id"]
    target = await mkdir(client, headers, top, "Films")
    src = importer.import_root(settings)
    src.mkdir()
    return {
        "settings": settings,
        "headers": headers,
        "owner": uuid.UUID(tokens["user"]["id"]),
        "target": uuid.UUID(target),
        "src": src,
    }


def put(base: Path, rel: str, data: bytes, age: float = OLD) -> Path:
    path = base / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    when = time.time() - age
    os.utime(path, (when, when))
    return path


async def run_import(w, source: str = "Bron", move: bool = False, target=None) -> dict:
    job_id = await importer.start(source, target or w["target"], w["owner"], move=move)
    assert jobs.lane_of(importer.JOB) == "import"
    await jobs.run_pending(w["settings"], lane="import")
    async with maker(w["settings"])() as session:
        job = await session.get(Job, job_id)
        assert job.status == "done", job.error
        return job.payload


async def tree(settings, parent: uuid.UUID, prefix: str = "") -> dict[str, Node]:
    """Alles onder deze map, op pad."""
    out: dict[str, Node] = {}
    async with maker(settings)() as session:
        rows = (
            await session.scalars(
                select(Node).where(Node.parent_id == parent, Node.deleted_at.is_(None))
            )
        ).all()
    for node in rows:
        path = f"{prefix}{node.name}"
        out[path] = node
        if node.kind == "dir":
            out.update(await tree(settings, node.id, path + "/"))
    return out


def skipped(payload: dict) -> dict[str, str]:
    return {i["path"]: i["reason"] for i in payload["skipped_items"]}


async def test_kopieren_laat_de_bron_staan(world):
    w = world
    put(w["src"], "Bron/Matrix (1999)/Matrix (1999).mkv", b"film" * 100)
    put(w["src"], "Bron/los.mkv", b"los" * 50)
    payload = await run_import(w)
    assert (payload["state"], payload["done"], payload["total"], payload["skipped"]) == (
        "done", 2, 2, 0,
    )  # fmt: skip
    assert payload["bytes"] == 400 + 150
    found = await tree(w["settings"], w["target"])
    assert set(found) == {"Matrix (1999)", "Matrix (1999)/Matrix (1999).mkv", "los.mkv"}
    assert found["Matrix (1999)"].kind == "dir"
    film = found["Matrix (1999)/Matrix (1999).mkv"]
    assert film.owner_id == w["owner"] and film.size == 400
    blob = w["settings"].blob_dir / film.blob_id[:2] / film.blob_id[2:4] / film.blob_id
    assert blob.read_bytes() == b"film" * 100
    assert (w["src"] / "Bron/los.mkv").exists()  # de bron blijft
    assert not list((w["settings"].blob_dir / ".tmp").glob("import-*"))


async def test_verplaatsen_kost_geen_extra_ruimte(world):
    """Klaar als: een import van 100 bestanden, verplaatst, kost geen extra ruimte: elke blob is
    hetzelfde bestand (st_ino) als de bron was."""
    w = world
    inodes = {}
    for i in range(100):
        path = put(w["src"], f"Bron/Map {i % 7}/f{i:03d}.mkv", f"inhoud {i}".encode() * 40)
        inodes[f"Map {i % 7}/f{i:03d}.mkv"] = (path.stat().st_ino, path.stat().st_dev)
    payload = await run_import(w, move=True)
    assert (payload["done"], payload["skipped"]) == (100, 0)
    found = await tree(w["settings"], w["target"])
    for rel, (ino, dev) in inodes.items():
        node = found[rel]
        blob = w["settings"].blob_dir / node.blob_id[:2] / node.blob_id[2:4] / node.blob_id
        st = blob.stat()
        assert (st.st_ino, st.st_dev, st.st_nlink) == (ino, dev, 1), rel
    assert not [p for p in (w["src"] / "Bron").rglob("*") if p.is_file()]  # de bron is leeg


async def test_exdev_kopieert_en_haalt_de_bron_weg(world, monkeypatch):
    w = world
    put(w["src"], "Bron/a.mkv", b"a" * 3000)
    original = (w["src"] / "Bron/a.mkv").stat().st_ino

    def exdev(*args):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(importer, "_rename", exdev)
    payload = await run_import(w, move=True)
    assert (payload["done"], payload["skipped"]) == (1, 0)
    assert not (w["src"] / "Bron/a.mkv").exists()
    node = (await tree(w["settings"], w["target"]))["a.mkv"]
    blob = w["settings"].blob_dir / node.blob_id[:2] / node.blob_id[2:4] / node.blob_id
    assert blob.read_bytes() == b"a" * 3000 and blob.stat().st_ino != original


async def test_ontdubbelen(world, client):
    w = world
    same = b"dezelfde inhoud" * 200
    put(w["src"], "Bron/een.mkv", same)
    put(w["src"], "Bron/sub/twee.mkv", same)
    # En inhoud die al in de opslag stond (een upload)
    put(w["src"], "Bron/drie.mkv", b"al geupload" * 10)
    r = await client.put(
        f"/api/v1/files/{w['target']}/upload",
        params={"name": "eerder.bin"},
        content=b"al geupload" * 10,
        headers=w["headers"],
    )
    assert r.status_code == 201
    payload = await run_import(w)
    assert payload["done"] == 3
    async with maker(w["settings"])() as session:
        refs = dict((await session.execute(select(Blob.sha256, Blob.ref_count))).all())
    found = await tree(w["settings"], w["target"])
    assert found["een.mkv"].blob_id == found["sub/twee.mkv"].blob_id
    assert refs[found["een.mkv"].blob_id] == 2
    assert found["drie.mkv"].blob_id == found["eerder.bin"].blob_id
    assert refs[found["drie.mkv"].blob_id] == 2
    assert len(refs) == 2


async def test_quotum_vol_halverwege(world, client):
    w = world
    bas, bas_id = await person(client, w["headers"], "bas@example.nl", quota_bytes=2500)
    top = (await root(client, bas))["folder"]["id"]
    target = uuid.UUID(await mkdir(client, bas, top, "Import"))
    for i in range(5):
        put(w["src"], f"Bron/f{i}.mkv", bytes([i]) * 1000)
    job_id = await importer.start("Bron", target, uuid.UUID(bas_id), move=True)
    await jobs.run_pending(w["settings"], lane="import")
    async with maker(w["settings"])() as session:
        job = await session.get(Job, job_id)
    payload = job.payload
    assert (job.status, payload["state"], payload["reason"], payload["done"]) == (
        "done", "stopped", "no_room", 2,
    )  # fmt: skip
    found = await tree(w["settings"], target)
    assert set(found) == {"f0.mkv", "f1.mkv"}
    # Wat niet binnenkwam, staat nog in de bron (ook het bestand dat net niet paste)
    left = sorted(p.name for p in (w["src"] / "Bron").iterdir())
    assert left == ["f2.mkv", "f3.mkv", "f4.mkv"]
    assert not list((w["settings"].blob_dir / ".tmp").glob("import-*"))
    view = importer.view(job)
    assert (view.state, view.done, view.total) == ("stopped", 2, 5)


async def test_symlink_en_fifo_worden_overgeslagen(world, tmp_path):
    w = world
    outside = tmp_path / "geheim.txt"
    outside.write_text("geheim")
    put(w["src"], "Bron/goed.mkv", b"goed" * 10)
    (w["src"] / "Bron/link.mkv").symlink_to(outside)
    (w["src"] / "Bron/map-link").symlink_to(tmp_path)
    os.mkfifo(w["src"] / "Bron/pijp")
    payload = await run_import(w)
    assert payload["done"] == 1
    assert skipped(payload) == {
        "link.mkv": importer.SYMLINK,
        "map-link": importer.SYMLINK,
        "pijp": importer.SPECIAL,
    }
    assert set(await tree(w["settings"], w["target"])) == {"goed.mkv"}


async def test_buiten_de_importmap_wordt_geweigerd(world):
    w = world
    (w["src"] / "Bron").mkdir()
    for source in ("../blobs", "Bron/../../blobs", "/etc", "/data/import/../blobs", ".", ""):
        with pytest.raises(HTTPException) as caught:
            await importer.start(source, w["target"], w["owner"])
        assert caught.value.status_code == 422, source
        assert caught.value.detail["code"] == "media_import_path"
    # Absoluut onder /data/import (de container) of onder de echte importmap mag
    assert importer.parts_of(w["settings"], "/data/import/Bron") == ["Bron"]
    assert importer.parts_of(w["settings"], f"{w['src']}/Bron/") == ["Bron"]


async def test_een_bronmap_die_een_symlink_is_geeft_media_import_path(world):
    w = world
    (w["src"] / "data").symlink_to(w["settings"].data_dir)
    (w["src"] / "Echt").mkdir()
    (w["src"] / "Echt" / "diep").symlink_to(w["settings"].data_dir)
    for source in ("data", "/data/import/data", "Echt/diep", "data/blobs"):
        with pytest.raises(HTTPException) as caught:
            await importer.start(source, w["target"], w["owner"])
        assert caught.value.detail["code"] == "media_import_path", source
    assert await importer.sources() == ["Echt"]


async def test_wat_groeit_of_jong_is_is_in_gebruik(world, monkeypatch):
    w = world
    put(w["src"], "Bron/groeit.mkv", b"x" * 5000)
    put(w["src"], "Bron/jong.mkv", b"y" * 10, age=5)
    put(w["src"], "Bron/rustig.mkv", b"z" * 10)
    real = importer._copy

    def growing(fd, tmp_dir):
        out = real(fd, tmp_dir)
        if os.fstat(fd).st_size == 5000:  # tijdens het tellen schrijft iemand erbij
            with open(w["src"] / "Bron/groeit.mkv", "ab") as f:
                f.write(b"meer")
        return out

    monkeypatch.setattr(importer, "_copy", growing)
    payload = await run_import(w)
    assert skipped(payload) == {"groeit.mkv": importer.IN_USE, "jong.mkv": importer.IN_USE}
    assert set(await tree(w["settings"], w["target"])) == {"rustig.mkv"}
    assert not list((w["settings"].blob_dir / ".tmp").glob("import-*"))


async def test_groeien_bij_verplaatsen_laat_het_bestand_staan(world, monkeypatch):
    w = world
    put(w["src"], "Bron/groeit.mkv", b"x" * 5000)
    real = importer._hash

    def growing(fd):
        out = real(fd)
        with open(w["src"] / "Bron/groeit.mkv", "ab") as f:
            f.write(b"meer")
        return out

    monkeypatch.setattr(importer, "_hash", growing)
    payload = await run_import(w, move=True)
    assert skipped(payload) == {"groeit.mkv": importer.IN_USE}
    assert (w["src"] / "Bron/groeit.mkv").stat().st_size == 5004
    assert await tree(w["settings"], w["target"]) == {}


async def test_een_harde_link_bij_verplaatsen_overgeslagen(world):
    w = world
    put(w["src"], "Bron/a.mkv", b"a" * 100)
    os.link(w["src"] / "Bron/a.mkv", w["src"] / "Bron/b.mkv")
    payload = await run_import(w, move=True)
    assert skipped(payload) == {"a.mkv": importer.HARD_LINK, "b.mkv": importer.HARD_LINK}
    assert (w["src"] / "Bron/a.mkv").exists() and (w["src"] / "Bron/b.mkv").exists()
    # Kopiëren mag wel
    payload = await run_import(w)
    assert payload["done"] == 2 and payload["skipped"] == 0


async def test_onleesbaar_wordt_overgeslagen(world, monkeypatch):
    """Een bestand van een andere uid dat de app niet mag lezen. Als root (de sandbox) negeert
    het systeem de rechten: dan geeft de open zelf EACCES; in CI (geen root) de echte chmod."""
    w = world
    put(w["src"], "Bron/goed.mkv", b"g" * 10)
    secret = put(w["src"], "Bron/van-een-ander.mkv", b"s" * 10)
    if os.geteuid() == 0:
        real = importer._open_file

        def denied(name, dir_fd):
            if name == "van-een-ander.mkv":
                raise PermissionError(errno.EACCES, "Permission denied")
            return real(name, dir_fd)

        monkeypatch.setattr(importer, "_open_file", denied)
    else:
        secret.chmod(0)
    try:
        payload = await run_import(w)
    finally:
        secret.chmod(0o644)
    assert skipped(payload) == {"van-een-ander.mkv": importer.UNREADABLE}
    assert set(await tree(w["settings"], w["target"])) == {"goed.mkv"}


async def test_nog_een_keer_draaien_doet_niets(world):
    w = world
    put(w["src"], "Bron/a.mkv", b"a" * 100)
    put(w["src"], "Bron/sub/b.mkv", b"b" * 100)
    await run_import(w)
    before = {k: (n.id, n.blob_id) for k, n in (await tree(w["settings"], w["target"])).items()}
    payload = await run_import(w)
    assert (payload["done"], payload["same"], payload["skipped"]) == (2, 2, 0)
    after = {k: (n.id, n.blob_id) for k, n in (await tree(w["settings"], w["target"])).items()}
    assert after == before
    async with maker(w["settings"])() as session:
        assert await session.scalar(select(func.count()).select_from(Version)) == 0
    # Een andere inhoud onder dezelfde naam: een nieuwe versie, zoals bij uploaden
    put(w["src"], "Bron/a.mkv", b"anders" * 10)
    payload = await run_import(w)
    assert (payload["done"], payload["same"]) == (2, 1)
    async with maker(w["settings"])() as session:
        assert await session.scalar(select(func.count()).select_from(Version)) == 1


async def test_een_map_met_een_bestandsnaam_wordt_overgeslagen(world, client):
    w = world
    r = await client.put(
        f"/api/v1/files/{w['target']}/upload",
        params={"name": "Serie"},
        content=b"een bestand",
        headers=w["headers"],
    )
    assert r.status_code == 201
    put(w["src"], "Bron/Serie/s01e01.mkv", b"1" * 10)
    payload = await run_import(w)
    assert skipped(payload) == {"Serie": importer.NAME_TAKEN}


async def test_een_tweede_import_is_bezig(world):
    w = world
    (w["src"] / "Bron").mkdir()
    await importer.start("Bron", w["target"], w["owner"])
    with pytest.raises(HTTPException) as caught:
        await importer.start("Bron", w["target"], w["owner"])
    assert (caught.value.status_code, caught.value.detail["code"]) == (409, "media_import_busy")


async def test_doelmap_en_eigenaar(world, client):
    w = world
    (w["src"] / "Bron").mkdir()
    with pytest.raises(HTTPException) as caught:
        await importer.start("Bron", uuid.uuid4(), w["owner"])
    assert caught.value.status_code == 404
    with pytest.raises(HTTPException) as caught:
        await importer.start("Bron", w["target"], uuid.uuid4())
    assert caught.value.status_code == 404
    # Een map van een ander: niet als doel van deze eigenaar
    bas, bas_id = await person(client, w["headers"], "bas@example.nl")
    with pytest.raises(HTTPException) as caught:
        await importer.start("Bron", w["target"], uuid.UUID(bas_id))
    assert caught.value.status_code == 404
    # In de prullenbak
    assert (await client.delete(f"/api/v1/files/{w['target']}", headers=w["headers"])).is_success
    with pytest.raises(HTTPException) as caught:
        await importer.start("Bron", w["target"], w["owner"])
    assert caught.value.detail["code"] == "folder_not_found"


async def test_start_cli_met_pad_en_e_mail(world, client):
    w = world
    put(w["src"], "Bron/a.mkv", b"a" * 10)
    settings = w["settings"]
    job_id = await importer.start_cli(settings, "/data/import/Bron", "/films/", "JONAS@example.nl")
    async with maker(settings)() as session:
        job = await session.get(Job, job_id)
    assert job.payload["target_id"] == str(w["target"]) and job.payload["source"] == "Bron"
    for target, email, code in (
        ("/Bestaat niet", "jonas@example.nl", "folder_not_found"),
        ("/Films", "niemand@example.nl", "not_found"),
    ):
        with pytest.raises(HTTPException) as caught:
            await importer.start_cli(settings, "Bron", target, email)
        assert caught.value.detail["code"] == code
    off = settings.model_copy(update={"media_enabled": False})
    with pytest.raises(HTTPException) as caught:
        await importer.start_cli(off, "Bron", "/Films", "jonas@example.nl")
    assert caught.value.detail["code"] == "media_off"


async def test_vooraf_kijken_en_events_naar_beheerders(world, client, monkeypatch):
    w = world
    put(w["src"], "Bron/a.mkv", b"a" * 10)
    put(w["src"], "Bron/sub/b.mkv", b"b" * 30)
    os.link(w["src"] / "Bron/a.mkv", w["src"] / "Bron/a2.mkv")
    found = await importer.survey(w["settings"], "Bron", move=True)
    assert (found.path, found.files, found.bytes, found.unmovable) == ("Bron", 3, 50, 2)
    assert (await importer.survey(w["settings"], "Bron")).unmovable == 0
    await person(client, w["headers"], "gewoon@example.nl")
    seen: list[tuple[list, str, dict]] = []
    monkeypatch.setattr(
        importer.events, "publish", lambda ids, type, data: seen.append((list(ids), type, data))
    )
    payload = await run_import(w)
    assert payload["done"] == 3
    mine = [(ids, data) for ids, t, data in seen if t == "media.import"]
    assert mine and all(ids == [w["owner"]] for ids, _ in mine)  # alleen de beheerder
    assert mine[-1][1]["state"] == "done" and mine[-1][1]["done"] == 3
    async with maker(w["settings"])() as session:
        view = await importer.current(session)
        assert view is not None and (view.state, view.total) == ("done", 3)
        admins = await session.scalars(select(User.id).where(User.role == "admin"))
        assert list(admins) == [w["owner"]]


async def test_media_uit(world, monkeypatch):
    w = world
    (w["src"] / "Bron").mkdir()
    off = w["settings"].model_copy(update={"media_enabled": False})
    monkeypatch.setattr(importer.runtime, "settings", lambda: off)
    with pytest.raises(HTTPException) as caught:
        await importer.start("Bron", w["target"], w["owner"])
    assert caught.value.detail["code"] == "media_off"


def test_de_job_staat_in_de_rij_import():
    kind = jobs._kinds[importer.JOB]
    assert (kind.lane, kind.timeout) == ("import", 86_400)


async def test_fuzz_namen_van_bestanden(world):
    """De namen komen van buiten (rule 10): willekeurige bytes, ongeldige UTF-8, stuurtekens,
    lange namen. Nooit een fout: binnen, of overgeslagen met "ongeldige naam"."""
    import random

    w = world
    rng = random.Random(20251029)
    base = os.fsencode(w["src"] / "Bron")
    os.mkdir(base)
    alphabet = [bytes([b]) for b in range(1, 256) if b != ord("/")] + ["é".encode(), b"\xff\xfe"]
    made = set()
    for _ in range(80):
        name = b"".join(rng.choice(alphabet) for _ in range(rng.randint(1, 300)))
        if name in (b".", b"..") or name in made:
            continue
        made.add(name)
        path = base + b"/" + name
        with contextlib.suppress(OSError):  # te lang voor het bestandssysteem
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
            os.write(fd, name)
            os.close(fd)
            when = time.time() - OLD
            os.utime(path, (when, when))
    present = len(os.listdir(base))
    payload = await run_import(w)
    assert payload["done"] + payload["skipped"] == present
    assert set(skipped(payload).values()) <= {importer.BAD_NAME}
    found = await tree(w["settings"], w["target"])
    assert len(found) == payload["done"]
    assert all(importer._valid(name) for name in found)
