"""De vaste typen van zoeken (bouwontwerp fase 6, §3.3 en §3.4). Z0, frozen.

Hit blijft {node, path} zoals in 0.7.0 (path is de lijst mappen erboven, PathPart) en krijgt
match en snippet erbij; de web van nu leest alleen results en more en werkt dus gewoon door.

Z0b (Keuze 55, 66 en 80) zette de velden en waarden van gezichten (X2) en zoeken op beeld (X3)
erbij, elk met een standaard: een vraag zonder mode krijgt hetzelfde antwoord als ervoor, met
mode "text" en fallback null erbij. mode en like staan in SearchParams en niet in SearchRule, dus
een regel van een slim album met mode geeft 422 (extra="forbid"). De vormen van de blokken op
/beheer zijn het voorstel van Z0b, gelijk aan web/src/admin/searchApi.ts; X2 en X3 leggen ze vast.
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
Match = Literal["name", "title", "text", "ocr", "meta", "image"]
EngineName = Literal["fts", "like"]
# Op naam en tekst (text, de standaard) of op wat er op de foto staat (image, X3; Keuze 80)
SearchMode = Literal["text", "image"]
# Waarom zoeken op beeld terugviel op gewoon zoeken: te laat, container weg, geen model (Keuze 78)
BeeldFallback = Literal["slow", "down", "no_model"]


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
    volgende pagina na ronde B (Keuze 9). mode en like: zoeken op beeld (X3, Keuze 80); zonder
    mode is het een vraag zoals ervoor."""

    offset: int = Field(0, ge=0, le=1000)
    limit: int = Field(30, ge=1, le=50)
    after: int | None = None
    mode: SearchMode = "text"
    like: uuid.UUID | None = None  # "Meer zoals deze": een eigen foto, met mode image


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
    # Z0b: welke stand antwoordde (zonder mode in de vraag altijd text), en of zoeken op beeld
    # terugviel op gewoon zoeken (X3)
    mode: SearchMode = "text"
    fallback: BeeldFallback | None = None


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
# no_preview en image_bad zijn van de stap embed (X3, Keuze 80)
AnalysisCode = Literal[
    "too_slow", "ocr_missing", "ocr_bad_image", "ocr_has_text", "zip_limit", "xml_refused",
    "pages_limit", "no_preview", "image_bad",
]  # fmt: skip


class AnalysisOut(BaseModel):
    """GET /files/{id}/analysis. Geen veld voor embed: of er een vector is, zegt de API niet
    (Keuze 79); alleen de codes van die stap staan in AnalysisCode."""

    text: StepState
    hash: StepState
    ocr: StepState
    truncated: bool
    code: AnalysisCode | None


# --- Beheer (Z7). §3.4 noemt de velden van GET /admin/search niet; dit is het voorstel uit
# Keuze 51, gelijk aan web/src/admin/searchApi.ts. Z7 legt het vast met app/search/admin.py ---

# faces: de mislukte foto's opnieuw en de versie nalopen (X2); beeld: alles opnieuw (X3)
RebuildWhat = Literal["names", "text", "geo", "hash", "ocr", "faces", "beeld"]


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


class AdminCheck(BaseModel):
    """De wekelijkse search-check (Keuze 12, R4; app/search/weekcheck.py): wanneer, hoeveel
    nodes vergeleken en bij hoeveel een verschil (elke num één keer, ook als fts_node en fts_name
    allebei afwijken). Nooit een num, naam of tekst."""

    at: str
    sampled: int
    bad: int


# --- De container analyse en de blokken van X2 en X3 (Z0b; Keuze 59, 67 en 82) ---

# De stand van de container analyse (Keuze 59), gedeeld door gezichten en beeld: off als
# PLATFORM_ANALYSE_ENABLED niet aan staat
AnalyseState = Literal["ok", "down", "models_missing", "off"]


class CodeCount(BaseModel):
    """Mislukt, als aantal per code (K2-21): nooit een foto, een gezicht of een gebruiker."""

    code: str
    n: int


class AnalyseModel(BaseModel):
    """Een model in de container (models.lock, Keuze 58): naam, versie en of de sha256 klopt."""

    name: str
    version: str
    sha_ok: bool


class AnalyseLimits(BaseModel):
    """De grenzen van de container: cpus en geheugen."""

    cpus: float
    mem_mb: int


class AnalyseQueue(BaseModel):
    """De rij analyse: wachtend, gedaan in het laatste uur, en waar hij op wacht."""

    waiting: int
    done_hour: int
    waits_for: Literal["transcodes", "office", "paused"] | None


class AnalyseQuality(BaseModel):
    """De laatste uitkomst van een meting van de kwaliteit (Vraag 8)."""

    at: str
    ok: bool


class FacesBench(BaseModel):
    """De laatste uitkomst van app.cli analyse-bench."""

    at: str
    photos_per_minute: float


class AdminFaces(BaseModel):
    """Het blok "Gezichten" (X2, Keuze 67; voorstel van Z0b). Nooit per gebruiker of gezicht."""

    allowed: bool  # "Gezichtsherkenning toestaan"
    analyse: AnalyseState
    reason: str | None  # bij down de reden, bij models_missing het model
    models: list[AnalyseModel]
    limits: AnalyseLimits | None  # None: zonder grenzen (geen cgroup-controllers)
    users_on: int
    queue: AnalyseQueue
    failed_by_code: list[CodeCount]
    faces: int
    named: int
    db_bytes: int
    bench: FacesBench | None
    eta_s: int | None
    wipe_waiting: bool
    quality: AnalyseQuality | None


class BeeldHour(BaseModel):
    """Zoeken op beeld in het afgelopen uur: vragen, de p95, en hoe vaak terug naar gewoon."""

    queries: int
    p95_ms: float | None
    slow: int
    down: int
    no_model: int


class BeeldSpace(BaseModel):
    """Ruimte in bytes: in de database, in de cache en aan modellen."""

    db_bytes: int
    cache_bytes: int
    models_bytes: int


class BeeldBench(BaseModel):
    """De uitkomst van app.cli beeld-bench (BZ6)."""

    at: str
    p95_ms: float
    photos_per_minute: float


class AdminBeeld(BaseModel):
    """Het blok "Zoeken op beeld" (X3, Keuze 82; voorstel van Z0b)."""

    on: bool
    model: Literal["ok", "missing", "unverified"]
    analyse: AnalyseState
    seen: int
    photos: int
    failed_by_code: list[CodeCount]
    hour: BeeldHour
    space: BeeldSpace
    bench: BeeldBench | None
    quality: AnalyseQuality | None


class AdminSearch(BaseModel):
    """GET en PATCH /admin/search: de stand van Keuze 51. faces en beeld (Z0b): None zolang X2
    en X3 er niet zijn. check (I1): de laatste wekelijkse search-check, None zolang er geen
    was."""

    engine: EngineName
    paused: bool
    index: AdminIndex
    rebuild: Rebuild | None
    extract_in_sandbox: bool
    per_hour: PerHour
    ocr: AdminOcr
    places: AdminPlaces
    bench: AdminBench | None
    faces: AdminFaces | None = None
    beeld: AdminBeeld | None = None
    check: AdminCheck | None = None


class AdminSearchPatch(BaseModel):
    """faces_allowed false wist alle gezichten van iedereen (Keuze 64); beeld false zet zoeken op
    beeld uit voor iedereen, de vectoren blijven tot DELETE /admin/search/beeld (Keuze 82)."""

    model_config = ConfigDict(extra="forbid")

    ocr: bool | None = None
    paused: bool | None = None
    faces_allowed: bool | None = None
    beeld: bool | None = None


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


# --- Zoeken op beeld voor jou (X3, Keuze 78; Z0b) ---


class BeeldStatus(BaseModel):
    """GET /photos/beeld: de stand van zoeken op beeld voor wie vraagt. bezig: je foto's worden
    nog bekeken (done 0..1); uit: jij; beheer_uit: de beheerder; niet_geinstalleerd: geen model;
    weg: de container draait niet."""

    state: Literal["ok", "bezig", "uit", "beheer_uit", "niet_geinstalleerd", "weg"]
    done: float | None = Field(ge=0, le=1)


# --- Gebeurtenissen (SSE, via events.publish, na de commit; §3.4) ---


class SearchReadyEvent(BaseModel):
    """search.ready, naar wie de nodes kan zien: de viewer ververst de tekstlaag."""

    nodes: list[uuid.UUID]
    step: Literal["text", "hash", "ocr"]


class PeopleChangedEvent(BaseModel):
    """people.changed, naar de eigenaar (leeg)."""


class SearchProgressEvent(BaseModel):
    """search.progress, alleen naar beheerders, elke 10 s tijdens een backfill of herbouw.
    faces en beeld: de backfill van X2 en X3 (Z0b)."""

    what: Literal["backfill", "names", "text", "geo", "hash", "ocr", "faces", "beeld"]
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
