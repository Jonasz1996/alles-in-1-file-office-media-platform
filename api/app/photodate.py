"""Opnamedatum van foto's en video's, voor de tijdlijn.

Een opnamedatum is een "zwevende" lokale tijd: de kloktijd van de camera, opgeslagen als
UTC-tijd met dezelfde cijfers en getoond met timeZone "UTC" (zie app/models.py). Zo staat een
foto van 23:30 op vakantie ook thuis op 23:30, op dezelfde dag.

Waar de datum vandaan komt, in volgorde:
1. de inhoud zelf (Blob.taken_at, uit EXIF of de metadata van een video; zie on_blob_meta);
2. anders de lastModified van het bestand bij de client;
3. anders het moment van uploaden.
De laatste twee zijn echte momenten (UTC) en worden in settings.timezone omgezet naar een
kloktijd.

Elk bestand dat een afbeelding of video is, krijgt bij het neerzetten taken_at en taken_day
(stamp); taken_day IS NOT NULL betekent dus "hoort op de tijdlijn". Komt de EXIF-datum later
binnen (de preview-taak leest hem), dan krijgen alle bestanden met die inhoud hem alsnog.

GPS en locatie bewaren we in fase 3 niet: split_capture gooit ze weg.
"""

from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app import render_meta
from app.models import Blob, Node

# Jaren daarbuiten zijn een kapotte klok of een kapot bestand
FIRST_YEAR, LAST_YEAR = 1900, 2100


def _zone(tz: str) -> ZoneInfo | Any:
    try:
        return ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        return UTC  # een onbekende tijdzone: dan maar UTC, liever dan een fout


def _aware(moment: datetime) -> datetime:
    """SQLite geeft tijden zonder tijdzone terug; ze staan altijd in UTC opgeslagen."""
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def wall_clock(moment: datetime, tz: str) -> datetime:
    """Een echt moment (zonder tijdzone: UTC) als zwevende lokale tijd in tijdzone tz:
    de kloktijd daar, met tzinfo UTC."""
    local = _aware(moment).astimezone(_zone(tz))
    return local.replace(tzinfo=UTC)


def day_of(taken_at: datetime) -> int:
    """De dag van een zwevende tijd als JJJJMMDD (20240701)."""
    return taken_at.year * 10000 + taken_at.month * 100 + taken_at.day


def is_media(mime: str | None) -> bool:
    """Afbeeldingen en video's: wat op de tijdlijn hoort."""
    mime = (mime or "").lower()
    return mime.startswith(("image/", "video/"))


def stamp_values(
    mime: str | None, blob_taken_at: datetime | None, fallback: datetime, tz: str
) -> dict[str, Any]:
    """{"taken_at", "taken_day"} voor een bestand met deze soort: de datum uit de inhoud
    (al een zwevende tijd), anders fallback (een echt moment) als kloktijd in tz. Geen
    afbeelding of video: beide None."""
    if not is_media(mime):
        return {"taken_at": None, "taken_day": None}
    taken = _aware(blob_taken_at) if blob_taken_at is not None else wall_clock(fallback, tz)
    return {"taken_at": taken, "taken_day": day_of(taken)}


def stamp(node: Node, *, blob_taken_at: datetime | None, fallback: datetime, tz: str) -> None:
    """Zet taken_at en taken_day van node (na het neerzetten of vervangen van de inhoud)."""
    values = stamp_values(node.mime, blob_taken_at, fallback, tz)
    node.taken_at, node.taken_day = values["taken_at"], values["taken_day"]


def from_ms(value: Any) -> datetime | None:
    """Een lastModified van de client (milliseconden sinds 1970, als getal of tekst) als echt
    moment in UTC; onzin of een jaar buiten 1900-2100 geeft None."""
    try:
        moment = datetime.fromtimestamp(int(value) / 1000, UTC)
    except (TypeError, ValueError, OverflowError, OSError):
        return None
    return moment if FIRST_YEAR <= moment.year <= LAST_YEAR else None


def split_capture(meta: dict) -> dict[str, Any]:
    """Haalt "taken_at", "taken_kind" en een eventuele "gps" uit meta (in place; gps wordt
    weggegooid) en geeft {"taken_at", "taken_kind"} terug. Wat overblijft, zoals "exif",
    blijft in meta en gaat naar preview_meta."""
    meta.pop("gps", None)
    return {"taken_at": meta.pop("taken_at", None), "taken_kind": meta.pop("taken_kind", None)}


def _parse(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return None


def captured_at(capture: dict, tz: str) -> datetime | None:
    """De opnamedatum uit split_capture als zwevende tijd. taken_kind "utc": een echt moment
    (zonder tijdzone: UTC), omgezet naar de kloktijd in tz. Anders ("local"): de kloktijd
    zoals hij is, ook als er een offset bij staat."""
    moment = _parse(capture.get("taken_at"))
    if moment is None:
        return None
    if capture.get("taken_kind") == "utc":
        taken = wall_clock(moment, tz)
    else:
        taken = moment.replace(tzinfo=UTC)
    if not FIRST_YEAR <= taken.year <= LAST_YEAR:
        return None
    return taken


async def on_blob_meta(
    session: AsyncSession, blob: Blob, capture: dict, exif: dict | None, tz: str
) -> None:
    """Na het lezen van de metadata van een blob (preview-taak of backfill), in de transactie
    van de aanroeper: blob.taken_at en meta_version, en de opnamedatum naar alle bestanden met
    deze inhoud die op de tijdlijn staan (taken_day gezet). Zonder datum in de inhoud houden
    die hun terugvaldatum. exif gegeven: in preview_meta onder "exif" (een nieuwe dict, zodat
    SQLAlchemy de wijziging ziet)."""
    taken = captured_at(capture, tz)
    blob.taken_at = taken
    blob.meta_version = render_meta.VERSION
    if exif is not None:
        blob.preview_meta = {**(blob.preview_meta or {}), "exif": exif}
    if taken is not None:
        await session.execute(
            update(Node)
            .where(Node.blob_id == blob.sha256, Node.taken_day.is_not(None))
            .values(taken_at=taken, taken_day=day_of(taken))
            .execution_options(synchronize_session=False)
        )
