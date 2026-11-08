"""Herinneringen (bouwontwerp fase 6, Keuze 41; Z6): GET /photos/memories en de regels om te
verbergen. De dag komt altijd van de test (?day= of een vaste klok in app.memories.now), nooit
van de echte klok.

- eigen, levende foto's van deze dag in eerdere jaren, het recentste jaar eerst;
- de schrikkeldag; de tijdzone (PLATFORM_TIMEZONE) voor "vandaag";
- verbergen: een dag, een map (met alles eronder), een persoon; 400 memories_bad_hide;
- de prullenbak (ook via een map erboven) en terugzetten; herinneringen uit (photo_pref).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import pytest
from sqlalchemy import func, select

from app import memories as M
from app.models import MemoryHide, Node, Person, PhotoPerson, PhotoPref
from tests.factories import code_of, create_file, create_folder, create_user, maker, params_of
from tests.test_photos import login, photo

pytestmark = [pytest.mark.anyio, pytest.mark.zoek]


def om(dag: str, uur: int = 12) -> datetime:
    """Een opnametijd op die dag (JJJJ-MM-DD)."""
    return datetime.fromisoformat(dag).replace(hour=uur, tzinfo=UTC)


@pytest.fixture
async def huis(client, settings):
    """Eva met foto's op 5 november van verschillende jaren (in de map Vakantie en in haar
    hoofdmap), een in de prullenbak, een onder een map in de prullenbak; Bram met een eigen
    foto op die dag."""
    async with maker(settings)() as session:
        eva = await create_user(session, "eva@example.nl")
        bram = await create_user(session, "bram@example.nl")
        vakantie = await create_folder(session, eva, name="Vakantie")
        diep = await create_folder(session, eva, vakantie.id, name="Strand")
        weg = await create_folder(session, eva, name="Weg", deleted=True)
        ids = {"eva": eva.id, "bram": bram.id, "vakantie": vakantie.id, "weg_map": weg.id}
        ids["bram_map"] = (await create_folder(session, bram, name="Brams map")).id
        for key, dag, uur, parent in (
            ("vorig_jaar", "2025-11-05", 12, None),
            ("drie_a", "2023-11-05", 9, vakantie.id),
            ("drie_b", "2023-11-05", 15, diep.id),
            ("andere_dag", "2024-11-04", 12, None),
            ("dit_jaar", "2026-11-05", 8, None),
            ("lang_geleden", "2001-11-05", 12, None),
        ):
            ids[key] = (await photo(session, eva, f"{key}.jpg", om(dag, uur), parent)).id
        ids["prullenbak"] = (
            await photo(session, eva, "prullenbak.jpg", om("2022-11-05"), deleted=True)
        ).id
        ids["onder_weg"] = (await photo(session, eva, "onder.jpg", om("2021-11-05"), weg.id)).id
        ids["tekst"] = (await create_file(session, eva, name="notitie.txt")).id
        ids["bram_foto"] = (await photo(session, bram, "bram.jpg", om("2024-11-05"))).id
        await session.commit()
    ids["h_eva"] = await login(client, "eva@example.nl")
    ids["h_bram"] = await login(client, "bram@example.nl")
    return ids


async def herinneringen(client, headers, **params) -> list[tuple[int, str, list[str]]]:
    r = await client.get("/api/v1/photos/memories", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return [(y["years_ago"], y["day"], [i["name"] for i in y["items"]]) for y in r.json()["years"]]


VIJF_NOVEMBER = [
    (1, "2025-11-05", ["vorig_jaar.jpg"]),
    (3, "2023-11-05", ["drie_b.jpg", "drie_a.jpg"]),
    (25, "2001-11-05", ["lang_geleden.jpg"]),
]


async def test_deze_dag_in_eerdere_jaren(client, huis):
    assert await herinneringen(client, huis["h_eva"], day="2026-11-05") == VIJF_NOVEMBER
    r = await client.get(
        "/api/v1/photos/memories", params={"day": "2026-11-05"}, headers=huis["h_eva"]
    )
    item = r.json()["years"][0]["items"][0]
    assert (item["id"], item["taken_day"], item["media"]) == (
        str(huis["vorig_jaar"]), 20251105, "image")  # fmt: skip
    # Op 4 november: alleen de foto van 4 november 2024
    assert await herinneringen(client, huis["h_eva"], day="2025-11-04") == [
        (1, "2024-11-04", ["andere_dag.jpg"])
    ]
    # Bram ziet alleen zijn eigen foto
    assert await herinneringen(client, huis["h_bram"], day="2026-11-05") == [
        (2, "2024-11-05", ["bram.jpg"])
    ]
    # Vóór de eerste foto, of een dag zonder foto's: leeg
    assert await herinneringen(client, huis["h_eva"], day="2001-11-05") == []
    assert await herinneringen(client, huis["h_eva"], day="2026-01-01") == []
    r = await client.get(
        "/api/v1/photos/memories", params={"day": "2026-13-01"}, headers=huis["h_eva"]
    )
    assert r.status_code == 422


async def test_de_prullenbak_en_terugzetten(client, huis):
    eva = huis["h_eva"]
    r = await client.delete(f"/api/v1/files/{huis['vorig_jaar']}", headers=eva)
    assert r.status_code == 204, r.text
    # De map Vakantie in de prullenbak: ook wat eronder ligt is weg
    r = await client.delete(f"/api/v1/files/{huis['vakantie']}", headers=eva)
    assert r.status_code == 204, r.text
    assert await herinneringen(client, eva, day="2026-11-05") == [VIJF_NOVEMBER[2]]
    for node in (huis["vorig_jaar"], huis["vakantie"]):
        r = await client.post(f"/api/v1/files/{node}/restore", headers=eva)
        assert r.status_code == 200, r.text
    assert await herinneringen(client, eva, day="2026-11-05") == VIJF_NOVEMBER
    # De map Weg ligt al in de prullenbak; terugzetten brengt zijn foto (2021) terug
    r = await client.post(f"/api/v1/files/{huis['weg_map']}/restore", headers=eva)
    assert r.status_code == 200, r.text
    jaren = [y for y, _, _ in await herinneringen(client, eva, day="2026-11-05")]
    assert jaren == [1, 3, 5, 25]


async def test_schrikkeldag(client, settings):
    async with maker(settings)() as session:
        eva = await create_user(session, "eva@example.nl")
        for dag in ("2024-02-29", "2020-02-29", "2023-02-28", "2025-02-28", "2024-02-28",
                    "2025-03-01"):  # fmt: skip
            await photo(session, eva, f"{dag}.jpg", om(dag))
        await session.commit()
    eva = await login(client, "eva@example.nl")
    # Op 29 februari: de 29e van eerdere schrikkeljaren
    assert await herinneringen(client, eva, day="2028-02-29") == [
        (4, "2024-02-29", ["2024-02-29.jpg"]),
        (8, "2020-02-29", ["2020-02-29.jpg"]),
    ]
    # Op 28 februari van een jaar zonder 29 februari: ook de 29e
    assert await herinneringen(client, eva, day="2027-02-28") == [
        (2, "2025-02-28", ["2025-02-28.jpg"]),
        (3, "2024-02-28", ["2024-02-28.jpg"]),
        (3, "2024-02-29", ["2024-02-29.jpg"]),
        (4, "2023-02-28", ["2023-02-28.jpg"]),
        (7, "2020-02-29", ["2020-02-29.jpg"]),
    ]
    # Op 28 februari van een schrikkeljaar komt de 29e de dag erna
    assert await herinneringen(client, eva, day="2028-02-28") == [
        (3, "2025-02-28", ["2025-02-28.jpg"]),
        (4, "2024-02-28", ["2024-02-28.jpg"]),
        (5, "2023-02-28", ["2023-02-28.jpg"]),
    ]
    # 1 maart blijft 1 maart
    assert await herinneringen(client, eva, day="2027-03-01") == [
        (2, "2025-03-01", ["2025-03-01.jpg"])
    ]


def test_de_dagen_van_eerdere_jaren():
    assert M.days_before(date(2026, 11, 5), 2023) == [
        date(2025, 11, 5), date(2024, 11, 5), date(2023, 11, 5)]  # fmt: skip
    assert M.days_before(date(2026, 11, 5), 2026) == []
    assert M.days_before(date(2101, 2, 28), 2096) == [
        date(2100, 2, 28), date(2099, 2, 28), date(2098, 2, 28), date(2097, 2, 28),
        date(2096, 2, 28), date(2096, 2, 29)]  # fmt: skip
    # 2000 is een schrikkeljaar, 1900 niet
    schrikkel = [date(year, 2, 29) for year in range(2000, 1895, -4) if year != 1900]
    assert M.days_before(date(2004, 2, 29), 1896) == schrikkel
    assert date(2000, 2, 29) in schrikkel and len(schrikkel) == 26


async def test_vandaag_in_de_tijdzone(client, huis, settings, monkeypatch):
    eva = huis["h_eva"]
    # 23:30 UTC op 4 november is 00:30 op 5 november in Amsterdam (de standaard)
    monkeypatch.setattr(M, "now", lambda: datetime(2026, 11, 4, 23, 30, tzinfo=UTC))
    assert settings.timezone == "Europe/Amsterdam"
    assert await herinneringen(client, eva) == VIJF_NOVEMBER
    monkeypatch.setattr(settings, "timezone", "UTC")
    assert await herinneringen(client, eva) == [(2, "2024-11-04", ["andere_dag.jpg"])]
    # Aan de andere kant van de wereld is het nog 4 november (UTC-10)
    monkeypatch.setattr(M, "now", lambda: datetime(2026, 11, 5, 9, 0, tzinfo=UTC))
    monkeypatch.setattr(settings, "timezone", "Pacific/Honolulu")
    assert await herinneringen(client, eva) == [(2, "2024-11-04", ["andere_dag.jpg"])]


async def test_verbergen(client, huis, settings):
    eva = huis["h_eva"]
    async with maker(settings)() as session:
        anna = Person(owner_id=huis["eva"], name="Anna", search_name="anna",
                      created_at=om("2026-01-01"), updated_at=om("2026-01-01"))  # fmt: skip
        session.add(anna)
        await session.flush()
        session.add(PhotoPerson(person_id=anna.id, node_id=huis["lang_geleden"],
                                owner_id=huis["eva"], source="hand",
                                created_at=om("2026-01-01")))  # fmt: skip
        await session.commit()
        anna_id = anna.id

    async def verberg(kind, ref, expect=204):
        r = await client.post(
            "/api/v1/photos/memories/hide", json={"kind": kind, "ref": ref}, headers=eva
        )
        assert r.status_code == expect, r.text
        return r

    await verberg("day", "2025-11-05")
    assert await herinneringen(client, eva, day="2026-11-05") == VIJF_NOVEMBER[1:]
    # Een map: ook wat in een map eronder ligt (drie_b in Vakantie/Strand)
    await verberg("folder", str(huis["vakantie"]).upper())
    assert await herinneringen(client, eva, day="2026-11-05") == [VIJF_NOVEMBER[2]]
    await verberg("person", str(anna_id))
    assert await herinneringen(client, eva, day="2026-11-05") == []
    # Twee keer is één regel; de lijst in vaste vorm
    await verberg("day", "2025-11-05")
    r = await client.get("/api/v1/photos/memories/hide", headers=eva)
    assert r.json() == {"hides": [
        {"kind": "day", "ref": "2025-11-05"},
        {"kind": "folder", "ref": str(huis["vakantie"])},
        {"kind": "person", "ref": str(anna_id)},
    ]}  # fmt: skip
    # Weer terug
    for kind, ref in (("day", "2025-11-05"), ("folder", huis["vakantie"]), ("person", anna_id)):
        r = await client.delete(f"/api/v1/photos/memories/hide/{kind}/{ref}", headers=eva)
        assert r.status_code == 204, r.text
    assert await herinneringen(client, eva, day="2026-11-05") == VIJF_NOVEMBER
    assert (await client.get("/api/v1/photos/memories/hide", headers=eva)).json() == {"hides": []}
    # Wat er niet was, weghalen: ook goed
    r = await client.delete("/api/v1/photos/memories/hide/day/1999-01-01", headers=eva)
    assert r.status_code == 204
    r = await client.delete("/api/v1/photos/memories/hide/person/geen-id", headers=eva)
    assert r.status_code == 204
    # Brams regels raken Eva niet
    r = await client.post(
        "/api/v1/photos/memories/hide", json={"kind": "day", "ref": "2025-11-05"},
        headers=huis["h_bram"],
    )  # fmt: skip
    assert r.status_code == 204
    assert await herinneringen(client, eva, day="2026-11-05") == VIJF_NOVEMBER


async def test_een_verborgen_map_raakt_zijn_buren_niet(client, settings):
    """Review golf 2, contract C1: een map verbergen verbergt alleen zijn eigen boom, niet een map
    ernaast waarvan het pad erna komt (de bovengrens van het bereik), en niet de map erboven."""
    async with maker(settings)() as session:
        eva = await create_user(session, "eva@example.nl")
        boven = await create_folder(session, eva, name="Boven")
        a = await create_folder(session, eva, boven.id, name="A")
        a_onder = await create_folder(session, eva, a.id, name="Onder")
        b = await create_folder(session, eva, boven.id, name="B")
        for name, dag, parent in (("in-a.jpg", "2024-11-05", a.id),
                                  ("onder-a.jpg", "2022-11-05", a_onder.id),
                                  ("in-b.jpg", "2023-11-05", b.id),
                                  ("boven.jpg", "2021-11-05", boven.id)):  # fmt: skip
            await photo(session, eva, name, om(dag), parent)
        await session.commit()
        a_id = a.id
        paden = [
            await session.scalar(select(Node.mpath).where(Node.id == i))
            for i in (a.id, a_onder.id, b.id)
        ]
    # B komt na A (en na alles onder A): zonder bovengrens viel B er ook onder
    assert paden[0] < paden[1] < paden[2]
    eva = await login(client, "eva@example.nl")
    r = await client.post(
        "/api/v1/photos/memories/hide", json={"kind": "folder", "ref": str(a_id)}, headers=eva
    )
    assert r.status_code == 204, r.text
    assert await herinneringen(client, eva, day="2026-11-05") == [
        (3, "2023-11-05", ["in-b.jpg"]),
        (5, "2021-11-05", ["boven.jpg"]),
    ]


async def test_een_regel_die_niet_klopt(client, huis, settings, monkeypatch):
    eva = huis["h_eva"]
    async with maker(settings)() as session:
        bert = Person(owner_id=huis["bram"], name="Bert", search_name="bert",
                      created_at=om("2026-01-01"), updated_at=om("2026-01-01"))  # fmt: skip
        session.add(bert)
        await session.commit()
        bert_id = bert.id
    fout = [
        ("day", "2025-13-05"),
        ("day", "20251105"),
        ("day", "2025-11-05T00:00"),
        ("day", "5 november"),
        ("folder", "geen-id"),
        ("folder", str(uuid.uuid4())),
        ("folder", str(huis["bram_map"])),  # van een ander
        ("folder", str(huis["vorig_jaar"])),  # een bestand, geen map
        ("folder", str(huis["weg_map"])),  # in de prullenbak
        ("person", str(bert_id)),  # van een ander
        ("person", str(uuid.uuid4())),
        ("person", "x"),
    ]
    for kind, ref in fout:
        r = await client.post(
            "/api/v1/photos/memories/hide", json={"kind": kind, "ref": ref}, headers=eva
        )
        assert (r.status_code, code_of(r)) == (400, "memories_bad_hide"), (kind, ref, r.text)
    for body in (
        {"kind": "jaar", "ref": "2025"},
        {"kind": "day", "ref": "x" * 256},
        {"kind": "day"},
    ):
        r = await client.post("/api/v1/photos/memories/hide", json=body, headers=eva)
        assert r.status_code == 422, body
    async with maker(settings)() as session:
        assert await session.scalar(select(func.count()).select_from(MemoryHide)) == 0
    # Hooguit MAX_HIDES regels
    monkeypatch.setattr(M, "MAX_HIDES", 2)
    for dag in ("2020-01-01", "2020-01-02"):
        r = await client.post(
            "/api/v1/photos/memories/hide", json={"kind": "day", "ref": dag}, headers=eva
        )
        assert r.status_code == 204, r.text
    r = await client.post(
        "/api/v1/photos/memories/hide", json={"kind": "day", "ref": "2020-01-03"}, headers=eva
    )
    assert (r.status_code, code_of(r), params_of(r)) == (400, "memories_bad_hide", {"max": 2})


async def test_hooguit_per_dag_nieuwste_eerst(client, settings, monkeypatch):
    monkeypatch.setattr(M, "PER_DAY", 3)
    async with maker(settings)() as session:
        eva = await create_user(session, "eva@example.nl")
        for uur in range(8, 13):
            await photo(session, eva, f"{uur}.jpg", om("2025-06-01", uur))
        await photo(session, eva, "ander.jpg", om("2024-06-01"))
        await session.commit()
    eva = await login(client, "eva@example.nl")
    assert await herinneringen(client, eva, day="2026-06-01") == [
        (1, "2025-06-01", ["12.jpg", "11.jpg", "10.jpg"]),
        (2, "2024-06-01", ["ander.jpg"]),
    ]


async def test_uit_en_een_gast(client, huis, settings):
    async with maker(settings)() as session:
        session.add(PhotoPref(user_id=huis["eva"], memories=0))
        await create_user(session, "gast@example.nl", role="guest")
        await session.commit()
    assert await herinneringen(client, huis["h_eva"], day="2026-11-05") == []
    gast = await login(client, "gast@example.nl")
    for method, path, body in (
        ("GET", "/api/v1/photos/memories", None),
        ("GET", "/api/v1/photos/memories/hide", None),
        ("POST", "/api/v1/photos/memories/hide", {"kind": "day", "ref": "2025-11-05"}),
        ("DELETE", "/api/v1/photos/memories/hide/day/2025-11-05", None),
    ):
        r = await client.request(method, path, json=body, headers=gast)
        assert (r.status_code, code_of(r)) == (403, "guest"), (method, path)
