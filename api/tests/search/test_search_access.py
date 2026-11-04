"""Wat zoeken laat zien (bouwontwerp fase 6, §4.2; Keuze 14 en 15; app/search/access.py en
FtsEngine).

- De wereld van §4.2 (tests/search/wereld.py): voor elke persoon en elke vraag geeft
  GET /search (FtsEngine, alle pagina's) precies wat Referentie.zoek zegt (woorden en stukken
  van namen), ook binnen een map;
- de cellen van de matrix: de OCR van een foto alleen voor de eigenaar, de prullenbak, de
  verlopen en de ingetrokken deling, de verplaatste map (meteen weg), Dirk en Ada;
- de wortels: een wortel binnen een andere valt weg, een gedeeld album geeft niets, de gast
  zonder eigen hoofdmap; 64, 65 en 1.000 wortels (een OR tot en met 64, daarboven json_each);
- een deling die in hetzelfde verzoek wordt ingetrokken: de tweede controle haalt de treffer
  weg, met één logregel search_access_mismatch met het node-id (geen vraag, geen naam).
"""

from __future__ import annotations

import logging
import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker
from sqlalchemy.orm import Session

from app.db import get_engine
from app.models import Node, Share, User
from app.search import access, engine, limits
from app.search.access import MAX_OR, ranges_sql, search_ranges
from tests.search import wereld as W
from tests.search.referentie import Referentie

pytestmark = pytest.mark.anyio

# Vragen met voor bijna elke persoon een ander antwoord: namen, tekst, OCR, de prullenbak
VRAGEN = W.NAAMVRAGEN + (
    W.TEKST_WOORD,
    W.FOTO_OCR_WOORD,
    W.OCR_WOORD,
    W.PRULLENBAK_WOORD,
    W.VERHUIS_WOORD,
    W.INBOX_WOORD,
    W.PRIVE_WOORD,
    W.DIRK_WOORD,
    W.INBOX_SCAN_WOORD,
    "verslag kwartaal",
    '"verslag kwartaal"',
    "zebra",
    "bon",
    "utr",
)


@pytest.fixture(autouse=True)
def _zonder_snelheidsgrens(monkeypatch):
    """Deze tests doen honderden vragen per persoon; de snelheid per gebruiker (10 per
    seconde, Keuze 13) staat in test_search_limits.py."""
    monkeypatch.setattr(limits, "RATE", {"search": 100_000, "suggest": 100_000})


@pytest.fixture(autouse=True)
def _ruime_grens(settings, monkeypatch):
    """Het gaat hier om wat iemand vindt, niet om de tijd: een ruime grens voor ronde A. De
    grens van 120 ms is een klok aan de muur, en de garbage collector van Python legt het hele
    proces stil: in dit bestand gemeten tot 290 ms per volle ronde (±300.000 objecten), in een
    volledige run ±1 s (tests/test_search.py, measuring()). Valt zo'n ronde in ronde A, dan geeft
    die terecht een gedeeltelijke pagina (Keuze 9). De rondes zelf: test_search_deadline.py."""
    monkeypatch.setattr(settings, "search_deadline_ms", 10_000)


def sessions(settings):
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


async def _lees(settings) -> Referentie:
    async with get_engine(settings).connect() as conn:
        return await conn.run_sync(Referentie.lees)


async def _doe(settings, fn, *args) -> None:
    async with get_engine(settings).begin() as conn:
        await conn.run_sync(fn, *args)


async def kop(client, naam: str) -> dict[str, str]:
    login = {"email": W.email(naam), "password": W.WACHTWOORD}
    r = await client.post("/api/v1/auth/login", json=login)
    assert r.status_code == 200, (naam, r.text)
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def alles(client, headers, **params) -> list[uuid.UUID]:
    """Alle treffers, pagina na pagina (limit 50, offset tot 1.000), in volgorde."""
    uit: list[uuid.UUID] = []
    offset = 0
    while True:
        r = await client.get(
            "/api/v1/search", params={**params, "limit": 50, "offset": offset}, headers=headers
        )
        assert r.status_code == 200, (params, r.text)
        page = r.json()
        assert page["engine"] == "fts" and page["partial"] is False, page
        uit += [uuid.UUID(h["node"]["id"]) for h in page["results"]]
        if not page["more"]:
            return uit
        offset += 50
        assert offset <= 1000


def _ids(w: W.Wereld, *sleutels: str) -> set[uuid.UUID]:
    return {w.ids[s] for s in sleutels}


async def test_wat_ziet_wie_volgt_de_referentie(client, settings):
    w = await W.bouw_async(settings)
    ref = await _lees(settings)
    koppen = {naam: await kop(client, naam) for naam in W.GEBRUIKERS}
    for naam, headers in koppen.items():
        for q in VRAGEN:
            gevonden = await alles(client, headers, q=q)
            assert len(gevonden) == len(set(gevonden)), (naam, q)
            assert set(gevonden) == ref.zoek(w.users[naam], q).alles, (naam, q)
    # Binnen een map, ook een gedeelde (en de map zelf telt mee, zoals in de referentie)
    bram = koppen["bram"]
    for map_ in ("werk", "inbox", "fotos"):
        for q in ("utrecht", "brief", "a", W.TEKST_WOORD, "inbox", "werk", "foto"):
            gevonden = set(await alles(client, bram, q=q, **{"in": str(w.ids[map_])}))
            assert gevonden == ref.zoek(w.users["bram"], q, in_=w.ids[map_]).alles, (map_, q)


async def test_de_ocr_van_een_foto_is_alleen_van_de_eigenaar(client, settings):
    """Bram, Cas en Gijs zien de foto, maar vinden hem niet op bonwoordy; Eva wel. Brams eigen
    kopie heeft geen OCR-tekst."""
    w = await W.bouw_async(settings)
    eva = await kop(client, "eva")
    assert set(await alles(client, eva, q=W.FOTO_OCR_WOORD)) == _ids(w, "foto")
    assert set(await alles(client, eva, q=W.FOTO_OCR_WOORD[:5])) >= _ids(w, "foto")
    for naam in ("bram", "cas", "gijs"):
        headers = await kop(client, naam)
        assert await alles(client, headers, q=W.FOTO_OCR_WOORD) == [], naam
        assert w.ids["foto"] in await alles(client, headers, q="IMG_utrecht"), naam
        # Ook samen met een woord dat er wel in staat, of als frase, niet
        assert await alles(client, headers, q=f"img {W.FOTO_OCR_WOORD}") == [], naam
        assert await alles(client, headers, q=f'"{W.FOTO_OCR_WOORD}"') == [], naam


async def test_de_cellen_van_de_matrix(client, settings):
    w = await W.bouw_async(settings)
    zebra = {
        "eva": _ids(w, "doc", "belasting"),
        "bram": _ids(w, "doc"),
        "cas": _ids(w, "doc"),
        "gijs": _ids(w, "doc"),
        "dirk": _ids(w, "dirk_notitie"),
        "ada": set(),
        "fien": set(),  # verlopen
        "hans": _ids(w, "doc"),
        "iris": set(),
    }
    koppen = {naam: await kop(client, naam) for naam in W.GEBRUIKERS}
    for naam, verwacht in zebra.items():
        assert set(await alles(client, koppen[naam], q=W.TEKST_WOORD)) == verwacht, naam
    for naam, headers in koppen.items():
        # De prullenbak: voor niemand, ook niet voor Eva
        assert await alles(client, headers, q=W.PRULLENBAK_WOORD) == [], naam
        # Een album is nooit een treffer
        assert w.ids["album"] not in await alles(client, headers, q="utrecht"), naam


async def test_intrekken_en_verhuizen_tellen_meteen(client, settings):
    """De eerstvolgende vraag na het intrekken van de deling met Hans, en na het verplaatsen
    van de map uit Werk: geen treffers meer (de bereiken worden per verzoek gemaakt)."""
    w = await W.bouw_async(settings)
    hans, bram, eva = [await kop(client, naam) for naam in ("hans", "bram", "eva")]
    assert set(await alles(client, hans, q=W.TEKST_WOORD)) == _ids(w, "doc")
    assert set(await alles(client, bram, q=W.VERHUIS_WOORD)) == _ids(w, "plan")
    await _doe(settings, W.intrekken, w)
    await _doe(settings, W.verhuis, w)
    assert await alles(client, hans, q=W.TEKST_WOORD) == []
    assert await alles(client, bram, q=W.VERHUIS_WOORD) == []
    assert set(await alles(client, eva, q=W.VERHUIS_WOORD)) == _ids(w, "plan")
    werk = {"in": str(w.ids["werk"])}
    assert await alles(client, eva, q=W.VERHUIS_WOORD, **werk) == []


async def test_een_deling_ingetrokken_in_hetzelfde_verzoek(
    client, settings, monkeypatch, caplog, mismatch_verwacht
):
    """De deling met Hans verdwijnt nadat de bereiken gemaakt zijn, nog vóór de vraag aan FTS:
    de tweede controle (access.resolve) haalt de treffer weg. Eén logregel per node, met alleen
    het node-id."""
    w = await W.bouw_async(settings)
    hans = await kop(client, "hans")
    echt = engine.search_ranges

    async def en_dan_intrekken(session, user):
        roots = await echt(session, user)
        if user.id == w.users["hans"]:
            await _doe(settings, W.intrekken, w)
        return roots

    monkeypatch.setattr(engine, "search_ranges", en_dan_intrekken)
    caplog.set_level(logging.INFO, logger="platform.search")
    r = await client.get("/api/v1/search", params={"q": W.TEKST_WOORD}, headers=hans)
    assert r.status_code == 200, r.text
    assert r.json()["results"] == []
    regels = [rec.getMessage() for rec in caplog.records if rec.name == "platform.search"]
    assert regels == [f"search_access_mismatch {w.ids['doc']}"]
    assert all(W.TEKST_WOORD not in m and "Verslag" not in m for m in caplog.messages)


async def test_de_wortels_van_iedereen(settings):
    """Een wortel binnen een andere valt weg (Werk/Inbox in Werk), een album geeft niets, een
    gast heeft geen eigen hoofdmap, Fien (verlopen) alleen haar eigen."""
    w = await W.bouw_async(settings)
    async with sessions(settings)() as db:
        paden = {
            row.id: row.mpath
            for row in await db.execute(sa.select(Node.__table__.c.id, Node.__table__.c.mpath))
        }

        async def wortels(naam: str) -> access.Ranges:
            user = await db.get(User, w.users[naam])
            return await search_ranges(db, user)

        def bereik(*ids: uuid.UUID) -> list[tuple[str, str]]:
            return sorted((paden[i], access.end_of(paden[i])) for i in ids)

        assert await wortels("eva") == access.Ranges(bereik(w.roots["eva"]), False)
        werk = w.ids["werk"]
        assert await wortels("bram") == access.Ranges(bereik(w.roots["bram"], werk), True)
        assert await wortels("cas") == access.Ranges(bereik(w.roots["cas"], werk), True)
        assert await wortels("gijs") == access.Ranges(bereik(werk), True)
        assert await wortels("fien") == access.Ranges(bereik(w.roots["fien"]), False)
        assert await wortels("dirk") == access.Ranges(bereik(w.roots["dirk"]), False)


def test_de_vorm_van_de_bereiken():
    een = [("/1/", "/10")]
    sql, params = ranges_sql(een)
    assert sql == "((n.mpath >= :rp0 AND n.mpath < :re0))"
    assert params == {"rp0": "/1/", "re0": "/10"}
    veel = [(f"/{i}/", f"/{i}0") for i in range(MAX_OR)]
    sql, params = ranges_sql(veel, alias="m")
    assert sql.count(" OR ") == MAX_OR - 1 and "json_each" not in sql and "m.mpath" in sql
    assert len(params) == 2 * MAX_OR
    meer = veel + [("/999/", "/9990")]
    sql, params = ranges_sql(meer)
    assert sql.startswith("EXISTS (SELECT 1 FROM json_each(:ranges)") and " OR " not in sql
    assert set(params) == {"ranges"}
    assert ranges_sql([]) == ("0", {})
    assert access.end_of("/1/7/42/") == "/1/7/420"


def _deel_mappen(conn, w: W.Wereld, n: int) -> list[uuid.UUID]:
    """n mappen van Eva in haar eigen map, elk apart gedeeld met Bram (lezen)."""
    s = Session(bind=conn)
    ids = []
    for i in range(n):
        node = Node(parent_id=w.roots["eva"], owner_id=w.users["eva"], name=f"doelmap{i:04}",
                    kind="dir", created_at=W.DAG, modified_at=W.DAG)  # fmt: skip
        s.add(node)
        s.flush()
        s.add(Share(node_id=node.id, owner_id=w.users["eva"], grantee_id=w.users["bram"],
                    permission="read", created_at=W.DAG))  # fmt: skip
        ids.append(node.id)
    # In de laatste een map: wat type=folder met in van die map vindt
    s.add(Node(parent_id=ids[-1], owner_id=w.users["eva"], name="binnen", kind="dir",
               created_at=W.DAG, modified_at=W.DAG))  # fmt: skip
    s.flush()
    return ids


def _binnen(conn, map_id: uuid.UUID) -> uuid.UUID:
    s = Session(bind=conn)
    return s.scalars(select(Node.id).where(Node.parent_id == map_id)).one()


@pytest.mark.parametrize("wortels", [64, 65, 1000])
async def test_veel_wortels(client, settings, wortels):
    """Bram: zijn eigen hoofdmap, Werk en n - 2 losse mappen van Eva. Tot en met 64 een OR,
    daarboven json_each; hij vindt precies de gedeelde mappen. Met 1.000 wortels en 1.000
    treffers kost ronde A ±220 ms (json_each met ->> per rij): ook daarom de ruime grens van
    _ruime_grens."""
    w = await W.bouw_async(settings)
    async with get_engine(settings).begin() as conn:
        mappen = await conn.run_sync(_deel_mappen, w, wortels - 2)
    async with sessions(settings)() as db:
        bram = await db.get(User, w.users["bram"])
        roots = await search_ranges(db, bram)
    assert len(roots.ranges) == wortels and roots.shared
    sql, _ = ranges_sql(roots.ranges)
    assert ("json_each" in sql) is (wortels > MAX_OR)
    bram_kop = await kop(client, "bram")
    gevonden = await alles(client, bram_kop, q="doelmap")
    assert sorted(gevonden, key=str) == sorted(mappen, key=str)
    ref = await _lees(settings)
    assert set(gevonden) == ref.zoek(w.users["bram"], "doelmap").alles
    # Alleen filters (Keuze 4a): ook over alle wortels, en met in alleen wat in die ene map
    # ligt, zonder de map zelf (review golf 1, contract N1)
    async with get_engine(settings).begin() as conn:
        binnen = await conn.run_sync(_binnen, mappen[-1])
    r = await client.get(
        "/api/v1/search",
        params={"type": "folder", "in": str(mappen[-1])},
        headers=bram_kop,
    )
    assert [h["node"]["id"] for h in r.json()["results"]] == [str(binnen)]


async def test_fien_ziet_niets_na_het_verlopen(client, settings):
    """De deling met Fien verliep gisteren (DAG - 1 dag): zoeken telt hem niet."""
    w = await W.bouw_async(settings)
    async with get_engine(settings).connect() as conn:
        verloopt = (
            await conn.execute(
                sa.select(Share.expires_at).where(Share.grantee_id == w.users["fien"])
            )
        ).scalar_one()
    assert verloopt is not None
    fien = await kop(client, "fien")
    assert await alles(client, fien, q=W.TEKST_WOORD) == []
    assert await alles(client, fien, q="verslag") == []
    assert verloopt.replace(tzinfo=None) <= (W.DAG - timedelta(days=1)).replace(tzinfo=None)
