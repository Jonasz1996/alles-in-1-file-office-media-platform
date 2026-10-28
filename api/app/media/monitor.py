"""De meldingen van media op /beheer (bouwontwerp fase 5, §3.4 en Keuze 45). Stub van M0; M10
bouwt ze.

NOTICES zijn de vaste codes met hun Nederlandse tekst (frozen); de web-app vertaalt op code
(MEDIA_NOTICES in web/src/media/types.ts). routers/admin.py roept notices() aan voor
/api/v1/admin/status, net als die van office; een fout daarin breekt /beheer niet. Zonder media
(PLATFORM_MEDIA_ENABLED uit) zijn er geen meldingen: /beheer is dan precies dat van 0.6.0.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.cluster import Notice, notice
from app.media import runtime

NOTICES: dict[str, str] = {
    "media_worker_down": "De mediaworker is niet bereikbaar; video en muziek spelen nu niet af.",
    "media_ffmpeg_missing": "ffmpeg ontbreekt of werkt niet in de mediaworker.",
    "media_cache_full": "De cache van media is vol; omzetten gaat langzamer tot er ruimte is.",
    "media_sandbox_off": "De mediaworker draait zonder afscherming (lokaal, in de app zelf).",
    "media_provider_auth": "De sleutel van de online metadata werkt niet meer.",
    "media_provider_failing": "Online metadata opzoeken mislukt al een tijd.",
}


def media_notice(code: str, **params: str | int | float) -> Notice:
    """Een melding van media met de vaste tekst van die code (KeyError bij een onbekende)."""
    return notice(code, NOTICES[code], **params)


async def notices(app, session: AsyncSession) -> list[Notice]:
    """De meldingen van media voor /beheer. M10 vult ze; de stub van M0 geeft er geen."""
    if not runtime.enabled(app):
        return []
    return []
