"""De keten van mediabeheer (fase 11, stap 3): zoeken, kiezen, downloaden, vastgelopen
vervangen en importeren. `tick(settings)` doet één ronde; `loop(settings)` elke minuut.

Een ronde:

1. **Bijhouden.** De torrents van de categorie van het platform in qBittorrent, per tag
   `platform-<id van de grab>`. Een grab die klaar is (seeden of klaar), gaat naar de import. Een
   grab die vastzit (fout, verdwenen, of `STALL_HOURS` zonder voortgang en zonder seeders) wordt
   weggehaald met zijn bestanden en komt op de zwarte lijst; de titel wordt dan opnieuw gezocht.
2. **Importeren.** Van een klare download zet de app de video's (en ondertitels ernaast) met een
   harde link klaar in `<data>/import/arr/<grab>/` met een nette mapnaam (`Titel (Jaar)/` of
   `Serie/Season 01/`), haalt de torrent weg uit qBittorrent en de download uit de downloadmap
   (zo heeft elk bestand nog één naam), en zet de
   import van fase 5 klaar (verplaatsen) naar de map van de bibliotheek, als de eigenaar van de
   bibliotheek. Zonder video (ingepakt in rar, of iets anders) faalt de grab: de volgende release.
   Er wordt na de import niet geseed (keuze voor nu: zo blijft er één kopie op de schijf).
   Slaat de import een bestand over (bv. "in gebruik"), dan blijft het in de staging staan; de
   app probeert zo'n rest elk `LEFTOVER_RETRY` opnieuw, een dag lang (`LEFTOVER_GIVE_UP`).
3. **Zoeken.** Elke gevolgde titel zonder lopende of geslaagde grab, hooguit eens per
   `SEARCH_HOURS`, en alleen wat nog niet in de bibliotheek staat: een film met hetzelfde TMDB-
   of IMDb-id (of dezelfde titel en hooguit een jaar verschil) is er al; van een serie telt per
   aflevering wat de scanner van fase 5 vond. Welke seizoenen en afleveringen er zijn, zegt TMDB
   (zonder sleutel zoekt de app geen series). Bij elke torrentsite van Prowlarr, via Torznab:
   films op IMDb-id als de site dat kan, anders op titel en jaar; series per seizoen met
   ontbrekende afleveringen (een seizoenspakket gaat voor als er van dat seizoen nog niets is). Een
   release telt alleen als zijn naam bij de titel past (de Nederlandse of de oorspronkelijke) en,
   bij een film, het jaar hooguit één verschilt. De keuze is die van `score.choose` met het
   profiel van de titel.

Zonder Prowlarr of qBittorrent doet een ronde niets. Een fout bij één site of titel houdt de
rest niet tegen; de log noemt nooit een sleutel of wachtwoord.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import unicodedata
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

import httpx
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.arr import prowlarr, qbit, score, subs, torznab
from app.arr import settings as arr_settings
from app.arr.release import parse_release
from app.config import Settings
from app.db import get_engine
from app.models import ArrGrab, ArrWanted, MediaItem, MediaLibrary, MediaShow

log = logging.getLogger("platform.arr")

INTERVAL_S = 60
SEARCH_HOURS = 6
STALL_HOURS = 6
SETTLE_S = 120  # zo lang na het klaar zijn wachten (importer.MIN_AGE_S is 60)
MAX_SEASONS = 30
VIDEO = frozenset({".mkv", ".mp4", ".avi", ".m4v", ".mov", ".ts", ".webm", ".wmv"})
SUBS = frozenset({".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx"})
SAMPLE = re.compile(r"(?i)(^|[\W_])sample([\W_]|$)")
ACTIVE = ("downloading", "importing")
LEFTOVER_RETRY = timedelta(hours=1)
LEFTOVER_GIVE_UP = timedelta(days=1)


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def tag_of(grab_id: uuid.UUID) -> str:
    return f"platform-{grab_id}"


def squash(text: str | None) -> str:
    """Een titel om te vergelijken: zonder accenten, leestekens en hoofdletters; & is and."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = text.lower().replace("&", "and")
    return re.sub(r"[^a-z0-9]+", "", text)


def fits(wanted: ArrWanted, name: str) -> bool:
    """Past deze releasenaam bij de titel (en bij een film bij het jaar)?"""
    release = parse_release(name, "movies" if wanted.kind == "movie" else "shows")
    found = squash(release.title)
    titles = {squash(wanted.title), squash(wanted.original_title)} - {""}
    if not found or found not in titles:
        # "The Office US" en "The Office" (de serie heet zo bij TMDB): het jaar of land erachter
        if not any(found.startswith(t) and len(found) - len(t) <= 4 for t in titles):
            return False
    if wanted.kind == "movie" and wanted.year and release.year:
        return abs(release.year - wanted.year) <= 1
    return True


@dataclass
class Links:
    prowlarr: arr_settings.ProwlarrConf
    qbit: arr_settings.QBitConf


async def _links(session: AsyncSession, settings: Settings) -> Links | None:
    p = await arr_settings.load_prowlarr(session, settings)
    q = await arr_settings.load_qbit(session, settings)
    if p is None or q is None:
        return None
    return Links(p, q)


# --- zoeken ---------------------------------------------------------------------------------


async def _results(
    client: httpx.AsyncClient,
    conf: arr_settings.ProwlarrConf,
    wanted: ArrWanted,
    season: int | None,
) -> list[tuple[str, torznab.Result]]:
    """Alle resultaten van alle torrentsites: (naam van de site, resultaat)."""
    found: list[tuple[str, torznab.Result]] = []
    for site in await prowlarr.indexers(client, conf.url, conf.key):
        base = prowlarr.torznab_base(conf.url, site.id)
        try:
            caps = torznab.parse_caps(await torznab.fetch(client, torznab.caps_url(base, conf.key)))
            if wanted.kind == "movie":
                by_id = caps.supports("movie-search", "imdbid") and wanted.imdb_id
                url = torznab.search_url(
                    base,
                    conf.key,
                    kind="movie" if caps.supports("movie-search") else "search",
                    imdb=wanted.imdb_id if by_id else None,
                    query=None
                    if by_id
                    else f"{wanted.original_title or wanted.title} {wanted.year or ''}".strip(),
                    categories=torznab.MOVIES,
                )
            else:
                url = torznab.search_url(
                    base,
                    conf.key,
                    kind="tv" if caps.supports("tv-search") else "search",
                    query=wanted.original_title or wanted.title,
                    season=season if caps.supports("tv-search", "season") else None,
                    categories=torznab.SHOWS,
                )
            for result in torznab.parse_results(await torznab.fetch(client, url)):
                found.append((site.name, result))
        except torznab.TorznabError as exc:
            log.info("arr: site %s: %s", site.name, exc)
    return found


def _candidates(
    wanted: ArrWanted, results: list[tuple[str, torznab.Result]], season: int | None
) -> tuple[list[score.Candidate], dict[str, tuple[str, torznab.Result]]]:
    kind = "movies" if wanted.kind == "movie" else "shows"
    out: list[score.Candidate] = []
    by_guid: dict[str, tuple[str, torznab.Result]] = {}
    for site, result in results:
        if result.guid in by_guid or not (result.link or result.magnet):
            continue
        if not fits(wanted, result.title):
            continue
        release = parse_release(result.title, kind)
        if wanted.kind == "show":
            if release.season != season:
                continue
            if release.season_end is not None:  # meer seizoenen in één: niet nu
                continue
        out.append(score.Candidate(release, result.size, result.seeders, result.guid))
        by_guid[result.guid] = (site, result)
    return out, by_guid


async def _blocked(session: AsyncSession, wanted: ArrWanted) -> set[str]:
    rows = await session.scalars(
        select(ArrGrab.guid).where(ArrGrab.wanted_id == wanted.id, ArrGrab.state == "failed")
    )
    return set(rows)


async def _grab(
    session: AsyncSession,
    q: qbit.QBit,
    links: Links,
    wanted: ArrWanted,
    candidate: score.Candidate,
    site: str,
    result: torznab.Result,
    season: int | None,
) -> ArrGrab:
    grab = ArrGrab(
        id=uuid.uuid4(),
        wanted_id=wanted.id,
        season=season,
        episode=candidate.release.episode,
        release=result.title[:500],
        indexer=site[:200],
        guid=result.guid[:1000],
        size=result.size,
        quality=score.quality(candidate.release),
        state="downloading",
        progress=0.0,
    )
    await q.add(result.magnet or result.link, tag=tag_of(grab.id), category=links.qbit.category)
    session.add(grab)
    await session.commit()
    log.info("arr: %s gekozen voor %s (%s)", grab.release, wanted.title, grab.quality)
    return grab


async def have_movie(session: AsyncSession, wanted: ArrWanted) -> bool:
    """Staat deze film al in zijn bibliotheek?"""
    rows = await session.execute(
        select(MediaItem.ids, MediaItem.title, MediaItem.year).where(
            MediaItem.library_id == wanted.library_id,
            MediaItem.kind == "movie",
            MediaItem.present.is_(True),
        )
    )
    titles = {squash(wanted.title), squash(wanted.original_title)} - {""}
    for ids, title, year in rows:
        ids = ids or {}
        if str(ids.get("tmdb", "")) == str(wanted.tmdb_id):
            return True
        if wanted.imdb_id and ids.get("imdb") == wanted.imdb_id:
            return True
        if squash(title) in titles and (
            not wanted.year or not year or abs(year - wanted.year) <= 1
        ):
            return True
    return False


async def have_episodes(session: AsyncSession, wanted: ArrWanted) -> set[tuple[int, int]]:
    """De afleveringen (seizoen, aflevering) van deze serie die al in de bibliotheek staan."""
    titles = {squash(wanted.title), squash(wanted.original_title)} - {""}
    shows = [
        show.id
        for show in await session.scalars(
            select(MediaShow).where(MediaShow.library_id == wanted.library_id)
        )
        if str((show.ids or {}).get("tmdb", "")) == str(wanted.tmdb_id)
        or squash(show.title) in titles
    ]
    if not shows:
        return set()
    rows = await session.execute(
        select(MediaItem.season, MediaItem.episode, MediaItem.episode_end).where(
            MediaItem.show_id.in_(shows),
            MediaItem.kind == "episode",
            MediaItem.present.is_(True),
        )
    )
    out: set[tuple[int, int]] = set()
    for season, episode, end in rows:
        if season is None or episode is None:
            continue
        for n in range(episode, (end or episode) + 1):
            out.add((season, n))
    return out


async def tmdb_episodes(session: AsyncSession, settings: Settings, wanted: ArrWanted):
    """{seizoen: [afleveringen die al uitkwamen]} van TMDB, of None zonder werkende sleutel.
    Tests vervangen deze functie."""
    from app.media.providers import tmdb
    from app.media.providers.base import ProviderUnavailable
    from app.media.providers.secrets import load

    await load(session, settings)
    provider = tmdb.provider()
    if provider is None:
        return None
    show_id = f"tv:{wanted.tmdb_id}"
    details = await provider.details("show", show_id, "en-US")
    today = _now().date().isoformat()
    out: dict[int, list[int]] = {}
    for n in range(1, min(details.seasons or 0, MAX_SEASONS) + 1):
        if wanted.seasons is not None and n not in wanted.seasons:
            continue
        try:
            episodes = await provider.season(show_id, n, "en-US")
        except ProviderUnavailable:
            continue
        aired = [e.episode for e in episodes if e.aired and e.aired <= today]
        if aired:
            out[n] = aired
    return out


async def search(
    session: AsyncSession,
    client: httpx.AsyncClient,
    links: Links,
    q: qbit.QBit,
    wanted: ArrWanted,
    settings: Settings | None = None,
) -> list[ArrGrab]:
    """Zoek één gevolgde titel en geef wat er gekozen is (leeg: niets goeds of al compleet)."""
    profile = score.BUILTIN.get(wanted.profile, score.BUILTIN["hd-1080p"])
    blocked = await _blocked(session, wanted)
    grabbed: list[ArrGrab] = []
    if wanted.kind == "movie" and await have_movie(session, wanted):
        pass
    elif wanted.kind == "movie":
        results = await _results(client, links.prowlarr, wanted, None)
        candidates, by_guid = _candidates(wanted, results, None)
        best, _ = score.choose(candidates, profile, minutes=wanted.runtime_min, blocked=blocked)
        if best is not None:
            site, result = by_guid[best.id]
            grabbed.append(await _grab(session, q, links, wanted, best, site, result, None))
    else:
        aired = await tmdb_episodes(session, settings, wanted) if settings is not None else None
        if aired is None:
            log.info("arr: %s overgeslagen: series zoeken heeft TMDB nodig", wanted.title)
            aired = {}
        have = await have_episodes(session, wanted)
        busy = {
            (g.season, g.episode)
            for g in await session.scalars(
                select(ArrGrab).where(ArrGrab.wanted_id == wanted.id, ArrGrab.state != "failed")
            )
        }
        for season, episodes in sorted(aired.items()):
            if (season, None) in busy:
                continue
            missing = {e for e in episodes if (season, e) not in have and (season, e) not in busy}
            if not missing:
                continue
            results = await _results(client, links.prowlarr, wanted, season)
            candidates, by_guid = _candidates(wanted, results, season)
            # Een seizoenspakket alleen als er van dit seizoen nog niets is
            if len(missing) == len(episodes):
                packs = [c for c in candidates if c.release.full_season]
                best, _ = score.choose(packs, profile, blocked=blocked)
                if best is not None:
                    site, result = by_guid[best.id]
                    grabbed.append(
                        await _grab(session, q, links, wanted, best, site, result, season)
                    )
                    continue
            # Anders per ontbrekende aflevering de beste
            per_episode: dict[int, list[score.Candidate]] = {}
            for c in candidates:
                if c.release.episode in missing and not c.release.episode_end:
                    per_episode.setdefault(c.release.episode, []).append(c)
            for episode in sorted(per_episode):
                best, _ = score.choose(per_episode[episode], profile, blocked=blocked)
                if best is not None:
                    site, result = by_guid[best.id]
                    grabbed.append(
                        await _grab(session, q, links, wanted, best, site, result, season)
                    )
    wanted.searched_at = _now()
    await session.commit()
    return grabbed


# --- importeren -------------------------------------------------------------------------------


def downloads_dir(settings: Settings) -> Path:
    """Waar de app de downloads van qBittorrent ziet (in qBittorrent `/downloads`)."""
    return Path(settings.data_dir) / "import" / "torrents"


def staging_dir(settings: Settings) -> Path:
    return Path(settings.data_dir) / "import" / "arr"


def local_path(settings: Settings, conf: arr_settings.QBitConf, content_path: str) -> Path | None:
    """Het pad van qBittorrent (onder zijn save_path) als pad van de app; None als het daar
    buiten valt of `..` bevat."""
    inner = PurePosixPath(content_path)
    base = PurePosixPath(conf.save_path)
    try:
        rest = inner.relative_to(base)
    except ValueError:
        return None
    if any(part in ("..", "") for part in rest.parts):
        return None
    return downloads_dir(settings).joinpath(*rest.parts)


def _safe(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', " ", name).strip(" .")
    return re.sub(r"\s+", " ", name)[:200] or "Onbekend"


def folder_name(wanted: ArrWanted) -> str:
    return _safe(f"{wanted.title} ({wanted.year})" if wanted.year else wanted.title)


def _files(root: Path) -> list[Path]:
    """De gewone bestanden onder root (geen symlinks volgen)."""
    if root.is_symlink():
        return []
    if root.is_file():
        return [root]
    out = []
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if not (Path(dirpath) / d).is_symlink()]
        for name in filenames:
            path = Path(dirpath) / name
            if path.is_file() and not path.is_symlink():
                out.append(path)
    return sorted(out)


def stage(settings: Settings, grab: ArrGrab, wanted: ArrWanted, source: Path) -> Path | None:
    """De video's en ondertitels met een harde link in <staging>/<grab>/…; geeft de bronmap voor
    de import (relatief aan de importmap) of None zonder video."""
    files = _files(source)
    videos = [f for f in files if f.suffix.lower() in VIDEO and not SAMPLE.search(f.stem)]
    if not videos:
        return None
    subs = [f for f in files if f.suffix.lower() in SUBS]
    top = staging_dir(settings) / str(grab.id)
    if wanted.kind == "movie":
        video = max(videos, key=lambda f: f.stat().st_size)
        target = top / folder_name(wanted)
        target.mkdir(parents=True, exist_ok=True)
        base = folder_name(wanted)
        os.link(video, target / f"{base}{video.suffix.lower()}")
        for sub in subs:
            # Film.nl.srt houdt zijn taal; anders gewoon de naam van de film
            lang = sub.stem.rsplit(".", 1)[-1] if "." in sub.stem else ""
            suffix = f".{lang}" if 2 <= len(lang) <= 3 and lang.isalpha() else ""
            dest = target / f"{base}{suffix}{sub.suffix.lower()}"
            if not dest.exists():
                os.link(sub, dest)
    else:
        show = _safe(wanted.title)
        for f in videos + subs:
            release = parse_release(f.name, "shows")
            season = release.season if release.season is not None else grab.season
            if season is None:
                continue
            target = top / show / f"Season {season:02d}"
            target.mkdir(parents=True, exist_ok=True)
            dest = target / _safe(f.name)
            if not dest.exists():
                os.link(f, dest)
    return top.relative_to(staging_dir(settings).parent)


def _remove_download(settings: Settings, path: Path) -> None:
    """Een download weg uit de downloadmap (nooit iets daarbuiten, nooit een symlink volgen)."""
    root = downloads_dir(settings).resolve()
    if path.is_symlink():
        path.unlink(missing_ok=True)
        return
    try:
        path.resolve().relative_to(root)
    except ValueError:
        return
    if path.resolve() == root:
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def clean_staging(settings: Settings) -> None:
    """Lege mappen in de staging weg (de import verplaatst de bestanden eruit)."""
    root = staging_dir(settings)
    if not root.is_dir():
        return
    for grab_dir in root.iterdir():
        if grab_dir.is_dir() and not grab_dir.is_symlink() and not _files(grab_dir):
            shutil.rmtree(grab_dir, ignore_errors=True)


async def start_import(source: str, library: MediaLibrary) -> None:
    """De import van fase 5 (verplaatsen). Tests vervangen deze functie."""
    from app.media import importer

    await importer.start(source, library.root_id, library.owner_id, move=True)


async def leftovers(session: AsyncSession, settings: Settings) -> None:
    """Wat de import in de staging liet staan, opnieuw importeren (hooguit één per ronde)."""
    root = staging_dir(settings)
    if not root.is_dir():
        return
    now = _now()
    for grab_dir in sorted(root.iterdir()):
        try:
            grab_id = uuid.UUID(grab_dir.name)
        except ValueError:
            continue
        grab = await session.get(ArrGrab, grab_id)
        if grab is None or grab.state != "imported":
            continue
        done = _aware(grab.done_at) or now
        tried = _aware(grab.updated_at) or done
        if now - done > LEFTOVER_GIVE_UP or now - tried < LEFTOVER_RETRY:
            continue
        if not await asyncio.to_thread(_files, grab_dir):
            continue
        wanted = await session.get(ArrWanted, grab.wanted_id)
        library = await session.get(MediaLibrary, wanted.library_id) if wanted else None
        if library is None:
            continue
        try:
            await start_import(f"arr/{grab.id}", library)
        except HTTPException:
            return  # er loopt een import (of hij kan nu niet): volgende ronde
        grab.updated_at = now
        await session.commit()
        log.info("arr: rest van %s opnieuw naar de import", grab.release)
        return


async def _fail(session: AsyncSession, grab: ArrGrab, reason: str) -> None:
    grab.state = "failed"
    grab.reason = reason[:200]
    grab.updated_at = grab.done_at = _now()
    wanted = await session.get(ArrWanted, grab.wanted_id)
    if wanted is not None:
        wanted.searched_at = None  # meteen opnieuw zoeken
    await session.commit()
    log.info("arr: %s mislukt: %s", grab.release, reason)


async def follow(session: AsyncSession, settings: Settings, links: Links, q: qbit.QBit) -> None:
    """Stap 1 en 2 van een ronde: de lopende grabs bijhouden en wat klaar is importeren."""
    grabs = list(await session.scalars(select(ArrGrab).where(ArrGrab.state.in_(ACTIVE))))
    if not grabs:
        return
    torrents = {
        tag: t
        for t in await q.torrents(category=links.qbit.category)
        for tag in t.tags
        if tag.startswith("platform-")
    }
    now = _now()
    for grab in grabs:
        wanted = await session.get(ArrWanted, grab.wanted_id)
        torrent = torrents.get(tag_of(grab.id))
        if grab.state == "importing":
            await _import(session, settings, grab, wanted)
            continue
        if torrent is None:
            if now - _aware(grab.created_at) > timedelta(minutes=10):
                await _fail(session, grab, "niet meer in qBittorrent")
            continue
        if torrent.progress > grab.progress:
            grab.progress = torrent.progress
            grab.updated_at = now
        if torrent.stage == "fout":
            await q.delete([torrent.hash], files=True)
            await _fail(session, grab, "qBittorrent meldt een fout")
            continue
        if torrent.stage == "klaar" or torrent.progress >= 1.0:
            done = torrent.completion_on
            if done and now - datetime.fromtimestamp(done, UTC) < timedelta(seconds=SETTLE_S):
                await session.commit()  # de import neemt alleen bestanden van minstens 60 s oud
                continue
            source = local_path(settings, links.qbit, torrent.content_path)
            staged = (
                stage(settings, grab, wanted, source)
                if source is not None and wanted is not None and source.exists()
                else None
            )
            # De torrent weg zonder zijn bestanden; de app haalt de namen in de downloadmap zelf
            # weg, zodat elk bestand in de staging precies één naam heeft (de import verplaatst
            # alleen een bestand met één naam)
            await q.delete([torrent.hash], files=False)
            if source is not None:
                await asyncio.to_thread(_remove_download, settings, source)
            if staged is None:
                await _fail(session, grab, "geen video in de download (ingepakt?)")
                continue
            grab.state = "importing"
            grab.reason = str(staged)
            grab.updated_at = now
            await session.commit()
            await _import(session, settings, grab, wanted)
            continue
        last = max(torrent.last_activity, torrent.added_on)
        quiet = now - datetime.fromtimestamp(last, UTC) if last else timedelta(0)
        if torrent.stage in ("vastgelopen", "metadata") and quiet > timedelta(hours=STALL_HOURS):
            await q.delete([torrent.hash], files=True)
            await _fail(session, grab, f"vastgelopen ({STALL_HOURS} uur niets)")
            continue
        await session.commit()


async def _import(
    session: AsyncSession, settings: Settings, grab: ArrGrab, wanted: ArrWanted | None
) -> None:
    library = await session.get(MediaLibrary, wanted.library_id) if wanted is not None else None
    if library is None:
        await _fail(session, grab, "de bibliotheek is weg")
        return
    try:
        await start_import(grab.reason or "", library)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("code") == "media_import_busy":
            return  # er loopt al een import: volgende ronde
        await _fail(session, grab, f"import: {detail.get('code', exc.status_code)}")
        return
    grab.state = "imported"
    grab.reason = None
    grab.progress = 1.0
    grab.updated_at = grab.done_at = _now()
    await session.commit()
    log.info("arr: %s geïmporteerd in %s", grab.release, library.name)


def due(wanted: ArrWanted, active: Iterable[uuid.UUID], now: datetime) -> bool:
    if not wanted.monitored or wanted.id in set(active):
        return False
    searched = _aware(wanted.searched_at)
    return searched is None or now - searched >= timedelta(hours=SEARCH_HOURS)


def http_client() -> httpx.AsyncClient:
    return arr_settings.http_client()


async def tick(settings: Settings) -> None:
    maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
    async with maker() as session, http_client() as client:
        links = await _links(session, settings)
        if links is None:
            return
        q = qbit.QBit(client, links.qbit.url, links.qbit.username, links.qbit.password)
        try:
            await follow(session, settings, links, q)
        except qbit.QBitError as exc:
            log.warning("arr: qBittorrent: %s", exc)
            return
        await leftovers(session, settings)
        await asyncio.to_thread(clean_staging, settings)
        now = _now()
        busy_rows = await session.scalars(
            select(ArrGrab.wanted_id).where(ArrGrab.state != "failed")
        )
        busy = set(busy_rows)
        for wanted in list(await session.scalars(select(ArrWanted))):
            # Een film met een grab (lopend of klaar) is klaar; een serie zoekt verder per seizoen
            active = busy if wanted.kind == "movie" else set()
            if not due(wanted, active, now):
                continue
            try:
                await search(session, client, links, q, wanted, settings)
            except (prowlarr.ProwlarrError, qbit.QBitError) as exc:
                log.warning("arr: zoeken naar %s: %s", wanted.title, exc)
                return


async def loop(settings: Settings) -> None:
    while True:
        try:
            await tick(settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - de volgende ronde probeert het opnieuw
            log.exception("arr: een ronde mislukte")
        try:
            await subs.tick(settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            log.exception("arr: een ronde ondertitels mislukte")
        await asyncio.sleep(INTERVAL_S)
