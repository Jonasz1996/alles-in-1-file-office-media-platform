"""python -m app.cli office-checkpoint (Keuze 18, vóór een update): forcesave voor elke open
sessie, dan hooguit --wait seconden wachten tot de opslag binnen is. Hier in het proces van de
test, met de nep-DS (zonder office: tests/test_cli_db.py, als echt proces)."""

import time

import pytest

from app import cli
from app.main import app
from app.office import runtime
from tests.office_world import office_world

pytestmark = pytest.mark.anyio


@pytest.fixture
async def world(client, settings):
    async with office_world(client, settings) as w:
        yield w


async def checkpoint(world, wait: float) -> int:
    """Zoals app.cli het doet (runtime zonder app); daarna de app van de test weer terug."""
    try:
        return await cli.office_checkpoint(world.settings, wait)
    finally:
        runtime.bind(app, world.settings)


async def test_office_checkpoint_slaat_open_sessies_op_en_wacht(world):
    anna = await world.user("anna@example.nl")
    bram = await world.user("bram@example.nl")
    a_node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    b_node = await world.docx(bram, None, "notities.docx", ["Begin"])
    still = await world.docx(anna, None, "ongemoeid.docx", ["Begin"])
    a = await world.open(anna, a_node)
    b = await world.open(bram, b_node)
    s = await world.open(anna, still)
    await world.type(a, "van Anna")
    await world.type(b, "van Bram")
    spy = world.spy()
    assert await checkpoint(world, 10) == 2  # de derde had niets te bewaren (error 4)
    forcesaves = [p for c, p in spy if c == "forcesave"]
    assert sorted(p["key"] for p in forcesaves) == sorted([a.key, b.key, s.key])
    assert {p["userdata"] for p in forcesaves} == {"stop"}
    # Al opgeslagen toen het commando terugkwam: het wachtte
    assert "van Anna" in await world.paragraphs(a_node)
    assert "van Bram" in await world.paragraphs(b_node)
    for h in (a, b, s):
        assert (await world.row(h.key)).state == "open"  # de editors blijven open


async def test_office_checkpoint_wacht_hooguit_wait(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    h = await world.open(anna, node)
    await world.type(h, "komt niet aan")
    await world.fail_callbacks(1000)
    start = time.monotonic()
    assert await checkpoint(world, 1) == 0
    assert time.monotonic() - start < 3
    await world.fail_callbacks(0)


async def test_office_checkpoint_zonder_open_sessies(world):
    anna = await world.user("anna@example.nl")
    node = await world.docx(anna, None, "verslag.docx", ["Begin"])
    await world.open_config(anna, node)  # pending: niet open, geen forcesave
    spy = world.spy()
    assert await checkpoint(world, 1) == 0
    assert [c for c, _ in spy if c == "forcesave"] == []
