"""De scanner (Keuze 5): gebeurtenissen via hooks.register_touched, plus een ronde bij de start en
elke 6 uur (jobs media.scan en media.reconcile in de rij media_scan). Stub van M0; M2 bouwt hem.

De haak voor de lus (review K1): runtime.start roept start(app, settings) aan, alleen met media
aan, en runtime.stop roept stop(handle) aan met wat start teruggaf. M2 start daar zijn eigen lus
van Settings.media_scan_tick (30 s, tests 0,5 s; niet de housekeeping, die tikt per 60 s) en zet
media.reconcile klaar bij de start, zonder runtime.py of main.py te wijzigen. De stub start
niets."""

from __future__ import annotations

import asyncio
import uuid

from app.config import Settings


def touched(node_ids: list[uuid.UUID], type: str) -> None:
    """De luisteraar voor hooks.touched; de stub meldt zich niet aan."""
    return None


def start(app, settings: Settings) -> asyncio.Task | None:
    """De lus van de scanner starten (M2); geeft iets terug voor stop, of None."""
    return None


async def stop(handle: asyncio.Task | None) -> None:
    """De lus stoppen; None is niets."""
    if handle is None:
        return
    handle.cancel()
    try:
        await handle
    except asyncio.CancelledError:
        pass
