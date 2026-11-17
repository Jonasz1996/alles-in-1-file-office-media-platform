"""Wat zoeken betekent (bouwontwerp fase 6, §4.3; Keuzes 4, 5, 6, 7 en 25a): FtsEngine naast de
referentie (tests/search/referentie.py, de definitie).

- De boom van 2.000 nodes (referentie.BOOM): voor elk toegangsprofiel van §4.4 (eigenaar,
  ontvanger van 5 en van 20 delingen, groepslid, gast, alles) en elke vraag van vragen.tsv geeft
  FtsEngine precies Referentie.zoek: eerst de treffers op hele woorden (ronde A), daarna de
  stukken van namen; niets dubbel, niets gedeeltelijk, en geen search_access_mismatch.
- De volgorde: een treffer op de naam staat boven een treffer in de tekst met hetzelfde woord;
  een heel woord boven een stuk van een naam; bij gelijke score op num, ook over de pagina's.

De grens van ronde A staat ruim: het gaat hier om wat er gevonden wordt (de rondes zelf:
test_search_deadline.py; een volle ronde van de garbage collector kan de grens van 120 ms
anders terecht raken, zie test_search_access.py).
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import analysis
from app.config import Settings
from app.db import get_engine
from app.models import Node, User
from app.search import bench
from app.search import engine as engines
from app.search.query import QueryTooLong
from app.search.types import SearchParams
from tests.factories import maker
from tests.search import referentie as R
from tests.search.migratie import MADE
from tests.search.test_referentie import _Verbinding
from tests.test_files import mkdir, put, root

pytestmark = pytest.mark.anyio

# Groter dan de boom: alles op één pagina
ALLES = 10_000
RUIM_MS = 60_000


@pytest.fixture(scope="module")
def boom(tmp_path_factory) -> tuple[Path, bench.Uitkomst, R.Referentie]:
    data = tmp_path_factory.mktemp("zoekreferentie")
    uit = bench.maak(data, R.BOOM)
    con = sqlite3.connect(data / "platform.db")
    try:
        ref = R.Referentie.lees(_Verbinding(con))
    finally:
        con.close()
    return data, uit, ref


async def zoek(db: AsyncSession, user: User, q: str):
    """FtsEngine zelf, met alles op één pagina (limit boven de 50 van de API: zonder controle
    gebouwd, zoals de router dat voor de vraag van 0.7.0 doet)."""
    p = SearchParams.model_construct(q=q, limit=ALLES, offset=0)
    return await engines.FtsEngine().search(db, user, p)


async def test_de_app_vindt_wat_de_referentie_zegt(boom, caplog):
    data, uit, ref = boom
    settings = Settings(data_dir=data, jwt_secret="test" * 8, search_deadline_ms=RUIM_MS)
    MADE.append(settings)
    caplog.set_level(logging.WARNING, logger="platform.search")
    token = engines.SETTINGS.set(settings)
    vragen = R.lees_vragen()
    treffers = namen = 0
    try:
        async with AsyncSession(get_engine(settings), expire_on_commit=False) as db:
            for rol, sleutel in uit.rollen.items():
                user = await db.get(User, uuid.UUID(uit.gebruikers[sleutel]["id"]))
                assert user is not None, rol
                for soort, q in vragen:
                    try:
                        verwacht = ref.zoek(user.id, q)
                    except R.TeLang:
                        with pytest.raises(QueryTooLong):
                            await zoek(db, user, q)
                        continue
                    page = await zoek(db, user, q)
                    ids = [h.node.id for h in page.results]
                    waar = (rol, soort, q)
                    assert page.engine == "fts" and not page.partial and not page.more, waar
                    assert len(ids) == len(set(ids)), waar
                    assert set(ids) == verwacht.alles, waar
                    # Eerst de hele woorden (ronde A), dan de stukken van namen (Keuze 6)
                    assert set(ids[: len(verwacht.woorden)]) == verwacht.woorden, waar
                    treffers += len(verwacht.woorden)
                    namen += len(verwacht.namen)
    finally:
        engines.SETTINGS.reset(token)
    # De vragen vinden echt iets, in beide delen
    assert treffers > 1_000 and namen > 100, (treffers, namen)
    assert "search_access_mismatch" not in caplog.text


# --- De volgorde ----------------------------------------------------------------------------


@pytest.fixture
def _ruim(settings, monkeypatch):
    monkeypatch.setattr(settings, "search_deadline_ms", RUIM_MS)


async def _namen(client, headers, q: str, **params) -> list[str]:
    r = await client.get("/api/v1/search", params={"q": q, **params}, headers=headers)
    assert r.status_code == 200, r.text
    page = r.json()
    assert page["engine"] == "fts" and page["partial"] is False and page["ranked"] is True
    return [h["node"]["name"] for h in page["results"]]


async def _tekst(settings, node_id: str, body: str) -> None:
    async with maker(settings)() as db:
        sha = await db.scalar(select(Node.blob_id).where(Node.id == uuid.UUID(node_id)))
        await analysis.write_text(db, sha, body=body)


@pytest.mark.usefixtures("_ruim")
async def test_de_naam_staat_boven_de_tekst(client, admin, settings):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    # De brief eerst: op num alleen zou hij bovenaan staan
    brief = (await put(client, headers, root_id, "brief.txt", b"brief")).json()["id"]
    await put(client, headers, root_id, "offerte.txt", b"offerte")
    await _tekst(settings, brief, "hierbij de offerte voor maart")
    assert await _namen(client, headers, "offerte") == ["offerte.txt", "brief.txt"]


@pytest.mark.usefixtures("_ruim")
async def test_een_heel_woord_staat_boven_een_stuk_van_een_naam(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    await put(client, headers, root_id, "jaarrapporten.pdf", b"%PDF-1.4\n1")
    await put(client, headers, root_id, "rapport.pdf", b"%PDF-1.4\n2")
    await put(client, headers, root_id, "rapporteur.txt", b"r")
    # rapport.pdf (heel woord) en rapporteur.txt (voorvoegsel) in ronde A, jaarrapporten.pdf
    # alleen als stuk van een naam: daarna
    namen = await _namen(client, headers, "rapport")
    assert namen[-1] == "jaarrapporten.pdf" and set(namen[:2]) == {"rapport.pdf", "rapporteur.txt"}
    # Een heel woord zonder voorvoegsel (de vraag eindigt op een spatie): alleen rapport.pdf in A
    assert await _namen(client, headers, "rapport ") == [
        "rapport.pdf",
        "jaarrapporten.pdf",
        "rapporteur.txt",
    ]


@pytest.mark.usefixtures("_ruim")
async def test_bij_gelijke_score_op_num_ook_over_de_paginas(client, admin):
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    ids = []
    for map_ in ("c", "a", "b"):
        folder = await mkdir(client, headers, root_id, map_)
        ids.append((await put(client, headers, folder, "gelijk.txt", b"x")).json()["id"])
    r = await client.get("/api/v1/search", params={"q": "gelijk"}, headers=headers)
    assert [h["node"]["id"] for h in r.json()["results"]] == ids
    # Pagina voor pagina dezelfde volgorde, en niets dubbel
    per_pagina = []
    for offset in range(3):
        r = await client.get(
            "/api/v1/search", params={"q": "gelijk", "limit": 1, "offset": offset}, headers=headers
        )
        page = r.json()
        per_pagina += [h["node"]["id"] for h in page["results"]]
        assert page["more"] is (offset < 2)
    assert per_pagina == ids
