"""De API-contracten van office (fase 4, §3.4): de Pydantic-modellen van O2 tot en met O5 en de
gebeurtenissen. Frozen na O0: alleen de hotfix-lane verandert ze.

Foutcodes (web/src/i18n/errors/office.ts): office_off, office_unavailable, office_format,
office_too_large, office_unsafe, office_busy, office_too_many {max}, office_gate, office_token,
office_convert_failed {code}, pdf_pending, office_owner_gone, office_misconfigured. Bestaande
codes blijven: not_found, read_only, trashed, not_a_file, album, no_room, folder_not_found,
guest, rate_limited.

Meldingen op /beheer (NOTICES): office_down, office_misconfigured, office_version,
office_limit {used, max}, office_unsaved {count}, office_convert_failed {count}.
"""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

OfficeHealth = Literal["off", "starting", "ok", "down", "misconfigured"]
PdfState = Literal["pending", "ready", "failed", "none", "unsafe", "ask"]
OfficeKind = Literal["docx", "xlsx", "pptx"]
# Wat POST /office/new maakt: de kantoorsoorten, plus een leeg tekstbestand (dat werkt ook
# zonder office: de web-app bewerkt tekst zelf)
NewKind = Literal["docx", "xlsx", "pptx", "txt"]
# Waarnaar "Kopie opslaan als" en "Omzetten" mogen schrijven (wat DS kan maken)
SAVE_AS_FORMATS: list[str] = [
    "docx", "odt", "rtf", "txt", "pdf", "xlsx", "ods", "csv", "pptx", "odp",
]  # fmt: skip

# Wat de editor bewerkt en wat "Nieuw" maakt (Keuze 23 en 24), en de grens van DS zelf
# (maxDownloadBytes)
EDIT_FORMATS: list[str] = ["docx", "xlsx", "pptx"]
NEW_KINDS: list[OfficeKind] = ["docx", "xlsx", "pptx"]
MAX_EDIT_BYTES = 104_857_600
API_URL = "/office/web-apps/apps/api/documents/api.js"
GATE_SECONDS = 7200


# --- O2: bewerken ---


class OfficeStatus(BaseModel):
    """GET /api/v1/office/status"""

    enabled: bool
    editing: bool
    state: OfficeHealth
    edit_formats: list[str] = EDIT_FORMATS
    new_kinds: list[OfficeKind] = NEW_KINDS
    max_bytes: int = MAX_EDIT_BYTES


class OfficeOpen(BaseModel):
    editors: int


OfficeReason = Literal[
    "read_only",
    "format",
    "too_large",
    "unsafe",
    "trashed",
    "album",
    "office_off",
    "office_down",
    "too_many",
]


class OfficeFile(BaseModel):
    """GET /api/v1/office/files/{id}"""

    can_edit: bool
    reason: OfficeReason | None = None
    open: OfficeOpen | None = None
    pdf_state: PdfState | None = None


class SessionIn(BaseModel):
    """POST /api/v1/office/files/{id}/session"""

    type: Literal["desktop", "mobile"] | None = None
    theme: Literal["light", "dark"] | None = None


class SessionOut(BaseModel):
    """200, plus Set-Cookie: office=… (Path=/office/)"""

    api_url: str = API_URL
    config: dict
    key: str
    stale: bool
    gate_seconds: int = GATE_SECONDS


# POST /api/v1/office/files/{id}/save -> 202 {}
# POST /api/v1/office/gate -> 204 + Set-Cookie
# Intern (poort 8081): GET /internal/office/doc/{key}, POST /internal/office/callback


# --- O3: voorbeelden en PDF ---


class ConvertOut(BaseModel):
    """POST /api/v1/office/files/{id}/convert -> 202"""

    pdf_state: Literal["pending", "ready"]


class PdfLink(BaseModel):
    """POST /api/v1/office/files/{id}/pdf"""

    url: str  # /api/v1/office/pdf/{id}?token=…
    expires_in: int = 600


# GET /api/v1/office/pdf/{id}?token= -> application/pdf
# Intern: GET /internal/office/src/{grant}


# --- O4: nieuw document ---


class NewDocumentIn(BaseModel):
    """POST /api/v1/office/new -> 201 NodeOut"""

    parent_id: uuid.UUID
    kind: NewKind
    name: str | None = Field(default=None, max_length=255)


class SaveAsIn(BaseModel):
    """POST /api/v1/office/files/{id}/save-as -> 201 NodeOut: "Kopie opslaan als" in de editor
    (onRequestSaveAs van DS). url is het adres dat DS gaf (al omgezet naar file_type)."""

    key: str = Field(max_length=128)
    url: str = Field(max_length=4096)
    file_type: str = Field(max_length=16)
    title: str | None = Field(default=None, max_length=255)


class ConvertToIn(BaseModel):
    """POST /api/v1/office/files/{id}/convert-to -> 201 NodeOut: een omgezette kopie in dezelfde
    map (bijv. bij hernoemen naar een andere extensie). name: de naam van de kopie (de extensie
    moet bij format passen); zonder naam de stam van het origineel met de nieuwe extensie."""

    format: str = Field(max_length=16)
    name: str | None = Field(default=None, max_length=255)


# --- O5: beheer ---


class OfficePeak(BaseModel):
    connections: int
    at: str


class OfficeOpenDocument(BaseModel):
    name: str
    owner: str
    editors: list[str]
    since: str
    # pending (een config uitgegeven, nog niemand verbonden), open of saving; pending en saving
    # houden de conversies tegen (sessions.busy), dus /beheer laat ze zien
    state: Literal["pending", "open", "saving"] | None = None


class OfficeUnsaved(BaseModel):
    key: str
    name: str
    owner: str | None = None
    at: str | None = None
    reason: str | None = None


class OfficeConversions(BaseModel):
    queued: int
    failed: int
    unsafe: int
    ask: int


class OfficeAdmin(BaseModel):
    """GET en PUT /api/v1/admin/office"""

    configured: bool
    enabled: bool
    state: OfficeHealth
    down_since: str | None = None
    version: str | None = None
    # ≥ 2147483647 = geen limiet
    license_connections: int | None = None
    tokens_enforced: bool | None = None
    max_editors: int
    # De andere twee waarden van OfficeConfig, zodat het formulier op /beheer ze kan tonen
    checkpoint_minutes: int = 5
    version_minutes: int = 30
    connections: int
    people: int
    documents: int
    peak: OfficePeak | None = None
    open: list[OfficeOpenDocument] = []
    unsaved: list[OfficeUnsaved] = []
    conversions: OfficeConversions


class OfficeAdminIn(BaseModel):
    enabled: bool
    max_editors: int = Field(ge=1, le=500)
    checkpoint_minutes: int = Field(ge=1, le=60)
    version_minutes: int = Field(ge=5, le=1440)


class CheckpointOut(BaseModel):
    """POST /api/v1/admin/office/checkpoint"""

    sent: int
    saved: int


class RetryOut(BaseModel):
    """POST /api/v1/admin/office/conversions/retry"""

    queued: int


# POST /api/v1/admin/office/unsaved/{key}/restore -> NodeOut
# DELETE /api/v1/admin/office/unsaved/{key} -> 204


# --- Gebeurtenissen (SSE, na de commit; ontvangers bepaald bij het versturen) ---

UNSAVED = "office.unsaved"
RECOVERED = "office.recovered"
DROPPED = "office.dropped"
PRESENCE = "office.presence"


class UnsavedEvent(BaseModel):
    """Naar de eigenaar en de deelnemers die nu nog schrijfrecht hebben."""

    node_id: uuid.UUID | None
    name: str
    reason: Literal["no_room", "too_large", "owner_gone", "error_status"]
    final: bool


class RecoveredEvent(BaseModel):
    """Naar wie de kopie nu kan zien (de eigenaar en access.watchers); nooit naar wie eruit
    gezet is."""

    node_id: uuid.UUID | None
    copy_id: uuid.UUID
    name: str
    reason: Literal["status3", "no_writer", "forgotten"]


class DroppedEvent(BaseModel):
    """Alleen naar wie eruit gezet is, of (stale) wiens editor een oude key had. Zonder
    naam."""

    node_id: uuid.UUID
    key: str
    reason: Literal["access", "stale"]


class PresenceEvent(BaseModel):
    """Na elke status 1, naar de deelnemers die nu schrijfrecht hebben."""

    node_id: uuid.UUID
    count: int
