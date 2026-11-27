"""De vormen van de API van gezichten automatisch (bouwontwerp fase 6, X2; de API van X2 en Keuze
60–69), vastgelegd in Z0b (Keuze 55). Gelijk aan web/src/photos/faces/types.ts.

Het bouwontwerp maakt A0 eigenaar van dit bestand (§2.0); Z0b zette de vormen al neer uit de
tabel van de API van X2, zodat de stubs van routers/people_faces.py hun antwoordmodel hebben en de
web ertegen kan bouwen. A0 legt ze vast. Tot X2 er is, geven de routes 501 not_built("X2").

Alleen de eigenaar ziet gezichten. Nooit een vector of een score: sure zegt alleen of het
voorstel ruim boven de drempel zit (score ≥ t_suggest + 0,10).
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.people.types import Box
from app.prefs import OnOff
from app.search.types import AnalyseState, PersonRef

# Zoveel gezichten mag één verzoek hooguit noemen; daarboven 422 (X2: faces_too_many)
MAX_FACES = 200
# Gezichten per pagina (de voorstellen voor één persoon, een groep)
FACES_PAGE = 60
# De uitsneden: 96 of 192 px (WebP, Cache-Control: no-store)
CropSize = Literal[96, 192]


# --- een gezicht ---


class FaceSuggest(BaseModel):
    """Een voorstel: lijkt op de bevestigde gezichten (sim) of een tag met die naam (tag)."""

    person_id: uuid.UUID
    name: str
    sure: bool
    why: Literal["sim", "tag"]


class FaceOut(BaseModel):
    """Een gezicht naar buiten: het kader als deel van de preview (0..1, zoals Keuze 37), de
    persoon als het bevestigd is, en anders misschien een voorstel."""

    id: uuid.UUID
    node_id: uuid.UUID
    box: Box
    person: PersonRef | None
    suggest: FaceSuggest | None


# --- de stand (GET /people/faces/status) ---


class FacesPhotos(BaseModel):
    total: int
    done: int
    waiting: int
    failed: int
    skipped: int


class FacesCount(BaseModel):
    found: int
    named: int
    open: int
    groups: int


class FacesStatus(BaseModel):
    """GET /people/faces/status (eigenaar): de regel bovenaan /fotos/personen."""

    setting: OnOff
    forbidden: bool  # de beheerder verbiedt het
    analyse: AnalyseState
    photos: FacesPhotos
    faces: FacesCount
    wiping: bool  # "Gezichten worden gewist …"


# --- voorstellen en groepen ---


class PersonSuggestions(BaseModel):
    """Per persoon: hoeveel voorstellen, met hooguit zes uitsneden ("Is dit Anna?")."""

    person: PersonRef
    count: int
    sample: list[FaceOut] = Field(max_length=6)


class SuggestionsByPerson(BaseModel):
    """GET /people/faces/suggestions?cursor= (zonder person)."""

    people: list[PersonSuggestions]
    cursor: str | None


class FacePage(BaseModel):
    """GET /people/faces/suggestions?person= en GET /people/faces/groups/{key}: de gezichten,
    beste eerst, per FACES_PAGE."""

    faces: list[FaceOut] = Field(max_length=FACES_PAGE)
    cursor: str | None


class FaceGroup(BaseModel):
    """Een groep van een ronde ("Wie is dit?"): key is "<run>:<cluster>"; hooguit twaalf."""

    key: str
    count: int
    faces: list[FaceOut] = Field(max_length=12)


class FaceGroups(BaseModel):
    """GET /people/faces/groups?cursor=: de grootste eerst."""

    groups: list[FaceGroup]
    cursor: str | None


# --- bevestigen, afwijzen, niet tonen, ongedaan maken ---


class FacesIn(BaseModel):
    """POST /people/faces/hide en /reset: alleen de gezichten die je zag en koos."""

    model_config = ConfigDict(extra="forbid")

    faces: list[uuid.UUID] = Field(min_length=1, max_length=MAX_FACES)


class FacesConfirmIn(FacesIn):
    """POST /people/faces/confirm: een bestaande persoon (person_id) of een nieuwe naam (name),
    niet allebei. Nooit "de hele groep"."""

    person_id: uuid.UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=100)

    @model_validator(mode="after")
    def one_of(self) -> FacesConfirmIn:
        if (self.person_id is None) == (self.name is None):
            raise ValueError("Geef person_id of name, niet allebei")
        return self


class FacesRejectIn(FacesIn):
    """POST /people/faces/reject: "nee, dit is niet …", en dat wordt onthouden."""

    person_id: uuid.UUID


class FacesQueued(BaseModel):
    """POST /people/faces/wipe-unnamed: zoveel gezichten gaan in de rij om gewist te worden."""

    queued: int
