"""De naad tussen de kern van office en een document server (Keuze 3).

De kern (sessies, opslaan, herstel, proxy, tunnel, conversie) kent alleen wat hier staat. De
adapter voor OnlyOffice staat in app/office/oo/; een tweede editor (Collabora) krijgt later
een eigen adapter met dezelfde vorm. Buiten app/office/oo/ importeert alleen dit bestand de
adapter (tests/test_office_imports.py); de rest vraagt current().
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Literal, Protocol

from app.office import runtime

if TYPE_CHECKING:
    import httpx


class PathClass(StrEnum):
    """Soorten paden onder /office/ (§3.10). Wat geen klasse heeft, wordt geweigerd."""

    STATIC = "static"  # scripts, stijlen, fonts: zonder cookie
    PAGE = "page"  # elke *.html (de editorpagina): cookie, Sec-Fetch-Dest
    DATA = "data"  # documentdata van één key: cookie, lid, recht van nu
    SESSION = "session"  # de socket van één key (polling en de tunnel)
    SETTINGS = "settings"  # plugins.json, themes.json: lid van een open sessie
    ACTION = "action"  # editoracties (downloaden, afdrukken, afbeelding invoegen)


@dataclass(frozen=True)
class Health:
    ok: bool
    version: str | None = None
    limit: int | None = None  # gelijktijdige verbindingen volgens de licentie


@dataclass(frozen=True)
class OpenContext:
    """Alles wat de adapter nodig heeft om een editorconfig te maken."""

    key: str
    ds_uid: str
    user_name: str
    title: str
    file_type: str
    lang: str
    region: str
    theme: Literal["light", "dark"]
    mobile: bool
    doc_url: str
    callback_url: str
    close_text: str | None


@dataclass(frozen=True)
class CallbackEvent:
    """Een callback van de editor, los van OnlyOffice.

    kind: presence (iemand opent of sluit), final (de laatste is weg, er is een opslag),
    failed (de editor kon niet opslaan; url kan een herstelbare versie zijn), closed (dicht
    zonder wijzigingen), checkpoint (tussentijds opgeslagen), checkpoint_failed, other."""

    kind: Literal[
        "presence", "final", "failed", "closed", "checkpoint", "checkpoint_failed", "other"
    ]
    key: str
    url: str | None = None  # pad en query, nog niet herschreven
    connected: list[str] = field(default_factory=list)  # ds_uid's
    editors: list[str] = field(default_factory=list)  # ds_uid's, de laatste bewerker eerst
    explicit: bool = False  # opslaan op verzoek van een gebruiker
    filetype: str | None = None
    mark: str | None = None  # volgorde van de opslag, sorteerbaar


class StreamGuard(Protocol):
    """Eén per tunnel; leest alleen het begin van de stroom van de browser."""

    def feed(self, data: bytes) -> Literal["more", "ok", "deny"]: ...


class TokenError(Exception):
    """Een token van of voor de editor klopt niet: handtekening, exp, vorm of operation."""


class ConvertError(Exception):
    """Een conversie mislukte. kind zegt wat er nu moet gebeuren (Keuze 25):
    new_key (opnieuw met een nieuwe key), failed (blijvend), none (dit formaat kan niet),
    retry (later opnieuw), misconfigured (DS eist geen of een ander token)."""

    def __init__(
        self,
        code: int,
        kind: Literal["new_key", "failed", "none", "retry", "misconfigured"],
    ) -> None:
        super().__init__(f"conversie mislukt: {code} ({kind})")
        self.code = code
        self.kind = kind


class Engine(Protocol):
    name: str
    strip_prefix: bool  # OnlyOffice: ja; Collabora: nee
    edit_formats: frozenset[str]
    convert_formats: frozenset[str]

    def classify(self, method: str, path: str, upgrade: bool) -> PathClass | None: ...

    def session_key(self, path: str) -> str | None:
        """De key in een pad van een sessie, documentdata of editoractie."""
        ...

    def stream_guard(self, key: str) -> StreamGuard: ...

    def editor_config(self, ctx: OpenContext) -> dict:
        """De editorconfig, getekend, met exp."""
        ...

    def read_callback(self, body: bytes, authorization: str | None) -> CallbackEvent:
        """TokenError als het token niet klopt."""
        ...

    def callback_reply(self, outcome: str) -> tuple[int, dict]:
        """saved | same | copy | ignored | retry | refused -> (status, body)."""
        ...

    def check_download(self, authorization: str | None, url: str) -> None:
        """TokenError als het download-token niet bij dit adres hoort."""
        ...

    async def health(self, deep: bool = True) -> Health:
        """deep=False: alleen of hij leeft; anders ook versie en licentie. TokenError als de
        editor ons getekende verzoek weigert: dan klopt het office-geheim niet met dat van de
        editor (een fout in de instellingen, geen uitval)."""
        ...

    async def enforces_tokens(self) -> bool:
        """Zonder token: version geeft 6 en een conversie -8 (Keuze 28)."""
        ...

    async def present(self, key: str) -> list[str] | None:
        """De verbonden ds_uid's; None als de editor de key niet kent."""
        ...

    async def checkpoint(
        self, key: str, userdata: str
    ) -> Literal["sent", "no_changes", "unknown"]: ...

    async def drop(self, key: str, ds_uids: Sequence[str] | None) -> None:
        """None: iedereen eruit."""
        ...

    async def convert(
        self, src_url: str, ext: str, key: str, title: str, out: Path, limit: int
    ) -> None:
        """ConvertError bij een fout."""
        ...

    async def forgotten(self) -> list[str]: ...

    async def fetch_forgotten(self, key: str, out: Path, limit: int) -> tuple[str, int]:
        """(sha, grootte) van wat in out staat."""
        ...

    async def delete_forgotten(self, key: str) -> None: ...


_cached: tuple[object, Engine] | None = None


def current() -> Engine | None:
    """De editor van deze app, of None als office niet ingesteld is."""
    global _cached
    settings = runtime.settings()
    if not settings.office_configured:
        return None
    if _cached is None or _cached[0] is not settings:
        from app.office.oo import OnlyOffice  # de enige import van de adapter buiten oo/

        _cached = (settings, OnlyOffice(settings))
    return _cached[1]


class Upstream(Protocol):
    """De http-verbinding met de editor, voor de proxy (gestreamd; de aanroeper sluit). open
    stuurt path byte voor byte, of geeft ValueError als dat niet kan."""

    async def open(
        self, method: str, path: str, headers: list[tuple[str, str]], content=None
    ) -> "httpx.Response": ...


def upstream() -> Upstream | None:
    """De http-client naar de editor (bij OnlyOffice: oo.client.DocServer), of None zonder
    office. Zo hoeft de proxy de adapter niet te importeren (regel 4)."""
    if current() is None:
        return None
    from app.office.oo import client

    return client.get()


async def close() -> None:
    """Sluit wat de adapter open heeft (de http-client), bij het stoppen van de app."""
    from app.office.oo import client

    await client.close()
    reset()


def reset() -> None:
    """Vergeet de editor (na andere instellingen; tests)."""
    global _cached
    _cached = None
