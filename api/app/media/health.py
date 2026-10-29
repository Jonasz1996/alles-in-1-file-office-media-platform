"""De gezondheid van de worker (Keuze 12, 13 en 46). Van M1 (M0 had de lus).

Elke INTERVAL s één keer caps, over een eigen verbinding die niet meetelt in de plaatsen: de
worker antwoordt uit zijn cache en start geen proces. Dat zet MediaState.worker en de caps in
de cache (MediaState.caps):

- ok: caps kwam terug, ffmpeg en ffprobe zijn er en er ontbreekt niets van §3.3;
- ffmpeg_missing: caps kwam terug, maar ffmpeg of iets wat §3.3 nodig heeft ontbreekt (de reden
  noemt wat; Keuze 46). Direct play werkt door;
- down: geen verbinding, geen antwoord binnen de tijd, een worker die zich niet bewijst, een
  weigering (de afscherming faalde: de reden van de worker), of een andere PROTOCOL (reden
  "versie", Keuze 47: tijdens een update even een oudere worker).

busy van de worker betekent ok: de worker leeft en heeft het druk (caps neemt nooit een plaats,
dus dat komt alleen van een oudere worker). Is de worker down, dan kijkt de lus vaker: na 1, 2,
4 … hooguit INTERVAL s, zodat een herstart snel weer ok geeft. Elke overgang komt één keer in
het log, met de reden.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

from app.config import Settings
from app.media import client, wire
from app.media.state import media_state

log = logging.getLogger("platform.media")

INTERVAL = 30.0
FIRST = 0.2  # snel na de start, daarna INTERVAL
RETRY_FIRST = 1.0  # down: na 1, 2, 4 … s opnieuw, hooguit INTERVAL
REASON = 200


def _reason(exc: BaseException) -> str:
    text = str(exc).strip() or exc.__class__.__name__
    if isinstance(exc, client.WorkerDown) and "PROTOCOL" in text:
        text = f"versie: {text} (de app spreekt {wire.PROTOCOL})"
    return text[:REASON]


def _set(state, worker: str, reason: str = "") -> None:
    before = state.worker
    if state.set_worker(worker, reason):
        if worker == "ok":
            ffmpeg = state.caps.ffmpeg if state.caps else None
            log.info("mediaworker bereikbaar (%s, was %s)", ffmpeg or "?", before)
        elif worker == "down":
            log.warning("mediaworker niet bereikbaar: %s", reason)
        else:
            log.warning("mediaworker zonder %s", reason)


async def check(app) -> str:
    """Eén keer caps; zet de staat en geeft hem terug."""
    state = media_state(app)
    try:
        caps = await client.caps()
    except client.WorkerBusy:
        _set(state, "ok", "druk")
        return state.worker
    except (client.WorkerDown, client.WorkerFailed, TimeoutError, OSError) as exc:
        _set(state, "down", _reason(exc) if not isinstance(exc, TimeoutError) else "geen antwoord")
        return state.worker
    state.caps = caps
    if caps.ffmpeg is None or caps.ffprobe is None or caps.missing:
        missing = ", ".join(caps.missing) or "ffmpeg en ffprobe"
        _set(state, "ffmpeg_missing", missing[:REASON])
    else:
        _set(state, "ok")
    return state.worker


async def _pause(seconds: float) -> None:
    await asyncio.sleep(seconds)  # apart, zodat een test de tijden van de lus kan zien


async def _loop(app, settings: Settings) -> None:
    delay = FIRST
    retry = RETRY_FIRST
    while True:
        await _pause(delay)
        try:
            worker = await check(app)
        except Exception as exc:  # de lus mag nooit stoppen
            log.exception("gezondheid van de mediaworker")
            # een fout telt als down, ook in MediaState: anders zegt play nog ok met de oude
            # caps (review golf 1, C3; bijv. caps van een nieuwere worker die niet valideert)
            with contextlib.suppress(Exception):
                _set(media_state(app), "down", _reason(exc))
            worker = "down"
        if worker == "down":
            delay, retry = retry, min(retry * 2, INTERVAL)
        else:
            delay, retry = INTERVAL, RETRY_FIRST


def start(app, settings: Settings) -> asyncio.Task:
    return asyncio.create_task(_loop(app, settings), name="media-health")


async def stop(task: asyncio.Task | None) -> None:
    if task is None:
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
