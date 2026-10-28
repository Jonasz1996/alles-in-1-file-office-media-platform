"""app.cli media-status [--wait N] [--sessions]. Stub van M0; M10 bouwt het.

collect geeft een dict met minstens enabled, worker, ok (alles goed, of media staat uit) en
lines (wat de cli print, in het Nederlands). wait: hooguit zoveel seconden wachten tot de worker
er is (na de start, Keuze 47). sessions: ook wie er nu kijkt (een waarschuwing vóór een update).
"""

from __future__ import annotations

from typing import Any

from app.config import Settings


async def collect(settings: Settings, *, sessions: bool = False, wait: float = 0) -> dict[str, Any]:
    """De staat van media voor de cli. De stub zegt alleen of media aan staat."""
    if not settings.media_enabled:
        return {"enabled": False, "worker": "off", "ok": True, "lines": ["Media: uit"]}
    return {
        "enabled": True,
        "worker": "unknown",
        "ok": True,
        "lines": ["Media: aan (de status volgt in M10)"],
    }
