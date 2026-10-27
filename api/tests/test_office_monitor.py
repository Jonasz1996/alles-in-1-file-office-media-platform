"""De bewaking van office (app/office/monitor.py): de meldingen voor /beheer en wat het tabblad
Office telt. Met de nep-DS: de licentie, de versie en de tokencontrole komen via health.py
(command license, version en de controle zonder token), de forgotten files via
getForgottenList, de mislukte conversies uit blob.pdf_state."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import update

from app.main import app
from app.models import Blob, Node
from app.office import health, monitor
from app.office.state import OfficeState
from tests.factories import maker
from tests.fake_docserver import docx
from tests.office_world import office_world

pytestmark = pytest.mark.anyio


@pytest.fixture(autouse=True)
def fresh_cache():
    monitor.forget_cache()
    yield
    monitor.forget_cache()


@pytest.fixture
async def world(client, settings):
    async with office_world(client, settings) as w:
        yield w


async def codes(world) -> dict[str, dict]:
    async with maker(world.base_settings)() as session:
        found = await monitor.notices(world.state, session)
    return {n.code: n.params for n in found}


async def deep_check(world) -> None:
    """Een grote controle van health.py, zoals bij "weer gezond": version, license en de
    controle zonder token, bij de nep-DS."""
    world.state.deep_checked_at = None
    await health.check_once(app)


async def fake(world, path: str, body: dict) -> None:
    r = await world.http.post(f"/__fake/{path}", json=body)
    assert r.status_code == 200, r.text


def editors(world, count: int, key: str = "k1") -> None:
    """count verbindingen van count verschillende gebruikers, zoals de tunnel (O1) ze telt."""
    world.state.tunnels[key] = {uuid.uuid4(): 1 for _ in range(count)}


async def set_pdf(world, node: uuid.UUID, state: str | None) -> None:
    async with maker(world.base_settings)() as session:
        sha = (await session.get(Node, node)).blob_id
        await session.execute(update(Blob).where(Blob.sha256 == sha).values(pdf_state=state))
        await session.commit()


# --- de licentie ---


async def test_licentie_2147483647_is_geen_limiet(world):
    await deep_check(world)
    assert world.state.license_connections == monitor.UNLIMITED
    assert monitor.unlimited(world.state.license_connections)
    editors(world, 500)
    assert "office_limit" not in await codes(world)
    assert monitor.unlimited(None) and monitor.unlimited(2_147_483_648)
    assert not monitor.unlimited(20)


async def test_licentie_20_meldt_vanaf_16(world):
    await fake(world, "license", {"connections": 20})
    await deep_check(world)
    assert world.state.license_connections == 20
    editors(world, 15)
    assert "office_limit" not in await codes(world)
    editors(world, 16)
    assert (await codes(world))["office_limit"] == {"used": 16, "max": 20}
    editors(world, 25)
    assert (await codes(world))["office_limit"] == {"used": 25, "max": 20}


# --- de versie ---


async def test_de_vastgepinde_versie_meldt_niets(world):
    await deep_check(world)
    assert world.state.version == monitor.PINNED_VERSION
    assert "office_version" not in await codes(world)


@pytest.mark.parametrize("version", ["9.4.0.12", "10.0.0.170", "10.1.0.1", "onzin"])
async def test_een_andere_versie_geeft_office_version(world, version):
    world.state.version = version
    found = await codes(world)
    assert found["office_version"] == {"version": version, "pinned": monitor.PINNED_VERSION}


async def test_versie_onbekend_telt_als_goed():
    assert monitor.version_ok(None)
    assert monitor.version_ok(monitor.PINNED_VERSION)
    assert not monitor.version_ok("9.4.0.1")


# --- de tokencontrole en de rest van de gezondheid ---


async def test_ds_eist_geen_tokens_geeft_office_misconfigured(world):
    await fake(world, "jwt-off", {"off": True})
    await deep_check(world)
    assert world.state.tokens_enforced is False and world.state.health == "misconfigured"
    assert "office_misconfigured" in await codes(world)
    await fake(world, "jwt-off", {"off": False})
    await deep_check(world)
    assert world.state.tokens_enforced is True and world.state.health == "ok"
    assert "office_misconfigured" not in await codes(world)


async def test_office_down_pas_na_twee_minuten(world):
    now = datetime.now(UTC)
    world.state.health, world.state.down_since = "down", now - timedelta(seconds=60)
    assert "office_down" not in await codes(world)
    world.state.down_since = now - timedelta(seconds=health.DOWN_NOTICE_SECONDS + 1)
    assert "office_down" in await codes(world)
    world.state.health = "starting"  # starten telt niet als weg (fonts)
    assert "office_down" not in await codes(world)


# --- forgotten files en conversies ---


async def test_forgotten_files_geven_een_melding(world):
    assert "office_unsaved" not in await codes(world)
    world.forgotten()["k-een"] = ("output.docx", docx.make_docx(["een"]))
    world.forgotten()["k-twee"] = ("output.docx", docx.make_docx(["twee"]))
    monitor.forget_cache()
    assert (await codes(world))["office_unsaved"] == {"count": 2}
    # De lijst van DS wordt even onthouden: /beheer vraagt hem bij elke ververs
    world.forgotten().pop("k-een")
    assert (await codes(world))["office_unsaved"] == {"count": 2}
    monitor.forget_cache()
    assert (await codes(world))["office_unsaved"] == {"count": 1}


async def test_ds_niet_gezond_geeft_de_laatste_lijst(world):
    world.forgotten()["k-een"] = ("output.docx", docx.make_docx(["een"]))
    assert (await codes(world))["office_unsaved"] == {"count": 1}
    world.state.health = "down"
    monitor.forget_cache()
    assert "office_unsaved" not in await codes(world)  # nooit gelezen: niets te melden
    world.state.health = "ok"
    assert (await codes(world))["office_unsaved"] == {"count": 1}
    world.state.health = "down"
    assert await monitor.forgotten(world.state, fresh=True) == ["k-een"]


async def test_mislukte_conversies_geven_een_melding(world):
    anna = await world.user("anna@example.nl")
    one = await world.docx(anna, None, "een.docx", ["een"])
    two = await world.docx(anna, None, "twee.docx", ["twee"])
    await set_pdf(world, one, None)
    await set_pdf(world, two, None)
    assert "office_convert_failed" not in await codes(world)
    await set_pdf(world, one, "failed")
    assert (await codes(world))["office_convert_failed"] == {"count": 1}
    await set_pdf(world, two, "failed")
    assert (await codes(world))["office_convert_failed"] == {"count": 2}
    async with maker(world.base_settings)() as session:
        counts = await monitor.conversions(session)
    assert counts == {"queued": 0, "failed": 2, "unsafe": 0, "ask": 0}


async def test_alles_goed_geen_meldingen(world):
    await deep_check(world)
    assert await codes(world) == {}


async def test_zonder_office_geen_meldingen(client, settings):
    """Zonder PLATFORM_OFFICE_URL meldt de bewaking niets, ook als OfficeState iets zegt."""
    state = OfficeState(
        health="down", down_since=datetime.now(UTC) - timedelta(hours=1), version="9.0.0"
    )
    async with maker(settings)() as session:
        assert await monitor.notices(state, session) == []


# --- tellen ---


async def test_verbindingen_mensen_documenten_en_de_piek(world):
    async with maker(world.base_settings)() as session:
        assert await monitor.usage(session, world.state) == monitor.Usage(0, 0, 0)
    assert monitor.peak(world.state, 0) is None
    anna, bram = uuid.uuid4(), uuid.uuid4()
    world.state.tunnels = {"k1": {anna: 2, bram: 1}, "k2": {anna: 1}}
    async with maker(world.base_settings)() as session:
        found = await monitor.usage(session, world.state)
    assert found == monitor.Usage(connections=4, people=2, documents=2)
    top = monitor.peak(world.state, found.connections)
    assert top is not None and top[0] == 4
    world.state.tunnels = {"k1": {anna: 1}}
    assert monitor.peak(world.state, 1) == top  # de piek blijft
