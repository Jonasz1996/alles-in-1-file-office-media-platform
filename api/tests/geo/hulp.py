"""Hulp voor de tests van de plek met echte uploads: de jobs draaien, de rijen van photo_geo
lezen, een tweede gebruiker. De plaatsen komen uit de kleine bronnen (fixture plaatsen)."""

import uuid

from sqlalchemy import select

from app import jobs, photometa
from app.geo import load
from app.models import BlobMeta, PhotoGeo
from tests.factories import maker
from tests.test_render_meta import camera_jpeg

# De plaats van camera_jpeg() (52°22'1" N, 4°53'2" O) in tests/geo/bronnen.py
AMSTERDAM = load.place_id("NL", "North Holland", "Amsterdam", 52.37403, 4.88969)
VENSTER = "3.0,50.0,8.0,54.0"


def met_gps(color: str = "red") -> bytes:
    """Een JPEG met GPS (Amsterdam); een andere kleur is een andere inhoud."""
    return camera_jpeg(color=color)


def zonder_gps(color: str = "blue") -> bytes:
    return camera_jpeg(gps=False, color=color)


async def verwerk(settings) -> None:
    """De previews, en daarna de keten van photometa (die wacht DELAY na preview.ready)."""
    await jobs.run_pending(settings)
    await jobs.run_pending(settings, at=jobs.now() + photometa.DELAY * 2)


async def rijen(settings) -> dict[str, tuple[str, int, int | None, str]]:
    """Alle rijen van photo_geo: node -> (eigenaar, dead, plaats, bron)."""
    async with maker(settings)() as db:
        rows = await db.execute(
            select(
                PhotoGeo.node_id, PhotoGeo.owner_id, PhotoGeo.dead, PhotoGeo.place_id,
                PhotoGeo.source,
            )
        )  # fmt: skip
        return {str(n): (str(o), d, p, s) for n, o, d, p, s in rows}


async def gps_van(settings) -> dict[str, int | None]:
    async with maker(settings)() as db:
        rows = await db.execute(select(BlobMeta.blob_sha, BlobMeta.gps))
        return dict(rows.all())


async def map_maken(client, headers, naam: str, parent: str | None = None) -> str:
    if parent is None:
        r = await client.get("/api/v1/files/root", headers=headers)
        parent = r.json()["folder"]["id"]
    r = await client.post(f"/api/v1/files/{parent}/folders", json={"name": naam}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def kaart_ids(client, headers) -> set[str]:
    r = await client.get("/api/v1/photos/map/items", params={"bbox": VENSTER}, headers=headers)
    assert r.status_code == 200, r.text
    return {i["id"] for i in r.json()["items"]}


async def plek(client, headers, node_id: str) -> int:
    r = await client.get(f"/api/v1/files/{node_id}/location", headers=headers)
    return r.status_code


def uid(tokens) -> str:
    return str(uuid.UUID(tokens["user"]["id"]))
