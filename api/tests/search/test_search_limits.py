"""Hoeveel zoekvragen tegelijk (bouwontwerp fase 6, Keuze 13; app/search/limits.py).

- 3 vragen tegelijk (van 3 gebruikers): 2 lopen, 1 wacht, en die gaat door zodra er een klaar
  is; een wachtende die wegvalt, laat de rij en de plekken kloppen;
- 25 tegelijk: 2 lopen, 20 wachten, de andere 3 krijgen meteen 429 search_busy met
  Retry-After: 1; wie wacht, houdt geen verbinding uit de pool vast;
- een tweede vraag van dezelfde gebruiker stopt de eerste: die geeft meteen een lege,
  gedeeltelijke pagina, en de tweede gaat daarna gewoon;
- de snelheid per gebruiker: de 11e zoekvraag en de 21e suggestie binnen een seconde geven
  429 rate_limited met Retry-After: 1; zoeken en suggesties tellen apart, en elke gebruiker
  apart.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text

from app import security
from app.db import get_engine
from app.models import User
from app.search import engine as engines
from app.search import limits
from app.search.deadline import STOP, deadline
from tests.factories import code_of, create_user, maker
from tests.test_files import put, root

pytestmark = pytest.mark.anyio

# Een vraag die alleen eindigt als ze gestopt wordt (geen LIMIT, en x < 0 is nooit waar)
EINDELOOS = (
    "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) "
    "SELECT x AS id, x AS num, x AS owner_id, '' AS search_name FROM c WHERE x < 0"
)


def iemand() -> User:
    return User(id=uuid.uuid4())


async def tot(check, seconden: float = 10.0) -> None:
    """Wacht tot check() waar is (de event loop draait intussen door)."""
    eind = time.monotonic() + seconden
    while not check():
        assert time.monotonic() < eind, "het gebeurde niet op tijd"
        await asyncio.sleep(0.005)


async def test_drie_tegelijk_geeft_een_keer_wachten(settings):
    los = asyncio.Event()
    binnen: list[int] = []

    async def vraag(i: int) -> None:
        async with limits.admit(iemand(), "search", settings):
            binnen.append(i)
            await los.wait()

    taken = [asyncio.create_task(vraag(i)) for i in range(3)]
    await tot(lambda: len(binnen) == 2)
    state = limits._now_state()
    assert state.running == 2 and len(state.waiting) == 1
    assert sorted(binnen) == [0, 1]
    los.set()
    await asyncio.gather(*taken)
    assert binnen == [0, 1, 2]
    assert state.running == 0 and not state.waiting


async def test_wie_wegvalt_uit_de_rij_laat_alles_kloppen(settings):
    """Een verzoek in de rij dat wegvalt (de browser sluit), ook net nadat het zijn plek kreeg:
    de rij en het aantal plekken kloppen daarna nog."""
    state = limits._now_state()
    los = [asyncio.Event() for _ in range(4)]
    binnen: list[int] = []

    async def vraag(i: int) -> None:
        async with limits.admit(iemand(), "search", settings):
            binnen.append(i)
            await los[i].wait()

    taken = [asyncio.create_task(vraag(i)) for i in range(4)]
    await tot(lambda: len(state.waiting) == 2)
    # Weg terwijl hij wacht: uit de rij, de plekken blijven gelijk
    taken[3].cancel()
    await asyncio.gather(taken[3], return_exceptions=True)
    assert len(state.waiting) == 1 and state.running == 2
    # Weg net nadat hij zijn plek kreeg (en voor hij weer liep): hij geeft hem door
    los[0].set()
    await asyncio.sleep(0)  # vraag 0 gaat eruit en geeft zijn plek aan vraag 2
    taken[2].cancel()
    await asyncio.gather(taken[0], taken[2], return_exceptions=True)
    assert 2 not in binnen
    assert state.running == 1 and not state.waiting
    los[1].set()
    await taken[1]
    assert state.running == 0


async def test_een_tweede_vraag_van_dezelfde_gebruiker_stopt_de_eerste(settings):
    ik = iemand()
    gestopt = asyncio.Event()
    volgorde: list[str] = []

    async def eerste() -> bool:
        async with limits.admit(ik, "search", settings):
            async with get_engine(settings).connect() as conn:
                gestopt.set()
                async with deadline(conn, 60_000) as d:
                    await conn.execute(text(EINDELOOS))
            volgorde.append("eerste")
            return d.hit

    async def tweede() -> None:
        async with limits.admit(ik, "search", settings):
            volgorde.append("tweede")
            assert STOP.get() is not None and not STOP.get().stopped

    taak = asyncio.create_task(eerste())
    await gestopt.wait()
    begin = time.monotonic()
    await asyncio.wait_for(tweede(), 10)
    assert await taak is True
    assert time.monotonic() - begin < 5  # niet pas na de grens van 60 s
    # Eén tegelijk per gebruiker: de tweede liep pas toen de eerste klaar was
    assert volgorde == ["eerste", "tweede"]


async def test_zoeken_en_suggesties_stoppen_elkaar_niet(settings):
    ik = iemand()
    los = asyncio.Event()
    stops = []

    async def vraag(kind: str) -> None:
        async with limits.admit(ik, kind, settings):
            stops.append(STOP.get())
            await los.wait()

    taken = [asyncio.create_task(vraag("search")), asyncio.create_task(vraag("suggest"))]
    await tot(lambda: len(stops) == 2)
    assert not any(s.stopped for s in stops)
    los.set()
    await asyncio.gather(*taken)


def nepklok(monkeypatch) -> list[float]:
    """De klok van limits (alleen die): de test zet hem zelf vooruit."""
    nu = [1000.0]
    monkeypatch.setattr(limits, "time", SimpleNamespace(monotonic=lambda: nu[0]))
    return nu


@pytest.mark.parametrize(("kind", "per_seconde"), [("search", 10), ("suggest", 20)])
async def test_de_snelheid_per_gebruiker(settings, monkeypatch, kind, per_seconde):
    nu = nepklok(monkeypatch)
    ik, ander = iemand(), iemand()
    for _ in range(per_seconde):
        async with limits.admit(ik, kind, settings):
            pass
    with pytest.raises(Exception) as fout:
        async with limits.admit(ik, kind, settings):
            pass
    assert fout.value.status_code == 429
    assert fout.value.detail["code"] == "rate_limited"
    assert fout.value.headers == {"Retry-After": "1"}
    # Een ander, en de andere soort vraag, tellen apart
    async with limits.admit(ander, kind, settings):
        pass
    async with limits.admit(ik, "suggest" if kind == "search" else "search", settings):
        pass
    # Na een seconde mag het weer
    nu[0] += limits.WINDOW_S
    async with limits.admit(ik, kind, settings):
        pass


async def test_zonder_rate_limits_geen_snelheidsgrens(settings, monkeypatch):
    """PLATFORM_RATE_LIMITS=false (een testserver, de meting van §4.4): geen 429 rate_limited,
    wel nog één vraag tegelijk per gebruiker."""
    nepklok(monkeypatch)
    los = settings.model_copy(update={"rate_limits": False})
    ik = iemand()
    for kind in ("search", "suggest"):
        for _ in range(limits.RATE[kind] * 5):
            async with limits.admit(ik, kind, los):
                pass
    # Met de grens aan telt alleen wat daarna komt
    for _ in range(limits.RATE["search"]):
        async with limits.admit(ik, "search", settings):
            pass
    with pytest.raises(Exception) as fout:
        async with limits.admit(ik, "search", settings):
            pass
    assert fout.value.detail["code"] == "rate_limited"


# --- Via GET /search ------------------------------------------------------------------------


async def koppen(settings, n: int) -> list[dict[str, str]]:
    """n gebruikers met elk een eigen access-token (zonder in te loggen: de login heeft zijn
    eigen grens)."""
    async with maker(settings)() as s:
        users = [await create_user(s, f"zoeker{i}@example.nl") for i in range(n)]
        await s.commit()
    return [
        {"Authorization": f"Bearer {security.access_token(settings, u, uuid.uuid4())}"}
        for u in users
    ]


async def test_25_tegelijk_geeft_search_busy(client, admin, settings, monkeypatch):
    los = asyncio.Event()
    binnen: list[str] = []

    async def wacht(self, db, user, p):
        binnen.append(user.email)
        await los.wait()
        return engines.SearchPage(results=[], more=False, engine="fts")

    monkeypatch.setattr(engines.FtsEngine, "search", wacht)
    headers = await koppen(settings, 25)
    taken = [
        asyncio.create_task(client.get("/api/v1/search", params={"q": "x"}, headers=h))
        for h in headers
    ]
    state = None

    def vol() -> bool:
        nonlocal state
        state = limits._now_state()
        return state.running == 2 and len(state.waiting) == 20

    await tot(vol)
    await tot(lambda: sum(t.done() for t in taken) == 3)
    druk = [t.result() for t in taken if t.done()]
    for r in druk:
        assert r.status_code == 429, r.text
        assert code_of(r) == "search_busy"
        assert r.headers["retry-after"] == "1"
    assert len(binnen) == 2
    # Wie in de rij wacht, houdt geen verbinding vast: de rest van de app werkt gewoon door
    admin_kop, _ = admin
    r = await asyncio.wait_for(client.get("/api/v1/files/root", headers=admin_kop), 5)
    assert r.status_code == 200
    los.set()
    klaar = await asyncio.gather(*taken)
    assert sorted(r.status_code for r in klaar) == [200] * 22 + [429] * 3
    assert len(binnen) == 22
    assert state.running == 0 and not state.waiting


async def test_de_tweede_vraag_stopt_de_eerste_via_de_api(client, admin, settings, monkeypatch):
    """De eerste vraag zit in ronde A (die alleen eindigt als ze stopt, met een grens van 60 s);
    de tweede stopt haar: de eerste geeft meteen een lege, gedeeltelijke pagina, zonder ronde B
    of C, en de tweede vindt gewoon wat er is."""
    headers, _ = admin
    root_id = (await root(client, headers))["folder"]["id"]
    await put(client, headers, root_id, "Jaarverslag 2025.pdf", b"%PDF-1.4\n")
    monkeypatch.setattr(settings, "search_deadline_ms", 60_000)
    echte = engines.ranked_select
    in_a = asyncio.Event()
    rondes: list[str] = []

    def ranked(parsed, scope, k):
        if not in_a.is_set():
            in_a.set()
            return text(EINDELOOS)
        return echte(parsed, scope, k)

    def stream(*args, **kwargs):
        rondes.append("B")
        return engines_stream(*args, **kwargs)

    engines_stream = engines.stream_select
    monkeypatch.setattr(engines, "ranked_select", ranked)
    monkeypatch.setattr(engines, "stream_select", stream)
    eerste = asyncio.create_task(
        client.get("/api/v1/search", params={"q": "jaarverslag"}, headers=headers)
    )
    await asyncio.wait_for(in_a.wait(), 10)
    await asyncio.sleep(0.05)  # ronde A loopt
    begin = time.monotonic()
    r2 = await asyncio.wait_for(
        client.get("/api/v1/search", params={"q": "jaarverslag"}, headers=headers), 10
    )
    r1 = await asyncio.wait_for(eerste, 10)
    assert time.monotonic() - begin < 5
    assert r1.status_code == 200, r1.text
    page = r1.json()
    assert page["results"] == [] and page["partial"] is True and page["more"] is False
    assert page["engine"] == "fts" and page["names_only"] is False
    assert rondes == []  # geen ronde B (en dus ook geen C) na het stoppen
    assert r2.status_code == 200, r2.text
    assert [h["node"]["name"] for h in r2.json()["results"]] == ["Jaarverslag 2025.pdf"]
    assert r2.json()["partial"] is False


async def test_de_snelheid_via_de_api(client, settings, monkeypatch):
    nepklok(monkeypatch)
    (kop,) = await koppen(settings, 1)
    for _ in range(limits.RATE["search"]):
        r = await client.get("/api/v1/search", params={"q": "x"}, headers=kop)
        assert r.status_code == 200, r.text
    r = await client.get("/api/v1/search", params={"q": "x"}, headers=kop)
    assert r.status_code == 429 and code_of(r) == "rate_limited", r.text
    assert r.headers["retry-after"] == "1"
    # Suggesties tellen apart
    r = await client.get("/api/v1/search/suggest", params={"q": "x"}, headers=kop)
    assert r.status_code == 200, r.text
