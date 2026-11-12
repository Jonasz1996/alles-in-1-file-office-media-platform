"""Gebeurtenissen live naar de web-app en apps: een bestand erbij, een preview klaar, een
nieuwe deling. Eén proces, dus gewoon in het geheugen; in het cluster komt hier later een
pub/sub tussen de nodes bij.

Publiceer pas na de commit, zodat wie de gebeurtenis ziet en opnieuw laadt, de wijziging
ook echt vindt.
"""

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterable
from typing import Any

# Wie zoveel gebeurtenissen achterloopt, haakt af en moet opnieuw verbinden (en laadt dan
# alles opnieuw); zo houdt één trage verbinding niemand anders op.
QUEUE_SIZE = 256


class Subscription:
    def __init__(self, hub: "Hub", user_id: uuid.UUID):
        self.hub = hub
        self.user_id = user_id
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(QUEUE_SIZE)

    def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        return self._events()

    async def _events(self) -> AsyncIterator[dict[str, Any]]:
        while (event := await self.queue.get()) is not None:
            yield event

    def end(self) -> None:
        """Laat de lus over deze inschrijving stoppen (ook als de wachtrij vol zit)."""
        while True:
            try:
                self.queue.put_nowait(None)
                return
            except asyncio.QueueFull:
                self.queue.get_nowait()

    def __enter__(self) -> "Subscription":
        return self

    def __exit__(self, *_exc) -> None:
        self.hub._remove(self)


class Hub:
    def __init__(self) -> None:
        self._subs: dict[uuid.UUID, set[Subscription]] = {}
        self.closed = False

    def subscribe(self, user_id: uuid.UUID) -> Subscription:
        """Gebruik als `with hub.subscribe(id) as sub: async for event in sub: ...`."""
        sub = Subscription(self, user_id)
        if self.closed:
            sub.end()
        else:
            self._subs.setdefault(user_id, set()).add(sub)
        return sub

    def _remove(self, sub: Subscription) -> None:
        subs = self._subs.get(sub.user_id)
        if subs is not None:
            subs.discard(sub)
            if not subs:
                del self._subs[sub.user_id]

    def publish(self, user_ids: Iterable[uuid.UUID], type: str, data: dict[str, Any]) -> None:
        """Stuurt {"type": type, "data": data} naar elke open verbinding van deze gebruikers.
        Wacht nooit: een volle wachtrij betekent dat die verbinding afhaakt."""
        event = {"type": type, "data": data}
        for user_id in set(user_ids):
            for sub in list(self._subs.get(user_id, ())):
                try:
                    sub.queue.put_nowait(event)
                except asyncio.QueueFull:
                    self._remove(sub)
                    sub.end()

    def close(self) -> None:
        """Bij het stoppen: alle verbindingen afronden, anders wacht de server erop."""
        self.closed = True
        for subs in list(self._subs.values()):
            for sub in list(subs):
                sub.end()
        self._subs.clear()

    def listeners(self, user_id: uuid.UUID) -> int:
        return len(self._subs.get(user_id, ()))


hub = Hub()


def publish(user_ids: Iterable[uuid.UUID], type: str, data: dict[str, Any]) -> None:
    hub.publish(user_ids, type, data)
