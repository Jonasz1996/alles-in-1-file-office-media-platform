"""De vaste typen van zoeken (bouwontwerp fase 6, §3.3 en §3.4). Z0, frozen.

Hit blijft {node, path} zoals in 0.7.0 (path is de lijst mappen erboven, PathPart) en krijgt
match en snippet erbij; de web van nu leest alleen results en more en werkt dus gewoon door.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.routers.files import NodeOut, TagRef

# De soorten van node.ftype (mime.FTYPES)
FileType = Literal[
    "folder", "image", "video", "audio", "pdf", "document", "spreadsheet", "presentation", "other"
]
Match = Literal["name", "title", "text", "ocr", "meta"]
EngineName = Literal["fts", "like"]


class SearchRule(BaseModel):
    """De filters van een zoekvraag zonder de pagina: ook de regel van een slim album (Z6).

    In Python de namen van §3.3 (types, date_from, …); als JSON en in de querystring de namen
    van §3.4 en Keuze 43 (type, from, to, person, tag, in), zoals web/src/search/types.ts ze
    stuurt. Binnen komen beide; naar buiten (en de regel in album_rule) met by_alias=True."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    q: str = Field("", max_length=200)
    types: list[FileType] = Field([], alias="type")
    date_from: date | None = Field(None, alias="from")
    date_to: date | None = Field(None, alias="to")
    persons: list[uuid.UUID] = Field([], alias="person")
    place: int | None = None
    country: str | None = None
    tags: list[uuid.UUID] = Field([], alias="tag")
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


# --- Tekst (Z2, X1) ---


class PageWord(BaseModel):
    """Een woord op een pagina met zijn plek, als deel van de pagina (0..1)."""

    t: str
    x: float
    y: float
    w: float
    h: float


class PageWords(BaseModel):
    """GET /files/{id}/pages/{n}/words: de tekstlaag van de viewer."""

    page: int
    words: list[PageWord]
    source: Literal["pdf", "ocr"]


# Een stap van de analyse zoals de API hem toont: wait en busy in de tabel zijn hier queued
StepState = Literal["queued", "done", "none", "skip", "failed"]
AnalysisCode = Literal[
    "too_slow", "ocr_missing", "ocr_bad_image", "ocr_has_text", "zip_limit", "xml_refused",
    "pages_limit",
]  # fmt: skip


class AnalysisOut(BaseModel):
    """GET /files/{id}/analysis."""

    text: StepState
    hash: StepState
    ocr: StepState
    truncated: bool
    code: AnalysisCode | None


# --- Beheer (Z7). §3.4 noemt de velden van GET /admin/search niet; dit is het voorstel uit
# Keuze 51, gelijk aan web/src/admin/searchApi.ts. Z7 legt het vast met app/search/admin.py ---

RebuildWhat = Literal["names", "text", "geo", "hash", "ocr"]


class Rebuild(IndexProgress):
    what: RebuildWhat
    started_at: str


class AdminIndex(BaseModel):
    names_done: bool
    text_done: int
    text_total: int
    failed: int


class PerHour(BaseModel):
    deadline_b: int
    deadline_c: int
    busy: int


class AdminOcr(BaseModel):
    installed: bool
    on: bool
    pages_queued: int


class AdminPlaces(BaseModel):
    loaded: bool
    source: str | None
    date: str | None


class AdminBench(BaseModel):
    at: str
    p95_owner_ms: float
    p95_all_ms: float


class AdminSearch(BaseModel):
    """GET en PATCH /admin/search: de stand van Keuze 51."""

    engine: EngineName
    paused: bool
    index: AdminIndex
    rebuild: Rebuild | None
    extract_in_sandbox: bool
    per_hour: PerHour
    ocr: AdminOcr
    places: AdminPlaces
    bench: AdminBench | None


class AdminSearchPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ocr: bool | None = None
    paused: bool | None = None


class RebuildIn(BaseModel):
    """POST /admin/search/rebuild → 202; 409 search_rebuild_running."""

    what: RebuildWhat


class FailedItem(BaseModel):
    sha: str
    step: Literal["text", "hash", "ocr", "geo"]
    code: str
    at: str


class FailedPage(BaseModel):
    """GET /admin/search/failed?cursor=."""

    items: list[FailedItem]
    cursor: str | None


# --- Gebeurtenissen (SSE, via events.publish, na de commit; §3.4) ---


class SearchReadyEvent(BaseModel):
    """search.ready, naar wie de nodes kan zien: de viewer ververst de tekstlaag."""

    nodes: list[uuid.UUID]
    step: Literal["text", "hash", "ocr"]


class PeopleChangedEvent(BaseModel):
    """people.changed, naar de eigenaar (leeg)."""


class SearchProgressEvent(BaseModel):
    """search.progress, alleen naar beheerders, elke 10 s tijdens een backfill of herbouw."""

    what: Literal["backfill", "names", "text", "geo", "hash", "ocr"]
    done: float = Field(ge=0, le=1)


EVENT_SEARCH_READY = "search.ready"
EVENT_PEOPLE_CHANGED = "people.changed"
EVENT_SEARCH_PROGRESS = "search.progress"
EVENTS: dict[str, type[BaseModel]] = {
    EVENT_SEARCH_READY: SearchReadyEvent,
    EVENT_PEOPLE_CHANGED: PeopleChangedEvent,
    EVENT_SEARCH_PROGRESS: SearchProgressEvent,
}


def event_data(type: str, data: BaseModel) -> dict[str, Any]:
    """De data van een gebeurtenis als JSON, gecontroleerd tegen de vorm van dat type."""
    model = EVENTS[type]
    if not isinstance(data, model):
        raise TypeError(f"{type} verwacht {model.__name__}")
    return data.model_dump(mode="json")
