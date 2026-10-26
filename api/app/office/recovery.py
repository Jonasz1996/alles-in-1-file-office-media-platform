"""Het herstel van forgotten files (Keuze 21). O0-stub; O2 maakt het af.

start(app): een eigen taak, bij de start van de app, elke office_recovery_seconds en na
kick(). run_once(): één ronde, het aantal afgehandelde keys. restore(key) en forget(key):
de knoppen Terugzetten en Verwijderen op /beheer (O5).
"""

from app.models import Node


async def start(app) -> None:
    return None


def kick() -> None:
    return None


async def run_once() -> int:
    return 0


async def restore(key: str) -> Node:
    raise NotImplementedError("O2")


async def forget(key: str) -> None:
    raise NotImplementedError("O2")
