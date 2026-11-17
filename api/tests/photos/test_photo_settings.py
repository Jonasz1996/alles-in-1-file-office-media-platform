"""De foto-instellingen (bouwontwerp fase 6, §3.4 en Keuze 44; Z6): GET en PATCH
/me/photo-settings.

- GET: prefs.photo_settings(), met de standaarden zolang er geen rij is;
- PATCH: alleen wat verandert; eerst prefs.check_switch() met alleen de velden die echt anders
  worden (z0b.md, Open 5), dan schrijven, dan prefs.switched();
- PATCH {faces: "aan"} zonder X2 geeft 409 faces_unavailable (Z6 in het bouwontwerp), en een
  weigering schrijft niets;
- herinneringen uit: de lijst is leeg.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app import prefs
from app.models import PhotoPref
from tests.factories import code_of, create_user, maker
from tests.test_photos import login, photo

pytestmark = [pytest.mark.anyio, pytest.mark.zoek]

STANDAARD = {
    "location": "mij",
    "memories": True,
    "ocr_photos": False,
    "faces": "uit",
    "faces_forbidden": False,
    "faces_available": False,
    "beeld": "uit",
}


@pytest.fixture(autouse=True)
def _zonder_aanmelding(monkeypatch):
    """Geen X2 of X3, tenzij een test zich aanmeldt (en dan alleen voor die test)."""
    monkeypatch.setattr(prefs, "_switches", {})


@pytest.fixture
async def eva(client, settings):
    async with maker(settings)() as session:
        user = await create_user(session, "eva@example.nl")
        await photo(session, user, "vorig-jaar.jpg", datetime(2025, 11, 5, 12, tzinfo=UTC))
        await create_user(session, "gast@example.nl", role="guest")
        await session.commit()
    return await login(client, "eva@example.nl")


async def instellingen(client, headers) -> dict:
    r = await client.get("/api/v1/me/photo-settings", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def wijzig(client, headers, expect=200, **body):
    r = await client.patch("/api/v1/me/photo-settings", json=body, headers=headers)
    assert r.status_code == expect, r.text
    return r


async def rijen(settings) -> list[tuple]:
    async with maker(settings)() as session:
        rows = await session.scalars(select(PhotoPref))
        return [(r.location, r.memories, r.ocr_photos, r.faces) for r in rows]


def aanmelden(key: str, state: prefs.SwitchState, gezien: list) -> None:
    async def stand(db, user):
        return state

    async def changed(db, user, value):
        gezien.append((key, value))

    prefs.register_switch(key, prefs.Switch(state=stand, changed=changed))


async def test_standaard_en_wijzigen(client, eva, settings):
    assert await instellingen(client, eva) == STANDAARD
    assert await rijen(settings) == []
    # Niets dat verandert: ook geen rij
    r = await wijzig(client, eva, location="mij", memories=True, faces="uit", beeld="uit")
    assert r.json() == STANDAARD
    assert await rijen(settings) == []
    r = await wijzig(client, eva, location="uit", ocr_photos=True)
    assert r.json() == {**STANDAARD, "location": "uit", "ocr_photos": True}
    assert await instellingen(client, eva) == r.json()
    assert await rijen(settings) == [("uit", 1, 1, "uit")]
    await wijzig(client, eva, location="mij")
    assert await rijen(settings) == [("mij", 1, 1, "uit")]
    # Wat de API niet kent: 422, en er verandert niets
    for body in ({"faces": "misschien"}, {"location": "iedereen"}, {"onbekend": 1},
                 {"memories": "soms"}):  # fmt: skip
        await wijzig(client, eva, expect=422, **body)
    assert await rijen(settings) == [("mij", 1, 1, "uit")]
    r = await client.get("/api/v1/me/photo-settings")
    assert r.status_code == 401


async def test_herinneringen_uit_geeft_een_lege_lijst(client, eva):
    dag = {"day": "2026-11-05"}
    r = await client.get("/api/v1/photos/memories", params=dag, headers=eva)
    assert [y["years_ago"] for y in r.json()["years"]] == [1]
    await wijzig(client, eva, memories=False)
    r = await client.get("/api/v1/photos/memories", params=dag, headers=eva)
    assert r.json()["years"] == []
    await wijzig(client, eva, memories=True)
    r = await client.get("/api/v1/photos/memories", params=dag, headers=eva)
    assert [y["years_ago"] for y in r.json()["years"]] == [1]


async def test_gezichten_en_beeld_aan_zonder_x2_en_x3(client, eva, settings):
    r = await wijzig(client, eva, expect=409, faces="aan")
    assert code_of(r) == "faces_unavailable"
    r = await wijzig(client, eva, expect=409, beeld="aan")
    assert code_of(r) == "beeld_not_installed"
    # Samen met iets anders: niets geschreven
    r = await wijzig(client, eva, expect=409, faces="aan", memories=False, location="uit")
    assert await rijen(settings) == []
    assert await instellingen(client, eva) == STANDAARD
    # Ook met de analyse aan, zolang X2 zich niet aanmeldt
    settings.analyse_enabled = True
    try:
        r = await wijzig(client, eva, expect=409, faces="aan")
        assert code_of(r) == "faces_unavailable"
    finally:
        settings.analyse_enabled = False


async def test_met_x2_aangemeld(client, eva, settings, monkeypatch):
    gezien: list[tuple] = []
    aanmelden("faces", prefs.SwitchState(available=True), gezien)
    # De analyse uit: Keuze 59 wint
    r = await wijzig(client, eva, expect=409, faces="aan")
    assert code_of(r) == "faces_unavailable"
    monkeypatch.setattr(settings, "analyse_enabled", True)
    assert (await instellingen(client, eva))["faces_available"] is True
    r = await wijzig(client, eva, faces="aan")
    assert r.json()["faces"] == "aan"
    assert gezien == [("faces", "aan")]
    assert await rijen(settings) == [("mij", 1, 0, "aan")]
    # Al aan: geen verandering, dus geen controle (z0b.md, Open 5) en geen melding, ook niet als
    # aanzetten nu niet meer kan
    monkeypatch.setattr(settings, "analyse_enabled", False)
    r = await wijzig(client, eva, faces="aan", memories=False)
    assert (r.json()["faces"], r.json()["memories"]) == ("aan", False)
    assert gezien == [("faces", "aan")]
    # Uit mag altijd, en X2 hoort het
    r = await wijzig(client, eva, faces="uit")
    assert r.json()["faces"] == "uit"
    assert gezien == [("faces", "aan"), ("faces", "uit")]
    # De beheerder verbiedt het
    verboden = prefs.SwitchState(forbidden=True, available=True)
    prefs.register_switch("faces", prefs.Switch(state=_vast(verboden), changed=_niets))
    monkeypatch.setattr(settings, "analyse_enabled", True)
    r = await wijzig(client, eva, expect=403, faces="aan")
    assert code_of(r) == "faces_forbidden"
    assert (await instellingen(client, eva))["faces_forbidden"] is True


async def test_op_postgresql_niet_beschikbaar(client, eva, settings, monkeypatch):
    """Review A0, veilig F5: de route vraagt analyse_on, niet alleen PLATFORM_ANALYSE_ENABLED.
    Op PostgreSQL (analyse_on False, zie test_op_postgresql_blijft_de_analyse_uit) gaan
    gezichten en beeld niet aan, ook als X2 en X3 zich aanmelden."""
    gezien: list[tuple] = []
    aanmelden("faces", prefs.SwitchState(available=True), gezien)
    aanmelden("beeld", prefs.SwitchState(available=True), gezien)
    monkeypatch.setattr(settings, "analyse_enabled", True)
    assert (await instellingen(client, eva))["faces_available"] is True
    monkeypatch.setattr(type(settings), "analyse_on", property(lambda self: False))
    assert (await instellingen(client, eva))["faces_available"] is False
    r = await wijzig(client, eva, expect=409, faces="aan")
    assert code_of(r) == "faces_unavailable"
    r = await wijzig(client, eva, expect=409, beeld="aan")
    assert code_of(r) == "beeld_not_installed"
    assert gezien == [] and await rijen(settings) == []
    # Nacontrole A0: ook het antwoord op een andere wijziging zegt dat gezichten er niet zijn
    r = await wijzig(client, eva, memories=False)
    assert r.json() == {**STANDAARD, "memories": False}
    assert gezien == [] and await rijen(settings) == [("mij", False, False, "uit")]


async def test_een_gast(client, eva, settings):
    gast = await login(client, "gast@example.nl")
    assert await instellingen(client, gast) == STANDAARD
    r = await wijzig(client, gast, expect=403, faces="aan")
    assert code_of(r) == "guest"
    # Uit terwijl het al uit is: geen verandering, dus ook geen weigering
    await wijzig(client, gast, faces="uit")
    assert await rijen(settings) == []
    # De andere velden ook niet (review golf 2, veilig V3): een gast heeft geen eigen foto's
    for body in ({"memories": False}, {"location": "uit"}, {"ocr_photos": True},
                 {"memories": False, "location": "uit", "ocr_photos": True}):  # fmt: skip
        r = await wijzig(client, gast, expect=403, **body)
        assert code_of(r) == "guest"
    assert await rijen(settings) == []
    assert await instellingen(client, gast) == STANDAARD
    # Niets dat verandert: 200, zoals voor iedereen
    r = await wijzig(client, gast, memories=True, location="mij")
    assert r.json() == STANDAARD


def _vast(state: prefs.SwitchState):
    async def stand(db, user):
        return state

    return stand


async def _niets(db, user, value):
    return None
