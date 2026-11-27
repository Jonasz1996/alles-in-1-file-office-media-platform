"""De cache onder data_dir/media en het opruimen (Keuze 31). M4.

    sessions/<uuid>/<n>.m4s     de segmenten per sessie (streams.py; de inits, hooguit een
                                paar KB per generatie, staan in het geheugen van de stroom)
    subs/, trick/, art/, audio/ van M5, M8 en M9

- Een sessie dicht: haar map weg (streams.close). Bij het starten is `sessions/` helemaal leeg
  (runtime.clear_sessions).
- **Het venster in bytes per sessie:** `cache_gb` gedeeld door het aantal live stromen. Komt een
  stroom erboven, dan gaan meteen zijn oudste segmenten achter de speler weg, maar nooit die
  binnen 60 s achter de speler (`trim`). Daarna, als het nog niet past, de segmenten ver vóór de
  speler (meer dan 120 s: van een eerdere plek, voor een sprong terug); die maakt de producent
  zo nodig opnieuw. Zo blijft ook een 4K-remux met een hoge bitrate binnen de grens.
- **Elke 5 minuten** (`sweep`, de housekeeping media.sweep): verlopen sessies dicht
  (sessions.prune: 30 minuten stil of 12 uur oud); boven `cache_gb` (de sessies samen met
  `audio/`) eerst de oudste stille sessies dicht (120 s geen verzoek), dan de oudste segmenten
  achter de speler, hoe jong ze ook zijn (wel nooit binnen 60 s achter de speler); en minder
  dan 10 GB vrij op data_dir zet `disk_low` (de melding media_cache_full is van M10;
  admission weigert dan een nieuwe transcode of remux met reason disk).
- **subs/ en trick/** (integratie, m10.md "Naar de integratie" punt 2): wat de app zelf uit een
  blob maakt, samen hooguit `cache_gb / DERIVED_SHARE` (standaard 5 GiB), los van de grens van
  de sessies en `audio/` (die blijft zoals hierboven). Daarboven gaat in dezelfde sweep de map
  per blob weg die het langst niet gebruikt is (de mtime van de map: het maken, en elke keer dat
  een kijker trick.vtt of een ondertitel haalt, zie routers/media_subs.py). Nooit van een blob
  die een open sessie speelt, ook niet van haar losse ondertitels; nooit een tijdelijke map of
  bestand (een naam met een punt ervoor). Van trick/ de hele map, en de staat van de sprites gaat
  van ready terug naar none (zoals Opruimen, admin._trick_again): play zet de analyse dan weer
  klaar (media_play.steps_left). Van subs/ alleen de VTT's: een markering `.failed` blijft (de
  staat failed); een ondertitel komt bij het volgende verzoek weer (subtitles.ensure_*).

`cache_gb` telt in GiB (1024³ bytes).
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from app.media import runtime

log = logging.getLogger("platform.media")

KINDS = ("sessions", "audio", "trick", "art")
GiB = 1024**3
KEEP_BEHIND_MS = 60_000  # nooit weg: wat de speler net speelde (een stap terug)
FAR_AHEAD_MS = 120_000  # wat verder vóór de speler ligt, mag weg als het moet
QUIET_S = 120.0  # een sessie zonder verzoek: stil (de producent is dan al dicht)
WALK_MAX = 200_000  # hooguit zoveel bestanden tellen per soort (sizes)
DERIVED = ("subs", "trick")  # wat de app zelf uit een blob maakt (M5)
DERIVED_SHARE = 4  # subs/ en trick/ samen hooguit cache_gb / 4 (zoals audio/, stream_audio)

# Minder dan 10 GB vrij op data_dir, bij de laatste sweep (voor M10: media_cache_full)
disk_low = False


@dataclass(frozen=True)
class Piece:
    """Eén segment in de cache: nummer, grootte, en waar het in mediatijd ligt."""

    n: int
    size: int
    begin_ms: int
    end_ms: int


def limit_bytes(cache_gb: float) -> int:
    return int(cache_gb * GiB)


def window(cache_gb: float, live: int) -> int:
    """Het venster van één stroom: cache_gb gedeeld door het aantal live stromen."""
    return limit_bytes(cache_gb) // max(1, live)


def candidates(
    pieces: Iterable[Piece], playhead_ms: int, *, keep_behind_ms: int | None = None
) -> list[Piece]:
    """Wat weg mag, in volgorde: eerst achter de speler (het verste eerst), nooit binnen
    keep_behind_ms (standaard KEEP_BEHIND_MS); dan ver vóór de speler (het verste eerst)."""
    keep = KEEP_BEHIND_MS if keep_behind_ms is None else keep_behind_ms
    behind = sorted((p for p in pieces if p.end_ms <= playhead_ms - keep), key=lambda p: p.begin_ms)
    ahead = sorted(
        (p for p in pieces if p.begin_ms >= playhead_ms + FAR_AHEAD_MS),
        key=lambda p: -p.begin_ms,
    )
    return behind + ahead


def trim(pieces: list[Piece], playhead_ms: int, budget: int) -> list[int]:
    """De segmenten die weg moeten om onder budget te komen (zie candidates); [] als het al
    past. Past het ook daarna niet, dan zoveel als mag."""
    total = sum(p.size for p in pieces)
    out: list[int] = []
    for p in candidates(pieces, playhead_ms):
        if total <= budget:
            break
        out.append(p.n)
        total -= p.size
    return out


def dir_size(path: Path, limit: int = WALK_MAX) -> int:
    """Bytes onder path (zonder symlinks te volgen), hooguit limit bestanden."""
    total = 0
    seen = 0
    stack = [path]
    while stack and seen < limit:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            seen += 1
            try:
                if entry.is_dir(follow_symlinks=False):
                    stack.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
            except OSError:
                continue
    return total


@dataclass(frozen=True)
class Made:
    """De map van één blob onder subs/ of trick/: wat er weg kan, en wanneer ze het laatst
    gebruikt is (de mtime van de map)."""

    kind: str
    sha: str
    path: Path
    size: int
    used: float


def _droppable(kind: str, name: str) -> bool:
    """Wat bij het opruimen weg mag: in trick/ alles, in subs/ alleen de VTT's (een markering
    .failed is de staat failed). Nooit een tijdelijk bestand (een punt ervoor)."""
    return not name.startswith(".") and (kind == "trick" or name.endswith(".vtt"))


def derived(base: Path, limit: int = WALK_MAX) -> list[Made]:
    """De mappen per blob onder base/subs en base/trick met iets dat weg kan; een tijdelijke map
    (een punt ervoor: trick._store) niet. Hooguit limit bestanden tellen."""
    out: list[Made] = []
    seen = 0
    for kind in DERIVED:
        try:
            folders = list(os.scandir(base / kind))
        except OSError:
            continue
        for folder in folders:
            if seen >= limit:
                return out
            try:
                if folder.name.startswith(".") or not folder.is_dir(follow_symlinks=False):
                    continue
                used = folder.stat(follow_symlinks=False).st_mtime
                files = list(os.scandir(folder.path))
            except OSError:
                continue
            size = 0
            for entry in files:
                seen += 1
                with contextlib.suppress(OSError):
                    if _droppable(kind, entry.name) and entry.is_file(follow_symlinks=False):
                        size += entry.stat(follow_symlinks=False).st_size
            if size:
                out.append(Made(kind, folder.name, Path(folder.path), size, used))
    return out


def evict(made: list[Made], budget: int, keep: set[str]) -> list[Made]:
    """Wat weg moet om onder budget te komen: het langst niet gebruikte eerst, nooit van een blob
    in keep (een open sessie). [] als het al past; past het ook daarna niet, dan zoveel als mag."""
    total = sum(m.size for m in made)
    out: list[Made] = []
    for m in sorted(made, key=lambda m: m.used):
        if total <= budget:
            break
        if m.sha in keep:
            continue
        out.append(m)
        total -= m.size
    return out


def _drop(made: Made) -> None:
    """Weg uit de cache (in een thread). trick: de hele map; subs: alleen de VTT's."""
    if made.kind == "trick":
        shutil.rmtree(made.path, ignore_errors=True)
        return
    with contextlib.suppress(OSError):
        for entry in list(os.scandir(made.path)):
            if _droppable(made.kind, entry.name):
                with contextlib.suppress(OSError):
                    os.unlink(entry.path)


def used(folder: Path) -> None:
    """Een kijker haalt iets uit deze map (trick.vtt, een ondertitel): de mtime is het laatste
    gebruik voor het opruimen hierboven. Een map die er (nog) niet is: niets."""
    with contextlib.suppress(OSError):
        os.utime(folder)


def in_use() -> set[str]:
    """De blobs die een open sessie speelt, en haar losse ondertitels."""
    from app.media import sessions

    keep: set[str] = set()
    for s in sessions.table().open():
        keep.add(s.sha)
        keep.update(str(entry.get("sha")) for entry in s.subs if entry.get("sha"))
    return keep


async def _trick_none(settings, shas: list[str]) -> None:
    """De sprites van deze blobs zijn weg: ready wordt none (dan klopt wat de app over het item
    zegt, en play zet de analyse weer klaar). Eén korte schrijfactie na het weghalen; geen
    verbinding open terwijl er gewacht wordt (regel 8)."""
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.db import get_engine
    from app.models import MediaProbe

    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    for start in range(0, len(shas), 500):
        part = shas[start : start + 500]
        async with maker() as session:
            await session.execute(
                update(MediaProbe)
                .where(MediaProbe.blob_sha.in_(part), MediaProbe.trick_state == "ready")
                .values(trick_state="none", trick=None)
            )
            await session.commit()


async def sweep_derived(settings, cache_gb: float) -> list[Made]:
    """subs/ en trick/ onder cache_gb / DERIVED_SHARE (zie boven). Geeft wat weg ging."""
    import asyncio

    made = await asyncio.to_thread(derived, settings.media_dir)
    drop = evict(made, limit_bytes(cache_gb) // DERIVED_SHARE, in_use())
    if not drop:
        return []
    for m in drop:
        await asyncio.to_thread(_drop, m)
    log.info("cache: %d mappen onder subs/ en trick/ weg (het langst niet gebruikt)", len(drop))
    tricks = sorted({m.sha for m in drop if m.kind == "trick"})
    if tricks:
        await _trick_none(settings, tricks)
    return drop


def sizes() -> dict[str, int]:
    """Bytes per soort, voor /beheer (M10). sessions uit wat de stromen bijhouden."""
    from app.media import streams

    base = runtime.settings().media_dir
    out = {kind: dir_size(base / kind) for kind in KINDS if kind != "sessions"}
    out["sessions"] = streams.cached_bytes()
    return {kind: out.get(kind, 0) for kind in KINDS}


async def sweep(settings) -> None:
    """Het opruimwerk van de cache (media.sweep, elke 5 minuten in de rij default zolang media
    aan staat)."""
    import asyncio

    from app.media import admission, sessions, streams

    global disk_low
    sessions.table().prune(sessions.clock())
    cache_gb = admission.config().cache_gb
    limit = limit_bytes(cache_gb)
    audio = await asyncio.to_thread(dir_size, settings.media_dir / "audio")
    total = streams.cached_bytes() + audio
    closed: set = set()
    if total > limit:
        # Eerst de oudste stille sessies dicht
        for st in sorted(streams.all(), key=lambda s: s.last_request):
            if total <= limit:
                break
            if st.quiet_s() < QUIET_S:
                continue
            freed = st.bytes
            log.info("cache vol: stille mediasessie %s dicht", st.session.id)
            sessions.close(st.session)
            closed.add(st.session.id)  # haar stroom gaat op de achtergrond dicht
            total -= freed
    if total > limit:
        # Dan de oudste segmenten achter de speler, hoe jong ook
        for st in sorted(streams.all(), key=lambda s: -s.bytes):
            if total <= limit:
                break
            if st.session.id in closed or st.closed:
                continue
            budget = max(0, st.bytes - (total - limit))
            before = st.bytes
            await st.trim(budget)
            total -= before - st.bytes
    await sweep_derived(settings, cache_gb)
    free = await asyncio.to_thread(admission.disk_free, settings.data_dir)
    low = free < admission.DISK_MIN
    if low and not disk_low:
        log.warning("minder dan 10 GB vrij op data_dir: geen nieuwe transcode of remux")
    disk_low = low
