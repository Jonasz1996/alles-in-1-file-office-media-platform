"""Het poort-cookie van /office/ (Keuze 7). issue() is van O0 (het cookie staat er al);
check() en forget_user() maakt O1 af.

Het cookie heet "office": een JWT met het app-geheim (nooit het office-geheim, en DS ziet het
nooit: de proxy stuurt geen Cookie door), typ "office_gate", de gebruiker en zijn login
(family), 2 uur geldig. HttpOnly, SameSite=Strict, Path=/office/, Secure over https.

check(headers, key=None) -> GateUser | None: cookie, gebruiker actief, login niet
ingetrokken; met een key ook lid van die sessie, niet banned, sessie niet dicht en nu
schrijfrecht. Uitslag hooguit office_gate_cache_seconds (15 s) in het geheugen. De stub
laat niemand door.
"""

import uuid
from dataclasses import dataclass

from fastapi import Response
from starlette.datastructures import Headers

from app.config import Settings
from app.models import User
from app.security import _encode

GATE_COOKIE = "office"
GATE_TYPE = "office_gate"
GATE_MINUTES = 120


@dataclass(frozen=True)
class GateUser:
    user_id: uuid.UUID
    family: uuid.UUID | None


def gate_token(settings: Settings, user: User, family: uuid.UUID | None) -> str:
    claims = {"sub": str(user.id), "typ": GATE_TYPE}
    if family is not None:
        claims["fam"] = str(family)
    return _encode(settings, claims, GATE_MINUTES)


def issue(
    response: Response, settings: Settings, user: User, family: uuid.UUID | None, https: bool
) -> None:
    response.set_cookie(
        GATE_COOKIE,
        gate_token(settings, user, family),
        max_age=GATE_MINUTES * 60,
        httponly=True,
        secure=https or settings.secure_cookies,
        samesite="strict",
        path="/office/",
    )


async def check(headers: Headers, key: str | None = None) -> GateUser | None:
    return None


def forget_user(user_id: uuid.UUID) -> None:
    return None
