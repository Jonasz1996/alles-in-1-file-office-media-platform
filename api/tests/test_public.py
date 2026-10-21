"""Publieke links: openen met token (en wachtwoord), bekijken, downloaden en uploaden zonder
in te loggen, en dat er niets meer werkt zodra de link weg, verlopen of in de prullenbak is."""

import asyncio
import base64
import logging
import uuid
from datetime import UTC, datetime, timedelta

import anyio
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.db import get_engine
from app.main import HideSecrets, app
from app.models import Share, Upload
from app.routers import files, public, uploads
from app.security import _encode
from tests.test_files import check_refs, mkdir, put, root
from tests.test_shares import person
from tests.test_uploads import PATCH, TUS, hold

TOKEN_PATH = "/s/"
BASE = "http://test"


@pytest.fixture(autouse=True)
def fresh_limits():
    for limiter in (public.open_limiter, public.upload_limiter, public.password_limiter):
        limiter.reset()
    yield
    for limiter in (public.open_limiter, public.upload_limiter, public.password_limiter):
        limiter.reset()


async def new_link(client, headers, node_id, expect=201, **body):
    r = await client.post(f"/api/v1/files/{node_id}/links", json=body, headers=headers)
    assert r.status_code == expect, r.text
    return r.json()


def token_of(link) -> str:
    url = link["link"]["url"]
    assert url.startswith(TOKEN_PATH)
    return url[len(TOKEN_PATH) :]


async def open_link(client, link, password=None, expect=200):
    body = {"token": token_of(link)}
    if password is not None:
        body["password"] = password
    r = await client.post("/api/v1/public/open", json=body)
    assert r.status_code == expect, r.text
    return r.json()


def bearer(key: str) -> dict:
    return {"Authorization": f"Bearer {key}"}


def detail_code(r):
    detail = r.json()["detail"]
    return detail["code"] if isinstance(detail, dict) else detail


@pytest.fixture
async def tree(client, admin):
    """Jonas met /Werk/Fotos/{Vakantie/zee.jpg, a.txt, pagina.html} en /Werk/geheim.txt."""
    jonas, tokens = admin
    root_id = (await root(client, jonas))["folder"]["id"]
    werk = await mkdir(client, jonas, root_id, "Werk")
    fotos = await mkdir(client, jonas, werk, "Fotos")
    vakantie = await mkdir(client, jonas, fotos, "Vakantie")
    zee = (await put(client, jonas, vakantie, "zee.jpg", b"\xff\xd8\xffjpeg")).json()["id"]
    a = (await put(client, jonas, fotos, "a.txt", b"0123456789")).json()["id"]
    html = (await put(client, jonas, fotos, "pagina.html", b"<script>1</script>")).json()["id"]
    geheim = (await put(client, jonas, werk, "geheim.txt", b"geheim")).json()["id"]
    return {"jonas": jonas, "jonas_id": tokens["user"]["id"], "root": root_id, "werk": werk,
            "fotos": fotos, "vakantie": vakantie, "zee": zee, "a": a, "html": html,
            "geheim": geheim}  # fmt: skip


async def set_expiry(settings, share_id, when):
    async with async_sessionmaker(get_engine(settings))() as session:
        await session.execute(
            update(Share).where(Share.id == uuid.UUID(share_id)).values(expires_at=when)
        )
        await session.commit()


@pytest.mark.anyio
async def test_link_aanmaken(client, tree):
    t = tree
    jonas = t["jonas"]
    link = await new_link(client, jonas, t["fotos"], permission="read", label="  Voor oma  ")
    assert link["type"] == "link" and link["permission"] == "read"
    assert link["link"]["label"] == "Voor oma" and link["link"]["has_password"] is False
    assert len(token_of(link)) >= 40
    assert link["user"] is None and link["group"] is None
    later = datetime.now(UTC) + timedelta(days=3)
    link2 = await new_link(
        client,
        jonas,
        t["fotos"],
        permission="upload",
        password="geheim1",
        expires_at=later.isoformat(),
    )
    assert link2["link"]["has_password"] is True
    assert datetime.fromisoformat(link2["expires_at"]) == later
    shares = (await client.get(f"/api/v1/files/{t['fotos']}/shares", headers=jonas)).json()
    assert [s["id"] for s in shares] == [link["id"], link2["id"]]
    # De eigenaar ziet dat het gedeeld is
    listing = (await client.get(f"/api/v1/files/{t['werk']}/children", headers=jonas)).json()
    assert {c["name"]: c["shared"] for c in listing["children"]}["Fotos"] is True

    for node, body, expect in [
        (t["a"], {"permission": "upload"}, "upload_folder_only"),
        (t["root"], {"permission": "read"}, "root"),
        (t["fotos"], {"permission": "read", "password": "kort"}, "password_too_short"),
        (t["fotos"], {"permission": "read", "label": "x" * 101}, "label_too_long"),
        (t["fotos"], {"permission": "read", "expires_at": "2020-01-01T00:00:00Z"}, "expires_past"),
    ]:
        r = await client.post(f"/api/v1/files/{node}/links", json=body, headers=jonas)
        assert r.status_code == 422 and detail_code(r) == expect, r.text
    r = await client.post(
        f"/api/v1/files/{t['fotos']}/links", json={"permission": "write"}, headers=jonas
    )
    assert r.status_code == 422
    anna, _ = await person(client, jonas, "anna@example.nl")
    r = await client.post(
        f"/api/v1/files/{t['fotos']}/links", json={"permission": "read"}, headers=anna
    )
    assert r.status_code == 404
    # Een leeg wachtwoord is geen wachtwoord
    link3 = await new_link(client, jonas, t["a"], permission="read", password="")
    assert link3["link"]["has_password"] is False


@pytest.mark.anyio
async def test_openen_en_bekijken(client, tree):
    t = tree
    link = await new_link(client, t["jonas"], t["fotos"], permission="read")
    opened = await open_link(client, link)
    assert 12 * 3600 - 5 <= opened["expires_in"] <= 12 * 3600
    share = opened["share"]
    assert share["kind"] == "dir" and share["name"] == "Fotos" and share["permission"] == "read"
    assert share["owner_name"] == "Jonas" and share["expires_at"] is None
    assert share["root"]["id"] == t["fotos"]
    assert set(share["root"]) == {"id", "name", "kind", "mime", "size", "etag", "modified_at"}
    key = opened["key"]

    r = await client.get("/api/v1/public/children", headers=bearer(key))
    assert r.status_code == 200, r.text
    listing = r.json()
    assert listing["folder"]["id"] == t["fotos"]
    assert [p["name"] for p in listing["path"]] == ["Fotos"]
    assert [c["name"] for c in listing["children"]] == ["Vakantie", "a.txt", "pagina.html"]
    for child in listing["children"]:
        assert "parent_id" not in child and "owner" not in child and "tags" not in child
    r = await client.get(
        "/api/v1/public/children", params={"folder": t["vakantie"]}, headers=bearer(key)
    )
    assert [p["name"] for p in r.json()["path"]] == ["Fotos", "Vakantie"]
    assert [c["name"] for c in r.json()["children"]] == ["zee.jpg"]
    # Erboven of ernaast: bestaat niet
    for folder in (t["werk"], t["root"], str(uuid.uuid4()), t["a"]):
        r = await client.get(
            "/api/v1/public/children", params={"folder": folder}, headers=bearer(key)
        )
        assert r.status_code == 404, folder

    # Downloaden met de header of met ?key= (zoals een <a> of <img>)
    r = await client.get(f"/api/v1/public/download/{t['a']}", headers=bearer(key))
    assert r.content == b"0123456789" and "attachment" in r.headers["content-disposition"]
    r = await client.get(f"/api/v1/public/download/{t['zee']}", params={"key": key, "inline": True})
    assert r.status_code == 200 and "inline" in r.headers["content-disposition"]
    r = await client.get(
        f"/api/v1/public/download/{t['a']}", headers={**bearer(key), "Range": "bytes=2-4"}
    )
    assert r.status_code == 206 and r.content == b"234"
    etag = r.headers["etag"]
    r = await client.get(
        f"/api/v1/public/download/{t['a']}", headers={**bearer(key), "If-None-Match": etag}
    )
    assert r.status_code == 304
    r = await client.get(f"/api/v1/public/download/{t['html']}", params={"key": key})
    assert r.headers["content-security-policy"] == "sandbox"
    for node in (t["geheim"], t["vakantie"], str(uuid.uuid4())):
        r = await client.get(f"/api/v1/public/download/{node}", params={"key": key})
        assert r.status_code == 404, node
    r = await client.post(
        "/api/v1/public/previews",
        json={"node_ids": [t["a"], t["zee"], t["geheim"], t["vakantie"]]},
        headers=bearer(key),
    )
    assert sorted(r.json()) == sorted([t["a"], t["zee"]])
    # Weggegooid binnen de gedeelde map: weg voor de link
    await client.delete(f"/api/v1/files/{t['vakantie']}", headers=t["jonas"])
    r = await client.get(
        "/api/v1/public/children", params={"folder": t["vakantie"]}, headers=bearer(key)
    )
    assert r.status_code == 404
    assert (
        await client.get(f"/api/v1/public/download/{t['zee']}", params={"key": key})
    ).status_code == 404
    r = await client.post(
        "/api/v1/public/previews", json={"node_ids": [t["zee"]]}, headers=bearer(key)
    )
    assert r.json() == {}
    # Zonder sleutel of met onzin
    assert (await client.get("/api/v1/public/children")).status_code == 401
    r = await client.get("/api/v1/public/children", headers=bearer("onzin"))
    assert r.status_code == 401 and detail_code(r) == "key_expired"


@pytest.mark.anyio
async def test_link_naar_een_bestand(client, tree):
    t = tree
    link = await new_link(client, t["jonas"], t["a"], permission="read")
    opened = await open_link(client, link)
    assert opened["share"]["kind"] == "file" and opened["share"]["root"]["size"] == 10
    key = opened["key"]
    r = await client.get("/api/v1/public/children", headers=bearer(key))
    assert r.status_code == 422
    assert (await client.get(f"/api/v1/public/download/{t['a']}", params={"key": key})).content == (
        b"0123456789"
    )
    r = await client.get(f"/api/v1/public/download/{t['html']}", params={"key": key})
    assert r.status_code == 404
    r = await client.post(
        "/api/v1/public/previews", json={"node_ids": [t["a"], t["html"]]}, headers=bearer(key)
    )
    assert list(r.json()) == [t["a"]]


@pytest.mark.anyio
async def test_link_bestaat_niet_verlopen_of_in_de_prullenbak(client, tree, settings):
    t = tree
    jonas = t["jonas"]
    r = await client.post("/api/v1/public/open", json={"token": "bestaat-niet"})
    assert r.status_code == 404
    assert r.json()["detail"] == {
        "message": "Deze link bestaat niet (meer)",
        "code": "link_not_found",
    }
    assert (await client.post("/api/v1/public/open", json={"token": ""})).status_code == 404
    link = await new_link(client, jonas, t["fotos"], permission="read")
    key = (await open_link(client, link))["key"]

    # Verlopen: 410, ook voor een sleutel die al uit was
    await set_expiry(settings, link["id"], datetime.now(UTC) - timedelta(minutes=1))
    r = await client.post("/api/v1/public/open", json={"token": token_of(link)})
    assert r.status_code == 410
    assert r.json()["detail"] == {"message": "Deze link is verlopen", "code": "link_expired"}
    r = await client.get("/api/v1/public/children", headers=bearer(key))
    assert r.status_code == 410 and detail_code(r) == "link_expired"
    # Weer geldig; de sleutel verloopt niet later dan de link
    later = datetime.now(UTC) + timedelta(hours=1)
    await client.patch(
        f"/api/v1/shares/{link['id']}", json={"expires_at": later.isoformat()}, headers=jonas
    )
    opened = await open_link(client, link)
    assert 3590 <= opened["expires_in"] <= 3600
    assert (await client.get("/api/v1/public/children", headers=bearer(key))).status_code == 200

    # In de prullenbak (of een map erboven): bestaat niet meer
    await client.delete(f"/api/v1/files/{t['werk']}", headers=jonas)
    await open_link(client, link, expect=404)
    r = await client.get("/api/v1/public/children", headers=bearer(key))
    assert r.status_code == 404 and detail_code(r) == "link_not_found"
    await client.post(f"/api/v1/files/{t['werk']}/restore", headers=jonas)
    assert (await client.get("/api/v1/public/children", headers=bearer(key))).status_code == 200

    # Weggehaald: ook de sleutels werken niet meer
    assert (await client.delete(f"/api/v1/shares/{link['id']}", headers=jonas)).status_code == 204
    await open_link(client, link, expect=404)
    r = await client.get(f"/api/v1/public/download/{t['a']}", params={"key": key})
    assert r.status_code == 404 and detail_code(r) == "link_not_found"


@pytest.mark.anyio
async def test_wachtwoord(client, tree, caplog):
    t = tree
    jonas = t["jonas"]
    link = await new_link(client, jonas, t["fotos"], permission="read", password="appeltaart")
    r = await client.post("/api/v1/public/open", json={"token": token_of(link)})
    assert r.status_code == 401
    assert r.json()["detail"] == {
        "message": "Voor deze link is een wachtwoord nodig",
        "code": "password_required",
    }
    with caplog.at_level(logging.WARNING, logger="platform.public"):
        r = await client.post(
            "/api/v1/public/open", json={"token": token_of(link), "password": "fout"}
        )
    assert r.status_code == 401
    assert r.json()["detail"] == {"message": "Wachtwoord klopt niet", "code": "wrong_password"}
    # In het log: welke link en vanaf waar, nooit het wachtwoord of het token
    logged = " ".join(rec.getMessage() for rec in caplog.records)
    assert link["id"] in logged and "fout" not in logged and token_of(link) not in logged
    key = (await open_link(client, link, password="appeltaart"))["key"]
    assert (await client.get("/api/v1/public/children", headers=bearer(key))).status_code == 200

    # Ander wachtwoord: de oude sleutel werkt niet meer
    r = await client.patch(
        f"/api/v1/shares/{link['id']}", json={"password": "perentaart"}, headers=jonas
    )
    assert r.status_code == 200 and r.json()["link"]["has_password"] is True
    r = await client.get("/api/v1/public/children", headers=bearer(key))
    assert r.status_code == 401 and detail_code(r) == "key_expired"
    await open_link(client, link, password="appeltaart", expect=401)
    key = (await open_link(client, link, password="perentaart"))["key"]
    # Geen wachtwoord meer: ook dan werkt de oude sleutel niet meer
    r = await client.patch(f"/api/v1/shares/{link['id']}", json={"password": ""}, headers=jonas)
    assert r.json()["link"]["has_password"] is False
    assert (await client.get("/api/v1/public/children", headers=bearer(key))).status_code == 401
    key = (await open_link(client, link))["key"]
    assert (await client.get("/api/v1/public/children", headers=bearer(key))).status_code == 200
    # Label aanpassen raakt de sleutels niet
    r = await client.patch(f"/api/v1/shares/{link['id']}", json={"label": "Nieuw"}, headers=jonas)
    assert r.json()["link"]["label"] == "Nieuw"
    assert (await client.get("/api/v1/public/children", headers=bearer(key))).status_code == 200
    r = await client.patch(f"/api/v1/shares/{link['id']}", json={"password": "kort"}, headers=jonas)
    assert r.status_code == 422


@pytest.mark.anyio
async def test_te_vaak_een_fout_wachtwoord(client, tree):
    t = tree
    link = await new_link(client, t["jonas"], t["fotos"], permission="read", password="appeltaart")
    other = await new_link(client, t["jonas"], t["a"], permission="read", password="appeltaart")
    for _ in range(10):
        await open_link(client, link, password="fout", expect=401)
    # Nu dicht voor iedereen, ook met het goede wachtwoord
    r = await client.post(
        "/api/v1/public/open", json={"token": token_of(link), "password": "appeltaart"}
    )
    assert r.status_code == 429 and detail_code(r) == "rate_limited"
    assert 0 < int(r.headers["retry-after"]) <= 600
    # Een andere link heeft er geen last van
    await open_link(client, other, password="appeltaart")


@pytest.mark.anyio
async def test_foute_wachtwoorden_tegelijk_niet_boven_de_limiet(client, tree):
    """Veel foute wachtwoorden tegelijk, vanaf verschillende adressen (elk met een eigen limiet
    voor openen): er worden er hooguit 10 gecontroleerd, de rest krijgt meteen 429. Het goede
    wachtwoord telt niet mee."""
    t = tree
    link = await new_link(client, t["jonas"], t["fotos"], permission="read", password="appeltaart")
    for _ in range(5):
        await open_link(client, link, password="appeltaart")
    others = [
        AsyncClient(transport=ASGITransport(app=app, client=(f"10.0.0.{i}", 1234)), base_url=BASE)
        for i in range(5)
    ]

    async def attempt(c, n):
        r = await c.post("/api/v1/public/open", json={"token": token_of(link), "password": f"x{n}"})
        return r.status_code, detail_code(r)

    try:
        results = await asyncio.gather(
            *(attempt(c, i * 100 + n) for i, c in enumerate(others) for n in range(20))
        )
    finally:
        for c in others:
            await c.aclose()
    assert results.count((401, "wrong_password")) == 10
    assert results.count((429, "rate_limited")) == 90


@pytest.mark.anyio
async def test_veel_mensen_tegelijk_met_het_goede_wachtwoord(client, tree):
    """Een link met wachtwoord die in een groepsapp rondgaat: velen openen hem tegelijk. Alleen
    foute wachtwoorden tellen; wie het goede geeft, komt er altijd in, ook tussen foute door."""
    t = tree
    link = await new_link(client, t["jonas"], t["fotos"], permission="read", password="appeltaart")
    others = [
        AsyncClient(transport=ASGITransport(app=app, client=(f"10.0.1.{i}", 1234)), base_url=BASE)
        for i in range(24)
    ]

    async def attempt(c, password):
        r = await c.post(
            "/api/v1/public/open", json={"token": token_of(link), "password": password}
        )
        return r.status_code

    try:
        results = await asyncio.gather(
            *(attempt(c, "fout" if i % 4 == 0 else "appeltaart") for i, c in enumerate(others))
        )
    finally:
        for c in others:
            await c.aclose()
    assert results.count(200) == 18
    assert results.count(401) == 6  # zes fout: onder de limiet van tien


@pytest.mark.anyio
async def test_te_vaak_openen_vanaf_een_adres(client, tree):
    t = tree
    link = await new_link(client, t["jonas"], t["fotos"], permission="read")
    for _ in range(20):
        await open_link(client, link)
    r = await client.post("/api/v1/public/open", json={"token": token_of(link)})
    assert r.status_code == 429 and "retry-after" in r.headers


@pytest.mark.anyio
async def test_sleutels_horen_bij_hun_eigen_link(client, tree, settings):
    t = tree
    jonas = t["jonas"]
    fotos = await new_link(client, jonas, t["fotos"], permission="read")
    vakantie = await new_link(client, jonas, t["vakantie"], permission="read")
    key = (await open_link(client, vakantie))["key"]
    r = await client.get(
        "/api/v1/public/children", params={"folder": t["fotos"]}, headers=bearer(key)
    )
    assert r.status_code == 404
    assert (
        await client.get(f"/api/v1/public/download/{t['a']}", params={"key": key})
    ).status_code == 404
    # Een sleutel is geen login, en een login geen sleutel
    assert (await client.get("/api/v1/files/root", headers=bearer(key))).status_code == 401
    fake = _encode(settings, {"sub": fotos["id"], "typ": "access", "pw": ""}, 10)
    assert (await client.get("/api/v1/public/children", headers=bearer(fake))).status_code == 401
    download = _encode(settings, {"sub": fotos["id"], "typ": "download"}, 10)
    assert (
        await client.get("/api/v1/public/children", headers=bearer(download))
    ).status_code == 401
    # Een gewone gebruiker kan met zijn token niets via de publieke weg
    jonas_token = jonas["Authorization"].split()[1]
    r = await client.get("/api/v1/public/children", headers=bearer(jonas_token))
    assert r.status_code == 401


def meta(**values: str) -> str:
    return ",".join(f"{k} {base64.b64encode(v.encode()).decode()}" for k, v in values.items())


async def tus_create(client, key, name, size, **extra):
    return await client.post(
        "/api/v1/public/tus",
        headers={
            **bearer(key),
            **TUS,
            "Upload-Length": str(size),
            "Upload-Metadata": meta(filename=name, **extra),
        },
    )


async def tus_send(client, key, location, offset, data):
    return await client.patch(
        location, content=data, headers={**bearer(key), **PATCH, "Upload-Offset": str(offset)}
    )


@pytest.mark.anyio
async def test_uploadlink(client, tree, settings):
    t = tree
    jonas = t["jonas"]
    link = await new_link(client, jonas, t["vakantie"], permission="upload")
    opened = await open_link(client, link)
    assert opened["share"]["permission"] == "upload" and opened["share"]["name"] == "Vakantie"
    key = opened["key"]
    # Niets te zien
    upload_only = {"message": "Met deze link kun je alleen uploaden", "code": "upload_only"}
    for r in [
        await client.get("/api/v1/public/children", headers=bearer(key)),
        await client.get(f"/api/v1/public/download/{t['zee']}", params={"key": key}),
        await client.post(
            "/api/v1/public/previews", json={"node_ids": [t["zee"]]}, headers=bearer(key)
        ),
    ]:
        assert r.status_code == 403 and r.json()["detail"] == upload_only

    assert (await client.options("/api/v1/public/tus")).status_code == 204
    before = (await client.get("/api/v1/auth/me", headers=jonas)).json()["used_bytes"]
    # Bestaat de naam al, dan een vrije naam; parent en conflict tellen niet
    r = await tus_create(client, key, "zee.jpg", 6, parent=t["fotos"], conflict="replace")
    assert r.status_code == 201, r.text
    location = r.headers["location"]
    assert location.startswith("/api/v1/public/tus/")
    assert "platform-node-id" not in r.headers
    r = await client.head(location, headers={**bearer(key), **TUS})
    assert r.headers["upload-offset"] == "0" and "platform-node-id" not in r.headers
    r = await tus_send(client, key, location, 0, b"abc")
    assert r.status_code == 204
    r = await tus_send(client, key, location, 3, b"def")
    assert r.status_code == 204, r.text
    assert "platform-node-id" not in r.headers
    r = await client.head(location, headers={**bearer(key), **TUS})
    assert r.headers["upload-offset"] == "6" and "platform-node-id" not in r.headers
    # Het staat bij Jonas, in de gedeelde map, en kost zijn quotum
    listing = (await client.get(f"/api/v1/files/{t['vakantie']}/children", headers=jonas)).json()
    assert [c["name"] for c in listing["children"]] == ["zee (1).jpg", "zee.jpg"]
    new = listing["children"][0]
    assert new["owner"]["id"] == t["jonas_id"]
    assert (
        await client.get(f"/api/v1/files/{new['id']}/content", headers=jonas)
    ).content == b"abcdef"
    assert (await client.get("/api/v1/auth/me", headers=jonas)).json()["used_bytes"] == before + 6
    async with async_sessionmaker(get_engine(settings))() as session:
        row = (await session.scalars(select(Upload))).one()
        assert (row.owner_id, row.created_by, row.share_id, row.parent_id) == (
            uuid.UUID(t["jonas_id"]),
            None,
            uuid.UUID(link["id"]),
            uuid.UUID(t["vakantie"]),
        )
    # Een leeg bestand meteen
    r = await tus_create(client, key, "leeg.txt", 0)
    assert r.status_code == 201 and "platform-node-id" not in r.headers
    # Ongeldige naam, ook een die er anders uitziet dan hij is (factuur-exe.pdf)
    assert (await tus_create(client, key, "../x", 1)).status_code == 422
    assert (await tus_create(client, key, "factuur-\u202efdp.exe", 1)).status_code == 422
    await check_refs(settings)

    # Een leeslink kan niet uploaden
    read = await new_link(client, jonas, t["vakantie"], permission="read")
    read_key = (await open_link(client, read))["key"]
    r = await tus_create(client, read_key, "x.txt", 1)
    assert r.status_code == 403
    assert r.json()["detail"] == {
        "message": "Met deze link kun je alleen kijken",
        "code": "read_only",
    }
    # Een upload van een link is alleen met een sleutel van die link te bereiken
    location = (await tus_create(client, key, "half.bin", 4)).headers["location"]
    other = await new_link(client, jonas, t["vakantie"], permission="upload")
    other_key = (await open_link(client, other))["key"]
    assert (await client.head(location, headers={**bearer(other_key), **TUS})).status_code == 404
    assert (await tus_send(client, other_key, location, 0, b"1234")).status_code == 404
    assert (await client.delete(location, headers={**bearer(other_key), **TUS})).status_code == 404
    # ...en niet via de gewone tus-weg, ook niet door de eigenaar
    upload_id = location.rsplit("/", 1)[1]
    r = await client.head(f"/api/v1/tus/{upload_id}", headers={**jonas, **TUS})
    assert r.status_code == 404
    assert (await client.delete(location, headers={**bearer(key), **TUS})).status_code == 204
    assert (await client.head(location, headers={**bearer(key), **TUS})).status_code == 404


@pytest.mark.anyio
async def test_uploadlink_weg_en_quotum(client, admin, settings):
    jonas, _ = admin
    klein, _ = await person(client, jonas, "klein@example.nl", quota_bytes=10)
    kroot = (await root(client, klein))["folder"]["id"]
    folder = await mkdir(client, klein, kroot, "Inbox")
    link = await new_link(client, klein, folder, permission="upload")
    key = (await open_link(client, link))["key"]
    r = await tus_create(client, key, "groot.bin", 11)
    assert r.status_code == 413
    assert r.json()["detail"].startswith("Er is niet genoeg ruimte meer bij Klein;")
    location = (await tus_create(client, key, "a.bin", 8)).headers["location"]
    # Gereserveerd bij Klein
    r = await client.put(
        f"/api/v1/files/{kroot}/upload", params={"name": "x"}, content=b"123", headers=klein
    )
    assert r.status_code == 413
    assert (await tus_send(client, key, location, 0, b"1234")).status_code == 204
    # Link weg: de upload gaat mee, de ruimte komt vrij, de sleutel werkt niet meer
    assert (await client.delete(f"/api/v1/shares/{link['id']}", headers=klein)).status_code == 204
    r = await client.put(
        f"/api/v1/files/{kroot}/upload", params={"name": "x"}, content=b"123", headers=klein
    )
    assert r.status_code == 201
    r = await tus_send(client, key, location, 4, b"5678")
    assert r.status_code == 404 and detail_code(r) == "link_not_found"
    async with async_sessionmaker(get_engine(settings))() as session:
        assert (await session.scalars(select(Upload).where(Upload.node_id.is_(None)))).all() == []
    # Een uploadlink die een leeslink wordt: ook dan komt de ruimte vrij
    link = await new_link(client, klein, folder, permission="upload")
    key = (await open_link(client, link))["key"]
    location = (await tus_create(client, key, "b.bin", 7)).headers["location"]
    r = await client.put(
        f"/api/v1/files/{kroot}/upload", params={"name": "y"}, content=b"1", headers=klein
    )
    assert r.status_code == 413
    r = await client.patch(
        f"/api/v1/shares/{link['id']}", json={"permission": "read"}, headers=klein
    )
    assert r.status_code == 200
    r = await client.put(
        f"/api/v1/files/{kroot}/upload", params={"name": "y"}, content=b"1", headers=klein
    )
    assert r.status_code == 201
    r = await tus_send(client, key, location, 0, b"1234567")
    assert r.status_code == 403 and detail_code(r) == "read_only"
    await client.patch(f"/api/v1/shares/{link['id']}", json={"permission": "upload"}, headers=klein)
    assert (await tus_send(client, key, location, 0, b"1234567")).status_code == 404
    await check_refs(settings)


@pytest.mark.parametrize("how", ["prullenbak", "weg", "verlopen"])
@pytest.mark.anyio
async def test_uploadlink_map_weg_tijdens_afronden(client, tree, settings, monkeypatch, how):
    """Nooit uitwijken naar de hoofdmap van de eigenaar: de upload gaat weg."""
    t = tree
    jonas = t["jonas"]
    link = await new_link(client, jonas, t["vakantie"], permission="upload")
    key = (await open_link(client, link))["key"]
    location = (await tus_create(client, key, "x.bin", 4)).headers["location"]
    entered, release = hold(monkeypatch, uploads, "room_left")  # midden in finish()
    with anyio.fail_after(5):
        finishing = asyncio.create_task(tus_send(client, key, location, 0, b"abcd"))
        await entered.wait()
        if how == "prullenbak":
            await client.delete(f"/api/v1/files/{t['vakantie']}", headers=jonas)
        elif how == "weg":
            await client.delete(f"/api/v1/files/{t['vakantie']}", headers=jonas)
            await client.delete(f"/api/v1/trash/{t['vakantie']}", headers=jonas)
        else:
            await set_expiry(settings, link["id"], datetime.now(UTC) - timedelta(seconds=1))
        release.set()
        r = await finishing
    assert r.status_code == 404
    assert not uploads.upload_path(settings, uuid.UUID(location.rsplit("/", 1)[1])).exists()
    names = [c["name"] for c in (await root(client, jonas))["children"]]
    assert names == ["Werk"]
    await check_refs(settings)


@pytest.mark.parametrize("how", ["weg", "verlopen", "weg na de controle", "map weg"])
@pytest.mark.anyio
async def test_uploadlink_weg_terwijl_aanmaken_wacht(client, tree, settings, monkeypatch, how):
    """Aanmaken wacht op het slot van de eigenaar (bijv. achter een grote kopie); is de link
    of de map intussen weg, dan 404 link_not_found en niets gereserveerd."""
    t = tree
    jonas = t["jonas"]
    link = await new_link(client, jonas, t["vakantie"], permission="upload")
    key = (await open_link(client, link))["key"]
    if how == "weg na de controle":
        entered, release = hold(monkeypatch, uploads, "room_left")  # na de controle
    else:
        lock, release = files.user_lock(uuid.UUID(t["jonas_id"])), asyncio.Event()
        await lock.acquire()
        entered = asyncio.Event()
        real = uploads.user_lock

        def user_lock(user_id):
            entered.set()
            return real(user_id)

        monkeypatch.setattr(uploads, "user_lock", user_lock)
    with anyio.fail_after(5):
        creating = asyncio.create_task(tus_create(client, key, "x.bin", 4))
        await entered.wait()
        if how == "verlopen":
            await set_expiry(settings, link["id"], datetime.now(UTC) - timedelta(seconds=1))
        elif how == "map weg":
            await client.delete(f"/api/v1/files/{t['vakantie']}", headers=jonas)
            await client.delete(f"/api/v1/trash/{t['vakantie']}", headers=jonas)
        else:
            r = await client.delete(f"/api/v1/shares/{link['id']}", headers=jonas)
            assert r.status_code == 204
        if how == "weg na de controle":
            release.set()
        else:
            lock.release()
        r = await creating
    assert r.status_code == 404 and detail_code(r) == "link_not_found"
    async with async_sessionmaker(get_engine(settings))() as session:
        assert (await session.scalars(select(Upload))).all() == []


def test_geheimen_niet_in_de_toegangslog():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        (
            "1.2.3.4",
            "GET",
            "/api/v1/public/download/abc?key=eyJhbGciOi.geheim&inline=true",
            "1.1",
            200,
        ),
        None,
    )
    HideSecrets().filter(record)
    assert "geheim" not in record.getMessage() and "key=***" in record.getMessage()
    for path in ("/s/AbC-dEf_123", "/s/AbC-dEf_123?x=1", "/s/AbC-dEf_123/"):
        record = logging.LogRecord(
            "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
            ("1.2.3.4", "GET", path, "1.1", 200), None,
        )  # fmt: skip
        HideSecrets().filter(record)
        assert "AbC-dEf_123" not in record.getMessage() and "/s/***" in record.getMessage()
    record = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4", "GET", "/assets/s/index.js", "1.1", 200), None,
    )  # fmt: skip
    HideSecrets().filter(record)
    assert "/assets/s/index.js" in record.getMessage()
