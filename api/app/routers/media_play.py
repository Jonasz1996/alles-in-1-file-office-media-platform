"""Afspelen starten (M3, Keuze 17, 19–21, 29).

GET  /api/v1/media/{node_id}/codecs   → CodecsOut {candidates, item}
POST /api/v1/media/{node_id}/play     PlayIn → 200 PlayOut
     → 404 not_found | 409 media_probe_pending {reason?: keyframes} | 409 media_unavailable
       | 415 media_unsupported {reason?: too_large} | 422 media_hdr {reason} | 503 media_busy
       {reason} | 503 media_worker_down | 503 media_ffmpeg_missing | 503 media_off

De weg van play (ook voor een publieke link, routers/public_media.py):
1. de toegang (lezen; de eigenaar niet uit zijn prullenbak), de instellingen, de losse
   ondertitels (gefilterd op toegang), en dan de databaseverbinding terug in de pool: het
   wachten op de worker gebeurt zonder (regel 8);
2. de probe uit media_probe, of nu via de worker (gedeeld per sha), binnen 5 s; daarna in
   hetzelfde budget de keyframe-index (keyframes.read_now, Keuze 17);
3. het profiel (basisprofiel plus metingen), de policy, wat er nu past (admission.view), en
   decide. De router kijkt zelf of de worker er is (remux, transcode, audio) en of de
   tone-mapping kan (422 media_hdr no_tonemap);
4. de toelating (admission.reserve), de sessie (sessions.create), en bij remux en transcode de
   stroom (streams.open, M4). Faalt een stap, dan gaat wat er al was weer dicht.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import access, errors
from app.config import MediaConfig, Settings, get_settings
from app.db import Session, get_engine
from app.media import (
    admission,
    analysis,
    chapters,
    client,
    decide,
    keyframes,
    runtime,
    sessions,
    streams,
    subtitles,
    trick,
)
from app.media import errors as media_errors
from app.media import profile as profiles
from app.media import public as media_public
from app.media.state import MediaPolicy, MediaState, media_policy, media_state
from app.media.types import (
    AudioTrack,
    CodecsOut,
    Gapless,
    Plan,
    PlayIn,
    PlayOut,
    PlayTracks,
    PlayUrls,
    Probe,
    QualityInfo,
    StreamInfo,
    SubtitleTrack,
    TrickInfo,
)
from app.models import MediaItem, Node, User
from app.routers import files
from app.security import CurrentUser, TokenFamily

log = logging.getLogger("platform.media")

router = APIRouter(prefix="/api/v1/media", tags=["media"])

NOT_FOUND = {"message": "Niet gevonden", "code": "not_found"}
PROBE_BUDGET = 5.0  # de probe en de keyframe-index samen (Keuze 17)
PROBE_RETRY = 2  # Retry-After als de probe niet op tijd klaar is
LADDER = (1080, 720, 480)
TONEMAP_FILTERS = ("zscale", "tonemap")
WORKER_MODES = ("remux", "transcode", "audio")

# Eén probe per sha tegelijk, gedeeld door wie er tegelijk om vraagt
_probing: dict[str, asyncio.Task] = {}

Inside = Callable[[list[uuid.UUID]], Awaitable[Any]]


@dataclass
class Viewer:
    """Wie kijkt: een ingelogde gebruiker (met zijn login), of een publieke link (de eigenaar
    van de link als gebruiker, zonder login)."""

    user: User
    family: uuid.UUID | None = None
    link_id: uuid.UUID | None = None
    link_print: str | None = None

    @property
    def public(self) -> bool:
        return self.link_id is not None


@dataclass
class Found:
    """Wat play uit de database haalt voordat de verbinding teruggaat."""

    node: Node
    config: MediaConfig
    library_id: uuid.UUID | None
    subs: list[dict[str, Any]]


def maker(settings: Settings) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(get_engine(settings), expire_on_commit=False)


def not_found() -> HTTPException:
    return errors.fail(404, **NOT_FOUND)


# --- de toegang ---------------------------------------------------------------------------------


async def readable_file(session: AsyncSession, user: User, node_id: uuid.UUID) -> Node:
    """Een bestand dat deze gebruiker mag lezen; de eigenaar niet uit zijn prullenbak (dat ziet
    hij wel in de boom, maar afspelen hoort daar niet bij). Anders 404."""
    found = await files.get_tree_access(session, user, node_id, "read")
    node = found.node
    if node.kind != "file" or not node.blob_id:
        raise not_found()
    if found.owner and await access.trashed(session, node.id):
        raise not_found()
    return node


async def allowed_subs(
    session: AsyncSession, viewer: Viewer, raw: list | None, inside: Inside | None = None
) -> list[dict[str, Any]]:
    """De losse ondertitels van het item die deze kijker mag lezen (Keuze 29): bij een
    ingelogde gebruiker zijn eigen bestanden (niet in de prullenbak) en wat access.resolve
    geeft; bij een publieke link alleen wat in de link ligt (inside). De volgorde blijft: x<i>
    is plek i in deze lijst."""
    wanted: list[tuple[uuid.UUID, dict]] = []
    for entry in raw or []:
        if not isinstance(entry, dict):
            continue
        try:
            wanted.append((uuid.UUID(str(entry.get("node"))), entry))
        except ValueError:
            continue
    if not wanted:
        return []
    ids = list(dict.fromkeys(node_id for node_id, _ in wanted))
    nodes = {
        n.id: n
        for n in (await session.scalars(select(Node).where(Node.id.in_(ids)))).all()
        if n.kind == "file" and n.blob_id
    }
    if inside is not None:
        ok = set(await inside(list(nodes))) if nodes else set()
    else:
        own = [i for i, n in nodes.items() if n.owner_id == viewer.user.id]
        gone = await files.trashed_among(session, own) if own else set()
        ok = {i for i in own if i not in gone}
        other = [i for i, n in nodes.items() if n.owner_id != viewer.user.id]
        if other:
            ok |= set(await access.resolve(session, viewer.user.id, other))
    out = []
    for node_id, entry in wanted:
        node = nodes.get(node_id)
        if node is None or node_id not in ok:
            continue
        lang = entry.get("lang")
        out.append(
            {
                "node": str(node_id),
                "sha": node.blob_id,
                "lang": lang if isinstance(lang, str) else None,
                "forced": bool(entry.get("forced")),
                "sdh": bool(entry.get("sdh")),
                "default": bool(entry.get("default")),
            }
        )
    return out


async def gather(
    request: Request,
    session: AsyncSession,
    settings: Settings,
    viewer: Viewer,
    node: Node,
    inside: Inside | None = None,
) -> Found:
    config = MediaPolicy.effective(await media_policy(request.app).get(session), settings)
    item = await session.get(MediaItem, node.id)
    subs = await allowed_subs(session, viewer, item.subs if item else [], inside)
    return Found(node, config, item.library_id if item else None, subs)


# --- de probe en de keyframe-index ----------------------------------------------------------------


async def _probe_and_save(settings: Settings, sha: str) -> Probe:
    probe = await analysis.probe_blob(settings, sha)
    await analysis.save_probe(maker(settings), sha, probe)
    return probe


def _forget(sha: str, task: asyncio.Task) -> None:
    if _probing.get(sha) is task:
        del _probing[sha]
    if not task.cancelled():
        task.exception()  # gezien: geen "exception was never retrieved"


async def probe_now(settings: Settings, sha: str, deadline: float) -> Probe:
    """De probe nu via de worker (één tegelijk per sha), binnen het budget. Niet op tijd: 409
    media_probe_pending (Retry-After: 2); de probe loopt door en staat er bij de volgende
    poging."""
    task = _probing.get(sha)
    if task is None or task.done():
        task = asyncio.ensure_future(_probe_and_save(settings, sha))
        _probing[sha] = task
        task.add_done_callback(lambda t, sha=sha: _forget(sha, t))
    remaining = deadline - asyncio.get_running_loop().time()
    try:
        return await asyncio.wait_for(asyncio.shield(task), max(0.0, remaining))
    except TimeoutError:
        raise media_errors.probe_pending("probe", PROBE_RETRY) from None
    except (client.WorkerBusy, client.WorkerDown, client.WorkerFailed) as exc:
        raise media_errors.from_worker(exc) from None


def check_probe(probe: Probe) -> None:
    """Een probe die het bestand afwees: 415 te groot, anders 409 media_unavailable."""
    if probe.state == "ready":
        return
    if probe.error == "too_large":
        raise media_errors.unsupported(["too_large"])
    raise media_errors.unavailable("probe")


async def index_now(settings: Settings, probe: Probe, sha: str, deadline: float) -> Probe:
    """De keyframe-index meteen na de probe, in hetzelfde budget (Keuze 17), voor elke video
    zonder index. Lukt het niet (op tijd), dan blijft het none: decide kiest dan transcode
    (keyframes_missing), en de analyse maakt de index later (de terugval)."""
    if probe.video() is None or probe.keyframes_state != "none":
        return probe
    remaining = deadline - asyncio.get_running_loop().time()
    if remaining <= 0:
        return probe
    try:
        state = await asyncio.wait_for(keyframes.read_now(sha), remaining)
    except Exception as exc:  # de index is een verbetering: afspelen gaat door
        log.info("keyframe-index bij play niet gelukt: %s", exc.__class__.__name__)
        return probe
    if state == "none":
        return probe
    fresh = await analysis.load(maker(settings), sha)
    return fresh if fresh is not None else probe


async def queue_analysis(
    settings: Settings, sha: str, library_id: uuid.UUID | None, probe: Probe, probed: bool
) -> None:
    """De rest van de analyse in de rij: na een probe die play zelf deed, of als de
    keyframe-index ontbreekt en er een stap keyframes is (M4, met de terugval)."""
    steps = {s.name for s in analysis.steps()}
    missing = probe.video() is not None and probe.keyframes_state in ("none", "sparse")
    if not (probed or (missing and "keyframes" in steps)):
        return
    try:
        async with maker(settings)() as session:
            await analysis.enqueue(session, sha, library_id=library_id)
            await session.commit()
    except Exception:  # de rij is niet nodig om nu af te spelen
        log.exception("analyse klaarzetten bij play")


# --- het antwoord ---------------------------------------------------------------------------------


def quality_options(probe: Probe, policy_max: int, transcode: bool) -> list:
    """auto en source, plus de treden onder de bron die de policy toestaat."""
    video = probe.video()
    if video is None:
        return ["auto"]
    options: list = ["auto", "source"]
    if transcode:
        source = decide.label(video.width, video.height)
        options += [h for h in LADDER if h < source and h <= policy_max]
    return options


def subtitle_tracks(probe: Probe, subs: list[dict[str, Any]], chosen: str | None) -> list:
    """De ingebedde sporen (subtitles.tracks, M5) plus de losse x<i> van de sessie."""
    out: list[SubtitleTrack] = []
    for entry in subtitles.tracks(probe):
        with contextlib.suppress(Exception):
            out.append(SubtitleTrack.model_validate(entry))
    for i, sub in enumerate(subs):
        lang = sub.get("lang")
        out.append(
            SubtitleTrack(
                id=f"x{i}",
                lang=lang,
                label=(lang or "").upper() or f"#{i + 1}",
                forced=sub["forced"],
                sdh=sub["sdh"],
                default=chosen == f"x{i}",
                state="pending",  # M5 maakt hem en weet de staat; tot dan pending
            )
        )
    return out


def trick_info(probe: Probe) -> TrickInfo | None:
    found = trick.info(probe)  # M5
    if not found:
        return None
    try:
        return TrickInfo(
            url="trick.vtt",
            interval_s=float(found["interval"]),
            width=int(found["w"]),
            height=int(found["h"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def gapless(probe: Probe) -> Gapless | None:
    """Voorloop en lengte (Keuze 18, 38), als de stap audio_info (M9) ze gaf."""
    data = probe.audio or {}
    try:
        return Gapless(rate=data["rate"], delay=data["delay"], samples=data["samples"])
    except (KeyError, TypeError, ValueError):
        return None


def file_mime(mime: str) -> str:
    return mime.split(";", 1)[0].strip()


def worker_problem(state: MediaState) -> HTTPException | None:
    """Wat de gezondheid (M1) over de worker weet: niets (ok), of de fout voor de client."""
    if state.worker == "ffmpeg_missing":
        return media_errors.ffmpeg_missing()
    if state.worker != "ok":
        return media_errors.worker_down()
    return None


def need_worker(state: MediaState, plan: Plan) -> None:
    """Remux, transcode en audio.mp4 hebben de worker nodig; direct play niet."""
    if plan.mode not in WORKER_MODES:
        return
    problem = worker_problem(state)
    if problem is not None:
        raise problem


def decide_or_worker(state: MediaState, *args: Any) -> Plan:
    """decide, maar zonder werkende worker is een weigering om plek (503 media_busy, alleen bij
    transcode) eigenlijk de worker: dan de fout van need_worker (bij het samenvoegen van M1 en
    M3). Zo krijgt een HEVC-film met de worker weg of zonder ffmpeg media_worker_down of
    media_ffmpeg_missing (de speler biedt dan Downloaden aan), en geen "druk, probeer later"."""
    try:
        return decide.decide(*args)
    except HTTPException as exc:
        code = exc.detail.get("code") if isinstance(exc.detail, dict) else None
        problem = worker_problem(state) if code == "media_busy" else None
        if problem is not None:
            raise problem from None
        raise


def need_tonemap(state: MediaState, plan: Plan) -> None:
    """Tone-mapping vraagt zscale en tonemap in de worker (Keuze 27); anders 422 met can_raw:
    de speler biedt "toch afspelen zonder kleurcorrectie" aan."""
    if plan.tonemap is None:
        return
    found = set(state.caps.filters) if state.caps is not None else set()
    if not all(name in found for name in TONEMAP_FILTERS):
        raise media_errors.hdr("no_tonemap", True)


async def start(
    request: Request,
    session: AsyncSession,
    settings: Settings,
    viewer: Viewer,
    body: PlayIn,
    found: Found,
) -> PlayOut:
    """Stap 2 tot en met 4 van play (de toegang is er al, de verbinding is nog open)."""
    state = media_state(request.app)
    node = found.node
    sha = node.blob_id
    assert sha is not None
    # Vanaf hier geen databaseverbinding meer vasthouden (regel 8, F1)
    await session.close()
    deadline = asyncio.get_running_loop().time() + PROBE_BUDGET
    stored = await analysis.load(maker(settings), sha)
    probed = not analysis.settled(stored)
    probe = await probe_now(settings, sha, deadline) if probed else stored
    assert probe is not None
    check_probe(probe)
    probe = await index_now(settings, probe, sha, deadline)
    await queue_analysis(settings, sha, found.library_id, probe, probed)

    choice = body.choice()
    if choice.audio is not None and decide.pick_audio(probe, choice.audio) is None:
        raise not_found()
    if choice.subtitle is not None:
        if choice.subtitle.startswith("e"):
            if decide.embedded_subtitle(probe, choice.subtitle) is None:
                raise not_found()
        elif int(choice.subtitle[1:]) >= len(found.subs):
            raise not_found()
    profile = profiles.merge(body.profile)
    policy = media_public.for_source(
        media_public.policy(found.config, public=viewer.public), found.config, probe
    )
    budget = admission.view(viewer.user.id, viewer.link_id)
    plan = decide_or_worker(state, probe, profile, choice, policy, budget)
    need_worker(state, plan)
    need_tonemap(state, plan)
    try:
        grant = admission.reserve(plan, viewer.user.id, viewer.link_id)
    except media_errors.MediaBusy as exc:
        raise media_errors.from_busy(exc) from None

    try:
        s = await sessions.create(
            viewer.user,
            node,
            probe,
            plan,
            grant,
            family=viewer.family,
            link_id=viewer.link_id,
            replace=body.replace,
            settings=settings,
        )
    except BaseException:
        admission.release(grant)
        raise
    audio = decide.pick_audio(probe, choice.audio) or decide.default_audio(probe)
    mime = decide.mime(probe, plan, audio)
    duration = probe.duration_ms or 0
    s.mime = file_mime(mime)
    s.subs = found.subs
    s.link_print = viewer.link_print
    s.start_ms = min(choice.start_ms, max(0, duration - 1))
    s.app = request.app

    info: StreamInfo | None = None
    if plan.mode in ("remux", "transcode"):
        try:
            info = await streams.open(s, plan)  # M4
        except BaseException:
            sessions.close(s)
            raise
        s.info = info
    if plan.mode == "direct":
        urls = PlayUrls(file="file")
        offset = 0.0
    elif plan.mode == "audio":
        urls = PlayUrls(audio="audio.mp4")  # M9
        offset = 0.0
    else:
        assert info is not None
        urls = PlayUrls(map=info.map_url or "map.json", master=info.url or "master.m3u8")
        offset = info.offset_ms / 1000
        mime = info.mime or mime
        duration = info.duration_ms or duration
    tracks = PlayTracks(
        audio=[
            AudioTrack(
                index=a.index,
                lang=a.lang,
                title=a.title,
                codec=a.codec or "",
                channels=a.channels,
                default=audio is not None and a.index == audio.index,
            )
            for a in probe.audio_streams()
        ],
        subtitles=subtitle_tracks(probe, found.subs, choice.subtitle),
    )
    return PlayOut(
        sid=s.sid,
        mode=plan.mode,
        reasons=plan.reasons,
        urls=urls,
        mime=mime,
        duration_ms=duration,
        start_ms=s.start_ms,
        offset=offset,
        tracks=tracks,
        chapters=probe.chapters,
        credits_ms=chapters.credits_ms(probe),  # M5
        trick=trick_info(probe),
        forced_available=plan.forced_available,
        quality=QualityInfo(
            current=choice.quality,
            options=quality_options(probe, policy.max_height, policy.transcode),
        ),
        gapless=gapless(probe) if probe.video() is None else None,
        started_at=s.created_at,
        expires_at=s.created_at + timedelta(seconds=sessions.MAX_AGE),
    )


# --- de routes ------------------------------------------------------------------------------------


@router.get("/{node_id}/codecs", response_model=CodecsOut)
async def codecs(
    node_id: uuid.UUID,
    user: CurrentUser,
    request: Request,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
):
    """De vaste kandidaten van Keuze 20 plus de strings van dit item (uit de probe; leeg als
    die er nog niet is). De client meet die met decodingInfo en isTypeSupported."""
    runtime.require(request)
    node = await readable_file(session, user, node_id)
    sha = node.blob_id
    await session.close()
    probe = await analysis.load(maker(settings), sha) if sha else None
    strings = [s.codec_string for s in probe.streams] if probe is not None else []
    return CodecsOut(
        candidates=profiles.candidates(),
        item=list(dict.fromkeys(s for s in strings if s)),
    )


@router.post("/{node_id}/play", response_model=PlayOut)
async def play(
    node_id: uuid.UUID,
    body: PlayIn,
    user: CurrentUser,
    family: TokenFamily,
    request: Request,
    session: Session,
    settings: Annotated[Settings, Depends(get_settings)],
):
    runtime.require(request)
    node = await readable_file(session, user, node_id)
    viewer = Viewer(user=user, family=family)
    found = await gather(request, session, settings, viewer, node)
    return await start(request, session, settings, viewer, body, found)
