"""/api/v1/admin/office: office op /beheer (Keuze 27 en 28). Alleen voor beheerders.

Zonder PLATFORM_OFFICE_URL geeft alles 503 office_off: de app is dan precies fase 3, en het
tabblad Office staat er niet. Met een adres maar foute instellingen geeft de GET configured
false (het tabblad zegt wat er is), en de knoppen die DS nodig hebben 503
office_misconfigured.

- GET: de toestand (O0), de verbindingen, de piek, de open documenten en de forgotten files
  (monitor.py), en de tellers van de conversies (O3).
- PUT: OfficeConfig in de tabel setting (sleutel office) en meteen in het geheugen: O2 leest
  daar max_editors bij het openen, checkpoint_minutes in de ronde en version_minutes bij het
  opslaan; enabled zet nieuw bewerken, Nieuw en de conversies (O3) aan of uit, zonder
  herstart. Wie al bewerkt, mag zijn werk afmaken: zijn opslag komt gewoon binnen. Weer aan
  met een gezonde DS start de inhaalronde van O3 (convert.start_backfill); is DS niet gezond,
  dan doet health.py dat bij "weer gezond".
- POST /checkpoint: forcesave voor elke open sessie (sweep.checkpoint_counts), en hooguit
  CHECKPOINT_WAIT seconden wachten op de opslag.
- POST /conversions/retry: elke failed meteen opnieuw, met nieuwe keys bij DS
  (convert.retry_failed). Alleen met office aan en een gezonde DS (anders 503): een taak die dan
  niet kan omzetten, gaat terug naar failed.
- Terugzetten en Verwijderen: recovery.restore en recovery.forget (O2), alleen voor een key
  die nu in getForgottenList staat.
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from app import errors
from app.config import Settings
from app.db import Session
from app.models import Node, User
from app.office import convert, engine, monitor, recovery, runtime, sweep
from app.office.state import OfficeConfig, OfficeState, office_policy, office_state
from app.routers.files import NodeOut, one_out
from app.routers.office_contracts import (
    CheckpointOut,
    OfficeAdmin,
    OfficeAdminIn,
    OfficeConversions,
    OfficeOpenDocument,
    OfficePeak,
    OfficeUnsaved,
    RetryOut,
)
from app.security import AdminUser

log = logging.getLogger("platform.office")

router = APIRouter(prefix="/api/v1/admin/office", tags=["beheer"])

OFF = "Bewerken van documenten staat op deze server niet aan"
MISCONFIGURED = "Office is verkeerd ingesteld; zie het log van de app"
UNAVAILABLE = recovery.UNAVAILABLE
NOT_FOUND = recovery.NOT_FOUND
# Zo lang wacht "Nu tussentijds opslaan" op de opslag van de open documenten
CHECKPOINT_WAIT = 10.0


def _office() -> Settings:
    """De instellingen; zonder PLATFORM_OFFICE_URL 503 office_off."""
    settings = runtime.settings()
    if not settings.office_url:
        raise errors.fail(503, "office_off", OFF)
    return settings


def _engine() -> engine.Engine:
    """Voor de knoppen die DS nodig hebben: geldige instellingen."""
    _office()
    eng = engine.current()
    if eng is None:
        raise errors.fail(503, "office_misconfigured", MISCONFIGURED)
    return eng


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None


async def _overview(
    session, settings: Settings, state: OfficeState, config: OfficeConfig
) -> OfficeAdmin:
    configured = settings.office_configured and engine.current() is not None
    usage = await monitor.usage(session, state)
    peak = monitor.peak(state, usage.connections)
    documents = await monitor.open_documents(session, state) if configured else []
    keys = await monitor.forgotten(state) if configured else None
    unsaved = await monitor.unsaved(session, keys or [])
    counts = await monitor.conversions(session)
    await session.commit()
    return OfficeAdmin(
        configured=configured,
        enabled=config.enabled,
        state=state.health,
        down_since=_iso(state.down_since),
        version=state.version,
        license_connections=state.license_connections,
        tokens_enforced=state.tokens_enforced,
        max_editors=config.max_editors,
        checkpoint_minutes=config.checkpoint_minutes,
        version_minutes=config.version_minutes,
        connections=usage.connections,
        people=usage.people,
        documents=usage.documents,
        peak=OfficePeak(connections=peak[0], at=peak[1].isoformat()) if peak else None,
        open=[
            OfficeOpenDocument(
                name=doc.name,
                owner=doc.owner,
                editors=doc.editors,
                since=doc.since.isoformat(),
                state=doc.state,
            )
            for doc in documents
        ],
        unsaved=[
            OfficeUnsaved(key=u.key, name=u.name, owner=u.owner, at=_iso(u.at), reason=u.reason)
            for u in unsaved
        ],
        conversions=OfficeConversions(**counts),
    )


@router.get("")
async def office_admin(_: AdminUser, request: Request, session: Session) -> OfficeAdmin:
    settings = _office()
    config = await office_policy(request.app).get(session)
    return await _overview(session, settings, office_state(request.app), config)


@router.put("")
async def save_office_admin(
    body: OfficeAdminIn, _: AdminUser, request: Request, session: Session
) -> OfficeAdmin:
    settings = _office()
    policy = office_policy(request.app)
    before = await policy.get(session)
    config = OfficeConfig(**body.model_dump())
    await policy.save(session, config)
    state = office_state(request.app)
    if config.enabled and not before.enabled:
        log.info("office: weer aangezet op /beheer")
        if state.healthy and engine.current() is not None:
            # De inhaalronde van O3: wat tijdens "uit" binnenkwam, krijgt alsnog een PDF
            try:
                await convert.start_backfill(settings)
            except Exception:  # noqa: BLE001 - de instelling staat; de volgende "weer gezond"
                log.exception("office: de inhaalronde starten mislukte")
    elif before.enabled and not config.enabled:
        log.info("office: uitgezet op /beheer")
    return await _overview(session, settings, state, config)


@router.post("/checkpoint")
async def checkpoint(_: AdminUser, request: Request) -> CheckpointOut:
    _engine()
    if not office_state(request.app).healthy:
        raise errors.fail(503, "office_unavailable", UNAVAILABLE)
    sent, saved = await sweep.checkpoint_counts(CHECKPOINT_WAIT)
    return CheckpointOut(sent=sent, saved=saved)


@router.post("/conversions/retry")
async def retry_conversions(_: AdminUser, request: Request, session: Session) -> RetryOut:
    _engine()
    if not (await office_policy(request.app).get(session)).enabled:
        raise errors.fail(503, "office_off", OFF)
    if not office_state(request.app).healthy:
        raise errors.fail(503, "office_unavailable", UNAVAILABLE)
    queued = await convert.retry_failed(session)
    await session.commit()
    return RetryOut(queued=queued)


async def _listed(request: Request, key: str) -> None:
    """Staat deze key nu in getForgottenList? Zo niet: 404 not_found; DS weg: 503."""
    _engine()
    keys = await monitor.forgotten(office_state(request.app), fresh=True)
    if keys is None:
        raise errors.fail(503, "office_unavailable", UNAVAILABLE)
    if key not in keys:
        raise errors.fail(404, "not_found", NOT_FOUND)


@router.post("/unsaved/{key}/restore")
async def restore_unsaved(key: str, _: AdminUser, request: Request, session: Session) -> NodeOut:
    await _listed(request, key)
    try:
        copy = await recovery.restore(key)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        if detail.get("code") == "office_owner_gone":
            raise errors.fail(409, "office_owner_gone", recovery.OWNER_GONE) from None
        raise
    finally:
        monitor.forget_cache()
    node = await session.get(Node, copy.id)
    owner = await session.get(User, node.owner_id) if node is not None else None
    if node is None or owner is None:
        raise errors.fail(404, "not_found", NOT_FOUND)
    # Zoals de eigenaar hem ziet: de beheerder heeft er zelf geen recht op
    return await one_out(session, owner, node)


@router.delete("/unsaved/{key}", status_code=204)
async def delete_unsaved(key: str, _: AdminUser, request: Request) -> None:
    await _listed(request, key)
    try:
        await recovery.forget(key)
    finally:
        monitor.forget_cache()
