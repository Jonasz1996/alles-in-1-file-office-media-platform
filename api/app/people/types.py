"""De vormen van de API van §3.4 voor personen, herinneringen en slimme albums (bouwontwerp fase
6). Z0, vast; Z6 bouwt de routes. Alleen eigen gegevens: een id van een ander geeft 404
not_found, en tellingen en omslagen gaan met own_live.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, model_validator

from app.routers.photos import PhotoItem
from app.search.types import PersonRef, SearchRule

NAME = Field(min_length=1, max_length=100)


# --- Personen ---


class Person(BaseModel):
    id: uuid.UUID
    name: str
    count: int
    cover: uuid.UUID | None
    hidden: bool


class People(BaseModel):
    """GET /people?hidden=0|1."""

    people: list[Person]


class PersonIn(BaseModel):
    """POST /people → 201 met de persoon; 409 people_name_taken."""

    model_config = ConfigDict(extra="forbid")

    name: str = NAME


class PersonPatch(BaseModel):
    """PATCH /people/{id}: alleen wat verandert."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(None, min_length=1, max_length=100)
    hidden: bool | None = None
    cover: uuid.UUID | None = None


class MergeIn(BaseModel):
    """POST /people/{id}/merge → de persoon waarin hij opging; 400 people_merge_self."""

    model_config = ConfigDict(extra="forbid")

    into: uuid.UUID


class PersonPhotos(BaseModel):
    """GET /people/{id}/photos?cursor=."""

    items: list[PhotoItem]
    cursor: str | None


# Een kader op de foto, als deel van de foto (0..1): x, y, breedte, hoogte
Box = tuple[float, float, float, float]


class FilePerson(BaseModel):
    person: PersonRef
    box: Box | None
    source: Literal["hand", "auto"]


class FilePeople(BaseModel):
    """GET /files/{id}/people; voor een ander dan de eigenaar altijd leeg."""

    people: list[FilePerson]


class AddPersonIn(BaseModel):
    """POST /files/{id}/people → 201: een bestaande persoon (person_id) of een nieuwe (name),
    met een kader of zonder. Alleen de eigenaar van de node."""

    model_config = ConfigDict(extra="forbid")

    person_id: uuid.UUID | None = None
    name: str | None = Field(None, min_length=1, max_length=100)
    box: Box | None = None

    @model_validator(mode="after")
    def one_of(self) -> AddPersonIn:
        if (self.person_id is None) == (self.name is None):
            raise ValueError("Geef person_id of name, niet allebei")
        return self


# --- Herinneringen ---


class MemoryYear(BaseModel):
    years_ago: int
    day: date
    items: list[PhotoItem]


class Memories(BaseModel):
    """GET /photos/memories?day=JJJJ-MM-DD."""

    years: list[MemoryYear]


class MemoryHide(BaseModel):
    """POST /photos/memories/hide → 204; 400 memories_bad_hide."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["day", "folder", "person"]
    ref: str = Field(min_length=1, max_length=255)


class MemoryHides(BaseModel):
    """GET /photos/memories/hide."""

    hides: list[MemoryHide]


# --- Slimme albums ---


class AlbumRuleIn(BaseModel):
    """PUT /albums/{id}/rule: een zoekvraag zonder pagina (SearchRule); share_new: nieuwe items
    meteen delen zoals het album (Keuze 42)."""

    model_config = ConfigDict(extra="forbid")

    rule: SearchRule
    share_new: bool | None = None


class AlbumRuleSaved(BaseModel):
    """Het antwoord van PUT /albums/{id}/rule en POST …/rule/confirm. needs_confirm: het album
    is gedeeld of heeft een publieke link, en share_new is niet gezet."""

    needs_confirm: bool = False


class RuleConfirmIn(BaseModel):
    """POST /albums/{id}/rule/confirm: true deelt alle items van de regel, false houdt ze voor
    de eigenaar."""

    model_config = ConfigDict(extra="forbid")

    share_new: bool


class AlbumRuleInfo(BaseModel):
    """Wat GET /albums en /albums/{id} de eigenaar over de regel van een album geven (AlbumOut
    .rule; voor anderen en zonder regel null). count: op hoeveel foto's de regel bij de laatste
    verversing paste; code: album_rule_too_many als dat er meer dan 10.000 waren (dan bleven de
    items zoals ze waren)."""

    rule: SearchRule
    share_new: bool
    refreshed_at: datetime
    count: int
    code: str | None = None

    @field_serializer("rule")
    def _rule(self, rule: SearchRule) -> dict:
        """Zoals de web hem stuurt (de namen van §3.4), zonder wat leeg is."""
        return rule.model_dump(mode="json", by_alias=True, exclude_defaults=True)


class AlbumItemRule(BaseModel):
    """Per item van een album, alleen voor de eigenaar (AlbumPhotoItem.album): met de hand of
    uit de regel, en of anderen het zien (false: het merkje "alleen voor jou")."""

    source: Literal["hand", "rule"]
    shared: bool


class RuleItemsPrivate(BaseModel):
    """In elk antwoord met een deling (routers/shares.ShareOut erft het): bij een nieuwe deling
    of publieke link op een album met een regel en share_new 0 zoveel items uit de regel die
    alleen voor de eigenaar blijven (de vraag in het deeldialoog, Keuze 42); anders null."""

    rule_items_private: int | None = None
