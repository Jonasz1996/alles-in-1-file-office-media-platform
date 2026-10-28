"""De gezondheid van de worker: elke INTERVAL s caps, over een eigen verbinding die niet
meetelt in de plaatsen (Keuze 12). M1 bouwt de meldingen, de backoff en de supervisie; M0 heeft
de lus die MediaState.worker bijhoudt.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app.config import Settings
from app.media import client
from app.media.state import media_state

log = logging.getLogger("platform.media")

INTERVAL = 30.0
FIRST = 0.2  # snel na de start, daarna INTERVAL


async def check(app) -> str:
    """Eén keer caps; zet de staat en geeft hem terug."""
    state = media_state(app)
    try:
        caps = await client.caps()
    except client.WorkerDown as exc:
        if state.set_worker("down", str(exc)[:200]):
            log.warning("mediaworker niet bereikbaar: %s", exc)
        return state.worker
    except (client.WorkerFailed, client.WorkerBusy, TimeoutError) as exc:
        state.set_worker("down", str(exc)[:200] or "geen antwoord")
        return state.worker
    state.caps = caps
    if caps.ffmpeg is None or caps.missing:
        missing = ", ".join(caps.missing) or "ffmpeg"
        if state.set_worker("ffmpeg_missing", missing):
            log.warning("mediaworker zonder %s", missing)
    elif state.set_worker("ok"):
        log.info("mediaworker bereikbaar (ffmpeg %s)", caps.ffmpeg)
    return state.worker


async def _loop(app, settings: Settings) -> None:
    delay = FIRST
    while True:
        await asyncio.sleep(delay)
        delay = FIRST if media_state(app).worker == "starting" else INTERVAL
        try:
            await check(app)
        except Exception:  # de lus mag nooit stoppen
            log.exception("gezondheid van de mediaworker")
        if media_state(app).worker != "starting":
            delay = INTERVAL


def start(app, settings: Settings) -> asyncio.Task:
    return asyncio.create_task(_loop(app, settings), name="media-health")


async def stop(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
