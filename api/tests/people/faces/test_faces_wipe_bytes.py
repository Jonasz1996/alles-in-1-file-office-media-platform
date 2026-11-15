"""Gewist is weg, ook als bytes (fase 6, X2 "Het wissen", Keuze 64 en 68, K1-15; X2b).

De merktekens zijn stukken van de verzegelde vectoren (vec, 540 B per gezicht) zoals ze in de
database staan. Vóór het wissen staan ze in platform.db of -wal (anders zegt de test niets); na
"niet tonen", "ongedaan maken", "niet benoemde wissen", een persoon wissen, definitief wissen (met
het checkpoint van het uur) en "uit" staat geen enkel merkteken meer in platform.db of in -wal.
Met een open lezer lukt het checkpoint niet: de job probeert het later opnieuw (Defer), /beheer
zegt "wissen wacht op een lezer", en na het sluiten van de lezer is alles weg.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import db as app_db
from app import jobs
from app.models import Face, Job
from app.people.faces import wipe
from tests.factories import create_user, maker
from tests.people.faces.conftest import aan, draai, foto
from tests.test_photos import login

pytestmark = pytest.mark.anyio

A = (0.10, 0.10, 0.20, 0.25)
B = (0.60, 0.20, 0.20, 0.25)
F = "/api/v1/people/faces"


@pytest.fixture
async def eva(client, settings, nep):
    """Eva (aan): drie foto's met persoon 1 en 2, gescand."""
    async with maker(settings)() as session:
        user = await create_user(session, "eva@example.nl")
        await session.commit()
    ids = {"id": user.id}
    ids["fotos"] = [
        await foto(settings, nep, user.id, f"{i}.jpg", [(A, 1), (B, 2)]) for i in range(3)
    ]
    await aan(settings, user.id)
    await draai(settings)
    ids["h"] = await login(client, "eva@example.nl")
    return ids


def marks(rows: list[Face]) -> list[bytes]:
    """Twee stukken van 32 B uit elke verzegelde vector (na de nonce): versleuteld, dus uniek."""
    out = []
    for f in rows:
        assert f.vec is not None and len(f.vec) == 540
        out += [f.vec[16:48], f.vec[300:332]]
    return out


def found(settings, wanted: list[bytes]) -> dict[str, int]:
    """Hoe vaak de merktekens in platform.db en -wal staan."""
    path = app_db.sqlite_path(settings.db_url)
    assert path is not None
    out = {}
    for name in (path, path.with_name(path.name + "-wal")):
        data = name.read_bytes() if name.exists() else b""
        out[name.name.rsplit(".", 1)[-1]] = sum(data.count(m) for m in wanted)
    return out


async def faces(settings, owner, *where) -> list[Face]:
    async with maker(settings)() as session:
        return list(
            await session.scalars(
                select(Face).where(Face.owner_id == owner, *where).order_by(Face.node_id, Face.x)
            )
        )


async def before(settings, rows) -> list[bytes]:
    wanted = marks(rows)
    seen = found(settings, wanted)
    assert seen["db"] + seen["db-wal"] >= len(wanted), seen
    return wanted


async def test_niet_tonen_en_ongedaan_maken(client, settings, eva):
    rows = await faces(settings, eva["id"], Face.node_id == eva["fotos"][0])
    wanted = await before(settings, rows)
    # Beide niet tonen; daarna het tweede ongedaan maken: weer zichtbaar, zonder vector
    ids = [str(f.id) for f in rows]
    r = await client.post(f"{F}/hide", json={"faces": ids}, headers=eva["h"])
    assert r.status_code == 204
    r = await client.post(f"{F}/reset", json={"faces": ids[1:]}, headers=eva["h"])
    assert r.status_code == 204, r.text
    after = await faces(settings, eva["id"], Face.id.in_([f.id for f in rows]))
    assert [(f.hidden, f.vec) for f in after] == [(1, None), (0, None)]
    await draai(settings)  # faces.wipe met scope checkpoint
    assert found(settings, wanted) == {"db": 0, "db-wal": 0}
    # De andere gezichten staan er nog
    rest = await faces(settings, eva["id"], Face.node_id != eva["fotos"][0])
    assert len(rest) == 4 and found(settings, marks(rest))["db"] > 0


async def test_niet_benoemde_wissen_en_een_persoon_wissen(client, settings, eva):
    h = eva["h"]
    alle = await faces(settings, eva["id"])
    wanted = await before(settings, alle)
    # Oma op de gezichten van persoon 1; de rest is open
    oma_faces = [str(f.id) for f in alle if f.x == A[0]]
    r = await client.post(f"{F}/confirm", json={"faces": oma_faces, "name": "Oma"}, headers=h)
    assert r.status_code == 204
    r = await client.post(f"{F}/wipe-unnamed", headers=h)
    assert r.status_code == 202, r.text
    await draai(settings)
    open_marks = marks([f for f in alle if f.x == B[0]])
    assert found(settings, open_marks) == {"db": 0, "db-wal": 0}
    # Oma wissen: haar gezichten "niet tonen", zonder vector
    oma = (await client.get("/api/v1/people", headers=h)).json()["people"][0]["id"]
    r = await client.delete(f"/api/v1/people/{oma}", headers=h)
    assert r.status_code == 204, r.text
    await draai(settings)
    assert found(settings, wanted) == {"db": 0, "db-wal": 0}
    assert all(f.vec is None for f in await faces(settings, eva["id"]))


async def test_definitief_wissen_met_het_checkpoint_van_het_uur(client, settings, eva):
    node = eva["fotos"][1]
    wanted = await before(settings, await faces(settings, eva["id"], Face.node_id == node))
    assert (await client.delete(f"/api/v1/files/{node}", headers=eva["h"])).status_code == 204
    assert (await client.delete(f"/api/v1/trash/{node}", headers=eva["h"])).status_code == 204
    await jobs._checkpoint(settings)
    assert found(settings, wanted) == {"db": 0, "db-wal": 0}


async def test_uit_met_een_open_lezer(client, settings, eva, monkeypatch):
    h = eva["h"]
    wanted = await before(settings, await faces(settings, eva["id"]))
    monkeypatch.setattr(wipe, "CHECKPOINT_BUDGET", 0.3)
    path = app_db.sqlite_path(settings.db_url)
    reader = sqlite3.connect(path, isolation_level=None)
    try:
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM face").fetchone()
        r = await client.patch("/api/v1/me/photo-settings", json={"faces": "uit"}, headers=h)
        assert r.status_code == 200
        await draai(settings)
        # De rijen zijn weg, maar het checkpoint wacht op de lezer: opnieuw over een minuut
        assert await faces(settings, eva["id"]) == []
        assert eva["id"].hex in wipe.WAITING
        async with maker(settings)() as session:
            job = (await session.scalars(select(Job).where(Job.type == wipe.WIPE))).one()
        assert (job.status, job.attempts) == ("queued", 0)
        assert job.run_after.replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(seconds=30)
        assert found(settings, wanted)["db-wal"] + found(settings, wanted)["db"] > 0
    finally:
        reader.execute("COMMIT")
        reader.close()
    await draai(settings, datetime.now(UTC) + timedelta(seconds=wipe.WAIT_S + 1))
    assert eva["id"].hex not in wipe.WAITING
    assert found(settings, wanted) == {"db": 0, "db-wal": 0}
    async with maker(settings)() as session:
        assert [
            j.status for j in await session.scalars(select(Job).where(Job.type == wipe.WIPE))
        ] == ["done"]


def test_merktekens_tellen_echt(tmp_path):
    """De zoeker zelf: een merkteken in een bestand wordt gevonden."""
    marker = uuid.uuid4().bytes * 2
    path = tmp_path / "platform.db"
    path.write_bytes(b"x" * 100 + marker + b"y" * 10)

    class S:
        db_url = f"sqlite+aiosqlite:///{path}"

    assert found(S, [marker]) == {"db": 1, "db-wal": 0}
