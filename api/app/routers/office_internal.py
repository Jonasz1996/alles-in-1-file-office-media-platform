"""/internal/office: wat de document server aanroept, alleen op de interne luisteraar (poort
8081, Keuze 8). O0-stubs die 503 office_off geven; O2 maakt ze af.

GET  /internal/office/doc/{key}   Authorization: Bearer <download-JWT van DS>
     -> 200 de blob van de sessie (Repr-Digest), 401 office_token, 404 not_found
POST /internal/office/callback    token in de body en/of Authorization: Bearer
     -> 200 {"error": 0} | 200 {"error": 1} | 503 (DS probeert opnieuw)
"""

from fastapi import APIRouter

from app import errors

router = APIRouter(prefix="/internal/office", tags=["office intern"])

OFF = "Bewerken van documenten staat op deze server niet aan"


@router.get("/doc/{key}")
async def document(key: str) -> None:
    raise errors.fail(503, "office_off", OFF)


@router.post("/callback")
async def callback() -> None:
    raise errors.fail(503, "office_off", OFF)
