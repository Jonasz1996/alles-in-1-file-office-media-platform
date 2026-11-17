"""Tijdelijke bronnen voor een conversie (Keuze 25), in het geheugen.

Voor een conversie haalt DS de bron op bij de app: GET /internal/office/src/{grant} op de
interne poort, met een download-JWT. De grant is een willekeurige naam voor één blob (sha),
tien minuten geldig; zo staat er geen sha of node-id in een adres dat DS ziet, en is een
grant na een herstart van de app vanzelf weg.
"""

import secrets
import time

TTL = 600.0
LIMIT = 10_000  # nooit onbegrensd groeien, ook niet als er iets misgaat

_grants: dict[str, tuple[str, float]] = {}


def _sweep(now: float) -> None:
    for grant_id, (_, expires) in list(_grants.items()):
        if expires <= now:
            del _grants[grant_id]


def grant(sha: str) -> str:
    now = time.monotonic()
    _sweep(now)
    while len(_grants) >= LIMIT:  # de oudste eerst weg
        del _grants[next(iter(_grants))]
    grant_id = secrets.token_urlsafe(24)
    _grants[grant_id] = (sha, now + TTL)
    return grant_id


def resolve(grant_id: str) -> str | None:
    """De sha van een grant, of None als hij niet bestaat of verlopen is."""
    found = _grants.get(grant_id)
    if found is None:
        return None
    sha, expires = found
    if expires <= time.monotonic():
        del _grants[grant_id]
        return None
    return sha


def clear() -> None:
    _grants.clear()
