"""De wereld van §4.2 (tests/search/wereld.py): staat hij er zoals beschreven, klopt hij met de
echte toegangsregels van de app (access.shared_roots), en geeft de referentie per persoon wat de
toegangsmatrix zegt. De tests op de app zelf (test_search_access.py en verder) komen in Z1, Z3
en Z6; die bouwen op deze wereld."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app import access, userdelete
from app.db import get_engine
from app.models import User
from tests.search import wereld
from tests.search.migratie import check_fts, check_tree
from tests.search.referentie import Referentie

pytestmark = pytest.mark.anyio

W = wereld


async def _lees(settings) -> Referentie:
    async with get_engine(settings).connect() as conn:
        return await conn.run_sync(Referentie.lees)


async def _doe(settings, fn, w) -> None:
    async with get_engine(settings).begin() as conn:
        await conn.run_sync(fn, w)


def _ids(w: wereld.Wereld, *sleutels: str) -> set[uuid.UUID]:
    return {w.ids[s] for s in sleutels}


async def test_de_wereld_staat_er(settings):
    w = await W.bouw_async(settings)
    async with get_engine(settings).connect() as conn:
        aantal = await conn.run_sync(check_tree)
        await conn.run_sync(check_fts)

        async def een(sql: str):
            return (await conn.exec_driver_sql(sql)).scalar()

        rollen = dict((await conn.exec_driver_sql('SELECT email, role FROM "user"')).all())
        assert rollen == {W.email(n): rol for n, (rol, _) in W.GEBRUIKERS.items()}
        # 9 hoofdmappen, 5 mappen, 15 bestanden en het album
        assert aantal == 9 + 5 + 15 + 1
        assert await een("SELECT count(*) FROM node WHERE dead = 1") == 1  # de prullenbak
        assert (
            await een(
                "SELECT count(*) FROM blob b WHERE ref_count <> "
                "(SELECT count(*) FROM node n WHERE n.blob_id = b.sha256)"
            )
            == 0
        )
        # Eva's foto, de kopie en Brams kopie: één inhoud, drie nodes
        assert await een(f"SELECT ref_count FROM blob WHERE sha256 = '{w.shas['foto']}'") == 3
        # De plek hoort bij de eigenaar van de node (Keuze 25a)
        assert (
            await een(
                "SELECT count(*) FROM photo_geo g JOIN node n ON n.id = g.node_id "
                "WHERE g.owner_id <> n.owner_id"
            )
            == 0
        )
        assert await een("SELECT count(*) FROM photo_geo") == 5
        assert await een("SELECT count(*) FROM share") == 9
        assert await een("SELECT count(*) FROM share WHERE token IS NOT NULL") == 2
        assert await een("SELECT count(*) FROM album_item WHERE shared = 1") == 1
        assert await een("SELECT count(*) FROM fts_node") == await een(
            "SELECT count(*) FROM search_src"
        )
    # Elk bestand staat in de BlobStore, met de inhoud die bij zijn sha hoort
    for sha in set(w.shas.values()):
        pad = settings.blob_dir / sha[:2] / sha[2:4] / sha
        assert pad.is_file(), sha


async def test_de_wereld_is_elke_keer_gelijk(settings, tmp_path):
    from app.config import Settings
    from app.migrate import upgrade

    een = await W.bouw_async(settings)
    ander = Settings(data_dir=tmp_path / "twee", web_dist=tmp_path / "web", jwt_secret="t" * 32)
    ander.data_dir.mkdir()
    await upgrade(ander)
    try:
        twee = await W.bouw_async(ander)
    finally:
        await get_engine(ander).dispose()
    assert (een.users, een.ids, een.shas, een.shares) == (twee.users, twee.ids, twee.shas,
                                                          twee.shares)  # fmt: skip


async def test_de_rollen_kloppen_met_de_app(settings):
    """De referentie rekent zelf uit wat iemand ziet; hier ligt hij naast access.shared_roots,
    de regel van de app sinds fase 2: dezelfde nodes (de eigen map erbij, behalve voor een gast;
    een gedeeld album niet: dat geeft niets om te zoeken)."""
    w = await W.bouw_async(settings)
    ref = await _lees(settings)
    levend = ref.zichtbaar(None)
    async with AsyncSession(get_engine(settings)) as session:
        for naam, user_id in w.users.items():
            wortels = {r.node_id for r in await access.shared_roots(session, user_id)}
            if W.GEBRUIKERS[naam][0] != "guest":
                wortels.add(w.roots[naam])
            app = {n for n in levend if wortels.intersection(ref.keten(n))}
            assert ref.zichtbaar(user_id) == app, naam
    # Bram: zijn eigen map, Werk, en Werk/Inbox apart (het dubbele pad); het album niet
    assert ref.wortels_van(w.users["bram"]) == {w.roots["bram"], w.ids["werk"], w.ids["inbox"]}
    assert ref.wortels_van(w.users["gijs"]) == {w.ids["werk"]}


# Wie wat vindt op het woord in de tekst (§4.2: ja, binnen Werk; nee is afwezig)
ZEBRA = {
    "eva": {"doc", "belasting"},
    "bram": {"doc"},
    "cas": {"doc"},
    "gijs": {"doc"},
    "dirk": {"dirk_notitie"},
    "ada": set(),
    "fien": set(),
    "hans": {"doc"},
    "iris": set(),
}


async def test_wie_wat_vindt(settings):
    w = await W.bouw_async(settings)
    ref = await _lees(settings)
    for naam, verwacht in ZEBRA.items():
        uit = ref.zoek(w.users[naam], W.TEKST_WOORD, nu=W.DAG)
        assert uit.woorden == _ids(w, *verwacht), naam
    eva, bram, cas = w.users["eva"], w.users["bram"], w.users["cas"]

    def vindt(user, q, **kw) -> set[uuid.UUID]:
        return ref.zoek(user, q, nu=W.DAG, **kw).alles

    # De OCR-tekst van een foto: alleen voor de eigenaar, ook niet op Brams eigen kopie
    assert vindt(eva, W.FOTO_OCR_WOORD) == _ids(w, "foto")
    assert vindt(bram, W.FOTO_OCR_WOORD) == set()
    assert vindt(cas, W.FOTO_OCR_WOORD) == set()
    # De prullenbak, de inbox (ook via het tweede pad) en de map die nog in Werk staat
    assert vindt(eva, W.PRULLENBAK_WOORD) == vindt(bram, W.PRULLENBAK_WOORD) == set()
    assert vindt(bram, W.INBOX_WOORD) == _ids(w, "brief")
    assert vindt(bram, W.VERHUIS_WOORD) == _ids(w, "plan")
    assert vindt(bram, W.PRIVE_WOORD) == set()
    # Namen: de foto's in Werk, en Brams eigen kopie alleen voor Bram
    foto_namen = _ids(w, "foto", "foto_kopie", "foto_klein")
    assert vindt(cas, "IMG_utrecht") == foto_namen
    assert vindt(bram, "utrecht") == foto_namen | _ids(w, "bram_kopie")
    assert vindt(eva, "utrecht") == foto_namen  # het album zelf is geen treffer
    # De scan heeft nog geen tekst (X1 leest het beeld): zijn woord vindt niets
    assert vindt(eva, W.OCR_WOORD) == set()
    # Alleen binnen Werk/Inbox
    assert vindt(bram, W.TEKST_WOORD, in_=w.ids["inbox"]) == set()
    assert vindt(bram, "brief", in_=w.ids["inbox"]) == _ids(w, "brief")


async def test_intrekken_en_verhuizen(settings):
    """Hans en de verplaatste map: weg in de eerstvolgende vraag."""
    w = await W.bouw_async(settings)
    await _doe(settings, W.intrekken, w)
    await _doe(settings, W.verhuis, w)
    async with get_engine(settings).connect() as conn:
        await conn.run_sync(check_tree)
        await conn.run_sync(check_fts)
    ref = await _lees(settings)
    hans, bram, eva = w.users["hans"], w.users["bram"], w.users["eva"]
    assert ref.zoek(hans, W.TEKST_WOORD, nu=W.DAG).alles == set()
    assert ref.zoek(bram, W.VERHUIS_WOORD, nu=W.DAG).alles == set()
    assert ref.zoek(eva, W.VERHUIS_WOORD, nu=W.DAG).alles == _ids(w, "plan")
    assert ref.zoek(eva, W.VERHUIS_WOORD, in_=w.ids["werk"], nu=W.DAG).alles == set()


async def test_fien_ziet_niets_meer_na_het_verlopen(settings):
    w = await W.bouw_async(settings)
    ref = await _lees(settings)
    fien = w.users["fien"]
    verlopen = W.DAG - timedelta(days=1)
    vroeger = verlopen - timedelta(seconds=1)
    assert ref.zoek(fien, W.TEKST_WOORD, nu=vroeger).woorden == _ids(w, "doc")
    assert ref.zoek(fien, W.TEKST_WOORD, nu=verlopen).woorden == set()


async def test_iris_wissen_laat_geen_personen_achter(settings):
    w = await W.bouw_async(settings)
    iris = w.users["iris"]
    async with get_engine(settings).begin() as conn:
        await conn.execute(sa.update(User).where(User.id == iris).values(disabled_at=W.DAG))
    await userdelete.run(settings, {"user_id": str(iris)})
    async with get_engine(settings).connect() as conn:

        async def een(sql: str) -> int:
            return (await conn.exec_driver_sql(sql, (iris.hex,))).scalar()

        assert await een('SELECT count(*) FROM "user" WHERE id = ?') == 0
        for tabel in ("person", "photo_person", "photo_geo", "node"):
            assert await een(f"SELECT count(*) FROM {tabel} WHERE owner_id = ?") == 0, tabel
        await conn.run_sync(check_tree)
        await conn.run_sync(check_fts)
        # Eva's persoon blijft
        assert (await conn.exec_driver_sql("SELECT count(*) FROM person")).scalar() == 1
