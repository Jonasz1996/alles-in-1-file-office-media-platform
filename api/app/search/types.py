"""De vaste typen van zoeken (bouwontwerp fase 6, §3.3 en §3.4). Z0, frozen.

Hit blijft {node, path} zoals in 0.7.0 (path is de lijst mappen erboven, PathPart) en krijgt
match en snippet erbij; de web van nu leest alleen results en more en werkt dus gewoon door.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.routers.files import NodeOut, TagRef

# De soorten van node.ftype (mime.FTYPES)
FileType = Literal[
    "folder", "image", "video", "audio", "pdf", "document", "spreadsheet", "presentation", "other"
]
Match = Literal["name", "title", "text", "ocr", "meta"]
EngineName = Literal["fts", "like"]


class SearchRule(BaseModel):
    """De filters van een zoekvraag zonder de pagina: ook de regel van een slim album (Z6)."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    q: str = Field("", max_length=200)
    types: list[FileType] = []
    date_from: date | None = None
    date_to: date | None = None
    persons: list[uuid.UUID] = []
    place: int | None = None
    country: str | None = None
    tags: list[uuid.UUID] = []
    library: uuid.UUID | None = None
    camera: str | None = None
    in_: uuid.UUID | None = Field(None, alias="in")

    def has_filter(self) -> bool:
        """Een filter naast q. Zonder q en zonder filter: 400 search_bad_filter (§3.4)."""
        return bool(
            self.types
            or self.date_from
            or self.date_to
            or self.persons
            or self.place is not None
            or self.country
            or self.tags
            or self.library
            or self.camera
            or self.in_
        )


class SearchParams(SearchRule):
    """Een zoekvraag (GET /search). offset en limit voor ronde A; after (een rowid) voor de
    volgende pagina na ronde B (Keuze 9)."""

    offset: int = Field(0, ge=0, le=1000)
    limit: int = Field(30, ge=1, le=50)
    after: int | None = None


class PathPart(BaseModel):
    id: uuid.UUID
    name: str


class SnippetPart(BaseModel):
    """Een stuk tekst van een snippet; m: een treffer (de web markeert het, als tekst)."""

    t: str
    m: bool


class Hit(BaseModel):
    node: NodeOut
    # Van de hoofdmap tot en met de map waar het in staat; voor wat met je gedeeld is vanaf de
    # gedeelde map (leeg als het de gedeelde map zelf is). Zoals in 0.7.0.
    path: list[PathPart]
    match: Match = "name"
    snippet: list[SnippetPart] | None = None


class GroupItem(BaseModel):
    """Een groep in de resultaten (personen, plaatsen): alleen eigen, met het aantal."""

    id: str
    name: str
    n: int


class Groups(BaseModel):
    people: list[GroupItem] = []
    places: list[GroupItem] = []


class IndexProgress(BaseModel):
    """Tijdens de backfill: hoeveel van de inhoud al in de index staat (0..1)."""

    done: float = Field(ge=0, le=1)


class SearchPage(BaseModel):
    results: list[Hit]
    more: bool
    offset: int = 0
    partial: bool = False  # tijdsgrens geraakt (Keuze 9)
    ranked: bool = False  # False na ronde A
    names_only: bool = False  # ronde C
    after: int | None = None  # voor de volgende pagina na ronde B
    groups: Groups | None = None
    indexing: IndexProgress | None = None
    engine: EngineName = "like"


class NameRef(BaseModel):
    id: uuid.UUID
    name: str
    kind: str


class PersonRef(BaseModel):
    id: uuid.UUID
    name: str


class PlaceRef(BaseModel):
    id: int
    name: str
    admin1: str | None = None
    country: str | None = None


class AlbumRef(BaseModel):
    id: uuid.UUID
    name: str


class Suggestions(BaseModel):
    """GET /search/suggest: elk ≤ 5."""

    names: list[NameRef] = Field(default_factory=list, max_length=5)
    people: list[PersonRef] = Field(default_factory=list, max_length=5)
    places: list[PlaceRef] = Field(default_factory=list, max_length=5)
    tags: list[TagRef] = Field(default_factory=list, max_length=5)
    albums: list[AlbumRef] = Field(default_factory=list, max_length=5)
