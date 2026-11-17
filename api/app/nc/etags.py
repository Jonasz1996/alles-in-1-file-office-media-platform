"""De ETag van een map voor WebDAV: verandert zodra er ergens onder die map iets verandert.

De desktopclient van Nextcloud vraagt elke 30 s alleen de hoofdmap op (Depth 0). Is de ETag
gelijk, dan is er niets veranderd; anders loopt hij de mappen af waarvan de ETag anders is.
Een bestand heeft de hash van zijn inhoud als ETag (Node.etag); een map krijgt hier een
versienummer.

Hoe: elke wijziging in de boom gaat al via events.publish (files.notify en de rest). Die
gebeurtenissen dragen parent_id en old_parent_id; die mappen komen in `_dirty`. Vlak voor een
PROPFIND gaan alle mappen in `_dirty` en al hun voorouders een versie omhoog (één recursieve
query). In het geheugen, met een `BOOT` per start: na een herstart (of een import buiten de
app om, met een herstart erna) zijn alle map-ETags één keer nieuw en vergelijkt de client
alles opnieuw. Bestanden met dezelfde inhoud downloadt hij dan niet opnieuw.
"""

import secrets
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import events
from app.models import Node

BOOT = secrets.token_hex(4)
_dirty: set[uuid.UUID] = set()
_version: dict[uuid.UUID, int] = {}
_seq = 0


def _tap(type: str, data: dict[str, Any]) -> None:
    if not type.startswith("node."):
        return
    for key in ("parent_id", "old_parent_id", "id"):
        value = data.get(key)
        if value:
            try:
                _dirty.add(uuid.UUID(str(value)))
            except ValueError:
                pass
    for value in data.get("ids") or ():
        try:
            _dirty.add(uuid.UUID(str(value)))
        except ValueError:
            pass


events.taps.append(_tap)


def touch(*node_ids: uuid.UUID | None) -> None:
    """Voor wijzigingen die geen gebeurtenis publiceren."""
    _dirty.update(i for i in node_ids if i is not None)


async def flush(session: AsyncSession) -> None:
    """Alle vuile mappen en hun voorouders een nieuwe versie."""
    global _seq
    if not _dirty:
        return
    start = list(_dirty)
    _dirty.clear()
    _seq += 1
    found: set[uuid.UUID] = set()
    for i in range(0, len(start), 500):
        chunk = start[i : i + 500]
        base = select(Node.id, Node.parent_id).where(Node.id.in_(chunk)).cte("up", recursive=True)
        up = base.union(select(Node.id, Node.parent_id).where(Node.id == base.c.parent_id))
        found.update(r[0] for r in await session.execute(select(up.c.id)))
    for node_id in found:
        _version[node_id] = _seq


def dir_etag(node_id: uuid.UUID) -> str:
    return f'"{BOOT}{node_id.hex[:12]}{_version.get(node_id, 0):x}"'
