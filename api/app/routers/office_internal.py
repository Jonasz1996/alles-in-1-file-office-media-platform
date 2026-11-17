"""/internal/office: wat de document server aanroept, alleen op de interne luisteraar (poort
8081, Keuze 8). Zonder office: 503 office_off.

GET  /internal/office/doc/{key}   Authorization: Bearer <download-JWT van DS>
     -> 200 de blob van de sessie (Repr-Digest), 401 office_token, 404 not_found (ook als de
        sessie dicht is)
POST /internal/office/callback    token in de body en/of Authorization: Bearer
     -> 200 {"error": 0} | 200 {"error": 1} | 503 (DS probeert opnieuw)

De interne app heeft geen eigen afhankelijkheden (get_session): de databasesessies komen van
sessions.maker(), met de instellingen van office (runtime).
"""

import logging

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

from app import errors
from app.office import engine, inspect, keys, runtime, saving, sessions, urls
from app.storage import BlobStore

log = logging.getLogger("platform.office")

router = APIRouter(prefix="/internal/office", tags=["office intern"])

OFF = "Bewerken van documenten staat op deze server niet aan"
TOKEN = "Token van de document server ongeldig of verlopen"
NOT_FOUND = "Niet gevonden"
# Zo groot mag een callback zijn voor we hem lezen (oo/editor.py MAX_BODY is 1 MB)
MAX_CALLBACK = 2 * 1024 * 1024


def _engine() -> engine.Engine:
    eng = engine.current()
    if eng is None:
        raise errors.fail(503, "office_off", OFF)
    return eng


@router.get("/doc/{key}")
async def document(key: str, request: Request) -> FileResponse:
    """De inhoud waar deze key voor staat (de blob van de sessie), alleen met het download-token
    van DS voor precies dit adres. Na de invoercontrole (Keuze 23), net als bij het openen."""
    eng = _engine()
    settings = runtime.settings()
    if not keys.valid(key):
        raise errors.fail(404, "not_found", NOT_FOUND)
    url = urls.internal_url(settings, f"/internal/office/doc/{key}")
    try:
        eng.check_download(request.headers.get("authorization"), url)
    except engine.TokenError:
        raise errors.fail(401, "office_token", TOKEN) from None
    async with sessions.maker()() as session:
        row = await sessions.find_by_key(session, key)
        if row is None or row.state == "closed" or not row.blob_id:
            raise errors.fail(404, "not_found", NOT_FOUND)
        sha, file_type = row.blob_id, row.file_type
    path = BlobStore(settings.blob_dir).path(sha)
    if not await anyio.Path(path).exists():
        raise errors.fail(404, "not_found", NOT_FOUND)
    verdict = await inspect.check(settings, path, file_type, sha)
    if not verdict.ok:
        log.warning("office: invoercontrole weigert de inhoud van een sessie (%s)", verdict.reason)
        raise errors.fail(422, "office_unsafe", "Dit bestand kwam niet door de controle")
    return FileResponse(
        path,
        media_type="application/octet-stream",
        headers={"Repr-Digest": saving.repr_digest(sha), "Cache-Control": "no-store"},
    )


@router.post("/callback")
async def callback(request: Request) -> JSONResponse:
    """Een callback van de editor (Keuze 15 en 16). Alleen wat in het token staat telt."""
    eng = _engine()
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > MAX_CALLBACK:
            raise errors.fail(401, "office_token", TOKEN)
    try:
        event = eng.read_callback(body, request.headers.get("authorization"))
    except engine.TokenError:
        raise errors.fail(401, "office_token", TOKEN) from None
    outcome = await saving.handle(event)
    status, reply = eng.callback_reply(outcome)
    return JSONResponse(reply, status_code=status)
