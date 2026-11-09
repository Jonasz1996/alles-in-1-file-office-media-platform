"""De plekken in de bestaande app die Z0 aanpast (bouwontwerp fase 6, §3.5).

Nieuwe inhoud meldt zich na de commit bij search.hooks.blob_added; kopiëren roept photo_copy in
dezelfde transactie; de prullenbak en het wissen van een gebruiker wissen nodes via de ene
helper; na preview.ready komt wie zich aanmeldde aan de beurt; render.py kent de ingangen text
(sinds Z2 echt) en ocr_page (een stub tot X1). De rijen index en ocr staan in test_jobs_lanes.py,
de log in test_log_hiding.py, en db-backup en de stubs van de cli in test_cli_db.py.

Z0b (Keuze 65): hier dat niemand zich met register_large aanmeldt, en (na de review) ook niet met
register_switch of register_admin. Wat er na de grote preview gebeurt, staat in test_previews.py:
daar draait de echte tweede ronde, met de renderer, en die heeft in python:3.12-slim van
e2e/slim.sh geen Pillow (hij start met -I en zonder PYTHONPATH).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
import sqlalchemy as sa
from PIL import Image
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import previews
from app.db import get_engine
from app.extract import frame
from app.models import Node
from app.search import hooks, purge

pytestmark = pytest.mark.anyio

API_DIR = Path(__file__).resolve().parents[2]


@pytest.fixture(autouse=True)
def _schoon():
    """Geen aanmeldingen van een vorige test, en ook niet naar de volgende."""
    hooks.reset()
    previews.forget_ready()
    previews.forget_large()
    yield
    hooks.reset()
    previews.forget_ready()
    previews.forget_large()


async def put(client, headers, parent: str, name: str, data: bytes) -> dict:
    r = await client.put(
        f"/api/v1/files/{parent}/upload",
        params={"name": name},
        content=data,
        headers=headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def mkdir(client, headers, parent: str, name: str) -> str:
    r = await client.post(f"/api/v1/files/{parent}/folders", json={"name": name}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def tree(client, headers) -> tuple[str, uuid.UUID]:
    """De hoofdmap van de beheerder en zijn id."""
    r = (await client.get("/api/v1/files/root", headers=headers)).json()
    me = (await client.get("/api/v1/auth/me", headers=headers)).json()
    return r["folder"]["id"], uuid.UUID(me["id"])


async def test_nieuwe_inhoud_meldt_zich_na_de_commit(client, admin, settings):
    """files.add_ref: blob_added(sha, owner) na de commit, en alleen voor nieuwe inhoud."""
    headers, _ = admin
    root, me = await tree(client, headers)
    seen: list[tuple[str, uuid.UUID | None]] = []
    hooks.register_blob_added(lambda sha, owner: seen.append((sha, owner)))

    body = b"tekst van een"
    await put(client, headers, root, "een.txt", body)
    assert seen == [(hashlib.sha256(body).hexdigest(), me)]

    # Dezelfde inhoud nog eens: geen nieuwe blob, dus geen melding
    await put(client, headers, root, "twee.txt", body)
    assert len(seen) == 1
    # Andere inhoud: wel
    await put(client, headers, root, "drie.txt", b"iets anders")
    assert len(seen) == 2


async def test_een_rollback_meldt_niets(settings):
    """Wat niet gecommit is, wordt niet gemeld."""
    seen: list[str] = []
    hooks.register_blob_added(lambda sha, owner: seen.append(sha))
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session:
        await session.execute(sa.text("SELECT 1"))  # er moet echt een transactie open staan
        hooks.blob_added_after_commit(session, "a" * 64, None)
        assert hooks.pending(session) == [("a" * 64, None)]
        await session.rollback()
        assert hooks.pending(session) == []
    assert seen == []


async def test_een_fout_in_de_haak_stopt_de_upload_niet(client, admin, caplog):
    headers, _ = admin
    root, _me = await tree(client, headers)

    def stuk(sha, owner):
        raise RuntimeError("kapot")

    hooks.register_blob_added(stuk)
    with caplog.at_level("ERROR"):
        await put(client, headers, root, "vier.txt", b"nog iets anders")
    assert "search.hooks.blob_added" in caplog.text


async def test_kopieren_roept_photo_copy_voor_de_commit(client, admin):
    """files.copy geeft (bron, kopie) per bestand, nog in dezelfde transactie."""
    headers, _ = admin
    root, _me = await tree(client, headers)
    paren: list[tuple[uuid.UUID, uuid.UUID]] = []
    staat_er_al: list[bool] = []

    async def meekijken(db, pairs):
        paren.extend(pairs)
        ids = [new for _, new in pairs]
        found = await db.scalar(
            sa.select(sa.func.count()).select_from(Node).where(Node.id.in_(ids))
        )
        staat_er_al.append(found == len(ids))

    hooks.register_photo_copy(meekijken)
    doel = await mkdir(client, headers, root, "doel")
    bestand = await put(client, headers, root, "foto.txt", b"een foto")

    r = await client.post(
        f"/api/v1/files/{bestand['id']}/copy", json={"parent_id": doel}, headers=headers
    )
    assert r.status_code == 201, r.text
    assert paren == [(uuid.UUID(bestand["id"]), uuid.UUID(r.json()["id"]))]
    assert staat_er_al == [True]  # de kopie staat er in de transactie van de haak al

    # Veel tegelijk (routers/batch.py), met een map erbij: alleen de bestanden
    tweede = await put(client, headers, root, "ander.txt", b"iets anders")
    map_met = await mkdir(client, headers, root, "map")
    binnen = await put(client, headers, map_met, "binnen.txt", b"binnenin")
    r = await client.post(
        "/api/v1/batch/copy",
        json={"ids": [tweede["id"], map_met], "parent_id": doel},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert [ok["ok"] for ok in r.json()["results"]] == [True, True]
    bronnen = {src for src, _ in paren}
    assert bronnen == {uuid.UUID(bestand["id"]), uuid.UUID(tweede["id"]), uuid.UUID(binnen["id"])}


async def test_een_map_zonder_bestanden_roept_de_haak_niet(client, admin):
    headers, _ = admin
    root, _me = await tree(client, headers)
    paren: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def meekijken(db, pairs):
        paren.extend(pairs)

    hooks.register_photo_copy(meekijken)
    leeg = await mkdir(client, headers, root, "leeg")
    doel = await mkdir(client, headers, root, "doel")
    r = await client.post(f"/api/v1/files/{leeg}/copy", json={"parent_id": doel}, headers=headers)
    assert r.status_code == 201, r.text
    assert paren == []  # een map is geen bestand


async def _count_nodes(settings) -> int:
    async with get_engine(settings).connect() as conn:
        return await conn.scalar(sa.select(sa.func.count()).select_from(Node))


async def test_de_prullenbak_wist_via_de_ene_helper(client, admin, settings, monkeypatch):
    """routers/trash.py wist nodes via purge.delete_nodes (Keuze 11), diepste eerst."""
    headers, _ = admin
    root, _me = await tree(client, headers)
    gewist: list[list[uuid.UUID]] = []
    echt = purge.delete_nodes

    async def tellen(db, ids):
        gewist.append(list(ids))
        return await echt(db, ids)

    monkeypatch.setattr(purge, "delete_nodes", tellen)

    folder = await mkdir(client, headers, root, "weg")
    bestand = await put(client, headers, folder, "in-de-map.txt", b"weg hiermee")
    voor = await _count_nodes(settings)

    assert (await client.delete(f"/api/v1/files/{folder}", headers=headers)).status_code == 204
    assert (await client.delete(f"/api/v1/trash/{folder}", headers=headers)).status_code == 204
    assert len(gewist) == 1  # één aanroep voor de map en het bestand erin
    assert gewist[0] == [uuid.UUID(bestand["id"]), uuid.UUID(folder)]  # kind voor zijn ouder
    assert await _count_nodes(settings) == voor - 2


async def test_veel_tegelijk_wist_ook_via_de_helper(client, admin, settings, monkeypatch):
    """DELETE /trash (leegmaken) en /trash/purge gaan via purge_chunks, en dus ook via de
    helper."""
    headers, _ = admin
    root, _me = await tree(client, headers)
    gewist: list[int] = []
    echt = purge.delete_nodes

    async def tellen(db, ids):
        gewist.append(len(ids))
        return await echt(db, ids)

    monkeypatch.setattr(purge, "delete_nodes", tellen)

    for name in ("een.txt", "twee.txt"):
        node = await put(client, headers, root, name, name.encode())
        assert (
            await client.delete(f"/api/v1/files/{node['id']}", headers=headers)
        ).status_code == 204
    r = await client.delete("/api/v1/trash", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["purged"] == 2
    assert sum(gewist) == 2
    assert await _count_nodes(settings) == 1  # alleen de hoofdmap


def test_de_vorm_van_de_helper():
    """De helper doet het in de transactie van de aanroeper; de stukken maken de aanroepers
    (test_search_purge_batches.py)."""
    assert purge.CHUNK == 2000 and purge.IN_LIMIT == 500
    assert purge.delete_nodes.__module__ == "app.search.purge"


async def test_na_preview_ready_is_de_volgende_aan_de_beurt(settings):
    """previews.register_ready(fn): na preview.ready, met (maker, sha, dated)."""
    gezien: list[tuple[str, bool]] = []

    async def klaar(maker, sha, dated):
        gezien.append((sha, dated))

    previews.register_ready(klaar)
    previews.register_ready(klaar)  # twee keer aanmelden is één keer
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    await previews.announce(maker, "b" * 64, dated=True)
    assert gezien == [("b" * 64, True)]


async def test_een_fout_na_preview_ready_gaat_door(settings, caplog):
    async def stuk(maker, sha, dated):
        raise RuntimeError("kapot")

    gezien: list[str] = []

    async def daarna(maker, sha, dated):
        gezien.append(sha)

    previews.register_ready(stuk)
    previews.register_ready(daarna)
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    with caplog.at_level("ERROR"):
        await previews.announce(maker, "c" * 64)
    assert "previews.ready" in caplog.text
    assert gezien == ["c" * 64]


def test_niemand_meldt_zich_aan_na_de_grote_preview():
    """Z0b meldt niemand aan, ook niet als de hele app geladen is (een eigen proces: de fixture
    hierboven haalt de aanmeldingen in dit proces weg)."""
    code = "import app.main; from app import previews; print(len(previews._large))"
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=API_DIR, capture_output=True, check=True, text=True
    )
    assert done.stdout.strip() == "0"


def test_niemand_meldt_een_schakelaar_of_een_deel_van_beheer_aan():
    """Ook de haken van de review van Z0b (contract F4) hebben nog niemand: geen schakelaar in
    app.prefs en geen deel van /admin/search in search.hooks, als de hele app geladen is. Dan
    weigert check_switch "aan" en geven admin_switch en admin_rebuild 501, tot X2 en X3 er zijn
    (nacontrole van Z0b)."""
    code = (
        "import app.main; from app import prefs; from app.search import hooks; "
        "print(len(prefs._switches), len(hooks._admin))"
    )
    done = subprocess.run(
        [sys.executable, "-c", code], cwd=API_DIR, capture_output=True, check=True, text=True
    )
    assert done.stdout.strip() == "0 0"


def _render(request: dict) -> dict:
    """render.py als eigen proces, zoals previews.py het start."""
    done = subprocess.run(
        [sys.executable, "-c", "from app import render; render.main()"],
        cwd=API_DIR,
        input=json.dumps(request).encode(),
        capture_output=True,
        check=True,
    )
    return json.loads(done.stdout)


def test_render_leest_text_en_maakt_de_pgm_van_ocr_page(tmp_path):
    """De ingangen van §3.5: text leest sinds Z2 (hier mode text met paden, de terugval zonder
    media; tests/extract heeft de rest), ocr_page maakt sinds X1 een grijze PGM (hier mode
    ocr_page met paden; tests/ocr heeft de rest: de handdruk, de rechtzetting, de worker)."""
    source = tmp_path / "doc.txt"
    source.write_text("Er staat tekst in dit bestand.")
    uit = tmp_path / "uit"
    items = [
        {"path": str(source), "kind": "plain"},
        {"path": str(tmp_path / "weg"), "kind": "plain"},
    ]
    out = _render({"mode": "text", "items": items, "out": str(uit), "max_chars": 1000})
    assert out == {"state": "ready", "done": 2}

    async def lees() -> list[frame.Found | None]:
        reader = asyncio.StreamReader()
        reader.feed_data(uit.read_bytes())
        reader.feed_eof()
        return [await frame.read(reader, i, 1000) for i in range(3)]

    first, second, end = asyncio.run(lees())
    assert first == frame.Found(0, "done", False, [], "", None, "Er staat tekst in dit bestand.")
    assert second == frame.Found(1, "failed", False, [], "", None, "")
    assert end is None
    pgm = tmp_path / "pagina.pgm"
    page = _render({"mode": "ocr_page", "source": str(source), "out": str(pgm), "kind": "image"})
    assert page == {"state": "failed", "error": "UnidentifiedImageError"}
    beeld = tmp_path / "wit.png"
    Image.new("RGB", (300, 200), "white").save(beeld)
    page = _render({"mode": "ocr_page", "source": str(beeld), "out": str(pgm), "kind": "image"})
    assert set(page.pop("ms")) == {"render", "deskew"}
    assert page == {
        "state": "ready",
        "skew": 0.0,
        "width": 300,
        "height": 200,
        "source": [300, 200],
    }
    assert pgm.read_bytes() == b"P5\n300 200\n255\n" + b"\xff" * 300 * 200
