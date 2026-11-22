"""Mediabeheer op /beheer: de koppelingen met Prowlarr en qBittorrent (fase 11, stap 2).

GET    /api/v1/admin/arr                      → ArrView (nooit een geheim)
PUT    /api/v1/admin/arr/prowlarr             ProwlarrIn → ProwlarrTest | 422 arr_link_auth
                                              | 503 arr_link_unreachable
POST   /api/v1/admin/arr/prowlarr/test        → ProwlarrTest | 404 not_found | 422 | 503
DELETE /api/v1/admin/arr/prowlarr             → 204
PUT    /api/v1/admin/arr/qbittorrent          QBitIn → QBitTest | 422 arr_link_auth | 503
POST   /api/v1/admin/arr/qbittorrent/test     → QBitTest | 404 not_found | 422 | 503
DELETE /api/v1/admin/arr/qbittorrent          → 204

Alleen beheerders. Opslaan test eerst de verbinding; lukt dat niet, dan wordt er niets bewaard.
Een fout zegt nooit een sleutel of wachtwoord, alleen een vaste reden.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field

from app.arr import prowlarr, qbit
from app.arr import settings as arr_settings
from app.config import Settings, get_settings
from app.db import Session
from app.errors import fail
from app.security import AdminUser

router = APIRouter(prefix="/api/v1/admin/arr", tags=["beheer"])
Config = Annotated[Settings, Depends(get_settings)]

_AUTH_WORDS = ("weigert", "ontbreekt", "ongeldig")


class ProwlarrIn(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    key: str | None = Field(default=None, max_length=200)


class QBitIn(BaseModel):
    url: str = Field(min_length=8, max_length=500)
    username: str = Field(min_length=1, max_length=200)
    password: str | None = Field(default=None, max_length=500)
    category: str = Field(default="platform", pattern=r"^[A-Za-z0-9_-]{1,64}$")
    save_path: str = Field(default="/downloads", pattern=r"^/[^\x00]{0,499}$")


class IndexerOut(BaseModel):
    id: int
    name: str
    privacy: str


class ProwlarrTest(BaseModel):
    indexers: list[IndexerOut]


class QBitTest(BaseModel):
    version: str


def _link_error(exc: Exception):
    """Een vaste reden uit prowlarr.py of qbit.py naar een fout van de API."""
    reason = str(exc)
    if any(word in reason for word in _AUTH_WORDS):
        return fail(422, "arr_link_auth", reason)
    return fail(503, "arr_link_unreachable", reason)


def _indexers(found: list[prowlarr.Indexer]) -> ProwlarrTest:
    return ProwlarrTest(
        indexers=[IndexerOut(id=i.id, name=i.name, privacy=i.privacy) for i in found]
    )


@router.get("")
async def overview(admin: AdminUser, session: Session, settings: Config) -> dict:
    return await arr_settings.view(session, settings)


@router.put("/prowlarr", response_model=ProwlarrTest)
async def save_prowlarr(body: ProwlarrIn, admin: AdminUser, session: Session, settings: Config):
    try:
        found = await arr_settings.save_prowlarr(session, settings, body.url, body.key)
    except prowlarr.ProwlarrError as exc:
        raise _link_error(exc) from None
    return _indexers(found)


@router.post("/prowlarr/test", response_model=ProwlarrTest)
async def test_prowlarr(admin: AdminUser, session: Session, settings: Config):
    conf = await arr_settings.load_prowlarr(session, settings)
    if conf is None:
        raise fail(404, "not_found", "Prowlarr is nog niet ingesteld")
    try:
        return _indexers(await arr_settings.check_prowlarr(conf))
    except prowlarr.ProwlarrError as exc:
        raise _link_error(exc) from None


@router.delete("/prowlarr", status_code=204)
async def forget_prowlarr(admin: AdminUser, session: Session):
    await arr_settings.forget(session, "prowlarr")
    return Response(status_code=204)


@router.put("/qbittorrent", response_model=QBitTest)
async def save_qbit(body: QBitIn, admin: AdminUser, session: Session, settings: Config):
    try:
        version = await arr_settings.save_qbit(
            session,
            settings,
            url=body.url,
            username=body.username,
            password=body.password,
            category=body.category,
            save_path=body.save_path,
        )
    except qbit.QBitError as exc:
        raise _link_error(exc) from None
    return QBitTest(version=version)


@router.post("/qbittorrent/test", response_model=QBitTest)
async def test_qbit(admin: AdminUser, session: Session, settings: Config):
    conf = await arr_settings.load_qbit(session, settings)
    if conf is None:
        raise fail(404, "not_found", "qBittorrent is nog niet ingesteld")
    try:
        return QBitTest(version=await arr_settings.check_qbit(conf))
    except qbit.QBitError as exc:
        raise _link_error(exc) from None


@router.delete("/qbittorrent", status_code=204)
async def forget_qbit(admin: AdminUser, session: Session):
    await arr_settings.forget(session, "qbittorrent")
    return Response(status_code=204)
