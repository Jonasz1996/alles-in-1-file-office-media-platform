"""Het poort-cookie (app/office/gate.py, Keuze 7): check(headers, key=None), still() en
forget_user(), tegen de echte database van de app.

- het cookie: geen, kapot, verlopen, een ander soort token (typ), het office-geheim in plaats van
  het app-geheim, een ander algoritme;
- de gebruiker: uitgelogd (login ingetrokken), geblokkeerd, 2FA verplicht zonder 2FA;
- met een key: geen lid, banned, de deling ingetrokken, het bestand in de prullenbak (de eigenaar
  mag nog, een deelnemer niet), de sessie dicht; zonder key: lid van een sessie die niet dicht is;
- de cache: een ja hooguit 15 s en nooit langer dan het cookie geldt, een nee nooit;
  forget_user() leegt hem;
- office_dev_ungated opent niets meer: niet in de proxy, niet in de tunnel, en geen code leest
  de vlag nog."""

import dataclasses
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import httpx
import jwt
import pytest
from sqlalchemy import update
from starlette.datastructures import Headers

import app as app_package
from app import security
from app.main import app
from app.models import RefreshToken, User
from app.office import gate
from app.security import SecurityConfig, SecurityPolicy
from tests.factories import PASSWORD, code_of, maker
from tests.fake_docserver import protocol
from tests.office_world import office_world
from tests.test_office_tunnel import Clock, public, raw_ds, rebind, status_of, upgrade

pytestmark = pytest.mark.anyio

V = protocol.V


@pytest.fixture
async def world(client, settings):
    async with office_world(client, settings) as w:
        gate.forget()
        try:
            yield w
        finally:
            gate.forget()


@pytest.fixture
def clock(monkeypatch) -> Clock:
    found = Clock()
    monkeypatch.setattr(gate, "clock", found)
    return found


def cookies(*values: str) -> Headers:
    if not values:
        return Headers({})
    return Headers({"cookie": "; ".join(f"office={v}" for v in values)})


async def begin(world, user, node) -> tuple[str, str]:
    """(key, poort-cookie) na POST …/session, zoals de editorpagina."""
    r = await world.open_config(user, node)
    assert r.status_code == 200, r.text
    cookie = r.cookies.get("office")
    assert cookie
    world.client.cookies.clear()
    return r.json()["key"], cookie


async def shared(world):
    """Anna (eigenaar) en Bram (deling met write) samen in de sessie van één bestand."""
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Samen")
    share = await world.share(anna, folder, bram, write=True)
    node = await world.docx(anna, folder, "verslag.docx", ["Begin"])
    key, cookie_a = await begin(world, anna, node)
    key_b, cookie_b = await begin(world, bram, node)
    assert key_b == key
    return SimpleNamespace(
        anna=anna, bram=bram, folder=folder, share=share, node=node, key=key, a=cookie_a, b=cookie_b
    )


def claims_of(world, cookie: str) -> dict:
    return jwt.decode(cookie, world.settings.secret(), algorithms=["HS256"])


def crafted(world, user_id: uuid.UUID, *, secret: str | None = None, alg: str = "HS256", **kw):
    claims = {"sub": str(user_id), "typ": gate.GATE_TYPE, "exp": int(time.time()) + 600, **kw}
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, secret or world.settings.secret(), algorithm=alg)


# --- het cookie ---


async def test_het_cookie_van_post_session_laat_een_lid_door(world):
    s = await shared(world)
    claims = claims_of(world, s.a)
    assert claims["typ"] == "office_gate" and claims["sub"] == str(s.anna.id)
    found = await gate.check(cookies(s.a), s.key)
    assert found == gate.GateUser(s.anna.id, uuid.UUID(claims["fam"]))
    assert await gate.check(cookies(s.a)) == found  # zonder key: lid van een open sessie
    assert (await gate.check(cookies(s.b), s.key)).user_id == s.bram.id
    # Een key die geen sessie is, of een kapotte key: nee
    assert await gate.check(cookies(s.a), "k1-abc") is None
    assert await gate.check(cookies(s.a), "") is None


async def test_geen_of_een_kapot_cookie(world):
    s = await shared(world)
    assert await gate.check(Headers({}), s.key) is None
    assert await gate.check(Headers({}), None) is None
    assert await gate.check(Headers({"cookie": f"platform_session={s.a}"}), s.key) is None
    assert await gate.check(Headers({"cookie": "office="}), s.key) is None
    assert await gate.check(cookies("kapot"), s.key) is None
    assert await gate.check(cookies(s.a[:-2]), s.key) is None
    # Het cookie ergens anders dan in Cookie telt niet
    assert await gate.check(Headers({"authorization": f"Bearer {s.a}"}), s.key) is None
    # Meer cookies: het goede telt, ook naast een kapot
    assert (await gate.check(cookies("kapot", s.a), s.key)).user_id == s.anna.id
    spaced = Headers({"cookie": f"andere=1;  office = {s.a} ; nog=2"})
    assert (await gate.check(spaced, s.key)).user_id == s.anna.id


async def test_verlopen_ander_soort_of_ander_geheim(world):
    s = await shared(world)
    fam = claims_of(world, s.a)["fam"]
    # Zelf gemaakt met dezelfde velden: gaat door (zo weten we dat de rest om de reden faalt)
    assert await gate.check(cookies(crafted(world, s.anna.id, fam=fam)), s.key) is not None
    gate.forget()
    refused = [
        crafted(world, s.anna.id, fam=fam, exp=int(time.time()) - 1),  # verlopen
        crafted(world, s.anna.id, fam=fam, exp=None),  # zonder exp
        crafted(world, s.anna.id, fam=fam, typ="access"),  # ander soort
        crafted(world, s.anna.id, fam=fam, typ=None),  # geen soort
        crafted(world, s.anna.id, fam=fam, secret=world.settings.office_secret),  # office-geheim
        crafted(world, s.anna.id, fam=fam, alg="HS512"),  # ander algoritme
        crafted(world, s.anna.id, fam=fam, sub="geen-uuid"),
        crafted(world, s.anna.id, fam="geen-uuid"),
        jwt.encode({"sub": str(s.anna.id), "typ": "office_gate", "exp": 2**40}, None, "none"),
    ]
    for token in refused:
        assert await gate.check(cookies(token), s.key) is None, jwt.get_unverified_header(token)
    # Het access-token van de app is geen poort-cookie
    row = SimpleNamespace(id=s.anna.id, role="user")
    access = security.access_token(world.settings, row, uuid.UUID(fam))
    assert await gate.check(cookies(access), s.key) is None


# --- de gebruiker ---


async def test_uitgelogd(world, clock):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "a.docx", ["x"])
    r = await world.client.post(
        "/api/v1/auth/login", json={"email": "anna@example.nl", "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    tokens = r.json()
    world.client.cookies.clear()
    logged_in = dataclasses.replace(
        anna, headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    key, cookie = await begin(world, logged_in, node)
    assert await gate.check(cookies(cookie), key) is not None
    r = await world.client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r.status_code == 204
    world.client.cookies.clear()
    clock.offset += 16  # voorbij de cache
    assert await gate.check(cookies(cookie), key) is None
    assert await gate.check(cookies(cookie)) is None
    family = uuid.UUID(claims_of(world, cookie)["fam"])
    assert await gate.still(gate.GateUser(anna.id, family), key) is False


async def test_uitloggen_vergeet_de_poort_meteen(world, clock):
    """Uitloggen trekt de login in, en de poort vergeet hem meteen, niet pas na de cache van
    15 s (o1.md, afwijking 14); het cookie office gaat ook weg."""
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "a.docx", ["x"])
    r = await world.client.post(
        "/api/v1/auth/login", json={"email": "anna@example.nl", "password": PASSWORD}
    )
    assert r.status_code == 200, r.text
    tokens = r.json()
    world.client.cookies.clear()
    logged_in = dataclasses.replace(
        anna, headers={"Authorization": f"Bearer {tokens['access_token']}"}
    )
    key, cookie = await begin(world, logged_in, node)
    family = uuid.UUID(claims_of(world, cookie)["fam"])
    # Alle drie in de cache
    assert await gate.check(cookies(cookie), key) is not None
    assert await gate.check(cookies(cookie)) is not None
    assert await gate.still(gate.GateUser(anna.id, family), key) is True
    r = await world.client.post(
        "/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]}
    )
    assert r.status_code == 204
    gone = [h for h in r.headers.get_list("set-cookie") if h.startswith(f"{gate.GATE_COOKIE}=")]
    assert len(gone) == 1 and "max-age=0" in gone[0].lower(), gone
    assert "path=/office/" in gone[0].lower(), gone
    world.client.cookies.clear()
    # Zonder de klok vooruit te zetten
    assert await gate.check(cookies(cookie), key) is None
    assert await gate.check(cookies(cookie)) is None
    assert await gate.still(gate.GateUser(anna.id, family), key) is False


async def test_geblokkeerd(world, clock):
    s = await shared(world)
    assert await gate.check(cookies(s.b), s.key) is not None
    async with maker(world.base_settings)() as session:
        await session.execute(
            update(User).where(User.id == s.bram.id).values(disabled_at=world.now())
        )
        await session.commit()
    clock.offset += 16
    assert await gate.check(cookies(s.b), s.key) is None
    assert await gate.check(cookies(s.b)) is None
    assert await gate.check(cookies(s.a), s.key) is not None


async def test_2fa_verplicht(world):
    s = await shared(world)
    previous = app.state.security
    app.state.security = SecurityPolicy(SecurityConfig(require_2fa=True))
    try:
        assert await gate.check(cookies(s.a), s.key) is None
        assert await gate.check(cookies(s.a)) is None
        async with maker(world.base_settings)() as session:
            await session.execute(
                update(User).where(User.id == s.anna.id).values(totp_secret="JBSWY3DPEHPK3PXP")
            )
            await session.commit()
        assert await gate.check(cookies(s.a), s.key) is not None
        assert await gate.check(cookies(s.b), s.key) is None
    finally:
        app.state.security = previous


# --- de sessie en het recht van nu ---


async def test_geen_lid(world):
    s = await shared(world)
    cees = await world.user("cees@example.nl")
    await world.share(s.anna, s.folder, cees, write=True)
    # Cees mag schrijven, maar opende de editor nooit: geen lid
    token = crafted(world, cees.id)
    assert await gate.check(cookies(token), s.key) is None
    assert await gate.check(cookies(token)) is None
    # Een nee wordt niet bewaard: na het openen meteen wel
    key, cookie = await begin(world, cees, s.node)
    assert key == s.key
    assert (await gate.check(cookies(token), s.key)).user_id == cees.id
    assert (await gate.check(cookies(cookie), s.key)).user_id == cees.id


async def test_banned(world):
    s = await shared(world)
    await world.set_session(s.key, banned=[world.uid(s.bram)])
    assert await gate.check(cookies(s.b), s.key) is None
    assert await gate.check(cookies(s.b)) is None
    assert await gate.check(cookies(s.a), s.key) is not None


async def test_deling_ingetrokken(world, clock):
    s = await shared(world)
    assert await gate.check(cookies(s.b), s.key) is not None
    await world.revoke(s.anna, s.share)
    clock.offset += 16
    assert await gate.check(cookies(s.b), s.key) is None
    assert await gate.check(cookies(s.b)) is None
    assert await gate.check(cookies(s.a), s.key) is not None


async def test_alleen_lezen_is_geen_schrijfrecht(world):
    s = await shared(world)
    await world.revoke(s.anna, s.share)
    await world.share(s.anna, s.folder, s.bram, write=False)
    assert await gate.check(cookies(s.b), s.key) is None


async def test_bestand_in_de_prullenbak(world):
    """De eigenaar doet mee (opslaan in het bestand in de prullenbak); een deelnemer via een
    deling verliest zijn toegang (Keuze 19)."""
    s = await shared(world)
    await world.trash(s.anna, s.node)
    assert await gate.check(cookies(s.a), s.key) is not None
    assert await gate.check(cookies(s.b), s.key) is None
    assert await gate.check(cookies(s.b)) is None


async def test_sessie_dicht(world):
    s = await shared(world)
    await world.set_session(s.key, state="closed", closed_reason="saved")
    assert await gate.check(cookies(s.a), s.key) is None
    assert await gate.check(cookies(s.a)) is None


async def test_office_niet_ingesteld(world):
    s = await shared(world)
    rebind(world, office_url="")
    assert await gate.check(cookies(s.a), s.key) is None
    assert await gate.still(gate.GateUser(s.anna.id, None), s.key) is False


# --- de cache ---


async def test_cache_hooguit_15_s(world, clock):
    s = await shared(world)
    assert await gate.check(cookies(s.b), s.key) is not None
    assert await gate.check(cookies(s.b)) is not None
    await world.revoke(s.anna, s.share)
    # Binnen 15 s: de cache (Keuze 7: "hooguit 15 s")
    clock.offset += 14.5
    assert await gate.check(cookies(s.b), s.key) is not None
    clock.offset += 1
    assert await gate.check(cookies(s.b), s.key) is None
    assert await gate.check(cookies(s.b)) is None
    # Sleutel: de sha-256 van het cookie plus de key; het cookie zelf staat er niet in
    assert s.b not in repr(gate._by_cookie)
    assert all(len(digest) == 64 for digest, _ in gate._by_cookie)


async def test_cache_nooit_langer_dan_het_cookie(world, clock):
    s = await shared(world)
    fam = claims_of(world, s.b)["fam"]
    short = crafted(world, s.bram.id, fam=fam, exp=int(time.time()) + 5)
    assert await gate.check(cookies(short), s.key) is not None
    (entry,) = [e for (_, k), e in gate._by_cookie.items() if k == s.key]
    assert entry.until - clock() <= 5
    await world.revoke(s.anna, s.share)
    clock.offset += 6  # voorbij exp in de cache, het cookie zelf geldt nog net
    assert await gate.check(cookies(short), s.key) is None


async def test_forget_user_leegt_de_cache(world):
    s = await shared(world)
    bram = gate.GateUser(s.bram.id, uuid.UUID(claims_of(world, s.b)["fam"]))
    assert await gate.check(cookies(s.b), s.key) is not None
    assert await gate.check(cookies(s.b)) is not None
    assert await gate.still(bram, s.key) is True
    assert await gate.check(cookies(s.a), s.key) is not None
    await world.revoke(s.anna, s.share)
    assert await gate.check(cookies(s.b), s.key) is not None  # nog in de cache
    gate.forget_user(str(s.bram.id))  # zoals de ronde hem geeft
    assert not [e for e in gate._by_cookie.values() if e.user.user_id == s.bram.id]
    assert not [e for e in gate._by_user.values() if e.user.user_id == s.bram.id]
    assert [e for e in gate._by_cookie.values() if e.user.user_id == s.anna.id]
    assert await gate.check(cookies(s.b), s.key) is None
    assert await gate.still(bram, s.key) is False
    gate.forget_user(s.anna.id)  # ook als UUID
    assert not gate._by_cookie
    gate.forget_user("geen-uuid")  # geen fout


async def test_still_voor_een_open_tunnel(world, clock):
    s = await shared(world)
    bram = gate.GateUser(s.bram.id, uuid.UUID(claims_of(world, s.b)["fam"]))
    assert await gate.still(bram, s.key) is True
    assert await gate.still(bram, "k1-abc") is False
    await world.revoke(s.anna, s.share)
    assert await gate.still(bram, s.key) is True  # de cache van 15 s
    clock.offset += 16
    assert await gate.still(bram, s.key) is False
    # Een andere login van dezelfde gebruiker die is ingetrokken: nee
    async with maker(world.base_settings)() as session:
        family = uuid.uuid4()
        session.add(
            RefreshToken(
                user_id=s.anna.id,
                family=family,
                token_hash=uuid.uuid4().hex,
                expires_at=world.now(),
                revoked_at=world.now(),
            )
        )
        await session.commit()
    assert await gate.still(gate.GateUser(s.anna.id, family), s.key) is False
    assert await gate.still(gate.GateUser(s.anna.id, None), s.key) is True


# --- office_dev_ungated opent niets meer ---


async def test_office_dev_ungated_opent_niets(world, monkeypatch):
    s = await shared(world)
    rebind(world, office_dev_ungated=True)
    seen: list[str] = []

    def record(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(
            200, headers={"Content-Type": "application/javascript"}, stream=httpx.ByteStream(b"1")
        )

    await world.use_transport(httpx.MockTransport(record))
    client = world.client
    client.cookies.clear()
    page = f"/office/{V}/web-apps/apps/documenteditor/main/index.html"
    data = f"/office/cache/files/data/{s.key}/Editor.bin/Editor.bin"
    for method, path, status, code in [
        ("GET", page, 404, "not_found"),
        ("GET", f"/office/{V}/themes.json", 401, "office_gate"),
        ("GET", data, 401, "office_gate"),
        ("POST", f"/office/{V}/upload/{s.key}", 401, "office_gate"),
        ("POST", f"/office/{V}/downloadas/{s.key}", 401, "office_gate"),
        ("GET", f"/office/{V}/doc/{s.key}/c/?EIO=4&transport=polling", 404, "not_found"),
        ("POST", f"/office/{V}/doc/{s.key}/c/?EIO=4&transport=polling", 404, "not_found"),
        ("POST", f"/office/{V}/command", 404, "not_found"),
        ("GET", "/office/healthcheck", 404, "not_found"),
        ("GET", "/office/", 404, "not_found"),
        ("GET", f"/office/{V}/info/info.json", 404, "not_found"),
    ]:
        r = await client.request(method, path)
        assert (r.status_code, code_of(r)) == (status, code), (method, path)
    assert seen == []  # DS kreeg niets
    # Statisch blijft voor iedereen
    r = await client.get("/office/web-apps/apps/api/documents/api.js")
    assert r.status_code == 200 and seen == ["/web-apps/apps/api/documents/api.js"]
    # De tunnel: zonder cookie 401, ook met de vlag
    async with raw_ds(world) as ds, public(monkeypatch) as listener:
        assert world.settings.office_dev_ungated
        assert await status_of(listener.port, upgrade(listener.port, s.key)) == 401
        assert ds.heads == []
    # En geen code leest de vlag nog (alleen config.py kent hem)
    root = Path(app_package.__file__).parent
    readers = sorted(
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if "office_dev_ungated" in p.read_text(encoding="utf-8")
    )
    assert readers == ["config.py"]
