"""Het tabblad Media op /beheer (M10, Keuze 45): wat GET /api/v1/admin/media laat zien, en wat
de knoppen doen. De router (routers/admin_media.py) is dun; alles staat hier.

- **Het overzicht** komt uit wat de andere pakketten al bijhouden (§3.3): de staat van de worker
  (MediaState en de caps), admission.snapshot() (M4), sessions.list() (M3), cache.sizes() (M4),
  providers.status() (M8), importer.current() (M2) en de wachtrij (jobs). Eén korte
  databasesessie; nooit wachten op de worker of een stroom (regel 8): de caps zijn die van de
  laatste gezondheidscontrole.
- **De redenen per dag** (Keuze 19) telt dit bestand zelf, in het geheugen: bij elke nieuwe
  sessie (behalve die van de belastingtest), de laatste 7 dagen. Na een herstart beginnen ze
  opnieuw (geen tabel, Keuze 50). install() hangt het tellen om sessions.create (M3) heen,
  zonder dat bestand te veranderen.
- **De instellingen** (MediaConfig): elk veld los (MediaConfigIn), met de grenzen van Keuze 45.
  max_transcodes kan niet boven PLATFORM_MEDIA_MAX_TRANSCODES: dan 422 invalid_input met het
  plafond.
- **Stop** sluit een sessie met de grafsteen "stopped" (403 media_access_gone voor de speler, die
  het netjes meldt en geen nieuwe sessie vraagt).
- **Opruimen** (cache/clear) breekt geen lopende sessie: bij sessions blijft van elke stroom het
  stuk rond de speler staan (cache.trim houdt 60 s achter de speler), bij audio blijft wat nu
  gemaakt wordt en wat een open sessie speelt (stream_audio.trim met keep), en bij trick gaan
  alleen de sprites weg (play geeft ze dan niet meer, tot de analyse ze opnieuw maakt: trick.info
  kijkt of trick.vtt er staat).
- **De meting** (bench): de opdracht bench in de worker, op de achtergrond; de uitkomst gaat in
  MediaConfig.bench. Weigert (409 media_bench_busy) zolang er een transcode of de belastingtest
  loopt.
- **TMDB** (Keuze 40): de sleutel bewaren, weghalen en testen doet M8 (providers/secrets.py).
  Dit bestand roept die aan via tmdb_hooks(); zolang M8 ze niet levert, wordt er niets bewaard
  (503 media_provider_unavailable). De sleutel komt nooit terug in een antwoord of een log.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import shutil
import uuid
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import access, errors, jobs
from app.config import BenchResult, MediaConfig, Settings
from app.media import errors as media_errors
from app.media.state import MediaPolicy, MediaState, media_policy, media_state
from app.media.types import (
    AdminLibrary,
    AdminSessionView,
    BudgetUse,
    CacheParts,
    CacheView,
    FfmpegView,
    ImportView,
    JobCounts,
    LoadtestFile,
    MediaAdmin,
    MediaConfigIn,
    NoticeView,
    PlaceUse,
    ProviderView,
    ReasonDay,
    Selftest,
    UserRef,
    WorkerCaps,
    WorkerView,
)
from app.models import Job, MediaItem, MediaLibrary, MediaProbe, Node, User

__all__ = ["MediaConfig"]

log = logging.getLogger("platform.media")

REASON_DAYS = 7
LOW_CONFIDENCE = 0.5  # "Weinig zeker" (Keuze 7: zekerheid < 0,5)
MEDIA_JOBS = "media.%"  # de soorten van de achtergrondbaan van media
BENCH_ENCODE_S = 10
BENCH_DECODE_S = 4
BENCH_LIMIT_S = 300.0
# De plaatsen van de worker per soort (Keuze 12), zolang de caps er niet zijn
DEFAULT_PLACES = {"remux": 6, "background": 1, "short": 2}


def _today() -> date:
    return datetime.now(UTC).date()


# --- de redenen per dag (Keuze 19) ------------------------------------------------------------


class Reasons:
    """De redenen van de beslissingen per dag (UTC), in het geheugen, de laatste 7 dagen."""

    def __init__(self) -> None:
        self.days: dict[date, Counter[str]] = {}

    def count(self, found: list[str], day: date | None = None) -> None:
        day = day or _today()
        counts = self.days.setdefault(day, Counter())
        for reason in found:
            counts[reason] += 1
        oldest = day - timedelta(days=REASON_DAYS - 1)
        for old in [d for d in self.days if d < oldest]:
            del self.days[old]

    def view(self, today: date | None = None) -> list[ReasonDay]:
        today = today or _today()
        out = []
        for back in range(REASON_DAYS - 1, -1, -1):
            day = today - timedelta(days=back)
            counts = self.days.get(day)
            if counts:
                out.append(ReasonDay(day=day.isoformat(), counts=dict(counts)))  # type: ignore[arg-type]
        return out

    def reset(self) -> None:
        self.days.clear()


reasons = Reasons()


def counting(create: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """sessions.create met het tellen van de redenen erom heen (na een geslaagde create). Een
    sessie van de belastingtest telt niet mee: die zegt niets over de kijkers."""

    @functools.wraps(create)
    async def counted(user, source, probe, plan, *args, **kwargs):
        made = await create(user, source, probe, plan, *args, **kwargs)
        if not isinstance(source, LoadtestFile):
            with contextlib.suppress(Exception):  # tellen breekt afspelen nooit
                reasons.count(list(plan.reasons))
        return made

    counted._counted = True  # type: ignore[attr-defined]
    return counted


def install() -> None:
    """Het tellen om sessions.create (M3) heen; een tweede keer doet niets."""
    from app.media import sessions

    if not getattr(sessions.create, "_counted", False):
        sessions.create = counting(sessions.create)  # type: ignore[assignment]


# --- de worker en ffmpeg ------------------------------------------------------------------------


def selftest_of(state: MediaState) -> Selftest | None:
    """De uitkomst van de zelftest (Keuze 11) uit wat de app weet: de lokale worker heeft er
    geen (sandbox off); een worker die zich niet kon afschermen, weigert elke hello met
    "zelftest: …" (selftest.RefusingWorker), en dan is de staat down met die reden."""
    if state.worker == "down" and "zelftest" in state.reason:
        rest = state.reason.split("zelftest", 1)[1].lstrip(": ")
        problems = [p.strip() for p in rest.split(";") if p.strip()]
        return Selftest(ok=False, sandbox="on", problems=problems or [state.reason])
    caps = state.caps
    if caps is None:
        return None
    if caps.selftest is not None and caps.selftest.get("sandbox") in ("on", "off"):
        # wat de worker zelf zag (__main__, Keuze 11); een vreemde vorm valt terug op de caps
        with contextlib.suppress(ValueError):
            return Selftest.model_validate(caps.selftest)
    if caps.sandbox == "on":
        return Selftest(ok=True, sandbox="on")
    if caps.mode == "local" or caps.sandbox == "off":
        return Selftest(ok=True, sandbox="off")
    return None  # unchecked: een worker in een container zonder zelftest (oud, of een test)


def places_of(caps: WorkerCaps | None, max_transcodes: int) -> dict[str, PlaceUse]:
    limits = dict(DEFAULT_PLACES, transcode=max_transcodes)
    used: dict[str, int] = {}
    if caps is not None:
        limits.update({k: int(v) for k, v in caps.places.items() if k != "control"})
        used = {k: int(v) for k, v in caps.in_use.items()}
    return {k: PlaceUse(used=used.get(k, 0), max=v) for k, v in sorted(limits.items())}


def worker_view(state: MediaState, settings: Settings) -> WorkerView:
    caps = state.caps
    mode = caps.mode if caps is not None else state.mode
    return WorkerView(
        mode=mode,
        state=state.worker,
        since=state.since,
        selftest=selftest_of(state),
        places=places_of(caps, settings.media_max_transcodes),
        rss_kb=caps.rss_kb if caps is not None else 0,
        children=list(caps.children) if caps is not None else [],
    )


def ffmpeg_view(caps: WorkerCaps | None) -> FfmpegView | None:
    if caps is None:
        return None
    return FfmpegView(
        version=caps.ffmpeg,
        encoders=list(caps.encoders),
        filters=list(caps.filters),
        missing=list(caps.missing),
        hwaccel=list(caps.hwaccels),
        gpu="/dev/dri" if caps.dri else None,
    )


def gpu_text(caps: WorkerCaps | None) -> str:
    """Voor media-status: "geen", of /dev/dri met de hwaccels die ffmpeg kent (een GPU is nooit
    een eis). Zonder /dev/dri zeggen de hwaccels niets: dat is wat ffmpeg kan, niet wat er is."""
    if caps is None or not caps.dri:
        return "geen"
    return "/dev/dri" + (f" ({', '.join(caps.hwaccels)})" if caps.hwaccels else "")


# --- budget, sessies, wachtrij, bibliotheken, cache, providers, import -------------------------


def budget_use() -> BudgetUse:
    from app.media import admission

    snap = admission.snapshot()
    return BudgetUse(used=snap.in_use, total=snap.budget, ds_starting=admission._office_starting())


def stream_numbers(st: Any) -> tuple[float | None, float | None]:
    """(snelheid × realtime van de run die nu loopt, voorsprong op de speler in s)."""
    if st is None:
        return None, None
    from app.media import producer as producers

    speed = ahead = None
    run = getattr(st, "producer", None)
    try:
        if run is not None and run.alive:
            wall = producers.clock() - run.started_at
            if wall > 0.5:
                made = max(0, run.produced_ms - st.layout.begin(run.start))
                speed = round(made / 1000 / wall, 2)
            ahead = round(max(0, run.produced_ms - st.playhead_ms()) / 1000, 1)
    except Exception:  # alleen ter informatie
        return None, None
    return speed, ahead


async def session_views(session: AsyncSession) -> list[AdminSessionView]:
    """De open sessies (de oudste eerst) met wie, wat, de snelheid en de voorsprong."""
    from app.media import sessions, streams

    views = sessions.list()
    if not views:
        return []
    user_ids = {v.user_id for v in views}
    node_ids = {v.node_id for v in views if v.node_id is not None}
    rows = await session.execute(select(User.id, User.display_name).where(User.id.in_(user_ids)))
    names = dict(rows.all())
    titles: dict[uuid.UUID, str] = {}
    if node_ids:
        found = await session.execute(
            select(Node.id, Node.name, MediaItem.title)
            .outerjoin(MediaItem, MediaItem.node_id == Node.id)
            .where(Node.id.in_(node_ids))
        )
        titles = {nid: title or name for nid, name, title in found}
    table = sessions.table()
    out = []
    for v in views:
        speed = ahead = None
        title = titles.get(v.node_id) if v.node_id is not None else None
        s = table.by_id(v.id)
        if s is not None:
            speed, ahead = stream_numbers(streams.get(s))
            if s.loadtest:
                title = "belastingtest"
        name = names.get(v.user_id)
        out.append(
            AdminSessionView(
                **v.model_dump(),
                user=UserRef(id=v.user_id, display_name=name) if name is not None else None,
                title=title,
                speed=speed,
                ahead_s=ahead,
            )
        )
    return out


async def job_counts(session: AsyncSession) -> JobCounts:
    rows = await session.execute(
        select(Job.status, func.count()).where(Job.type.like(MEDIA_JOBS)).group_by(Job.status)
    )
    found = dict(rows.all())
    return JobCounts(
        queued=found.get("queued", 0),
        running=found.get("running", 0),
        failed=found.get("failed", 0),
    )


async def retry_failed(session: AsyncSession) -> int:
    """De mislukte taken van media opnieuw, met nieuwe pogingen (zoals /beheer → Taken)."""
    result = await session.execute(
        update(Job)
        .where(Job.status == "failed", Job.type.like(MEDIA_JOBS))
        .values(**jobs._retry_values())
        .execution_options(synchronize_session=False)
    )
    count = result.rowcount or 0
    await session.commit()
    if count:
        jobs.kick()
    return count


async def library_views(session: AsyncSession) -> list[AdminLibrary]:
    """Alle bibliotheken met eigenaar, aantal, fouten (de probe faalde) en "Weinig zeker"."""
    from app.media import library

    libs = list((await session.scalars(select(MediaLibrary).order_by(MediaLibrary.name))).all())
    if not libs:
        return []
    ids = [lib.id for lib in libs]
    counts = await library.counts(session, ids)
    present = (
        MediaItem.library_id.in_(ids),
        MediaItem.present.is_(True),
        MediaItem.kind.in_(library.COUNTED),
    )
    low = dict(
        (
            await session.execute(
                select(MediaItem.library_id, func.count())
                .where(*present, MediaItem.confidence < LOW_CONFIDENCE)
                .group_by(MediaItem.library_id)
            )
        ).all()
    )
    failed = dict(
        (
            await session.execute(
                select(MediaItem.library_id, func.count())
                .join(Node, Node.id == MediaItem.node_id)
                .join(MediaProbe, MediaProbe.blob_sha == Node.blob_id)
                .where(*present, MediaProbe.state == "failed")
                .group_by(MediaItem.library_id)
            )
        ).all()
    )
    owner_ids = {lib.owner_id for lib in libs}
    owners = dict(
        (
            await session.execute(select(User.id, User.display_name).where(User.id.in_(owner_ids)))
        ).all()
    )
    outer = await inside(session, libs)
    out = []
    for lib in libs:
        base = library.view(library.Seen(lib, "owner"), counts.get(lib.id, 0))
        out.append(
            AdminLibrary(
                **base.model_dump(),
                owner=UserRef(id=lib.owner_id, display_name=owners.get(lib.owner_id, "?")),
                errors=failed.get(lib.id, 0),
                low_confidence=low.get(lib.id, 0),
                inside=outer.get(lib.id),
            )
        )
    return out


async def inside(session: AsyncSession, libs: list[MediaLibrary]) -> dict[uuid.UUID, str]:
    """Per bibliotheek de naam van een andere waar haar map in ligt (m2.md C2): het maken weigert
    nesting, maar een map verschuiven kan daarna nog (de bestandsroutes zijn van M0). De binnenste
    wint al (de scan van de buitenste slaat haar over); /beheer waarschuwt alleen. Eén query voor
    alle bibliotheken: van elke map omhoog, de eerste andere bibliotheek op de weg."""
    roots = {lib.root_id: lib for lib in libs}
    up = access.ancestry(Node.id.in_(list(roots)))
    rows = (
        await session.execute(
            select(up.c.origin, up.c.depth, MediaLibrary.name)
            .join(MediaLibrary, MediaLibrary.root_id == up.c.id)
            .where(up.c.depth > 0)
            .order_by(up.c.origin, up.c.depth)
        )
    ).all()
    found: dict[uuid.UUID, str] = {}
    for origin, _depth, name in rows:
        lib = roots.get(origin)
        if lib is not None and lib.id not in found:
            found[lib.id] = name  # de dichtstbijzijnde
    return found


async def cache_view(settings: Settings) -> CacheView:
    from app.media import admission, cache

    def measure() -> tuple[dict[str, int], int]:
        base = settings.media_dir
        sizes = {k: cache.dir_size(base / k) for k in ("audio", "trick", "art", "subs")}
        return sizes, admission.disk_free(settings.data_dir)

    from app.media import streams

    sizes, free = await asyncio.to_thread(measure)
    parts = CacheParts(
        sessions=streams.cached_bytes(),
        subs=sizes["subs"],
        trick=sizes["trick"],
        art=sizes["art"],
        audio=sizes["audio"],
    )
    return CacheView(parts=parts, free_bytes=free)


def provider_views(tmdb: dict[str, Any] | None = None) -> list[ProviderView]:
    """providers.status() (M8) als ProviderView; tmdb is tmdb_status() (de sleutel staat er, en
    werkt hij nog). De stub van M0 zegt alleen configured."""
    from app.media import providers

    try:
        found = providers.status()
    except Exception:
        log.exception("de staat van de providers")
        found = {}
    out = []
    for name in providers.PROVIDERS:
        s = found.get(name) or {}
        breaker = s.get("breaker", "closed")
        key_set = bool(s.get("key_set", s.get("configured", False)))
        last_error = s.get("last_error")
        if name == "tmdb" and tmdb:
            key_set = key_set or tmdb.get("set") is True
            if tmdb.get("ok") is False and not last_error:
                last_error = "auth"
        out.append(
            ProviderView(
                name=name,  # type: ignore[arg-type]
                key_set=key_set,
                last_ok=s.get("last_ok"),
                last_error=last_error,
                breaker=breaker if breaker in ("closed", "open", "half_open") else "closed",
            )
        )
    return out


async def import_view(session: AsyncSession) -> ImportView | None:
    from app.media import importer

    return await importer.current(session)


# --- het overzicht -------------------------------------------------------------------------------


async def overview(app, session: AsyncSession, settings: Settings) -> MediaAdmin:
    """GET /api/v1/admin/media. Ook als media op /beheer uit staat (MediaConfig.enabled false):
    daar zet de beheerder het weer aan."""
    from app.media import monitor

    state = media_state(app)
    config = await media_policy(app).get(session)
    notices = [
        NoticeView(code=n.code, params=dict(n.params), text=n.text)
        for n in await monitor.notices(app, session)
    ]
    out = MediaAdmin(
        worker=worker_view(state, settings),
        ffmpeg=ffmpeg_view(state.caps),
        bench=config.bench.model_dump(mode="json") if config.bench is not None else None,
        budget=budget_use(),
        sessions=await session_views(session),
        reasons=reasons.view(),
        jobs=await job_counts(session),
        libraries=await library_views(session),
        import_=await import_view(session),
        config=MediaPolicy.effective(config, settings).model_dump(mode="json"),
        notices=notices,
        native_reloads=native_reloads(),
    )
    tmdb = await tmdb_status(session, settings)
    await session.close()  # klaar met de database vóór het tellen van de cache (regel 8)
    out.cache = await cache_view(settings)
    out.providers = provider_views(tmdb)
    return out


async def rescan(session: AsyncSession, library_id: uuid.UUID) -> bool:
    """Opnieuw scannen vanaf /beheer (Keuze 45): een volledige ronde, net als de knop van M2,
    maar ook voor de bibliotheek van een ander (de beheerder ziet ze hier allemaal). False als
    de bibliotheek er niet is."""
    from app.media import scanner

    lib = await session.get(MediaLibrary, library_id)
    if lib is None:
        return False
    await scanner.queue_full(session, lib.id)
    await session.commit()
    scanner.activate()
    log.info("opnieuw scannen vanaf /beheer: bibliotheek %s", lib.id)
    return True


def native_reloads() -> int:
    from app.media import streams

    return int(streams.native_reloads)


# --- de instellingen -----------------------------------------------------------------------------


async def save_config(
    app, session: AsyncSession, settings: Settings, body: MediaConfigIn
) -> MediaConfig:
    """PUT /config: alleen de velden die er staan. max_transcodes boven het plafond uit env:
    422 invalid_input (niet stil afgekapt: wat er staat, is wat geldt)."""
    ceiling = max(0, settings.media_max_transcodes)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if changes.get("max_transcodes", 0) > ceiling:
        raise errors.fail(
            422,
            "invalid_input",
            "Hooguit {max} omzettingen tegelijk (PLATFORM_MEDIA_MAX_TRANSCODES)",
            field="max_transcodes",
            max=ceiling,
        )
    policy = media_policy(app)
    current = await policy.get(session)
    new = MediaConfig.model_validate({**current.model_dump(), **changes})
    await policy.save(session, new)
    log.info("instellingen van media gewijzigd: %s", ", ".join(sorted(changes)) or "niets")
    return MediaPolicy.effective(new, settings)


# --- stop, opruimen ------------------------------------------------------------------------------


def stop_session(session_id: uuid.UUID) -> bool:
    from app.media import sessions

    stopped = sessions.stop(session_id, "stopped")
    if stopped:
        log.info("mediasessie %s gestopt op /beheer", session_id)
    return stopped


def _clear_dir(base: Path) -> int:
    """Alles onder base weg (zonder symlinks te volgen); base zelf blijft."""
    removed = 0
    try:
        entries = list(os.scandir(base))
    except OSError:
        return 0
    for entry in entries:
        path = base / entry.name
        if entry.is_dir(follow_symlinks=False):
            shutil.rmtree(path, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                path.unlink()
        removed += 1
    return removed


def audio_in_use() -> set[str]:
    """De bestanden in audio/ van de open sessies van muziek: die blijven bij opruimen staan
    (een speler die er een Range van vraagt, krijgt anders een nieuw, groeiend bestand)."""
    from app.media import sessions
    from app.routers import stream_audio

    keep: set[str] = set()
    for s in sessions.table().open():
        if s.plan.mode != "audio" or not s.sha:
            continue
        with contextlib.suppress(Exception):
            position = stream_audio._position(s)
            keep.add(f"{s.sha}.mp4" if position == 0 else f"{s.sha}.{position}.mp4")
    return keep


async def clear_cache(settings: Settings, what: str) -> int:
    """POST /cache/clear. Geeft hoeveel er weg ging (stromen, bestanden of mappen)."""
    if what == "sessions":
        from app.media import streams

        count = 0
        for st in streams.all():
            if st.closed:
                continue
            before = st.bytes
            await st.trim(0)  # houdt 60 s achter de speler en wat nu gevraagd wordt
            count += before != st.bytes
        return count
    if what == "audio":
        from app.routers import stream_audio

        keep = audio_in_use()
        return await asyncio.to_thread(stream_audio.trim, settings.media_dir / "audio", 0, keep)
    return await asyncio.to_thread(_clear_dir, settings.media_dir / "trick")


# --- de meting (bench) ---------------------------------------------------------------------------


@dataclass
class Task:
    """Een taak op de achtergrond van /beheer (de meting); één tegelijk."""

    id: uuid.UUID
    started_at: datetime
    task: asyncio.Task | None = None
    result: dict[str, Any] = field(default_factory=dict)
    error: str | None = None

    @property
    def running(self) -> bool:
        return self.task is not None and not self.task.done()


_bench: Task | None = None


def transcodes_running() -> int:
    """Echte omzettingen van kijkers die nu lopen (niet die van de belastingtest)."""
    from app.media import streams

    return sum(
        1
        for st in streams.all()
        if st.plan.mode == "transcode" and not st.closed and not st.session.loadtest
    )


def bench_running() -> bool:
    return _bench is not None and _bench.running


async def _bench_run(app, settings: Settings, job: Task) -> None:
    from app.db import get_engine
    from app.media import client
    from app.media.wire import Op
    from app.media.worker.args import BenchArgs

    try:
        async with asyncio.timeout(BENCH_LIMIT_S):
            call, _ = await client.call(
                Op.BENCH, BenchArgs(encode_s=BENCH_ENCODE_S, decode_s=BENCH_DECODE_S)
            )
            async with call:
                data = await call.result()
        result = BenchResult(f=data["f"], f_dec=data["f_dec"], at=datetime.now(UTC))
        maker = async_sessionmaker(get_engine(settings), expire_on_commit=False)
        async with maker() as session:
            policy = media_policy(app)
            current = await policy.get(session)
            await policy.save(session, current.model_copy(update={"bench": result}))
        media_state(app).bench_factor = result.f
        job.result = result.model_dump(mode="json")
        log.info("de meting van media: f %.3f, f_dec %.3f", result.f, result.f_dec)
    except Exception as exc:  # de meting faalt netjes; /beheer houdt de vorige
        job.error = exc.__class__.__name__
        log.warning("de meting van media mislukte: %s", exc.__class__.__name__)


def start_bench(app, settings: Settings) -> uuid.UUID:
    """POST /bench: 202 met het id, of 409 media_bench_busy (een transcode, de belastingtest of
    al een meting)."""
    global _bench
    from app.media import loadtest

    if bench_running() or transcodes_running() or loadtest.running():
        raise media_errors.bench_busy()
    job = Task(id=uuid.uuid4(), started_at=datetime.now(UTC))
    job.task = asyncio.get_running_loop().create_task(
        _bench_run(app, settings, job), name="media-bench"
    )
    _bench = job
    return job.id


# --- TMDB (Keuze 40; M8 bewaart en test) ---------------------------------------------------------


@dataclass
class TmdbHooks:
    """Wat M8 levert in providers/secrets.py: bewaren (pas na een geslaagde test), weghalen, de
    bewaarde sleutel testen, en de staat ({"set", "set_at", "ok"}, nooit de sleutel). Een fout
    is ProviderAuth (422 media_provider_auth), ProviderUnavailable (503
    media_provider_unavailable) of ProviderRate (503 media_provider_rate)."""

    store: Callable[[AsyncSession, Settings, str], Awaitable[None]]
    clear: Callable[[AsyncSession], Awaitable[None]]
    test: Callable[[AsyncSession, Settings], Awaitable[None]]
    status: Callable[[AsyncSession, Settings], Awaitable[dict[str, Any]]]


TMDB_HOOKS = ("save_tmdb", "forget_tmdb", "verify_tmdb", "status")


def tmdb_hooks() -> TmdbHooks | None:
    """De functies van M8 (TMDB_HOOKS in providers/secrets.py), of None zolang ze er niet zijn."""
    from app.media.providers import secrets

    found = [getattr(secrets, name, None) for name in TMDB_HOOKS]
    if not all(callable(f) for f in found):
        return None
    return TmdbHooks(*found)  # type: ignore[arg-type]


def _hooks() -> TmdbHooks:
    hooks = tmdb_hooks()
    if hooks is None:
        raise media_errors.provider_unavailable()
    return hooks


@contextlib.contextmanager
def _provider_errors():
    """De fouten van M8 als API-fout uit de catalogus; de tekst van de fout gaat nooit mee (daar
    kan niets van de sleutel in staan, maar zeker is zeker)."""
    from app.media.providers.base import ProviderAuth, ProviderRate, ProviderUnavailable

    try:
        yield
    except ProviderAuth:
        raise media_errors.provider_auth() from None
    except ProviderRate as exc:
        raise media_errors.provider_rate(exc.retry_after) from None
    except ProviderUnavailable:
        raise media_errors.provider_unavailable() from None


async def tmdb_store(session: AsyncSession, settings: Settings, key: str) -> None:
    with _provider_errors():
        await _hooks().store(session, settings, key)
    log.info("de sleutel van TMDB is bewaard")  # nooit de sleutel zelf


async def tmdb_clear(session: AsyncSession, settings: Settings) -> None:
    with _provider_errors():
        await _hooks().clear(session)
    log.info("de sleutel van TMDB is weggehaald")


async def tmdb_test(session: AsyncSession, settings: Settings) -> None:
    with _provider_errors():
        await _hooks().test(session, settings)


async def tmdb_status(session: AsyncSession, settings: Settings) -> dict[str, Any]:
    """{"set", "ok"} van M8, of {} zonder M8 of bij een fout (alleen ter informatie)."""
    hooks = tmdb_hooks()
    if hooks is None:
        return {}
    try:
        found = await hooks.status(session, settings)
    except Exception as exc:
        log.warning("de staat van de sleutel van TMDB: %s", exc.__class__.__name__)
        return {}
    return {"set": found.get("set") is True, "ok": found.get("ok")}
