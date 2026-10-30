"""app.cli media-status [--wait N] [--sessions]. Stub van M0; M10 bouwt het.

collect geeft een dict met minstens enabled, worker, ok (alles goed, of media staat uit) en
lines (wat de cli print, in het Nederlands). wait: hooguit zoveel seconden wachten tot de worker
er is (na de start, Keuze 47). sessions: ook wie er nu kijkt (een waarschuwing vóór een update).

De stub van M0 zegt of media aan staat en vraagt de worker om caps, over dezelfde socket als de
app (Settings.media_socket). Zo ziet het harnas (e2e en de job media-image) dat de worker er is
en wat hij kan. M10 maakt er de echte status van (afgeschermd, GPU, sessies, meldingen).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.config import Settings

RETRY = 0.5  # seconden tussen twee pogingen zolang wait loopt
# Alleen een geslaagde zelftest (M1) is afgeschermd; zonder is het geen bewijs (review S6)
SANDBOX_TEXT = {
    "on": "afgeschermd",
    "off": "lokaal, zonder afscherming",
    "unchecked": "afscherming niet gecontroleerd",
}


async def _caps(settings: Settings, wait: float) -> tuple[Any, str]:
    """(WorkerCaps of None, de reden als het niet lukte); probeert opnieuw tot wait om is."""
    from app.media import client

    client.bind(settings.media_socket, settings.media_key())
    problem = settings.media_problem()
    if problem is not None:
        return None, problem
    end = time.monotonic() + max(0.0, wait)
    while True:
        try:
            return await client.caps(), ""
        except (client.WorkerDown, client.WorkerBusy, client.WorkerFailed, TimeoutError) as exc:
            reason = str(exc)[:200] or exc.__class__.__name__
        if time.monotonic() + RETRY > end:
            return None, reason
        await asyncio.sleep(RETRY)


async def collect(settings: Settings, *, sessions: bool = False, wait: float = 0) -> dict[str, Any]:
    """De staat van media voor de cli."""
    if not settings.media_enabled:
        return {"enabled": False, "worker": "off", "ok": True, "lines": ["Media: uit"]}
    caps, reason = await _caps(settings, wait)
    if caps is None:
        return {
            "enabled": True,
            "worker": "down",
            "ok": False,
            "caps": None,
            "lines": [f"Media: aan, maar de worker is niet bereikbaar ({reason})"],
        }
    missing = list(caps.missing)
    where = SANDBOX_TEXT.get(caps.sandbox, SANDBOX_TEXT["unchecked"])
    lines = [f"Media: aan ({where}, ffmpeg {caps.ffmpeg or 'ontbreekt'})"]
    if missing:
        lines.append("Let op: de worker mist " + ", ".join(missing))
    return {
        "enabled": True,
        "worker": "ffmpeg_missing" if caps.ffmpeg is None or missing else "ok",
        "ok": caps.ffmpeg is not None and not missing,
        "caps": caps.model_dump(mode="json"),
        "lines": lines,
    }
