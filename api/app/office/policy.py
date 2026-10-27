"""De regels van bewerken als pure functies, zonder database (Keuze 15, 16 en 17).

make_version(row, current_sha, now, cfg) -> bool: wordt de huidige inhoud een versie?
choose_actor(history, actions, members, banned, can_write) -> UUID | None: wie slaat op?
target(node_alive, owner_alive, actor) -> "original" | "copy" | "nothing": waarheen?
late(row, event) -> "ignore" | "save" | "drop": een callback op een dichte sessie.
recovery_name(name, when, locale) -> str: de naam van een herstelkopie.

Alles hier is te testen met tabellen (test_office_policy.py); de rest van office roept het aan
met wat het uit de database las.
"""

import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Literal

from app.models import OfficeSession
from app.office.engine import CallbackEvent
from app.office.state import OfficeConfig

# Hooguit zoveel versies maakt één sessie (Keuze 17): met MAX_VERSIONS = 20 blijft de inhoud van
# vóór de sessie dan altijd staan
MAX_PER_SESSION = 10
# De langste naam van een bestand (routers/files.py MAX_NAME)
MAX_NAME = 255

# Waarom een sessie dicht is (OfficeSession.closed_reason)
OWN_FINAL = frozenset({"saved", "no_changes", "copy", "refused"})
SAVES = frozenset({"final", "failed", "checkpoint"})


def _utc(value: datetime) -> datetime:
    """SQLite geeft tijden zonder tijdzone terug; ze staan altijd in UTC."""
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def make_version(row: OfficeSession, current_sha: str, now: datetime, cfg: OfficeConfig) -> bool:
    """Wordt de inhoud die er nu staat (current_sha) een versie als deze opslag hem vervangt?

    - de eerste opslag van de sessie: ja, zo blijft de inhoud van vóór de sessie bewaard;
    - de huidige inhoud komt niet van deze sessie (een upload of "versie terugzetten"
      ertussen): ja, die mag nooit wegvallen;
    - anders is het de vorige tussenstand van deze sessie: alleen een versie als de laatste
      versie van deze sessie ouder is dan version_minutes, en hooguit MAX_PER_SESSION keer."""
    if row.checkpoint is None or current_sha != row.checkpoint:
        return True
    if (row.versions_made or 0) >= MAX_PER_SESSION:
        return False
    if row.versioned_at is None:
        return True
    return _utc(now) - _utc(row.versioned_at) >= timedelta(minutes=cfg.version_minutes)


def choose_actor(
    history: Sequence[str],
    actions: Sequence[str],
    members: Mapping[str, str],
    banned: Sequence[str],
    can_write: Callable[[uuid.UUID], bool],
) -> uuid.UUID | None:
    """De eerste uit [de laatste bewerkers, de gebruikers uit actions, de rest van members] die
    lid is, niet in banned staat en nu nog schrijfrecht heeft (can_write). Allemaal als ds_uid;
    het antwoord is de echte gebruiker. Niemand: None (dan een herstelkopie)."""
    out = set(banned)
    seen: set[str] = set()
    for ds_uid in [*history, *actions, *members]:
        if ds_uid in seen:
            continue
        seen.add(ds_uid)
        if ds_uid in out or ds_uid not in members:
            continue
        try:
            user_id = uuid.UUID(str(members[ds_uid]))
        except ValueError:
            continue
        if can_write(user_id):
            return user_id
    return None


def target(
    node_alive: bool, owner_alive: bool, actor: uuid.UUID | None
) -> Literal["original", "copy", "nothing"]:
    """Bestand of eigenaar definitief weg: niets (de eigenaar wilde het weg). Niemand meer met
    schrijfrecht: een herstelkopie bij de eigenaar. Anders het origineel."""
    if not node_alive or not owner_alive:
        return "nothing"
    if actor is None:
        return "copy"
    return "original"


def late(row: OfficeSession, event: CallbackEvent) -> Literal["ignore", "save", "drop"]:
    """Een callback op een dichte sessie (de tabel van Keuze 15):

    - status 1: die gebruikers eruit (drop), zodat hun editor opnieuw laadt met de key van nu;
    - status 2, 3 of 6 met een adres op een sessie die als verloren (lost) dichtging: een late
      opslag (op het origineel als het bestand niet veranderde, anders een herstelkopie);
    - al het andere: {"error": 0} en niets schrijven (een late herhaling, of het bestand is
      weg)."""
    if event.kind == "presence":
        return "drop" if event.connected else "ignore"
    if row.closed_reason == "lost" and event.kind in SAVES and event.url:
        return "save"
    return "ignore"


RECOVERED = {"nl": "hersteld", "en": "recovered"}


def recovery_name(name: str, when: datetime, locale: str | None) -> str:
    """De naam "<naam> (hersteld <datum tijd>).<ext>", in de taal van de eigenaar (zonder taal:
    Nederlands). when is al in de tijdzone van de server. Geen dubbele punt in de tijd: die mag
    op Windows niet in een bestandsnaam. Een lange naam wordt ingekort tot hij past."""
    word = RECOVERED.get((locale or "nl")[:2].lower(), RECOVERED["nl"])
    stem, dot, ext = name.rpartition(".")
    if not stem or len(ext) > 16:
        stem, dot, ext = name, "", ""
    tail = f" ({word} {when:%Y-%m-%d %H.%M}){dot}{ext}"
    return stem[: MAX_NAME - len(tail)].rstrip() + tail
