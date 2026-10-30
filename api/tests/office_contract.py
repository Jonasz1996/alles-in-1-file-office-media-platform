"""De contracttests van office (§4.4): scenario's tegen een klein testprotocol, los van
OnlyOffice. O0 schrijft ze, O2 laat ze slagen (tests/test_office_contract.py).

Dit bestand wordt niet zelf verzameld (het heet niet test_*.py). test_office_contract.py
maakt per editor een Driver (nu één: de nep-DS van OnlyOffice) en een App (de kant van het
platform: gebruikers, bestanden, delingen, de klok en de ronde), en draait elk scenario uit
SCENARIOS met elk paar:

    @pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.__name__)
    async def test_contract(scenario, driver, app):
        await scenario(driver, app)

`pytest -k contract` draait ze dan voor elke Driver. Een tweede editor (Collabora) krijgt
later een eigen Driver en dezelfde scenario's: zo is "de terugval is echt" een test.

Afspraken voor elke Driver:
- open(user, node) opent zoals de web-app (sessie bij de app, editor laden, verbinden). Weigert
  de app, dan Refused met de status en de code. Handle.key is document.key.
- type(h, text) zet text als eigen alinea van die gebruiker in het document; de opgeslagen
  versie bevat de alinea's van iedereen (de nep-DS: één alinea per gebruiker).
- save(h, explicit=True) is de knop Opslaan (in de editor of "Nu opslaan"); explicit=False is
  een tussenstand van de klok van de app (Keuze 18). Beide wachten niet op het resultaat: dat
  doet App.settle().
- close(h) sluit de editor zoals een tabblad dat dichtgaat; de laatste die sluit, laat de
  editor opslaan (status 2).
- reconnect(h) verbindt opnieuw zoals een browser na een breuk; False als het geweigerd wordt.
- fail_callbacks(n): de app geeft de volgende n callbacks 503 (een testhaak in de app). Met
  GIVE_UP geeft de editor het op en bewaart hij het document als forgotten file. Een herstart
  van de app vergeet de haak.
- replay_refused() stuurt de laatste callback die fail_callbacks weigerde nog één keer, precies
  zoals de editor hem stuurde, en geeft de HTTP-status van de app. Zo komt een oude opslag ná
  een nieuwere aan (bij een echte editor: de opgenomen body en kop opnieuw posten).
- crash(): de editor stopt hard (kill, niets opgeslagen) en start daarna leeg weer op, zoals
  een container die herstart. Wat niet opgeslagen was, is weg.
- restart_app(): de app stopt en start (dezelfde database en blobs; geheugen leeg).
"""

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

# Meer callbacks dan een editor ooit probeert (OnlyOffice: 8, Keuze 29)
GIVE_UP = 100
RECOVERED = " (hersteld "  # zo begint het achtervoegsel van een herstelkopie (Keuze 16)


class Refused(Exception):
    """De app weigert het openen of verbinden."""

    def __init__(self, status: int, code: str | None = None) -> None:
        super().__init__(f"{status} {code}")
        self.status = status
        self.code = code


@dataclass
class Handle:
    """Eén geopende editor van één gebruiker."""

    user: Any
    node_id: uuid.UUID
    key: str
    state: Any = None  # van de Driver zelf (bij de nep-DS: zijn EditorClient)
    extra: dict = field(default_factory=dict)


class Driver(Protocol):
    async def open(self, user: Any, node: uuid.UUID) -> Handle: ...

    async def type(self, h: Handle, text: str) -> None: ...

    async def save(self, h: Handle, explicit: bool) -> None: ...

    async def close(self, h: Handle) -> None: ...

    async def reconnect(self, h: Handle) -> bool: ...

    async def fail_callbacks(self, n: int) -> None: ...

    async def replay_refused(self) -> int: ...

    async def crash(self) -> None: ...

    async def restart_app(self) -> None: ...


@dataclass(frozen=True)
class NodeView:
    name: str
    parent_id: uuid.UUID | None
    trashed: bool


@dataclass(frozen=True)
class SessionView:
    state: str  # pending | open | saving | closed
    closed_reason: str | None
    versions_made: int


class App(Protocol):
    """De kant van het platform, zonder editor. O2 maakt hem met de testclient en de
    database; alles gaat via de gewone routes waar die er zijn."""

    async def user(self, email: str) -> Any: ...

    async def folder(self, owner: Any, name: str, parent: uuid.UUID | None = None) -> uuid.UUID: ...

    async def docx(
        self, owner: Any, folder: uuid.UUID | None, name: str, paragraphs: Sequence[str]
    ) -> uuid.UUID:
        """Een docx met deze alinea's (folder None: de hoofdmap)."""
        ...

    async def share(self, owner: Any, folder: uuid.UUID, other: Any, write: bool = True) -> Any:
        """Een deling van folder met other; geeft iets terug voor revoke."""
        ...

    async def revoke(self, owner: Any, share: Any) -> None: ...

    async def rename(self, user: Any, node: uuid.UUID, name: str) -> None: ...

    async def move(self, user: Any, node: uuid.UUID, folder: uuid.UUID) -> None: ...

    async def trash(self, user: Any, node: uuid.UUID) -> None: ...

    async def purge(self, user: Any, node: uuid.UUID) -> None:
        """Definitief verwijderen (prullenbak en daarna leegmaken)."""
        ...

    async def upload(self, user: Any, node: uuid.UUID, paragraphs: Sequence[str]) -> None:
        """Nieuwe inhoud via een gewone upload over het bestand heen (vervangen)."""
        ...

    async def room(self, user: Any, free_bytes: int | None) -> None:
        """Het quotum zo zetten dat er nog free_bytes vrij is (None: geen grens)."""
        ...

    async def paragraphs(self, node: uuid.UUID) -> list[str]:
        """De alinea's van de huidige inhoud."""
        ...

    async def version_count(self, node: uuid.UUID) -> int: ...

    async def node(self, node: uuid.UUID) -> NodeView | None:
        """None als het bestand definitief weg is."""
        ...

    async def find(self, owner: Any, prefix: str) -> list[uuid.UUID]:
        """De bestanden van owner (ook in de prullenbak) waarvan de naam zo begint."""
        ...

    async def session(self, key: str) -> SessionView | None: ...

    async def set_session(self, key: str, **fields: Any) -> None:
        """Testhaak: velden van de rij zetten, bijvoorbeeld state="saving"."""
        ...

    async def events(self, user: Any) -> list[str]:
        """De soorten gebeurtenissen die user kreeg, in volgorde (office.recovered, ...)."""
        ...

    async def advance(self, minutes: float) -> None:
        """De klok van de app vooruit (versies na 30 minuten, de ronde, het herstel)."""
        ...

    async def sweep(self) -> None:
        """Eén ronde (Keuze 20), en wachten tot wat hij in gang zette klaar is."""
        ...

    async def recover(self) -> None:
        """Eén ronde van het herstel van forgotten files (Keuze 21)."""
        ...

    async def settle(self) -> None:
        """Wachten tot de editor en de app klaar zijn: geen callbacks of downloads meer
        onderweg, ook geen nieuwe pogingen van de editor."""
        ...


Scenario = Callable[[Driver, App], Awaitable[None]]


async def _one_file(app: App, paragraphs: Sequence[str] = ("Begin",), name="verslag.docx"):
    anna = await app.user("anna@example.nl")
    node = await app.docx(anna, None, name, paragraphs)
    return anna, node


# --- de scenario's ---


async def samen_bewerken(driver: Driver, app: App) -> None:
    """Twee gebruikers, één key, beide teksten in de opgeslagen versie; één nieuwe versie."""
    anna = await app.user("anna@example.nl")
    bram = await app.user("bram@example.nl")
    folder = await app.folder(anna, "Samen")
    await app.share(anna, folder, bram, write=True)
    node = await app.docx(anna, folder, "verslag.docx", ["Begin"])
    before = await app.version_count(node)
    a = await driver.open(anna, node)
    b = await driver.open(bram, node)
    assert a.key == b.key
    await driver.type(a, "Anna was hier")
    await driver.type(b, "Bram ook")
    await driver.close(a)
    await driver.close(b)
    await app.settle()
    text = await app.paragraphs(node)
    assert {"Begin", "Anna was hier", "Bram ook"} <= set(text)
    assert await app.version_count(node) == before + 1
    session = await app.session(a.key)
    assert session is not None and session.state == "closed"
    again = await driver.open(anna, node)
    assert again.key != a.key  # na het sluiten altijd een nieuwe key
    await driver.close(again)
    await app.settle()


async def tussenstanden_en_versies(driver: Driver, app: App) -> None:
    """De eerste opslag maakt van de inhoud van vóór de sessie een versie; daarna binnen 30
    minuten geen extra versie, na 30 minuten wel (Keuze 17)."""
    anna, node = await _one_file(app)
    v0 = await app.version_count(node)
    h = await driver.open(anna, node)
    await driver.type(h, "een")
    await driver.save(h, explicit=True)
    await app.settle()
    assert await app.version_count(node) == v0 + 1
    assert "een" in await app.paragraphs(node)
    await driver.type(h, "twee")
    await driver.save(h, explicit=False)
    await app.settle()
    assert await app.version_count(node) == v0 + 1  # vervangen, geen versie
    assert "twee" in await app.paragraphs(node)
    await app.advance(31)
    await driver.type(h, "drie")
    await driver.save(h, explicit=False)
    await app.settle()
    assert await app.version_count(node) == v0 + 2
    await driver.close(h)
    await app.settle()
    assert await app.version_count(node) == v0 + 2  # de laatste opslag binnen 30 minuten
    assert "drie" in await app.paragraphs(node)


async def hooguit_tien_versies(driver: Driver, app: App) -> None:
    """Na 10 versies in één sessie komen er geen meer bij; de inhoud wordt wel bijgewerkt."""
    anna, node = await _one_file(app)
    v0 = await app.version_count(node)
    h = await driver.open(anna, node)
    for n in range(1, 13):
        await app.advance(31)
        await driver.type(h, f"ronde {n}")
        await driver.save(h, explicit=False)
        await app.settle()
        assert await app.version_count(node) == v0 + min(n, 10), n
        assert f"ronde {n}" in await app.paragraphs(node)
    await driver.close(h)
    await app.settle()
    assert await app.version_count(node) == v0 + 10
    session = await app.session(h.key)
    assert session is not None and session.versions_made == 10


async def hernoemen_en_verplaatsen(driver: Driver, app: App) -> None:
    """Opgeslagen in het hernoemde en verplaatste bestand, niet in een nieuw (exact)."""
    anna = await app.user("anna@example.nl")
    work = await app.folder(anna, "Werk")
    archive = await app.folder(anna, "Archief")
    node = await app.docx(anna, work, "verslag.docx", ["Begin"])
    h = await driver.open(anna, node)
    await driver.type(h, "na hernoemen")
    await app.rename(anna, node, "ander.docx")
    await app.move(anna, node, archive)
    await app.sweep()  # de ronde laat de eigenaar gewoon zitten
    await driver.close(h)
    await app.settle()
    view = await app.node(node)
    assert view == NodeView(name="ander.docx", parent_id=archive, trashed=False)
    assert "na hernoemen" in await app.paragraphs(node)
    assert await app.find(anna, "verslag") == []  # geen tweede bestand


async def prullenbak(driver: Driver, app: App) -> None:
    """De eigenaar zet het in de prullenbak terwijl hij bewerkt: het werk staat in het bestand
    in de prullenbak. Een nieuwe sessie op dat bestand: 422 trashed."""
    anna, node = await _one_file(app)
    h = await driver.open(anna, node)
    await driver.type(h, "in de prullenbak")
    await app.trash(anna, node)
    await app.sweep()
    await driver.close(h)
    await app.settle()
    view = await app.node(node)
    assert view is not None and view.trashed
    assert "in de prullenbak" in await app.paragraphs(node)
    try:
        await driver.open(anna, node)
    except Refused as refused:
        assert (refused.status, refused.code) == (422, "trashed")
    else:
        raise AssertionError("openen in de prullenbak hoort geweigerd te worden")


async def upload_ertussen(driver: Driver, app: App) -> None:
    """Een upload terwijl de editor open is: de editor wint, de upload blijft als versie."""
    anna, node = await _one_file(app)
    v0 = await app.version_count(node)
    h = await driver.open(anna, node)
    await driver.type(h, "een")
    await driver.save(h, explicit=False)
    await app.settle()
    assert await app.version_count(node) == v0 + 1
    await app.upload(anna, node, ["van buiten"])
    assert await app.version_count(node) == v0 + 2  # de tussenstand werd een versie
    await driver.type(h, "uit de editor")
    await driver.close(h)
    await app.settle()
    text = await app.paragraphs(node)
    assert "uit de editor" in text and "van buiten" not in text
    assert await app.version_count(node) == v0 + 3  # squash past niet: de upload blijft


async def quotum_vol(driver: Driver, app: App) -> None:
    """Vol quotum: de tussenstand schrijft niets (melding); de laatste opslag ook niet, en ook
    geen kopie: een forgotten file. Met weer ruimte zet het herstel hem op het origineel."""
    anna, node = await _one_file(app)
    before = await app.paragraphs(node)
    h = await driver.open(anna, node)
    await driver.type(h, "past niet meer")
    await app.room(anna, 0)
    await driver.save(h, explicit=False)
    await app.settle()
    assert await app.paragraphs(node) == before
    assert "office.unsaved" in await app.events(anna)
    await driver.close(h)
    await app.settle()
    assert await app.paragraphs(node) == before
    assert await app.find(anna, "verslag" + RECOVERED) == []
    await app.room(anna, None)
    await app.recover()
    assert "past niet meer" in await app.paragraphs(node)
    assert await app.find(anna, "verslag" + RECOVERED) == []


async def definitief_verwijderd(driver: Driver, app: App) -> None:
    """Definitief weg: niets geschreven, geen kopie, de sessie dicht met gone."""
    anna, node = await _one_file(app)
    h = await driver.open(anna, node)
    await driver.type(h, "voor niets")
    await app.purge(anna, node)
    await app.sweep()
    await driver.close(h)
    await app.settle()
    assert await app.node(node) is None
    assert await app.find(anna, "verslag") == []
    session = await app.session(h.key)
    assert session is not None and session.state == "closed"
    assert session.closed_reason == "gone"
    await app.recover()
    assert await app.find(anna, "verslag") == []  # ook het herstel maakt niets


async def deling_ingetrokken(driver: Driver, app: App) -> None:
    """Alleen Bram bewerkt, de deling gaat eraf: hij is eruit en komt er niet meer in. Zijn
    werk wordt een herstelkopie bij Anna; het origineel blijft. Bram krijgt alleen
    office.dropped."""
    anna = await app.user("anna@example.nl")
    bram = await app.user("bram@example.nl")
    folder = await app.folder(anna, "Samen")
    share = await app.share(anna, folder, bram, write=True)
    node = await app.docx(anna, folder, "verslag.docx", ["Begin"])
    before = await app.paragraphs(node)
    h = await driver.open(bram, node)
    await driver.type(h, "Bram typte")
    await app.revoke(anna, share)
    await app.sweep()
    await app.settle()
    assert await driver.reconnect(h) is False
    await app.sweep()
    await app.settle()
    assert await app.paragraphs(node) == before
    copies = await app.find(anna, "verslag" + RECOVERED)
    assert len(copies) == 1
    assert "Bram typte" in await app.paragraphs(copies[0])
    copy = await app.node(copies[0])
    assert copy is not None and copy.parent_id != folder  # in de hoofdmap van Anna
    bram_events = await app.events(bram)
    assert "office.dropped" in bram_events and "office.recovered" not in bram_events
    assert "office.recovered" in await app.events(anna)


async def oude_opslag_na_de_laatste(driver: Driver, app: App) -> None:
    """De eerste tussenstand mislukt eerst en komt na de laatste opslag nog eens: de laatste
    blijft, en er komt geen versie bij."""
    anna, node = await _one_file(app)
    h = await driver.open(anna, node)
    await driver.type(h, "eerste")
    await driver.fail_callbacks(1)
    await driver.save(h, explicit=False)
    await driver.type(h, "laatste")
    await driver.close(h)
    await app.settle()
    text = await app.paragraphs(node)
    assert "laatste" in text
    session = await app.session(h.key)
    assert session is not None and session.state == "closed"
    versions = await app.version_count(node)
    # De geweigerde tussenstand komt nu pas, na de laatste opslag (de editor hield hem vast)
    assert await driver.replay_refused() == 200
    await app.settle()
    assert "laatste" in await app.paragraphs(node)
    assert await app.version_count(node) == versions
    await app.recover()
    assert "laatste" in await app.paragraphs(node)


async def app_weg_tijdens_opslaan(driver: Driver, app: App) -> None:
    """De app is weg terwijl de editor de laatste opslag stuurt. Wat er ook gebeurt (de editor
    probeert opnieuw, of geeft het op en bewaart een forgotten file): het werk staat daarna op
    het origineel, één keer, en de volgende opener krijgt een nieuwe key."""
    anna, node = await _one_file(app)
    v0 = await app.version_count(node)
    h = await driver.open(anna, node)
    await driver.type(h, "voor de herstart")
    await driver.fail_callbacks(GIVE_UP)
    await driver.close(h)
    await driver.restart_app()
    await app.settle()
    await app.advance(7)  # saving ouder dan 6 minuten: de ronde vraagt info
    await app.sweep()
    await app.recover()
    assert "voor de herstart" in await app.paragraphs(node)
    assert await app.version_count(node) == v0 + 1
    assert await app.find(anna, "verslag" + RECOVERED) == []
    session = await app.session(h.key)
    assert session is not None and session.state == "closed"
    again = await driver.open(anna, node)
    assert again.key != h.key
    await driver.close(again)
    await app.settle()


async def hangend_saving(driver: Driver, app: App) -> None:
    """Een sessie die in saving blijft hangen (de app viel om na stap 3 van Keuze 16) en
    waarvan de editor het opgaf: de ronde sluit hem (lost), het herstel zet het werk op het
    origineel, en de volgende opener krijgt een nieuwe key."""
    anna, node = await _one_file(app)
    h = await driver.open(anna, node)
    await driver.type(h, "bleef hangen")
    await driver.fail_callbacks(GIVE_UP)
    await driver.close(h)
    await app.settle()
    await app.set_session(h.key, state="saving")
    await driver.restart_app()  # vergeet ook de testhaak
    await app.advance(7)
    await app.sweep()
    session = await app.session(h.key)
    assert session is not None and session.state == "closed"
    await app.recover()
    assert "bleef hangen" in await app.paragraphs(node)
    again = await driver.open(anna, node)
    assert again.key != h.key
    await driver.close(again)
    await app.settle()


async def crash(driver: Driver, app: App) -> None:
    """De editor crasht: wat na de laatste tussenstand kwam is weg, de tussenstand niet. De
    ronde merkt dat de editor de key niet meer kent; de volgende opener krijgt een nieuwe."""
    anna, node = await _one_file(app)
    h = await driver.open(anna, node)
    await driver.type(h, "veilig")
    await driver.save(h, explicit=False)
    await app.settle()
    await driver.type(h, "verloren")
    await driver.crash()
    await app.settle()
    await app.advance(3)  # info per open key elke 2 minuten
    await app.sweep()
    text = await app.paragraphs(node)
    assert "veilig" in text and "verloren" not in text
    session = await app.session(h.key)
    assert session is not None and session.state == "closed"
    assert session.closed_reason == "lost"
    again = await driver.open(anna, node)
    assert again.key != h.key
    await driver.close(again)
    await app.settle()


SCENARIOS: list[Scenario] = [
    samen_bewerken,
    tussenstanden_en_versies,
    hooguit_tien_versies,
    hernoemen_en_verplaatsen,
    prullenbak,
    upload_ertussen,
    quotum_vol,
    definitief_verwijderd,
    deling_ingetrokken,
    oude_opslag_na_de_laatste,
    app_weg_tijdens_opslaan,
    hangend_saving,
    crash,
]
