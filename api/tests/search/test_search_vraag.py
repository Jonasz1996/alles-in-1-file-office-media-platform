"""De vraag van GET /search aan de randen, in beide motoren (review golf 1, contract en veilig).

Elke test draait met FtsEngine (de index aan) en met LikeFallback (PLATFORM_SEARCH_INDEX=uit),
en waar het kan ook met de vraag van 0.7.0 (limit 200: altijd LikeFallback, "De overgang" in
§3.4). Dezelfde vraag geeft dan hetzelfde antwoord, of dezelfde fout:

- een vraag van meer dan 200 tekens: 400 search_query_too_long, ook bij suggest (N2);
- person, place, country en camera: 501 not_built tot Z3 en Z6 ze aanmelden (S1);
- library van een bibliotheek die je niet ziet: 404 (veilig F3);
- de eigen hoofdmap is nooit een treffer, ook niet zonder q (S2);
- in en library zoeken onder die map; de map zelf is geen treffer (N1);
- from en to filteren ook in LikeFallback (S3).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_engine
from app.models import MediaLibrary, Node
from tests.test_files import mkdir, new_user, put, root

pytestmark = pytest.mark.anyio

PNG = b"\x89PNG\r\n\x1a\n\0\0\0\rIHDR"
# Een vaste dag om 12:00 UTC: in elke tijdzone van Europa dezelfde dag
DAG = datetime(2024, 3, 14, 12, tzinfo=UTC)


@pytest.fixture(params=["fts", "like"])
def motor(request, settings, monkeypatch) -> str:
    if request.param == "like":
        monkeypatch.setattr(settings, "search_index", "uit")
    return request.param


async def _zoek(client, headers, expect=200, **params):
    r = await client.get("/api/v1/search", params=params, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def _namen(page) -> set[str]:
    return {hit["node"]["name"] for hit in page["results"]}


async def _beide(client, headers, motor, **params) -> set[str]:
    """Dezelfde vraag via de motor en als vraag van 0.7.0 (limit 200): dezelfde namen."""
    page = await _zoek(client, headers, **params)
    assert page["engine"] == motor
    oud = await _zoek(client, headers, **params, limit=200)
    assert oud["engine"] == "like"
    assert _namen(oud) == _namen(page), params
    return _namen(page)


async def test_een_te_lange_vraag_is_400(client, admin, motor):
    headers, _ = admin
    for pad in ("/api/v1/search", "/api/v1/search/suggest"):
        r = await client.get(pad, params={"q": "x" * 201}, headers=headers)
        assert r.status_code == 400, (pad, r.text)
        assert r.json()["detail"]["code"] == "search_query_too_long"
        r = await client.get(pad, params={"q": "x" * 200}, headers=headers)
        assert r.status_code == 200, (pad, r.text)
    # Ook als vraag van 0.7.0 (limit 200 gaat altijd naar LikeFallback)
    r = await client.get("/api/v1/search", params={"q": "x" * 201, "limit": 200}, headers=headers)
    assert r.status_code == 400 and r.json()["detail"]["code"] == "search_query_too_long"


@pytest.mark.parametrize(
    ("param", "waarde", "pakket"),
    [
        ("person", str(uuid.UUID(int=7)), "Z6"),
        ("place", "2759794", "Z3"),
        ("place", "0", "Z3"),
        ("country", "NL", "Z3"),
        ("camera", "Canon EOS 5D", "Z3"),
    ],
)
async def test_de_filters_van_later_zijn_501(client, admin, motor, param, waarde, pakket):
    headers, _ = admin
    for extra in ({}, {"q": "foto"}, {"q": "foto", "limit": 200}):
        r = await client.get("/api/v1/search", params={param: waarde, **extra}, headers=headers)
        assert r.status_code == 501, (extra, r.text)
        assert r.json()["detail"] == {
            "code": "not_built",
            "message": f"Nog niet gebouwd ({pakket})",
            "params": {"package": pakket},
        }


async def _bibliotheek(settings, owner: uuid.UUID, root_id: str) -> uuid.UUID:
    library_id = uuid.uuid4()
    async with AsyncSession(get_engine(settings)) as session:
        session.add(MediaLibrary(id=library_id, owner_id=owner, root_id=uuid.UUID(root_id),
                                 kind="videos", name="Films"))  # fmt: skip
        await session.commit()
    return library_id


async def test_een_bibliotheek_die_je_niet_ziet_is_404(client, admin, settings, motor):
    headers, tokens = admin
    eigen = (await root(client, headers))["folder"]["id"]
    films = await mkdir(client, headers, eigen, "films")
    await put(client, headers, films, "film een.txt", b"x")
    await put(client, headers, eigen, "film buiten.txt", b"x")
    library = await _bibliotheek(settings, uuid.UUID(tokens["user"]["id"]), films)
    # Van jezelf: alleen wat onder de hoofdmap van de bibliotheek ligt, zonder die map zelf
    assert await _beide(client, headers, motor, q="film", library=str(library)) == {"film een.txt"}
    assert await _beide(client, headers, motor, library=str(library)) == {"film een.txt"}
    # Een ander kent het id, maar ziet de map niet: 404, in beide motoren en als 0.7.0
    bram = await new_user(client, headers, "bram@example.nl")
    for extra in ({}, {"q": "film"}, {"q": "film", "limit": 200}):
        r = await client.get(
            "/api/v1/search", params={"library": str(library), **extra}, headers=bram
        )
        assert r.status_code == 404, (extra, r.text)
    # Een id dat niet bestaat ook
    await _zoek(client, headers, expect=404, library=str(uuid.uuid4()))
    # Gedeeld met bram: dan wel
    bram_id = (await client.get("/api/v1/auth/me", headers=bram)).json()["id"]
    r = await client.post(
        f"/api/v1/files/{films}/shares",
        json={"user_id": bram_id, "permission": "read"},
        headers=headers,
    )
    assert r.status_code == 201, r.text
    assert await _beide(client, bram, motor, q="film", library=str(library)) == {"film een.txt"}


async def test_de_eigen_hoofdmap_is_nooit_een_treffer(client, admin, settings, motor):
    headers, _ = admin
    eigen = (await root(client, headers))["folder"]["id"]
    await mkdir(client, headers, eigen, "map")
    await put(client, headers, eigen, "los.txt", b"x")
    async with AsyncSession(get_engine(settings)) as session:
        await session.execute(update(Node).values(modified_at=DAG))
        await session.commit()
    assert await _beide(client, headers, motor, type="folder") == {"map"}
    assert await _beide(client, headers, motor, **{"from": "2024-03-14"}) == {"map", "los.txt"}
    assert await _beide(client, headers, motor, **{"to": "2024-03-14"}) == {"map", "los.txt"}


async def test_in_zoekt_onder_de_map_zonder_de_map_zelf(client, admin, motor):
    headers, _ = admin
    eigen = (await root(client, headers))["folder"]["id"]
    rapport = await mkdir(client, headers, eigen, "rapport")
    sub = await mkdir(client, headers, rapport, "rapport sub")
    await put(client, headers, sub, "rapport diep.txt", b"x")
    await put(client, headers, eigen, "rapport buiten.txt", b"x")
    alles = {"rapport sub", "rapport diep.txt"}
    assert await _beide(client, headers, motor, q="rapport", **{"in": rapport}) == alles
    assert await _beide(client, headers, motor, **{"in": rapport}) == alles
    assert await _beide(client, headers, motor, type="folder", **{"in": rapport}) == {"rapport sub"}
    assert await _beide(client, headers, motor, q="rapport") == alles | {
        "rapport",
        "rapport buiten.txt",
    }


async def test_from_en_to_in_beide_motoren(client, admin, settings, motor):
    headers, _ = admin
    eigen = (await root(client, headers))["folder"]["id"]
    ids = {}
    for naam in ("dag ervoor.txt", "dag zelf.txt", "dag erna.txt"):
        ids[naam] = (await put(client, headers, eigen, naam, b"x")).json()["id"]
    # Een foto met een opnamedag (de lastModified van de client); modified_at is vandaag
    foto = await put(client, headers, eigen, "dag foto.png", PNG, mtime=int(DAG.timestamp() * 1000))
    assert foto.status_code == 201, foto.text
    dagen = {"dag ervoor.txt": 13, "dag zelf.txt": 14, "dag erna.txt": 15}
    async with AsyncSession(get_engine(settings)) as session:
        for naam, dag in dagen.items():
            await session.execute(
                update(Node)
                .where(Node.id == uuid.UUID(ids[naam]))
                .values(modified_at=DAG.replace(day=dag))
            )
        await session.commit()
    gelijk = {"from": "2024-03-14", "to": "2024-03-14"}
    assert await _beide(client, headers, motor, q="dag", **gelijk) == {
        "dag zelf.txt",
        "dag foto.png",
    }
    assert await _beide(client, headers, motor, q="dag", **{"from": "2024-03-15"}) == {
        "dag erna.txt"
    }
    assert await _beide(client, headers, motor, q="dag", **{"to": "2024-03-13"}) == {
        "dag ervoor.txt"
    }
    assert await _beide(client, headers, motor, **gelijk) == {"dag zelf.txt", "dag foto.png"}
