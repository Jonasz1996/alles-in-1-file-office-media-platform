"""De staat van media in één proces, op app.state.media (bouwontwerp fase 5, §3.3). Frozen.

MediaState zegt hoe het met de worker gaat (health.py houdt het bij) en houdt de sessies (M3) en
de toelating (M4) vast. MediaPolicy is MediaConfig uit de tabel setting, in het geheugen, zoals
OfficePolicy. /beheer (M10) leest en schrijft via MediaPolicy.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import MediaConfig, Settings
from app.media.types import WorkerCaps
from app.models import Setting

MEDIA_KEY = "media"
WorkerHealth = Literal["starting", "ok", "down", "ffmpeg_missing"]


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass
class MediaState:
    """Op app.state.media. enabled: PLATFORM_MEDIA_ENABLED en MediaConfig.enabled samen;
    zonder staat elk media-eindpunt op 503 media_off."""

    worker: WorkerHealth = "starting"
    mode: Literal["container", "local"] = "local"
    since: datetime = field(default_factory=_now)
    caps: WorkerCaps | None = None
    bench_factor: float = 1.0  # f van de meting; 1,0 tot er gemeten is (Keuze 26)
    sessions: Any = None  # SessionTable (M3)
    admission: Any = None  # Admission (M4)
    enabled: bool = False
    reason: str = ""  # waarom down of ffmpeg_missing, voor /beheer en het log
    # F3 (§3.5): het stoppen is begonnen (runtime.begin_stop). Verzoeken onder /api/v1/stream/
    # krijgen dan 503 media_busy met Retry-After: 2, ook wie op een segment wacht
    stopping: bool = False
    stop_waiters: set[asyncio.Future] = field(default_factory=set, repr=False)

    def set_worker(self, worker: WorkerHealth, reason: str = "") -> bool:
        """Een nieuwe toestand van de worker; True als hij veranderde (since gaat dan mee)."""
        changed = worker != self.worker
        if changed:
            self.worker = worker
            self.since = _now()
        self.reason = reason
        return changed

    @property
    def healthy(self) -> bool:
        return self.enabled and self.worker == "ok"


def media_state(app) -> MediaState:
    """De MediaState van deze app; zonder media een die uit staat."""
    state = getattr(app.state, "media", None)
    if state is None:
        state = app.state.media = MediaState()
    return state


class MediaPolicy:
    """MediaConfig uit de tabel setting, in het geheugen. effective houdt het plafond van
    PLATFORM_MEDIA_MAX_TRANSCODES aan: /beheer kan alleen lager (Keuze 26)."""

    def __init__(self, config: MediaConfig | None = None):
        self.config = config  # None = nog niet uit de database gelezen
        self._lock = asyncio.Lock()

    async def get(self, session: AsyncSession) -> MediaConfig:
        if self.config is None:
            async with self._lock:
                if self.config is None:
                    row = await session.get(Setting, MEDIA_KEY)
                    self.config = MediaConfig.model_validate(row.value if row else {})
        return self.config

    async def save(self, session: AsyncSession, config: MediaConfig) -> None:
        # Onder het slot: bij twee gelijktijdige wijzigingen houden geheugen en database
        # dezelfde waarde
        async with self._lock:
            value = config.model_dump(mode="json")
            row = await session.get(Setting, MEDIA_KEY)
            if row is None:
                session.add(Setting(key=MEDIA_KEY, value=value))
            else:
                row.value = value
            await session.commit()
            self.config = config

    @staticmethod
    def effective(config: MediaConfig, settings: Settings) -> MediaConfig:
        """De instellingen zoals ze gelden: max_transcodes niet boven het plafond."""
        ceiling = max(0, settings.media_max_transcodes)
        if config.max_transcodes <= ceiling:
            return config
        return config.model_copy(update={"max_transcodes": ceiling})


def media_policy(app) -> MediaPolicy:
    policy = getattr(app.state, "media_policy", None)
    if policy is None:
        policy = app.state.media_policy = MediaPolicy()
    return policy
