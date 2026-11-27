import asyncio
import uuid

import pytest

from app import events
from app.events import Hub


@pytest.mark.anyio
async def test_alleen_naar_de_juiste_gebruiker():
    hub = Hub()
    jonas, ander = uuid.uuid4(), uuid.uuid4()
    with hub.subscribe(jonas) as a, hub.subscribe(ander) as b:
        hub.publish([jonas], "node.created", {"id": "x"})
        assert await asyncio.wait_for(anext(aiter(a)), 1) == {
            "type": "node.created",
            "data": {"id": "x"},
        }
        assert b.queue.empty()
    assert hub.listeners(jonas) == 0


@pytest.mark.anyio
async def test_trage_verbinding_haakt_af(monkeypatch):
    monkeypatch.setattr(events, "QUEUE_SIZE", 3)
    hub = Hub()
    user = uuid.uuid4()
    with hub.subscribe(user) as sub:
        for i in range(5):
            hub.publish([user], "x", {"i": i})
        assert hub.listeners(user) == 0
        seen = [e async for e in sub]  # eindigt: de verbinding is afgehaakt
        assert len(seen) < 5


@pytest.mark.anyio
async def test_sluiten_rondt_open_verbindingen_af():
    hub = Hub()
    user = uuid.uuid4()
    with hub.subscribe(user) as sub:
        task = asyncio.create_task(anext(aiter(sub), "klaar"))
        await asyncio.sleep(0)
        hub.close()
        assert await asyncio.wait_for(task, 1) == "klaar"
    with hub.subscribe(user) as late:  # na het sluiten meteen klaar
        assert [e async for e in late] == []
