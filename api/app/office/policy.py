"""De regels van bewerken als pure functies, zonder database (Keuze 15, 16 en 17).
O0-stub; O2 maakt het af, met tabeltests.

make_version(row, current_sha, now, cfg) -> bool; choose_actor(history, actions, members,
banned, can_write) -> UUID | None; target(node_alive, owner_alive, actor) -> "original" |
"copy" | "nothing"; late(row, event) -> "ignore" | "save" | "drop"; recovery_name(name,
when, locale) -> str.
"""

import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Literal

from app.models import OfficeSession
from app.office.engine import CallbackEvent
from app.office.state import OfficeConfig


def make_version(row: OfficeSession, current_sha: str, now: datetime, cfg: OfficeConfig) -> bool:
    raise NotImplementedError("O2")


def choose_actor(
    history: Sequence[str],
    actions: Sequence[str],
    members: Mapping[str, str],
    banned: Sequence[str],
    can_write: Callable[[uuid.UUID], bool],
) -> uuid.UUID | None:
    raise NotImplementedError("O2")


def target(
    node_alive: bool, owner_alive: bool, actor: uuid.UUID | None
) -> Literal["original", "copy", "nothing"]:
    raise NotImplementedError("O2")


def late(row: OfficeSession, event: CallbackEvent) -> Literal["ignore", "save", "drop"]:
    raise NotImplementedError("O2")


def recovery_name(name: str, when: datetime, locale: str | None) -> str:
    raise NotImplementedError("O2")
