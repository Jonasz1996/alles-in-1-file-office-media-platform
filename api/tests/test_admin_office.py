"""/api/v1/admin/office (app/routers/admin_office.py) met de nep-DS: alle velden van de GET,
alleen voor beheerders, de grens en de andere waarden van OfficeConfig (opgeslagen en meteen
gebruikt), office uit en weer aan, terugzetten en verwijderen van forgotten files, eigenaar weg,
mislukte conversies opnieuw en "Nu tussentijds opslaan"."""

import uuid

import pytest
from sqlalchemy import delete, select, update

from app.config import Settings
from app.main import app
from app.models import Blob, Job, Node, Setting
from app.office import convert, engine, health, monitor, runtime
from app.office.state import OFFICE_KEY, OfficeConfig, OfficeState, office_policy
from tests.factories import code_of, maker
from tests.fake_docserver import docx
from tests.office_world import office_world, wait_until

pytestmark = pytest.mark.anyio

ADMIN = "/api/v1/admin/office"


@pytest.fixture(autouse=True)
def fresh_cache():
    monitor.forget_cache()
    yield
    monitor.forget_cache()


@pytest.fixture
async def world(client, settings):
    async with office_world(client, settings) as w:
        yield w


@pytest.fixture
def boss(admin):
    headers, _ = admin
    return headers


async def get_admin(world, boss) -> dict:
    r = await world.client.get(ADMIN, headers=boss)
    assert r.status_code == 200, r.text
    return r.json()


async def put_admin(world, boss, **change) -> dict:
    body = {**OfficeConfig().model_dump(), **change}
    r = await world.client.put(ADMIN, json=body, headers=boss)
    assert r.status_code == 200, r.text
    return r.json()


async def duo(world):
    """Anna deelt de map Samen met Bram (schrijven), met daarin verslag.docx."""
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    folder = await world.folder(anna, "Samen")
    await world.share(anna, folder, bram, write=True)
    node = await world.docx(anna, folder, "verslag.docx", ["Begin"])
    return anna, bram, node


async def lost(world, user, node, paragraphs) -> str:
    """Een sessie waarvan de laatste opslag de app niet bereikte: DS bewaarde de inhoud als
    forgotten file."""
    key = await world.begin(user, node)
    await world.set_session(key, state="closed", closed_reason="lost", closed_at=world.now())
    world.forgotten()[key] = ("output.docx", docx.make_docx(paragraphs))
    return key


async def set_pdf(world, node, state: str | None) -> str:
    async with maker(world.base_settings)() as session:
        sha = (await session.get(Node, node)).blob_id
        await session.execute(update(Blob).where(Blob.sha256 == sha).values(pdf_state=state))
        await session.commit()
    return sha


async def pdf_state(world, sha: str) -> str | None:
    async with maker(world.base_settings)() as session:
        return (await session.get(Blob, sha, populate_existing=True)).pdf_state


async def backfills(world) -> int:
    async with maker(world.base_settings)() as session:
        rows = await session.scalars(
            select(Job).where(Job.type == convert.BACKFILL, Job.status == "queued")
        )
        return len(rows.all())


async def no_backfill(world) -> None:
    async with maker(world.base_settings)() as session:
        await session.execute(delete(Job).where(Job.type == convert.BACKFILL))
        await session.commit()


# --- alleen voor beheerders ---


ENDPOINTS = [
    ("GET", ADMIN, None),
    ("PUT", ADMIN, OfficeConfig().model_dump()),
    ("POST", f"{ADMIN}/checkpoint", None),
    ("POST", f"{ADMIN}/conversions/retry", None),
    ("POST", f"{ADMIN}/unsaved/k1/restore", None),
    ("DELETE", f"{ADMIN}/unsaved/k1", None),
]


async def test_alleen_voor_beheerders(world, boss):
    anna = await world.user("anna@example.nl")
    for method, path, body in ENDPOINTS:
        r = await world.client.request(method, path, json=body, headers=anna.headers)
        assert r.status_code == 403, (method, path, r.text)
        r = await world.client.request(method, path, json=body)
        assert r.status_code == 401, (method, path)
    assert office_policy(app).config == OfficeConfig()


# --- de GET ---


async def test_alle_velden_met_de_nep_ds(world, boss):
    world.state.deep_checked_at = None
    await health.check_once(app)  # version, license en de tokencontrole, zoals bij de start
    anna, bram, node = await duo(world)
    first = await world.open(anna, node)
    second = await world.open(bram, node)

    async def both():
        row = await world.row(first.key)
        return row is not None and len(row.connected) == 2

    await wait_until(both)
    out = await get_admin(world, boss)
    assert out["configured"] is True and out["enabled"] is True
    assert out["state"] == "ok" and out["down_since"] is None
    assert out["version"] == monitor.PINNED_VERSION
    assert out["license_connections"] == monitor.UNLIMITED
    assert out["tokens_enforced"] is True
    assert (out["max_editors"], out["checkpoint_minutes"], out["version_minutes"]) == (20, 5, 30)
    assert (out["connections"], out["people"], out["documents"]) == (2, 2, 1)
    assert out["peak"]["connections"] >= 2 and out["peak"]["at"]
    [doc] = out["open"]
    assert doc["name"] == "verslag.docx" and doc["owner"] == "Anna"
    assert doc["editors"] == ["Anna", "Bram"] and doc["state"] == "open" and doc["since"]
    assert out["unsaved"] == []
    assert set(out["conversions"]) == {"queued", "failed", "unsafe", "ask"}

    await world.close(first)
    await world.close(second)

    async def closed():
        row = await world.row(first.key)
        return row is not None and row.state == "closed"

    await wait_until(closed, 15)
    out = await get_admin(world, boss)
    assert (out["connections"], out["people"], out["documents"]) == (0, 0, 0)
    assert out["open"] == []
    assert out["peak"]["connections"] >= 2  # de piek blijft


async def test_een_sessie_die_blijft_hangen_staat_erbij(world, boss):
    """Een sessie in pending (een config uitgegeven, nog niemand verbonden) houdt de
    conversies tegen (sessions.busy); het tabblad toont hem."""
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "hangt.docx", ["Begin"])
    r = await world.open_config(anna, node)
    assert r.status_code == 200, r.text
    [doc] = (await get_admin(world, boss))["open"]
    assert doc["name"] == "hangt.docx" and doc["state"] == "pending" and doc["editors"] == []
    await world.set_session(r.json()["key"], state="closed", closed_reason="lost")


async def test_de_naam_van_nu(world, boss):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "oud.docx", ["Begin"])
    key = await world.begin(anna, node)
    await world.rename(anna, node, "nieuw.docx")
    [doc] = (await get_admin(world, boss))["open"]
    assert doc["name"] == "nieuw.docx" and doc["editors"] == ["Anna"]
    await world.set_session(key, state="closed", closed_reason="lost")


async def test_verkeerd_ingesteld(client, settings, boss):
    """Een adres maar een te kort geheim: configured false, de knoppen die DS nodig hebben
    503 office_misconfigured."""
    office = Settings(
        **{**settings.model_dump(), "office_url": "http://office", "office_secret": "kort"}
    )
    runtime.bind(app, office)
    engine.reset()
    app.state.office = OfficeState(health="misconfigured", problem=office.office_problem())
    try:
        r = await client.get(ADMIN, headers=boss)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["configured"] is False and out["state"] == "misconfigured"
        assert out["open"] == [] and out["unsaved"] == []
        for method, path, body in ENDPOINTS[2:]:
            r = await client.request(method, path, json=body, headers=boss)
            assert r.status_code == 503 and code_of(r) == "office_misconfigured", path
    finally:
        engine.reset()
        runtime.unbind()
        for name in ("office", "office_policy"):
            if hasattr(app.state, name):
                delattr(app.state, name)


# --- de PUT ---


async def test_grens_opslaan_en_meteen_gebruikt(world, boss):
    anna, bram, node = await duo(world)
    out = await put_admin(world, boss, max_editors=1, checkpoint_minutes=7, version_minutes=45)
    assert (out["max_editors"], out["checkpoint_minutes"], out["version_minutes"]) == (1, 7, 45)
    assert office_policy(app).config == OfficeConfig(
        max_editors=1, checkpoint_minutes=7, version_minutes=45
    )
    async with maker(world.base_settings)() as session:
        row = await session.get(Setting, OFFICE_KEY)
        assert row is not None and row.value["max_editors"] == 1
    key = await world.begin(anna, node)
    r = await world.open_config(bram, node)
    assert r.status_code == 429 and code_of(r) == "office_too_many"
    assert r.json()["detail"]["params"] == {"max": 1}
    # Na een herstart van de app (het geheugen leeg) leest hij hem uit de tabel setting
    office_policy(app).config = None
    assert (await get_admin(world, boss))["max_editors"] == 1
    out = await put_admin(world, boss, max_editors=2)
    r = await world.open_config(bram, node)
    assert r.status_code == 200 and r.json()["key"] == key
    await world.set_session(key, state="closed", closed_reason="lost")


@pytest.mark.parametrize(
    "change",
    [
        {"max_editors": 0},
        {"max_editors": 501},
        {"checkpoint_minutes": 0},
        {"checkpoint_minutes": 61},
        {"version_minutes": 4},
        {"version_minutes": 1441},
    ],
)
async def test_grenzen_van_de_waarden(world, boss, change):
    body = {**OfficeConfig().model_dump(), **change}
    r = await world.client.put(ADMIN, json=body, headers=boss)
    assert r.status_code == 422 and code_of(r) == "invalid_input"
    assert office_policy(app).config == OfficeConfig()


async def test_uit_en_weer_aan(world, boss):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    root = await world.root(anna)
    out = await put_admin(world, boss, enabled=False)
    assert out["enabled"] is False
    r = await world.client.get("/api/v1/office/status", headers=anna.headers)
    assert (r.json()["enabled"], r.json()["editing"], r.json()["state"]) == (False, False, "ok")
    r = await world.open_config(anna, node)
    assert r.status_code == 503 and code_of(r) == "office_off"
    r = await world.client.post(
        "/api/v1/office/new", json={"parent_id": str(root), "kind": "docx"}, headers=anna.headers
    )
    assert r.status_code == 503 and code_of(r) == "office_off"
    r = await world.client.post(f"/api/v1/office/files/{node}/convert", headers=anna.headers)
    assert r.status_code == 503 and code_of(r) == "office_off"

    # Weer aan, zonder herstart: bewerken kan meteen, en de inhaalronde van O3 staat klaar
    await no_backfill(world)
    out = await put_admin(world, boss, enabled=True)
    assert out["enabled"] is True
    assert await backfills(world) == 1
    r = await world.client.get("/api/v1/office/status", headers=anna.headers)
    assert (r.json()["enabled"], r.json()["editing"], r.json()["state"]) == (True, True, "ok")
    r = await world.open_config(anna, node)
    assert r.status_code == 200, r.text
    await world.set_session(r.json()["key"], state="closed", closed_reason="lost")
    # Nog een keer aan (was al aan): geen tweede inhaalronde nodig
    await no_backfill(world)
    await put_admin(world, boss, enabled=True)
    assert await backfills(world) == 0


async def test_weer_aan_met_ds_weg_wacht_op_gezond(world, boss):
    await put_admin(world, boss, enabled=False)
    await no_backfill(world)
    world.state.health = "down"
    await put_admin(world, boss, enabled=True)
    assert await backfills(world) == 0  # health.py start hem bij "weer gezond"


# --- terugzetten en verwijderen ---


async def test_terugzetten(world, boss):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = await lost(world, anna, node, ["terug"])
    await world.room(anna, 0)
    await world.recover()  # de kopie past niet: de key blijft, met no_room
    assert key in world.forgotten()
    [item] = (await get_admin(world, boss))["unsaved"]
    assert item["key"] == key and item["name"] == "verslag.docx"
    assert item["owner"] == "Anna" and item["reason"] == "no_room" and item["at"]

    r = await world.client.post(f"{ADMIN}/unsaved/{key}/restore", headers=boss)
    assert r.status_code == 413 and code_of(r) == "no_room"
    assert key in world.forgotten()

    await world.room(anna, None)
    r = await world.client.post(f"{ADMIN}/unsaved/{key}/restore", headers=boss)
    assert r.status_code == 200, r.text
    copy = r.json()
    assert copy["kind"] == "file" and copy["id"] != str(node)
    assert await world.paragraphs(uuid.UUID(copy["id"])) == ["terug"]
    assert await world.paragraphs(node) == ["Begin"]
    assert key not in world.forgotten()
    assert (await get_admin(world, boss))["unsaved"] == []
    assert world.event_data(anna, "office.recovered")[-1]["copy_id"] == copy["id"]
    r = await world.client.post(f"{ADMIN}/unsaved/{key}/restore", headers=boss)
    assert r.status_code == 404 and code_of(r) == "not_found"


async def test_eigenaar_weg_is_409(world, boss):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = await lost(world, anna, node, ["weg"])
    await world.set_session(key, node_id=None, owner_id=None)
    [item] = (await get_admin(world, boss))["unsaved"]
    assert item["key"] == key and item["owner"] is None
    r = await world.client.post(f"{ADMIN}/unsaved/{key}/restore", headers=boss)
    assert r.status_code == 409 and code_of(r) == "office_owner_gone"
    assert key in world.forgotten()
    r = await world.client.delete(f"{ADMIN}/unsaved/{key}", headers=boss)
    assert r.status_code == 204
    assert key not in world.forgotten()


async def test_verwijderen(world, boss):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = await lost(world, anna, node, ["weg ermee"])
    assert [u["key"] for u in (await get_admin(world, boss))["unsaved"]] == [key]
    r = await world.client.delete(f"{ADMIN}/unsaved/{key}", headers=boss)
    assert r.status_code == 204
    assert key not in world.forgotten()
    assert await world.paragraphs(node) == ["Begin"]
    assert (await get_admin(world, boss))["unsaved"] == []
    r = await world.client.delete(f"{ADMIN}/unsaved/{key}", headers=boss)
    assert r.status_code == 404 and code_of(r) == "not_found"


async def test_een_onbekende_key(world, boss):
    world.forgotten()["vreemd-1"] = ("output.docx", docx.make_docx(["?"]))
    [item] = (await get_admin(world, boss))["unsaved"]
    assert item == {"key": "vreemd-1", "name": "", "owner": None, "at": None, "reason": "unknown"}
    r = await world.client.post(f"{ADMIN}/unsaved/vreemd-1/restore", headers=boss)
    assert r.status_code == 404 and code_of(r) == "not_found"
    r = await world.client.delete(f"{ADMIN}/unsaved/vreemd-1", headers=boss)
    assert r.status_code == 204
    assert "vreemd-1" not in world.forgotten()


async def test_ds_weg_bij_terugzetten(world, boss):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    key = await lost(world, anna, node, ["later"])
    world.state.health = "down"
    r = await world.client.post(f"{ADMIN}/unsaved/{key}/restore", headers=boss)
    assert r.status_code == 503 and code_of(r) == "office_unavailable"
    assert key in world.forgotten()


# --- conversies en tussentijds opslaan ---


async def test_mislukte_conversies_opnieuw(world, boss):
    anna = await world.user("anna@example.nl")
    one = await world.docx(anna, None, "een.docx", ["een"])
    two = await world.docx(anna, None, "twee.docx", ["twee"])
    sha_one = await set_pdf(world, one, "failed")
    sha_two = await set_pdf(world, two, "unsafe")
    out = await get_admin(world, boss)
    assert out["conversions"]["failed"] == 1 and out["conversions"]["unsafe"] == 1
    await no_backfill(world)
    r = await world.client.post(f"{ADMIN}/conversions/retry", headers=boss)
    assert r.status_code == 200 and r.json() == {"queued": 1}
    assert await pdf_state(world, sha_one) is None
    assert await pdf_state(world, sha_two) == "unsafe"  # de invoercontrole blijft gelden
    assert await backfills(world) == 1
    assert (await get_admin(world, boss))["conversions"]["failed"] == 0
    r = await world.client.post(f"{ADMIN}/conversions/retry", headers=boss)
    assert r.json() == {"queued": 0}


async def test_nu_tussentijds_opslaan(world, boss):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    h = await world.open(anna, node)

    async def connected():
        row = await world.row(h.key)
        return row is not None and row.state == "open" and row.connected

    await wait_until(connected)
    await world.type(h, "erbij")
    r = await world.client.post(f"{ADMIN}/checkpoint", headers=boss)
    assert r.status_code == 200 and r.json() == {"sent": 1, "saved": 1}
    assert await world.paragraphs(node) == ["Begin", "erbij"]
    await world.close(h)
    world.state.health = "down"
    r = await world.client.post(f"{ADMIN}/checkpoint", headers=boss)
    assert r.status_code == 503 and code_of(r) == "office_unavailable"


async def test_meldingen_op_beheer(world, boss):
    """De haak van O0 in admin.system_info: de meldingen staan op /api/v1/admin/status."""
    world.forgotten()["vreemd-2"] = ("output.docx", docx.make_docx(["?"]))
    r = await world.client.get("/api/v1/admin/status", headers=boss)
    assert r.status_code == 200, r.text
    found = {n["code"]: n["params"] for n in r.json()["notices"]}
    assert found["office_unsaved"] == {"count": 1}
    assert r.json()["status"] == "degraded"
