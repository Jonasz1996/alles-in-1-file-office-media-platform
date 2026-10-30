"""De API van de kijkvoortgang (M7): PUT en batch, de rijen van /media/home, en wie wat mag.

Met media aan (de nep-worker van media_app) en een echte scan van de bibliotheek. De regels zelf
staan in test_media_progress.py.
"""

from __future__ import annotations

import datetime as dt
import uuid
from datetime import UTC

import pytest
from sqlalchemy import update

from app import events
from app.media import analysis, progress
from app.media.types import Probe
from app.models import PlaybackState
from tests.test_files import mkdir, put, root
from tests.test_media_contract import media_app  # noqa: F401
from tests.test_media_scanner import library, scan, upload, world  # noqa: F401
from tests.test_media_sessions import blob_of, content, maker
from tests.test_shares import person, share_with

pytestmark = pytest.mark.anyio

URL = "/api/v1/media/progress"
MIN = 60


def body(position_s: float, duration_s: float = 3600, event="pause", origin="tab", seq=1,
         started: dt.datetime | None = None, **extra) -> dict:  # fmt: skip
    started = started or dt.datetime.now(UTC) - dt.timedelta(minutes=1)
    return {
        "position_ms": int(position_s * 1000),
        "duration_ms": int(duration_s * 1000),
        "event": event,
        "origin": origin,
        "seq": seq,
        "started_at": started.isoformat(),
        **extra,
    }


class Seq:
    """Een tabblad: elke PUT een hoger volgnummer."""

    def __init__(self):
        self.n = 0

    def __call__(self) -> int:
        self.n += 1
        return self.n


@pytest.fixture
async def w(world, client):  # noqa: F811
    settings, _, headers = world
    progress.reset()
    yield settings, headers
    await progress.stop()


async def save(client, headers, node, position_s, *, seq, **kw):
    return await client.put(f"{URL}/{node}", json=body(position_s, seq=seq, **kw), headers=headers)


async def home(client, headers) -> dict:
    r = await client.get("/api/v1/media/home", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


def titles(cards: list[dict]) -> list[str]:
    return [c["title"] for c in cards]


async def films(client, headers, settings, names: list[str]) -> tuple[str, dict[str, str]]:
    library_id, folder = await library(client, headers)
    nodes = {name: await upload(client, headers, folder, f"{name}.mkv") for name in names}
    await scan(settings)
    return library_id, nodes


async def series(client, headers, settings, episodes: dict[str, list[str]], name="Serie"):
    """Een bibliotheek met één serie: per seizoensmap de afleveringen. Geeft (show_id, nodes)."""
    _, folder = await library(client, headers, "Series", "shows")
    show = await mkdir(client, headers, folder, name)
    nodes = {}
    for season, files in episodes.items():
        parent = await mkdir(client, headers, show, season)
        for file in files:
            nodes[file] = await upload(client, headers, parent, f"{name} {file}.mkv")
    await scan(settings)
    r = await client.get(f"/api/v1/media/items/{nodes[next(iter(nodes))]}", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["show_id"], nodes


# PUT en Verder kijken


async def test_pauze_na_een_minuut_staat_in_verder_kijken(w, client):
    settings, headers = w
    tab = Seq()
    _, nodes = await films(client, headers, settings, ["Een (2001)", "Twee (2002)"])
    r = await save(client, headers, nodes["Een (2001)"], 30, seq=tab())
    assert r.status_code == 204, r.text
    h = await home(client, headers)
    assert h["continue"] == []  # 30 s: nog niet
    [een] = [c for c in h["recent"] if c["title"] == "Een"]
    assert een["progress"] == {"position_ms": 30_000, "duration_ms": 3_600_000}
    r = await save(client, headers, nodes["Een (2001)"], 90, seq=tab())
    assert r.status_code == 204
    h = await home(client, headers)
    assert titles(h["continue"]) == ["Een"]
    assert h["continue"][0]["progress"] == {"position_ms": 90_000, "duration_ms": 3_600_000}
    assert h["next_up"] == []
    # In de database (pauze schrijft meteen), dus ook op de pagina van het item
    r = await client.get(f"/api/v1/media/items/{nodes['Een (2001)']}", headers=headers)
    assert r.json()["progress"]["position_ms"] == 90_000


async def test_verder_kijken_de_nieuwste_eerst_en_hooguit_20(w, client):
    settings, headers = w
    names = [f"Film {n:02d} ({2000 + n})" for n in range(22)]
    _, nodes = await films(client, headers, settings, names)
    for name in names:
        r = await save(client, headers, nodes[name], 5 * MIN, seq=1)
        assert r.status_code == 204
    h = await home(client, headers)
    expected = [f"Film {n:02d}" for n in reversed(range(22))][:20]
    assert titles(h["continue"]) == expected


async def test_gezien_staat_niet_in_verder_kijken(w, client):
    settings, headers = w
    _, nodes = await films(client, headers, settings, ["Uit (2001)"])
    node = nodes["Uit (2001)"]
    assert (await save(client, headers, node, 30 * MIN, seq=1)).status_code == 204
    assert titles((await home(client, headers))["continue"]) == ["Uit"]
    assert (await save(client, headers, node, 59 * MIN, seq=2, event="end")).status_code == 204
    h = await home(client, headers)
    assert h["continue"] == []
    [card] = h["recent"]
    assert (card["watched"], card["progress"]) == (True, None)


async def test_een_tick_binnen_5_s_geeft_429(w, client):
    settings, headers = w
    _, nodes = await films(client, headers, settings, ["Tik (2001)"])
    node = nodes["Tik (2001)"]
    assert (await save(client, headers, node, 61, seq=1, event="tick")).status_code == 204
    r = await save(client, headers, node, 62, seq=2, event="tick")
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "rate_limited"
    assert r.headers["retry-after"] == "5"
    # Pauze gaat altijd door
    assert (await save(client, headers, node, 63, seq=3)).status_code == 204


async def test_iets_wat_je_niet_mag_lezen_geeft_404(w, client, admin):
    settings, jonas = w
    bas, _ = await person(client, jonas, "bas@example.nl")
    jroot = (await root(client, jonas))["folder"]["id"]
    folder = await mkdir(client, jonas, jroot, "Gedeeld")
    shared = await upload(client, jonas, folder, "film.mkv")
    own = await upload(client, jonas, jroot, "eigen.mkv")
    # Van een ander, niet gedeeld
    r = await save(client, bas, own, 90, seq=1)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"
    # Gedeeld: mag; deling weg: 404
    bas_id = (await client.get("/api/v1/auth/me", headers=bas)).json()["id"]
    share = await share_with(client, jonas, folder, user_id=bas_id, permission="read")
    assert (await save(client, bas, shared, 90, seq=1)).status_code == 204
    r = await client.delete(f"/api/v1/shares/{share['id']}", headers=jonas)
    assert r.status_code == 204
    assert (await save(client, bas, shared, 95, seq=2)).status_code == 404
    # In de eigen prullenbak, een map, iets wat niet bestaat
    assert (await client.delete(f"/api/v1/files/{own}", headers=jonas)).status_code == 204
    assert (await save(client, jonas, own, 90, seq=1)).status_code == 404
    assert (await save(client, jonas, folder, 90, seq=1)).status_code == 404
    assert (await save(client, jonas, uuid.uuid4(), 90, seq=1)).status_code == 404


async def test_het_event_gaat_naar_dezelfde_gebruiker_niet_naar_een_ander(w, client):
    settings, jonas = w
    bas, bas_id = await person(client, jonas, "bas@example.nl")
    jonas_id = (await client.get("/api/v1/auth/me", headers=jonas)).json()["id"]
    jroot = (await root(client, jonas))["folder"]["id"]
    folder = await mkdir(client, jonas, jroot, "Samen")
    node = await upload(client, jonas, folder, "film.mkv")
    await share_with(client, jonas, folder, user_id=bas_id, permission="read")
    with (
        events.hub.subscribe(uuid.UUID(jonas_id)) as mine,
        events.hub.subscribe(uuid.UUID(bas_id)) as theirs,
    ):
        assert (await save(client, jonas, node, 90, seq=1, origin="laptop")).status_code == 204
        event = mine.queue.get_nowait()
        assert event["type"] == "playback.progress"
        assert event["data"]["node_id"] == node
        assert event["data"]["origin"] == "laptop"
        assert event["data"]["position_ms"] == 90_000
        assert theirs.queue.empty()  # Bas ziet hetzelfde bestand, maar niet Jonas' voortgang
        # En andersom: Bas' eigen voortgang gaat alleen naar Bas
        assert (await save(client, bas, node, 30, seq=1, origin="tv")).status_code == 204
        assert theirs.queue.get_nowait()["data"]["origin"] == "tv"
        assert mine.queue.empty()
    # Ieder zijn eigen stand
    async with maker(settings)() as session:
        mine_row = await session.get(PlaybackState, (uuid.UUID(jonas_id), "", uuid.UUID(node)))
        bas_row = await session.get(PlaybackState, (uuid.UUID(bas_id), "", uuid.UUID(node)))
    assert (mine_row.position_ms, bas_row.position_ms) == (90_000, 30_000)


async def test_een_late_tik_na_gezien_via_de_api(w, client):
    settings, headers = w
    _, nodes = await films(client, headers, settings, ["Laat (2001)"])
    node = nodes["Laat (2001)"]
    started = dt.datetime.now(UTC) - dt.timedelta(hours=1)
    assert (await save(client, headers, node, 58 * MIN, seq=5, started=started)).status_code == 204
    # De keepalive van het tabblad dat dichtging, met een oudere seq
    r = await save(client, headers, node, 50 * MIN, seq=4, started=started)
    assert r.status_code == 204  # telt niet, maar geen fout
    h = await home(client, headers)
    assert h["continue"] == [] and h["recent"][0]["watched"] is True


async def test_validatie(w, client):
    settings, headers = w
    _, nodes = await films(client, headers, settings, ["Val (2001)"])
    node = nodes["Val (2001)"]
    bad = [
        body(90, origin="x" * 65),
        body(90, origin=""),
        body(90, subtitle="z1"),
        body(90, event="play"),
        {**body(90), "seq": -1},
        {**body(90), "position_ms": -5},
    ]
    for b in bad:
        r = await client.put(f"{URL}/{node}", json=b, headers=headers)
        assert r.status_code == 422, b
    ok = body(90, origin="x" * 64, subtitle="e2", audio=1)
    assert (await client.put(f"{URL}/{node}", json=ok, headers=headers)).status_code == 204
    for b in (
        {"watched": True},
        {"node_ids": [node], "show_id": str(uuid.uuid4()), "watched": True},
        {"node_ids": [node], "season": 1, "watched": True},
        {"node_ids": [str(uuid.uuid4()) for _ in range(1001)], "watched": True},
    ):
        r = await client.post(f"{URL}/batch", json=b, headers=headers)
        assert r.status_code == 422, b


# Batch


async def test_batch_met_node_ids(w, client):
    settings, jonas = w
    bas, _ = await person(client, jonas, "bas@example.nl")
    _, nodes = await films(client, jonas, settings, ["Een (2001)", "Twee (2002)"])
    een, twee = nodes["Een (2001)"], nodes["Twee (2002)"]
    vreemd = await upload(client, bas, (await root(client, bas))["folder"]["id"], "van bas.mkv")
    assert (await save(client, jonas, een, 20 * MIN, seq=1)).status_code == 204
    r = await client.post(
        f"{URL}/batch", json={"node_ids": [een, twee, vreemd, een], "watched": True}, headers=jonas
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 2}  # dat van Bas valt stil weg
    h = await home(client, jonas)
    assert h["continue"] == []
    assert all(c["watched"] for c in h["recent"])
    r = await client.post(f"{URL}/batch", json={"node_ids": [een], "watched": False}, headers=jonas)
    assert r.json() == {"updated": 1}
    [card] = [c for c in (await home(client, jonas))["recent"] if c["title"] == "Een"]
    assert (card["watched"], card["progress"]) == (False, None)
    # Van Bas zelf niets veranderd
    async with maker(settings)() as session:
        rows = (await session.scalars(PlaybackState.__table__.select())).all()
    assert len(rows) == 2


async def test_batch_met_show_id_en_seizoen(w, client):
    settings, jonas = w
    show_id, nodes = await series(
        client, jonas, settings,
        {"Season 1": ["S01E01", "S01E02"], "Season 2": ["S02E01"]},
    )  # fmt: skip
    r = await client.post(
        f"{URL}/batch", json={"show_id": show_id, "season": 1, "watched": True}, headers=jonas
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"updated": 2}
    r = await client.get(f"/api/v1/media/shows/{show_id}", headers=jonas)
    seasons = {s["season"]: [e["watched"] for e in s["episodes"]] for s in r.json()["seasons"]}
    assert seasons == {1: [True, True], 2: [False]}
    # De hele serie op niet gezien: alleen wat gezien was, telt
    r = await client.post(f"{URL}/batch", json={"show_id": show_id, "watched": False},
                          headers=jonas)  # fmt: skip
    assert r.json() == {"updated": 2}
    # Een serie die je niet ziet: 404
    bas, _ = await person(client, jonas, "bas@example.nl")
    r = await client.post(f"{URL}/batch", json={"show_id": show_id, "watched": True}, headers=bas)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"
    r = await client.post(
        f"{URL}/batch", json={"show_id": str(uuid.uuid4()), "watched": True}, headers=jonas
    )
    assert r.status_code == 404


# Volgende aflevering


async def test_volgende_aflevering_na_de_laatst_geziene(w, client):
    settings, headers = w
    tab = Seq()
    show_id, nodes = await series(
        client, headers, settings,
        {"Season 1": ["S01E01", "S01E02", "S01E03"], "Season 2": ["S02E01"],
         "Specials": ["S00E01"]},
    )  # fmt: skip
    assert (await home(client, headers))["next_up"] == []  # nog niets gezien
    await save(client, headers, nodes["S01E01"], 59 * MIN, seq=tab(), event="end")
    [card] = (await home(client, headers))["next_up"]
    assert (card["node_id"], card["season"], card["episode"], card["show_id"]) == (
        nodes["S01E02"], 1, 2, show_id,
    )  # fmt: skip
    r = await client.post(
        f"{URL}/batch", json={"node_ids": [nodes["S01E02"]], "watched": True}, headers=headers
    )
    assert r.json() == {"updated": 1}
    assert [c["node_id"] for c in (await home(client, headers))["next_up"]] == [nodes["S01E03"]]
    # S01E03 half gekeken: die staat in Verder kijken, niet nog eens in Volgende aflevering
    await save(client, headers, nodes["S01E03"], 10 * MIN, seq=tab())
    h = await home(client, headers)
    assert [c["node_id"] for c in h["continue"]] == [nodes["S01E03"]]
    assert h["next_up"] == []
    await save(client, headers, nodes["S01E03"], 59 * MIN, seq=tab(), event="end")
    h = await home(client, headers)
    assert [c["node_id"] for c in h["next_up"]] == [nodes["S02E01"]]
    assert h["continue"] == []
    # Na de laatste: niets (specials tellen niet mee)
    await save(client, headers, nodes["S02E01"], 59 * MIN, seq=tab(), event="end")
    assert (await home(client, headers))["next_up"] == []


async def test_volgende_aflevering_na_de_laatste_overgang_naar_gezien(w, client):
    """De laatst geziene in de tijd, niet de verste: wie S01E03 zag en daarna S01E01 nog eens
    (opnieuw gezien), krijgt S01E02."""
    settings, headers = w
    _, nodes = await series(client, headers, settings,
                            {"Season 1": ["S01E01", "S01E02", "S01E03", "S01E04"]})  # fmt: skip
    await save(client, headers, nodes["S01E03"], 59 * MIN, seq=1, event="end")
    assert [c["node_id"] for c in (await home(client, headers))["next_up"]] == [nodes["S01E04"]]
    await save(client, headers, nodes["S01E01"], 59 * MIN, seq=1, event="end")
    assert [c["node_id"] for c in (await home(client, headers))["next_up"]] == [nodes["S01E02"]]


async def test_volgende_aflevering_alleen_met_activiteit_in_90_dagen(w, client):
    settings, headers = w
    _, nodes = await series(client, headers, settings, {"Season 1": ["S01E01", "S01E02"]})
    await save(client, headers, nodes["S01E01"], 59 * MIN, seq=1, event="end")
    assert len((await home(client, headers))["next_up"]) == 1
    await progress.stop()  # alles in de database en het geheugen leeg
    old = dt.datetime.now(UTC) - dt.timedelta(days=91)
    async with maker(settings)() as session:
        await session.execute(update(PlaybackState).values(updated_at=old))
        await session.commit()
    assert (await home(client, headers))["next_up"] == []


# Wat de lijsten tonen


async def test_een_vervangen_bestand_staat_niet_meer_in_verder_kijken(w, client):
    settings, headers = w
    library_id, folder = await library(client, headers)
    node = await upload(client, headers, folder, "Film (2001).mkv")
    await scan(settings)
    assert (await save(client, headers, node, 30 * MIN, seq=1)).status_code == 204
    assert titles((await home(client, headers))["continue"]) == ["Film"]
    # Een nieuwe versie met (bijna) dezelfde duur: de positie blijft
    r = await put(client, headers, folder, "Film (2001).mkv", content("v2", 2000),
                  conflict="replace")  # fmt: skip
    assert r.status_code == 201 and r.json()["id"] == node
    sha = await blob_of(settings, node)
    await analysis.save_probe(maker(settings), sha, Probe(duration_ms=3_650_000))
    assert titles((await home(client, headers))["continue"]) == ["Film"]
    # Een ander bestand met een duur die meer dan 5 % afwijkt: terug naar 0
    r = await put(client, headers, folder, "Film (2001).mkv", content("v3", 2000),
                  conflict="replace")  # fmt: skip
    sha = await blob_of(settings, node)
    await analysis.save_probe(maker(settings), sha, Probe(duration_ms=2_400_000))
    assert (await home(client, headers))["continue"] == []
    # Wie het nieuwe bestand speelt, begint gewoon een nieuwe stand
    assert (await save(client, headers, node, 5 * MIN, seq=2, duration_s=2400)).status_code == 204
    [card] = (await home(client, headers))["continue"]
    assert card["progress"] == {"position_ms": 300_000, "duration_ms": 2_400_000}


async def test_verder_kijken_alleen_wat_je_nog_mag_lezen(w, client):
    settings, jonas = w
    bas, bas_id = await person(client, jonas, "bas@example.nl")
    library_id, folder = await library(client, jonas)
    share = await share_with(client, jonas, folder, user_id=bas_id, permission="read")
    node = await upload(client, jonas, folder, "Samen (2001).mkv")
    await scan(settings)
    assert (await save(client, bas, node, 10 * MIN, seq=1)).status_code == 204
    assert titles((await home(client, bas))["continue"]) == ["Samen"]
    assert (await home(client, jonas))["continue"] == []  # Jonas heeft zijn eigen stand
    r = await client.delete(f"/api/v1/shares/{share['id']}", headers=jonas)
    assert r.status_code == 204
    assert (await home(client, bas))["continue"] == []
    # En wat in de prullenbak ligt, ook niet (ook vóór de volgende scan)
    assert (await save(client, jonas, node, 10 * MIN, seq=1)).status_code == 204
    assert titles((await home(client, jonas))["continue"]) == ["Samen"]
    assert (await client.delete(f"/api/v1/files/{node}", headers=jonas)).status_code == 204
    assert (await home(client, jonas))["continue"] == []


async def test_de_rijen_gebruiken_het_geheugen(w, client):
    """Een tick wacht nog op zijn beurt (hooguit 10 s), maar Verder kijken toont hem al."""
    settings, headers = w
    _, nodes = await films(client, headers, settings, ["Een (2001)", "Twee (2002)"])
    assert (await save(client, headers, nodes["Een (2001)"], 2 * MIN, seq=1)).status_code == 204
    r = await save(client, headers, nodes["Twee (2002)"], 3 * MIN, seq=1, event="tick")
    assert r.status_code == 204
    live = progress.current()
    assert live is not None and live.due  # nog niet geschreven
    h = await home(client, headers)
    assert titles(h["continue"]) == ["Twee", "Een"]
    assert h["continue"][0]["progress"]["position_ms"] == 180_000


async def test_wie_op_het_slot_wacht_houdt_geen_transactie_open(w, client):
    """Regel 8 en de volgorde van sloten: de router commit vóór het slot van de gebruiker. Terwijl
    een PUT op dat slot wacht, kan een ander gewoon schrijven; daarna gaat de PUT door."""
    import asyncio

    import anyio

    settings, headers = w
    _, nodes = await films(client, headers, settings, ["Slot (2001)"])
    node = nodes["Slot (2001)"]
    user_id = uuid.UUID((await client.get("/api/v1/auth/me", headers=headers)).json()["id"])
    live = progress.store(settings)
    lock = live.lock(user_id)
    async with lock:
        task = asyncio.create_task(save(client, headers, node, 90, seq=1))
        for _ in range(200):
            if len(lock._waiters or ()) == 1:
                break
            await anyio.sleep(0.01)
        assert len(lock._waiters or ()) == 1
        with anyio.fail_after(2):
            async with maker(settings)() as session:
                await session.execute(
                    update(PlaybackState).values(seq=0).where(PlaybackState.user_id == user_id)
                )
                await session.commit()
        assert not task.done()
    r = await task
    assert r.status_code == 204
    assert titles((await home(client, headers))["continue"]) == ["Slot"]
