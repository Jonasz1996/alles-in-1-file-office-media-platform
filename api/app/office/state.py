"""De toestand van office in het geheugen (op app.state.office; er draait één proces).

health.py schrijft health, version, license_connections en tokens_enforced; de tunnel (O1)
houdt tunnels en peak bij; /beheer (O5) leest alleen. node_lock(node_id) is het slot per
bestand: altijd vóór user_lock en blobs.lock (regel 6 van §2.0), en nooit vasthouden tijdens
een aanroep naar DS, behalve het downloaden in de callback.

OfficeConfig staat in de tabel setting (sleutel "office") en is op /beheer aan te passen
(O5); hij staat in het geheugen zoals SecurityPolicy, zodat een verzoek er geen query voor
kost.
"""

import asyncio
import uuid
import weakref
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Setting

OFFICE_KEY = "office"

Health = Literal["off", "starting", "ok", "down", "misconfigured"]


class OfficeConfig(BaseModel):
    enabled: bool = True
    # Hooguit zoveel editors tegelijk op één document (Keuze 27)
    max_editors: int = Field(default=20, ge=1, le=500)
    # Tussentijds opslaan na zoveel minuten (Keuze 18)
    checkpoint_minutes: int = Field(default=5, ge=1, le=60)
    # Een opslag maakt een nieuwe versie als de vorige versie van deze sessie zo oud is
    # (Keuze 17)
    version_minutes: int = Field(default=30, ge=5, le=1440)


@dataclass
class OfficeState:
    health: Health = "off"
    down_since: datetime | None = None
    version: str | None = None
    license_connections: int | None = None
    tokens_enforced: bool | None = None
    # key -> gebruiker -> aantal open tunnels (O1)
    tunnels: dict[str, dict[uuid.UUID, int]] = field(default_factory=dict)
    # (verbindingen, wanneer): het hoogste aantal sinds de start (O1, O5)
    peak: tuple[int, datetime] | None = None
    # Waarom office niet ingesteld is (Settings.office_problem), voor het log en /beheer
    problem: str | None = None
    # Wanneer health.py voor het laatst version, license en de tokencontrole deed
    deep_checked_at: datetime | None = None
    # Sinds wanneer health.py kijkt, en of DS sindsdien al eens antwoordde (gezond of
    # misconfigured); zo niet, en nog geen STARTING_SECONDS, dan is het "starting"
    checking_since: datetime | None = None
    ever_ok: bool = False
    # De taken van de lifespan (health, sweep, recovery), om bij het stoppen te annuleren
    tasks: list[asyncio.Task] = field(default_factory=list)
    _locks: weakref.WeakValueDictionary = field(default_factory=weakref.WeakValueDictionary)

    def node_lock(self, node_id: uuid.UUID) -> asyncio.Lock:
        """Het slot per bestand. Zolang iemand het vasthoudt of erop wacht, is het hetzelfde
        object; daarna mag het weg."""
        lock = self._locks.get(node_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[node_id] = lock
        return lock

    @property
    def healthy(self) -> bool:
        return self.health == "ok"


def office_state(app) -> OfficeState:
    """De OfficeState van deze app; zonder office een die "off" zegt."""
    state = getattr(app.state, "office", None)
    if state is None:
        state = app.state.office = OfficeState()
    return state


class OfficePolicy:
    """OfficeConfig uit de tabel setting, in het geheugen (zoals SecurityPolicy)."""

    def __init__(self, config: OfficeConfig | None = None):
        self.config = config  # None = nog niet uit de database gelezen
        self._lock = asyncio.Lock()

    async def get(self, session: AsyncSession) -> OfficeConfig:
        if self.config is None:
            async with self._lock:
                if self.config is None:
                    row = await session.get(Setting, OFFICE_KEY)
                    self.config = OfficeConfig.model_validate(row.value if row else {})
        return self.config

    async def save(self, session: AsyncSession, config: OfficeConfig) -> None:
        # Onder het slot: bij twee gelijktijdige wijzigingen houden geheugen en database
        # dezelfde waarde
        async with self._lock:
            row = await session.get(Setting, OFFICE_KEY)
            if row is None:
                session.add(Setting(key=OFFICE_KEY, value=config.model_dump()))
            else:
                row.value = config.model_dump()
            await session.commit()
            self.config = config


def office_policy(app) -> OfficePolicy:
    policy = getattr(app.state, "office_policy", None)
    if policy is None:
        policy = app.state.office_policy = OfficePolicy()
    return policy
