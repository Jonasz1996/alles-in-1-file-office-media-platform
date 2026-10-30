"""De fouten van media als API-fout (bouwontwerp fase 5, §3.4). Frozen.

Elke code staat in web/src/i18n/errors/media.ts (test_error_catalog.py). Een pakket raist
`raise media_errors.busy("cpu", 30)` en nooit zelf een HTTPException; de vorm is die van
app.errors.fail.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from fastapi import HTTPException

from app.errors import fail
from app.media.client import WorkerBusy, WorkerDown, WorkerFailed
from app.media.wire import Code


class MediaBusy(Exception):
    """De toelating zegt nee (admission.reserve, Keuze 26). reason: cpu, disk, user, link of
    worker; retry_after in seconden."""

    def __init__(self, reason: str, retry_after: float = 2):
        super().__init__(reason)
        self.reason = reason
        self.retry_after = retry_after


def _retry(seconds: float) -> dict[str, str]:
    return {"Retry-After": str(max(1, math.ceil(seconds)))}


def off() -> HTTPException:
    return fail(503, "media_off", "Media staat uit op deze server")


def worker_down() -> HTTPException:
    return fail(503, "media_worker_down", "De mediaworker is niet bereikbaar")


def ffmpeg_missing() -> HTTPException:
    return fail(503, "media_ffmpeg_missing", "ffmpeg ontbreekt of werkt niet in de mediaworker")


def probe_pending(reason: str = "probe", retry_after: float = 2) -> HTTPException:
    return fail(
        409,
        "media_probe_pending",
        "Dit bestand wordt nog voorbereid",
        headers=_retry(retry_after),
        reason=reason,
    )


def unavailable(reason: str = "probe") -> HTTPException:
    return fail(409, "media_unavailable", "Dit bestand kan niet worden afgespeeld", reason=reason)


def unsupported(reasons: Sequence[str]) -> HTTPException:
    return fail(
        415,
        "media_unsupported",
        "Dit bestand kan op dit apparaat niet worden afgespeeld",
        reasons=list(reasons),
    )


def hdr(reason: str, can_raw: bool) -> HTTPException:
    return fail(
        422,
        "media_hdr",
        "Deze HDR-video kan niet worden omgezet",
        reason=reason,
        can_raw=can_raw,
    )


def busy(reason: str, retry_after: float = 2) -> HTTPException:
    return fail(
        503,
        "media_busy",
        "De server is te druk om dit nu af te spelen",
        headers=_retry(retry_after),
        reason=reason,
        retry_after=retry_after,
    )


def session_gone(reason: str | None = None) -> HTTPException:
    params = {"reason": reason} if reason else {}
    return fail(410, "media_session_gone", "Deze afspeelsessie bestaat niet meer", **params)


def access_gone(reason: str) -> HTTPException:
    return fail(403, "media_access_gone", "Je hebt geen toegang meer tot deze video", reason=reason)


def timeout(retry_after: float = 2) -> HTTPException:
    return fail(503, "media_timeout", "Het omzetten duurt te lang", headers=_retry(retry_after))


def transcode_failed() -> HTTPException:
    return fail(502, "media_transcode_failed", "Het omzetten van deze video mislukte")


def public_transcode() -> HTTPException:
    return fail(403, "media_public_transcode", "Omzetten mag niet via een publieke link")


def not_owner() -> HTTPException:
    return fail(
        403, "media_not_owner", "Alleen de eigenaar van de map kan er een bibliotheek van maken"
    )


def library_nested(library_id: str) -> HTTPException:
    return fail(
        409,
        "media_library_nested",
        "Deze map ligt binnen of boven een andere bibliotheek",
        library_id=library_id,
    )


def library_not_folder() -> HTTPException:
    return fail(422, "media_library_not_folder", "Een bibliotheek moet een map zijn")


def library_limit(maximum: int) -> HTTPException:
    return fail(422, "media_library_limit", "Je hebt al {max} bibliotheken", max=maximum)


def import_path() -> HTTPException:
    return fail(422, "media_import_path", "Dit pad ligt niet in de importmap")


def import_busy() -> HTTPException:
    return fail(409, "media_import_busy", "Er loopt al een import")


def bench_busy() -> HTTPException:
    return fail(409, "media_bench_busy", "Meten kan alleen als er niets wordt omgezet")


def provider_off() -> HTTPException:
    return fail(409, "media_provider_off", "Online opzoeken staat uit")


def provider_auth() -> HTTPException:
    return fail(422, "media_provider_auth", "De sleutel van de provider werkt niet")


def provider_unavailable() -> HTTPException:
    return fail(503, "media_provider_unavailable", "De provider is niet bereikbaar")


def provider_rate(retry_after: float) -> HTTPException:
    return fail(
        503,
        "media_provider_rate",
        "De provider remt af; probeer het zo opnieuw",
        headers=_retry(retry_after),
        retry_after=retry_after,
    )


def playlist_full(maximum: int) -> HTTPException:
    return fail(
        409, "media_playlist_full", "Een afspeellijst heeft hooguit {max} nummers", max=maximum
    )


def from_busy(exc: MediaBusy) -> HTTPException:
    return busy(exc.reason, exc.retry_after)


def from_worker(exc: BaseException) -> HTTPException:
    """Een fout van client.call als API-fout (Keuze 12, 13, 46)."""
    if isinstance(exc, WorkerBusy):
        return busy("worker", 2)
    if isinstance(exc, WorkerDown):
        return worker_down()
    if isinstance(exc, WorkerFailed):
        if exc.code == Code.FFMPEG_MISSING:
            return ffmpeg_missing()
        if exc.code == Code.TIMEOUT:
            return timeout()
        if exc.code in (Code.UNREADABLE, Code.UNKNOWN_FORMAT):
            return unavailable("probe")
        if exc.code in (Code.FAILED, Code.INTERNAL):
            return transcode_failed()
        # protocol, too_large, bad_fds, unknown_op, bad_args, not_allowed, not_built: een fout
        # in de app of een worker van een andere versie
        return worker_down()
    if isinstance(exc, TimeoutError):
        return timeout()
    return worker_down()
