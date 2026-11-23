"""Gezichten in laag 1 (fase 6, X2b): de app met ANALYSE aan, de nep-worker van de analyse
(tests/analyse/fake_worker.py) met de nep van faces en group (tests/analyse/faces_nep.py), en een
map met sleutels buiten de datamap. Tests en CI kiezen zo zelf voor gezichten; zonder deze
fixtures (zoals elke andere test, en zoals 0.8.0) staat de analyse uit en is gezichten
onzichtbaar (test_faces_off.py).

Een foto (foto()) is een node met mime image/jpeg, een blob, en een echte 1024.webp onder de
previews met preview_meta klaar; de nep kent de bytes van die 1024 en geeft de gezichten die de
test opgaf.
"""

from __future__ import annotations

import io
import itertools
import sqlite3
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app import db, jobs, previews
from app.analyse import monitor
from app.migrate import upgrade
from app.models import Blob, Node, PhotoPref
from app.people.faces import register, state
from app.search import hooks as search_hooks
from app.storage import BlobStore
from tests.analyse import faces_nep, pgdb
from tests.analyse.fake_worker import FakeAnalyseWorker
from tests.factories import create_file, maker

BASE = datetime(2024, 7, 1, 12, 0, tzinfo=UTC)
_kleur = itertools.count(1)


@pytest.fixture(autouse=True)
def _aangemeld():
    """Een andere test kan de haken weggehaald hebben (people.hooks.reset, search.hooks.reset,
    prefs.forget_switches): opnieuw aanmelden, zoals de import van de router het doet."""
    register.register()
    yield
    state.reset()


@pytest.fixture
async def fake(tmp_path):
    async with FakeAnalyseWorker(tmp_path / "run" / "worker.sock") as worker:
        yield worker


@pytest.fixture
def nep(fake) -> faces_nep.FacesNep:
    return faces_nep.install(fake)


@pytest.fixture
async def settings(tmp_path, database_url_head, fake, nep):
    """Settings met ANALYSE aan, de socket van de nep en PLATFORM_FACES_KEYS buiten de datamap.
    De database: die van de test (database_url_head: op PostgreSQL een kopie van het sjabloon,
    met ANALYSE_POSTGRES en de triggers van 0013). De stand van de monitor is ok (een echte caps
    bij de nep)."""
    keys = tmp_path / "sleutels"
    keys.mkdir()
    (tmp_path / "data").mkdir()
    s = fake.settings(tmp_path / "data", faces_keys=str(keys), **pgdb.on_db(database_url_head))
    await upgrade(s)
    await pgdb.ready(s)
    state.reset()
    state.configure(s)
    register.register_admin(s)
    monitor._status = await monitor.check(s)
    assert monitor._status.state == "ok", monitor._status
    yield s
    monitor._status = monitor.OFF
    monitor._task = None
    search_hooks._admin.pop("faces", None)  # weer als na de import (uit)
    await db.get_engine(s).dispose()
    db._engine.cache_clear()


def webp(seed: int, size: tuple[int, int] = (64, 48)) -> bytes:
    """Een kleine, echte WebP (verliesloos), elke seed andere bytes."""
    from PIL import Image

    img = Image.new("RGB", size, (seed % 251, (seed // 251) % 251, 7))
    img.putpixel((0, 0), (seed % 256, 3, (seed * 7) % 256))
    out = io.BytesIO()
    img.save(out, "WEBP", lossless=True)
    return out.getvalue()


async def write_large(settings, sha: str, data: bytes, *, sizes=(256, 1024, 2048)) -> None:
    folder = previews.directory(BlobStore(settings.blob_dir), sha)
    folder.mkdir(parents=True, exist_ok=True)
    if previews.LARGE in sizes:
        (folder / "1024.webp").write_bytes(data)
    async with maker(settings)() as session:
        blob = await session.get(Blob, sha)
        blob.preview_meta = {"sizes": list(sizes), "partial": False}
        await session.commit()


async def foto(
    settings,
    nep: faces_nep.FacesNep,
    owner: uuid.UUID,
    name: str,
    faces: list[tuple[tuple[float, float, float, float], int]],
    *,
    taken: datetime | None = None,
    parent: uuid.UUID | None = None,
    large: bool = True,
    sizes=(256, 1024, 2048),
) -> uuid.UUID:
    """Een foto van owner met deze gezichten (kader, persoon) in de nep."""
    from app.models import User

    async with maker(settings)() as session:
        user = await session.get(User, owner)
        node = await create_file(
            session,
            user,
            parent,
            name,
            data=f"{owner}-{name}-{next(_kleur)}".encode(),
            mime="image/jpeg",
            taken_at=taken or BASE,
            blobs=BlobStore(settings.blob_dir),
        )
        sha, node_id = node.blob_id, node.id
        await session.commit()
    seed = next(_kleur)
    data = webp(seed)
    nep.add(data, faces)
    if large:
        await write_large(settings, sha, data, sizes=sizes)
    else:
        await write_large(settings, sha, b"", sizes=sizes)
    return node_id


async def aan(settings, *owners: uuid.UUID) -> None:
    """Gezichten aan voor deze eigenaren (zoals PATCH /me/photo-settings, met faces.plan)."""
    from app.people.faces import scan

    async with maker(settings)() as session:
        for owner in owners:
            row = await session.get(PhotoPref, owner)
            if row is None:
                session.add(PhotoPref(user_id=owner, faces="aan"))
            else:
                row.faces = "aan"
            await scan.queue_plan(session, "owner", owner=owner)
        await session.commit()


async def draai(settings, at: datetime | None = None) -> int:
    """Alle jobs die klaarstaan (ook de rij analyse)."""
    return await jobs.run_pending(settings, at)


async def later(settings, seconds: float = 120) -> int:
    """De jobs die over seconds klaarstaan (zoals faces.group 30 s na een keuze)."""
    return await jobs.run_pending(settings, datetime.now(UTC) + timedelta(seconds=seconds))


async def node_ids(settings, owner: uuid.UUID) -> list[uuid.UUID]:
    async with maker(settings)() as session:
        return list(await session.scalars(select(Node.id).where(Node.owner_id == owner)))


def lezer(settings) -> sqlite3.Connection:
    """Een open lezer die de WAL echt vasthoudt: BEGIN en een SELECT op een eigen verbinding.

    Staat de WAL bij zijn BEGIN al helemaal in de database (de checkpointer van app/dbgate.py
    loopt op de achtergrond), dan leest SQLite voor hem alleen het bestand, en houdt hij geen
    checkpoint op. Dan eerst één frame erbij (user_version op zijn eigen waarde; de app gebruikt
    user_version niet) en opnieuw. Nagegaan met db.wal_truncate: busy, met een echte uitkomst
    (niet het slot van een ander checkpoint). De test sluit hem met COMMIT en close()."""
    path = db.sqlite_path(settings.db_url)
    assert path is not None
    reader = sqlite3.connect(path, isolation_level=None)
    for _ in range(5):
        version = int(reader.execute("PRAGMA user_version").fetchone()[0])
        reader.execute(f"PRAGMA user_version = {version}")
        reader.execute("BEGIN")
        reader.execute("SELECT count(*) FROM face").fetchone()
        found = db.wal_truncate(path)
        if found.busy and found.log >= 0:
            return reader
        reader.execute("COMMIT")
    reader.close()
    raise AssertionError("geen lezer die de WAL vasthoudt")
