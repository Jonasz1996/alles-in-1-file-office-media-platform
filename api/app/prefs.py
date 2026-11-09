"""De foto-instellingen van een gebruiker (bouwontwerp fase 6, §3.3 en §3.4). Z0, vast.

Zonder rij in photo_pref gelden de standaarden van PHOTO_PREFS. location "mij": de plek van je
eigen foto's zie alleen jij (Vraag 4); "uit": ook jij niet, en geen plaatsen. faces blijft "uit"
zolang X2 er niet is (Vraag 2).

Z0b (Keuze 55, 66 en 80) zette faces (X2) en beeld (X3) in de API, allebei standaard "uit" per
gebruiker (Vraag 11). beeld heeft nog geen kolom (die komt met 0012, A0): tot dan geeft
photo_prefs() de standaard. faces_forbidden en faces_available komen van X2 (de schakelaar van de
beheerder en de container analyse, Keuze 59); zonder X2 zijn ze False.

De route GET en PATCH /me/photo-settings is van Z6 (routers/photo_prefs.py). Wat gezichten en
beeld aan- en uitzetten mag en doet, is van X2 en X3: die melden zich hier aan (register_switch),
zodat Z6 zijn route niet voor ze hoeft te openen (review Z0b, contract F4). Z6 doet daarom:

- GET: photo_settings(), met faces_forbidden en faces_available van X2;
- PATCH: eerst check_switch(), vóór er iets geschreven wordt (review Z0b, veilig V3 en contract
  F1). Die weigert faces of beeld "aan" zolang X2 of X3 zich niet aanmeldt, en altijd als de
  analyse uit staat (Keuze 59: 409 faces_unavailable, 409 beeld_not_installed); "uit" gaat
  altijd, behalve voor een gast (403 guest). Daarna schrijven, en dan switched() in dezelfde
  transactie: X2 zet faces.plan of faces.wipe klaar, X3 de backfill of het wissen.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app import errors
from app.models import PhotoPref

if TYPE_CHECKING:
    from app.models import User

PHOTO_PREFS: dict[str, Any] = {
    "location": "mij",
    "memories": True,
    "ocr_photos": False,
    "faces": "uit",
    "beeld": "uit",
}

# Aan of uit, zoals photo_pref het bewaart (gezichten en beeld)
OnOff = Literal["aan", "uit"]


class PhotoSettings(BaseModel):
    """Het antwoord van GET /me/photo-settings."""

    location: Literal["mij", "uit"]
    memories: bool
    ocr_photos: bool
    # Z0b: gezichten (X2) en zoeken op beeld (X3), met een standaard
    faces: OnOff = "uit"
    faces_forbidden: bool = False  # de beheerder verbiedt het (Keuze 64)
    faces_available: bool = False  # aanzetten kan: niet in de stand off (Keuze 59)
    beeld: OnOff = "uit"


class PhotoSettingsPatch(BaseModel):
    """De body van PATCH /me/photo-settings: alleen wat verandert. faces en beeld: X2 en X3."""

    model_config = ConfigDict(extra="forbid")

    location: Literal["mij", "uit"] | None = None
    memories: bool | None = None
    ocr_photos: bool | None = None
    faces: OnOff | None = None
    beeld: OnOff | None = None


async def photo_prefs(db: AsyncSession, user_id: uuid.UUID) -> dict[str, Any]:
    """Alle vijf, met de standaard voor wat er niet is (beeld: altijd, tot 0012)."""
    row = await db.get(PhotoPref, user_id)
    if row is None:
        return dict(PHOTO_PREFS)
    return {
        "location": row.location,
        "memories": bool(row.memories),
        "ocr_photos": bool(row.ocr_photos),
        "faces": row.faces,
        "beeld": PHOTO_PREFS["beeld"],
    }


# --- Gezichten en beeld aan- en uitzetten (X2 en X3; review Z0b, veilig V3, contract F1 en F4) ---

# De schakelaars van X2 en X3 in PATCH /me/photo-settings, met het pakket dat ze vult
SWITCHES: dict[str, str] = {"faces": "X2", "beeld": "X3"}


@dataclass(frozen=True)
class SwitchState:
    """Wat X2 of X3 over zijn schakelaar zegt, voor deze gebruiker. forbidden: de beheerder
    verbiedt het (faces_allowed false, Keuze 64; beeld uit voor het platform, Keuze 82).
    available: aanzetten kan. Niet in de stand off (Keuze 59), en voor X2 ook niet zonder
    PLATFORM_FACES_KEYS; wel in down en models_missing, want dan wacht het werk in de rij (Keuze
    59). Zonder aanmelding: allebei False."""

    forbidden: bool = False
    available: bool = False


@dataclass(frozen=True)
class Switch:
    """De aanmelding van X2 (faces) of X3 (beeld). state: de stand voor deze gebruiker. changed:
    na het schrijven, in dezelfde transactie, als de waarde echt veranderde (X2: aan zet
    faces.plan klaar, uit faces.wipe; X3: de backfill of het wissen)."""

    state: Callable[[AsyncSession, User], Awaitable[SwitchState]]
    changed: Callable[[AsyncSession, User, OnOff], Awaitable[None]]


_switches: dict[str, Switch] = {}


def register_switch(key: Literal["faces", "beeld"], switch: Switch) -> None:
    if key not in SWITCHES:
        raise ValueError(key)
    _switches[key] = switch


def forget_switches() -> None:
    """Alleen voor tests: alle aanmeldingen weg."""
    _switches.clear()


async def switch_state(
    db: AsyncSession, user: User, key: str, *, analyse_enabled: bool
) -> SwitchState:
    """De stand van een schakelaar. Zonder PLATFORM_ANALYSE_ENABLED is er nooit iets beschikbaar,
    wat X2 of X3 ook zegt (Keuze 59: de stand off)."""
    switch = _switches.get(key)
    if switch is None:
        return SwitchState()
    state = await switch.state(db, user)
    return SwitchState(forbidden=state.forbidden, available=state.available and analyse_enabled)


async def check_switch(
    db: AsyncSession, user: User, patch: PhotoSettingsPatch, *, analyse_enabled: bool
) -> None:
    """Vóór PATCH /me/photo-settings iets schrijft. Gooit de fout als faces of beeld niet zo
    mag: een gast nooit (403 guest); aan alleen als het niet verboden is (403 faces_forbidden,
    409 beeld_off) en de analyse er is (409 faces_unavailable, 409 beeld_not_installed). Uit mag
    altijd: wissen draait in de rij default (Keuze 59)."""
    for key in SWITCHES:
        value = getattr(patch, key)
        if value is None:
            continue
        if user.role == "guest":
            raise errors.fail(403, "guest", "Gasten hebben geen eigen foto's")
        if value == "uit":
            continue
        state = await switch_state(db, user, key, analyse_enabled=analyse_enabled)
        if key == "faces":
            if state.forbidden:
                raise errors.fail(
                    403, "faces_forbidden", "De beheerder heeft gezichtsherkenning uitgezet"
                )
            if not state.available:
                raise errors.fail(
                    409,
                    "faces_unavailable",
                    "Gezichtsherkenning is op deze server niet geïnstalleerd",
                )
        else:
            if state.forbidden:
                raise errors.fail(409, "beeld_off", "Zoeken op beeld staat uit voor dit platform")
            if not state.available:
                raise errors.fail(
                    409,
                    "beeld_not_installed",
                    "Zoeken op beeld is op deze server niet geïnstalleerd",
                )


# De schakelaar location (Z3, Keuze 28): wat er na het schrijven gebeurt, in dezelfde transactie
# ("uit": de job die alle plekken van die gebruiker wist; "mij": photometa leest ze opnieuw). Geen
# controle vooraf: beide waarden mogen altijd (een gast weigert de route al).
LocationChanged = Callable[[AsyncSession, "User", str], Awaitable[None]]
_location: list[LocationChanged] = []


def register_location(fn: LocationChanged) -> None:
    """Z3 (app/geo/wire.py); twee keer dezelfde telt één keer."""
    if fn not in _location:
        _location.append(fn)


# De schakelaar ocr_photos (X1, Keuze 23): aan zet de foto's van die eigenaar in de rij ocr, in
# dezelfde transactie. Geen controle vooraf: beide waarden mogen altijd.
OcrPhotosChanged = Callable[[AsyncSession, "User", bool], Awaitable[None]]
_ocr_photos: list[OcrPhotosChanged] = []


def register_ocr_photos(fn: OcrPhotosChanged) -> None:
    """X1 (app/ocr/step.py); twee keer dezelfde telt één keer."""
    if fn not in _ocr_photos:
        _ocr_photos.append(fn)


async def switched(
    db: AsyncSession, user: User, before: dict[str, Any], patch: PhotoSettingsPatch
) -> None:
    """Na het schrijven van PATCH /me/photo-settings, in dezelfde transactie: X2 en X3 horen
    welke schakelaar echt veranderde (before: photo_prefs() van vóór het schrijven), Z3 of
    location veranderde, X1 of ocr_photos veranderde."""
    for key in SWITCHES:
        value = getattr(patch, key)
        switch = _switches.get(key)
        if value is not None and value != before.get(key) and switch is not None:
            await switch.changed(db, user, value)
    if patch.location is not None and patch.location != before.get("location"):
        for fn in list(_location):
            await fn(db, user, patch.location)
    if patch.ocr_photos is not None and patch.ocr_photos != before.get("ocr_photos"):
        for fn in list(_ocr_photos):
            await fn(db, user, patch.ocr_photos)


async def photo_settings(db: AsyncSession, user: User, *, analyse_enabled: bool) -> PhotoSettings:
    """Het antwoord van GET /me/photo-settings (en van PATCH, na het schrijven)."""
    state = await switch_state(db, user, "faces", analyse_enabled=analyse_enabled)
    return PhotoSettings(
        **await photo_prefs(db, user.id),
        faces_forbidden=state.forbidden,
        faces_available=state.available,
    )
