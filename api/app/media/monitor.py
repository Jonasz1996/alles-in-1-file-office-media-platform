"""De meldingen van media op /beheer (bouwontwerp fase 5, §3.4 en Keuze 45). Gebouwd door M10.

NOTICES zijn de vaste codes met hun Nederlandse tekst (frozen); de web-app vertaalt op code
(MEDIA_NOTICES in web/src/media/types.ts). routers/admin.py roept notices() aan voor
/api/v1/admin/status, net als die van office; een fout daarin breekt /beheer niet. Zonder media
(PLATFORM_MEDIA_ENABLED uit, of uitgezet op /beheer) zijn er geen meldingen: /beheer is dan
precies dat van 0.6.0.

Elke melding staat aan zolang het zo is, en gaat vanzelf weg als het over is. Niets wordt
onthouden: alles leest wat de andere delen al bijhouden.

- media_worker_down: de worker is weg (MediaState.worker "down", health.py van M1), ook als
  hij zijn zelftest niet haalde (de reden staat op het tabblad Media);
- media_ffmpeg_missing: de worker antwoordt, maar ffmpeg ontbreekt of mist iets (caps);
- media_cache_full: minder dan 10 GB vrij op data_dir (cache.disk_low, gezet door de sweep van
  M4). Nieuwe omzettingen wachten dan (503 media_busy reason disk); direct play kan nog;
- media_sandbox_off: de worker draait zonder afscherming (de lokale worker, of een container
  die zegt dat hij niet afgeschermd is). Jonas zet dat zelf zo (MEDIA_SANDBOX=uit);
- media_provider_auth: een provider zegt dat de sleutel niet werkt (providers.status() van M8:
  auth true), of de bewaarde sleutel van TMDB werkt niet meer (secrets.status() van M8: ok
  false, na een 401 of een mislukte test);
- media_provider_failing: een provider faalt al 24 uur aan één stuk (failing_since van M8 is
  minstens 24 uur geleden, of M8 zegt het zelf met failing true). Korter offline geeft geen
  melding: de jobs proberen het later opnieuw (Keuze 39).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.cluster import Notice, notice
from app.media import runtime
from app.media.state import media_policy, media_state

log = logging.getLogger("platform.media")

NOTICES: dict[str, str] = {
    "media_worker_down": "De mediaworker is niet bereikbaar; video en muziek spelen nu niet af.",
    "media_ffmpeg_missing": "ffmpeg ontbreekt of werkt niet in de mediaworker.",
    "media_cache_full": "De cache van media is vol; omzetten gaat langzamer tot er ruimte is.",
    "media_sandbox_off": "De mediaworker draait zonder afscherming (lokaal, in de app zelf).",
    "media_provider_auth": "De sleutel van de online metadata werkt niet meer.",
    "media_provider_failing": "Online metadata opzoeken mislukt al een tijd.",
}

# Pas na zoveel fouten aan één stuk een melding (Keuze 39)
PROVIDER_FAILING = timedelta(hours=24)


def media_notice(code: str, **params: str | int | float) -> Notice:
    """Een melding van media met de vaste tekst van die code (KeyError bij een onbekende)."""
    return notice(code, NOTICES[code], **params)


def now() -> datetime:
    """De klok van de meldingen (tests zetten hier een nepklok)."""
    return datetime.now(UTC)


def _since(value) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value:
        try:
            found = datetime.fromisoformat(value)
        except ValueError:
            return None
        return found if found.tzinfo else found.replace(tzinfo=UTC)
    return None


def worker_notices(app) -> list[Notice]:
    state = media_state(app)
    if state.worker == "down":
        return [media_notice("media_worker_down")]
    out = []
    caps = state.caps
    if state.worker == "ffmpeg_missing" or (caps is not None and (not caps.ffmpeg or caps.missing)):
        out.append(media_notice("media_ffmpeg_missing"))
    if state.worker != "starting" and (
        (caps is not None and caps.sandbox == "off") or (caps is None and state.mode == "local")
    ):
        out.append(media_notice("media_sandbox_off"))
    return out


def cache_notices() -> list[Notice]:
    from app.media import cache

    return [media_notice("media_cache_full")] if cache.disk_low else []


def provider_notices(at: datetime | None = None, tmdb: dict | None = None) -> list[Notice]:
    """Uit providers.status() (M8) en tmdb (admin.tmdb_status). Per provider hooguit één
    melding; auth gaat voor."""
    from app.media import providers

    at = at or now()
    try:
        found = providers.status()
    except Exception:  # de meldingen breken /beheer nooit
        log.exception("de staat van de providers")
        return []
    out = []
    for name in providers.PROVIDERS:
        s = found.get(name) or {}
        if s.get("auth") is True or (name == "tmdb" and tmdb and tmdb.get("ok") is False):
            out.append(media_notice("media_provider_auth", provider=name))
            continue
        since = _since(s.get("failing_since"))
        if s.get("failing") is True or (since is not None and at - since >= PROVIDER_FAILING):
            out.append(media_notice("media_provider_failing", provider=name))
    return out


async def notices(app, session: AsyncSession) -> list[Notice]:
    """De meldingen van media voor /beheer (in de volgorde van NOTICES)."""
    if not media_state(app).enabled:
        return []
    policy = media_policy(app)
    if policy.config is None and session is not None:
        await policy.get(session)  # zo weet runtime.enabled of /beheer hem uitzette
    if not runtime.enabled(app):
        return []
    tmdb = None
    if session is not None:
        from app.media import admin

        tmdb = await admin.tmdb_status(session, runtime.settings())
    found = [*worker_notices(app), *cache_notices(), *provider_notices(tmdb=tmdb)]
    order = list(NOTICES)
    return sorted(found, key=lambda n: order.index(n.code))
