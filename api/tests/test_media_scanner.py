"""De scanner (Keuze 5–9, M2): van een wijziging in de boom tot een item, in stukken.

De lus draait hier niet (geen lifespan): elke test roept scanner.tick aan (wat de lus elke
media_scan_tick doet) en draait daarna de rij media_scan leeg. De worker is de nep van M0; de
opdracht nfo krijgt de echte lezer van de worker (worker/ops/nfo.py) als antwoord.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import time
import uuid

import pytest
from sqlalchemy import func, select, update

from app import jobs
from app.main import app
from app.media import analysis, classify, hooks, names, scanner
from app.media import client as media_client
from app.media.wire import Code as wire_code
from app.media.wire import Op, WorkClass
from app.media.worker.ops import nfo as nfo_op
from app.models import Blob, Job, MediaItem, MediaLibrary, MediaShow, Node, PlaybackState
from tests.test_files import mkdir, put, root
from tests.test_media_contract import media_app  # noqa: F401
from tests.test_media_sessions import content, maker

pytestmark = pytest.mark.anyio


@pytest.fixture
async def world(media_app, client, admin):  # noqa: F811
    settings, fake = media_app
    scanner.reset()
    headers, _ = admin
    yield settings, fake, headers
    scanner.reset()


async def library(client, headers, name="Films", kind="movies", parent=None) -> tuple[str, str]:
    """Een map met een bibliotheek erop: (library_id, map)."""
    parent = parent or (await root(client, headers))["folder"]["id"]
    folder = await mkdir(client, headers, parent, name)
    r = await client.post(
        "/api/v1/media/libraries", json={"root_id": folder, "kind": kind}, headers=headers
    )
    assert r.status_code == 201, r.text
    return r.json()["id"], folder


async def upload(client, headers, parent: str, name: str, data: bytes | None = None) -> str:
    r = await put(client, headers, parent, name, data if data is not None else content(name, 2000))
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def scan(settings) -> int:
    """Wat de lus doet, en dan de rij media_scan leeg: het aantal gedraaide jobs."""
    await scanner.tick(settings)
    return await jobs.run_pending(settings, lane="media_scan")


async def items(settings, library_id: str) -> dict[str, MediaItem]:
    """De items van een bibliotheek per naam van het bestand."""
    async with maker(settings)() as session:
        rows = await session.execute(
            select(MediaItem, Node.name)
            .join(Node, Node.id == MediaItem.node_id)
            .where(MediaItem.library_id == uuid.UUID(library_id))
        )
        return {name: item for item, name in rows}


async def analyses(settings) -> dict[str, dict]:
    async with maker(settings)() as session:
        rows = await session.scalars(select(Job).where(Job.type == analysis.JOB))
        return {job.payload["sha"]: job.payload for job in rows}


async def nfo_handler(job):
    data = os.pread(job.inputs[0], job.args.max_bytes + 1, 0)
    return nfo_op.parse(data)


async def test_een_nieuw_bestand_wordt_een_item(world, client):
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    film = await mkdir(client, headers, folder, "The Matrix (1999)")
    node = await upload(client, headers, film, "The Matrix (1999) [imdbid-tt0133093].mkv")
    await upload(client, headers, film, "lees mij.txt")
    assert await scan(settings) >= 1
    found = await items(settings, library_id)
    assert set(found) == {"The Matrix (1999) [imdbid-tt0133093].mkv"}
    item = found["The Matrix (1999) [imdbid-tt0133093].mkv"]
    assert item.node_id == uuid.UUID(node)
    assert (item.title, item.year, item.kind, item.present) == ("The Matrix", 1999, "movie", True)
    assert item.ids == {"imdb": "tt0133093"}
    assert item.sort_title == "matrix"
    assert item.source == "name"
    # De analyse staat klaar in de rij media, met de bibliotheek erbij
    queued = await analyses(settings)
    assert len(queued) == 1
    [payload] = queued.values()
    assert payload["library_id"] == library_id
    assert jobs.lane_of(analysis.JOB) == "media"
    async with maker(settings)() as session:
        lib = await session.get(MediaLibrary, uuid.UUID(library_id))
        assert (lib.scan_state, lib.item_count) == ("idle", 1)
        assert lib.scanned_at is not None


async def test_zonder_bibliotheek_doet_touched_niets(world, client):
    settings, _, headers = world
    parent = (await root(client, headers))["folder"]["id"]
    await upload(client, headers, parent, "los.mkv")
    assert not scanner._pending
    assert await scanner.tick(settings) == 0
    scanner.activate()
    hooks.touched([uuid.uuid4()], "node.created")
    assert len(scanner._pending) == 1


async def test_hernoemd_en_verplaatst_binnen_houdt_item_en_voortgang(world, client):
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    a = await mkdir(client, headers, folder, "A")
    b = await mkdir(client, headers, folder, "B")
    node = await upload(client, headers, a, "Oud (2001).mkv")
    await scan(settings)
    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]
    async with maker(settings)() as session:
        session.add(
            PlaybackState(
                user_id=uuid.UUID(user_id),
                profile="",
                node_id=uuid.UUID(node),
                position_ms=60_000,
                duration_ms=600_000,
            )  # fmt: skip
        )
        await session.commit()
    r = await client.patch(
        f"/api/v1/files/{node}", json={"name": "Nieuw (2002).mkv"}, headers=headers
    )
    assert r.status_code == 200, r.text
    await scan(settings)
    found = await items(settings, library_id)
    assert (found["Nieuw (2002).mkv"].title, found["Nieuw (2002).mkv"].year) == ("Nieuw", 2002)
    r = await client.patch(f"/api/v1/files/{node}", json={"parent_id": b}, headers=headers)
    assert r.status_code == 200, r.text
    await scan(settings)
    found = await items(settings, library_id)
    assert list(found) == ["Nieuw (2002).mkv"]
    assert found["Nieuw (2002).mkv"].node_id == uuid.UUID(node)
    async with maker(settings)() as session:
        state = await session.get(PlaybackState, (uuid.UUID(user_id), "", uuid.UUID(node)))
        assert state is not None and state.position_ms == 60_000


async def test_naar_buiten_verplaatst_haalt_de_rij_weg_en_de_voortgang_blijft(world, client):
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    outside = await mkdir(client, headers, (await root(client, headers))["folder"]["id"], "Elders")
    sub = await mkdir(client, headers, folder, "Map")
    node = await upload(client, headers, sub, "Film (2010).mkv")
    other = await upload(client, headers, folder, "Los (2011).mkv")
    await scan(settings)
    assert len(await items(settings, library_id)) == 2
    # Een bestand naar buiten
    r = await client.patch(f"/api/v1/files/{other}", json={"parent_id": outside}, headers=headers)
    assert r.status_code == 200
    await scan(settings)
    assert set(await items(settings, library_id)) == {"Film (2010).mkv"}
    # Een map naar buiten: het item eronder gaat ook weg
    r = await client.patch(f"/api/v1/files/{sub}", json={"parent_id": outside}, headers=headers)
    assert r.status_code == 200
    await scan(settings)
    assert await items(settings, library_id) == {}
    async with maker(settings)() as session:
        assert await session.get(Node, uuid.UUID(node)) is not None
        lib = await session.get(MediaLibrary, uuid.UUID(library_id))
        assert lib.item_count == 0


async def test_prullenbak_en_terugzetten(world, client):
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    sub = await mkdir(client, headers, folder, "Film (2010)")
    node = await upload(client, headers, sub, "Film (2010).mkv")
    await scan(settings)
    user_id = (await client.get("/api/v1/auth/me", headers=headers)).json()["id"]
    async with maker(settings)() as session:
        session.add(
            PlaybackState(user_id=uuid.UUID(user_id), profile="", node_id=uuid.UUID(node),
                          position_ms=5000, duration_ms=9000)
        )  # fmt: skip
        await session.commit()
    # De map erboven naar de prullenbak: present uit, de rij en de voortgang blijven
    assert (await client.delete(f"/api/v1/files/{sub}", headers=headers)).status_code == 204
    await scan(settings)
    item = (await items(settings, library_id))["Film (2010).mkv"]
    assert item.present is False
    r = await client.get(f"/api/v1/media/items/{node}", headers=headers)
    assert r.status_code == 404
    page = (await client.get(f"/api/v1/media/libraries/{library_id}/items", headers=headers)).json()
    assert page["items"] == []
    # Terugzetten: present weer aan, dezelfde rij
    assert (await client.post(f"/api/v1/files/{sub}/restore", headers=headers)).status_code == 200
    await scan(settings)
    item = (await items(settings, library_id))["Film (2010).mkv"]
    assert item.present is True
    r = await client.get(f"/api/v1/media/items/{node}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["progress"] == {"position_ms": 5000, "duration_ms": 9000}
    # Het bestand zelf in de prullenbak (en terug)
    assert (await client.delete(f"/api/v1/files/{node}", headers=headers)).status_code == 204
    await scan(settings)
    assert (await items(settings, library_id))["Film (2010).mkv"].present is False
    assert (await client.post(f"/api/v1/files/{node}/restore", headers=headers)).status_code == 200
    await scan(settings)
    assert (await items(settings, library_id))["Film (2010).mkv"].present is True


async def test_de_bibliotheek_zelf_in_de_prullenbak(world, client):
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    await upload(client, headers, folder, "Film (2010).mkv")
    await scan(settings)
    assert (await client.delete(f"/api/v1/files/{folder}", headers=headers)).status_code == 204
    await scan(settings)
    assert (await items(settings, library_id))["Film (2010).mkv"].present is False
    r = await client.get("/api/v1/media/libraries", headers=headers)
    assert r.json() == []  # verborgen zolang de map in de prullenbak ligt
    assert (
        await client.post(f"/api/v1/files/{folder}/restore", headers=headers)
    ).status_code == 200
    await scan(settings)
    assert (await items(settings, library_id))["Film (2010).mkv"].present is True


async def test_een_nieuwe_versie_geeft_opnieuw_analyse(world, client):
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    node = await upload(client, headers, folder, "Film (2010).mkv", b"eerste versie" * 100)
    await scan(settings)
    first = set(await analyses(settings))
    r = await client.put(
        f"/api/v1/files/{node}/content", content=b"tweede versie" * 100, headers=headers
    )
    assert r.status_code == 200, r.text
    await scan(settings)
    after = await analyses(settings)
    assert len(after) == 2 and first < set(after)
    assert all(p["library_id"] == library_id for p in after.values())
    assert list(await items(settings, library_id)) == ["Film (2010).mkv"]


async def all_done(settings) -> None:
    """Alle analyses klaar, zonder ze te draaien."""
    async with maker(settings)() as session:
        await session.execute(update(Job).where(Job.type == analysis.JOB).values(status="done"))
        await session.commit()


async def queued_analyses(settings) -> list[dict]:
    async with maker(settings)() as session:
        rows = await session.scalars(
            select(Job).where(Job.type == analysis.JOB, Job.status.in_(jobs.ACTIVE))
        )
        return [job.payload for job in rows]


async def test_een_probe_die_er_al_is_geeft_de_stappen_van_de_bibliotheek(world, client):
    """Een video die al geprobed is (play buiten een bibliotheek, zoals de viewer van M6) en dan
    in een bibliotheek komt: toch de analyse, met de bibliotheek, anders komen subs en sprites
    er nooit. Eén keer: een volgende scan van hetzelfde item zet niets meer klaar."""
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    node = await upload(client, headers, folder, "Film (2010).mkv")
    async with maker(settings)() as session:
        sha = (await session.get(Node, uuid.UUID(node))).blob_id
    await analysis.save_probe(maker(settings), sha, analysis.Probe(duration_ms=1000))
    await scan(settings)
    assert await queued_analyses(settings) == [{"sha": sha, "library_id": library_id}]
    await all_done(settings)
    r = await client.patch(
        f"/api/v1/files/{node}", json={"name": "Film (2011).mkv"}, headers=headers
    )
    assert r.status_code == 200, r.text
    await scan(settings)
    assert (await items(settings, library_id))["Film (2011).mkv"].year == 2011
    assert await queued_analyses(settings) == []


async def test_naar_een_andere_bibliotheek_geeft_de_analyse_met_die_bibliotheek(world, client):
    """Verhuisd uit een bibliotheek met films naar een met eigen video's: de analyse opnieuw met
    de nieuwe bibliotheek (de stappen hangen van het soort af), ook al is de probe er."""
    settings, _, headers = world
    _, films = await library(client, headers)
    videos_id, videos = await library(client, headers, name="Video's", kind="videos")
    node = await upload(client, headers, films, "Film (2010).mkv")
    async with maker(settings)() as session:
        sha = (await session.get(Node, uuid.UUID(node))).blob_id
    await analysis.save_probe(maker(settings), sha, analysis.Probe(duration_ms=1000))
    await scan(settings)
    await all_done(settings)
    r = await client.patch(f"/api/v1/files/{node}", json={"parent_id": videos}, headers=headers)
    assert r.status_code == 200, r.text
    await scan(settings)
    assert list(await items(settings, videos_id)) == ["Film (2010).mkv"]
    assert await queued_analyses(settings) == [{"sha": sha, "library_id": videos_id}]


async def test_een_bibliotheek_in_een_andere_geschoven(world, client):
    """C2 (review golf 3): de map van een bibliotheek geschoven in die van een andere. Het
    maken weigert dat (409), maar verplaatsen kan nog. De binnenste wint: de items blijven
    van haar, ook na volledige rondes van beide om beurten (eerst wisselden ze bij elke ronde
    van bibliotheek, en ging de rij met added_at elke keer weg)."""
    settings, _, headers = world
    films_id, films = await library(client, headers)
    kids_id, kids = await library(client, headers, name="Kids")
    nemo = await mkdir(client, headers, kids, "Nemo (2003)")
    node = await upload(client, headers, nemo, "Nemo (2003).mkv")
    await upload(client, headers, films, "Heat (1995).mkv")
    await scan(settings)
    [before] = (await items(settings, kids_id)).values()
    r = await client.patch(f"/api/v1/files/{kids}", json={"parent_id": films}, headers=headers)
    assert r.status_code == 200, r.text
    await scan(settings)
    for library_id in (films_id, kids_id, films_id, kids_id):
        r = await client.post(f"/api/v1/media/libraries/{library_id}/scan", headers=headers)
        assert r.status_code in (200, 202), r.text
        await jobs.run_pending(settings, lane="media_scan")
        assert list(await items(settings, films_id)) == ["Heat (1995).mkv"]
        found = await items(settings, kids_id)
        assert list(found) == ["Nemo (2003).mkv"]
        assert found["Nemo (2003).mkv"].node_id == uuid.UUID(node)
        assert found["Nemo (2003).mkv"].added_at == before.added_at
    async with maker(settings)() as session:
        counts = dict(
            (await session.execute(select(MediaLibrary.id, MediaLibrary.item_count))).all()
        )
    assert (counts[uuid.UUID(films_id)], counts[uuid.UUID(kids_id)]) == (1, 1)


async def test_play_zette_de_analyse_klaar_zonder_bibliotheek(world, client):
    """Play van een bestand dat nog geen item was: de job staat er zonder library_id. De scan
    zet die er alsnog bij (de stappen subs en sprites zien anders het soort niet)."""
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    node = await upload(client, headers, folder, "Film (2010).mkv")
    async with maker(settings)() as session:
        sha = (await session.get(Node, uuid.UUID(node))).blob_id
        await analysis.enqueue(session, sha, library_id=None)
        await session.commit()
    await scan(settings)
    assert (await analyses(settings))[sha]["library_id"] == library_id


async def test_play_analyseert_al_zonder_bibliotheek(world, client):
    """C5 (review golf 3): de analyse zonder library_id loopt al (play) als de scan het item
    maakt. enqueue ziet de lopende job en zet niets klaar, en die job ziet het soort niet: de
    stappen van de bibliotheek kwamen er nooit. Nu een opvolger met de bibliotheek erbij."""
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    node = await upload(client, headers, folder, "Film (2010).mkv")
    async with maker(settings)() as session:
        sha = (await session.get(Node, uuid.UUID(node))).blob_id
        await analysis.enqueue(session, sha, library_id=None)
        await session.commit()
        await session.execute(update(Job).where(Job.type == analysis.JOB).values(status="running"))
        await session.commit()
    await scan(settings)
    async with maker(settings)() as session:
        rows = (await session.scalars(select(Job).where(Job.type == analysis.JOB))).all()
    assert sorted((j.status, j.payload["library_id"] or "") for j in rows) == [
        ("queued", library_id),
        ("running", ""),
    ]
    # Een tweede scan zet er geen derde bij
    async with maker(settings)() as session:
        await scanner._queue_analysis(session, uuid.UUID(library_id), [sha])
        await session.commit()
    assert len(await queued_analyses(settings)) == 2


async def test_locked_blijft_staan(world, client):
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    node = await upload(client, headers, folder, "Film (2010).mkv")
    await scan(settings)
    async with maker(settings)() as session:
        await session.execute(
            update(MediaItem)
            .where(MediaItem.node_id == uuid.UUID(node))
            .values(title="Mijn titel", sort_title="mijn titel", locked=["title", "sort_title"])
        )
        await session.commit()
    r = await client.patch(
        f"/api/v1/files/{node}", json={"name": "Ander (1990).mkv"}, headers=headers
    )
    assert r.status_code == 200
    await scan(settings)
    item = (await items(settings, library_id))["Ander (1990).mkv"]
    assert (item.title, item.sort_title, item.year) == ("Mijn titel", "mijn titel", 1990)


async def test_2000_bestanden_in_stukken(world, client, monkeypatch):
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    sample = await upload(client, headers, folder, "eerste.mkv")
    big = await mkdir(client, headers, folder, "Groot")
    small = [await mkdir(client, headers, folder, f"Map {i}") for i in range(4)]
    async with maker(settings)() as session:
        first = await session.get(Node, uuid.UUID(sample))
        owner, sha = first.owner_id, first.blob_id
        rows = [(big, f"Film {i:04d} ({1950 + i % 70}).mkv") for i in range(1200)]
        rows += [(small[i % 4], f"Ander {i:04d}.mp4") for i in range(799)]
        for parent, name in rows:
            session.add(
                Node(parent_id=uuid.UUID(parent), owner_id=owner, name=name, kind="file",
                     mime="video/x-matroska", size=1, blob_id=sha)
            )  # fmt: skip
        await session.commit()
    runs: list[dict] = []
    real = scanner.run

    async def counted(settings, payload):
        runs.append(dict(payload))
        await real(settings, payload)

    kind = dataclasses.replace(jobs._kinds[scanner.JOB], run=counted)
    monkeypatch.setitem(jobs._kinds, scanner.JOB, kind)
    r = await client.post(f"/api/v1/media/libraries/{library_id}/scan", headers=headers)
    assert r.status_code == 202
    started = time.monotonic()
    await jobs.run_pending(settings, lane="media_scan")
    took = time.monotonic() - started
    found = await items(settings, library_id)
    assert len(found) == 2000
    assert len(runs) >= 4  # 2.000 bestanden, hooguit 500 per stuk
    assert all(r.get("full") for r in runs)
    # De grote map ging in delen (een map met meer dan 500 bestanden)
    assert any(r.get("after") and r["after"][1] > 0 for r in runs[1:])
    assert took < 120, took  # Klaar als: 1.000 bestanden ≤ 60 s (zonder analyse)
    async with maker(settings)() as session:
        lib = await session.get(MediaLibrary, uuid.UUID(library_id))
        assert lib.item_count == 2000 and lib.scan_state == "idle"


async def test_de_rest_van_een_volledige_ronde_gaat_niet_verloren(world, client, monkeypatch):
    """Review golf 3, C1: een ronde in stukken zette zijn rest met jobs.chain klaar, en die
    ontdubbelt tegen een scan die al klaarstaat. Kwam er tijdens stuk 1 een upload (en een
    tik), dan won die scan met alleen zijn eigen nodes, en viel de rest weg: 500 van de 1.202
    bestanden tot de ronde van 6 uur, en scan_state idle alsof de ronde klaar was."""
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    sample = await upload(client, headers, folder, "eerste.mkv")
    big = await mkdir(client, headers, folder, "Groot")
    await scan(settings)  # de eerste ronde (bij het maken), en de set van touched is leeg
    async with maker(settings)() as session:
        first = await session.get(Node, uuid.UUID(sample))
        for i in range(1200):  # zonder de haak: alleen een volledige ronde ziet ze
            session.add(
                Node(parent_id=uuid.UUID(big), owner_id=first.owner_id, name=f"Film {i:04d}.mkv",
                     kind="file", mime="video/x-matroska", size=1, blob_id=first.blob_id)
            )  # fmt: skip
        await session.commit()
    runs: list[dict] = []
    real = scanner.run

    async def counted(settings, payload):
        runs.append(dict(payload))
        if len(runs) == 1:  # tijdens stuk 1: een upload en een tik zetten een scan klaar
            await upload(client, headers, folder, "tussendoor.mkv")
            await scanner.tick(settings)
        await real(settings, payload)

    kind = dataclasses.replace(jobs._kinds[scanner.JOB], run=counted)
    monkeypatch.setitem(jobs._kinds, scanner.JOB, kind)
    r = await client.post(f"/api/v1/media/libraries/{library_id}/scan", headers=headers)
    assert r.status_code == 202
    await jobs.run_pending(settings, lane="media_scan")
    found = await items(settings, library_id)
    assert len(found) == 1202, (len(found), runs)
    assert runs[1].get("nodes") and runs[1].get("full") and runs[1]["after"][1] > 0
    async with maker(settings)() as session:
        lib = await session.get(MediaLibrary, uuid.UUID(library_id))
        assert lib.item_count == 1202 and lib.scan_state == "idle"


def test_merge_van_de_rest_van_een_ronde():
    """C1: de rest van een ronde (full, after en mappen met een plek) in een scan die al
    klaarstaat; een nieuwe volledige ronde begint weer vooraan."""
    queued = {"library_id": "x", "nodes": ["n1"]}
    out = scanner._merge(queued, full=True, after=["k", 500], parts=[["d1", 20]])
    assert out == {"library_id": "x", "nodes": ["n1"], "full": True, "after": ["k", 500],
                   "dirs": [["d1", 20]]}  # fmt: skip
    assert scanner._merge(out, full=True, after=["j", 0])["after"] == ["j", 0]
    assert scanner._merge(out, full=True, after=["z", 0])["after"] == ["k", 500]
    assert scanner._merge(out, full=True)["after"] is None  # opnieuw scannen: vanaf het begin
    assert scanner._merge(out, parts=[["d1", 5]])["dirs"] == [["d1", 5]]


async def test_een_externe_ondertitel_hoort_bij_de_goede_video(world, client):
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    film = await mkdir(client, headers, folder, "Film (2010)")
    await upload(client, headers, film, "Film (2010).mkv")
    await upload(client, headers, film, "Film (2010) - Director's Cut.mkv")
    forced = await upload(client, headers, film, "Film (2010) - Director's Cut.nl.forced.srt", b"1")
    plain = await upload(client, headers, film, "Film (2010).en.srt", b"2")
    await scan(settings)
    found = await items(settings, library_id)
    cut = found["Film (2010) - Director's Cut.mkv"]
    assert cut.subs == [
        {"node": forced, "lang": "nld", "forced": True, "sdh": False, "default": False}
    ]
    assert found["Film (2010).mkv"].subs == [
        {"node": plain, "lang": "eng", "forced": False, "sdh": False, "default": False}
    ]
    # Een ondertitel erbij: de map wordt opnieuw gescand
    sdh = await upload(client, headers, film, "Film (2010).nl.sdh.srt", b"3")
    await scan(settings)
    subs = (await items(settings, library_id))["Film (2010).mkv"].subs
    assert [s["node"] for s in subs] == [plain, sdh]
    # Op de pagina van het item: x0 en x1, in die volgorde
    r = await client.get(f"/api/v1/media/items/{found['Film (2010).mkv'].node_id}", headers=headers)
    assert [(s["id"], s["lang"], s["sdh"]) for s in r.json()["subtitles"]] == [
        ("x0", "eng", False),
        ("x1", "nld", True),
    ]


async def test_een_crash_en_dan_de_ronde_bij_de_start(world, client):
    """De set gaat verloren (een crash tussen de commit en de lus): media.reconcile bij de
    start vindt alles alsnog."""
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    await jobs.run_pending(settings, lane="media_scan")  # de eerste ronde van de nieuwe map
    for i in range(5):
        await upload(client, headers, folder, f"Film {i} (200{i}).mkv")
    assert len(scanner._pending) >= 5
    scanner._pending.clear()  # de crash
    assert await scanner.tick(settings) == 0
    assert await items(settings, library_id) == {}
    assert await scanner.queue_reconcile(settings) is True
    await jobs.run_pending(settings, lane="media_scan")
    assert len(await items(settings, library_id)) == 5


async def test_meer_dan_de_grens_wordt_een_volledige_ronde(world, client, monkeypatch):
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    await jobs.run_pending(settings, lane="media_scan")
    monkeypatch.setattr(scanner, "MAX_TOUCHED", 3)
    for i in range(4):
        await upload(client, headers, folder, f"Film {i}.mkv")
    assert scanner._overflow and not scanner._pending
    assert await scanner.tick(settings) == 1
    async with maker(settings)() as session:
        job = await session.scalar(
            select(Job).where(Job.key == f"media.scan:{library_id}", Job.status == "queued")
        )
        assert job is not None and job.payload.get("full") is True
    await jobs.run_pending(settings, lane="media_scan")
    assert len(await items(settings, library_id)) == 4


async def test_ids_komen_bij_een_scan_die_al_klaarstaat(world, client):
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    await jobs.run_pending(settings, lane="media_scan")
    a = await upload(client, headers, folder, "A.mkv")
    await scanner.tick(settings)
    b = await upload(client, headers, folder, "B.mkv")
    await scanner.tick(settings)
    async with maker(settings)() as session:
        rows = list(
            (await session.scalars(select(Job).where(Job.key == f"media.scan:{library_id}"))).all()
        )
    queued = [j for j in rows if j.status == "queued"]
    assert len(queued) == 1
    assert {a, b} <= set(queued[0].payload["nodes"])


async def test_een_nieuwe_upload_wacht_niet_op_100_analyses(world, client):
    """Klaar als: een item verschijnt binnen één tik, ook met 100 analyses in de rij media
    (de scan heeft een eigen rij, Keuze 14)."""
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    async with maker(settings)() as session:
        for i in range(100):
            await jobs.enqueue(session, analysis.JOB, {"sha": f"{i:064x}"}, key=f"x{i}")
        await session.commit()
    await upload(client, headers, folder, "Nieuw (2024).mkv")
    await scanner.tick(settings)
    assert await jobs.run_pending(settings, lane="media_scan") >= 1
    assert "Nieuw (2024).mkv" in await items(settings, library_id)
    async with maker(settings)() as session:
        waiting = await session.scalar(
            select(func.count()).select_from(Job).where(Job.type == analysis.JOB)
        )
    assert waiting >= 100  # de analyses staan er nog; de scan wachtte er niet op


async def test_extras_en_samples(world, client):
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    film = await mkdir(client, headers, folder, "Film (2010)")
    extras = await mkdir(client, headers, film, "Extras")
    interview = await upload(client, headers, extras, "Interview.mkv")
    main = await upload(client, headers, film, "Film (2010).mkv")
    trailer = await upload(client, headers, film, "Film (2010)-trailer.mkv")
    short = await upload(client, headers, film, "Film.2010.sample.mkv")
    await scan(settings)
    found = await items(settings, library_id)
    assert found["Interview.mkv"].kind == "extra" and found["Interview.mkv"].extra == "other"
    assert found["Interview.mkv"].parent_id == uuid.UUID(main)
    assert found["Film (2010)-trailer.mkv"].extra == "trailer"
    assert found["Film (2010)-trailer.mkv"].parent_id == uuid.UUID(main)
    assert found["Film.2010.sample.mkv"].extra == "sample"
    page = (await client.get(f"/api/v1/media/libraries/{library_id}/items", headers=headers)).json()
    assert [c["node_id"] for c in page["items"]] == [main]  # extra's tellen niet mee
    assert interview and trailer
    # Een "sample" van 5 minuten of langer is een gewone film (Keuze 5)
    async with maker(settings)() as session:
        sha = (await session.get(Node, uuid.UUID(short))).blob_id
    await analysis.save_probe(
        maker(settings), sha, analysis.Probe(duration_ms=classify.SAMPLE_MAX_MS)
    )
    r = await client.post(f"/api/v1/media/libraries/{library_id}/scan", headers=headers)
    assert r.status_code == 202
    await jobs.run_pending(settings, lane="media_scan")
    item = (await items(settings, library_id))["Film.2010.sample.mkv"]
    assert (item.kind, item.extra, item.parent_id) == ("movie", None, None)


async def test_een_extra_eerder_dan_zijn_film(world, client):
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    film = await mkdir(client, headers, folder, "Film (2010)")
    extras = await mkdir(client, headers, film, "Featurettes")
    await upload(client, headers, extras, "Maken.mkv")
    await scan(settings)
    assert (await items(settings, library_id))["Maken.mkv"].parent_id is None
    main = await upload(client, headers, film, "Film (2010).mkv")
    await scan(settings)
    assert (await items(settings, library_id))["Maken.mkv"].parent_id == uuid.UUID(main)


async def test_series_seizoenen_en_absolute_nummering(world, client):
    settings, _, headers = world
    library_id, folder = await library(client, headers, "Series", "shows")
    serie = await mkdir(client, headers, folder, "Serie (2010)")
    s1 = await mkdir(client, headers, serie, "Season 1")
    specials = await mkdir(client, headers, serie, "Specials")
    await upload(client, headers, s1, "Serie S01E01 Pilot.mkv")
    await upload(client, headers, s1, "Serie S01E02.mkv")
    await upload(client, headers, specials, "Serie S00E01.mkv")
    anime = await mkdir(client, headers, folder, "Titel")
    await upload(client, headers, anime, "[Groep] Titel - 012 [1080p].mkv")
    await upload(client, headers, folder, "Losse video.mkv")
    await scan(settings)
    found = await items(settings, library_id)
    pilot = found["Serie S01E01 Pilot.mkv"]
    assert (pilot.kind, pilot.title, pilot.season, pilot.episode) == ("episode", "Pilot", 1, 1)
    assert found["Serie S00E01.mkv"].season == 0
    absolute = found["[Groep] Titel - 012 [1080p].mkv"]
    assert (absolute.season, absolute.absolute) == (None, 12)  # nooit een seizoen 1 erbij
    assert found["Losse video.mkv"].kind == "video"
    async with maker(settings)() as session:
        shows = {s.title: s for s in (await session.scalars(select(MediaShow))).all()}
    assert set(shows) == {"Serie", "Titel"}
    assert shows["Serie"].year == 2010 and shows["Serie"].folder_id == uuid.UUID(serie)
    assert pilot.show_id == shows["Serie"].id == found["Serie S00E01.mkv"].show_id
    # De API: een lijst van series en een serie met seizoenen
    page = (await client.get(f"/api/v1/media/libraries/{library_id}/items", headers=headers)).json()
    assert [(c["title"], c["kind"]) for c in page["items"]] == [
        ("Serie", "show"),
        ("Titel", "show"),
    ]
    detail = (await client.get(f"/api/v1/media/shows/{shows['Serie'].id}", headers=headers)).json()
    assert [s["season"] for s in detail["seasons"]] == [1, 0]  # specials achteraan
    assert [e["episode"] for e in detail["seasons"][0]["episodes"]] == [1, 2]
    detail = (await client.get(f"/api/v1/media/shows/{shows['Titel'].id}", headers=headers)).json()
    assert [s["season"] for s in detail["seasons"]] == [None]


async def test_een_serie_met_alleen_cijfers(world, client):
    """C8 (review golf 3): 24, 1883 en 9-1-1 zijn series. Eerst was zo'n map "rommel" (geen
    letters): de serie kreeg geen map, dus ook geen tvshow.nfo en geen poster."""
    settings, _, headers = world
    library_id, folder = await library(client, headers, "Series", "shows")
    folders = {}
    for name, sub, file in (
        ("24", "Season 1", "24 S01E01.mkv"),
        ("1883", None, "1883.S01E02.mkv"),
        ("9-1-1", "Season 3", "9-1-1 - S03E05 - Titel.mkv"),
    ):
        folders[name] = await mkdir(client, headers, folder, name)
        parent = await mkdir(client, headers, folders[name], sub) if sub else folders[name]
        await upload(client, headers, parent, file)
    await scan(settings)
    async with maker(settings)() as session:
        shows = {s.title: s.folder_id for s in (await session.scalars(select(MediaShow))).all()}
    assert shows == {name: uuid.UUID(f) for name, f in folders.items()}
    found = await items(settings, library_id)
    assert found["9-1-1 - S03E05 - Titel.mkv"].title == "Titel"


async def test_delen_van_een_film(world, client):
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    film = await mkdir(client, headers, folder, "Film (2010)")
    one = await upload(client, headers, film, "Film (2010) cd1.mkv")
    two = await upload(client, headers, film, "Film (2010) cd2.mkv")
    await scan(settings)
    r = await client.get(f"/api/v1/media/items/{one}", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["parts"] == [{"node_id": one, "part": 1}, {"node_id": two, "part": 2}]


async def test_nfo_gaat_voor_de_naam_en_staat_in_de_cache(world, client):
    settings, fake, headers = world
    fake.on(Op.NFO, nfo_handler)
    library_id, folder = await library(client, headers)
    film = await mkdir(client, headers, folder, "film-map")
    await upload(client, headers, film, "dvd-rip.mkv")
    nfo = (
        b"<movie><title>De Echte Titel</title><year>1985</year><plot>Een verhaal.</plot>"
        b'<uniqueid type="tmdb">603</uniqueid></movie>'
    )
    nfo_node = await upload(client, headers, film, "movie.nfo", nfo)
    await scan(settings)
    item = (await items(settings, library_id))["dvd-rip.mkv"]
    assert (item.title, item.year, item.overview, item.source) == (
        "De Echte Titel", 1985, "Een verhaal.", "nfo",
    )  # fmt: skip
    assert item.ids["tmdb"] == "603"
    async with maker(settings)() as session:
        sha = (await session.get(Node, uuid.UUID(nfo_node))).blob_id
    assert scanner.nfo_cache(settings, sha).is_file()
    calls = len([r for r in fake.requests if r.op == "nfo"])
    r = await client.post(f"/api/v1/media/libraries/{library_id}/scan", headers=headers)
    assert r.status_code == 202
    await jobs.run_pending(settings, lane="media_scan")
    assert len([r for r in fake.requests if r.op == "nfo"]) == calls  # uit de cache
    # De NFO weg: terug naar de naam, zonder iets van de NFO (review golf 3, C4: de samenvatting
    # en het jaar van de NFO bleven staan naast de titel uit de naam)
    assert (await client.delete(f"/api/v1/files/{nfo_node}", headers=headers)).status_code == 204
    await scan(settings)
    item = (await items(settings, library_id))["dvd-rip.mkv"]
    assert (item.title, item.source, item.overview, item.year, item.ids) == (
        "dvd-rip", "name", None, None, {},
    )  # fmt: skip
    assert item.sort_title == names.sort_title("dvd-rip")


async def test_een_nfo_met_sorteertitel_en_een_serie_zonder_tvshow_nfo(world, client):
    """C4 ook bij een serie (tvshow.nfo weg: geen samenvatting meer) en de sorteertitel."""
    settings, fake, headers = world
    fake.on(Op.NFO, nfo_handler)
    library_id, folder = await library(client, headers, "Series", "shows")
    serie = await mkdir(client, headers, folder, "Mooie Serie")
    tv = await upload(client, headers, serie, "tvshow.nfo", b"<tvshow><title>Mooie Serie</title>"
                      b"<plot>Over een serie.</plot><year>2015</year></tvshow>")  # fmt: skip
    season = await mkdir(client, headers, serie, "Season 1")
    await upload(client, headers, season, "Mooie Serie S01E01.mkv")
    ep_nfo = await upload(client, headers, season, "Mooie Serie S01E01.nfo",
                          b"<episodedetails><title>Begin</title><sorttitle>Aaa</sorttitle>"
                          b"<plot>Het begin.</plot></episodedetails>")  # fmt: skip
    await scan(settings)
    item = (await items(settings, library_id))["Mooie Serie S01E01.mkv"]
    async with maker(settings)() as session:
        show = await session.get(MediaShow, item.show_id)
    assert (item.title, item.sort_title, item.overview) == ("Begin", "aaa", "Het begin.")
    assert (show.overview, show.year) == ("Over een serie.", 2015)
    for node in (tv, ep_nfo):
        assert (await client.delete(f"/api/v1/files/{node}", headers=headers)).status_code == 204
    await scan(settings)
    item = (await items(settings, library_id))["Mooie Serie S01E01.mkv"]
    async with maker(settings)() as session:
        show = await session.get(MediaShow, item.show_id)
    assert (item.source, item.overview) == ("name", None)
    assert item.sort_title != "aaa"
    assert (show.title, show.overview, show.year) == ("Mooie Serie", None, None)


def test_een_aanvulling_zonder_titel_wist_niets():
    """Een gekoppelde serie krijgt bij een scan alleen lokale art en ids: dat wist de
    beschrijving van de provider niet. En onder een provider wist een lokale ronde ook niets."""
    from app.media import items as media_items

    show = MediaShow(title="Echt", sort_title="echt", year=1999, overview="Van TMDB.", ids={},
                     art={}, locked=[])  # fmt: skip
    media_items.merge(show, {"art": {}, "ids": {"tvdb": "1"}}, "name")
    assert (show.year, show.overview) == (1999, "Van TMDB.")
    item = MediaItem(title="Echt", sort_title="echt", year=1999, overview="Van TMDB.", ids={},
                     art={}, locked=[], source="provider")  # fmt: skip
    media_items.merge(item, {"title": "naam", "year": None}, "name")
    assert (item.title, item.year, item.overview) == ("Echt", 1999, "Van TMDB.")
    item = MediaItem(title="Echt", sort_title="echt", year=1999, overview="Eigen.", ids={},
                     art={}, locked=["overview"], source="nfo")  # fmt: skip
    media_items.merge(item, {"title": "naam", "year": None}, "name")
    assert (item.title, item.year, item.overview) == ("naam", None, "Eigen.")


async def test_een_nfo_van_een_serie_en_een_aflevering(world, client):
    settings, fake, headers = world
    fake.on(Op.NFO, nfo_handler)
    library_id, folder = await library(client, headers, "Series", "shows")
    serie = await mkdir(client, headers, folder, "serie-map")
    await upload(client, headers, serie, "tvshow.nfo", b"<tvshow><title>Mooie Serie</title>"
                 b"<year>2015</year></tvshow>")  # fmt: skip
    season = await mkdir(client, headers, serie, "Season 2")
    await upload(client, headers, season, "serie-map S02E03.mkv")
    episode = (
        b"<episodedetails><title>Het Begin</title><season>2</season><episode>3</episode>"
        b"</episodedetails>"
    )
    await upload(client, headers, season, "serie-map S02E03.nfo", episode)
    await scan(settings)
    item = (await items(settings, library_id))["serie-map S02E03.mkv"]
    assert (item.title, item.season, item.episode) == ("Het Begin", 2, 3)
    async with maker(settings)() as session:
        show = await session.get(MediaShow, item.show_id)
    assert (show.title, show.year) == ("Mooie Serie", 2015)


async def test_een_tvshow_nfo_en_poster_die_later_komen(world, client):
    """C3 (review golf 3): tvshow.nfo en poster.jpg die later in de map van de serie komen. De
    afleveringen liggen een map lager (Season 1); eerst werd alleen de map van de serie zelf
    gescand, en daar staat geen aflevering: de serie bleef zonder titel en poster."""
    settings, fake, headers = world
    fake.on(Op.NFO, nfo_handler)
    library_id, folder = await library(client, headers, "Series", "shows")
    serie = await mkdir(client, headers, folder, "serie-map")
    season = await mkdir(client, headers, serie, "Season 1")
    await upload(client, headers, season, "serie-map S01E01.mkv")
    await scan(settings)

    async def show():
        item = (await items(settings, library_id))["serie-map S01E01.mkv"]
        async with maker(settings)() as session:
            return await session.get(MediaShow, item.show_id)

    assert ((await show()).title, (await show()).art) == ("serie-map", {})
    tv = b"<tvshow><title>Mooie Serie</title><plot>Echt.</plot></tvshow>"
    nfo = await upload(client, headers, serie, "tvshow.nfo", tv)
    poster = await upload(client, headers, serie, "poster.jpg", b"\xff\xd8\xff")
    await scan(settings)
    found = await show()
    assert (found.title, found.overview) == ("Mooie Serie", "Echt.")
    assert found.art == {"poster": {"node": poster}}
    # En weg (de prullenbak): weer de naam van de map, zonder poster
    for node in (nfo, poster):
        r = await client.delete(f"/api/v1/files/{node}", headers=headers)
        assert r.status_code in (200, 204), r.text
    await scan(settings)
    found = await show()
    assert (found.title, found.overview, found.art) == ("serie-map", None, {})


async def test_een_kapotte_nfo_geeft_de_naam(world, client):
    settings, fake, headers = world
    fake.on(Op.NFO, nfo_handler)
    library_id, folder = await library(client, headers)
    await upload(client, headers, folder, "Film (2010).mkv")
    await upload(client, headers, folder, "Film (2010).nfo",
                 b'<!DOCTYPE x [<!ENTITY a "b">]><movie><title>&a;</title></movie>')  # fmt: skip
    await scan(settings)
    item = (await items(settings, library_id))["Film (2010).mkv"]
    assert (item.title, item.source) == ("Film", "name")


async def test_een_appledouble_wordt_geen_item(world, client):
    """Review golf 3, S5: '._Film (2010).mkv' (4 KB, van een Mac over SMB) werd een tweede film
    'Film' (2010) met een probe die altijd faalt. Nu: één item, één analyse."""
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    film = await upload(client, headers, folder, "Film (2010).mkv")
    await upload(client, headers, folder, "._Film (2010).mkv", b"\x00\x05\x16\x07" + b"\0" * 4092)
    hidden = await mkdir(client, headers, folder, ".AppleDouble")
    await upload(client, headers, hidden, "Film (2010).mkv", b"\x00\x05\x16\x07" + b"\1" * 4092)
    await scan(settings)
    got = await items(settings, library_id)
    assert list(got) == ["Film (2010).mkv"] and str(got["Film (2010).mkv"].node_id) == film
    async with maker(settings)() as session:
        sha = (await session.get(Node, uuid.UUID(film))).blob_id
    assert list(await analyses(settings)) == [sha]


async def test_een_nfo_die_de_tijd_overschrijdt_is_een_fout_van_het_bestand(world, client):
    """Review golf 3, S1: een timeout van de opdracht nfo ligt aan de bytes. Hij komt in de cache
    zoals unreadable, en de bibliotheek komt niet in de herkansing van 60 s, ook niet met een
    tweede NFO in dezelfde scan die nog niet gelezen is."""
    from tests.media.fake_worker import OpError

    settings, fake, headers = world

    async def slow_or_real(job):
        data = os.pread(job.inputs[0], job.args.max_bytes + 1, 0)
        if b"traag" in data:
            raise OpError(wire_code.TIMEOUT, "nfo duurde langer dan 10 s")
        return nfo_op.parse(data)

    fake.on(Op.NFO, slow_or_real)
    library_id, folder = await library(client, headers)
    await upload(client, headers, folder, "a.mkv")
    await upload(client, headers, folder, "a.nfo", b"<movie><title>traag</title></movie>")
    await upload(client, headers, folder, "b.mkv")
    await upload(client, headers, folder, "b.nfo", b"<movie><title>Snel</title></movie>")
    await scan(settings)
    got = await items(settings, library_id)
    assert (got["a.mkv"].title, got["a.mkv"].source) == ("a", "name")
    assert got["b.mkv"].title == "Snel"
    assert library_id not in {str(k) for k in scanner._retry}
    async with maker(settings)() as session:
        shas = {
            n.name: n.blob_id
            for n in await session.scalars(select(Node).where(Node.name.in_(["a.nfo", "b.nfo"])))
        }
    assert scanner._read_cache(scanner.nfo_cache(settings, shas["a.nfo"]))["error"] == "timeout"
    calls = len([r for r in fake.requests if r.op == "nfo"])
    r = await client.post(f"/api/v1/media/libraries/{library_id}/scan", headers=headers)
    assert r.status_code == 202
    await jobs.run_pending(settings, lane="media_scan")
    assert len([r for r in fake.requests if r.op == "nfo"]) == calls  # niet opnieuw


async def test_de_worker_druk_eerst_de_naam_daarna_de_nfo(world, client, monkeypatch):
    """Is er maar één short-plaats vrij (de analyse heeft er een), dan leest de scanner geen NFO:
    afspelen houdt zijn plaats. Het item komt er met de naam; na NFO_RETRY komt de NFO."""
    settings, fake, headers = world
    fake.on(Op.NFO, nfo_handler)
    fake.used[WorkClass.SHORT] = 1  # de analyse
    library_id, folder = await library(client, headers)
    await upload(client, headers, folder, "x.mkv")
    await upload(client, headers, folder, "x.nfo", b"<movie><title>Echt</title></movie>")
    await scan(settings)
    assert (await items(settings, library_id))["x.mkv"].title == "x"
    assert not [r for r in fake.requests if r.op == "nfo"]
    assert library_id in {str(k) for k in scanner._retry}
    fake.used[WorkClass.SHORT] = 0
    assert await scan(settings) == 0  # nog niet: de herkansing is over NFO_RETRY seconden
    monkeypatch.setattr(scanner.time, "monotonic", lambda: time.time() + 10_000)
    await scan(settings)
    assert (await items(settings, library_id))["x.mkv"].title == "Echt"


async def test_afspelen_krijgt_een_plaats_terwijl_scan_en_analyse_lopen(world, client):
    """De analyse houdt één short-plaats (de rij media heeft één worker); de scanner neemt nooit
    de laatste. Een probe van play die tegelijk komt, krijgt dus een plaats."""
    settings, fake, headers = world
    gate = asyncio.Event()

    async def slow_nfo(job):
        await gate.wait()
        return await nfo_handler(job)

    fake.on(Op.NFO, slow_nfo)
    library_id, folder = await library(client, headers)
    for i in range(3):
        await upload(client, headers, folder, f"f{i}.mkv")
        await upload(client, headers, folder, f"f{i}.nfo", b"<movie><title>T</title></movie>")
    await scanner.tick(settings)
    scan_task = asyncio.create_task(jobs.run_pending(settings, lane="media_scan"))
    for _ in range(200):
        if fake.used[WorkClass.SHORT] == 1:
            break
        await asyncio.sleep(0.01)
    assert fake.used[WorkClass.SHORT] == 1  # de scanner leest een NFO
    # Nu de analyse erbij: hij neemt de tweede plaats niet als de scanner er een heeft? Nee:
    # de analyse is van een andere rij en weet niets van de scanner. De scanner wel: zolang er
    # maar één vrij is, leest hij de volgende NFO niet.
    fake.used[WorkClass.SHORT] += 1  # de analyse pakt de tweede
    gate.set()
    await scan_task
    reads = [r for r in fake.requests if r.op == "nfo"]
    assert len(reads) == 1  # de rest wachtte: na de eerste was er niets meer vrij
    fake.used[WorkClass.SHORT] -= 1  # de analyse is klaar
    # play: de probe krijgt een plaats, ook terwijl de scanner weer kan lezen
    caps = await media_client.caps()
    assert caps.places["short"] - caps.in_use["short"] >= 1
    assert library_id


async def test_start_en_stop_van_de_lus(world, client, monkeypatch):
    settings, _, headers = world
    assert scanner.start(None, None) is None
    library_id, folder = await library(client, headers)
    await jobs.run_pending(settings, lane="media_scan")
    async with maker(settings)() as session:
        await session.execute(update(Job).values(status="done"))
        await session.commit()
    scanner.reset()
    fast = settings.model_copy(update={"media_scan_tick": 0.05})
    task = scanner.start(app, fast)
    assert isinstance(task, asyncio.Task)
    try:
        for _ in range(200):
            async with maker(settings)() as session:
                job = await session.scalar(select(Job).where(Job.type == scanner.RECONCILE))
            if job is not None:
                break
            await asyncio.sleep(0.02)
        assert job is not None and job.status == "queued"
        assert scanner.active()
        # De lus zet een upload binnen een paar tikken klaar
        await upload(client, headers, folder, "Lus (2020).mkv")
        for _ in range(200):
            async with maker(settings)() as session:
                queued = await session.scalar(
                    select(Job).where(Job.key == f"media.scan:{library_id}", Job.status == "queued")
                )
            if queued is not None and queued.payload.get("nodes"):
                break
            await asyncio.sleep(0.02)
        assert queued is not None
    finally:
        await scanner.stop(task)
    assert task.done()


async def test_de_scan_van_een_verdwenen_bibliotheek_doet_niets(world, client):
    settings, _, headers = world
    await scanner.run(settings, {"library_id": str(uuid.uuid4()), "full": True})


async def test_definitief_weg_telt_opnieuw(world, client):
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    node = await upload(client, headers, folder, "Film.mkv")
    await scan(settings)
    assert (await client.delete(f"/api/v1/files/{node}", headers=headers)).status_code == 204
    await scan(settings)
    r = await client.delete(f"/api/v1/trash/{node}", headers=headers)
    assert r.status_code == 204, r.text
    await scan(settings)
    async with maker(settings)() as session:
        lib = await session.get(MediaLibrary, uuid.UUID(library_id))
        assert lib.item_count == 0
    assert await items(settings, library_id) == {}


async def test_ontdubbelde_inhoud_is_een_eigen_item_per_bestand(world, client):
    settings, _, headers = world
    library_id, folder = await library(client, headers)
    data = b"zelfde" * 500
    await upload(client, headers, folder, "Een (2001).mkv", data)
    await upload(client, headers, folder, "Twee (2002).mkv", data)
    await scan(settings)
    found = await items(settings, library_id)
    assert {i.title for i in found.values()} == {"Een", "Twee"}
    assert len(await analyses(settings)) == 1  # één inhoud, één analyse
    async with maker(settings)() as session:
        assert await session.scalar(select(func.count()).select_from(Blob)) == 1


# --- plan: de stukken ----------------------------------------


def tree_of(
    counts: dict[str, int],
) -> tuple[scanner.Tree, dict[uuid.UUID, int], dict[str, uuid.UUID]]:
    root = scanner.Dir(uuid.uuid4(), None, "", (), False, scanner.END + "0")
    dirs = {root.id: root}
    ids = {"": root.id}
    children: dict[uuid.UUID, list[uuid.UUID]] = {root.id: []}
    for name in counts:
        if not name:
            continue
        d = scanner.Dir(uuid.uuid4(), root.id, name, (name,), False, name + scanner.END + "1")
        dirs[d.id] = d
        ids[name] = d.id
        children[root.id].append(d.id)
    return scanner.Tree(root, dirs, children), {ids[n]: c for n, c in counts.items()}, ids


def test_plan_een_volledige_ronde_in_stukken_van_500():
    tree, counts, ids = tree_of({"": 0, "a": 300, "b": 300, "c": 1200, "d": 10})
    library = uuid.uuid4()
    seen = []
    after = None
    for _ in range(10):
        p = scanner.plan(tree, counts, [], True, after, library)
        seen.append([(tree.dirs[u.dir_id].name, u.start, u.end) for u in p.units])
        assert sum(u.end - u.start for u in p.units) <= 500
        if p.rest is None:
            assert p.full_done
            break
        assert p.rest["full"] is True
        after = tuple(p.rest["after"])
    assert seen == [
        [("", 0, 0), ("a", 0, 300), ("b", 0, 200)],
        [("b", 200, 300), ("c", 0, 400)],
        [("c", 400, 900)],
        [("c", 900, 1200), ("d", 0, 10)],
    ]


def test_plan_genoemde_mappen_eerst_en_de_rest_later():
    tree, counts, ids = tree_of({"": 0, "a": 400, "b": 400})
    library = uuid.uuid4()
    p = scanner.plan(
        tree, counts, [(ids["b"], 0), (ids["a"], 0), (uuid.uuid4(), 0)], False, None, library
    )
    assert [(tree.dirs[u.dir_id].name, u.start, u.end) for u in p.units] == [
        ("a", 0, 400),
        ("b", 0, 100),
    ]
    assert p.rest == {"library_id": str(library), "dirs": [[str(ids["b"]), 100]]}
    assert not p.full_done
    p = scanner.plan(tree, counts, [(ids["b"], 100)], False, None, library)
    assert p.rest is None and [(u.start, u.end) for u in p.units] == [(100, 400)]


def test_plan_genoemd_en_volledig_samen():
    tree, counts, ids = tree_of({"": 0, "a": 400, "b": 400})
    library = uuid.uuid4()
    p = scanner.plan(tree, counts, [(ids["b"], 0)], True, None, library)
    assert [(tree.dirs[u.dir_id].name, u.end) for u in p.units] == [("b", 400), ("", 0), ("a", 100)]
    assert p.rest == {"library_id": str(library), "full": True,
                      "after": [tree.dirs[ids["a"]].key, 100]}  # fmt: skip


def test_merge_van_een_payload():
    a, b = uuid.uuid4(), uuid.uuid4()
    out = scanner._merge({"library_id": "x", "nodes": [str(a)]}, nodes=[a, b])
    assert out["nodes"] == [str(a), str(b)]
    out = scanner._merge(out, full=True)
    assert out["full"] is True and out["after"] is None and out["nodes"]
    many = [uuid.uuid4() for _ in range(scanner.MAX_NODES + 1)]
    out = scanner._merge({"library_id": "x"}, nodes=many)
    assert out["full"] is True and "nodes" not in out
    out = scanner._merge({"library_id": "x", "dirs": [[str(a), 300]]}, dirs=[a])
    assert out["dirs"] == [[str(a), 0]]


async def test_polite_wil_twee_vrije_plaatsen(world):
    _, fake, _ = world
    assert await scanner.polite()
    fake.used[WorkClass.SHORT] = 1
    assert not await scanner.polite()
    fake.used[WorkClass.SHORT] = 0
